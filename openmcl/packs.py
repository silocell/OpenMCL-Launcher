"""Modpack import and export (.mrpack and CurseForge .zip)."""
from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import loaders
from .instances import Instance, InstanceStore, ModRecord
from .net import DownloadTask, download_many
from .providers import ProviderError, curseforge

log = logging.getLogger(__name__)

Progress = Optional[Callable[[int, int, str], None]]

# modrinth.index.json dependency key -> our loader name
MR_LOADERS = {
    "fabric-loader": "fabric",
    "quilt-loader": "quilt",
    "forge": "forge",
    "neoforge": "neoforge",
}


class PackError(RuntimeError):
    pass


def _step(progress: Progress, msg: str) -> None:
    if progress:
        progress(0, 0, msg)


def _safe_extract(zf: zipfile.ZipFile, prefix: str, dest: Path) -> int:
    """Copy `prefix/...` out of the zip, refusing paths that escape `dest`."""
    count = 0
    root = dest.resolve()
    for member in zf.namelist():
        if not member.startswith(prefix) or member.endswith("/"):
            continue
        rel = member[len(prefix):].lstrip("/")
        if not rel:
            continue
        target = (dest / rel).resolve()
        if not str(target).startswith(str(root)):
            log.warning("refusing path traversal entry: %s", member)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out)
        count += 1
    return count


def detect_pack_type(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile as exc:
        raise PackError(f"{path.name} is not a readable zip archive.") from exc
    if "modrinth.index.json" in names:
        return "mrpack"
    if "manifest.json" in names:
        return "curseforge"
    raise PackError(
        f"{path.name} does not look like a Modrinth (.mrpack) or CurseForge modpack."
    )


# ------------------------------------------------------------------------- .mrpack

def import_mrpack(
    path: Path, store: InstanceStore, name: str = "", progress: Progress = None
) -> Instance:
    with zipfile.ZipFile(path) as zf:
        index = json.loads(zf.read("modrinth.index.json"))

        deps = index.get("dependencies", {})
        mc_version = deps.get("minecraft")
        if not mc_version:
            raise PackError("The pack does not say which Minecraft version it needs.")

        loader, loader_version = "vanilla", ""
        for key, value in deps.items():
            if key in MR_LOADERS:
                loader, loader_version = MR_LOADERS[key], value
                break

        pack_name = name or index.get("name") or path.stem
        inst = store.create(pack_name, mc_version, loader, loader_version)
        inst.notes = (
            f"Imported from {path.name}"
            + (f" (pack version {index['versionId']})" if index.get("versionId") else "")
        )

        try:
            _step(progress, f"Installing {loader} {loader_version}".strip())
            inst.version_id = loaders.install(loader, mc_version, loader_version, progress)
            inst.save()

            _step(progress, "Downloading pack files")
            tasks: list[DownloadTask] = []
            records: list[ModRecord] = []
            for entry in index.get("files", []):
                env = entry.get("env", {})
                if env.get("client") == "unsupported":
                    continue
                urls = entry.get("downloads") or []
                if not urls:
                    continue
                rel = entry["path"].replace("\\", "/").lstrip("/")
                target = (inst.game_dir / rel).resolve()
                if not str(target).startswith(str(inst.game_dir.resolve())):
                    log.warning("skipping unsafe pack path %s", rel)
                    continue
                hashes = entry.get("hashes", {})
                tasks.append(
                    DownloadTask(urls[0], target, sha1=hashes.get("sha1"),
                                 size=entry.get("fileSize"))
                )
                if rel.startswith("mods/"):
                    records.append(
                        ModRecord(
                            filename=Path(rel).name,
                            source="modrinth",
                            name=Path(rel).stem,
                            sha1=hashes.get("sha1", ""),
                            url=urls[0],
                        )
                    )
            download_many(tasks, progress=progress)

            _step(progress, "Applying overrides")
            _safe_extract(zf, "overrides/", inst.game_dir)
            _safe_extract(zf, "client-overrides/", inst.game_dir)

            inst.mods = records
            inst.save()
            return inst
        except Exception:
            store.delete(inst)
            raise


# -------------------------------------------------------------------- CurseForge zip

def import_curseforge_zip(
    path: Path, store: InstanceStore, name: str = "", progress: Progress = None
) -> Instance:
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read("manifest.json"))

        mc = manifest.get("minecraft", {})
        mc_version = mc.get("version")
        if not mc_version:
            raise PackError("The pack manifest has no Minecraft version.")

        loader, loader_version = "vanilla", ""
        mod_loaders = mc.get("modLoaders") or []
        primary = next((m for m in mod_loaders if m.get("primary")), None) or (
            mod_loaders[0] if mod_loaders else None
        )
        if primary:
            raw = primary.get("id", "")
            family, _, ver = raw.partition("-")
            loader = family.lower() if family.lower() in loaders.LOADERS else "forge"
            loader_version = ver

        pack_name = name or manifest.get("name") or path.stem
        inst = store.create(pack_name, mc_version, loader, loader_version)
        inst.notes = f"Imported from {path.name}"

        try:
            _step(progress, f"Installing {loader} {loader_version}".strip())
            inst.version_id = loaders.install(loader, mc_version, loader_version, progress)
            inst.save()

            entries = [f for f in manifest.get("files", []) if f.get("projectID")]
            _step(progress, f"Resolving {len(entries)} mod downloads")

            tasks: list[DownloadTask] = []
            records: list[ModRecord] = []
            manual: list[str] = []
            for n, entry in enumerate(entries, 1):
                project_id = str(entry["projectID"])
                file_id = str(entry["fileID"])
                if progress:
                    progress(n, len(entries), f"Resolving mod {n}/{len(entries)}")
                try:
                    url = curseforge.download_url(project_id, file_id)
                except ProviderError as exc:
                    raise PackError(str(exc)) from exc
                if not url:
                    manual.append(f"https://www.curseforge.com/projects/{project_id}")
                    continue
                filename = url.rsplit("/", 1)[-1]
                tasks.append(DownloadTask(url, inst.mods_dir / filename))
                records.append(
                    ModRecord(
                        filename=filename,
                        source="curseforge",
                        project_id=project_id,
                        version_id=file_id,
                        name=Path(filename).stem,
                        url=url,
                    )
                )

            _step(progress, "Downloading mods")
            download_many(tasks, progress=progress)

            _step(progress, "Applying overrides")
            overrides = manifest.get("overrides", "overrides")
            _safe_extract(zf, overrides.rstrip("/") + "/", inst.game_dir)

            inst.mods = records
            if manual:
                inst.notes += (
                    "\n\nThese mods block third-party downloads and must be added "
                    "by hand:\n" + "\n".join(manual)
                )
            inst.save()
            return inst
        except Exception:
            store.delete(inst)
            raise


