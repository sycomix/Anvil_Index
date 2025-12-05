#!/usr/bin/env python3
"""Anvil - a small source-based package manager (forging packages from source and managing an index)."""
import os
import sys
import json
import shutil
import subprocess
import argparse
import platform
import urllib.request
import urllib.parse
# tarfile and zipfile removed; using shell tools in AutoBuilder
import sqlite3
from pathlib import Path
import stat
import time

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
        if os.name == 'nt':
            print(f"{prefix} {msg}")
        else:
            print(f"{color}{prefix} {msg}{Colors.ENDC}")

# --- Utilities ---
def run_cmd(command, cwd=None, shell=True, verbose=True):
    """Run a shell command and return on success.

    If the command fails, prints an error and exits unless the command is
    a git command, in which case the original exception is re-raised.
    """
    try:
        if verbose:
            subprocess.check_call(command, cwd=cwd, shell=shell)
        else:
            subprocess.check_call(command, cwd=cwd, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        Colors.print(f"Command failed: {command}", Colors.FAIL)
        if "git" in command:
            raise
        sys.exit(1)

def run_cmd_output(command, cwd=None, shell=True):
    """Run a command and return its stdout or None if the command fails.

    This function is a convenience wrapper around subprocess.check_output,
    returning None on failure so callers can detect absence of output.
    """
    try:
        out = subprocess.check_output(command, cwd=cwd, shell=shell, stderr=subprocess.DEVNULL)
        return out.decode('utf-8').strip()
    except subprocess.CalledProcessError:
        return None


def _on_rm_error(func, path, exc_info):
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


def safe_rmtree(path, retries: int = 3, delay: float = 0.5):
    """Remove directory tree safely, dealing with Windows read-only attributes.
    Retries removal a few times with small delays in case files are transiently locked.
    """
    if not path.exists():
        return
    attempt = 0
    last_err = None
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
    def detect(source_path, install_prefix):
        steps = []
        # 1. Check for explicit 'anvil.json' in the repo (The "Gold Standard")
        if (source_path / "anvil.json").exists():
            with open(source_path / "anvil.json", encoding='utf-8') as f:
                data = json.load(f)
                build_deps = data.get("build_dependencies", [])
                if build_deps:
                    AutoBuilder.install_build_dependencies(build_deps)
                return data.get("build", {}).get("common", []), data.get("binaries", [])
        elif (source_path / "setup.py").exists():
            Colors.print("Detected Python project (setup.py)", Colors.OKBLUE)
            steps = [
                f"{sys.executable} -m pip install . --target {install_prefix} --upgrade"
            ]
            return steps, []
        elif (source_path / "requirements.txt").exists():
            Colors.print("Detected Python requirements", Colors.OKBLUE)
            steps = [f"{sys.executable} -m pip install -r requirements.txt --target {install_prefix}"]
            return steps, []
        # Handle Makefile variants (GNUmakefile, Makefile, makefile)
        elif any((source_path / name).exists() for name in ("Makefile", "GNUmakefile", "makefile")):
            Colors.print("Detected Makefile", Colors.OKBLUE)
            # Determine which make binary is available (gmake, make, mingw32-make, nmake)
            make_bin = shutil.which("make") or shutil.which("gmake") or shutil.which("mingw32-make") or shutil.which("nmake")
            if not make_bin:
                Colors.print("Make not found on PATH. Please install build tools or use anvil.json.", Colors.WARNING)
                return [], []

            # Quote install path
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
            steps = [f"{make_bin}"]
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
                    return steps, [bin_name]
                steps.append(AutoBuilder._copy_build_bins)
            return steps, []
        elif (source_path / "CMakeLists.txt").exists():
            Colors.print("Detected CMake project", Colors.OKBLUE)
            steps = [
                "mkdir -p build",
                f"cd build && cmake .. -DCMAKE_INSTALL_PREFIX={install_prefix}",
                "cd build && make",
                "cd build && make install"
            ]
            return steps, []
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
            return steps, []
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
                return steps, [binary_name]
            else:
                Colors.print('No Go binary found (library-only module). Skipping direct build.', Colors.WARNING)
                return [], []
        elif (source_path / "package.json").exists():
            Colors.print("Detected Node.js project (package.json)", Colors.OKBLUE)
            steps = [
                "npm install",
                "npm run build || true"
            ]
            return steps, []
        elif (source_path / "pyproject.toml").exists():
            Colors.print("Detected Python project (pyproject.toml)", Colors.OKBLUE)
            steps = [
                f"{sys.executable} -m pip install . --target {install_prefix} --upgrade"
            ]
            return steps, []
        elif (source_path / "meson.build").exists():
            Colors.print("Detected Meson project (meson.build)", Colors.OKBLUE)
            steps = [
                "meson setup build",
                "ninja -C build",
                f"ninja -C build install --destdir={install_prefix} || true"
            ]
            return steps, []
        elif (source_path / "SConstruct").exists():
            Colors.print("Detected SCons project (SConstruct)", Colors.OKBLUE)
            steps = [
                f"scons PREFIX={install_prefix}",
                f"scons install PREFIX={install_prefix} || true"
            ]
            return steps, []
        elif (source_path / "build.gradle").exists() or (source_path / "gradlew").exists():
            Colors.print("Detected Gradle project (build.gradle)", Colors.OKBLUE)
            gradle_cmd = "./gradlew" if (source_path / "gradlew").exists() else "gradle"
            steps = [
                f"{gradle_cmd} build",
                f"cp -r build/libs/* {install_prefix}/ || true"
            ]
            return steps, []
        elif (source_path / "WORKSPACE").exists() or (source_path / "BUILD").exists():
            Colors.print("Detected Bazel project (WORKSPACE/BUILD)", Colors.OKBLUE)
            steps = [
                "bazel build //...",
                f"cp -r bazel-bin/* {install_prefix}/ || true"
            ]
            return steps, []
        elif any(source_path.glob('*.csproj')):
            Colors.print("Detected .NET project (csproj)", Colors.OKBLUE)
            steps = [
                f"dotnet publish -c Release -o {install_prefix}"
            ]
            return steps, []
        elif (source_path / "build.zig").exists() or (source_path / "zig.toml").exists():
            Colors.print("Detected Zig project (build.zig)", Colors.OKBLUE)
            steps = [
                "zig build -Drelease-safe",
                f"cp zig-out/bin/* {install_prefix / 'bin'} || true"
            ]
            return steps, []
        elif (source_path / "pom.xml").exists():
            Colors.print("Detected Java project (pom.xml)", Colors.OKBLUE)
            steps = [
                "mvn package",
                f"cp target/*.jar {install_prefix}/"
            ]
            return steps, []
        else:
            # Archives (.tar.xz, .7z, etc.)
            for ext in [".tar.xz", ".7z", ".tar.bz2", ".tar.gz", ".tgz", ".tar", ".zip"]:
                for file in source_path.glob(f"*{ext}"):
                    Colors.print(f"Detected archive: {file.name}", Colors.OKBLUE)
                    if ext == ".zip":
                        steps = [f"unzip -o {file} -d {install_prefix}"]
                    else:
                        steps = [f"tar -xf {file} -C {install_prefix}"]
                    return steps, []
            if (source_path / ".hg").exists():
                Colors.print("Detected Mercurial repository", Colors.OKBLUE)
                steps = ["hg pull", "hg update"]
                return steps, []
            if (source_path / ".svn").exists():
                Colors.print("Detected SVN repository", Colors.OKBLUE)
                steps = ["svn update"]
                return steps, []
            Colors.print("No build system detected. Copying files as-is.", Colors.WARNING)
            steps = [f"cp -r ./* {install_prefix}/"]
            return steps, []


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

    def get_url(self, name):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT url FROM repositories WHERE name=?", (name,))
            result = c.fetchone()
            return result[0] if result else None

    def has_url(self, url):
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

    def add_local(self, name, url):
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
    def normalize_url(url: str) -> str:
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

    def housekeeping(self):
        """
        Cleans up build directories, orphaned binaries, and unused dependencies.
        """
        Colors.print("Running housekeeping...", Colors.HEADER)
        # Remove build directory
        if BUILD_DIR.exists():
            safe_rmtree(BUILD_DIR)
            Colors.print("Build directory cleaned.", Colors.OKGREEN)
        # Remove orphaned binaries (not in installed packages)
        installed = {p.name for p in INSTALL_DIR.iterdir() if p.is_dir()}
        for bin_file in BIN_DIR.iterdir():
            if bin_file.is_file():
                name = bin_file.stem
                if name not in installed:
                    try:
                        bin_file.unlink()
                        Colors.print(f"Removed orphaned binary: {bin_file.name}", Colors.OKBLUE)
                    except OSError as e:
                        Colors.print(f"Failed to remove binary: {bin_file.name} ({e})", Colors.WARNING)
        Colors.print("Housekeeping complete.", Colors.OKGREEN)

    def forge(self, target):
        """
        Target can be:
        1. A package name in the index (e.g., 'htop')
        2. A git URL (e.g., 'https://github.com/foo/bar')
        3. A local path (e.g., './my-project')
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
        steps, binaries = AutoBuilder.detect(build_path, install_path)

        # Build
        Colors.print("Forging (Building)...", Colors.OKBLUE)
        if install_path.exists():
            safe_rmtree(install_path)
        install_path.mkdir(parents=True, exist_ok=True)

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
                run_cmd(rendered, cwd=build_path)

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


def main():
    parser = argparse.ArgumentParser(description="Anvil: Source Forge")
    subparsers = parser.add_subparsers(dest="command")

    # FORGE: The main tool. Accepts Name, URL, or Path.
    subparsers.add_parser("forge", help="Install from Index, URL, or Path").add_argument("target")

    # SUBMIT: Add to index
    subparsers.add_parser("submit", help="Add URL to index").add_argument("url")

    subparsers.add_parser("update", help="Update index")
    subparsers.add_parser("list", help="List installed")

    subparsers.add_parser("housekeeping", help="Clean up builds and binaries")

    args = parser.parse_args()
    anvil = Anvil()

    if args.command == "forge":
        anvil.forge(args.target)
    elif args.command == "submit":
        anvil.submit(args.url)
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