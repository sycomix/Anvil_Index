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
HAMMERS_DIR = ANVIL_ROOT / "hammers"
INDEX_DIR = ANVIL_ROOT / "index"
BUILD_DIR = ANVIL_ROOT / "build"
INSTALL_DIR = ANVIL_ROOT / "opt"
BIN_DIR = ANVIL_ROOT / "bin"

# The central registry of sources
INDEX_REPO_URL = "https://github.com/sycomix/Anvil_Index.git"

# ANSI Colors
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
        if os.name == 'nt': # No ANSI on basic cmd.exe
            print(f"{prefix} {msg}")
        else:
            print(f"{color}{prefix} {msg}{Colors.ENDC}")

# --- Utilities ---
def get_os_key():
    """Returns the OS key for formula parsing."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    elif system == "windows":
        return "windows"
    elif "android" in os.environ.get("PREFIX", ""):
        return "android"
    else:
        return "linux"

def run_cmd(command, cwd=None, shell=True, verbose=True):
    """Executes a shell command."""
    try:
        if verbose:
            subprocess.check_call(command, cwd=cwd, shell=shell)
        else:
            subprocess.check_call(command, cwd=cwd, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        Colors.print(f"Command failed: {command}", Colors.FAIL)
        # Don't exit immediately on git errors to allow offline fallback
        if "git" in command:
             raise
        sys.exit(1)

def download_file(url, dest):
    Colors.print(f"Downloading {url}...", Colors.OKBLUE)
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        Colors.print(f"Download error: {e}", Colors.FAIL)
        sys.exit(1)

# --- The Core Logic ---

class Formula:
    def __init__(self, file_path):
        self.file_path = Path(file_path)
        with open(file_path, 'r') as f:
            self.data = json.load(f)
        
        self.name = self.data.get("name")
        self.version = self.data.get("version")
        self.desc = self.data.get("description", "No description")
        self.url = self.data.get("url")
        self.type = self.data.get("type", "git") # git or archive
        self.binaries = self.data.get("binaries", []) # List of executables to link
        
        # Build steps
        self.build_steps = self.data.get("build", {}).get("common", [])
        
        # OS Specific Overrides
        os_key = get_os_key()
        if os_key in self.data.get("build", {}):
            self.build_steps = self.data.get("build", {})[os_key]
            
        self.dependencies = self.data.get("dependencies", [])

class RepoIndex:
    def __init__(self):
        self.db_path = INDEX_DIR / "index.db"
        self._ensure_exists()

    def _ensure_exists(self):
        """Ensures the index directory and a fallback DB exist."""
        if not INDEX_DIR.exists():
            INDEX_DIR.mkdir(parents=True, exist_ok=True)
            # Try to clone
            try:
                Colors.print(f"Cloning Index from {INDEX_REPO_URL}...", Colors.OKBLUE)
                run_cmd(f"git clone {INDEX_REPO_URL} .", cwd=INDEX_DIR, verbose=True)
            except:
                Colors.print("Could not clone remote index (offline or repo missing). Creating local cache.", Colors.WARNING)
        
        # Check for DB file, if missing (e.g. failed clone), create bootstrap
        if not self.db_path.exists():
            self._create_bootstrap_db()

    def _create_bootstrap_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS repositories
                     (name text PRIMARY KEY, url text, description text)''')
        # Seed with examples
        c.execute("INSERT OR IGNORE INTO repositories VALUES ('core', 'https://github.com/sycomix/anvil-core.git', 'Essential system tools')")
        c.execute("INSERT OR IGNORE INTO repositories VALUES ('games', 'https://github.com/sycomix/anvil-games.git', 'Fun and Games')")
        c.execute("INSERT OR IGNORE INTO repositories VALUES ('science', 'https://github.com/sycomix/anvil-science.git', 'Scientific computing tools')")
        conn.commit()
        conn.close()

    def add_repo(self, name, url, description):
        """Adds a repository to the local index."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                c.execute("INSERT INTO repositories VALUES (?, ?, ?)", (name, url, description))
                conn.commit()
            Colors.print(f"Repository '{name}' registered in local index.", Colors.OKGREEN)
        except sqlite3.IntegrityError:
            Colors.print(f"Repository '{name}' already exists in the index.", Colors.FAIL)
        except Exception as e:
            Colors.print(f"Failed to add repository: {e}", Colors.FAIL)

    def get_url(self, name):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT url FROM repositories WHERE name=?", (name,))
            result = c.fetchone()
            return result[0] if result else None

    def list_repos(self):
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            return c.execute("SELECT name, description FROM repositories").fetchall()

    def update(self):
        if (INDEX_DIR / ".git").exists():
            Colors.print("Updating Central Index...", Colors.HEADER)
            try:
                run_cmd("git pull", cwd=INDEX_DIR, verbose=False)
            except:
                Colors.print("Failed to pull index. Using cached version.", Colors.WARNING)
        else:
             # Re-try clone if it failed previously
             try:
                run_cmd(f"git clone {INDEX_REPO_URL} .", cwd=INDEX_DIR)
             except:
                pass


