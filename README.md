Anvil Package ManagerAnvil is a decentralized, source-based package manager designed to work seamlessly across macOS, Linux, Windows, and Android (via Termux).Unlike binary package managers, Anvil "forges" (compiles) packages directly from their source repositories, ensuring you get the latest versions optimized for your specific environment.🚀 InstallationUnix (macOS, Linux, Android/Termux)Run the following command in your terminal:curl -fsSL [https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.sh](https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.sh) | bash
Windows (Command Prompt)Run the following command:curl -L -o install.bat [https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.bat](https://raw.githubusercontent.com/sycomix/Anvil_Index/main/install.bat) && install.bat
Note: After installation, you may need to restart your terminal to ensure anvil is in your PATH.🛠 UsageCore Commands1. Forge (Install) a PackageDownloads source code, compiles it, and links binaries to your path.anvil forge hello-anvil 2. Search for PackagesSearches your local "hammers" (package repositories) for formulas matching the term.anvil search htop 3. Uninstall a PackageRemoves the package directory and unlinks executables.anvil uninstall hello-anvil 4. Update AnvilUpdates the central index and pulls changes for all local hammers.anvil update
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
