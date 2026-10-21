"""Modrinth (api.modrinth.com) - no API key needed, but a real User-Agent is."""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..net import get_json
from .base import ModFile, ModHit, ProviderError

log = logging.getLogger(__name__)

API = "https://api.modrinth.com/v2"
NAME = "modrinth"

# Modrinth project types we expose in the browser.
PROJECT_TYPES = ("mod", "modpack", "resourcepack", "shader", "datapack")

SORTS = {
    "Relevance": "relevance",
    "Downloads": "downloads",
    "Follows": "follows",
    "Newest": "newest",
    "Recently updated": "updated",
}


def _facets(
    project_type: str, mc_version: str = "", loader: str = "", categories: Optional[list] = None
) -> str:
    facets: list[list[str]] = [[f"project_type:{project_type}"]]
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    # Modrinth files loaders under `categories`; resource packs have no loader.
    if loader and loader != "vanilla" and project_type in ("mod", "modpack"):
        facets.append([f"categories:{loader}"])
    for c in categories or []:
        facets.append([f"categories:{c}"])
    return json.dumps(facets)


def search(
    query: str = "",
    project_type: str = "mod",
    mc_version: str = "",
    loader: str = "",
    sort: str = "relevance",
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[ModHit], int]:
    params = {
        "query": query,
        "limit": max(1, min(limit, 100)),
        "offset": offset,
        "index": sort,
        "facets": _facets(project_type, mc_version, loader),
    }
    try:
        data = get_json(f"{API}/search", params=params)
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"Modrinth search failed: {exc}") from exc

    hits = [
        ModHit(
            source=NAME,
            project_id=h.get("project_id", ""),
            slug=h.get("slug", ""),
            name=h.get("title", ""),
            summary=h.get("description", ""),
            author=h.get("author", ""),
            downloads=h.get("downloads", 0),
            icon_url=h.get("icon_url") or "",
            page_url=f"https://modrinth.com/{h.get('project_type', 'mod')}/{h.get('slug', '')}",
            categories=h.get("display_categories") or h.get("categories", []),
            project_type=h.get("project_type", project_type),
        )
        for h in data.get("hits", [])
    ]
    return hits, data.get("total_hits", len(hits))


def _to_file(v: dict) -> Optional[ModFile]:
    files = v.get("files", [])
    chosen = next((f for f in files if f.get("primary")), files[0] if files else None)
    if not chosen:
        return None
    return ModFile(
        source=NAME,
        project_id=v.get("project_id", ""),
        version_id=v.get("id", ""),
        filename=chosen.get("filename", ""),
        url=chosen.get("url", ""),
        name=v.get("name", ""),
        version_number=v.get("version_number", ""),
        sha1=(chosen.get("hashes") or {}).get("sha1", ""),
        size=chosen.get("size", 0),
        release_type=v.get("version_type", "release"),
        date=v.get("date_published", "")[:10],
        game_versions=v.get("game_versions", []),
        loaders=v.get("loaders", []),
        dependencies=[
            (d.get("project_id") or "", d.get("dependency_type", "required"))
            for d in v.get("dependencies", [])
            if d.get("project_id")
        ],
    )


def versions(
    project_id: str, mc_version: str = "", loader: str = ""
) -> list[ModFile]:
    params = {}
    if mc_version:
        params["game_versions"] = json.dumps([mc_version])
    if loader and loader != "vanilla":
        params["loaders"] = json.dumps([loader])
    try:
        data = get_json(f"{API}/project/{project_id}/version", params=params)
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"Could not list Modrinth versions: {exc}") from exc
    out = [f for f in (_to_file(v) for v in data) if f]
    return out


def project(project_id: str) -> dict:
    return get_json(f"{API}/project/{project_id}")


def version_by_id(version_id: str) -> Optional[ModFile]:
    try:
        return _to_file(get_json(f"{API}/version/{version_id}"))
    except Exception:  # noqa: BLE001
        return None


def best_file(project_id: str, mc_version: str, loader: str) -> Optional[ModFile]:
    """Newest release that matches, falling back to any matching build."""
    files = versions(project_id, mc_version, loader)
    if not files:
        return None
    stable = [f for f in files if f.release_type == "release"]
    return (stable or files)[0]