class Anvil:
    def __init__(self):
        self._setup_dirs()
        self._ensure_path()
        self.index = RepoIndex()

    def _setup_dirs(self):
        for p in [ANVIL_ROOT, HAMMERS_DIR, BUILD_DIR, INSTALL_DIR, BIN_DIR, INDEX_DIR]:
            p.mkdir(parents=True, exist_ok=True)
            
        # Create a default "Core" hammer if empty
        core_hammer = HAMMERS_DIR / "core"
        if not core_hammer.exists():
            core_hammer.mkdir()
            self._create_sample_formula(core_hammer)

    def _create_sample_formula(self, path):
        """Creates a dummy formula for demonstration."""
        sample = {
            "name": "hello-anvil",
            "version": "1.0.0",
            "description": "A test package that compiles a simple C file",
            "url": "https://github.com/octocat/Hello-World.git", 
            "type": "git",
            "dependencies": [],
            "binaries": ["hello_anvil"],
            "build": {
                "common": [
                    "echo 'int main() { printf(\"Hello from Anvil!\\n\"); return 0; }' > hello_anvil.c",
                    "gcc hello_anvil.c -o hello_anvil"
                ],
                "windows": [
                    "echo int main() { printf(\"Hello from Anvil!\\n\"); return 0; } > hello_anvil.c",
                    "gcc hello_anvil.c -o hello_anvil.exe"
                ]
            }
        }
        
        # Windows adjustment for the sample binary name
        if os.name == 'nt':
            sample['binaries'] = ["hello_anvil.exe"]

        with open(path / "hello-anvil.json", 'w') as f:
            json.dump(sample, f, indent=2)

    def _ensure_path(self):
        """Checks if BIN_DIR is in PATH (Warning only)."""
        if str(BIN_DIR) not in os.environ["PATH"]:
            Colors.print(f"WARNING: {BIN_DIR} is not in your PATH.", Colors.WARNING)
            Colors.print(f"Add it to your shell profile (.bashrc, .zshrc, or Environment Variables).", Colors.WARNING)

    def find_formula(self, name):
        """Searches all hammers for a formula json file."""
        for hammer in HAMMERS_DIR.iterdir():
            if hammer.is_dir():
                found = list(hammer.glob(f"{name}.json"))
                if found:
                    return Formula(found[0])
        return None

    def list_packages(self):
        Colors.print("Installed Packages:", Colors.HEADER)
        if not INSTALL_DIR.exists():
            return
        for item in INSTALL_DIR.iterdir():
            if item.is_dir():
                print(f" - {item.name}")

    def list_available_repos(self):
        Colors.print("Available Repositories (from Index):", Colors.HEADER)
        repos = self.index.list_repos()
        for name, desc in repos:
            # Check if installed
            status = f"{Colors.OKGREEN}[Installed]{Colors.ENDC}" if (HAMMERS_DIR / name).exists() else ""
            print(f" - {Colors.BOLD}{name}{Colors.ENDC}: {desc} {status}")

    def search_hammers(self, term):
        Colors.print(f"Searching for '{term}' in local hammers...", Colors.HEADER)
        found = False
        for hammer in HAMMERS_DIR.iterdir():
            if hammer.is_dir():
                for f_file in hammer.glob("*.json"):
                    if term in f_file.stem:
                        print(f" - {f_file.stem} ({hammer.name})")
                        found = True
        if not found:
            print("No packages found.")

    def hammer_repo(self, name, url=None):
        """Clones a git repository as a Hammer."""
        
        # If no URL provided, look up in Index
        if not url:
            Colors.print(f"Looking up '{name}' in index...", Colors.OKBLUE)
            url = self.index.get_url(name)
            if not url:
                Colors.print(f"Error: Unknown repo '{name}' and no URL provided.", Colors.FAIL)
                return

        target = HAMMERS_DIR / name
        if target.exists():
            Colors.print(f"Hammer {name} already exists.", Colors.WARNING)
            return
        
        Colors.print(f"Hammering {name} from {url}...", Colors.OKBLUE)
        run_cmd(f"git clone {url} {target}")
        Colors.print("Hammer added successfully.", Colors.OKGREEN)

    def unhammer_repo(self, name):
        target = HAMMERS_DIR / name
        if not target.exists():
            Colors.print(f"Hammer {name} does not exist.", Colors.FAIL)
            return
        shutil.rmtree(target)
        Colors.print(f"Hammer {name} removed.", Colors.OKGREEN)

    def register_repo(self, name, url, description):
        self.index.add_repo(name, url, description)

    def submit_repo(self, name, url, description):
        """Registers locally AND generates a GitHub PR URL for upstream submission."""
        
        # 1. Register locally first so it is usable immediately
        Colors.print(f"Step 1: Registering '{name}' locally...", Colors.HEADER)
        self.index.add_repo(name, url, description)

        # 2. Construct the JSON payload for the PR
        payload = {
            "name": name,
            "url": url,
            "description": description
        }
        json_content = json.dumps(payload, indent=2)
        
        # 3. Construct the GitHub URL
        # We assume the Index repo accepts submissions in a 'submissions' folder
        # The user can then click this link, which opens the "Create new file" UI on GitHub
        # GitHub will automatically fork the repo if the user doesn't have write access.
        
        base_url = "https://github.com/sycomix/Anvil_Index/new/main"
        params = {
            "filename": f"submissions/{name}.json",
            "value": json_content,
            "message": f"Add {name} to Anvil Index"
        }
        
        # Python's urllib.parse to safely encode URL parameters
        query_string = urllib.parse.urlencode(params)
        final_url = f"{base_url}?{query_string}"
        
        Colors.print("\nStep 2: Submit to Anvil Index", Colors.HEADER)
        Colors.print(f"To submit '{name}' upstream, please open the following URL in your browser:", Colors.OKBLUE)
        print(f"\n{final_url}\n")
        Colors.print("This will create a Pull Request with your repository details.", Colors.OKGREEN)
        Colors.print("Anvil staff will review your submission and merge it into the index.", Colors.OKGREEN)

    def update_hammers(self):
        # 1. Update the Index
        self.index.update()

        # 2. Update the Hammers
        Colors.print("Updating Local Hammers...", Colors.HEADER)
        for hammer in HAMMERS_DIR.iterdir():
            if (hammer / ".git").exists():
                Colors.print(f"Updating {hammer.name}...", Colors.OKBLUE)
                try:
                    run_cmd("git pull", cwd=hammer, verbose=False)
                except:
                    Colors.print(f"Failed to update {hammer.name}", Colors.FAIL)
        Colors.print("Update complete.", Colors.OKGREEN)

    def forge(self, package_name):
        formula = self.find_formula(package_name)
        if not formula:
            Colors.print(f"Package '{package_name}' not found in any Hammer.", Colors.FAIL)
            return

        Colors.print(f"Forging {formula.name} v{formula.version}...", Colors.HEADER)

        # 1. Check Dependencies
        if formula.dependencies:
            Colors.print(f"Forging dependencies: {', '.join(formula.dependencies)}", Colors.OKBLUE)
            for dep in formula.dependencies:
                self.forge(dep)

        # 2. Prepare Build Env
        build_path = BUILD_DIR / formula.name
        if build_path.exists():
            shutil.rmtree(build_path)
        build_path.mkdir()

        # 3. Fetch Source
        Colors.print("Fetching source...", Colors.OKBLUE)
        if formula.type == "git":
            run_cmd(f"git clone --depth 1 {formula.url} .", cwd=build_path)
        elif formula.type == "archive":
            local_file = build_path / "source_archive"
            download_file(formula.url, local_file)
            if str(formula.url).endswith(".zip"):
                with zipfile.ZipFile(local_file, 'r') as zip_ref:
                    zip_ref.extractall(build_path)
            else: # Assume tar
                with tarfile.open(local_file) as tar:
                    tar.extractall(build_path)
        
        # 4. Build
        Colors.print("Building...", Colors.OKBLUE)
        for step in formula.build_steps:
            # Simple variable substitution
            step = step.replace("{PREFIX}", str(INSTALL_DIR / formula.name))
            Colors.print(f"Running: {step}")
            run_cmd(step, cwd=build_path)

        # 5. Install (Move to Opt)
        Colors.print("Installing...", Colors.OKBLUE)
        install_path = INSTALL_DIR / formula.name
        if install_path.exists():
            shutil.rmtree(install_path)
        
        # If the build step didn't handle moving files, we move the whole build dir
        # A real package manager would have a specific 'install' step in the JSON
        # For this prototype, we copy the whole build dir to opt
        shutil.copytree(build_path, install_path)
        
        # 6. Link Binaries
        self._link_binaries(formula, install_path)
        
        # Cleanup
        shutil.rmtree(build_path)
        Colors.print(f"Successfully forged {formula.name}!", Colors.OKGREEN)

    def _link_binaries(self, formula, install_path):
        for binary in formula.binaries:
            # Recursively find the binary in the install folder
            found_bins = list(install_path.rglob(binary))
            if not found_bins:
                Colors.print(f"Warning: Binary {binary} not found in build output.", Colors.WARNING)
                continue
            
            src_bin = found_bins[0]
            dest_link = BIN_DIR / binary

            if dest_link.exists():
                dest_link.unlink() # Overwrite

            Colors.print(f"Linking {binary}...", Colors.OKBLUE)
            
            if os.name == 'nt':
                # Windows Shim (Batch file)
                # Symlinks on Windows require admin, batch files don't
                shim_path = BIN_DIR / f"{src_bin.stem}.bat"
                with open(shim_path, 'w') as bat:
                    bat.write(f"@echo off\n\"{src_bin}\" %*")
            else:
                # Unix Symlink
                os.chmod(src_bin, 0o755) # Make executable
                os.symlink(src_bin, dest_link)

    def uninstall(self, package_name):
        install_path = INSTALL_DIR / package_name
        if not install_path.exists():
            Colors.print(f"{package_name} is not installed.", Colors.FAIL)
            return

        # Unlink binaries
        # In a real app, we would store a manifest of installed files. 
        # Here we just check the bin dir for matching names roughly.
        formula = self.find_formula(package_name)
        if formula:
            for binary in formula.binaries:
                target = BIN_DIR / binary
                if os.name == 'nt':
                    target = BIN_DIR / f"{Path(binary).stem}.bat"
                
                if target.exists():
                    target.unlink()
                    Colors.print(f"Unlinked {binary}", Colors.OKBLUE)

        shutil.rmtree(install_path)
        Colors.print(f"Uninstalled {package_name}", Colors.OKGREEN)

