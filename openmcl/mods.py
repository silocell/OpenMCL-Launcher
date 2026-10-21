"""Installing mods into an instance, with dependency resolution."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from .instances import Instance, ModRecord
from .net import DownloadTask, download
from .providers import PROVIDERS, ModFile, ProviderError
from .providers import curseforge

log = logging.getLogger(__name__)

Progress = Optional[Callable[[int, int, str], None]]

MAX_DEPTH = 4


class ModInstallError(RuntimeError):
    def __init__(self, message: str, manual_url: str = ""):
        super().__init__(message)
        self.manual_url = manual_url


def _resolve_url(mod_file: ModFile) -> str:
    if mod_file.source == "curseforge":
        return curseforge.resolve_url(mod_file)
    return mod_file.url


def install_file(
    instance: Instance,
    mod_file: ModFile,
    progress: Progress = None,
) -> ModRecord:
    """Download one mod jar into the instance and record it."""
    url = _resolve_url(mod_file)
    if not url:
        raise ModInstallError(
            f"{mod_file.name or mod_file.filename} cannot be downloaded by third-party "
            "launchers - the author has disabled it on CurseForge. Download it by hand "
            "and drop it in the instance's mods folder.",
            manual_url=mod_file.page_url,
        )

    instance.mods_dir.mkdir(parents=True, exist_ok=True)
    dest = instance.mods_dir / mod_file.filename
    if progress:
        progress(0, 0, f"Downloading {mod_file.filename}")
    download(
        DownloadTask(
            url,
            dest,
            sha1=mod_file.sha1 or None,
            size=mod_file.size or None,
        )
    )

    record = ModRecord(
        filename=mod_file.filename,
        source=mod_file.source,
        project_id=mod_file.project_id,
        version_id=mod_file.version_id,
        name=mod_file.name or mod_file.filename,
        version=mod_file.version_number,
        sha1=mod_file.sha1,
        url=url,
        enabled=True,
    )
    instance.add_mod(record)
    return record


def install_with_dependencies(
    instance: Instance,
    mod_file: ModFile,
    progress: Progress = None,
    include_optional: bool = False,
) -> list[ModRecord]:
    """Install a mod plus everything it requires."""
    installed: list[ModRecord] = []
    seen: set[str] = set()

    def walk(current: ModFile, depth: int):
        key = f"{current.source}:{current.project_id}"
        if key in seen or depth > MAX_DEPTH:
            return
        seen.add(key)

        installed.append(install_file(instance, current, progress))

        provider = PROVIDERS.get(current.source)
        if provider is None:
            return
        wanted = {"required"} | ({"optional"} if include_optional else set())
        for project_id, kind in current.dependencies:
            if kind not in wanted:
                continue
            if f"{current.source}:{project_id}" in seen:
                continue
            try:
                dep = provider.best_file(project_id, instance.mc_version, instance.loader)
            except ProviderError as exc:
                log.warning("dependency lookup failed for %s: %s", project_id, exc)
                continue
            if dep is None:
                log.warning(
                    "no build of dependency %s for %s / %s",
                    project_id, instance.mc_version, instance.loader,
                )
                continue
            try:
                walk(dep, depth + 1)
            except ModInstallError as exc:
                log.warning("skipping dependency: %s", exc)

    walk(mod_file, 0)
    return installed


def check_updates(instance: Instance, progress: Progress = None) -> list[tuple[ModRecord, ModFile]]:
    """Mods with a newer build available for this instance's version and loader."""
    out: list[tuple[ModRecord, ModFile]] = []
    records = [m for m in instance.mod_records() if m.project_id and m.source in PROVIDERS]
    for n, record in enumerate(records, 1):
        if progress:
            progress(n, len(records), f"Checking {record.name}")
        provider = PROVIDERS[record.source]
        try:
            latest = provider.best_file(record.project_id, instance.mc_version, instance.loader)
        except ProviderError:
            continue
        if latest and latest.version_id != record.version_id:
            out.append((record, latest))
    return out


def apply_update(instance: Instance, record: ModRecord, new_file: ModFile,
                 progress: Progress = None) -> ModRecord:
    was_enabled = record.enabled
    instance.remove_mod(record.filename)
    new_record = install_file(instance, new_file, progress)
    if not was_enabled:
        instance.set_mod_enabled(new_record.filename, False)
    return new_record


def import_local_jar(instance: Instance, jar: Path) -> ModRecord:
    instance.mods_dir.mkdir(parents=True, exist_ok=True)
    dest = instance.mods_dir / jar.name
    if jar.resolve() != dest.resolve():
        dest.write_bytes(jar.read_bytes())
    record = ModRecord(filename=jar.name, source="local", name=jar.stem, enabled=True)
    instance.add_mod(record)
    return record
