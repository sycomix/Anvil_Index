@echo off
setlocal EnableDelayedExpansion

:: Configuration
set "ANVIL_SOURCE_URL=https://raw.githubusercontent.com/sycomix/Anvil_Index/main/anvil.py"

set "ANVIL_ROOT=%USERPROFILE%\.anvil"
set "CORE_DIR=%ANVIL_ROOT%\core"
set "BIN_DIR=%ANVIL_ROOT%\bin"

echo [ANVIL] Installing Anvil Package Manager...

:: 1. Check for Python
python --version >nul 2>&1
if !errorlevel! neq 0 goto :NoPython

:: 2. Create Directories
if not exist "%CORE_DIR%" mkdir "%CORE_DIR%"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"

:: 3. Download Anvil
echo [ANVIL] Downloading core files...
curl -f -L -o "%CORE_DIR%\anvil.py" "%ANVIL_SOURCE_URL%"

if !errorlevel! neq 0 goto :DownloadError

:: Double check file content for 404 text just in case curl didn't catch it
findstr /C:"404: Not Found" "%CORE_DIR%\anvil.py" >nul
if !errorlevel! equ 0 goto :ContentError

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
:: setx can sometimes truncate long paths, but it is the standard way to set persistent vars
setx PATH "%BIN_DIR%;%PATH%" >nul

echo.
echo [SUCCESS] Anvil has been installed!
echo.
echo You may need to restart your command prompt or terminal for the PATH changes to take effect.
echo Try running: anvil --help
echo.
goto :End

:: --- Error Handlers ---

:NoPython
echo [ERROR] Python is not installed or not in your PATH.
echo Please install Python from https://python.org or the Microsoft Store.
goto :End

:DownloadError
echo [ERROR] Failed to download anvil.py (HTTP Error).
echo Please check that the URL exists: %ANVIL_SOURCE_URL%
goto :End

:ContentError
echo [ERROR] Downloaded file contains '404: Not Found'.
echo The URL is incorrect or the file does not exist on GitHub yet.
if exist "%CORE_DIR%\anvil.py" del "%CORE_DIR%\anvil.py"
goto :End

:End
pause