# --- CLI Entry Point ---

def main():
    parser = argparse.ArgumentParser(description="Anvil: The Source Package Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Commands
    subparsers.add_parser("forge", help="Build and install a package").add_argument("package")
    subparsers.add_parser("uninstall", help="Remove a package").add_argument("package")
    subparsers.add_parser("search", help="Search available packages").add_argument("term")
    subparsers.add_parser("list", help="List installed packages")
    subparsers.add_parser("repos", help="List repositories in the central index")
    subparsers.add_parser("update", help="Update all Hammers and the Index")
    
    hammer_parser = subparsers.add_parser("hammer", help="Add a new formula repository")
    hammer_parser.add_argument("name", help="Name for the hammer")
    hammer_parser.add_argument("url", nargs='?', help="Git URL of the hammer (Optional if in index)")
    
    subparsers.add_parser("unhammer", help="Remove a formula repository").add_argument("name")

    # Register (Local)
    reg_parser = subparsers.add_parser("register", help="Add a repository to the local index only")
    reg_parser.add_argument("name", help="Name of the repository")
    reg_parser.add_argument("url", help="Git URL")
    reg_parser.add_argument("description", nargs='+', help="Description")

    # Submit (Upstream PR)
    sub_parser = subparsers.add_parser("submit", help="Submit a repository to the central index via PR")
    sub_parser.add_argument("name", help="Name of the repository")
    sub_parser.add_argument("url", help="Git URL")
    sub_parser.add_argument("description", nargs='+', help="Description")

    args = parser.parse_args()
    anvil = Anvil()

    if args.command == "forge":
        anvil.forge(args.package)
    elif args.command == "uninstall":
        anvil.uninstall(args.package)
    elif args.command == "search":
        anvil.search_hammers(args.term)
    elif args.command == "list":
        anvil.list_packages()
    elif args.command == "repos":
        anvil.list_available_repos()
    elif args.command == "update":
        anvil.update_hammers()
    elif args.command == "hammer":
        anvil.hammer_repo(args.name, args.url)
    elif args.command == "unhammer":
        anvil.unhammer_repo(args.name)
    elif args.command == "register":
        anvil.register_repo(args.name, args.url, " ".join(args.description))
    elif args.command == "submit":
        anvil.submit_repo(args.name, args.url, " ".join(args.description))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()