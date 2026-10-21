"""Finding a usable Java runtime."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

EXE = "java.exe" if sys.platform == "win32" else "java"


@dataclass
class JavaRuntime:
    path: Path
    major: int
    version: str
    vendor: str = ""

    def __str__(self) -> str:
        return f"Java {self.major} ({self.version}) - {self.path}"


def _parse_major(version: str) -> int:
    # "1.8.0_382" -> 8, "17.0.9" -> 17, "21" -> 21
    m = re.match(r"(\d+)(?:\.(\d+))?", version)
    if not m:
        return 0
    first = int(m.group(1))
    if first == 1 and m.group(2):
        return int(m.group(2))
    return first


def probe(java_path: Path) -> Optional[JavaRuntime]:
    """Ask a java binary what it is. Returns None if it is not usable."""
    if not java_path.exists():
        return None
    try:
        proc = subprocess.run(
            [str(java_path), "-XshowSettings:properties", "-version"],
            capture_output=True,
            text=True,
            timeout=20,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("probe failed for %s: %s", java_path, exc)
        return None
    blob = (proc.stderr or "") + (proc.stdout or "")
    ver = re.search(r"java\.version\s*=\s*(\S+)", blob)
    vendor = re.search(r"java\.vendor\s*=\s*(.+)", blob)
    if not ver:
        ver = re.search(r'version "([^"]+)"', blob)
    if not ver:
        return None
    version = ver.group(1).strip()
    return JavaRuntime(
        path=java_path,
        major=_parse_major(version),
        version=version,
        vendor=vendor.group(1).strip() if vendor else "",
    )


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        for base in (
            Path("C:/Program Files/Java"),
            Path("C:/Program Files/Eclipse Adoptium"),
            Path("C:/Program Files/Microsoft/jdk"),
            Path("C:/Program Files/Zulu"),
            Path("C:/Program Files (x86)/Java"),
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Eclipse Adoptium",
        ):
            if base.is_dir():
                roots.extend(p for p in base.iterdir() if p.is_dir())
    elif sys.platform == "darwin":
        base = Path("/Library/Java/JavaVirtualMachines")
        if base.is_dir():
            roots.extend(p / "Contents" / "Home" for p in base.iterdir())
        brew = Path("/opt/homebrew/opt")
        if brew.is_dir():
            roots.extend(p / "libexec" / "openjdk.jdk" / "Contents" / "Home"
                         for p in brew.glob("openjdk*"))
    else:
        for base in (
            Path("/usr/lib/jvm"),
            Path("/usr/java"),
            Path("/opt/java"),
            Path.home() / ".sdkman" / "candidates" / "java",
        ):
            if base.is_dir():
                roots.extend(p for p in base.iterdir() if p.is_dir())
    return roots


def discover() -> list[JavaRuntime]:
    """Every Java runtime we can find, newest major version first."""
    seen: set[Path] = set()
    found: list[JavaRuntime] = []

    def consider(p: Optional[Path]):
        if not p:
            return
        try:
            p = p.resolve()
        except OSError:
            return
        if p in seen:
            return
        seen.add(p)
        rt = probe(p)
        if rt and rt.major:
            found.append(rt)

    on_path = shutil.which("java")
    if on_path:
        consider(Path(on_path))

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        consider(Path(java_home) / "bin" / EXE)

    for root in _candidate_roots():
        consider(root / "bin" / EXE)

    # Runtimes the official Minecraft launcher has already installed.
    for base in (
        Path.home() / ".minecraft" / "runtime",
        Path(os.environ.get("APPDATA", "")) / ".minecraft" / "runtime",
        Path.home() / "Library" / "Application Support" / "minecraft" / "runtime",
    ):
        if base.is_dir():
            for exe in base.glob(f"**/bin/{EXE}"):
                consider(exe)

    found.sort(key=lambda r: r.major, reverse=True)
    return found


def required_major(version: dict) -> int:
    """What the version JSON asks for, with a sane fallback for old versions."""
    declared = version.get("javaVersion", {}).get("majorVersion")
    if declared:
        return int(declared)
    return 8


def select(version: dict, override: str = "") -> JavaRuntime:
    """Pick a runtime for this Minecraft version, or explain why we cannot."""
    want = required_major(version)
    if override:
        p = Path(override)
        if p.is_dir():
            p = p / "bin" / EXE
        rt = probe(p)
        if not rt:
            raise RuntimeError(f"{override} is not a working Java runtime.")
        if rt.major < want:
            raise RuntimeError(
                f"Minecraft {version.get('id')} needs Java {want} or newer, "
                f"but the configured runtime is Java {rt.major}."
            )
        return rt

    runtimes = discover()
    if not runtimes:
        raise RuntimeError(
            "No Java runtime found. Install a JDK/JRE (Temurin "
            f"{want} is a good choice) or set one in Settings."
        )
    # Prefer an exact major match; otherwise the oldest runtime that is new enough.
    exact = [r for r in runtimes if r.major == want]
    if exact:
        return exact[0]
    newer = sorted([r for r in runtimes if r.major > want], key=lambda r: r.major)
    if newer:
        return newer[0]
    best = runtimes[0]
    raise RuntimeError(
        f"Minecraft {version.get('id')} needs Java {want} or newer. "
        f"The newest runtime found is Java {best.major} at {best.path}."
    )
