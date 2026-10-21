"""Mod loader installation: Fabric, Quilt, NeoForge and Forge."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import config, java, mojang
from .net import DownloadTask, download, download_many, get_json, session, sha1_file
from .rules import maven_to_path, maven_to_url

log = logging.getLogger(__name__)

Progress = Optional[Callable[[int, int, str], None]]

FABRIC_META = "https://meta.fabricmc.net/v2"
QUILT_META = "https://meta.quiltmc.org/v3"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"
FORGE_MAVEN = "https://maven.minecraftforge.net"
FORGE_PROMOTIONS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"

LOADERS = ("vanilla", "fabric", "quilt", "neoforge", "forge")


class LoaderError(RuntimeError):
    pass


def _step(progress: Progress, msg: str) -> None:
    if progress:
        progress(0, 0, msg)


# ------------------------------------------------------------------ Fabric and Quilt

def fabric_game_versions(include_snapshots: bool = False) -> list[str]:
    data = get_json(f"{FABRIC_META}/versions/game")
    return [v["version"] for v in data if include_snapshots or v.get("stable")]


def fabric_loader_versions(mc_version: str) -> list[str]:
    data = get_json(f"{FABRIC_META}/versions/loader/{mc_version}")
    return [v["loader"]["version"] for v in data]


def quilt_loader_versions(mc_version: str) -> list[str]:
    data = get_json(f"{QUILT_META}/versions/loader/{mc_version}")
    return [v["loader"]["version"] for v in data]


def _install_meta_profile(url: str, progress: Progress) -> str:
    """Fabric and Quilt both hand us a ready-made profile JSON."""
    _step(progress, "Fetching loader profile")
    profile = get_json(url)
    version_id = profile["id"]
    mojang.write_version_json(version_id, profile)
    return version_id


def install_fabric(mc_version: str, loader_version: str = "", progress: Progress = None) -> str:
    if not loader_version:
        versions = fabric_loader_versions(mc_version)
        if not versions:
            raise LoaderError(f"Fabric has no builds for Minecraft {mc_version}.")
        loader_version = versions[0]
    url = f"{FABRIC_META}/versions/loader/{mc_version}/{loader_version}/profile/json"
    return _install_meta_profile(url, progress)


def install_quilt(mc_version: str, loader_version: str = "", progress: Progress = None) -> str:
    if not loader_version:
        versions = quilt_loader_versions(mc_version)
        if not versions:
            raise LoaderError(f"Quilt has no builds for Minecraft {mc_version}.")
        loader_version = versions[0]
    url = f"{QUILT_META}/versions/loader/{mc_version}/{loader_version}/profile/json"
    return _install_meta_profile(url, progress)


# ------------------------------------------------------------- NeoForge / Forge lists

def _neoforge_prefix(mc_version: str) -> str:
    """Minecraft 1.21.1 -> NeoForge 21.1.x ; 1.21 -> 21.0.x"""
    parts = mc_version.split(".")
    if len(parts) < 2 or parts[0] != "1":
        return ""
    minor = parts[1]
    patch = parts[2] if len(parts) > 2 else "0"
    return f"{minor}.{patch}."


def neoforge_versions(mc_version: str) -> list[str]:
    data = get_json(NEOFORGE_VERSIONS)
    prefix = _neoforge_prefix(mc_version)
    if not prefix:
        return []
    matching = [v for v in data.get("versions", []) if v.startswith(prefix)]
    return sorted(matching, reverse=True)


def forge_versions(mc_version: str) -> list[str]:
    """Forge build numbers for a Minecraft version, newest first."""
    r = session().get(
        f"{FORGE_MAVEN}/net/minecraftforge/forge/maven-metadata.xml", timeout=30
    )
    r.raise_for_status()
    all_versions = re.findall(r"<version>([^<]+)</version>", r.text)
    out = []
    for v in all_versions:
        mc, _, forge = v.partition("-")
        if mc == mc_version and forge:
            out.append(forge)
    return sorted(out, reverse=True, key=lambda s: [int(p) if p.isdigit() else -1
                                                    for p in s.split(".")])


def forge_recommended(mc_version: str) -> str:
    try:
        promos = get_json(FORGE_PROMOTIONS).get("promos", {})
    except Exception:  # noqa: BLE001
        return ""
    return promos.get(f"{mc_version}-recommended") or promos.get(f"{mc_version}-latest") or ""


# ------------------------------------------------------- Forge/NeoForge installer run

def _manifest_main_class(jar: Path) -> str:
    with zipfile.ZipFile(jar) as zf:
        try:
            text = zf.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
        except KeyError:
            raise LoaderError(f"{jar.name} has no manifest")
    # Manifest lines wrap at 72 bytes with a leading space on continuations.
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n ", "")
    m = re.search(r"^Main-Class:\s*(\S+)", text, re.MULTILINE)
    if not m:
        raise LoaderError(f"{jar.name} declares no Main-Class")
    return m.group(1)


def _lib_path(coord: str) -> Path:
    return config.LIBRARIES_DIR / maven_to_path(coord)


def _download_profile_libraries(libraries: list[dict], progress: Progress) -> None:
    tasks: list[DownloadTask] = []
    for lib in libraries:
        art = lib.get("downloads", {}).get("artifact")
        name = lib.get("name")
        if art and art.get("url"):
            dest = config.LIBRARIES_DIR / (art.get("path") or maven_to_path(name))
            tasks.append(DownloadTask(art["url"], dest, sha1=art.get("sha1"),
                                      size=art.get("size")))
        elif name:
            base = lib.get("url") or "https://libraries.minecraft.net/"
            tasks.append(DownloadTask(maven_to_url(name, base), _lib_path(name)))
    download_many(tasks, progress=progress)


def _resolve_data(
    data: dict, installer: Path, work: Path, mc_jar: Path, root: Path
) -> dict[str, str]:
    """Turn install_profile `data` into concrete literals/paths for the client side."""
    resolved: dict[str, str] = {
        "SIDE": "client",
        "MINECRAFT_JAR": str(mc_jar),
        "ROOT": str(root),
        "INSTALLER": str(installer),
        "LIBRARY_DIR": str(config.LIBRARIES_DIR),
    }
    with zipfile.ZipFile(installer) as zf:
        for key, sides in data.items():
            value = sides.get("client") if isinstance(sides, dict) else sides
            if not isinstance(value, str):
                continue
            if value.startswith("[") and value.endswith("]"):
                resolved[key] = str(_lib_path(value[1:-1]))
            elif value.startswith("'") and value.endswith("'"):
                resolved[key] = value[1:-1]
            elif value.startswith("/"):
                member = value.lstrip("/")
                out = work / Path(member).name
                try:
                    with zf.open(member) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    resolved[key] = str(out)
                except KeyError:
                    log.warning("installer has no %s", member)
                    resolved[key] = value
            else:
                resolved[key] = value
    return resolved


_TOKEN = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _expand_arg(arg: str, data: dict[str, str]) -> str:
    if arg.startswith("[") and arg.endswith("]"):
        return str(_lib_path(arg[1:-1]))
    return _TOKEN.sub(lambda m: data.get(m.group(1), m.group(0)), arg)


def _outputs_satisfied(processor: dict, data: dict[str, str]) -> bool:
    outputs = processor.get("outputs") or {}
    if not outputs:
        return False
    for raw_path, raw_hash in outputs.items():
        path = Path(_expand_arg(raw_path, data))
        want = _expand_arg(raw_hash, data).strip("'")
        if not path.exists():
            return False
        if want and sha1_file(path) != want:
            return False
    return True


def _run_processors(
    install_profile: dict,
    installer: Path,
    data: dict[str, str],
    java_exe: Path,
    progress: Progress,
) -> None:
    processors = [
        p for p in install_profile.get("processors", [])
        if "client" in (p.get("sides") or ["client", "server"])
    ]
    total = len(processors)
    for n, proc in enumerate(processors, 1):
        if _outputs_satisfied(proc, data):
            log.info("processor %d/%d already applied", n, total)
            continue
        if progress:
            progress(n, total, f"Patching Minecraft ({n}/{total})")

        jar = _lib_path(proc["jar"])
        if not jar.exists():
            raise LoaderError(f"Installer library missing: {proc['jar']}")

        classpath = [str(_lib_path(c)) for c in proc.get("classpath", [])]
        classpath.append(str(jar))
        sep = ";" if sys.platform == "win32" else ":"
        args = [_expand_arg(a, data) for a in proc.get("args", [])]

        cmd = [str(java_exe), "-cp", sep.join(classpath), _manifest_main_class(jar), *args]
        log.info("processor %d/%d: %s", n, total, proc["jar"])
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=1800, errors="replace")
        if result.returncode != 0:
            tail = (result.stdout or "")[-1500:] + (result.stderr or "")[-1500:]
            raise LoaderError(
                f"Installer step {n}/{total} ({proc['jar']}) failed:\n{tail.strip()}"
            )


def _install_from_installer(
    installer_url: str,
    version_label: str,
    mc_version: str,
    progress: Progress,
) -> str:
    """Shared NeoForge/Forge path: download the installer and run it ourselves."""
    config.ensure_dirs()
    _step(progress, "Downloading installer")
    installer = config.CACHE_DIR / f"{version_label}-installer.jar"
    download(DownloadTask(installer_url, installer))

    with zipfile.ZipFile(installer) as zf:
        names = set(zf.namelist())
        if "install_profile.json" not in names:
            raise LoaderError("That installer is not in a format this launcher understands.")
        install_profile = json.loads(zf.read("install_profile.json"))
        version_json = (
            json.loads(zf.read("version.json")) if "version.json" in names else None
        )

    # Pre-1.13 installers embed the profile instead of shipping version.json.
    if version_json is None:
        version_json = install_profile.get("versionInfo")
        if version_json is None:
            raise LoaderError("Installer contains no version profile.")

    version_id = version_json.get("id") or version_label
    version_json.setdefault("inheritsFrom", mc_version)
    mojang.write_version_json(version_id, version_json)

    _step(progress, "Installing base Minecraft")
    mojang.install_version(mc_version, progress=progress)

    _step(progress, "Downloading loader libraries")
    _download_profile_libraries(version_json.get("libraries", []), progress)
    _download_profile_libraries(install_profile.get("libraries", []), progress)

    if not install_profile.get("processors"):
        return version_id  # Old Forge: nothing further to do.

    mc_jar = mojang.client_jar_path(mc_version)
    base_version = mojang.load_version(mc_version)
    runtime = java.select(base_version)

    with tempfile.TemporaryDirectory(prefix="openmcl-install-") as tmp:
        work = Path(tmp)
        data = _resolve_data(
            install_profile.get("data", {}), installer, work, mc_jar, config.DATA
        )
        _run_processors(install_profile, installer, data, runtime.path, progress)

    return version_id


def install_neoforge(mc_version: str, loader_version: str = "", progress: Progress = None) -> str:
    if not loader_version:
        versions = neoforge_versions(mc_version)
        if not versions:
            raise LoaderError(f"NeoForge has no builds for Minecraft {mc_version}.")
        loader_version = versions[0]
    url = (
        f"{NEOFORGE_MAVEN}/net/neoforged/neoforge/{loader_version}/"
        f"neoforge-{loader_version}-installer.jar"
    )
    return _install_from_installer(url, f"neoforge-{loader_version}", mc_version, progress)


def install_forge(mc_version: str, loader_version: str = "", progress: Progress = None) -> str:
    if not loader_version:
        loader_version = forge_recommended(mc_version)
    if not loader_version:
        versions = forge_versions(mc_version)
        if not versions:
            raise LoaderError(f"Forge has no builds for Minecraft {mc_version}.")
        loader_version = versions[0]
    full = f"{mc_version}-{loader_version}"
    url = f"{FORGE_MAVEN}/net/minecraftforge/forge/{full}/forge-{full}-installer.jar"
    return _install_from_installer(url, f"forge-{full}", mc_version, progress)


# ---------------------------------------------------------------------- entry points

def available_versions(loader: str, mc_version: str) -> list[str]:
    try:
        if loader == "fabric":
            return fabric_loader_versions(mc_version)
        if loader == "quilt":
            return quilt_loader_versions(mc_version)
        if loader == "neoforge":
            return neoforge_versions(mc_version)
        if loader == "forge":
            return forge_versions(mc_version)
    except Exception as exc:  # noqa: BLE001 - the UI shows an empty list instead
        log.warning("could not list %s versions for %s: %s", loader, mc_version, exc)
    return []


def install(
    loader: str, mc_version: str, loader_version: str = "", progress: Progress = None
) -> str:
    """Install a loader and return the version id to launch."""
    if loader == "vanilla":
        mojang.install_version(mc_version, progress=progress)
        return mc_version
    installers = {
        "fabric": install_fabric,
        "quilt": install_quilt,
        "neoforge": install_neoforge,
        "forge": install_forge,
    }
    if loader not in installers:
        raise LoaderError(f"Unknown loader: {loader}")
    version_id = installers[loader](mc_version, loader_version, progress)
    # Pull in anything the merged profile still needs (vanilla libs, assets).
    mojang.install_version(version_id, progress=progress)
    return version_id
