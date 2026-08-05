"""Background, single-flight renderer for dashboard hover previews.

Keeps the screenshot work OFF the request thread. The
``/compose/<id>/preview.png`` route serves a cached PNG when one exists and
otherwise enqueues a background render and returns immediately (the client
falls back to a live iframe in the meantime). So a burst of hovers can never
pin the waitress worker pool on ~105s render waits, and the render itself
still only needs one transient free worker to serve the inner ``/compose``.

Renders run one at a time on a single daemon thread (matching the browser
pool's own serialisation) and are deduped by cache key, so repeated hovers of
the same unchanged dashboard enqueue at most one render.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# (key, base_url, page_id, width, height, cache_path, pool, force)
_Task = tuple[str, str, str, int, int, Path, Any, bool]


class PreviewRenderQueue:
    """A deduped, single-worker queue that renders dashboard previews off the
    request thread."""

    def __init__(self) -> None:
        self._q: queue.Queue[_Task] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def submit(
        self,
        *,
        key: str,
        base_url: str,
        page_id: str,
        width: int,
        height: int,
        cache_path: Path,
        pool: Any,
        force: bool = False,
    ) -> None:
        """Queue a preview render unless one with the same key is already
        queued or in flight. Starts the worker thread lazily. ``force``
        re-renders even when a cached file exists (live-status thumbnails
        refreshing a dashboard whose data moved under an unchanged token)."""
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="tesserae-preview-render", daemon=True
                )
                self._thread.start()
        self._q.put((key, base_url, page_id, width, height, cache_path, pool, force))

    def join(self) -> None:
        """Block until every queued task has been processed."""
        self._q.join()

    def _run(self) -> None:
        while True:
            key, base_url, page_id, width, height, cache_path, pool, force = self._q.get()
            try:
                if force or not cache_path.exists():
                    self._render(base_url, page_id, width, height, cache_path, pool)
            except Exception:
                logger.debug("preview: background render failed for %s", page_id, exc_info=True)
            finally:
                with self._lock:
                    self._pending.discard(key)
                self._q.task_done()

    def _render(
        self, base_url: str, page_id: str, width: int, height: int, cache_path: Path, pool: Any
    ) -> None:
        from app.renderer import RenderRequest, render_to_png, to_loopback_url

        url = to_loopback_url(f"{base_url}/compose/{page_id}?w={width}&h={height}")
        png = render_to_png(RenderRequest(url=url, viewport_w=width, viewport_h=height), pool=pool)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        # Drop older versions of this page so the cache doesn't grow per edit.
        for stale in cache_path.parent.glob(f"{page_id}__*.png"):
            if stale.name != cache_path.name:
                stale.unlink(missing_ok=True)
        # Atomic publish so a concurrent read never sees a half-written file.
        tmp = cache_path.parent / f".{cache_path.stem}.{os.getpid()}.tmp"
        tmp.write_bytes(png)
        tmp.replace(cache_path)


_QUEUE = PreviewRenderQueue()


def submit(**kwargs: Any) -> None:
    """Enqueue a background preview render (deduped, single-flight)."""
    _QUEUE.submit(**kwargs)


def join() -> None:
    """Block until every queued render has finished. Test helper only."""
    _QUEUE.join()
