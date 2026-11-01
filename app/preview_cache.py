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
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# (key, base_url, page_id, width, height, cache_path, pool, force, timezone_id, thumb_max)
_Task = tuple[str, str, str, int, int, Path, Any, bool, str | None, int | None]

# Panel-view renders (``<id>~panel<w>x<h>``) are kept per size, so a render can't
# evict a sibling size; those left behind by a rebind age out after this long.
_PANEL_STALE_S = 24 * 3600


class PreviewRenderQueue:
    """A deduped, single-worker queue that renders dashboard previews off the
    request thread."""

    def __init__(self) -> None:
        self._q: queue.Queue[_Task] = queue.Queue()
        self._pending: set[str] = set()
        self._failed_until: dict[str, float] = {}
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
        timezone_id: str | None = None,
        thumb_max: int | None = None,
    ) -> None:
        """Queue a preview render unless one with the same key is already
        queued or in flight. Starts the worker thread lazily. ``force``
        re-renders even when a cached file exists (live-status thumbnails
        refreshing a dashboard whose data moved under an unchanged token).
        ``thumb_max`` downscales the finished PNG so its longest side is at
        most that, after rendering at the full ``width`` x ``height`` (layout
        stays native; only the image shrinks).

        ``timezone_id`` must be resolved by the caller, on the request
        thread: this queue runs on a bare daemon thread with no Flask app
        context, so it cannot read the settings store itself. Omitting it
        leaves Chromium on the container clock, which is UTC under Docker,
        and any clock widget in the preview renders in the wrong zone."""
        # Hover and Lineups callers share a flight, scoped to their data root.
        key = str(cache_path)
        with self._lock:
            if key in self._pending or time.monotonic() < self._failed_until.get(key, 0):
                return
            self._failed_until = {
                k: until for k, until in self._failed_until.items() if until > time.monotonic()
            }
            self._pending.add(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="tesserae-preview-render", daemon=True
                )
                self._thread.start()
        self._q.put(
            (key, base_url, page_id, width, height, cache_path, pool, force, timezone_id, thumb_max)
        )

    def join(self) -> None:
        """Block until every queued task has been processed."""
        self._q.join()

    def _run(self) -> None:
        while True:
            key, base_url, page_id, width, height, cache_path, pool, force, tz, thumb = (
                self._q.get()
            )
            try:
                if force or not cache_path.exists():
                    self._render(base_url, page_id, width, height, cache_path, pool, tz, thumb)
            except Exception:
                with self._lock:
                    self._failed_until[key] = time.monotonic() + 60
                logger.debug("preview: background render failed for %s", page_id, exc_info=True)
            finally:
                with self._lock:
                    self._pending.discard(key)
                self._q.task_done()

    def _render(
        self,
        base_url: str,
        page_id: str,
        width: int,
        height: int,
        cache_path: Path,
        pool: Any,
        timezone_id: str | None = None,
        thumb_max: int | None = None,
    ) -> None:
        from app.renderer import RenderRequest, render_to_png, to_loopback_url

        url = to_loopback_url(f"{base_url}/compose/{page_id}?w={width}&h={height}")
        png = render_to_png(
            RenderRequest(
                url=url,
                viewport_w=width,
                viewport_h=height,
                timezone_id=timezone_id,
            ),
            pool=pool,
        )
        if thumb_max and max(width, height) > thumb_max:
            png = _downscale(png, thumb_max)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._evict_stale(cache_path)
        # Atomic publish so a concurrent read never sees a half-written file.
        tmp = cache_path.parent / f".{cache_path.stem}.{os.getpid()}.tmp"
        tmp.write_bytes(png)
        tmp.replace(cache_path)

    @staticmethod
    def _evict_stale(cache_path: Path) -> None:
        """Drop older versions of this page so the cache doesn't grow per edit.

        The cache key is ``<prefix>__<16-hex token>``, so the prefix is matched
        exactly (a page id containing ``__`` can't reach another page's files).
        Panel-view prefixes are per size and only evict their own size; stale
        sizes from a rebind age out instead (ponytail: age-based, not exact)."""
        m = re.match(r"^(.+?)__[0-9a-f]{16}(?:__.*)?\.png$", cache_path.name)
        if m is None:
            return
        prefix = m.group(1)
        own = re.compile(rf"^{re.escape(prefix)}__[0-9a-f]{{16}}(?:__.*)?\.png$")
        panel_family = prefix.split("~panel", 1)[0] if "~panel" in prefix else None
        now = time.time()
        for f in cache_path.parent.glob("*.png"):
            if f.name == cache_path.name:
                continue
            if own.match(f.name) or (
                panel_family is not None
                and f.name.startswith(f"{panel_family}~panel")
                and now - f.stat().st_mtime > _PANEL_STALE_S
            ):
                f.unlink(missing_ok=True)


def _downscale(png: bytes, longest: int) -> bytes:
    """Shrink a PNG so its longest side is ``longest``, aspect preserved."""
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        im.thumbnail((longest, longest), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()


_QUEUE = PreviewRenderQueue()


def submit(**kwargs: Any) -> None:
    """Enqueue a background preview render (deduped, single-flight)."""
    _QUEUE.submit(**kwargs)


def join() -> None:
    """Block until every queued render has finished. Test helper only."""
    _QUEUE.join()
