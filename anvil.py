#!/usr/bin/env python3
import os
import sys
import json
import shutil
import subprocess
import argparse
import platform
import urllib.request
import urllib.parse
import tarfile
import zipfile
import sqlite3
from pathlib import Path
import stat

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
    try:
        if verbose:
            subprocess.check_call(command, cwd=cwd, shell=shell)
        else:
            subprocess.check_call(command, cwd=cwd, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        Colors.print(f"Command failed: {command}", Colors.FAIL)
        if "git" in command: raise
        sys.exit(1)


def _on_rm_error(func, path, exc_info):
    """Error handler for shutil.rmtree to handle read-only files on Windows.
    Tries to make file writable and retries the operation.
    """
    import errno
    # Only handle permission errors
    if not os.access(path, os.W_OK):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            Colors.print(f"Failed to remove {path}: {exc_info}", Colors.FAIL)
    else:
        # Raise if it's a different error
        raise


def safe_rmtree(path):
    """Remove directory tree safely, dealing with Windows read-only attributes.
    Falls back to a best-effort recursive unlink with chmod on errors.
    """
    if not path.exists():
        return
    try:
        shutil.rmtree(path, onerror=_on_rm_error)
    except Exception as e:
        Colors.print(f"Could not remove path {path}: {e}", Colors.WARNING)

# --- Auto-Discovery Build Engine ---

class AutoBuilder:
    """
    Inspects a source directory and generates a build plan
    without requiring a formula file.
    """
    @staticmethod
    def detect(source_path, install_prefix):
        steps = []
        binaries = []
        # 1. Check for explicit 'anvil.json' in the repo (The "Gold Standard")
        if (source_path / "anvil.json").exists():
            with open(source_path / "anvil.json") as f:
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
        elif (source_path / "Makefile").exists():
            Colors.print("Detected Makefile", Colors.OKBLUE)
            steps = [
                "make",
                f"make install PREFIX={install_prefix}"
            ]
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
            except: pass
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
        elif (source_path / "go.mod").exists():
            Colors.print("Detected Go project (go.mod)", Colors.OKBLUE)
            steps = [
                f"go build -o {install_prefix / 'bin' / source_path.name}",
            ]
            return steps, [source_path.name]
        elif (source_path / "package.json").exists():
            Colors.print("Detected Node.js project (package.json)", Colors.OKBLUE)
            steps = [
                "npm install",
                "npm run build || true"
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
            for ext in [".tar.xz", ".7z", ".tar.bz2"]:
                for file in source_path.glob(f"*{ext}"):
                    Colors.print(f"Detected archive: {file.name}", Colors.OKBLUE)
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
                    bin_file.unlink()
                    Colors.print(f"Removed orphaned binary: {bin_file.name}", Colors.OKBLUE)
        Colors.print("Housekeeping complete.", Colors.OKGREEN)

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
            if not item.is_file(): continue
            
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
        except Exception:
            pass
        return False


# --- Index Management ---

class RepoIndex:
    def __init__(self):
        self.db_path = INDEX_DIR / "index.db"
        self._ensure_exists()

    def _ensure_exists(self):
        if not INDEX_DIR.exists():
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            try:
                # Try to clone index, but don't fail if offline/empty
                run_cmd(f"git clone {INDEX_REPO_URL} .", cwd=INDEX_DIR, verbose=False)
            except: pass
        
        if not self.db_path.exists():
            self._create_bootstrap_db()

    def _create_bootstrap_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS repositories
                     (name text PRIMARY KEY, url text, description text)''')
        # Simple bootstrap
        c.execute("INSERT OR IGNORE INTO repositories VALUES ('anvil-core', 'https://github.com/sycomix/anvil-core.git', 'Anvil Core')")
        conn.commit()
        conn.close()

    def get_url(self, name):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT url FROM repositories WHERE name=?", (name,))
            result = c.fetchone()
            return result[0] if result else None

    def add_local(self, name, url):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO repositories VALUES (?, ?, ?)", (name, url, "User added"))
            conn.commit()

    def update(self):
        if (INDEX_DIR / ".git").exists():
            Colors.print("Syncing Central Index...", Colors.HEADER)
            try:
                run_cmd("git pull", cwd=INDEX_DIR, verbose=False)
            except: pass

# --- Main App ---

class Anvil:
    def __init__(self):
        self._setup_dirs()
        self._ensure_path()
        self.index = RepoIndex()

    def _setup_dirs(self):
        for p in [ANVIL_ROOT, BUILD_DIR, INSTALL_DIR, BIN_DIR, INDEX_DIR]:
            p.mkdir(parents=True, exist_ok=True)

    def _ensure_path(self):
        if str(BIN_DIR) not in os.environ["PATH"]:
            Colors.print(f"WARNING: Add {BIN_DIR} to your PATH.", Colors.WARNING)

    def forge(self, target):
        """
        Target can be:
        1. A package name in the index (e.g., 'htop')
        2. A git URL (e.g., 'https://github.com/foo/bar')
        3. A local path (e.g., './my-project')
        """
        
        url = None
        name = None

        # 1. Check if it's a URL
        if target.startswith("http") or target.startswith("git@"):
            url = target
            name = target.split("/")[-1].replace(".git", "")
            Colors.print(f"Direct Forge: {name} from {url}", Colors.HEADER)

        # 2. Check if it's a local path
        elif os.path.exists(target) and os.path.isdir(target):
            url = str(Path(target).resolve())
            name = Path(target).name
            Colors.print(f"Local Forge: {name} from {url}", Colors.HEADER)

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

        if build_path.exists(): safe_rmtree(build_path)
        build_path.mkdir()

        # Fetch Source
        if os.path.exists(target) and os.path.isdir(target):
             # Local copy
             run_cmd(f"cp -r {target}/. {build_path}/")
        else:
             # Git clone
             Colors.print("Cloning source...", Colors.OKBLUE)
             run_cmd(f"git clone --depth 1 {url} .", cwd=build_path)

        # Auto-Detect Build System
        steps, binaries = AutoBuilder.detect(build_path, install_path)
        
        # Build
        Colors.print("Forging (Building)...", Colors.OKBLUE)
        if install_path.exists(): safe_rmtree(install_path)
        install_path.mkdir(parents=True, exist_ok=True)

        for step in steps:
            # Step can be a string (shell command) or a callable (python function)
            if callable(step):
                step(build_path, install_path)
            else:
                Colors.print(f"Running: {step}")
                run_cmd(step, cwd=build_path)

        # Link Binaries (Heuristic + Explicit)
        self._link_binaries(install_path, binaries)

        # Cleanup
        shutil.rmtree(build_path)
        Colors.print(f"Successfully forged {name}!", Colors.OKGREEN)

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

        # 2. Add explicit ones
        for b in explicit_binaries:
            candidates.extend(list(install_path.rglob(b)))

        # 3. Link
        for src in set(candidates): # set for unique
            if src.name.startswith("."): continue # skip hidden
            
            dest = BIN_DIR / src.name
            if dest.exists(): dest.unlink()

            Colors.print(f"Linking {src.name}...", Colors.OKBLUE)
            if os.name == 'nt':
                # Windows Shim
                with open(str(dest) + ".bat", 'w') as bat:
                    bat.write(f"@echo off\n\"{src}\" %*")
            else:
                os.symlink(src, dest)

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

    if args.command == "forge": anvil.forge(args.target)
    elif args.command == "submit": anvil.submit(args.url)
    elif args.command == "update": anvil.index.update()
    elif args.command == "list": 
        for p in INSTALL_DIR.iterdir(): print(p.name)
    elif args.command == "housekeeping":
        anvil.housekeeping()
    else: parser.print_help()

if __name__ == "__main__":
    main()