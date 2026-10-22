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
VERSION="$(python3 -c 'import re,pathlib;print(re.search(r"__version__\s*=\s*\"([^\"]+)\"",pathlib.Path("openmcl/__init__.py").read_text()).group(1))')"

# AppStream needs a reverse-DNS component id, and the desktop file, icon and
# metainfo file are all named after it so software centres match them up.
APPID="io.github.silocell.OpenMCL"

# A release needs a date to validate. The commit date is accurate and needs no
# maintenance; today's date is the fallback outside a git checkout.
RELEASE_DATE="$(git log -1 --format=%cd --date=format:%Y-%m-%d 2>/dev/null || date -u +%Y-%m-%d)"

echo "== Building OpenMCL $VERSION (onedir)"
rm -rf build dist
python3 -m PyInstaller openmcl.spec --noconfirm -- --onedir

echo "== Assembling AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/512x512/apps" \
         "$APPDIR/usr/share/metainfo"
cp -a dist/OpenMCL/. "$APPDIR/usr/bin/"

cat > "$APPDIR/$APPID.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=OpenMCL
GenericName=Minecraft Launcher
Comment=Launch Minecraft with isolated instances and mods
Exec=OpenMCL
Icon=$APPID
Categories=Game;
Keywords=minecraft;launcher;mods;fabric;forge;
Terminal=false
StartupWMClass=OpenMCL
DESKTOP
cp "$APPDIR/$APPID.desktop" "$APPDIR/usr/share/applications/$APPID.desktop"

cp packaging/icons/openmcl.png "$APPDIR/$APPID.png"
cp packaging/icons/openmcl.png \
   "$APPDIR/usr/share/icons/hicolor/512x512/apps/$APPID.png"
# Some desktops look for .DirIcon rather than reading the .desktop file.
cp packaging/icons/openmcl.png "$APPDIR/.DirIcon"

cat > "$APPDIR/usr/share/metainfo/$APPID.appdata.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>$APPID</id>
  <name>OpenMCL</name>
  <summary>Launch Minecraft with isolated instances and mods</summary>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>GPL-3.0-only</project_license>
  <developer id="io.github.silocell">
    <name>silocell</name>
  </developer>
  <description>
    <p>
      OpenMCL gives each Minecraft setup its own worlds, mods and Java, so
      updating one never breaks another.
    </p>
    <p>
      It installs the Fabric, Quilt, NeoForge and Forge mod loaders, browses
      mods from both Modrinth and CurseForge in one search, and imports and
      exports modpacks.
    </p>
    <p>
      A Microsoft account that owns Minecraft: Java Edition is required. There
      is no offline mode.
    </p>
  </description>
  <launchable type="desktop-id">$APPID.desktop</launchable>
  <url type="homepage">https://silocell.github.io/OpenMCL-Launcher/</url>
  <url type="bugtracker">https://github.com/silocell/OpenMCL-Launcher/issues</url>
  <url type="vcs-browser">https://github.com/silocell/OpenMCL-Launcher</url>
  <content_rating type="oars-1.1"/>
  <releases>
    <release version="$VERSION" date="$RELEASE_DATE"/>
  </releases>
</component>
XML

if command -v appstreamcli >/dev/null 2>&1; then
    echo "== Validating AppStream metadata"
    appstreamcli validate --no-net --pedantic \
        "$APPDIR/usr/share/metainfo/$APPID.appdata.xml" || {
        echo "AppStream validation failed; appimagetool would reject this." >&2
        exit 1
    }
fi

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
