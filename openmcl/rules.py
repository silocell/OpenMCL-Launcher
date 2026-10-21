"""Pure helpers: OS detection, version-JSON rule evaluation, maven coordinates,
and argument template substitution. No I/O here, so it is all unit-testable.
"""
from __future__ import annotations

import platform
import re
import sys
from pathlib import PurePosixPath
from typing import Any, Iterable, Optional


def os_name() -> str:
    """The name Mojang's version JSON uses."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "osx"
    return "linux"


def os_arch() -> str:
    """The value used by `os.arch` rules: x86, x86_64, arm64 or arm32."""
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if m in ("i386", "i486", "i586", "i686", "x86"):
        return "x86"
    if m in ("aarch64", "arm64"):
        return "arm64"
    if m.startswith("arm"):
        return "arm32"
    return m


def natives_arch_word() -> str:
    """What `${arch}` expands to inside a natives classifier."""
    return "64" if os_arch() in ("x86_64", "arm64") else "32"


def os_version() -> str:
    return platform.release()


def _match_os(spec: dict) -> bool:
    if "name" in spec and spec["name"] != os_name():
        return False
    if "arch" in spec and spec["arch"] != os_arch():
        return False
    if "version" in spec:
        try:
            if not re.search(spec["version"], os_version()):
                return False
        except re.error:
            return False
    return True


def rules_allow(
    rules: Optional[Iterable[dict]],
    features: Optional[dict[str, bool]] = None,
) -> bool:
    """Evaluate a Mojang `rules` block.

    No rules means allowed. Otherwise the last matching rule's action wins,
    starting from an implicit deny.
    """
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        matched = True
        if "os" in rule:
            matched = matched and _match_os(rule["os"])
        if "features" in rule:
            for key, want in rule["features"].items():
                if bool(features.get(key, False)) != bool(want):
                    matched = False
                    break
        if matched:
            allowed = rule.get("action", "allow") == "allow"
    return allowed


def maven_to_path(coord: str) -> str:
    """`group:artifact:version[:classifier][@ext]` -> relative repository path."""
    coord, _, ext = coord.partition("@")
    ext = ext or "jar"
    parts = coord.split(":")
    if len(parts) < 3:
        raise ValueError(f"not a maven coordinate: {coord!r}")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else ""
    name = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "") + f".{ext}"
    return str(PurePosixPath(*group.split("."), artifact, version, name))


def maven_to_url(coord: str, base: str) -> str:
    return base.rstrip("/") + "/" + maven_to_path(coord)


_TEMPLATE = re.compile(r"\$\{([A-Za-z0-9_]+)\}")


def substitute(text: str, ctx: dict[str, Any]) -> str:
    """Replace `${name}` placeholders; unknown names are left untouched."""
    return _TEMPLATE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), text)


def flatten_arguments(
    args: Iterable[Any],
    ctx: dict[str, Any],
    features: Optional[dict[str, bool]] = None,
) -> list[str]:
    """Turn a 1.13+ `arguments.game`/`arguments.jvm` list into a flat argv list.

    Entries are either plain strings or `{"rules": [...], "value": str|[str]}`.
    """
    out: list[str] = []
    for entry in args:
        if isinstance(entry, str):
            out.append(substitute(entry, ctx))
            continue
        if not isinstance(entry, dict):
            continue
        if not rules_allow(entry.get("rules"), features):
            continue
        value = entry.get("value", [])
        if isinstance(value, str):
            value = [value]
        out.extend(substitute(v, ctx) for v in value)
    return out


def library_natives_classifier(lib: dict) -> Optional[str]:
    """Classifier for a legacy-style natives jar, or None."""
    natives = lib.get("natives")
    if not natives:
        return None
    key = natives.get(os_name())
    if not key:
        return None
    return key.replace("${arch}", natives_arch_word())


def is_native_only(lib: dict) -> bool:
    """1.19+ ships some natives as ordinary libraries named `...:natives-linux`."""
    name = lib.get("name", "")
    parts = name.split(":")
    if len(parts) < 4:
        return False
    return parts[3].startswith("natives-")


def newer_or_equal(version_a: str, version_b: str) -> bool:
    """Compare dotted release strings leniently; non-numeric pieces sort last."""
    def key(v: str):
        return [int(p) if p.isdigit() else -1 for p in re.split(r"[.\-_]", v)]
    try:
        return key(version_a) >= key(version_b)
    except ValueError:
        return False
