"""Turning an instance + account into a running Minecraft process."""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import __version__, config, java, mojang
from .auth import Account
from .config import Settings
from .instances import Instance
from .rules import flatten_arguments, os_name, substitute

log = logging.getLogger(__name__)

CLASSPATH_SEP = ";" if sys.platform == "win32" else ":"

# Pre-1.13 versions have no `arguments.jvm`; this is what vanilla used.
LEGACY_JVM_ARGS = [
    "-Djava.library.path=${natives_directory}",
    "-cp",
    "${classpath}",
]


@dataclass
class LaunchPlan:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    java: java.JavaRuntime
    version: dict

    def pretty(self) -> str:
        return " ".join(shlex.quote(c) for c in self.command)


def _setting(instance_value, global_value):
    """Instance overrides win when they are set (non-empty / non-zero)."""
    if isinstance(instance_value, bool) or instance_value is None:
        return global_value if instance_value is None else instance_value
    return instance_value if instance_value else global_value


def build_plan(
    instance: Instance,
    account: Account,
    settings: Settings,
    version: Optional[dict] = None,
    quick_play_server: str = "",
) -> LaunchPlan:
    version = version or mojang.load_version(instance.launch_version)
    runtime = java.select(version, _setting(instance.java_path, settings.java_path))

    game_dir = instance.game_dir
    game_dir.mkdir(parents=True, exist_ok=True)

    natives_dir = config.NATIVES_DIR / version["id"]
    if not natives_dir.is_dir() or not any(natives_dir.iterdir()):
        mojang.extract_natives(version, natives_dir)

    index = mojang.ensure_asset_index(version)
    assets_dir = mojang.materialise_legacy_assets(version, index, game_dir)
    assets_index_name = version.get("assetIndex", {}).get("id") or version.get(
        "assets", "legacy"
    )

    cp = mojang.classpath_entries(version)
    classpath = CLASSPATH_SEP.join(str(p) for p in cp)

    width = _setting(instance.window_width, settings.window_width)
    height = _setting(instance.window_height, settings.window_height)
    fullscreen = (
        settings.fullscreen if instance.fullscreen is None else instance.fullscreen
    )

    ctx = {
        "auth_player_name": account.name,
        "auth_uuid": account.uuid.replace("-", ""),
        "auth_access_token": account.access_token,
        "auth_session": f"token:{account.access_token}:{account.uuid.replace('-', '')}",
        "auth_xuid": account.xuid,
        "clientid": settings.ms_client_id or "openmcl",
        "user_type": "msa",
        "user_properties": "{}",
        "version_name": version["id"],
        "version_type": version.get("type", "release"),
        "game_directory": str(game_dir),
        "assets_root": str(assets_dir),
        "game_assets": str(assets_dir),
        "assets_index_name": assets_index_name,
        "natives_directory": str(natives_dir),
        "launcher_name": "openmcl",
        "launcher_version": __version__,
        "classpath": classpath,
        "classpath_separator": CLASSPATH_SEP,
        "library_directory": str(config.LIBRARIES_DIR),
        "resolution_width": str(width),
        "resolution_height": str(height),
        "quickPlayPath": str(game_dir / "quickPlay.json"),
        "quickPlaySingleplayer": "",
        "quickPlayMultiplayer": quick_play_server,
        "quickPlayRealms": "",
    }

    features = {
        "is_demo_user": False,
        "has_custom_resolution": not fullscreen,
        "has_quick_plays_support": bool(quick_play_server),
        "is_quick_play_singleplayer": False,
        "is_quick_play_multiplayer": bool(quick_play_server),
        "is_quick_play_realms": False,
    }

    # ---- JVM arguments
    memory = _setting(instance.memory_mb, settings.memory_mb)
    min_memory = _setting(instance.min_memory_mb, settings.min_memory_mb)
    jvm: list[str] = [f"-Xmx{memory}M", f"-Xms{min_memory}M"]

    extra = _setting(instance.jvm_args, settings.jvm_args)
    if extra:
        jvm.extend(substitute(a, ctx) for a in shlex.split(extra))

    declared_jvm = version.get("arguments", {}).get("jvm")
    if declared_jvm:
        jvm.extend(flatten_arguments(declared_jvm, ctx, features))
    else:
        jvm.extend(substitute(a, ctx) for a in LEGACY_JVM_ARGS)

    if os_name() == "osx" and "-XstartOnFirstThread" not in jvm:
        jvm.append("-XstartOnFirstThread")

    log_cfg = version.get("logging", {}).get("client")
    if log_cfg and log_cfg.get("file", {}).get("id"):
        path = config.ASSETS_DIR / "log_configs" / log_cfg["file"]["id"]
        if path.exists():
            # Mojang's own XML config writes an ANSI-coloured console log we can parse.
            jvm.append(log_cfg.get("argument", "-Dlog4j.configurationFile=${path}")
                       .replace("${path}", str(path)))

    jvm.append("-Dminecraft.launcher.brand=openmcl")
    jvm.append(f"-Dminecraft.launcher.version={__version__}")

    # ---- game arguments
    game_args_spec = version.get("arguments", {}).get("game")
    if game_args_spec is not None:
        game: list[str] = flatten_arguments(game_args_spec, ctx, features)
    else:
        legacy = version.get("minecraftArguments", "")
        game = [substitute(a, ctx) for a in legacy.split()]
        if not fullscreen:
            game += ["--width", str(width), "--height", str(height)]

    if fullscreen and "--fullscreen" not in game:
        game.append("--fullscreen")

    command = [str(runtime.path), *jvm, version["mainClass"], *game]

    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in settings.extra_env.items()})
    env.update({str(k): str(v) for k, v in instance.env.items()})

    return LaunchPlan(command=command, cwd=game_dir, env=env, java=runtime, version=version)


def launch(
    plan: LaunchPlan,
    on_output: Optional[Callable[[str], None]] = None,
    pre_launch_command: str = "",
) -> subprocess.Popen:
    """Start the game. Output is line-buffered and passed to `on_output`."""
    if pre_launch_command:
        log.info("running pre-launch command")
        subprocess.run(
            pre_launch_command, shell=True, cwd=plan.cwd, env=plan.env, timeout=300
        )

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        plan.command,
        cwd=str(plan.cwd),
        env=plan.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        errors="replace",
        creationflags=creationflags,
    )

    if on_output and proc.stdout:
        import threading

        def pump():
            assert proc.stdout is not None
            for line in proc.stdout:
                on_output(line.rstrip("\n"))

        threading.Thread(target=pump, daemon=True, name="mc-output").start()

    return proc


def write_crash_log(instance: Instance, lines: list[str]) -> Path:
    config.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = config.LOGS_DIR / f"{instance.id}-{stamp}.log"
    path.write_text("\n".join(lines), "utf-8")
    return path
