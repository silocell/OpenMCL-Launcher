"""Background work: a generic worker thread and an async icon loader."""
from __future__ import annotations

import hashlib
import logging
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal
from PySide6.QtGui import QPixmap

from .. import config
from ..net import session

log = logging.getLogger(__name__)

# Qt aborts the whole process if a still-running QThread is garbage collected
# ("QThread: Destroyed while thread is still running"). That is easy to cause
# by accident: `self._worker = new_worker` drops the previous one, and if it
# has not quite finished the app dies instantly with no traceback. Holding a
# reference here until the thread signals finished removes the hazard from
# every call site at once.
_live: set["Worker"] = set()


class Worker(QThread):
    """Runs a callable off the UI thread.

    If the callable takes a `progress` keyword, it is handed one that emits
    the `progress` signal.
    """

    progress = Signal(int, int, str)
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[..., Any], *args, wants_progress: bool = True, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._wants_progress = wants_progress

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        _live.add(self)
        self.finished.connect(self._forget)
        super().start(priority)

    def _forget(self) -> None:
        _live.discard(self)

    def run(self) -> None:
        try:
            kwargs = dict(self._kwargs)
            if self._wants_progress:
                kwargs["progress"] = self._emit_progress
            result = self._fn(*self._args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as text
            log.debug("worker failed\n%s", traceback.format_exc())
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit(result)

    def _emit_progress(self, current: int, total: int, label: str) -> None:
        self.progress.emit(current, total, label)


class _IconSignals(QObject):
    loaded = Signal(str, QPixmap)


class _IconJob(QRunnable):
    def __init__(self, url: str, signals: _IconSignals, size: int):
        super().__init__()
        self.url = url
        self.signals = signals
        self.size = size

    def start(self, priority: QThread.Priority = QThread.Priority.InheritPriority) -> None:
        _live.add(self)
        self.finished.connect(self._forget)
        super().start(priority)

    def _forget(self) -> None:
        _live.discard(self)

    def run(self) -> None:
        try:
            cache_dir = config.CACHE_DIR / "icons"
            cache_dir.mkdir(parents=True, exist_ok=True)
            key = hashlib.sha1(self.url.encode()).hexdigest()
            path = cache_dir / key
            if not path.exists():
                r = session().get(self.url, timeout=20)
                r.raise_for_status()
                if len(r.content) > 8 * 1024 * 1024:
                    return
                path.write_bytes(r.content)
            pix = QPixmap()
            if not pix.loadFromData(path.read_bytes()):
                return
            self.signals.loaded.emit(self.url, pix)
        except Exception as exc:  # noqa: BLE001 - a missing icon is not an error
            log.debug("icon load failed %s: %s", self.url, exc)


class IconLoader(QObject):
    """Fetches project icons in the background, with a small disk cache."""

    loaded = Signal(str, QPixmap)

    def __init__(self, parent=None, max_threads: int = 6):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max_threads)
        self._signals = _IconSignals()
        self._signals.loaded.connect(self._on_loaded)
        self._memory: dict[str, QPixmap] = {}
        self._inflight: set[str] = set()

    def _on_loaded(self, url: str, pix: QPixmap) -> None:
        self._memory[url] = pix
        self._inflight.discard(url)
        self.loaded.emit(url, pix)

    def cached(self, url: str) -> QPixmap | None:
        return self._memory.get(url)

    def request(self, url: str, size: int = 48) -> QPixmap | None:
        if not url:
            return None
        hit = self._memory.get(url)
        if hit is not None:
            return hit
        if url not in self._inflight:
            self._inflight.add(url)
            self._pool.start(_IconJob(url, self._signals, size))
        return None

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)