def import_pack(
    path: Path, store: InstanceStore, name: str = "", progress: Progress = None
) -> Instance:
    kind = detect_pack_type(path)
    if kind == "mrpack":
        return import_mrpack(path, store, name, progress)
    return import_curseforge_zip(path, store, name, progress)


# -------------------------------------------------------------------------- export

EXPORT_SKIP = {"logs", "crash-reports", "assets", "versions", "libraries", "natives"}


def export_mrpack(instance: Instance, dest: Path, pack_version: str = "1.0.0") -> Path:
    """Write a .mrpack. Modrinth mods are linked; everything else is embedded."""
    deps = {"minecraft": instance.mc_version}
    if instance.loader == "fabric":
        deps["fabric-loader"] = instance.loader_version
    elif instance.loader == "quilt":
        deps["quilt-loader"] = instance.loader_version
    elif instance.loader in ("forge", "neoforge"):
        deps[instance.loader] = instance.loader_version

    linked: list[dict] = []
    linked_names: set[str] = set()
    for record in instance.mod_records():
        if record.source == "modrinth" and record.url and record.sha1:
            linked.append(
                {
                    "path": f"mods/{record.filename}",
                    "hashes": {"sha1": record.sha1},
                    "env": {"client": "required", "server": "required"},
                    "downloads": [record.url],
                }
            )
            linked_names.add(record.filename)

    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": pack_version,
        "name": instance.name,
        "summary": instance.notes[:400],
        "files": linked,
        "dependencies": deps,
    }

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("modrinth.index.json", json.dumps(index, indent=2))
        for path in instance.game_dir.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(instance.game_dir)
            if rel.parts and rel.parts[0] in EXPORT_SKIP:
                continue
            if rel.parts[:1] == ("mods",) and path.name in linked_names:
                continue
            zf.write(path, f"overrides/{rel.as_posix()}")
    return dest
