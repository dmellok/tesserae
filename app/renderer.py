"""Headless-browser screenshot pipeline.

Wraps Playwright's sync API. The composer route ``/compose/<id>`` is what
Chromium points at; the screenshot of that URL is the composition-orientation
PNG that every renderer plugin's ``transform()`` then takes as input.

The screenshot is always at the panel's exact pixel size — no resampling, no
DPR scaling. ``device_scale_factor=1`` is load-bearing.

Two execution paths:

* **Cold path** (``render_to_png(request)``) — spins up a fresh ``sync_playwright``
  + Chromium per call. ~1–2 s overhead per render. Used when the
  ``keep_browser_warm`` toggle is off (low-memory deployments).
* **Warm path** (``render_to_png(request, pool=BrowserPool)``) — reuses a
  long-lived browser owned by a dedicated worker thread, creating a fresh
  ``context`` per render so state never leaks between pushes. ~200 ms per
  render after the first warm-up. The dedicated thread is required because
  Playwright's sync API isn't thread-safe.

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

DEFAULT_PANEL_W: Final[int] = 1600
DEFAULT_PANEL_H: Final[int] = 1200

WaitUntil = Literal["load", "domcontentloaded", "networkidle", "commit"]

_CHROMIUM_SIDECAR: Final[Path] = (
    Path(__file__).resolve().parent.parent / "data" / "core" / ".chromium"
)


def _chromium_launch_kwargs() -> dict[str, Any]:
    """Resolve the Chromium binary in order: ``TESSERAE_CHROMIUM_PATH`` env
    var → ``data/core/.chromium`` sidecar (written by install.sh when
    Playwright has no prebuilt for the host's OS+arch) → empty (Playwright
    uses its bundled binary)."""
    path = os.environ.get("TESSERAE_CHROMIUM_PATH", "").strip()
    if not path:
        try:
            path = _CHROMIUM_SIDECAR.read_text(encoding="utf-8").strip()
        except OSError:
            path = ""
    return {"executable_path": path} if path else {}


def to_loopback_url(url: str) -> str:
    """Rewrite the host portion of ``url`` to ``127.0.0.1`` while preserving
    the port + path + query.

    The renderer always runs in-process with Flask, so internal compose URLs
    should always resolve via loopback regardless of what the user set
    ``base_url`` to. Two reasons:

    1. **Auth gate (M3).** The single-password gate will have a loopback
       bypass for ``/compose/<id>`` so the renderer can fetch dashboards
       without juggling a session cookie. Going via the LAN-IP base_url
       would skip the bypass and screenshot the login page.
    2. **Routing.** A LAN-IP round-trip leaves the loopback interface on
       some OS/network configs, adding latency for no gain.

    ``base_url`` is still used as-is for OUTBOUND URLs (image entity in HA,
    public render links). This helper is for the in-process renderer only.
    """
    parts = urlsplit(url)
    netloc = "127.0.0.1"
    if parts.port:
        netloc = f"127.0.0.1:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class RenderRequest:
    url: str
    viewport_w: int = DEFAULT_PANEL_W
    viewport_h: int = DEFAULT_PANEL_H
    timeout_ms: int = 15_000
    wait_until: WaitUntil = "networkidle"


_FONT_WAIT_JS: Final[str] = """async () => {
    if (!document.fonts || !document.fonts.load) return;
    const families = new Set();
    document.querySelectorAll('.cell').forEach((cell) => {
        const ff = getComputedStyle(cell).fontFamily;
        if (!ff) return;
        const first = ff.split(',')[0].trim()
            .replace(/^['\"]|['\"]$/g, '');
        if (first) families.add(first);
    });
    const loads = [];
    for (const family of families) {
        for (const weight of [400, 500, 600, 700]) {
            loads.push(
                document.fonts.load(
                    weight + ' 100px \"' + family + '\"'
                ).catch(() => {})
            );
        }
    }
    await Promise.all(loads);
    await document.fonts.ready;
}"""


def _screenshot_one(browser: Browser, request: RenderRequest) -> bytes:
    """The actual render — opens a fresh context on ``browser``, navigates,
    screenshots, and disposes. Reused by both cold and warm paths so the
    ``networkidle`` timeout / font-load / pixel-exact viewport behaviour
    stays identical regardless of which path the caller took."""
    context = browser.new_context(
        viewport={"width": request.viewport_w, "height": request.viewport_h},
        device_scale_factor=1,
        color_scheme="light",
    )
    try:
        page = context.new_page()
        page.set_default_timeout(request.timeout_ms)
        # Best-effort ``networkidle``: a single widget holding a long-poll
        # open shouldn't block the whole render. On timeout, fall back to
        # ``load`` — DOM is laid out, the font wait below gives one more
        # settle point before screenshotting.
        if request.wait_until == "networkidle":
            try:
                page.goto(request.url, wait_until="networkidle")
            except PlaywrightTimeoutError:
                page.wait_for_load_state("load", timeout=request.timeout_ms)
        else:
            page.goto(request.url, wait_until=request.wait_until)
        # Block screenshot until every cell's font is actually loaded.
        # ``document.fonts.ready`` only awaits fonts already pending;
        # ``document.fonts.load()`` triggers the request and waits.
        page.evaluate(_FONT_WAIT_JS)
        png: bytes = page.screenshot(
            full_page=False,
            type="png",
            animations="disabled",
            omit_background=False,
        )
        return png
    finally:
        # Closing the context tears down the page, cookies, localStorage,
        # font cache — so the next render starts clean even though the
        # browser itself stays alive on the warm path.
        try:
            context.close()
        except Exception:
            logger.debug("context close failed (continuing)", exc_info=True)


def render_to_png(request: RenderRequest, *, pool: BrowserPool | None = None) -> bytes:
    """Open the URL in headless Chromium and return a PNG screenshot.

    The browser viewport matches viewport_w/h exactly so the screenshot is
    pixel-equal to what the panel will receive (after each renderer
    transforms it).

    If ``pool`` is supplied the render runs against its long-lived browser
    (cheap, ~200 ms steady-state). Otherwise a fresh ``sync_playwright`` +
    ``chromium.launch()`` is spun up per call (~1–2 s, but zero idle RAM)."""
    if pool is not None:
        return pool.render(request)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**_chromium_launch_kwargs())
        try:
            return _screenshot_one(browser, request)
        finally:
            browser.close()


# -- warm path: long-lived browser owned by a dedicated thread -----------


class BrowserPool:
    """Long-running Chromium owned by a single worker thread.

    Playwright's sync API binds objects (Playwright, Browser, Page) to the
    OS thread that created them, so the pool routes every render request
    through one dedicated worker that owns the ``sync_playwright`` instance
    and the launched Chromium handle. Callers from any other thread enqueue
    a (request, Future) pair and block on the future.

    Each render still uses a fresh ``context``, so cookies / localStorage /
    runtime font cache never leak between dashboards. Only the browser
    process is reused — which is where the ~1.5 s of cold-start lives.

    Crash recovery: if Chromium dies under us, the next render relaunches
    it. The worker thread itself survives until ``stop()`` is called."""

    _SENTINEL: Final = ()  # signal the worker to drain + exit

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[RenderRequest, concurrent.futures.Future[bytes]] | tuple[()]] = (
            queue.Queue()
        )
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stopped:
                return
            t = threading.Thread(target=self._run, name="tesserae-browser-pool", daemon=True)
            t.start()
            self._thread = t

    def stop(self, *, timeout: float = 10.0) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
        if thread is None:
            return
        self._q.put(self._SENTINEL)
        thread.join(timeout=timeout)
        if thread.is_alive():
            logger.warning("browser pool worker did not exit within %.1fs", timeout)

    def render(self, request: RenderRequest) -> bytes:
        # Lazy start on first request — the App settings toggle decides
        # whether the caller routes here at all, so the pool stays cold
        # (no Chromium spawned) if it's never asked.
        if self._thread is None:
            self.start()
        if self._stopped:
            raise RuntimeError("browser pool has been stopped")
        fut: concurrent.futures.Future[bytes] = concurrent.futures.Future()
        self._q.put((request, fut))
        # Allow the request's own timeout plus generous slack for launch +
        # context setup; the pool isn't meant to be a hard timeout layer.
        return fut.result(timeout=request.timeout_ms / 1000 + 60)

    def _run(self) -> None:
        pw: Playwright | None = None
        browser: Browser | None = None
        try:
            pw = sync_playwright().start()
            while True:
                item = self._q.get()
                if item == self._SENTINEL:
                    break
                # Narrow the type — non-sentinel items are always
                # (request, future) tuples per the put() contract.
                request, fut = item  # type: ignore[misc]
                try:
                    if browser is None or not browser.is_connected():
                        if browser is not None:
                            with contextlib.suppress(Exception):
                                browser.close()
                        browser = pw.chromium.launch(**_chromium_launch_kwargs())
                    fut.set_result(_screenshot_one(browser, request))
                except Exception as exc:
                    fut.set_exception(exc)
                    # If the failure was a browser-level crash, drop the
                    # handle so the next render relaunches Chromium cleanly.
                    if browser is not None and not browser.is_connected():
                        browser = None
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    logger.debug("browser close on shutdown failed", exc_info=True)
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    logger.debug("playwright stop on shutdown failed", exc_info=True)
