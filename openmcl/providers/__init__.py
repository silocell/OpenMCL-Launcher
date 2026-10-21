"""Mod sources. Each provider exposes the same search/versions interface."""
from .base import ModFile, ModHit, ProviderError
from . import curseforge, modrinth

PROVIDERS = {"modrinth": modrinth, "curseforge": curseforge}

__all__ = ["ModFile", "ModHit", "ProviderError", "PROVIDERS", "modrinth", "curseforge"]
