"""Version manifest, version JSON resolution, and asset/library installation."""
from __future__ import annotations

import copy
import json
import logging
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import config
from .net import DownloadTask, download, download_many, get_json
from .rules import (
    is_native_only,
    library_natives_classifier,
    maven_to_url,
    maven_to_path,
    rules_allow,
)

log = logging.getLogger(__name__)

VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RESOURCES_BASE = "https://resources.download.minecraft.net"
DEFAULT_MAVEN = "https://libraries.minecraft.net/"

Progress = Optional[Callable[[int, int, str], None]]


# --------------------------------------------------------------------------- manifest

def fetch_manifest(max_age: int = 3600) -> dict:
    """The version manifest, cached on disk so we work offline for installed versions."""
    config.ensure_dirs()
    cache = config.CACHE_DIR / "version_manifest_v2.json"
    if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age:
        try:
            return json.loads(cache.read_text("utf-8"))
        except json.JSONDecodeError:
            pass
    try:
        data = get_json(VERSION_MANIFEST)
        cache.write_text(json.dumps(data), "utf-8")
        return data
    except Exception as exc:  # noqa: BLE001 - offline is a normal condition
        if cache.exists():
            log.warning("using cached manifest (%s)", exc)
            return json.loads(cache.read_text("utf-8"))
        raise


def list_versions(include_snapshots: bool = False) -> list[dict]:
    versions = fetch_manifest()["versions"]
    if include_snapshots:
        return versions
    return [v for v in versions if v.get("type") == "release"]


def latest_release() -> str:
    return fetch_manifest()["latest"]["release"]


# ------------------------------------------------------------------- version JSON I/O

def version_dir(version_id: str) -> Path:
    return config.VERSIONS_DIR / version_id


def version_json_path(version_id: str) -> Path:
    return version_dir(version_id) / f"{version_id}.json"


def client_jar_path(version_id: str) -> Path:
    return version_dir(version_id) / f"{version_id}.jar"


def write_version_json(version_id: str, data: dict) -> Path:
    p = version_json_path(version_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), "utf-8")
    return p


def ensure_vanilla_json(version_id: str) -> dict:
    """Download the vanilla version JSON if we do not have it yet."""
    p = version_json_path(version_id)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except json.JSONDecodeError:
            log.warning("corrupt version json for %s, refetching", version_id)
    entry = next(
        (v for v in fetch_manifest()["versions"] if v["id"] == version_id), None
    )
    if entry is None:
        raise ValueError(f"Unknown Minecraft version: {version_id}")
    task = DownloadTask(entry["url"], p, sha1=entry.get("sha1"))
    download(task, force=True)
    return json.loads(p.read_text("utf-8"))


def _merge(child: dict, parent: dict) -> dict:
    """Merge a loader profile over the vanilla version it inherits from."""
    merged = copy.deepcopy(parent)

    # Child libraries must come first: loaders ship patched copies of vanilla jars
    # and the first match on the classpath wins.
    merged["libraries"] = copy.deepcopy(child.get("libraries", [])) + merged.get(
        "libraries", []
    )

    for key in (
        "id", "mainClass", "minecraftArguments", "releaseTime", "time", "type",
        "assets", "assetIndex", "javaVersion", "logging", "complianceLevel",
    ):
        if key in child:
            merged[key] = copy.deepcopy(child[key])

    if "arguments" in child or "arguments" in merged:
        out = {}
        for section in ("game", "jvm"):
            out[section] = list(merged.get("arguments", {}).get(section, [])) + list(
                child.get("arguments", {}).get(section, [])
            )
        merged["arguments"] = out

    # Remember which version actually ships the client jar: a Fabric/Forge profile
    # has its own id but runs the parent's jar.
    merged["_jarVersion"] = parent.get("_jarVersion", parent["id"])
    merged.pop("inheritsFrom", None)
    return merged


def jar_version_id(version: dict) -> str:
    """The version id whose client jar belongs on the classpath."""
    return version.get("_jarVersion") or version["id"]


