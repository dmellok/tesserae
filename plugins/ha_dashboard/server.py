"""ha_dashboard, an existing Home Assistant dashboard captured as a frame.

The recurring ask is "show the dashboard I already built in Home Assistant
on the panel, without rebuilding it as Tesserae widgets". Two things stop
the ``webpage`` widget from doing that: Home Assistant sends
``X-Frame-Options: SAMEORIGIN``, so the composer's iframe is refused, and
the frontend signs in from tokens in ``localStorage``, so a bearer header
on the navigation does nothing.

This widget goes around both. The server opens the dashboard as a
top-level page in headless Chromium (no iframe, so the frame header never
applies), and an init script seeds ``localStorage`` with a session built
from the same long-lived token ``ha_core`` already holds, plus the
frontend's own "sidebar always hidden" preference. The 56 px app header is
rendered off the top of the viewport and cropped away, the same approach
the Puppet add-on takes, which is the closest thing to a reference for how
the frontend expects to be driven.

Timing is the constraint that shapes the rest. A widget ``fetch()`` runs
inside the composer's 6 s hydrate budget and a cold Chromium plus a
dashboard load does not fit, so the render runs on its own thread and the
result is cached per (path, size, zoom, fit, theme, light or dark). ``fetch()`` returns the
cached frame when it is younger than ``refresh_seconds``, otherwise it
starts a render and returns the previous frame while that runs. Only the
very first render for a cell is waited for, and only up to ``FIRST_WAIT_S``,
so a panel's first paint never stalls on it. A cold render rarely fits in
that window, so the first fetch for a cell size usually goes out as
``pending`` and the cell shows a placeholder. That result carries a
``next_change_at`` hint (#243) so the device path re-renders the page once
the frame has landed, instead of leaving the placeholder on glass until
the next Send. Renders are single-flight per key and serialised across keys, so a
page with three dashboard cells opens one browser at a time. The pooled
browser is not used on purpose: its worker is the thread rendering the
composer page this fetch is part of, so queuing on it would deadlock.

The token reaches Chromium only through the init script on the render
request; it is never placed in the data returned to the cell, in the
cache, or in a log line.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from flask import current_app

logger = logging.getLogger(__name__)

# Height of the Home Assistant app header at zoom 1. Puppet uses the same
# constant; the frontend has kept it since the Material redesign.
HEADER_PX = 56
# How long a first-ever render for a cell may hold the fetch. Under the
# composer's 6 s per-widget budget with room for the return trip.
FIRST_WAIT_S = 5.0
# After a failed render, how long the cell shows the error before a fetch
# tries again. Stops an editor preview that polls every few seconds from
# relaunching Chromium against a dead host each time.
ERROR_RETRY_S = 60.0
# Playwright budget for one render: launch + navigation + ready wait fit
# comfortably; a dashboard that takes longer than this is not going to
# paint.
RENDER_TIMEOUT_MS = 25_000
# How far out a ``pending`` result declares its next change. A cold render
# (launch + navigation + ready wait + settle) lands well inside this, and the
# device path adds its own render margin before it polls again; a render
# that overruns simply declares once more on the re-render.
PENDING_RETRY_S = 30.0
READY_TIMEOUT_MS = 10_000
DEFAULT_PATH = "lovelace/0"
DEFAULT_REFRESH_S = 300
DEFAULT_SETTLE_S = 2.0
THREAD_NAME = "ha-dashboard-render"
# Home Assistant puts no limit on a theme name; this only stops a pasted
# essay from ending up in the cache key and the init script.
THEME_MAX_LEN = 100

# Polled after networkidle until the frontend reports its panel resolved
# and loaded. Lifted from Puppet's wait, which tracks the frontend's own
# ``_loading`` flags rather than any element that might restyle. A landing
# on the login page counts as ready too, so a bad token is reported at
# once rather than after the wait times out.
READY_JS = """() => {
  if (location.pathname.startsWith("/auth/")) return true;
  const haEl = document.querySelector("home-assistant");
  if (!haEl) return false;
  const mainEl = haEl.shadowRoot?.querySelector("home-assistant-main");
  if (!mainEl) return false;
  const resolver = mainEl.shadowRoot?.querySelector("partial-panel-resolver");
  if (!resolver || resolver._loading) return false;
  const panel = resolver.children[0];
  if (!panel) return false;
  return !("_loading" in panel) || !panel._loading;
}"""

# Fit to cell. Appended to the ready wait rather than run afterwards because
# the renderer screenshots straight after it: the zoom has to be settled by
# then. The renderer polls the function once a frame, so each call does one
# step and keeps its place on ``window.__tesseraeFit``.
#
# Zooming is not a plain scale. A sections view lays its columns out from the
# width it is given, and a smaller zoom gives it more, so the height does not
# follow the zoom in proportion. The search therefore measures: the largest
# zoom first (the common case, where everything already fits and nothing
# changes), then the smallest, then a bisection between them. Each
# measurement waits for the height to hold still, since cards arrive and
# reflow on their own schedule.
#
# The height is the bottom of ``hui-view``, which is as tall as its cards.
# The document is never shorter than the window, so it would call a short
# dashboard too tall at every zoom below the largest; it only stands in when
# a frontend has no such element.
FIT_MIN_ZOOM = 0.25
FIT_FIRST_STABLE_MS = 800
FIT_STEP_STABLE_MS = 350
FIT_MAX_STEPS = 8
FIT_READY_TIMEOUT_MS = 16_000
FIT_JS = """
  const fit = __FIT__;
  const st = (window.__tesseraeFit ||= {
    z: fit.max, lo: null, hi: null, steps: 0, lastH: -1, since: 0, done: false,
  });
  if (st.done) return true;
  const view = panel.shadowRoot?.querySelector("hui-root")?.shadowRoot?.querySelector("hui-view");
  const height = Math.ceil(
    view
      ? view.getBoundingClientRect().bottom + window.scrollY
      : document.documentElement.scrollHeight
  );
  const now = performance.now();
  if (height !== st.lastH) { st.lastH = height; st.since = now; return false; }
  if (now - st.since < (st.steps ? fit.stepMs : fit.firstMs)) return false;
  const fits = height <= fit.height + fit.header * st.z + 1;
  const apply = (z) => {
    st.z = z; st.steps += 1; st.lastH = -1;
    document.body.style.zoom = String(z);
  };
  if (st.lo === null && st.hi === null) {
    if (fits) { st.done = true; return true; }
    st.hi = st.z; apply(fit.min); return false;
  }
  if (st.lo === null) {
    if (!fits) { st.done = true; return true; }
    st.lo = st.z;
  } else if (st.z !== st.lo) {
    if (fits) st.lo = st.z; else st.hi = st.z;
  } else { st.done = true; return true; }
  if (st.steps >= fit.maxSteps || st.hi - st.lo < 0.02) { apply(st.lo); return false; }
  apply(Math.round(((st.lo + st.hi) / 2) * 1000) / 1000);
  return false;
}"""
# What the capture reads back: where the page ended up, and the zoom the fit
# left on it (null when fit is off), which sets how tall the header is.
RESULT_JS = "({href: location.href, zoom: window.__tesseraeFit ? window.__tesseraeFit.z : null})"


# Runs inside the init script, before any page script. Rewrites the key on
# the frontend's per-user theme messages (get, subscribe and, should one ever
# be sent, set) so the capture neither follows nor touches the theme the
# token's owner picked for themselves. Outgoing frames can be one message or
# a coalesced array of them. See ``init_script``.
THEME_USER_DATA_KEY = "tesserae_capture_theme"
THEME_REDIRECT_JS = """  const nativeSend = WebSocket.prototype.send;
  WebSocket.prototype.send = function (data) {
    if (typeof data === "string" && data.includes("_user_data")) {
      try {
        const parsed = JSON.parse(data);
        let changed = false;
        for (const msg of Array.isArray(parsed) ? parsed : [parsed]) {
          if (
            msg &&
            /^frontend\\/(get|subscribe|set)_user_data$/.test(String(msg.type)) &&
            msg.key === "theme"
          ) {
            msg.key = "__KEY__";
            changed = true;
          }
        }
        if (changed) data = JSON.stringify(parsed);
      } catch (err) {}
    }
    return nativeSend.call(this, data);
  };""".replace("__KEY__", THEME_USER_DATA_KEY)


class Params(NamedTuple):
    """Everything that changes what gets captured; doubles as the cache key.

    NamedTuple rather than dataclass so the module also loads standalone
    (the test suite executes it outside ``sys.modules``, where a dataclass
    with postponed annotations cannot resolve its own module)."""

    path: str
    w: int
    h: int
    zoom: float
    dark: bool
    kiosk: bool
    settle_s: float
    theme: str = ""
    fit: bool = False
    # The dashboard already hides Home Assistant's header (kiosk-mode, a theme):
    # nothing to crop, or the crop would cut the top of the dashboard off.
    header_hidden: bool = False

    def header_at(self, zoom: float) -> int:
        return 0 if self.header_hidden else round(HEADER_PX * zoom)

    @property
    def header_px(self) -> int:
        return self.header_at(self.zoom)


class Frame(NamedTuple):
    png: bytes
    rendered_at: float
    w: int
    h: int


class _Job:
    """One in-flight render, so concurrent fetches for the same key share it."""

    __slots__ = ("done",)

    def __init__(self) -> None:
        self.done = threading.Event()


_frames: dict[Params, Frame] = {}
_errors: dict[Params, tuple[float, str]] = {}
_jobs: dict[Params, _Job] = {}
_state_lock = threading.Lock()
# One Chromium at a time across every key: a page with several dashboard
# cells should not launch several browsers at once on a small host.
_render_lock = threading.Lock()


def _reset() -> None:
    """Test hook: forget every frame, error, and in-flight job."""
    with _state_lock:
        _frames.clear()
        _errors.clear()
        _jobs.clear()


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


# -- option shaping ---------------------------------------------------------


def choices(name: str) -> list[dict[str, str]]:
    """Theme picker for the editor: the themes Home Assistant has installed."""
    core = _core()
    if name == "theme" and core is not None:
        return list(core.theme_choices())
    return []


def normalise_path(raw: Any) -> str:
    """Turn what the operator typed into a site-relative path.

    Accepts ``lovelace/0``, ``/lovelace/0``, a pasted full URL on the
    configured host (the host part is dropped), or blank for the default
    dashboard. A query string the operator included is kept."""
    text = str(raw or "").strip()
    if not text:
        return "/" + DEFAULT_PATH
    if text.startswith(("http://", "https://")):
        parts = urlsplit(text)
        text = parts.path or "/"
        if parts.query:
            text += "?" + parts.query
    if not text.startswith("/"):
        text = "/" + text
    return text


def normalise_theme(raw: Any) -> str:
    """The chosen theme name, trimmed. Blank, or Home Assistant's own name
    for its built-in theme, means "whatever the frontend defaults to", so
    those two spellings share one cache entry."""
    text = str(raw or "").strip()[:THEME_MAX_LEN]
    return "" if text.lower() == "default" else text


def _float_option(
    options: dict[str, Any], name: str, default: float, lo: float, hi: float
) -> float:
    try:
        value = float(options.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, lo), hi)


def _params(options: dict[str, Any], ctx: dict[str, Any]) -> Params:
    w = int(ctx.get("cell_w") or 0) or int(ctx.get("panel_w") or 0) or 800
    h = int(ctx.get("cell_h") or 0) or int(ctx.get("panel_h") or 0) or 480
    return Params(
        path=normalise_path(options.get("path")),
        w=max(w, 1),
        h=max(h, 1),
        zoom=_float_option(options, "zoom", 1.0, 0.5, 3.0),
        dark=options.get("dark_mode") is True,
        kiosk=options.get("kiosk", True) is not False,
        settle_s=_float_option(options, "settle_seconds", DEFAULT_SETTLE_S, 0.0, 12.0),
        theme=normalise_theme(options.get("theme")),
        fit=options.get("fit") is True,
        header_hidden=options.get("header_hidden") is True,
    )


def _refresh_s(options: dict[str, Any]) -> float:
    return _float_option(options, "refresh_seconds", DEFAULT_REFRESH_S, 30.0, 86400.0)


# -- the render -------------------------------------------------------------


def init_script(base_url: str, token: str, params: Params) -> str:
    """JavaScript the renderer runs before any page script.

    ``hassTokens`` is the shape the frontend's auth module reads back from
    ``localStorage``: a long-lived token as the access token, an expiry far
    enough out that it never tries to refresh, and ``clientId`` set to the
    site root, which is what the frontend compares against ``hassUrl``.
    ``dockedSidebar: always_hidden`` is the user preference that keeps the
    sidebar closed; ``selectedTheme`` pins light or dark so the capture does
    not depend on the browser's colour scheme, and names the theme when the
    cell asks for one. That is the same record the profile page's theme
    picker writes, so it overrides the theme for this capture only and
    leaves what people see in their own browsers alone. A name Home
    Assistant does not have falls back to its default theme.

    Newer frontends also keep the theme in the signed-in user's server-side
    data (``frontend/subscribe_user_data``, key ``theme``), and a value there
    replaces the ``localStorage`` one as soon as the socket connects. The
    capture signs in as the token's owner, so that person's own theme would
    win over the cell's every time. ``THEME_REDIRECT_JS`` points those
    messages at a key nothing writes: the server answers "no value", the
    frontend keeps the seeded record, and the owner's saved theme is never
    read or changed. Frontends from before the sync never send the message.

    The zoom is applied on ``<body>`` once it exists, matching Puppet, so
    the header stays ``HEADER_PX * zoom`` tall and the crop below lines up."""
    root = base_url.rstrip("/") + "/"
    tokens = {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 1800,
        "hassUrl": base_url.rstrip("/"),
        "clientId": root,
        "expires": 9999999999999,
        "refresh_token": "",
    }
    selected_theme: dict[str, Any] = {"dark": params.dark}
    if params.theme:
        selected_theme["theme"] = params.theme
    entries = {
        "hassTokens": json.dumps(tokens),
        "dockedSidebar": json.dumps("always_hidden"),
        "selectedTheme": json.dumps(selected_theme),
    }
    return (
        "(() => {\n"
        f"  const entries = {json.dumps(entries)};\n"
        "  for (const [key, value] of Object.entries(entries)) {\n"
        "    try { localStorage.setItem(key, value); } catch (err) {}\n"
        "  }\n"
        f"{THEME_REDIRECT_JS}\n"
        f"  const zoom = {json.dumps(params.zoom)};\n"
        "  const applyZoom = () => { if (document.body) document.body.style.zoom = String(zoom); };\n"
        "  if (document.readyState === 'loading') {\n"
        "    document.addEventListener('DOMContentLoaded', applyZoom, { once: true });\n"
        "  } else { applyZoom(); }\n"
        "})();"
    )


def dashboard_url(base_url: str, params: Params) -> str:
    url = base_url.rstrip("/") + params.path
    if params.kiosk:
        url += "&kiosk" if "?" in url else "?kiosk"
    return url


def ready_script(params: Params) -> str:
    """The function the renderer polls before it screenshots: the frontend's
    own "loaded" check, followed by the fit search when the cell asks for it."""
    if not params.fit:
        return READY_JS
    config = {
        "max": params.zoom,
        "min": min(FIT_MIN_ZOOM, params.zoom),
        "height": params.h,
        "header": 0 if params.header_hidden else HEADER_PX,
        "firstMs": FIT_FIRST_STABLE_MS,
        "stepMs": FIT_STEP_STABLE_MS,
        "maxSteps": FIT_MAX_STEPS,
    }
    head, tail = READY_JS.rsplit('  return !("_loading" in panel) || !panel._loading;\n}', 1)
    assert not tail
    return (
        head
        + '  if ("_loading" in panel && panel._loading) return false;'
        + FIT_JS.replace("__FIT__", json.dumps(config))
    )


def crop_header(png: bytes, params: Params, zoom: float | None = None) -> bytes:
    """Drop the app header from the top of the capture and return a PNG of
    exactly ``params.w × params.h``. ``zoom`` is the zoom the page was left
    at when a fit changed it; the header shrinks with it."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as im:
        top = params.header_px if zoom is None else params.header_at(zoom)
        frame = im.convert("RGB").crop((0, top, params.w, top + params.h))
        out = io.BytesIO()
        frame.save(out, format="PNG", optimize=True)
        return out.getvalue()


