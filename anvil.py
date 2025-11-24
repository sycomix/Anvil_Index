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
                return data.get("build", {}).get("common", []), data.get("binaries", [])

        # 2. Python (setup.py)
        if (source_path / "setup.py").exists():
            Colors.print("Detected Python project (setup.py)", Colors.OKBLUE)
            steps = [
                f"{sys.executable} -m pip install . --target {install_prefix} --upgrade"
            ]
            # Heuristic: bin usually ends up in 'bin' subdir of target, handled by link step
            return steps, [] # Binaries handled dynamically later

        # 3. Python (requirements.txt only)
        elif (source_path / "requirements.txt").exists():
            Colors.print("Detected Python requirements", Colors.OKBLUE)
            steps = [f"{sys.executable} -m pip install -r requirements.txt --target {install_prefix}"]
            return steps, []

        # 4. Make
        elif (source_path / "Makefile").exists():
            Colors.print("Detected Makefile", Colors.OKBLUE)
            steps = [
                "make",
                f"make install PREFIX={install_prefix}"
            ]
            return steps, []

        # 5. CMake
        elif (source_path / "CMakeLists.txt").exists():
            Colors.print("Detected CMake project", Colors.OKBLUE)
            steps = [
                "mkdir -p build",
                f"cd build && cmake .. -DCMAKE_INSTALL_PREFIX={install_prefix}",
                "cd build && make",
                "cd build && make install"
            ]
            return steps, []

        # 6. Rust (Cargo)
        elif (source_path / "Cargo.toml").exists():
            Colors.print("Detected Rust project", Colors.OKBLUE)
            # Installs binary to {install_prefix}/bin
            steps = [f"cargo install --path . --root {install_prefix}"]
            return steps, []

        else:
            Colors.print("No build system detected. Copying files as-is.", Colors.WARNING)
            # Fallback: Just copy everything
            steps = [f"cp -r ./* {install_prefix}/"]
            return steps, []

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

        if build_path.exists(): shutil.rmtree(build_path)
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
        if install_path.exists(): shutil.rmtree(install_path)
        install_path.mkdir(parents=True, exist_ok=True)

        for step in steps:
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

    args = parser.parse_args()
    anvil = Anvil()

    if args.command == "forge": anvil.forge(args.target)
    elif args.command == "submit": anvil.submit(args.url)
    elif args.command == "update": anvil.index.update()
    elif args.command == "list": 
        for p in INSTALL_DIR.iterdir(): print(p.name)
    else: parser.print_help()

if __name__ == "__main__":
    main()