def load_version(version_id: str, _depth: int = 0) -> dict:
    """Load a version JSON with `inheritsFrom` chains fully resolved."""
    if _depth > 8:
        raise RuntimeError("inheritsFrom chain too deep (circular reference?)")
    p = version_json_path(version_id)
    if p.exists():
        data = json.loads(p.read_text("utf-8"))
    else:
        data = ensure_vanilla_json(version_id)
    parent_id = data.get("inheritsFrom")
    if not parent_id:
        return data
    parent = load_version(parent_id, _depth + 1)
    return _merge(data, parent)


# ------------------------------------------------------------------------- libraries

def _library_download_entry(lib: dict, classifier: Optional[str]) -> Optional[dict]:
    downloads = lib.get("downloads", {})
    if classifier:
        return downloads.get("classifiers", {}).get(classifier)
    return downloads.get("artifact")


def _library_task(lib: dict, classifier: Optional[str] = None) -> Optional[DownloadTask]:
    """Build a download task for a library, falling back to maven coordinates."""
    entry = _library_download_entry(lib, classifier)
    if entry and entry.get("url"):
        rel = entry.get("path") or maven_to_path(
            lib["name"] + (f":{classifier}" if classifier else "")
        )
        return DownloadTask(
            entry["url"],
            config.LIBRARIES_DIR / rel,
            sha1=entry.get("sha1"),
            size=entry.get("size"),
        )

    name = lib.get("name")
    if not name:
        return None
    coord = name + (f":{classifier}" if classifier else "")
    rel = maven_to_path(coord)
    dest = config.LIBRARIES_DIR / rel

    base = lib.get("url") or DEFAULT_MAVEN
    if entry is None and "downloads" in lib and classifier:
        # Declared no such classifier: nothing to fetch.
        return None
    if dest.exists():
        return DownloadTask(maven_to_url(coord, base), dest)
    return DownloadTask(maven_to_url(coord, base), dest, sha1=lib.get("sha1"))


def iter_libraries(version: dict, features: Optional[dict] = None):
    """Yield (library, classifier_or_None) pairs that apply to this machine."""
    for lib in version.get("libraries", []):
        if not rules_allow(lib.get("rules"), features):
            continue
        classifier = library_natives_classifier(lib)
        if classifier:
            # Legacy layout: a plain artifact plus a separate natives jar.
            if lib.get("downloads", {}).get("artifact"):
                yield lib, None
            yield lib, classifier
        else:
            yield lib, None


def library_tasks(version: dict) -> tuple[list[DownloadTask], list[tuple[dict, Path]]]:
    """Returns (all download tasks, [(library, natives_jar_path), ...])."""
    tasks: list[DownloadTask] = []
    natives: list[tuple[dict, Path]] = []
    seen: set[Path] = set()
    for lib, classifier in iter_libraries(version):
        task = _library_task(lib, classifier)
        if task is None:
            continue
        if task.dest not in seen:
            seen.add(task.dest)
            tasks.append(task)
        if classifier or is_native_only(lib):
            natives.append((lib, task.dest))
    return tasks, natives


def classpath_entries(version: dict) -> list[Path]:
    """Jars for -cp, in order, excluding natives-only artifacts."""
    out: list[Path] = []
    seen: set[Path] = set()
    for lib, classifier in iter_libraries(version):
        if classifier or is_native_only(lib):
            continue
        task = _library_task(lib, None)
        if task is None or task.dest in seen:
            continue
        seen.add(task.dest)
        out.append(task.dest)
    out.append(client_jar_path(jar_version_id(version)))
    return out


