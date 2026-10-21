#!/usr/bin/env bash
# Build OpenMCL for the machine you run this on (Linux binary or macOS .app).
# PyInstaller does not cross-compile: for a Windows .exe, run
# packaging/build_windows.bat on Windows, or use the GitHub Actions workflow.
#
#   packaging/build.sh            single file
#   packaging/build.sh onedir     folder build, starts faster
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== Installing build dependencies"
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt pyinstaller

echo "== Cleaning previous build"
rm -rf build dist

echo "== Building"
if [ "${1:-}" = "onedir" ]; then
    python3 -m PyInstaller openmcl.spec --noconfirm -- --onedir
else
    python3 -m PyInstaller openmcl.spec --noconfirm
fi

echo "== Smoke test"
if [ "$(uname)" = "Darwin" ] && [ -d dist/OpenMCL.app ]; then
    ./dist/OpenMCL.app/Contents/MacOS/OpenMCL --version
    echo "Done: dist/OpenMCL.app"
elif [ -d dist/OpenMCL ]; then
    ./dist/OpenMCL/OpenMCL --version
    echo "Done: dist/OpenMCL/OpenMCL"
else
    ./dist/OpenMCL --version
    echo "Done: dist/OpenMCL"
fi
