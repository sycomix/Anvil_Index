Windows (Command Prompt)Run the following command:curl -L -o install.bat [https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.bat](https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.bat) && install.bat
Note: After installation, you may need to restart your terminal to ensure anvil is in your PATH.🛠 UsageCore Commands1. Forge (Install) a PackageDownloads source code, compiles it, and links binaries to your path.anvil forge hello-anvil 2. Search for PackagesSearches your local "hammers" (package repositories) for formulas matching the term.anvil search htop 3. Uninstall a PackageRemoves the package directory and unlinks executables.anvil uninstall hello-anvil 4. Update AnvilUpdates the central index and pulls changes for all local hammers.anvil update
Anvil Package Manager

Anvil is a decentralized, source-based package manager designed to work across macOS, Linux, Windows, and Android (via Termux). Instead of shipping pre-built binaries, Anvil "forges" packages from source repositories so builds are tailored to each environment.

Quick Install

Unix (macOS, Linux, Android/Termux)
```bash
curl -fsSL https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.sh | bash
```

Windows (PowerShell)
```powershell
Invoke-WebRequest -UseBasicParsing https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.bat -OutFile install.bat; ./install.bat
```

Windows (CMD)
```bat
curl -L -o install.bat https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.bat && install.bat
```

Note: You may need to restart your terminal or re-open your shell for PATH changes to take effect.

Usage
-------
Core commands:
- Forge (install from Index, URL, or local path):
```bash
python anvil.py forge ./path-to-repo
python anvil.py forge https://github.com/user/repo.git
```
- Submit a hammer (adds to local index and generates a PR link):
```bash
python anvil.py submit https://github.com/user/hammer.git
```
- Update the local index from the cloned central index:
```bash
python anvil.py update
```
- Process PR-ready JSON submissions locally:
```bash
python scripts/update_index.py
```
- Housekeeping (clean build dir, orphaned bins):
```bash
python anvil.py housekeeping
```

Supported Build Systems
-----------------------
Anvil's `AutoBuilder` detects common build systems automatically and generates a reasonable build plan. Supported systems include (but are not limited to):
- anvil.json (explicit formula)
- Python: `setup.py`, `requirements.txt`, `pyproject.toml`
- Rust: `Cargo.toml` (library vs binary handling, workspaces supported)
- Go: `go.mod` and `main.go` detection
- Node: `package.json`
- Java: `pom.xml` (Maven) and Gradle (`build.gradle`/`gradlew`)
- C/C++: `Makefile`, `CMakeLists.txt`, `meson.build`

Makefiles & Platform Notes
-------------------------
- The AutoBuilder detects common Makefile filenames (`Makefile`, `GNUmakefile`, `makefile`).
- If `make` is present on PATH the builder runs `make` followed by `make install PREFIX="{PREFIX}"` and `make install DESTDIR="{PREFIX}"` as it increases compatibility.
- On Windows, the detection will try `mingw32-make` and `nmake` if `make` is not found. Users should install appropriate build tools (MSYS2/MinGW or Visual Studio) if building native projects on Windows.
- .NET: `*.csproj` -> `dotnet publish`
- Zig: `build.zig`
- Bazel: `WORKSPACE`/`BUILD` files
- Archives: `*.tar.*`, `*.zip`, `*.7z`

Examples & Formulas
--------------------
An example `anvil.json` formula (preferred when present in repo):
```json
{
	"name": "htop",
	"version": "3.2.0",
	"url": "https://github.com/htop-dev/htop.git",
	"type": "git",
	"dependencies": ["ncurses"],
	"binaries": ["htop"],
	"build": {
		"common": [
			"./autogen.sh",
			"./configure --prefix={PREFIX}",
			"make",
			"make install"
		],
		"windows": [
			"cmake -G \"MinGW Makefiles\" .",
			"mingw32-make"
		]
	}
}
```

Go example
```bash
# Build/create a Go binary
python anvil.py forge https://github.com/user/go-app.git
```

Local git repo example (auto-submit)
```bash
# If a local directory is a git repo with a remote origin, forging it will auto-submit
python anvil.py forge ./submissions/hello-git
```

