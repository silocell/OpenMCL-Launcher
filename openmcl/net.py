"""HTTP session, verified downloads and a small parallel download pool."""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__

log = logging.getLogger(__name__)

# Modrinth requires a uniquely identifying User-Agent. Change the URL if you fork this.
USER_AGENT = f"openmcl/{__version__} (+https://github.com/openmcl/openmcl)"

_local = threading.local()


def session() -> requests.Session:
    """One pooled session per thread."""
    s = getattr(_local, "session", None)
    if s is not None:
        return s
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.6,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "HEAD"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers["User-Agent"] = USER_AGENT
    _local.session = s
    return s


def get_json(url: str, **kw):
    r = session().get(url, timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r.json()


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


@dataclass
class DownloadTask:
    url: str
    dest: Path
    sha1: Optional[str] = None
    size: Optional[int] = None
    executable: bool = False

    def is_satisfied(self) -> bool:
        """True when the file on disk already matches what we would download."""
        if not self.dest.exists():
            return False
        if self.size is not None and self.dest.stat().st_size != self.size:
            return False
        if self.sha1:
            return sha1_file(self.dest) == self.sha1.lower()
        # No hash to check against: size match (or mere existence) has to do.
        return True


class DownloadError(RuntimeError):
    pass


def download(task: DownloadTask, force: bool = False) -> Path:
    if not force and task.is_satisfied():
        return task.dest
    task.dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = task.dest.with_name(task.dest.name + f".part{os.getpid()}")
    try:
        with session().get(task.url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with tmp.open("wb") as fh:
                shutil.copyfileobj(r.raw, fh, length=1 << 20)
        if task.sha1:
            got = sha1_file(tmp)
            if got != task.sha1.lower():
                raise DownloadError(
                    f"checksum mismatch for {task.url}: expected {task.sha1}, got {got}"
                )
        tmp.replace(task.dest)
        if task.executable:
            task.dest.chmod(task.dest.stat().st_mode | 0o111)
        return task.dest
    finally:
        tmp.unlink(missing_ok=True)


def download_many(
    tasks: Iterable[DownloadTask],
    workers: int = 16,
    progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> None:
    """Download in parallel. `progress(done, total, label)` is called per completion."""
    pending = [t for t in tasks if not t.is_satisfied()]
    total = len(pending)
    if not total:
        if progress:
            progress(0, 0, "up to date")
        return
    done = 0
    errors: list[str] = []
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(download, t): t for t in pending}
        for fut in as_completed(futures):
            task = futures[fut]
            with lock:
                done += 1
                n = done
            try:
                fut.result()
            except Exception as exc:  # noqa: BLE001 - collected and reported together
                log.warning("download failed: %s (%s)", task.url, exc)
                errors.append(f"{task.dest.name}: {exc}")
            if progress:
                progress(n, total, task.dest.name)
            if cancel is not None and cancel.is_set():
                for f in futures:
                    f.cancel()
                raise DownloadError("cancelled")
    if errors:
        head = "\n".join(errors[:8])
        more = f"\n...and {len(errors) - 8} more" if len(errors) > 8 else ""
        raise DownloadError(f"{len(errors)} download(s) failed:\n{head}{more}")
