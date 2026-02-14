#!/usr/bin/env python3
"""Anvil - a small source-based package manager (forging packages from source and managing an index)."""
import os
import sys
import json
import re
import shutil
import subprocess
import argparse
import platform
import urllib.request
import urllib.parse
import urllib.error
# tarfile and zipfile removed; using shell tools in AutoBuilder
import sqlite3
from pathlib import Path
import stat
import time
import logging
from typing import Optional, List, Dict, Tuple, Union, Callable, Any, Set
# Setup logger for Anvil; level can be overridden via ANVIL_LOG_LEVEL
log_level = os.environ.get('ANVIL_LOG_LEVEL', 'INFO').upper()
numeric_level = getattr(logging, log_level, logging.INFO)
logging.basicConfig(level=numeric_level, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('anvil')

# --- Configuration & Constants ---
HOME = Path.home()
ANVIL_ROOT = HOME / ".anvil"
INDEX_DIR = ANVIL_ROOT / "index"
BUILD_DIR = ANVIL_ROOT / "build"
INSTALL_DIR = ANVIL_ROOT / "opt"
BIN_DIR = ANVIL_ROOT / "bin"

# The central registry of sources
INDEX_REPO_URL = "https://github.com/sycomix/Anvil_Index.git"

class Colors:
    """Console color helpers used for printing status messages."""
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

    @staticmethod
    def print(msg, color=ENDC, prefix="[ANVIL]"):
        # Use module-level logger for structured logs and Colors for terminal output
        # Do not rebind to avoid shadowing the module-level 'logger'
        # Default INFO level; warnings and errors mapped by color
        if color == Colors.FAIL:
            log_method = logger.error
        elif color == Colors.WARNING:
            log_method = logger.warning
        else:
            log_method = logger.info

        try:
            log_method("%s %s", prefix, msg)
        except (ValueError, TypeError, OSError, UnicodeEncodeError):
            # Ensure that logging errors (encoding, type, OS issues) don't prevent console output
            pass
        if os.name == 'nt':
            print(f"{prefix} {msg}")
        else:
            print(f"{color}{prefix} {msg}{Colors.ENDC}")

# --- Utilities ---
def run_cmd(command: str, cwd: Optional[str] = None, shell: bool = True, verbose: bool = True, env: Optional[Dict[str, str]] = None) -> str:
    """Run a shell command and return its stdout on success.

    On failure, raises CommandExecutionError for command-specific failures or
    exits the process for non-git related OS/subprocess errors (legacy behavior).
    """
    try:
        # Use run to capture output for diagnostics (esp. linker errors)
        cp = subprocess.run(command, cwd=cwd, shell=shell, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        if cp.returncode == 0:
            if verbose:
                # Print the command executed in verbose mode
                Colors.print(f"Command succeeded: {command}")
            return cp.stdout
        # Failure: raise a specialized error with captured output
        raise CommandExecutionError(command, cp.returncode, cp.stdout, cp.stderr)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as e:
        # Generic fallback for known exception types (avoid catching BaseException/Exception)
        Colors.print(f"Command failed: {command} ({e})", Colors.FAIL)
        # Preserve original behavior for git commands (caller expects an exception)
        if isinstance(command, str) and "git" in command:
            raise
        # Keep previous behavior: exit on failure for non-git commands
        sys.exit(1)

def run_cmd_output(command: str, cwd: Optional[str] = None, shell: bool = True, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Run a command and return its stdout or None if the command fails.

    This function is a convenience wrapper around subprocess.check_output,
    returning None on failure so callers can detect absence of output.
    """
    try:
        out = subprocess.check_output(command, cwd=cwd, shell=shell, stderr=subprocess.DEVNULL, env=env)
        return out.decode('utf-8').strip()
    except subprocess.CalledProcessError:
        return None


class CommandExecutionError(Exception):
    """Raised when a command executed via run_cmd fails.

    Carries stdout/stderr to aid diagnostic analysis.
    """
    def __init__(self, command, returncode, stdout, stderr):
        super().__init__(f"Command '{command}' failed with return code {returncode}")
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def detect_lnk_and_pic_issues(stderr: str) -> List[str]:
    """Scan output for LNK2038 (RuntimeLibrary mismatch) or PIC errors and return suggestions.

    Returns a list of suggestion strings to apply in order to fix the issue.
    """
    suggestions = []
    if not stderr:
        return suggestions
    text = stderr
    # Detect MSVC Runtime mismatch LNK2038
    if 'LNK2038' in text and 'RuntimeLibrary' in text:
        # Extract the two settings if possible
        # Example substring: "value 'MD_DynamicRelease' doesn't match value 'MT_StaticRelease'"
        try:
            m = re.search(r"value '([A-Z]+)_.*?' doesn't match value '([A-Z]+)_.*?'", text)
            if m:
                left = m.group(1)
                right = m.group(2)
                suggestions.append(f"Detected MSVC runtime mismatch between {left} and {right}. Consider building with a consistent C runtime.")
        except (re.error, IndexError, TypeError):
            suggestions.append("Detected MSVC runtime mismatch (LNK2038). Consider building with consistent /MD or /MT options.")
        suggestions.append("Fix options to try:")
        suggestions.append(" - Set environment variable ANVIL_MSVC_RUNTIME=MD (default dynamic CRT) or ANVIL_MSVC_RUNTIME=MT (static CRT)")
        suggestions.append(" - For per-formula control, add 'msvc_runtime': 'MD' or 'MT' to anvil.json in the project")
        suggestions.append(" - For CMake projects, add '-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL' or 'MultiThreaded' depending on your choice")

    # Detect PIC-related errors (relocation errors on Linux/macOS)
    if 'recompile with -fPIC' in text or 'relocation' in text and 'R_X86_64' in text:
        suggestions.append("Detected link-time relocation errors suggesting -fPIC is required for shared libraries.")
        suggestions.append("Fix options to try:")
        suggestions.append(" - Set environment variable ANVIL_FORCE_PIC=1 to add -fPIC to CFLAGS/CXXFLAGS when building.")
        suggestions.append(" - Add 'force_pic': true to the project's anvil.json to force PIC for that formula")

    return suggestions


def default_build_env(msvc_runtime_override: Optional[str] = None, force_pic_override: Optional[bool] = None) -> Dict[str, str]:
    """Return a default environment dictionary for build commands.

    On Windows with MSVC we force the compiler runtime to use the dynamic CRT
    (i.e. /MD / MultiThreadedDLL) to avoid linker mismatches across multi-stage
    builds that include both rust/cargo cmake and C/C++ build steps.
    """
    env: Dict[str, str] = os.environ.copy()
    if os.name == 'nt':
        # Ensure we request the dynamic CRT. Prefer /MD over /MT.
        # Allow overriding via ANVIL_MSVC_RUNTIME: 'MD' (dll) or 'MT' (static)
        requested = os.environ.get('ANVIL_MSVC_RUNTIME', '').strip().upper()
        if msvc_runtime_override:
            requested = str(msvc_runtime_override).strip().upper()
        if requested == 'MT':
            cl_flag = '/MT'
            cmake_flag = 'MultiThreaded'
        else:
            # Default to dynamic CRT
            cl_flag = '/MD'
            cmake_flag = 'MultiThreadedDLL'

        cl = env.get('CL', '')
        # If CL contains either prefix, replace it with our chosen flag; otherwise prepend
        if '/MT' in cl or '/MD' in cl:
            env['CL'] = cl.replace('/MT', cl_flag).replace('/MD', cl_flag)
        else:
            env['CL'] = f"{cl_flag} {cl}".strip()

        # Make CMake default to the matching MSVC runtime
        env['CMAKE_MSVC_RUNTIME_LIBRARY'] = cmake_flag
    # Handle POSIX-specific optional flags (Linux, macOS): allow user to
    # force -fPIC for libraries with ANVIL_FORCE_PIC=1 to avoid reloc issues
    # when creating shared libraries copied into install prefixes.
    # When not requested, do not modify the user's CFLAGS/CXXFLAGS.
    if os.name != 'nt':
        # Respect explicit override; otherwise look at env var
        force_pic = False
        if force_pic_override is not None:
            force_pic = bool(force_pic_override)
        else:
            force_pic = os.environ.get('ANVIL_FORCE_PIC', '').strip() in ('1', 'true', 'True', 'TRUE')
        if force_pic:
            cflags = env.get('CFLAGS', '')
            if '-fPIC' not in cflags:
                env['CFLAGS'] = (cflags + ' -fPIC').strip()
            cxxflags = env.get('CXXFLAGS', '')
            if '-fPIC' not in cxxflags:
                env['CXXFLAGS'] = (cxxflags + ' -fPIC').strip()

    return env


# --- GitHub release check helpers ---
def _platform_asset_tokens() -> Set[str]:
    """Return tokens to match against release asset filenames for this platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()
    tokens: Set[str] = {system, machine, "x86_64", "x64", "amd64"}
    if system == 'windows':
        tokens.update({'win', 'windows', '.exe', 'zip'})
    elif system == 'darwin' or 'mac' in system or 'darwin' in system:
        tokens.update({'mac', 'darwin', 'dylib', 'tar.gz', 'zip'})
    else:
        tokens.update({'linux', 'tar.gz', 'tar.xz', 'tgz'})
    return tokens


def _asset_name_matches_platform(asset_name: str) -> bool:
    """Return True if the asset name looks like a prebuilt for this platform."""
    if not asset_name:
        return False
    name = asset_name.lower()
    tokens = _platform_asset_tokens()
    return any(tok in name for tok in tokens)


def _normalize_github_owner_repo(target: str) -> Optional[str]:
    """Try to extract 'owner/repo' from a URL or 'owner/repo' string."""
    if not target:
        return None
    # already in owner/repo form
    if '/' in target and target.count('/') == 1 and not target.startswith('http'):
        return target
    # https://github.com/owner/repo(.git)?
    m = re.search(r'github\\.com[:/]+([^/]+)/([^/\\.]+)', target)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None


def check_for_release(target: str) -> bool:
    """Check GitHub releases for a platform-matching prebuilt for `target`.

    If a matching prebuilt asset is already installed under the local
    install prefix (`~/.anvil/opt/<name>`) this returns True.  If a remote
    platform-matching release asset is found the function will attempt to
    download and extract it into the install prefix and then return True.
    On any error or if no suitable release is found, returns False.

    This helper is conservative: it never raises on network errors and is
    safe to call from unit tests (tests should patch it when offline).
    """
    # (type hints maintained for callers)    # Derive a package name from the target (URL, owner/repo, or local name)
    name = None
    owner_repo = _normalize_github_owner_repo(target) if isinstance(target, str) else None
    if owner_repo:
        name = owner_repo.split('/')[-1]
    else:
        # If target is a local path like ./foo or absolute, use basename
        name = os.path.basename(str(target)).replace('.git', '')

    install_path = INSTALL_DIR / name
    # If already installed locally, skip build
    if install_path.exists() and any(install_path.iterdir()):
        Colors.print(f"Found existing installation for {name}; skipping release check.", Colors.OKGREEN)
        return True

    # If we cannot determine a GitHub owner/repo, bail out
    if not owner_repo:
        return False

    api_url = f"https://api.github.com/repos/{owner_repo}/releases/latest"
    try:
        headers = {'User-Agent': 'anvil-release-check/1.0'}
        token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if token:
            headers['Authorization'] = f"token {token}"
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = getattr(resp, 'status', None) or resp.getcode()
            if status != 200:
                logger.warning("GitHub API returned status %s for %s", status, api_url)
                return False
            data = json.load(resp)
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        logger.warning("Release check network/parsing error for %s: %s", api_url, e)
        return False

    assets = data.get('assets', []) or []
    for asset in assets:
        asset_name = asset.get('name') or ''
        if _asset_name_matches_platform(asset_name):
            download_url = asset.get('browser_download_url')
            if not download_url:
                continue
            # Attempt to download and extract/install into install_path
            try:
                tmp_dir = Path(os.getenv('TMP', '/tmp'))
                tmp_file = tmp_dir / asset_name
                Colors.print(f"Downloading prebuilt release asset: {asset_name}", Colors.OKBLUE)
                urllib.request.urlretrieve(download_url, str(tmp_file))
                install_path.mkdir(parents=True, exist_ok=True)
                # Try to unpack common archive formats; fall back to saving a single binary in bin/
                try:
                    shutil.unpack_archive(str(tmp_file), str(install_path))
                except (shutil.ReadError, ValueError):
                    # Not an archive — copy to bin
                    bin_dir = install_path / 'bin'
                    bin_dir.mkdir(parents=True, exist_ok=True)
                    dest = bin_dir / asset_name
                    shutil.copy2(str(tmp_file), str(dest))
                    if os.name != 'nt':
                        dest.chmod(dest.stat().st_mode | stat.S_IXUSR)
                finally:
                    # Best-effort cleanup of temporary file
                    try:
                        if tmp_file.exists():
                            tmp_file.unlink()
                    except OSError as e:
                        logger.warning("Failed to remove temp file %s: %s", tmp_file, e)
                Colors.print(f"Installed prebuilt release for {name}", Colors.OKGREEN)
                return True
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, shutil.ReadError, ValueError) as e:
                logger.warning("Failed to download/install release asset %s: %s", asset_name, e)
                # treat as no suitable release available
                return False

    return False


def _on_rm_error(func: Callable[[str], None], path: str, exc_info: Any) -> None:
    """Error handler for shutil.rmtree to handle read-only files on Windows.
    Tries to make file writable and retries the operation.
    """
    # Only handle permission errors
    if not os.access(path, os.W_OK):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError as e:
            Colors.print(f"Failed to remove {path}: {exc_info} ({e})", Colors.FAIL)
    else:
        # Raise if it's a different error - re-raise the original exception object
        raise exc_info[1]


def safe_rmtree(path: Path, retries: int = 3, delay: float = 0.5) -> None:
    """Remove directory tree safely, dealing with Windows read-only attributes.
    Retries removal a few times with small delays in case files are transiently locked.

    SAFETY: refuse to remove the Anvil root, the user's HOME, or the filesystem root.
    This prevents accidental mass-deletion when housekeeping is run with bad paths.
    """
    if not path.exists():
        return

    # Safety guards: never remove the repository/home/root directories
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError) as e:
        # If resolution fails for expected OS/runtime issues, refuse to remove the path
        Colors.print(f"Refusing to remove path (unable to resolve): {path} ({e})", Colors.FAIL)
        return

    dangerous_targets = {ANVIL_ROOT.resolve(), HOME.resolve(), Path('/').resolve()}
    if resolved in dangerous_targets:
        Colors.print(f"Refusing to remove critical path: {path}", Colors.FAIL)
        return

    attempt = 0
    last_err: Optional[OSError] = None
    while attempt < retries:
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            return
        except OSError as e:
            last_err = e
            Colors.print(f"Retrying removal of {path}: {e}", Colors.WARNING)
            time.sleep(delay)
            attempt += 1
    Colors.print(f"Could not remove path {path} after {retries} attempts: {last_err}", Colors.FAIL)

# --- Auto-Discovery Build Engine ---

class AutoBuilder:
    """
    Inspects a source directory and generates a build plan
    without requiring a formula file.
    """
    @staticmethod
    def _get_parallel_jobs() -> str:
        """Return the number of parallel jobs to use for builds (e.g. -j4)."""
        count = os.cpu_count()
        if not count or count < 1:
            return "1"
        return str(count)

    @staticmethod
    def detect(source_path: Path, install_prefix: Path) -> Tuple[List[Union[str, Callable[[Path, Path], None]]], List[str], Dict[str, Any]]:
        steps = []
        metadata = {}
        # 1. Check for explicit 'anvil.json' in the repo (The "Gold Standard")
        if (source_path / "anvil.json").exists():
            with open(source_path / "anvil.json", encoding='utf-8') as f:
                data = json.load(f)
                build_deps = data.get("build_dependencies", [])
                if build_deps:
                    AutoBuilder.install_build_dependencies(build_deps)
                metadata['msvc_runtime'] = data.get('msvc_runtime')
                metadata['force_pic'] = data.get('force_pic')
                return data.get("build", {}).get("common", []), data.get("binaries", []), metadata
        elif (source_path / "setup.py").exists():
            Colors.print("Detected Python project (setup.py)", Colors.OKBLUE)
            steps = [
                f"{sys.executable} -m pip install . --target {install_prefix} --upgrade"
            ]
            return steps, [], metadata
        elif (source_path / "requirements.txt").exists():
            Colors.print("Detected Python requirements", Colors.OKBLUE)
            steps = [f"{sys.executable} -m pip install -r requirements.txt --target {install_prefix}"]
            return steps, [], metadata
        # Handle Autotools (configure script)
        elif (source_path / "configure").exists():
            Colors.print("Detected Autotools project (configure)", Colors.OKBLUE)
            install_prefix_str = str(install_prefix).replace('\\', '/')
            steps = [
                f"./configure --prefix=\"{install_prefix_str}\"",
                f"make -j{AutoBuilder._get_parallel_jobs()}",
                f"make install"
            ]
            return steps, [], metadata
        # Handle Makefile variants (GNUmakefile, Makefile, makefile)
        elif any((source_path / name).exists() for name in ("Makefile", "GNUmakefile", "makefile")):
            Colors.print("Detected Makefile", Colors.OKBLUE)
            # Determine which make binary is available (gmake, make, mingw32-make, nmake)
            make_bin = shutil.which("make") or shutil.which("gmake") or shutil.which("mingw32-make") or shutil.which("nmake")
            if not make_bin:
                Colors.print("Make not found on PATH. Please install build tools or use anvil.json.", Colors.WARNING)
                return [], [], metadata
            
            jobs = f"-j{AutoBuilder._get_parallel_jobs()}"
            # nmake doesn't support -j
            if "nmake" in make_bin:
                jobs = ""

            install_prefix_str = str(install_prefix)
            # On many projects, 'make install' responds to PREFIX= or DESTDIR=.
            # We prefer to only run 'make install' when an 'install' target exists; otherwise,
            # we run 'make' and copy any produced build artifacts to the install directory.
            install_target = False
            for name in ("Makefile", "GNUmakefile", "makefile"):
                mf = source_path / name
                if mf.exists():
                    try:
                        content = mf.read_text(encoding='utf-8')
                        if "\ninstall:" in content or content.startswith("install:"):
                            install_target = True
                            break
                    except (OSError, UnicodeDecodeError):
                        # If we cannot read the file, assume no install target
                        install_target = False
            steps = [f"{make_bin} {jobs}"]
            if install_target:
                steps.extend([
                    f"{make_bin} install PREFIX=\"{install_prefix_str}\"",
                    f"{make_bin} install DESTDIR=\"{install_prefix_str}\"",
                ])
            else:
                # We'll rely on a generic copy step to collect built binaries
                # If this is a go module, prefer running `go build` to produce a binary
                if (source_path / 'go.mod').exists():
                    bin_name = source_path.name
                    steps.append(f"go build -o \"{install_prefix / 'bin' / bin_name}\" ./...")
                    return steps, [bin_name], metadata
                steps.append(AutoBuilder._copy_build_bins)
            return steps, [], metadata
        if (source_path / "CMakeLists.txt").exists():
            Colors.print("Detected CMake project", Colors.OKBLUE)
            cmake_args = f"-DCMAKE_INSTALL_PREFIX={install_prefix}"
            # If building on Windows with MSVC, select the matching runtime.
            if os.name == 'nt':
                # Prefer env var override; otherwise default to MultiThreadedDLL.
                requested = os.environ.get('ANVIL_MSVC_RUNTIME', '').strip().upper()
                requested = metadata.get('msvc_runtime', requested) if metadata else requested
                if requested == 'MT':
                    cmake_flag = 'MultiThreaded'
                else:
                    cmake_flag = 'MultiThreadedDLL'
                cmake_args += f" -DCMAKE_MSVC_RUNTIME_LIBRARY={cmake_flag} -A x64"
                # Use PowerShell style make (nmake/mingw) automatically should be chosen by the project's CMake
            steps = [
                "mkdir -p build",
                f"cd build && cmake .. {cmake_args}",
                f"cd build && make -j{AutoBuilder._get_parallel_jobs()}",
                "cd build && make install"
            ]
            return steps, [], metadata
        elif (source_path / "Cargo.toml").exists():
            Colors.print("Detected Rust project", Colors.OKBLUE)
            is_virtual_workspace = False
            try:
                with open(source_path / "Cargo.toml", 'r', encoding='utf-8') as f:
                    content = f.read()
                    if "[workspace]" in content and "[package]" not in content:
                        is_virtual_workspace = True
            except (OSError, UnicodeDecodeError):
                # If reading the file fails, treat as not a workspace and continue
                pass
            # Workspace: build all, then copy any bins and libs
            if is_virtual_workspace:
                Colors.print("Detected Cargo Workspace. Building release target...", Colors.OKBLUE)
                steps = [
                    "cargo build --release",
                    AutoBuilder._copy_cargo_bins,
                    AutoBuilder._copy_cargo_libs
                ]
            else:
                # Single package: determine if it's a binary or library
                if AutoBuilder._has_cargo_binary(source_path):
                    steps = [f"cargo install --path . --root {install_prefix}"]
                else:
                    Colors.print("Detected Rust library crate. Building release and copying library artifacts...", Colors.OKBLUE)
                    steps = [
                        "cargo build --release",
                        AutoBuilder._copy_cargo_libs
                    ]
            return steps, [], metadata
        elif (source_path / "go.mod").exists() or (source_path / "main.go").exists() or any(source_path.glob("*.go")):
            Colors.print("Detected Go project (go.mod)", Colors.OKBLUE)
            # Prefer module-aware install if go 1.18+ and module path; otherwise build
            # If there's a single main package with main.go, we'll build a single binary
            # Only build a binary if main.go or cmd/ exists
            if (source_path / 'main.go').exists() or ((source_path / 'cmd').exists() and any((source_path / 'cmd').rglob('*.go'))):
                binary_name = source_path.name
                steps = [
                    f"go build -o \"{install_prefix / 'bin' / binary_name}\"",
                ]
                return steps, [binary_name], metadata
            else:
                Colors.print('No Go binary found (library-only module). Skipping direct build.', Colors.WARNING)
                return [], [], metadata
        elif (source_path / "package.json").exists():
            Colors.print("Detected Node.js project (package.json)", Colors.OKBLUE)
            steps = [
                "npm install",
                "npm run build || true"
            ]
            return steps, [], metadata
        elif (source_path / "pyproject.toml").exists():
            Colors.print("Detected Python project (pyproject.toml)", Colors.OKBLUE)
            steps = [
                f"{sys.executable} -m pip install . --target {install_prefix} --upgrade"
            ]
            return steps, [], metadata
            return steps, [], metadata
        elif (source_path / "build.ninja").exists():
            Colors.print("Detected Ninja project (build.ninja)", Colors.OKBLUE)
            steps = [
                f"ninja -j{AutoBuilder._get_parallel_jobs()}",
                f"ninja install || true"
            ]
            return steps, [], metadata
        elif (source_path / "meson.build").exists():
            Colors.print("Detected Meson project (meson.build)", Colors.OKBLUE)
            steps = [
                "meson setup build",
                "ninja -C build",
                f"ninja -C build install --destdir={install_prefix} || true"
            ]
            return steps, [], metadata
        elif any(source_path.glob("*.gemspec")):
            Colors.print("Detected Ruby project (*.gemspec)", Colors.OKBLUE)
            gem = next(source_path.glob("*.gemspec"))
            steps = [
                f"gem build {gem.name}",
                f"gem install *.gem --install-dir {install_prefix} --bindir {install_prefix}/bin --no-document"
            ]
            return steps, [], metadata
        elif (source_path / "Package.swift").exists():
            Colors.print("Detected Swift project (Package.swift)", Colors.OKBLUE)
            steps = [
                "swift build -c release",
                AutoBuilder._copy_swift_artifacts
            ]
            return steps, [], metadata
        elif (source_path / "SConstruct").exists():
            Colors.print("Detected SCons project (SConstruct)", Colors.OKBLUE)
            steps = [
                f"scons PREFIX={install_prefix}",
                f"scons install PREFIX={install_prefix} || true"
            ]
            return steps, [], metadata
        elif (source_path / "build.gradle").exists() or (source_path / "gradlew").exists():
            Colors.print("Detected Gradle project (build.gradle)", Colors.OKBLUE)
            gradle_cmd = "./gradlew" if (source_path / "gradlew").exists() else "gradle"
            steps = [
                f"{gradle_cmd} build",
                AutoBuilder._copy_gradle_artifacts
            ]
            return steps, [], metadata
        elif (source_path / "WORKSPACE").exists() or (source_path / "BUILD").exists():
            Colors.print("Detected Bazel project (WORKSPACE/BUILD)", Colors.OKBLUE)
            steps = [
                "bazel build //...",
                AutoBuilder._copy_bazel_artifacts
            ]
            return steps, [], metadata
        elif any(source_path.glob('*.csproj')):
            Colors.print("Detected .NET project (csproj)", Colors.OKBLUE)
            steps = [
                f"dotnet publish -c Release -o {install_prefix}"
            ]
            return steps, [], metadata
        elif (source_path / "build.zig").exists() or (source_path / "zig.toml").exists():
            Colors.print("Detected Zig project (build.zig)", Colors.OKBLUE)
            steps = [
                "zig build -Drelease-safe",
                AutoBuilder._copy_zig_artifacts
            ]
            return steps, [], metadata
        elif (source_path / "pom.xml").exists():
            Colors.print("Detected Java project (pom.xml)", Colors.OKBLUE)
            steps = [
                "mvn package",
                AutoBuilder._copy_maven_artifacts
            ]
            return steps, [], metadata
        else:
            # Archives (.tar.xz, .7z, etc.)
            for ext in [".tar.xz", ".7z", ".tar.bz2", ".tar.gz", ".tgz", ".tar", ".zip"]:
                for file in source_path.glob(f"*{ext}"):
                    Colors.print(f"Detected archive: {file.name}", Colors.OKBLUE)
                    if ext == ".zip":
                        steps = [f"unzip -o {file} -d {install_prefix}"]
                    else:
                        steps = [f"tar -xf {file} -C {install_prefix}"]
                    return steps, [], metadata
            if (source_path / ".hg").exists():
                Colors.print("Detected Mercurial repository", Colors.OKBLUE)
                steps = ["hg pull", "hg update"]
                return steps, [], metadata
            if (source_path / ".svn").exists():
                Colors.print("Detected SVN repository", Colors.OKBLUE)
                steps = ["svn update"]
                return steps, [], metadata
            Colors.print("No build system detected. Copying files as-is.", Colors.WARNING)
            steps = [AutoBuilder._copy_all]
            return steps, [], metadata


    @staticmethod
    def install_build_dependencies(deps):
        """
        Install build dependencies using system package manager.
        """
        if not deps:
            return
        Colors.print(f"Installing build dependencies: {', '.join(deps)}", Colors.OKBLUE)
        if platform.system() == "Linux":
            run_cmd(f"sudo apt-get update && sudo apt-get install -y {' '.join(deps)}")
        elif platform.system() == "Darwin":
            run_cmd(f"brew install {' '.join(deps)}")
        elif platform.system() == "Windows":
            run_cmd(f"choco install {' '.join(deps)}")
        else:
            Colors.print("Unknown platform for dependency installation.", Colors.WARNING)

    # Removed from AutoBuilder; housekeeping belongs on the Anvil instance

    @staticmethod
    def _copy_cargo_bins(build_path, install_path):
        """Helper to find and copy compiled Rust binaries."""
        release_dir = build_path / "target" / "release"
        bin_dir = install_path / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)

        if not release_dir.exists():
            Colors.print(f"Build failed: {release_dir} does not exist", Colors.FAIL)
            return

        count = 0
        for item in release_dir.iterdir():
            if not item.is_file():
                continue

            # Windows: Check for .exe
            if os.name == 'nt':
                if item.suffix == '.exe':
                    Colors.print(f"Copying {item.name}...", Colors.OKBLUE)
                    shutil.copy(item, bin_dir)
                    count += 1
            # Unix: Check for executable permission and no extension (usually)
            else:
                if os.access(item, os.X_OK) and '.' not in item.name:
                    Colors.print(f"Copying {item.name}...", Colors.OKBLUE)
                    shutil.copy(item, bin_dir)
                    count += 1

        if count == 0:
            Colors.print("Warning: No executables found in target/release", Colors.WARNING)

    @staticmethod
    def _copy_build_bins(build_path, install_path):

        """Generic helper to find and copy executables produced by a build.

        Searches common locations (bin, build, dist, top-level) for executables
        and copies them into install_path/bin.
        """
        bin_dir = install_path / 'bin'
        bin_dir.mkdir(parents=True, exist_ok=True)
        locations = [build_path, build_path / 'bin', build_path / 'build', build_path / 'dist', build_path / 'target' / 'release', build_path / 'cmd']
        found = 0
        probable_names = {build_path.name, install_path.name}
        probable_names.add(f"{build_path.name}.exe")
        probable_names.add(f"{install_path.name}.exe")

        for loc in locations:
            if not loc.exists():
                continue
            for item in loc.rglob('*'):
                if not item.is_file():
                    continue
                # Windows: check for .exe, else check unix executable bit and skip typical extensions
                try:
                    if os.name == 'nt':
                        if item.suffix.lower() == '.exe' or item.name in probable_names:
                            Colors.print(f"Copying build artifact {item.name}...", Colors.OKBLUE)
                            shutil.copy(item, bin_dir)
                            found += 1
                    else:
                        if os.access(item, os.X_OK) or item.name in probable_names:
                            # Avoid copying common archive files or scripts with extensions
                            if item.suffix in ['.py', '.sh', '.txt', '.md', '.c', '.h', '.o', '.a', '.so', '.dll', '.dylib']:
                                continue
                            Colors.print(f"Copying build artifact {item.name}...", Colors.OKBLUE)
                            shutil.copy(item, bin_dir)
                            found += 1
                except OSError:
                    # ignore copy errors for individual files
                    pass
        if found == 0:
            Colors.print("Warning: No build artifacts found to copy", Colors.WARNING)

    @staticmethod
    def _copy_cargo_libs(build_path, install_path):
        """Helper to find and copy compiled Rust library artifacts.
        Copies .rlib, static and shared library artifacts into <install_prefix>/lib.
        """
        release_dir = build_path / "target" / "release"
        lib_dir = install_path / "lib"
        lib_dir.mkdir(parents=True, exist_ok=True)

        if not release_dir.exists():
            Colors.print(f"Build failed: {release_dir} does not exist", Colors.FAIL)
            return

        patterns = ["*.rlib", "*.a", "*.so", "*.dll", "*.dylib"]
        count = 0
        for pat in patterns:
            for item in release_dir.glob(pat):
                if item.is_file():
                    Colors.print(f"Copying lib {item.name}...", Colors.OKBLUE)
                    shutil.copy(item, lib_dir)
                    count += 1

        if count == 0:
            Colors.print("Warning: No library artifacts found in target/release", Colors.WARNING)

    @staticmethod
    def _copy_all(build_path, install_path):
        """Copy all files from build_path to install_path."""
        Colors.print(f"Copying all files to {install_path}...", Colors.OKBLUE)
        if hasattr(shutil, 'copytree'):
            # Python 3.8+ handles existing dest with dirs_exist_ok=True
            shutil.copytree(build_path, install_path, dirs_exist_ok=True)
        else:
            # Fallback for older python if needed, though 3.12 is required per comments
            # But let's be safe: iterate and copy
             for item in build_path.iterdir():
                dest = install_path / item.name
                if item.is_dir():
                    if dest.exists():
                        safe_rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

    @staticmethod
    def _copy_gradle_artifacts(build_path, install_path):
        src = build_path / "build" / "libs"
        if not src.exists(): return
        for item in src.iterdir():
            shutil.copy2(item, install_path)

    @staticmethod
    def _copy_bazel_artifacts(build_path, install_path):
        src = build_path / "bazel-bin"
        if not src.exists(): return
        if hasattr(shutil, 'copytree'):
             shutil.copytree(src, install_path, dirs_exist_ok=True)

    @staticmethod
    def _copy_zig_artifacts(build_path, install_path):
        src = build_path / "zig-out" / "bin"
        dest = install_path / "bin"
        dest.mkdir(parents=True, exist_ok=True)
        if not src.exists(): return
        for item in src.iterdir():
            shutil.copy2(item, dest)

    @staticmethod
    def _copy_maven_artifacts(build_path, install_path):
        src = build_path / "target"
        if not src.exists(): return
        for item in src.glob("*.jar"):
            shutil.copy2(item, install_path)

    @staticmethod
    def _copy_swift_artifacts(build_path, install_path):
        src = build_path / ".build" / "release"
        dest = install_path / "bin"
        dest.mkdir(parents=True, exist_ok=True)
        if not src.exists(): return
        for item in src.iterdir():
             if item.is_file() and os.access(item, os.X_OK):
                shutil.copy2(item, dest)
             elif os.name == 'nt' and item.suffix == '.exe':
                shutil.copy2(item, dest)

    @staticmethod
    def _has_cargo_binary(source_path):
        """Return True if this Cargo package has any binary targets (bins or src/main.rs).
        Uses heuristics: existence of src/main.rs, src/bin/*, or [[bin]] in Cargo.toml.
        """
        # 1. main.rs
        if (source_path / "src" / "main.rs").exists():
            return True
        # 2. src/bin directory exists and has files
        bin_dir = source_path / "src" / "bin"
        if bin_dir.exists() and any(bin_dir.iterdir()):
            return True
        # 3. explicit [[bin]] entries in Cargo.toml
        try:
            toml_path = source_path / "Cargo.toml"
            if toml_path.exists():
                content = toml_path.read_text(encoding='utf-8')
                if "[[bin]]" in content:
                    return True
        except (OSError, UnicodeDecodeError):
            # File not found or decode error - treat as no explicit [[bin]] entries
            pass
        return False


