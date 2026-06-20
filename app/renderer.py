"""Headless-browser screenshot pipeline.

Wraps Playwright's sync API. The composer route ``/compose/<id>`` is what
Chromium points at; the screenshot of that URL is the composition-orientation
PNG that every renderer plugin's ``transform()`` then takes as input.

The screenshot is always at the panel's exact pixel size, no resampling, no
DPR scaling. ``device_scale_factor=1`` is load-bearing.

Two execution paths:

* **Cold path** (``render_to_png(request)``), spins up a fresh ``sync_playwright``
  + Chromium per call. ~1–2 s overhead per render. Used when the
  ``keep_browser_warm`` toggle is off (low-memory deployments).
* **Warm path** (``render_to_png(request, pool=BrowserPool)``), reuses a
  long-lived browser owned by a dedicated worker thread, creating a fresh
  ``context`` per render so state never leaks between pushes. ~200 ms per
  render after the first warm-up. The dedicated thread is required because
  Playwright's sync API isn't thread-safe.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import Browser, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
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
    # Prefer the actual bind port the server is listening on internally,
    # since under HA the URL's port is the *host* mapping (e.g. 8766 for
    # edge) and nothing is listening on it inside the container, the
    # add-on always binds 8765 internally. ``TESSERAE_BIND_PORT`` is set
    # by the add-on config; fall back to the URL's port otherwise so a
    # bare-metal install with ``tesserae --port 5050`` keeps working.
    import os as _os

    bind_port_raw = _os.environ.get("TESSERAE_BIND_PORT", "").strip()
    bind_port = int(bind_port_raw) if bind_port_raw.isdigit() else None
    netloc = "127.0.0.1"
    if bind_port:
        netloc = f"127.0.0.1:{bind_port}"
    elif parts.port:
        netloc = f"127.0.0.1:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


@dataclass(frozen=True)
class RenderRequest:
    url: str
    viewport_w: int = DEFAULT_PANEL_W
    viewport_h: int = DEFAULT_PANEL_H
    timeout_ms: int = 15_000
    wait_until: WaitUntil = "networkidle"
    # Total attempts (1 = no retry). Each attempt gets a fresh context, so
    # a transient slow goto / Chromium hiccup doesn't sink the whole push.
    # The intermittent failure mode this exists for is HA-driven pushes
    # where a Playwright ``Page.goto: Timeout 15000ms exceeded`` surfaces
    # under no obvious cause, most commonly a brief loopback contention
    # or a background-thread GC pause that ate the navigation window.
    max_attempts: int = 3
    # IANA zone name (e.g. ``"Europe/London"``) forwarded to Chromium so
    # widgets' client-side ``new Date()`` matches the app's configured
    # timezone, NOT the Docker container's ``TZ`` env var (which defaults
    # to UTC). ``None`` leaves Chromium on its default behaviour, which
    # is what unit tests + the local dev server want.
    timezone_id: str | None = None
    # True when this URL points at our own ``/compose/<id>`` page, False
    # for arbitrary external URLs from the Send → Webpage tab. The two
    # cases need different wait strategies:
    #
    # * Composer renders own the page, so we can rely on
    #   ``window.__tesseraeComposed = true`` as the "screenshot now"
    #   signal, AND we deliberately use ``wait_until="load"`` for the
    #   initial nav because composer pages keep network busy long after
    #   they're visually ready (widget client.js + font + Phosphor CSS
    #   fetches).
    # * External URLs (``is_composer=False``) have no composer signal,
    #   so we'd just stall 15s waiting for a flag that never fires; AND
    #   they're often SPAs that don't paint anything meaningful until
    #   their JS hydrates the page (Reddit's the obvious example), so
    #   ``wait_until="load"`` screenshots an empty React shell. For
    #   external URLs we honour the request's declared ``wait_until``
    #   (defaulting to ``networkidle`` so JS-driven hydration completes
    #   before screenshot), and skip the composer-signal wait.
    is_composer: bool = True


@dataclass(frozen=True)
class FetchRequest:
    """An out-of-band fetch through Chromium's network stack, used by
    widgets whose upstream blocks vanilla ``urllib`` (Reddit, CDNs
    behind JA3 / TLS-fingerprint gates). A fresh context per fetch keeps
    cookies from leaking between widgets / sites; the browser is the
    pool's warm Chromium so cost is one ``new_context`` per call, not
    a full launch."""

    url: str
    timeout_ms: int = 15_000
    user_agent: str | None = None
    accept: str | None = None


_IMAGE_WAIT_JS: Final[str] = """async () => {
    // Walk every <img> across the page AND every shadow root (each
    // widget lives in its own shadow tree), then resolve once they've
    // all either loaded or errored. Without this, widgets that fetch
    // remote images via a normal <img src> tag (HA camera snapshots,
    // Spotify album art, Unsplash pictures) often hadn't finished
    // their download by the time the composer signals "mounted",
    // and the screenshot captured an empty / broken-image frame.
    // Capped at 5 s so a single hung CDN doesn't block the whole
    // render, we ship the best frame we have.
    function* allImages(root) {
        for (const img of root.querySelectorAll('img')) yield img;
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) yield* allImages(el.shadowRoot);
        }
    }
    const pending = [];
    for (const img of allImages(document)) {
        if (img.complete && img.naturalWidth > 0) continue;
        pending.push(new Promise((resolve) => {
            const done = () => resolve();
            img.addEventListener('load', done, { once: true });
            img.addEventListener('error', done, { once: true });
        }));
    }
    if (!pending.length) return;
    await Promise.race([
        Promise.all(pending),
        new Promise((r) => setTimeout(r, 5000)),
    ]);
}"""


_FONT_WAIT_JS: Final[str] = """async () => {
    if (!document.fonts || !document.fonts.load) return;
    const families = new Set();
    // Take both the cell's inline font-family (the page/per-cell font
    // picker) AND the cell's cascaded --font-family (the active Spectra
    // style, which paints inside the shadow root). Reading the property
    // direct via getPropertyValue is unreliable for inherited custom
    // properties in some shadow-host edge cases (same gotcha that drove
    // the spectra-chart.js probe), so we probe via a real CSS property:
    // a hidden child with font-family: var(--font-family) inherits the
    // cell's cascade and getComputedStyle resolves the actual family.
    document.querySelectorAll('.cell').forEach((cell) => {
        const inline = getComputedStyle(cell).fontFamily;
        if (inline) {
            const first = inline.split(',')[0].trim()
                .replace(/^['\"]|['\"]$/g, '');
            if (first) families.add(first);
        }
        const probe = document.createElement('span');
        probe.style.cssText = 'position:absolute;visibility:hidden;width:0;height:0;font-family:var(--font-family)';
        cell.appendChild(probe);
        const cascaded = getComputedStyle(probe).fontFamily;
        probe.remove();
        if (cascaded) {
            const first = cascaded.split(',')[0].trim()
                .replace(/^['\"]|['\"]$/g, '');
            if (first) families.add(first);
        }
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
    """Retry shell around ``_screenshot_attempt``.

    Each attempt opens a fresh ``new_context`` + ``new_page``, so a
    half-loaded page from the previous timeout doesn't carry into the
    retry. Only Playwright ``TimeoutError`` triggers a retry (the
    typical mode: ``Page.goto: Timeout 15000ms exceeded``). Other
    Playwright errors (invalid URL, browser-side crash, etc.) surface
    immediately, retrying them just wastes the deadline."""
    last_err: PlaywrightTimeoutError | None = None
    for attempt in range(1, request.max_attempts + 1):
        try:
            return _screenshot_attempt(browser, request, attempt)
        except PlaywrightTimeoutError as err:
            last_err = err
            if attempt >= request.max_attempts:
                logger.error(
                    "render failed after %d attempts: %s (url=%s)",
                    attempt,
                    err,
                    request.url,
                )
                raise
            logger.warning(
                "render attempt %d/%d hit a timeout (will retry): %s (url=%s)",
                attempt,
                request.max_attempts,
                err,
                request.url,
            )
    # Unreachable, the loop either returns on success or raises on the
    # last attempt. The assert keeps mypy honest about the bound.
    assert last_err is not None
    raise last_err


def _screenshot_attempt(browser: Browser, request: RenderRequest, attempt: int) -> bytes:
    """A single render pass, opens a fresh context on ``browser``,
    navigates, screenshots, and disposes. Reused by both cold and warm
    paths so the ``networkidle`` timeout / font-load / pixel-exact viewport
    behaviour stays identical regardless of which path the caller took.

    ``attempt`` is the 1-indexed retry counter, surfaced in the phase log
    so a "render took 30s" investigation can spot when a goto failure
    burned the first attempt + 15s before the second one succeeded."""
    context_kwargs: dict[str, Any] = {
        "viewport": {"width": request.viewport_w, "height": request.viewport_h},
        "device_scale_factor": 1,
        "color_scheme": "light",
    }
    if request.timezone_id:
        context_kwargs["timezone_id"] = request.timezone_id
    context = browser.new_context(**context_kwargs)
    try:
        page = context.new_page()
        page.set_default_timeout(request.timeout_ms)
        # ``set_default_timeout`` covers actions (evaluate, click, …) but
        # NOT navigation, ``goto`` uses Playwright's 30s default unless
        # we override it. Without this, ``goto + fallback + evaluate +
        # screenshot`` can sum to ~75s, which races the BrowserPool's
        # outer 75s deadline and surfaces as an empty-message TimeoutError.
        page.set_default_navigation_timeout(request.timeout_ms)
        # Per-phase timing, surfaces in the add-on log so a future
        # "render took 70s" investigation can point at the specific
        # Playwright stage that timed out instead of guessing.
        t0 = time.monotonic()
        # ``load`` fires when the main document + critical subresources
        # have downloaded, fast and deterministic. ``networkidle`` was
        # the previous default for composer renders but routinely timed
        # out (widget client.js imports, font fetches, and Phosphor CSS
        # keep network busy long after the page is visually ready). When
        # goto's networkidle timed out, Playwright aborted the navigation
        # and the next ``page.evaluate`` stalled for ~60s waiting for
        # stability, which is how a normal render ballooned to 73s.
        #
        # External URLs (``is_composer=False``) are a different beast.
        # SPAs like reddit.com render only an empty shell at ``load`` and
        # don't paint real content until JS has hydrated, so screenshotting
        # at ``load`` captures a blank page. But picking ``networkidle``
        # for the goto itself hard-fails on ad-heavy news sites (Guardian,
        # arstechnica) where the network never idles. The compromise is
        # a hybrid: nav on ``load`` so we never hard-fail, then a brief
        # best-effort wait for ``networkidle`` so JS-driven SPAs get a
        # chance to settle. Sites whose networks won't idle hit the
        # ``wait_for_load_state`` timeout and we screenshot what we have.
        if not request.is_composer:
            # External URL: goto on "load" so ad-heavy sites don't hard-
            # fail at the navigation step, then a brief best-effort wait
            # for networkidle so SPAs get time to hydrate.
            page.goto(request.url, wait_until="load")
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except PlaywrightError:
                logger.debug("external url networkidle wait gave up", exc_info=True)
        elif request.wait_until == "networkidle":
            # Composer render: translate the dataclass default
            # ``networkidle`` to ``load`` because widget CSS/JS keep
            # network busy long after the page is visually ready.
            page.goto(request.url, wait_until="load")
        else:
            page.goto(request.url, wait_until=request.wait_until)
        t_goto = time.monotonic()
        # composer.js sets ``window.__tesseraeComposed = true`` after
        # every cell's mount() promise resolves. That's a far cleaner
        # "page is ready to screenshot" signal than networkidle. The
        # poll is best-effort: if it times out (a stuck widget mount)
        # we screenshot whatever's rendered rather than failing.
        #
        # Skipped entirely for external URLs: ``__tesseraeComposed`` is a
        # composer.js-only signal that has no meaning on reddit.com /
        # bbc.com / any other site, and waiting 15s for a flag that
        # never fires is the bulk of why Send → Webpage felt slow.
        if request.is_composer:
            try:
                page.wait_for_function(
                    "window.__tesseraeComposed === true",
                    timeout=request.timeout_ms,
                )
            except PlaywrightError as err:
                logger.warning("composer mount wait timed out: %s", err)
        t1 = time.monotonic()
        # Block on every <img> the page (including shadow roots) has
        # already issued a request for, capped at 5 s. The compose-
        # done signal only proves widget JS finished writing markup
        # , slow remote images (HA cameras, Spotify art, Unsplash
        # CDN) keep downloading after that, and the screenshot would
        # otherwise capture a half-loaded / broken-image frame.
        try:
            page.evaluate(_IMAGE_WAIT_JS)
        except PlaywrightError as err:
            logger.warning("image wait skipped: %s", err)
        t_img = time.monotonic()
        # Block screenshot until every cell's font is actually loaded.
        # ``document.fonts.ready`` only awaits fonts already pending;
        # ``document.fonts.load()`` triggers the request and waits.
        #
        # Best-effort: if the page is mid-navigation when we hit evaluate
        # ("Execution context was destroyed"), or the font CSS itself
        # raises, fall through to the screenshot anyway. Most cells have
        # already settled by this point; a missed font wait beats a
        # whole-render failure on what's a cosmetic refinement.
        try:
            page.evaluate(_FONT_WAIT_JS)
        except PlaywrightError as err:
            logger.warning("font wait skipped: %s", err)
        t2 = time.monotonic()
        png: bytes = page.screenshot(
            full_page=False,
            type="png",
            animations="disabled",
            omit_background=False,
        )
        t3 = time.monotonic()
        logger.info(
            "render phases (s): attempt=%d goto=%.2f compose=%.2f images=%.2f fonts=%.2f screenshot=%.2f (url=%s)",
            attempt,
            t_goto - t0,
            t1 - t_goto,
            t_img - t1,
            t2 - t_img,
            t3 - t2,
            request.url,
        )
        return png
    finally:
        # Closing the context tears down the page, cookies, localStorage,
        # font cache, so the next render starts clean even though the
        # browser itself stays alive on the warm path.
        try:
            context.close()
        except Exception:
            logger.debug("context close failed (continuing)", exc_info=True)


def _fetch_one(browser: Browser, request: FetchRequest) -> str:
    """One-shot fetch through a fresh incognito context's APIRequest
    pipeline. We don't navigate a page (Reddit serves RSS as XML, the
    browser would render a tree but the response body is the only thing
    we want), we just use the context's network stack to GET the URL.

    Going via Chromium gets us the browser's TLS/JA3 fingerprint and
    realistic HTTP/2 frame ordering, which is what upstreams like Reddit
    actually fingerprint on. ``urllib`` looks bot-shaped at that layer
    even with a Safari User-Agent.

    Raises on non-2xx so the caller can decide whether to fall back."""
    extra_headers: dict[str, str] = {}
    if request.accept:
        extra_headers["Accept"] = request.accept
    context_kwargs: dict[str, Any] = {}
    if request.user_agent:
        context_kwargs["user_agent"] = request.user_agent
    if extra_headers:
        context_kwargs["extra_http_headers"] = extra_headers
    context = browser.new_context(**context_kwargs)
    try:
        response = context.request.get(request.url, timeout=request.timeout_ms)
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status} {response.status_text}")
        return response.text()
    finally:
        try:
            context.close()
        except Exception:
            logger.debug("fetch context close failed (continuing)", exc_info=True)


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
    process is reused, which is where the ~1.5 s of cold-start lives.

    Crash recovery: if Chromium dies under us, the next render relaunches
    it. The worker thread itself survives until ``stop()`` is called."""

    _SENTINEL: Final = ()  # signal the worker to drain + exit

    def __init__(self) -> None:
        # Queue carries either a render task (RenderRequest, Future[bytes]),
        # a fetch task (FetchRequest, Future[str]), or the empty-tuple
        # sentinel that signals "drain + exit". The worker discriminates
        # by ``isinstance(request, FetchRequest)``.
        self._q: queue.Queue[
            tuple[RenderRequest, concurrent.futures.Future[bytes]]
            | tuple[FetchRequest, concurrent.futures.Future[str]]
            | tuple[()]
        ] = queue.Queue()
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
        # Lazy start on first request, the App settings toggle decides
        # whether the caller routes here at all, so the pool stays cold
        # (no Chromium spawned) if it's never asked.
        if self._thread is None:
            self.start()
        if self._stopped:
            raise RuntimeError("browser pool has been stopped")
        fut: concurrent.futures.Future[bytes] = concurrent.futures.Future()
        self._q.put((request, fut))
        # Allow the request's own timeout × max_attempts (so retries fit)
        # plus generous slack for launch + context setup; the pool isn't
        # meant to be a hard timeout layer.
        return fut.result(timeout=(request.timeout_ms * request.max_attempts) / 1000 + 60)

    def fetch_text(self, request: FetchRequest) -> str:
        """Fetch a URL through the pooled Chromium's network stack.
         Returns the response body as text. Each call spawns a fresh
         incognito context so cookies don't carry between widgets / sites
        , important for Reddit, which keys its rate-limit / challenge on
         the cookie jar."""
        if self._thread is None:
            self.start()
        if self._stopped:
            raise RuntimeError("browser pool has been stopped")
        fut: concurrent.futures.Future[str] = concurrent.futures.Future()
        self._q.put((request, fut))
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
                # Narrow the type, non-sentinel items are always
                # (request, future) tuples per the put() contract.
                request, fut = item  # type: ignore[misc]
                try:
                    if browser is None or not browser.is_connected():
                        if browser is not None:
                            with contextlib.suppress(Exception):
                                browser.close()
                        browser = pw.chromium.launch(**_chromium_launch_kwargs())
                    # mypy can narrow ``request`` here but the queue's
                    # union widens ``fut`` to ``Future[str] | Future[bytes]``;
                    # the isinstance check on the request half doesn't
                    # propagate. Casting the future on each branch is
                    # cheaper than restructuring the queue to a tagged
                    # union.
                    if isinstance(request, FetchRequest):
                        cast(concurrent.futures.Future[str], fut).set_result(
                            _fetch_one(browser, request)
                        )
                    else:
                        cast(concurrent.futures.Future[bytes], fut).set_result(
                            _screenshot_one(browser, request)
                        )
                except Exception as exc:
                    fut.set_exception(exc)
                    # Force a relaunch if the browser is actually dead, OR
                    # if Playwright reported "Execution context was
                    # destroyed", that error indicates the page-level
                    # state is corrupted in ways the next ``new_context``
                    # might not recover from. ``is_connected()`` returns
                    # True on a dead-but-still-attached Chromium, so we
                    # need both checks.
                    exc_str = str(exc).lower()
                    poisoned = (
                        "execution context was destroyed" in exc_str
                        or "target page, context or browser has been closed" in exc_str
                    )
                    if browser is not None and (not browser.is_connected() or poisoned):
                        with contextlib.suppress(Exception):
                            browser.close()
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