def _capture(request: Any, script: str) -> tuple[bytes, Any]:
    """Indirection so tests can swap the browser out."""
    from app.renderer import CaptureRequest, capture_composed

    return capture_composed(CaptureRequest(render=request, script=script))


def _timezone_id() -> str | None:
    try:
        from app.push import resolve_render_timezone_id

        return resolve_render_timezone_id(current_app.config["SETTINGS_STORE"])
    except Exception:
        return None


def render_frame(
    params: Params,
    *,
    base_url: str,
    token: str,
    verify_tls: bool = True,
    timezone_id: str | None = None,
) -> bytes:
    """Open the dashboard, capture it, crop the header. Raises on a login
    redirect (bad token) or a renderer failure; callers decide what the
    cell shows."""
    from app.renderer import RenderRequest

    request = RenderRequest(
        url=dashboard_url(base_url, params),
        viewport_w=params.w,
        viewport_h=params.h + params.header_px,
        timeout_ms=RENDER_TIMEOUT_MS,
        max_attempts=1,
        is_composer=False,
        timezone_id=timezone_id,
        init_script=init_script(base_url, token, params),
        ready_js=ready_script(params),
        ready_timeout_ms=FIT_READY_TIMEOUT_MS if params.fit else READY_TIMEOUT_MS,
        settle_ms=int(params.settle_s * 1000),
        ignore_https_errors=not verify_tls,
    )
    png, result = _capture(request, RESULT_JS)
    href, zoom = "", None
    if isinstance(result, dict):
        href = result.get("href") or ""
        fitted = result.get("zoom")
        if params.fit and isinstance(fitted, (int, float)) and 0 < fitted <= params.zoom:
            zoom = float(fitted)
    landed = urlsplit(str(href or "")).path
    if landed.startswith("/auth/"):
        raise RuntimeError(
            "Home Assistant showed the login page instead of the dashboard; "
            "check the access token in Settings → Widgets → Home Assistant Core"
        )
    return crop_header(png, params, zoom)


