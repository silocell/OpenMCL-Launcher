# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OpenMCL.

Build on the platform you are targeting - PyInstaller does not cross-compile,
so a Windows .exe must be built on Windows. See packaging/README.md.

    pyinstaller openmcl.spec                  # single file (default)
    pyinstaller openmcl.spec -- --onedir      # folder build, starts faster
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve()
ICONS = ROOT / "packaging" / "icons"

onedir = "--onedir" in sys.argv

# PySide6 ships far more than a launcher needs. Dropping these takes the build
# from roughly 400 MB to under 100 MB. QtSvg stays: the stylesheet renders its
# combo-box and spin-box arrows from generated SVG files.
EXCLUDED_QT = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNfc", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtSql",
    "PySide6.QtStateMachine", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets", "PySide6.QtXml",
]

EXCLUDED_OTHER = [
    "tkinter", "unittest", "pydoc", "doctest", "pytest", "setuptools",
    "numpy", "PIL", "shiboken6.support",
]

datas = [(str(ICONS / "openmcl.png"), "packaging/icons")]

a = Analysis(
    ["run.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("openmcl"),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED_QT + EXCLUDED_OTHER,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if sys.platform == "win32":
    icon = str(ICONS / "openmcl.ico")
elif sys.platform == "darwin":
    icns = ICONS / "openmcl.icns"
    icon = str(icns) if icns.exists() else str(ICONS / "openmcl.png")
else:
    icon = str(ICONS / "openmcl.png")

common = dict(
    name="OpenMCL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # No console window: the launcher writes to logs/launcher.log instead, and
    # the game's own output goes to the in-app console window.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

if onedir:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(
        exe, a.binaries, a.datas, strip=False, upx=False, name="OpenMCL"
    )
    bundle_target = coll
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        runtime_tmpdir=None, **common
    )
    bundle_target = exe

if sys.platform == "darwin":
    app = BUNDLE(
        bundle_target,
        name="OpenMCL.app",
        icon=icon,
        bundle_identifier="com.openmcl.launcher",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
