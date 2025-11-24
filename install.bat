@echo off
setlocal

:: Configuration
:: REPLACE THIS URL with the actual raw URL of your anvil.py
set "ANVIL_SOURCE_URL=https://raw.githubusercontent.com/sycomix/Anvil/main/anvil.py"

set "ANVIL_ROOT=%USERPROFILE%\.anvil"
set "CORE_DIR=%ANVIL_ROOT%\core"
set "BIN_DIR=%ANVIL_ROOT%\bin"

echo [ANVIL] Installing Anvil Package Manager...

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python from https://python.org or the Microsoft Store.
    pause
    exit /b 1
)

:: 2. Create Directories
if not exist "%CORE_DIR%" mkdir "%CORE_DIR%"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"

:: 3. Download Anvil
echo [ANVIL] Downloading core files...
curl -L -o "%CORE_DIR%\anvil.py" "%ANVIL_SOURCE_URL%"

if not exist "%CORE_DIR%\anvil.py" (
    echo [ERROR] Failed to download anvil.py. Check your internet connection or the URL.
    pause
    exit /b 1
)

:: 4. Create Batch Shim
echo [ANVIL] Creating executable shim...
(
echo @echo off
echo python "%CORE_DIR%\anvil.py" %%*
) > "%BIN_DIR%\anvil.bat"

:: 5. Initialize
echo [ANVIL] Initializing environment...
call "%BIN_DIR%\anvil.bat" update

:: 6. Add to PATH
echo [ANVIL] Adding to User PATH...
:: This checks if the path is already there to avoid duplication could be complex in batch,
:: so we stick to a simple setx. 
:: WARNING: setx truncates PATH if it's > 1024 chars, but it's the standard way in batch.
setx PATH "%BIN_DIR%;%PATH%" >nul

echo.
echo [SUCCESS] Anvil has been installed!
echo.
echo You may need to restart your command prompt or terminal for the PATH changes to take effect.
echo Try running: anvil --help
echo.
pause