def _run_job(params: Params, job: _Job, render_kwargs: dict[str, Any]) -> None:
    try:
        with _render_lock:
            png = render_frame(params, **render_kwargs)
    except Exception as err:
        logger.warning("ha_dashboard render of %s failed: %s", params.path, err)
        with _state_lock:
            _errors[params] = (time.monotonic(), f"{type(err).__name__}: {err}")
    else:
        with _state_lock:
            _frames[params] = Frame(png=png, rendered_at=time.time(), w=params.w, h=params.h)
            _errors.pop(params, None)
    finally:
        with _state_lock:
            _jobs.pop(params, None)
        job.done.set()


def _start_render(params: Params, render_kwargs: dict[str, Any]) -> _Job:
    """Start a render for ``params`` unless one is already running; either
    way return the job to wait on."""
    with _state_lock:
        job = _jobs.get(params)
        if job is not None:
            return job
        job = _Job()
        _jobs[params] = job
    thread = threading.Thread(
        target=_run_job, args=(params, job, render_kwargs), name=THREAD_NAME, daemon=True
    )
    thread.start()
    return job


# -- widget entry point -----------------------------------------------------


def _shape(frame: Frame, params: Params, *, stale: bool) -> dict[str, Any]:
    return {
        "frame": "data:image/png;base64," + base64.b64encode(frame.png).decode("ascii"),
        "w": frame.w,
        "h": frame.h,
        "path": params.path,
        "rendered_at": frame.rendered_at,
        "stale": stale,
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}
    if not core.is_configured():
        return {
            "error": "Set the Home Assistant URL and token in Settings → Widgets → Home Assistant Core."
        }

    params = _params(options, ctx)
    refresh_s = _refresh_s(options)
    now = time.time()

    with _state_lock:
        frame = _frames.get(params)
        error = _errors.get(params)
    fresh = frame is not None and (now - frame.rendered_at) < refresh_s and not ctx.get("fresh")
    if fresh and frame is not None:
        return _shape(frame, params, stale=False)

    if error is not None and frame is None and not ctx.get("fresh"):
        age = time.monotonic() - error[0]
        if age < ERROR_RETRY_S:
            return {"error": error[1], "path": params.path}

    render_kwargs = {
        "base_url": core.base_url(),
        "token": core.token(),
        "verify_tls": _verify_tls(core),
        "timezone_id": _timezone_id(),
    }
    job = _start_render(params, render_kwargs)
    if frame is not None:
        # Stale-while-revalidate: the previous frame goes out now, and the
        # page is asked for again once the new one has had time to land, as a
        # pending first render is. Otherwise an old capture (a display back
        # from days of pause) stayed on glass until something else redrew it.
        return {
            **_shape(frame, params, stale=True),
            "next_change_at": time.time() + PENDING_RETRY_S,
        }

    job.done.wait(FIRST_WAIT_S)
    with _state_lock:
        frame = _frames.get(params)
        error = _errors.get(params)
    if frame is not None:
        return _shape(frame, params, stale=False)
    if error is not None:
        return {"error": error[1], "path": params.path}
    return {
        "pending": True,
        "path": params.path,
        "next_change_at": time.time() + PENDING_RETRY_S,
    }


def _verify_tls(core: Any) -> bool:
    checker = getattr(core, "_verify_tls", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return True
    return True