def extract_natives(version: dict, dest: Path) -> Path:
    """Unpack native libraries into a per-version directory."""
    dest.mkdir(parents=True, exist_ok=True)
    _, natives = library_tasks(version)
    for lib, jar in natives:
        if not jar.exists():
            log.warning("missing natives jar %s", jar)
            continue
        exclude = lib.get("extract", {}).get("exclude", ["META-INF/"])
        try:
            with zipfile.ZipFile(jar) as zf:
                for member in zf.namelist():
                    if member.endswith("/"):
                        continue
                    if any(member.startswith(x) for x in exclude):
                        continue
                    # Flatten: natives loaders only look at the directory itself.
                    target = dest / Path(member).name
                    if target.exists():
                        continue
                    with zf.open(member) as src, target.open("wb") as out:
                        shutil.copyfileobj(src, out)
        except zipfile.BadZipFile:
            log.warning("bad natives jar %s", jar)
    return dest


# ---------------------------------------------------------------------------- assets

def asset_index_path(version: dict) -> Path:
    index_id = version.get("assetIndex", {}).get("id") or version.get("assets", "legacy")
    return config.ASSETS_DIR / "indexes" / f"{index_id}.json"


def ensure_asset_index(version: dict) -> dict:
    info = version.get("assetIndex")
    path = asset_index_path(version)
    if info:
        download(
            DownloadTask(info["url"], path, sha1=info.get("sha1"), size=info.get("size"))
        )
    if not path.exists():
        return {"objects": {}}
    return json.loads(path.read_text("utf-8"))


def asset_tasks(index: dict) -> list[DownloadTask]:
    tasks: list[DownloadTask] = []
    seen: set[str] = set()
    for obj in index.get("objects", {}).values():
        h = obj["hash"]
        if h in seen:
            continue
        seen.add(h)
        tasks.append(
            DownloadTask(
                f"{RESOURCES_BASE}/{h[:2]}/{h}",
                config.ASSETS_DIR / "objects" / h[:2] / h,
                sha1=h,
                size=obj.get("size"),
            )
        )
    return tasks


def materialise_legacy_assets(version: dict, index: dict, instance_dir: Path) -> Path:
    """Pre-1.7 builds read loose files instead of the hashed object store.

    Returns the directory to pass as --assetsDir.
    """
    index_id = version.get("assetIndex", {}).get("id") or version.get("assets", "legacy")
    virtual = index.get("virtual")
    map_to_resources = index.get("map_to_resources")
    if not (virtual or map_to_resources):
        return config.ASSETS_DIR

    target = (
        instance_dir / "resources"
        if map_to_resources
        else config.ASSETS_DIR / "virtual" / index_id
    )
    for name, obj in index.get("objects", {}).items():
        h = obj["hash"]
        src = config.ASSETS_DIR / "objects" / h[:2] / h
        dst = target / name
        if not src.exists() or dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    return config.ASSETS_DIR if map_to_resources else target


# --------------------------------------------------------------------------- install

def install_version(version_id: str, progress: Progress = None, workers: int = 16) -> dict:
    """Download everything needed to launch `version_id`."""
    def step(msg: str):
        if progress:
            progress(0, 0, msg)

    step("Reading version metadata")
    version = load_version(version_id)

    tasks: list[DownloadTask] = []

    client = version.get("downloads", {}).get("client")
    if client:
        tasks.append(
            DownloadTask(
                client["url"],
                client_jar_path(jar_version_id(version)),
                sha1=client.get("sha1"),
                size=client.get("size"),
            )
        )

    step("Resolving libraries")
    lib_tasks, _ = library_tasks(version)
    tasks.extend(lib_tasks)

    logging_cfg = version.get("logging", {}).get("client", {}).get("file")
    if logging_cfg:
        tasks.append(
            DownloadTask(
                logging_cfg["url"],
                config.ASSETS_DIR / "log_configs" / logging_cfg["id"],
                sha1=logging_cfg.get("sha1"),
                size=logging_cfg.get("size"),
            )
        )

    step("Fetching asset index")
    index = ensure_asset_index(version)
    tasks.extend(asset_tasks(index))

    step(f"Downloading {len(tasks)} files")
    download_many(tasks, workers=workers, progress=progress)

    step("Unpacking native libraries")
    extract_natives(version, config.NATIVES_DIR / version["id"])
    return version
