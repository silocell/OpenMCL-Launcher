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

# One source of truth for the version. The Windows version resource below is
# generated from it, so a release can never ship a binary whose properties
# disagree with the application.
sys.path.insert(0, str(ROOT / "packaging"))
from version import version as _read_version  # noqa: E402

VERSION = _read_version()
VERSION_TUPLE = tuple(
    int(p) for p in (VERSION.split("+")[0].split("-")[0].split(".") + ["0", "0", "0", "0"])[:4]
)

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

if sys.platform == "win32":
    VERSION_INFO = Path(workpath) / "version_info.txt"
    VERSION_INFO.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION_TUPLE},
    prodvers={VERSION_TUPLE},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'OpenMCL'),
        StringStruct('FileDescription', 'OpenMCL Minecraft launcher'),
        StringStruct('FileVersion', '{VERSION}'),
        StringStruct('InternalName', 'OpenMCL'),
        StringStruct('OriginalFilename', 'OpenMCL.exe'),
        StringStruct('ProductName', 'OpenMCL'),
        StringStruct('ProductVersion', '{VERSION}'),
        StringStruct('LegalCopyright',
                     'GPL-3.0. Not affiliated with Mojang, Microsoft, Modrinth or CurseForge.')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

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

# PyInstaller rejects --version-file on the command line when a .spec is given,
# so the Windows version resource is attached here instead.
if sys.platform == "win32" and VERSION_INFO.exists():
    common["version"] = str(VERSION_INFO)

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
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
