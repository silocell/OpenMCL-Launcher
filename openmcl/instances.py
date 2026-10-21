"""Instances: isolated game directories with their own version, mods and settings."""
from __future__ import annotations

import json
import re
import shutil
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from . import config


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-.").lower()
    return slug or "instance"


@dataclass
class ModRecord:
    """A mod file we installed, so we can list, update and remove it cleanly."""
    filename: str
    source: str = ""          # "modrinth" | "curseforge" | "local"
    project_id: str = ""
    version_id: str = ""
    name: str = ""
    version: str = ""
    sha1: str = ""
    url: str = ""
    enabled: bool = True


@dataclass
class Instance:
    id: str
    name: str
    mc_version: str
    loader: str = "vanilla"           # vanilla | fabric | quilt | neoforge | forge
    loader_version: str = ""
    version_id: str = ""              # launch profile id; = mc_version for vanilla
    icon: str = ""
    notes: str = ""
    created: float = field(default_factory=time.time)
    last_played: float = 0.0
    play_seconds: int = 0

    # Per-instance overrides. Empty / zero means "use the global setting".
    java_path: str = ""
    memory_mb: int = 0
    min_memory_mb: int = 0
    jvm_args: str = ""
    env: dict = field(default_factory=dict)
    pre_launch_command: str = ""
    window_width: int = 0
    window_height: int = 0
    fullscreen: Optional[bool] = None

    mods: list = field(default_factory=list)

    # Where this instance lives. Set by the store that loaded it; not persisted,
    # because the folder knows its own path.
    root: Optional[Path] = None

    # ---------------------------------------------------------------- locations
    @property
    def dir(self) -> Path:
        return (self.root or config.INSTANCES_DIR) / self.id

    @property
    def game_dir(self) -> Path:
        """What Minecraft sees as .minecraft - saves, options, mods all live here."""
        return self.dir / "minecraft"

    @property
    def mods_dir(self) -> Path:
        return self.game_dir / "mods"

    @property
    def config_path(self) -> Path:
        return self.dir / "instance.json"

    @property
    def launch_version(self) -> str:
        return self.version_id or self.mc_version

    @property
    def loader_label(self) -> str:
        if self.loader == "vanilla":
            return "Vanilla"
        return f"{self.loader.capitalize()} {self.loader_version}".strip()

    # ------------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("root", None)
        data["mods"] = [asdict(m) if not isinstance(m, dict) else m for m in self.mods]
        return data

    @classmethod
    def from_dict(cls, raw: dict, root: Optional[Path] = None) -> "Instance":
        known = set(cls.__dataclass_fields__) - {"root"}
        data = {k: v for k, v in raw.items() if k in known}
        mod_fields = set(ModRecord.__dataclass_fields__)
        data["mods"] = [
            ModRecord(**{k: v for k, v in m.items() if k in mod_fields})
            for m in raw.get("mods", [])
        ]
        return cls(root=root, **data)

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.game_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), "utf-8")
        tmp.replace(self.config_path)

    # --------------------------------------------------------------- mod helpers
    def mod_records(self) -> list[ModRecord]:
        return [m if isinstance(m, ModRecord) else ModRecord(**m) for m in self.mods]

    def add_mod(self, record: ModRecord) -> None:
        self.mods = [m for m in self.mod_records() if m.filename != record.filename]
        self.mods.append(record)
        self.save()

    def remove_mod(self, filename: str) -> None:
        for suffix in ("", ".disabled"):
            p = self.mods_dir / (filename + suffix)
            p.unlink(missing_ok=True)
        self.mods = [m for m in self.mod_records() if m.filename != filename]
        self.save()

    def set_mod_enabled(self, filename: str, enabled: bool) -> None:
        active = self.mods_dir / filename
        disabled = self.mods_dir / (filename + ".disabled")
        if enabled and disabled.exists():
            disabled.rename(active)
        elif not enabled and active.exists():
            active.rename(disabled)
        for m in self.mod_records():
            if m.filename == filename:
                m.enabled = enabled
        self.mods = self.mod_records()
        self.save()

    def scan_mods_dir(self) -> list[str]:
        """Jar filenames present on disk, including ones added by hand."""
        if not self.mods_dir.is_dir():
            return []
        return sorted(
            p.name for p in self.mods_dir.iterdir()
            if p.is_file() and p.suffix in (".jar", ".disabled")
        )


class InstanceStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or config.INSTANCES_DIR

    def all(self) -> list[Instance]:
        config.ensure_dirs()
        out: list[Instance] = []
        for d in sorted(self.root.iterdir() if self.root.is_dir() else []):
            cfg = d / "instance.json"
            if not cfg.is_file():
                continue
            try:
                out.append(
                    Instance.from_dict(json.loads(cfg.read_text("utf-8")), self.root)
                )
            except (json.JSONDecodeError, TypeError, OSError):
                continue
        out.sort(key=lambda i: (-i.last_played, i.name.lower()))
        return out

    def get(self, instance_id: str) -> Optional[Instance]:
        cfg = self.root / instance_id / "instance.json"
        if not cfg.is_file():
            return None
        return Instance.from_dict(json.loads(cfg.read_text("utf-8")), self.root)

    def unique_id(self, name: str) -> str:
        base = slugify(name)
        candidate, n = base, 2
        while (self.root / candidate).exists():
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    def create(
        self,
        name: str,
        mc_version: str,
        loader: str = "vanilla",
        loader_version: str = "",
    ) -> Instance:
        config.ensure_dirs()
        self.root.mkdir(parents=True, exist_ok=True)
        inst = Instance(
            root=self.root,
            id=self.unique_id(name),
            name=name.strip() or mc_version,
            mc_version=mc_version,
            loader=loader,
            loader_version=loader_version,
            version_id=mc_version if loader == "vanilla" else "",
        )
        inst.save()
        inst.mods_dir.mkdir(parents=True, exist_ok=True)
        return inst

    def duplicate(self, inst: Instance, new_name: str) -> Instance:
        clone = Instance.from_dict(inst.to_dict(), self.root)
        clone.id = self.unique_id(new_name)
        clone.name = new_name
        clone.created = time.time()
        clone.last_played = 0.0
        clone.play_seconds = 0
        shutil.copytree(inst.dir, clone.dir, dirs_exist_ok=True)
        clone.save()
        return clone

    def delete(self, inst: Instance) -> None:
        shutil.rmtree(inst.dir, ignore_errors=True)
