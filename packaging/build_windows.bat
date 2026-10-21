@echo off
REM Build OpenMCL.exe. Run this on Windows, from the project root:
REM     packaging\build_windows.bat
REM Add "onedir" as an argument for a folder build that starts faster.
setlocal

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH. Install Python 3.10 or newer from python.org
    echo and tick "Add python.exe to PATH" during setup.
    exit /b 1
)

echo == Installing build dependencies
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller || exit /b 1

echo == Cleaning previous build
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo == Building
if /i "%1"=="onedir" (
    python -m PyInstaller openmcl.spec --noconfirm -- --onedir || exit /b 1
    echo.
    echo Done: dist\OpenMCL\OpenMCL.exe
) else (
    python -m PyInstaller openmcl.spec --noconfirm --version-file packaging\version_info.txt || exit /b 1
    echo.
    echo Done: dist\OpenMCL.exe
)

echo == Smoke test
if /i "%1"=="onedir" (
    dist\OpenMCL\OpenMCL.exe --version
) else (
    dist\OpenMCL.exe --version
)
endlocal
