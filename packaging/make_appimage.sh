#!/usr/bin/env bash
# Build OpenMCL-x86_64.AppImage from a PyInstaller --onedir build.
#
# A onedir build is used deliberately: AppImage already mounts itself as a
# filesystem, so a onefile binary inside would unpack a second time on every
# launch. With onedir the AppImage runs the real binary directly.
#
#   packaging/make_appimage.sh
#
# Needs: pyinstaller, and network access to fetch appimagetool once.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
APPDIR="$ROOT/dist/OpenMCL.AppDir"
VERSION="$(python3 -c 'import re,pathlib;print(re.search(r"\"([^\"]+)\"",pathlib.Path("openmcl/__init__.py").read_text()).group(1))')"

echo "== Building OpenMCL $VERSION (onedir)"
rm -rf build dist
python3 -m PyInstaller openmcl.spec --noconfirm -- --onedir

echo "== Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/512x512/apps" \
         "$APPDIR/usr/share/metainfo"
cp -a dist/OpenMCL/. "$APPDIR/usr/bin/"

cat > "$APPDIR/openmcl.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=OpenMCL
GenericName=Minecraft Launcher
Comment=Launch Minecraft with isolated instances and mods
Exec=OpenMCL
Icon=openmcl
Categories=Game;
Keywords=minecraft;launcher;mods;fabric;forge;
Terminal=false
StartupWMClass=OpenMCL
DESKTOP
cp "$APPDIR/openmcl.desktop" "$APPDIR/usr/share/applications/openmcl.desktop"

cp packaging/icons/openmcl.png "$APPDIR/openmcl.png"
cp packaging/icons/openmcl.png \
   "$APPDIR/usr/share/icons/hicolor/512x512/apps/openmcl.png"
# Some desktops look for .DirIcon rather than reading the .desktop file.
cp packaging/icons/openmcl.png "$APPDIR/.DirIcon"

cat > "$APPDIR/usr/share/metainfo/openmcl.appdata.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>openmcl</id>
  <name>OpenMCL</name>
  <summary>Minecraft launcher with isolated instances and mod management</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>GPL-3.0-only</project_license>
  <description>
    <p>
      OpenMCL gives each Minecraft setup its own worlds, mods and Java. It
      installs Fabric, Quilt, NeoForge and Forge, and browses mods from
      Modrinth and CurseForge. A Microsoft account that owns Minecraft: Java
      Edition is required.
    </p>
  </description>
  <launchable type="desktop-id">openmcl.desktop</launchable>
  <url type="homepage">https://silocell.github.io/OpenMCL-Launcher/</url>
  <releases><release version="$VERSION"/></releases>
</component>
XML

cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/OpenMCL" "$@"
APPRUN
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/OpenMCL"

echo "== Fetching appimagetool"
TOOL="$ROOT/build/appimagetool"
if [ ! -x "$TOOL" ]; then
    mkdir -p "$ROOT/build"
    curl -fsSL -o "$TOOL" \
      https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi

echo "== Packaging"
# --appimage-extract-and-run: CI runners have no FUSE.
ARCH=x86_64 "$TOOL" --appimage-extract-and-run \
    "$APPDIR" "$ROOT/dist/OpenMCL-x86_64.AppImage"

chmod +x "$ROOT/dist/OpenMCL-x86_64.AppImage"
echo
echo "Done: dist/OpenMCL-x86_64.AppImage"
ls -lh "$ROOT/dist/OpenMCL-x86_64.AppImage"
