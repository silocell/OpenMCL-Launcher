"""Paths and persisted launcher settings."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path

APP_NAME = "OpenMCL"


def data_dir() -> Path:
    """Per-platform application data directory."""
    env = os.environ.get("OPENMCL_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "openmcl"


DATA = data_dir()
INSTANCES_DIR = DATA / "instances"
# Shared across instances so two instances on the same version don't download twice.
VERSIONS_DIR = DATA / "shared" / "versions"
LIBRARIES_DIR = DATA / "shared" / "libraries"
ASSETS_DIR = DATA / "shared" / "assets"
NATIVES_DIR = DATA / "shared" / "natives"
CACHE_DIR = DATA / "cache"
LOGS_DIR = DATA / "logs"
ICONS_DIR = DATA / "icons"

ALL_DIRS = [
    DATA, INSTANCES_DIR, VERSIONS_DIR, LIBRARIES_DIR, ASSETS_DIR,
    NATIVES_DIR, CACHE_DIR, LOGS_DIR, ICONS_DIR,
]


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# Public client id used by the open-source launcher ecosystem is NOT shipped here.
# Register your own at https://portal.azure.com (see README) and paste it in Settings.
DEFAULT_MS_CLIENT_ID = ""


@dataclass
class Settings:
    ms_client_id: str = DEFAULT_MS_CLIENT_ID
    curseforge_api_key: str = ""
    java_path: str = ""              # blank = auto-detect
    memory_mb: int = 4096
    min_memory_mb: int = 512
    jvm_args: str = (
        "-XX:+UnlockExperimentalVMOptions -XX:+UseG1GC -XX:G1NewSizePercent=20 "
        "-XX:G1ReservePercent=20 -XX:MaxGCPauseMillis=50 -XX:G1HeapRegionSize=32M"
    )
    window_width: int = 854
    window_height: int = 480
    fullscreen: bool = False
    close_launcher_on_launch: bool = False
    show_snapshots: bool = False
    concurrent_downloads: int = 16
    theme: str = "dark"              # dark | light | midnight
    accent: str = "#4f9d69"
    font_size: int = 13
    extra_env: dict = field(default_factory=dict)

    @classmethod
    def path(cls) -> Path:
        return DATA / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        try:
            raw = json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        ensure_dirs()
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), "utf-8")
        tmp.replace(self.path())