Developer Notes
---------------
- Entry point: `anvil.py` (Anvil class orchestrates CLI and operations)
- Index DB: `index.db` under `~/.anvil/index/index.db`. `RepoIndex` manages cloning and DB creation.
- AutoBuilder: heuristics in `AutoBuilder.detect` prefer `anvil.json` then try common project files. When adding new build types, add a minimal detection block and include a `submissions/` example for testing.
 - Examples: `submissions/hello-make/` is a sample Makefile-based project that shows how to write an `anvil.json` and a simple Makefile using `DESTDIR` to install into Anvil's `opt` folder, and can be used for local validation.
 - Auto-submission: When an install is performed from a Git repository (via a URL or a local Git repo with a remote origin), Anvil will automatically add the repo to the local index and print a PR link so you can submit it to the central index for approval. This only happens if the remote URL is not already in the index.
	- Note: By default, Anvil will auto-submit any installed repository not already present in the index. You may opt out by setting the `ANVIL_AUTO_SUBMIT` environment variable to `0` or `false` (e.g., `export ANVIL_AUTO_SUBMIT=0` on Unix shells or `setx ANVIL_AUTO_SUBMIT 0` on Windows). See also URL normalization below.
	- URL normalization: Anvil normalizes repository URLs for comparison (converting `git@host:user/repo.git` to `https://host/user/repo`, stripping `.git`, and normalizing host case) to avoid duplicates across different URL formats. This means `git@github.com:user/repo.git` and `https://github.com/user/repo` are treated as the same repository for indexing purposes.
- Tests: N/A — use `python anvil.py forge` against example projects for manual verification.

Contributing
------------
1. Add an example `anvil.json` in `submissions/` when introducing new build detection.
2. Use `python anvil.py forge` to test builds locally.
3. Use `python scripts/update_index.py` to ingest `submissions/*.json` into `index.db`.

Repository Management ("Hammers")Anvil uses "Hammers" (similar to Taps in Homebrew) to store package formulas.List available repositories in the Central Index:anvil repos
Add a new Hammer:# From the central index (by name)
anvil hammer games

# From a specific Git URL

anvil hammer my-repo [https://github.com/user/my-anvil-formulas.git](https://github.com/user/my-anvil-formulas.git)
Remove a Hammer:anvil unhammer games
📦 Creating a Custom Package (Formula)Packages in Anvil are simple JSON files located in ~/.anvil/hammers/<repo_name>/.Example Formula (htop.json){
"name": "htop",
"version": "3.2.0",
"description": "Interactive process viewer",
"url": "[https://github.com/htop-dev/htop.git](https://github.com/htop-dev/htop.git)",
"type": "git",
"dependencies": ["ncurses"],
"binaries": ["htop"],
"build": {
"common": [
"./autogen.sh",
"./configure --prefix={PREFIX}",
"make",
"make install"
],
"android": [
"./autogen.sh",
"./configure --prefix={PREFIX} --host=aarch64-linux-android",
"make",
"make install"
],
"windows": [
"cmake -G \"MinGW Makefiles\" .",
"mingw32-make"
]
}
}
build: A list of shell commands to run.{PREFIX}: Automatically replaced with the installation path (~/.anvil/opt/<name>).binaries: List of executables to symlink into ~/.anvil/bin. Anvil searches recursively for these filenames after the build completes.🌐 The Central IndexAnvil maintains a central database of trusted repositories (index.db).Submitting Your RepositoryIf you have created a collection of formulas (a Hammer), you can submit it to the central index so others can easily discover it.Run the submit command:anvil submit my-hammer [https://github.com/username/my-hammer](https://github.com/username/my-hammer) "A collection of retro games"
Open the Pull Request:Anvil will generate a URL in your terminal. Open it in your browser to automatically create a Pull Request against the Anvil Index.Approval:Once approved by maintainers, your repository will be added to the index.db and distributed to all users via anvil update.📂 Directory Structure~/.anvil/bin: Symlinks (Unix) or Batch shims (Windows) for executables.~/.anvil/opt: Where packages are installed.~/.anvil/hammers: Git repositories containing package formulas.~/.anvil/build: Temporary build directory.~/.anvil/index: Local copy of the central repository index.

## Rust Libraries

If a repository is a Rust library (non-binary crate), Anvil will run `cargo build --release` and copy the built library artifacts into `~/.anvil/opt/<package>/lib` (e.g., `.rlib`, `.so`, `.dylib`, `.dll`, or `.a`). Binaries are not installed via `cargo install` for library-only crates.

## Housekeeping

Use the `anvil housekeeping` command to remove temporary build directories and clean up orphaned binaries in `~/.anvil/bin` that do not correspond to installed packages. This is useful after failed builds or when iterating on formulas locally.
