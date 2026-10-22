#!/usr/bin/env bash
# Build OpenMCL.dmg from the .app produced by PyInstaller. macOS only.
#
#   packaging/make_dmg.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"

if [ "$(uname)" != "Darwin" ]; then
    echo "make_dmg.sh only runs on macOS (it needs hdiutil)." >&2
    exit 1
fi

if [ ! -d dist/OpenMCL.app ]; then
    echo "== Building OpenMCL.app"
    rm -rf build dist
    python3 -m PyInstaller openmcl.spec --noconfirm
fi

echo "== Staging disk image contents"
STAGE="$ROOT/build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R dist/OpenMCL.app "$STAGE/"
# The drag-to-install target users expect.
ln -s /Applications "$STAGE/Applications"

cat > "$STAGE/Read me first.txt" <<'NOTE'
OpenMCL

1. Drag OpenMCL onto the Applications folder.
2. The first time you open it, right-click the app and choose Open, then
   Open again. macOS blocks unsigned apps opened by double-click.
3. Open Settings and follow the sign-in setup:
   https://github.com/silocell/OpenMCL-Launcher#set-up-sign-in

You need a Microsoft account that owns Minecraft: Java Edition.
NOTE

echo "== Creating disk image"
rm -f dist/OpenMCL.dmg
hdiutil create \
    -volname "OpenMCL" \
    -srcfolder "$STAGE" \
    -fs HFS+ \
    -format UDZO \
    -imagekey zlib-level=9 \
    -ov \
    dist/OpenMCL.dmg

echo
echo "Done: dist/OpenMCL.dmg"
ls -lh dist/OpenMCL.dmg
