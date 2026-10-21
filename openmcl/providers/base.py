"""Shared shapes so the UI does not care which site a mod came from."""
from __future__ import annotations

from dataclasses import dataclass, field


class ProviderError(RuntimeError):
    pass


@dataclass
class ModHit:
    source: str
    project_id: str
    name: str
    summary: str = ""
    slug: str = ""
    author: str = ""
    downloads: int = 0
    icon_url: str = ""
    page_url: str = ""
    categories: list[str] = field(default_factory=list)
    project_type: str = "mod"

    @property
    def downloads_human(self) -> str:
        n = self.downloads
        for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
            if n >= limit:
                return f"{n / limit:.1f}{suffix}"
        return str(n)


@dataclass
class ModFile:
    source: str
    project_id: str
    version_id: str
    filename: str
    url: str = ""
    name: str = ""
    version_number: str = ""
    sha1: str = ""
    size: int = 0
    release_type: str = "release"
    date: str = ""
    game_versions: list[str] = field(default_factory=list)
    loaders: list[str] = field(default_factory=list)
    # (project_id, "required" | "optional" | "incompatible" | "embedded")
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    distributable: bool = True
    page_url: str = ""

    @property
    def label(self) -> str:
        base = self.version_number or self.name or self.filename
        tag = "" if self.release_type == "release" else f"  [{self.release_type}]"
        return f"{base}{tag}"
