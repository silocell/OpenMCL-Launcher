"""CurseForge (api.curseforge.com).

Needs your own API key from https://console.curseforge.com/ - paste it into
Settings. Using the key means accepting CurseForge's third-party API terms.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..net import session
from .base import ModFile, ModHit, ProviderError

log = logging.getLogger(__name__)

API = "https://api.curseforge.com/v1"
NAME = "curseforge"
GAME_ID = 432  # Minecraft

CLASS_IDS = {
    "mod": 6,
    "modpack": 4471,
    "resourcepack": 12,
    "shader": 6552,
    "world": 17,
    "datapack": 6945,
}

LOADER_IDS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}

RELEASE_TYPES = {1: "release", 2: "beta", 3: "alpha"}

# relationType in the files API
RELATION_TYPES = {
    1: "embedded", 2: "optional", 3: "required",
    4: "tool", 5: "incompatible", 6: "embedded",
}

SORTS = {
    "Relevance": 1,
    "Popularity": 2,
    "Downloads": 6,
    "Recently updated": 3,
    "Name": 4,
}

_api_key: str = ""


def configure(api_key: str) -> None:
    global _api_key
    _api_key = (api_key or "").strip()


def available() -> bool:
    return bool(_api_key)


def _request(path: str, params: Optional[dict] = None) -> dict:
    if not _api_key:
        raise ProviderError(
            "No CurseForge API key configured. Get one free at "
            "https://console.curseforge.com/ and paste it into Settings."
        )
    r = session().get(
        f"{API}{path}",
        params=params,
        headers={"x-api-key": _api_key, "Accept": "application/json"},
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise ProviderError("CurseForge rejected the API key. Check it in Settings.")
    if r.status_code >= 400:
        raise ProviderError(f"CurseForge returned {r.status_code}: {r.text[:200]}")
    return r.json()


def search(
    query: str = "",
    project_type: str = "mod",
    mc_version: str = "",
    loader: str = "",
    sort: int = 2,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[ModHit], int]:
    params: dict = {
        "gameId": GAME_ID,
        "classId": CLASS_IDS.get(project_type, 6),
        "pageSize": max(1, min(limit, 50)),
        "index": offset,
        "sortField": sort,
        "sortOrder": "desc",
    }
    if query:
        params["searchFilter"] = query
    if mc_version:
        params["gameVersion"] = mc_version
    if loader and loader in LOADER_IDS and project_type == "mod":
        params["modLoaderType"] = LOADER_IDS[loader]

    data = _request("/mods/search", params)
    hits = []
    for m in data.get("data", []):
        authors = m.get("authors") or []
        hits.append(
            ModHit(
                source=NAME,
                project_id=str(m.get("id", "")),
                slug=m.get("slug", ""),
                name=m.get("name", ""),
                summary=m.get("summary", ""),
                author=authors[0].get("name", "") if authors else "",
                downloads=int(m.get("downloadCount", 0) or 0),
                icon_url=(m.get("logo") or {}).get("thumbnailUrl")
                or (m.get("logo") or {}).get("url", ""),
                page_url=(m.get("links") or {}).get("websiteUrl", ""),
                categories=[c.get("name", "") for c in m.get("categories", [])],
                project_type=project_type,
            )
        )
    total = (data.get("pagination") or {}).get("totalCount", len(hits))
    # CurseForge caps paging at 10,000 results.
    return hits, min(total, 10_000)


def _hash(file_data: dict, algo: int = 1) -> str:
    for h in file_data.get("hashes", []):
        if h.get("algo") == algo:
            return h.get("value", "")
    return ""


def _to_file(f: dict, project_id: str, distributable: bool = True) -> ModFile:
    url = f.get("downloadUrl") or ""
    return ModFile(
        source=NAME,
        project_id=str(project_id),
        version_id=str(f.get("id", "")),
        filename=f.get("fileName", ""),
        url=url,
        name=f.get("displayName", ""),
        version_number=f.get("displayName", ""),
        sha1=_hash(f, 1),
        size=int(f.get("fileLength", 0) or 0),
        release_type=RELEASE_TYPES.get(f.get("releaseType", 1), "release"),
        date=(f.get("fileDate") or "")[:10],
        game_versions=[v for v in f.get("gameVersions", [])],
        loaders=[v.lower() for v in f.get("gameVersions", []) if v.lower() in LOADER_IDS],
        dependencies=[
            (str(d.get("modId")), RELATION_TYPES.get(d.get("relationType", 3), "required"))
            for d in f.get("dependencies", [])
            if d.get("modId")
        ],
        distributable=distributable and bool(url),
    )


def versions(project_id: str, mc_version: str = "", loader: str = "") -> list[ModFile]:
    params: dict = {"pageSize": 50}
    if mc_version:
        params["gameVersion"] = mc_version
    if loader in LOADER_IDS:
        params["modLoaderType"] = LOADER_IDS[loader]
    data = _request(f"/mods/{project_id}/files", params)

    distributable = True
    try:
        info = _request(f"/mods/{project_id}").get("data", {})
        distributable = info.get("allowModDistribution", True) is not False
    except ProviderError:
        pass

    files = [_to_file(f, project_id, distributable) for f in data.get("data", [])]
    files.sort(key=lambda f: f.date, reverse=True)
    return files


def download_url(project_id: str, file_id: str) -> str:
    """Some projects hide downloadUrl in listings; this endpoint still answers."""
    data = _request(f"/mods/{project_id}/files/{file_id}/download-url")
    return data.get("data") or ""


def project(project_id: str) -> dict:
    return _request(f"/mods/{project_id}").get("data", {})


def resolve_url(mod_file: ModFile) -> str:
    """Final download URL, or "" when CurseForge forbids third-party downloads."""
    if mod_file.url:
        return mod_file.url
    try:
        return download_url(mod_file.project_id, mod_file.version_id)
    except ProviderError:
        return ""


def best_file(project_id: str, mc_version: str, loader: str) -> Optional[ModFile]:
    files = versions(project_id, mc_version, loader)
    if not files:
        return None
    stable = [f for f in files if f.release_type == "release"]
    return (stable or files)[0]