# --- Index Management ---

class RepoIndex:
    """Manage the local sqlite index of repository metadata and sync with the central index."""
    def __init__(self):
        self.db_path = INDEX_DIR / "index.db"
        self._ensure_exists()

    def _ensure_exists(self):
        if not INDEX_DIR.exists():
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            try:
                # Try to clone index, but don't fail if offline/empty
                run_cmd(f"git clone {INDEX_REPO_URL} .", cwd=INDEX_DIR, verbose=False)
            except (subprocess.CalledProcessError, OSError):
                # Ignore clone errors (no network or git missing)
                pass

        if not self.db_path.exists():
            self._create_bootstrap_db()
        else:
            # If the DB already exists, ensure schema is migrated if necessary.
            try:
                self._migrate_schema()
            except sqlite3.Error:
                # If migration fails, ignore and continue; DB is likely in usable state.
                pass

    def _create_bootstrap_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS repositories
                     (name text PRIMARY KEY, url text, normalized_url text, description text)''')
        # Simple bootstrap
        normalized_init = RepoIndex.normalize_url('https://github.com/sycomix/anvil-core.git')
        c.execute("INSERT OR IGNORE INTO repositories (name, url, normalized_url, description) VALUES ('anvil-core', 'https://github.com/sycomix/anvil-core.git', ?, 'Anvil Core')", (normalized_init,))
        c.execute("CREATE INDEX IF NOT EXISTS idx_repositories_normalized_url ON repositories(normalized_url)")
        conn.commit()
        conn.close()

    def _migrate_schema(self):
        """Add normalized_url column if missing and populate existing records."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        try:
            c.execute("PRAGMA table_info(repositories)")
            cols = [row[1] for row in c.fetchall()]
            if 'normalized_url' not in cols:
                c.execute("ALTER TABLE repositories ADD COLUMN normalized_url text")
                # Populate normalized_url for existing rows
                c.execute("SELECT name, url FROM repositories")
                rows = c.fetchall()
                for name, url in rows:
                    normalized = RepoIndex.normalize_url(url) if url else None
                    c.execute("UPDATE repositories SET normalized_url=? WHERE name=?", (normalized, name))
                conn.commit()
                # Create an index on normalized_url for fast lookups
                c.execute("CREATE INDEX IF NOT EXISTS idx_repositories_normalized_url ON repositories(normalized_url)")
                conn.commit()
        finally:
            conn.close()

    def ensure_db(self):
        """Public helper to ensure the DB and schema exist.

        Tests and external callers can use this to make the index DB ready for use.
        """
        self._ensure_exists()

    def get_url(self, name: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT url FROM repositories WHERE name=?", (name,))
            result = c.fetchone()
            return result[0] if result else None

    def has_url(self, url: str) -> bool:
        """Return True if the given URL is already present in the index DB."""
        if not url:
            return False
        normalized = RepoIndex.normalize_url(url)
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT normalized_url, url FROM repositories")
            rows = c.fetchall()
            for (row_norm, row_url) in rows:
                if row_norm and row_norm == normalized:
                    return True
                if row_url and RepoIndex.normalize_url(row_url) == normalized:
                    return True
            return False

    def add_local(self, name: str, url: str) -> None:
        # Normalize url before adding to avoid duplicates across formats
        if not url:
            raise ValueError("URL cannot be empty when adding to index")
        normalized = RepoIndex.normalize_url(url)
        if self.has_url(normalized):
            return
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO repositories (name, url, normalized_url, description) VALUES (?, ?, ?, ?)", (name, url, normalized, "User added"))
            conn.commit()

    @staticmethod
    def normalize_url(url: Optional[str]) -> Optional[str]:
        """Return a canonical normalized URL for easier comparison.

        Normalizes forms like git@host:user/repo.git -> https://host/user/repo, strips
        trailing .git and trailing slashes, and lower-cases the host component.
        """
        if not url:
            return url
        url = url.strip()
        # ssh style: git@host:user/repo.git
        if url.startswith('git@'):
            # Split into host and path
            try:
                user_host, path = url.split(':', 1)
                host = user_host.split('@', 1)[1]
                url = f"https://{host}/{path}"
            except (ValueError, IndexError):
                # Fallback to original
                pass
        # Replace ssh://git@host/ -> https://host/
        if url.startswith('ssh://'):
            parsed = urllib.parse.urlparse(url)
            host = parsed.hostname or parsed.netloc
            path = parsed.path
            url = f"https://{host}{path}"
        # For http/https parse and reconstruct canonical form
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ('http', 'https') and parsed.netloc:
            host = parsed.netloc.lower()
            path = parsed.path.rstrip('/')
            # strip .git suffix
            if path.endswith('.git'):
                path = path[:-4]
            url = f"https://{host}{path}"
        # Ensure no trailing slash
        if url.endswith('/'):
            url = url[:-1]
        return url.lower()

    def search(self, query):
        """Search for repositories matching the query in name or description."""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            pattern = f"%{query}%"
            c.execute("SELECT name, description, url FROM repositories WHERE name LIKE ? OR description LIKE ?", (pattern, pattern))
            return c.fetchall()

    def update(self):
        if (INDEX_DIR / ".git").exists():
            Colors.print("Syncing Central Index...", Colors.HEADER)
            try:
                run_cmd("git pull", cwd=INDEX_DIR, verbose=False)
            except (subprocess.CalledProcessError, OSError):
                # If pull fails, ignore and continue
                pass

# --- Main App ---

class Anvil:
    """Main CLI class responsible for forging packages, submitting repos, and housekeeping."""
    def __init__(self):
        self._setup_dirs()
        self._ensure_path()
        self.index = RepoIndex()
        # Auto-submit repositories to the central index unless disabled via env var
        val = os.environ.get('ANVIL_AUTO_SUBMIT', '1')
        self.auto_submit = str(val).strip().lower() not in ('0', 'false', 'no')

    def _setup_dirs(self):
        for p in [ANVIL_ROOT, BUILD_DIR, INSTALL_DIR, BIN_DIR, INDEX_DIR]:
            p.mkdir(parents=True, exist_ok=True)

    def _ensure_path(self):
        if str(BIN_DIR) not in os.environ["PATH"]:
            Colors.print(f"WARNING: Add {BIN_DIR} to your PATH.", Colors.WARNING)

    def housekeeping(self) -> None:
        """Cleans up build directory contents, orphaned binaries, and unused dependencies.

        Safety: do NOT remove the Anvil root directory itself; only clear the
        contents of the build directory so accidental deletion of ~/.anvil is prevented.
        """
        Colors.print("Running housekeeping...", Colors.HEADER)

        # Clear contents of the build directory but keep the BUILD_DIR itself intact
        if BUILD_DIR.exists():
            for child in BUILD_DIR.iterdir():
                try:
                    if child.is_dir():
                        safe_rmtree(child)
                    else:
                        # remove files directly
                        child.unlink()
                except (OSError, ValueError) as e:
                    Colors.print(f"Failed to remove build entry {child}: {e}", Colors.WARNING)
            Colors.print("Build directory cleaned.", Colors.OKGREEN)

        # Remove orphaned binaries (only remove files that point to known install prefixes)
        installed = {p.name for p in INSTALL_DIR.iterdir() if p.is_dir()}
        if BIN_DIR.exists():
            for bin_file in BIN_DIR.iterdir():
                if not bin_file.is_file():
                    continue
                # On Windows we have shims like <name>.bat -> installed files; check stem
                name = bin_file.stem
                # Only remove the shim if it does NOT correspond to any installed package
                if name not in installed:
                    try:
                        bin_file.unlink()
                        Colors.print(f"Removed orphaned binary: {bin_file.name}", Colors.OKBLUE)
                    except OSError as e:
                        Colors.print(f"Failed to remove binary: {bin_file.name} ({e})", Colors.WARNING)
        Colors.print("Housekeeping complete.", Colors.OKGREEN)

    def forge(self, target: str, msvc_runtime: Optional[str] = None, force_pic: Optional[bool] = None, check_release: bool = True) -> None:
        """
        Target can be:
        1. A package name in the index (e.g., 'htop')
        2. A git URL (e.g., 'https://github.com/foo/bar')
        3. A local path (e.g., './my-project')

        check_release: consult `check_for_release` to detect and install a
        platform-matching prebuilt release before attempting to clone/build.
        """

        url = None
        name = None
        source_remote = None

        # 1. Check if it's a URL
        if target.startswith("http") or target.startswith("git@"):
            url = target
            name = target.split("/")[-1].replace(".git", "")
            # Remote URL
            Colors.print(f"Direct Forge: {name} from {url}", Colors.HEADER)

        # 2. Check if it's a local path
        elif os.path.exists(target) and os.path.isdir(target):
            # Local copy. Determine if it's a Git repo and try to find a remote URL.
            src_path = Path(target)
            name = src_path.name
            Colors.print(f"Local Forge: {name} from {src_path}", Colors.HEADER)
            # Detect git remote origin if present
            git_dir = src_path / '.git'
            if git_dir.exists():
                try:
                    source_remote = run_cmd_output(
                        'git config --get remote.origin.url', cwd=str(src_path)
                    )
                    if source_remote:
                        url = source_remote
                except (FileNotFoundError, subprocess.CalledProcessError, OSError):
                    source_remote = None

        # 3. Check Index
        else:
            url = self.index.get_url(target)
            if not url:
                Colors.print(f"Package '{target}' not found in index.", Colors.FAIL)
                return
            name = target
            Colors.print(f"Index Forge: {name} from {url}", Colors.HEADER)

        # Prepare Paths
        # If requested, consult GitHub releases / local install before building
        if check_release:
            release_target = url or name
            if check_for_release(release_target):
                Colors.print(f"Platform-matching release found for {name}; skipping build.", Colors.OKGREEN)
                return

        build_path = BUILD_DIR / name
        install_path = INSTALL_DIR / name

        if build_path.exists():
            safe_rmtree(build_path)
        build_path.mkdir()

        # Fetch Source
        if os.path.exists(target) and os.path.isdir(target):
            # Local copy - use shutil for cross-platform copies instead of shell 'cp'
            Colors.print("Copying local source to build dir...", Colors.OKBLUE)
            src_path = Path(target)
            for item in src_path.iterdir():
                dest = build_path / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
        else:
            # Git clone
            Colors.print("Cloning source...", Colors.OKBLUE)
            run_cmd(f"git clone --depth 1 {url} .", cwd=build_path)

        # Auto-Detect Build System
        steps, binaries, metadata = AutoBuilder.detect(build_path, install_path)

        # Build
        Colors.print("Forging (Building)...", Colors.OKBLUE)
        if install_path.exists():
            safe_rmtree(install_path)
        install_path.mkdir(parents=True, exist_ok=True)

        # Prepare a platform-sensitive build env for all build steps so
        # that different compilers used by multi-stage builds consistently
        # link against the same C runtime (particularly on Windows/MSVC).
        # Determine the overrides precedence: CLI args > per-formula metadata > environment
        msvc_override = msvc_runtime if msvc_runtime is not None else metadata.get('msvc_runtime')
        force_pic_override = force_pic if force_pic is not None else metadata.get('force_pic')
        build_env = default_build_env(msvc_runtime_override=msvc_override, force_pic_override=force_pic_override)
        # Post-process CMake steps to inject MSVC runtime choice (if detected) so cmake call uses -D flag
        if os.name == 'nt' and msvc_override:
            cmake_flag = 'MultiThreaded' if str(msvc_override).strip().upper() == 'MT' else 'MultiThreadedDLL'
            processed_steps = []
            for step in steps:
                if isinstance(step, str) and 'cmake ' in step and 'CMAKE_MSVC_RUNTIME_LIBRARY' not in step:
                    step = step + f" -DCMAKE_MSVC_RUNTIME_LIBRARY={cmake_flag}"
                elif isinstance(step, str) and 'cmake ' in step and 'CMAKE_MSVC_RUNTIME_LIBRARY' in step:
                    # Replace existing flag
                    step = re.sub(r"-DCMAKE_MSVC_RUNTIME_LIBRARY=[^\s]+", f"-DCMAKE_MSVC_RUNTIME_LIBRARY={cmake_flag}", step)
                processed_steps.append(step)
            steps = processed_steps
        if os.name == 'nt':
            Colors.print(f"Enforcing MSVC runtime in build environment (CL='{build_env.get('CL','')}', CMAKE_MSVC_RUNTIME_LIBRARY='{build_env.get('CMAKE_MSVC_RUNTIME_LIBRARY','')}')", Colors.OKBLUE)

        for step in steps:
            # Step can be a string (shell command) or a callable (python function)
            if callable(step):
                step(build_path, install_path)
            else:
                # Replace known placeholders (e.g., {PREFIX}) with real paths
                if isinstance(step, str):
                    # Use forward slashes for paths in shell commands to avoid escaping issues
                    prefix_safe = str(install_path).replace('\\', '/')
                    rendered = step.replace("{PREFIX}", prefix_safe)
                else:
                    rendered = step
                Colors.print(f"Running: {rendered}")
                try:
                    run_cmd(rendered, cwd=build_path, env=build_env)
                except CommandExecutionError as cee:
                    # Analyze stderr for common link/runtime issues and provide suggestions
                    suggestions = detect_lnk_and_pic_issues(getattr(cee, 'stderr', ''))
                    if suggestions:
                        Colors.print("Build failed with suggestions:", Colors.WARNING)
                        for s in suggestions:
                            Colors.print(f"  {s}", Colors.WARNING)
                    # Re-raise to keep existing behavior (exit or raise)
                    raise

        # Link Binaries (Heuristic + Explicit)
        self._link_binaries(install_path, binaries)

        # Cleanup
        safe_rmtree(build_path)
        Colors.print(f"Successfully forged {name}!", Colors.OKGREEN)
        # If we installed from a git repo (URL or local git with a remote) and that remote
        # isn't in the local index yet, auto-submit it to produce a PR for maintainers.
        try:
            final_url = None
            if source_remote:
                final_url = source_remote
            elif url and (url.startswith('http') or url.startswith('git@')):
                final_url = url
            if self.auto_submit and final_url and not self.index.has_url(final_url):
                Colors.print(f"Repository {final_url} not found in index — submitting a PR to add it.", Colors.HEADER)
                self.submit(final_url)
        except (sqlite3.Error, subprocess.CalledProcessError, OSError, ValueError):
            Colors.print("Auto submission failed; continuing without PR", Colors.WARNING)

    def _link_binaries(self, install_path, explicit_binaries):
        """
        Links explicit binaries AND scans for obvious executables.
        """
        candidates = []

        # 1. Look in common bin folders
        for bin_folder in [install_path / "bin", install_path]:
            if bin_folder.exists():
                for f in bin_folder.iterdir():
                    if f.is_file() and os.access(f, os.X_OK):
                        candidates.append(f)
                    elif f.suffix in ['.exe', '.bat', '.py', '.sh']: # Windows/Script check
                        candidates.append(f)

        # 2. Add explicit ones: look for files whose stem (name without extension) matches explicit names
        for b in explicit_binaries:
            for f in install_path.rglob('*'):
                if f.is_file() and f.stem == b:
                    candidates.append(f)

        # 3. Link
        for src in set(candidates):  # set for unique
            if src.name.startswith("."):
                # skip hidden
                continue

            dest = BIN_DIR / src.name
            if dest.exists():
                try:
                    dest.unlink()
                except PermissionError:
                    # Try make writable then unlink
                    try:
                        os.chmod(dest, stat.S_IWRITE)
                        dest.unlink()
                    except OSError as e:
                        Colors.print(f"Could not remove old link {dest}: {e}", Colors.WARNING)

            Colors.print(f"Linking {src.name}...", Colors.OKBLUE)
            if os.name == 'nt':
                # Windows Shim
                with open(str(dest) + ".bat", 'w', encoding='utf-8') as bat:
                    bat.write(f"@echo off\n\"{src}\" %*")

    def submit(self, url):
        """Simple submission: Just URL and Name."""
        name = url.split("/")[-1].replace(".git", "")

        # 1. Add Locally
        self.index.add_local(name, url)
        Colors.print(f"Added '{name}' to local index.", Colors.OKGREEN)

        # 2. Generate PR Link
        payload = {"name": name, "url": url}
        json_content = json.dumps(payload, indent=2)
        base = "https://github.com/sycomix/Anvil_Index/new/main"
        params = {
            "filename": f"submissions/{name}.json",
            "value": json_content,
            "message": f"Add {name}"
        }
        final_url = f"{base}?{urllib.parse.urlencode(params)}"

        Colors.print("\n=== Submit to Global Index ===", Colors.HEADER)
        print(f"{final_url}\n")
        Colors.print("Click link to track this repo in the global index.", Colors.OKBLUE)

    def search(self, query):
        """Search for packages."""
        results = self.index.search(query)
        if not results:
            Colors.print(f"No packages found matching '{query}'.", Colors.WARNING)
            return
        
        Colors.print(f"Found {len(results)} packages:", Colors.HEADER)
        for name, desc, url in results:
            print(f"{Colors.BOLD}{name}{Colors.ENDC} - {desc} ({url})")

    def uninstall(self, name):
        """Remove a package and its binaries."""
        install_path = INSTALL_DIR / name
        if not install_path.exists():
            Colors.print(f"Package '{name}' is not installed.", Colors.FAIL)
            return

        Colors.print(f"Uninstalling {name}...", Colors.HEADER)
        
        # 1. Remove linked binaries
        # We check BIN_DIR for any symlinks or shims that point into the install_path.
        count = 0
        for bin_file in BIN_DIR.iterdir():
            if not bin_file.is_file():
                continue
            
            should_remove = False
            try:
                # Windows .bat shim check
                if os.name == 'nt' and bin_file.suffix.lower() == '.bat':
                    try:
                        # Read the batch file to see if it points to our install dir
                        content = bin_file.read_text(encoding='utf-8', errors='ignore')
                        # Simple check: does the unique install path string appear in the bat file?
                        # We use the absolute path string.
                        if str(install_path.resolve()) in content or str(install_path) in content:
                            should_remove = True
                    except OSError:
                        pass
                
                # Unix symlink check
                elif bin_file.is_symlink():
                    target = bin_file.resolve()
                    # Check if target is inside install_path
                    # pathlib.Path.is_relative_to() is available in Python 3.9+
                    # We'll use string check for compatibility or try/except
                    if str(install_path.resolve()) in str(target):
                        should_remove = True

                if should_remove:
                    bin_file.unlink()
                    Colors.print(f"Removed shim/link: {bin_file.name}", Colors.OKBLUE)
                    count += 1

            except OSError as e:
                Colors.print(f"Error checking {bin_file.name}: {e}", Colors.WARNING)

        # 2. Remove package directory
        safe_rmtree(install_path)
        Colors.print(f"Successfully uninstalled {name}.", Colors.OKGREEN)


def main():
    parser = argparse.ArgumentParser(description="Anvil: Source Forge")
    subparsers = parser.add_subparsers(dest="command")

    # FORGE: The main tool. Accepts Name, URL, or Path.
    forge_parser = subparsers.add_parser("forge", help="Install from Index, URL, or Path")
    forge_parser.add_argument("target")
    forge_parser.add_argument("--msvc-runtime", choices=['MD', 'MT'], help="Override MSVC runtime used for builds (MD or MT)")
    forge_parser.add_argument("--force-pic", action='store_true', help="Force -fPIC on POSIX builds (overrides env/meta)")
    forge_parser.add_argument("--no-release-check", action='store_true', help="Disable GitHub release check; force build from source")

    # SUBMIT: Add to index
    subparsers.add_parser("submit", help="Add URL to index").add_argument("url")

    # SEARCH
    subparsers.add_parser("search", help="Search for packages").add_argument("query")

    # UNINSTALL
    subparsers.add_parser("uninstall", help="Uninstall a package").add_argument("name")

    subparsers.add_parser("update", help="Update index")
    subparsers.add_parser("list", help="List installed")

    subparsers.add_parser("housekeeping", help="Clean up builds and binaries")

    args = parser.parse_args()
    anvil = Anvil()

    if args.command == "forge":
        anvil.forge(args.target, msvc_runtime=args.msvc_runtime, force_pic=args.force_pic, check_release=not getattr(args, 'no_release_check', False))
    elif args.command == "submit":
        anvil.submit(args.url)
    elif args.command == "search":
        anvil.search(args.query)
    elif args.command == "uninstall":
        anvil.uninstall(args.name)
    elif args.command == "update":
        anvil.index.update()
    elif args.command == "list":
        for p in INSTALL_DIR.iterdir():
            print(p.name)
    elif args.command == "housekeeping":
        anvil.housekeeping()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()