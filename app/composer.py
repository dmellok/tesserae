"""Composer: builds the page that Playwright screenshots and the editor previews.

Reads pages from the file-backed ``PageStore``, resolves font references through
the plugin registry, emits ``@font-face`` rules for every loaded font, and
renders one ``<div class="cell">`` per cell.

For plugins that ship a ``server.py``, the composer calls ``fetch()`` and
embeds the result as ``data-data`` on the cell so client.js receives it via
``ctx.data``.

The theme system was stripped in v0.17 to clear the deck for a redesign; cells
no longer carry palette / --theme-* / --c-* tokens.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, render_template, request

from app.bindings import apply_binding
from app.panel import PANEL_PRESETS, resolve_panel_for_page
from app.plugin_http import fetch_json
from app.plugin_loader import Font, PluginRegistry
from app.state.page_store import Page, PageStore

logger = logging.getLogger(__name__)

bp = Blueprint("composer", __name__)


# Sizes used by the /_test/render scaffolding so a single widget can be
# rendered into a known cell without a saved Page.
SIZE_DIMENSIONS: dict[str, tuple[int, int]] = {
    "xs": (180, 180),
    "sm": (380, 240),
    "md": (640, 400),
    "lg": (1200, 800),
}

# Bounds for an explicit ``w,h`` screenshot request (Screenshot Contract). Wide
# enough for any real panel, tight enough that a typo can't ask the browser for
# a runaway viewport.
SCREENSHOT_MIN_DIM = 16
SCREENSHOT_MAX_DIM = 4096


def clamp_screenshot_dim(value: int) -> int:
    """Clamp an explicit screenshot dimension to ``[MIN, MAX]``."""
    return max(SCREENSHOT_MIN_DIM, min(SCREENSHOT_MAX_DIM, value))


def _registry() -> PluginRegistry:
    registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    return registry


def _app_location_dict() -> dict[str, Any] | None:
    """Return the app-level ``{latitude, longitude, name}`` dict from
    ``settings.app.location`` (v0.69.6, issue #52 items 5 + 6), or a
    migrated dict built from the legacy flat ``latitude`` / ``longitude``
    pair when the picker hasn't been touched yet. Returns ``None`` when
    neither is set.

    The migration lets a pre-v0.69.6 install upgrade cleanly: users with
    a flat lat/lon still get their weather widgets served, without
    forcing them to re-pick their location on first launch after the
    upgrade. Once the user opens Settings and re-saves via the picker,
    the ``location`` key wins and the flat fields become inert.
    """
    store = current_app.config.get("SETTINGS_STORE")
    if store is None:
        return None
    app_section = store.get_section("app") or {}

    picked = app_section.get("location")
    if isinstance(picked, dict) and picked:
        lat = picked.get("latitude")
        lon = picked.get("longitude")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return {
                "latitude": float(lat),
                "longitude": float(lon),
                "name": str(picked.get("name") or ""),
            }

    # Legacy flat fields. Explicit isinstance narrowing (``int | float |
    # str`` is what ``float()`` accepts) so mypy can see the type is
    # safe. The subsequent try/except still catches malformed strings
    # (e.g. someone hand-edited ``settings.json`` with junk in the lat
    # field).
    lat_raw = app_section.get("latitude")
    lon_raw = app_section.get("longitude")
    if not isinstance(lat_raw, (int, float, str)) or not isinstance(lon_raw, (int, float, str)):
        return None
    if lat_raw == "" or lon_raw == "":
        return None
    try:
        lat_f = float(lat_raw)
        lon_f = float(lon_raw)
    except (TypeError, ValueError):
        return None
    return {"latitude": lat_f, "longitude": lon_f, "name": ""}


# Process-lifetime geocode cache. Keyed on the lowercased query string.
# A resolved dict is cached on success; the sentinel ``False`` is cached
# on failure so an unresolvable place doesn't re-hit the API on every
# render tick. Cleared on process restart, which is fine.
_GEOCODE_CACHE: dict[str, Any] = {}
_GEOCODE_TIMEOUT_S: Final[float] = 5.0


def _parse_lat_lon(text: str) -> dict[str, Any] | None:
    """Parse a literal ``"lat,lon"`` pair (``"-37.65, 145.09"``) into a
    location dict without touching the network. Returns ``None`` when the
    string isn't two in-range numbers."""
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0].strip())
        lon = float(parts[1].strip())
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return {"latitude": lat, "longitude": lon, "name": ""}


def _geocode(query: str) -> dict[str, Any] | None:
    """Resolve free-text into ``{latitude, longitude, name}``.

    Tolerant of three canonical shapes: a bare place name
    (``"South Morang"``), a ``"City, CC"`` form (``"Paris, FR"``), or a
    literal ``"lat,lon"`` pair (``"-37.65,145.09"``). The ``lat,lon``
    form is parsed locally; a name is resolved through the Open-Meteo
    geocoding API (no key required), trying the full string first and
    then the city segment so ``"Paris, FR"`` still resolves. Results
    cache in-process so repeated renders of the same dashboard don't
    re-hit the API. Returns ``None`` for an empty or unresolvable query
    so the caller can surface a real error instead of guessing."""
    q = query.strip()
    if not q:
        return None
    key = q.lower()
    if key in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[key] or None

    coords = _parse_lat_lon(q)
    if coords is not None:
        _GEOCODE_CACHE[key] = coords
        return coords

    # Name search. "Paris, FR" won't match the API's bare-city name field,
    # so fall back to the segment before the comma.
    candidates = [q]
    if "," in q:
        head = q.split(",", 1)[0].strip()
        if head and head != q:
            candidates.append(head)
    for cand in candidates:
        try:
            payload = fetch_json(
                "https://geocoding-api.open-meteo.com/v1/search"
                f"?name={quote(cand)}&count=1&language=en&format=json",
                timeout=_GEOCODE_TIMEOUT_S,
                retries=0,
            )
        except Exception:
            continue
        results = payload.get("results") if isinstance(payload, dict) else None
        if not (isinstance(results, list) and results and isinstance(results[0], dict)):
            continue
        top = results[0]
        lat = top.get("latitude")
        lon = top.get("longitude")
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
            continue
        resolved = {
            "latitude": float(lat),
            "longitude": float(lon),
            "name": str(top.get("name") or q),
        }
        _GEOCODE_CACHE[key] = resolved
        return resolved

    _GEOCODE_CACHE[key] = False  # negative cache
    return None


def _location_configured(raw: dict[str, Any] | None) -> bool:
    """True when an element carries an explicit location (a non-empty
    ``location`` dict or free-text string). The weather widgets fall back
    to a demo sample only when NO location is set; a set-but-unresolvable
    location surfaces its own error rather than silently rendering the
    sample city."""
    if not isinstance(raw, dict):
        return False
    loc = raw.get("location")
    if isinstance(loc, str):
        return bool(loc.strip())
    return isinstance(loc, dict) and bool(loc)


def _touch_attr(cell_value: Any, manifest_value: Any) -> str:
    """Serialise a touch action declaration into its ``data-on-*``
    attribute string (issue #49). The cell-level value wins over the
    widget-manifest default; strings pass through (the flat action
    grammar), dicts serialise to JSON (the structured / swipe forms).
    Empty string means "emit no attribute"."""
    value = cell_value if cell_value not in (None, "") else manifest_value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict) and value:
        return json.dumps(value)
    return ""


def _stamp_touch(item: dict[str, Any], e: Any) -> None:
    """Attach a canvas element's touch attributes (issue #49) to its
    template dict. Config-authored actions (editor / MCP fields) are the
    trusted origin the dispatch gate honours for side-effecting actions;
    the compose template marks them ``data-touch-origin="config"``. Code
    elements also carry their named ``actions`` map so ``@name``
    references inside their markup resolve at extraction time."""
    item["on_tap_attr"] = _touch_attr(getattr(e, "on_tap", None), None)
    item["on_swipe_attr"] = _touch_attr(getattr(e, "on_swipe", None), None)
    item["on_slide_attr"] = _touch_attr(getattr(e, "on_slide", None), None)
    actions = getattr(e, "actions", None) or {}
    item["touch_actions_json"] = json.dumps(actions) if actions else ""


def _resolved_options(plugin_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    plugin = _registry().get(plugin_id)
    if plugin is None:
        return dict(raw)
    merged: dict[str, Any] = plugin.cell_option_defaults()
    merged.update(raw)
    # Promote a ``location_search`` dict into the top-level options the
    # widget server.py reads (``latitude``, ``longitude``, ``label``).
    # The user-facing UX is a single search field + an editable label;
    # the dict is the source of truth and these promoted fields are
    # what the existing widget data-fetch code consumes, so no widget
    # code change is needed for the simpler shape.
    #
    # v0.69.6 (issue #52 items 5 + 6): the app-level Settings → Location
    # picker is the fallback for a cell that hasn't picked its own
    # location. The old objection (a half-configured cell silently
    # showing weather for somewhere else) doesn't apply when the app-
    # level location is itself an explicit ``location_search`` pick,
    # not two separate number fields someone could half-fill. If the
    # cell has no ``location`` dict of its own, we splice the app-level
    # one in here so the promote-to-flat step below still fills
    # ``latitude`` / ``longitude`` on the widget's options.
    # A ``location`` may arrive as the search-field dict, or as a bare
    # string (an MCP agent, or a hand-authored / imported doc, setting
    # ``location: "South Morang"`` or ``"-37.65,145.09"``). Geocode the
    # string here so both the preview and the push resolve it identically
    # through this one code path. An explicit-but-unresolvable location
    # does NOT fall back to the app-level location: that would resurrect
    # the "shows Melbourne" bug. We leave coords unset and carry the query
    # as the label so the widget surfaces an error for the intended place.
    location = merged.get("location")
    explicit_unresolved = False
    if isinstance(location, str) and location.strip():
        query = location.strip()
        resolved = _geocode(query)
        if resolved is not None:
            location = resolved
        else:
            # ``label`` pre-exists (empty) from the plugin defaults, so
            # setdefault won't take: fill it only when the user hasn't
            # typed a custom label.
            if not merged.get("label"):
                merged["label"] = query
            explicit_unresolved = True
            location = None
    if not (isinstance(location, dict) and location) and not explicit_unresolved:
        location = _app_location_dict()
    if isinstance(location, dict) and location:
        lat = location.get("latitude")
        lon = location.get("longitude")
        loc_name = location.get("name")
        if isinstance(lat, (int, float)):
            merged["latitude"] = float(lat)
        if isinstance(lon, (int, float)):
            merged["longitude"] = float(lon)
        # ``label`` defaults to the city name when the user hasn't typed
        # anything custom. JS in the cell editor mirrors this by auto-
        # filling the Label input on location select, the server-side
        # fallback handles the case where the cell was created via the
        # API (or restored from a backup) without the editor running.
        if isinstance(loc_name, str) and loc_name and not merged.get("label"):
            merged["label"] = loc_name
    return merged


def _resolve_font(font_id: str | None, registry: PluginRegistry) -> Font | None:
    if font_id:
        font = registry.get_font(font_id)
        if font is not None:
            return font
    return registry.get_font("default")


# Self-contained (data: URL) @font-face CSS per font, cached. Built from the
# font plugin's woff2 on disk. Used by the code element sandbox, which has no
# network and a ``font-src data:`` CSP, so file-URL @font-face won't load there.
_FONT_FACE_DATAURI_CACHE: dict[str, str] = {}


@bp.get("/fonts/face/<font_id>.css")
def font_face_datauri(font_id: str) -> Any:
    """Self-contained @font-face CSS (woff2 embedded as ``data:`` URLs) for one
    font id, so a code element can use it by family name inside its sandbox.
    Cached; the file list comes from the plugin manifest (not user input)."""
    css = _FONT_FACE_DATAURI_CACHE.get(font_id)
    if css is None:
        reg = current_app.config["PLUGIN_REGISTRY"]
        font = reg.fonts.get(font_id)
        plugin = reg.get(font.plugin_id) if font is not None else None
        if font is None or plugin is None:
            abort(404)
        entry = next((f for f in plugin.manifest.get("fonts", []) if f.get("id") == font_id), None)
        if entry is None:
            abort(404)
        rules: list[str] = []
        for weight, rel in sorted(entry.get("files", {}).items()):
            try:
                data = (plugin.path / rel).read_bytes()
            except OSError:
                continue
            b64 = base64.b64encode(data).decode("ascii")
            rules.append(
                f"@font-face {{ font-family: '{font.name}'; font-weight: {weight}; "
                f"src: url(data:font/woff2;base64,{b64}) format('woff2'); font-display: block; }}"
            )
        css = "\n".join(rules)
        _FONT_FACE_DATAURI_CACHE[font_id] = css
    return current_app.response_class(css, mimetype="text/css")


def _font_face_css(fonts: dict[str, Font]) -> str:
    """Emit @font-face rules for every loaded font + weight."""
    rules: list[str] = []
    for font in fonts.values():
        for weight, url in font.files.items():
            rules.append(
                "@font-face { "
                f"font-family: '{font.name}'; "
                f"font-weight: {weight}; "
                f"src: url('{url}') format('woff2'); "
                "font-display: block; }"
            )
    return "\n".join(rules)


# Hydration-time hard caps. These bound the page-render budget against
# misbehaving widgets, a single hung fetch shouldn't sink the dashboard.
# Sized to fit inside the renderer's 15s page.goto budget: if hydration
# blows past goto's timeout, Playwright reports a broken navigation and
# the screenshot captures an empty page. Per-widget cap is the safety
# net; the overall cap is what actually fires when an upstream is dead.
# Per-widget cap is deliberately smaller than the overall cap so a
# widget with a 15s HTTP-level timeout can't push the total past the
# overall budget; the executor's shutdown below uses
# ``cancel_futures=True`` so stuck HTTP threads don't hold the
# composer up either.
_HYDRATE_PER_WIDGET_TIMEOUT_S: float = 6.0
_HYDRATE_OVERALL_TIMEOUT_S: float = 12.0
# Max concurrent in-flight widget fetches. Eight is enough for typical
# dashboards (~6 cells) without spawning a thread per cell on giant
# dashboards.
_HYDRATE_MAX_WORKERS: int = 8

# Process-lifetime "last good" cache. When a widget's server.py fetch()
# returns an error dict (or its fetch was cancelled by the hydration
# timeout), the composer falls back to the most recent successful
# result for the same (plugin_id, options, panel size) tuple. Without
# this, the first push of a dashboard with a slow upstream paints a
# "TimeoutError" into the cell; the second push (after the executor's
# straggler completes and writes the on-disk cache) is the workaround
# users found themselves doing manually. Now they don't have to -
# pushes after the first one show stale-but-real data instead of an
# error state. Cleared on process restart, which is fine for fresh
# installs (no fallback available either way).
_LAST_GOOD_DATA: dict[str, Any] = {}


def _last_good_key(plugin_id: str, options: dict[str, Any], panel_w: int, panel_h: int) -> str:
    """Stable key for ``_LAST_GOOD_DATA``. Same widget at the same panel
    dims with the same options resolves to the same key, so the fallback
    is on-target rather than serving a 1200×1600 result into a tall
    portrait cell."""
    opts = json.dumps(options, sort_keys=True, default=str)
    return f"{plugin_id}::{panel_w}x{panel_h}::{opts}"


def _parallel_fetch_plugin_data(
    cells_meta: list[dict[str, Any]],
    panel_w: int,
    panel_h: int,
    preview: bool,
    *,
    sample: bool = False,
    fresh: bool = False,
    target_device_id: str = "",
    page_name: str = "",
    page_icon: str = "",
) -> dict[int, Any]:
    """Run each cell's ``server.py`` fetch() in a worker thread.

    Returns ``{cell_index: data}``. Cells with no plugin or whose plugin
    has no fetch() function are absent from the result (the caller
    treats them as ``None``-data). Cells whose fetch raises or exceeds
    the per-widget timeout get a ``{"error": …}`` payload, matching the
    serial path's failure shape so widget templates keep rendering an
    error state instead of crashing the whole page.
    """
    import concurrent.futures

    indexed: list[tuple[int, str, dict[str, Any], int, int]] = []
    sample_results: dict[int, Any] = {}
    for idx, meta in enumerate(cells_meta):
        plugin_id = meta["plugin_id"]
        if not plugin_id:
            continue
        # Sample mode (dev gallery): short-circuit fetch with a hand-
        # written fixture so widgets that need backends (HA, Spotify)
        # still render in /_test/widgets. Widgets without a sample
        # fall through to their real fetch.
        if sample:
            from app.widget_samples import get_sample

            sample_data = get_sample(plugin_id)
            if sample_data is not None:
                sample_results[idx] = sample_data
                continue
        plugin = _registry().get(plugin_id)
        if plugin is None or plugin.server_module is None:
            continue
        if getattr(plugin.server_module, "fetch", None) is None:
            continue
        cell = meta.get("cell") or {}
        cell_w = int(cell.get("w") or 0)
        cell_h = int(cell.get("h") or 0)
        indexed.append((idx, plugin_id, meta["resolved_options"], cell_w, cell_h))

    if not indexed:
        return sample_results

    # Capture the live Flask app object so worker threads can push
    # ``app.app_context()`` themselves, ``current_app`` is a thread-
    # local proxy and won't follow us off the request thread.
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    def _worker(plugin_id: str, options: dict[str, Any], cell_w: int, cell_h: int) -> Any:
        with app.app_context():
            return _fetch_plugin_data(
                plugin_id,
                options,
                panel_w,
                panel_h,
                preview,
                cell_w=cell_w,
                cell_h=cell_h,
                fresh=fresh,
                target_device_id=target_device_id,
                page_name=page_name,
                page_icon=page_icon,
            )

    results: dict[int, Any] = {}
    # Cells whose result was synthesised by US (executor caught an
    # exception, or the future never completed before the overall
    # timeout) rather than returned by the widget's own ``fetch()``.
    # Only these are candidates for the last-good fallback, a widget
    # that legitimately returns something error-shaped (e.g.
    # ``{"connected": false, "error": "Spotify not connected"}``) is
    # providing real data and must NOT get overridden by a stale prior
    # result.
    synthesised_errors: set[int] = set()
    max_workers = min(_HYDRATE_MAX_WORKERS, len(indexed))
    # Manual pool + try/finally instead of ``with``: the context
    # manager's ``__exit__`` calls ``shutdown(wait=True)`` which blocks
    # until every worker returns, so a stuck HTTP call (upstream API
    # dead, 15s socket timeout) would hold the composer up long past
    # the overall budget and blow Playwright's page.goto downstream.
    # ``cancel_futures=True`` (3.9+) drops queued-but-unstarted work
    # immediately; still-running futures finish in the background but
    # don't hold us up. See v0.64.72 release notes.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = {
            pool.submit(_worker, plugin_id, options, cw, ch): idx
            for idx, plugin_id, options, cw, ch in indexed
        }
        try:
            for fut in concurrent.futures.as_completed(futures, timeout=_HYDRATE_OVERALL_TIMEOUT_S):
                idx = futures[fut]
                try:
                    results[idx] = fut.result(timeout=_HYDRATE_PER_WIDGET_TIMEOUT_S)
                except Exception as err:
                    logger.warning(
                        "widget hydration failed (cell #%d): %s: %s",
                        idx,
                        type(err).__name__,
                        err,
                    )
                    results[idx] = {"error": f"{type(err).__name__}: {err}"}
                    synthesised_errors.add(idx)
        except concurrent.futures.TimeoutError:
            # Overall budget blew; any unfinished cells get a synthetic
            # error so the widget templates render a clear failure
            # message rather than ``None`` (which most widgets handle
            # as "no data").
            for _fut, idx in futures.items():
                if idx in results:
                    continue
                results[idx] = {
                    "error": "TimeoutError: widget data fetch exceeded the page-render budget"
                }
                synthesised_errors.add(idx)
            logger.warning(
                "page hydration overall timeout (%.1fs); cells still running: %d",
                _HYDRATE_OVERALL_TIMEOUT_S,
                sum(1 for f in futures if not f.done()),
            )
    finally:
        # Non-blocking shutdown: cancel queued work, let in-flight
        # threads finish in the background. Composer returns now.
        pool.shutdown(wait=False, cancel_futures=True)

    # Last-good fallback. Walk each cell's result; if it came back from
    # ``fetch()`` cleanly (whatever its shape, including widget-
    # returned error states), stash it under its (plugin, options,
    # panel) key. If we synthesised the error (executor exception or
    # overall timeout), try to serve the previous successful result.
    for idx, plugin_id, options, _cw, _ch in indexed:
        result = results.get(idx)
        if result is None:
            continue
        key = _last_good_key(plugin_id, options, panel_w, panel_h)
        if idx not in synthesised_errors:
            _LAST_GOOD_DATA[key] = result
            continue
        # ``fresh`` surfaces the real (fresh) result instead of masking a broken
        # reload with a stale last-good render.
        fallback = None if fresh else _LAST_GOOD_DATA.get(key)
        if fallback is not None:
            logger.info("widget hydration fallback to last-good for cell #%d (%s)", idx, plugin_id)
            results[idx] = fallback
    results.update(sample_results)
    return results


def _fetch_plugin_data(
    plugin_id: str,
    options: dict[str, Any],
    panel_w: int,
    panel_h: int,
    preview: bool,
    *,
    cell_w: int = 0,
    cell_h: int = 0,
    fresh: bool = False,
    target_device_id: str = "",
    page_name: str = "",
    page_icon: str = "",
) -> Any:
    """Call the plugin's server.py fetch() if present. Returns None on miss.

    The call runs inside :func:`app.capabilities.capability_scope` so
    the socket egress hook can match against the widget's declared
    ``requires:`` list. Undeclared widgets get an unrestricted scope
    (legacy behaviour), declared ones get the snapshot the loader
    parsed at discovery.

    ``cell_w`` / ``cell_h`` carry the cell's actual pixel dims, useful
    for widgets that want to request an upstream image at the exact
    size they'll be painted at (e.g. fal_image). Defaults of 0 keep
    existing callers (single-cell preview, sample mode) backwards
    compatible: a plugin treats 0 as "unknown" and falls back to
    ``panel_w``/``panel_h``."""
    from app.capabilities import CapabilityDenied, capability_scope

    plugin = _registry().get(plugin_id)
    if plugin is None or plugin.server_module is None:
        return None
    fetch_fn = getattr(plugin.server_module, "fetch", None)
    if fetch_fn is None:
        return None
    settings_store = current_app.config.get("SETTINGS_STORE")
    settings: dict[str, Any] = {}
    if settings_store is not None:
        settings = settings_store.get_for_runtime(
            "plugins", plugin_id, plugin.manifest.get("settings", [])
        )
    # Server-level home location, used as a fallback when a cell's own
    # latitude/longitude is empty. Saves the user from re-typing the
    # same coordinates on every weather / sky / ai_* widget. Empty
    # string in settings.json means "not set"; the widget treats that
    # as 0.0 and renders an error state. Widgets opt in by reading
    # ctx["home_lat"] / ctx["home_lon"] in their fetch().
    home_lat = 0.0
    home_lon = 0.0
    if settings_store is not None:
        app_section = settings_store.get_section("app") or {}
        try:
            home_lat = float(app_section.get("latitude") or 0.0)
        except (TypeError, ValueError):
            home_lat = 0.0
        try:
            home_lon = float(app_section.get("longitude") or 0.0)
        except (TypeError, ValueError):
            home_lon = 0.0
    # v0.70.0: install identifier propagation. Plugins opt in via their
    # manifest with either ``needs_install_id`` (the raw UUID, for
    # shared-world features like the planned tamagotchi pet or dashboard
    # traveler that need cross-widget correlation) or ``needs_scoped_id``
    # (a per-plugin SHA-256 hash of the install id, so the widget's
    # identity can't be correlated with any other widget's). Plugins
    # that declare neither never see either value.
    manifest = plugin.manifest or {}
    ctx: dict[str, Any] = {
        "panel_w": panel_w,
        "panel_h": panel_h,
        "cell_w": cell_w,
        "cell_h": cell_h,
        "preview": preview,
        "data_dir": str(plugin.data_dir),
        "home_lat": home_lat,
        "home_lon": home_lon,
    }
    # ``fresh`` (from ?fresh=1 on the probe / render) tells a widget to bypass
    # its own data_dir cache, so an edit to server.py is instantly verifiable.
    # Widgets opt in by checking ctx.get("fresh"); the field is absent otherwise
    # so non-fresh renders are byte-identical to before.
    if fresh:
        ctx["fresh"] = True
    # v0.71.x: per-device rendering. When the push pipeline is fanning
    # this render out to a specific device (multiple bound to the same
    # panel), the compose URL carries ``?device_id=<id>``. Widgets that
    # declare ``render.per_device_id: true`` in their manifest opt in
    # to reading it; other widgets never see the field so shared
    # renders stay identical to what they were before.
    if target_device_id and manifest.get("render", {}).get("per_device_id"):
        ctx["target_device_id"] = target_device_id
    # v0.71.x: expose the containing page's display metadata so widgets
    # like tesserae_status can inherit the dashboard's icon / name for
    # their leading chip. Always populated when the composer knows
    # them; widgets that don't care simply ignore the fields.
    if page_name:
        ctx["page_name"] = page_name
    if page_icon:
        ctx["page_icon"] = page_icon
    install_uuid = current_app.config.get("INSTALL_ID")
    if isinstance(install_uuid, str) and install_uuid:
        if manifest.get("needs_install_id"):
            ctx["install_id"] = install_uuid
        if manifest.get("needs_scoped_id"):
            from app import install_id as _install_id_module

            ctx["widget_scoped_id"] = _install_id_module.scoped_id(install_uuid, plugin_id)
    try:
        with capability_scope(plugin.capabilities):
            return fetch_fn(
                options,
                settings,
                ctx=ctx,
            )
    except CapabilityDenied as err:
        # Capability violations get a tailored message so the cell
        # surfaces "this widget tried something its manifest didn't
        # claim" rather than the generic exception trace.
        logger.warning("plugin %s capability denied: %s", plugin_id, err)
        return {"error": f"Capability denied: {err}"}
    except Exception as err:
        # Surface the failure to the cell instead of failing the whole render.
        logger.warning("plugin %s fetch() raised: %s", plugin_id, err)
        return {"error": f"{type(err).__name__}: {err}"}


def _hydrate_page(
    page_dict: dict[str, Any], *, preview: bool = False, sample: bool = False, fresh: bool = False
) -> dict[str, Any]:
    """Resolve options, fonts, server-side data, and visual layout."""
    registry = _registry()
    page_font = _resolve_font(page_dict.get("font"), registry)
    page_font_family = page_font.name if page_font else "system-ui"
    # Default to ``light`` when no per-page override is set so the
    # template can render ``page.theme`` unconditionally.
    if not page_dict.get("theme"):
        page_dict = {**page_dict, "theme": "light"}
    # Same for the orthogonal style axis, fall back to ``standard``
    # so the template can render ``page.style`` unconditionally.
    if not page_dict.get("style"):
        page_dict = {**page_dict, "style": "standard"}

    gap = int(page_dict.get("gap", 0) or 0)
    # ``gap`` is the visible matting strip the user sees. Make it look
    # identical whether between two cells or between a cell and the panel
    # edge: outer edges inset by gap/2, inner edges by gap/4 (so two facing
    # insets sum to gap/2).
    outer_pad = gap // 2
    inner_pad = gap // 4
    corner_radius = int(page_dict.get("corner_radius", 0) or 0)
    panel_w = int(page_dict["panel"]["w"])
    panel_h = int(page_dict["panel"]["h"])

    # Auto-rotate/scale cells if the saved layout doesn't match the
    # current panel orientation (e.g. dashboard designed for landscape
    # is being rendered for a flipped-to-portrait panel).
    from app.panel import fit_cells_to_panel  # local import: avoid cycle

    raw_coords = [(int(c["x"]), int(c["y"]), int(c["w"]), int(c["h"])) for c in page_dict["cells"]]
    # v0.71.1: the auto-managed status bar cell is orientation-fixed
    # (always at the top of the target). Without this hint,
    # fit_cells_to_panel's 90° rotation puts it on the right edge of a
    # portrait panel.
    status_bar_id = page_dict.get("status_bar_cell_id")
    top_strip_index: int | None = None
    if status_bar_id:
        for i, c in enumerate(page_dict["cells"]):
            if c.get("id") == status_bar_id:
                top_strip_index = i
                break
    fitted = fit_cells_to_panel(raw_coords, panel_w, panel_h, top_strip_index=top_strip_index)
    page_dict = {
        **page_dict,
        "cells": [
            {**cell, "x": nx, "y": ny, "w": nw, "h": nh}
            for cell, (nx, ny, nw, nh) in zip(page_dict["cells"], fitted, strict=True)
        ],
    }

    # First pass: assemble the layout, font, options for each cell
    # synchronously. These are all in-memory operations (registry
    # lookups); the slow part, server-side widget data fetches, is
    # split out so it can run in parallel below.
    cells_meta: list[dict[str, Any]] = []
    for cell in page_dict["cells"]:
        cell_font = _resolve_font(cell["font"], registry) if cell.get("font") else page_font
        cell_font_family = cell_font.name if cell_font else page_font_family
        plugin_id = cell.get("plugin") or None
        plugin = registry.get(plugin_id) if plugin_id else None
        resolved_options = (
            _resolved_options(plugin_id, cell.get("options", {})) if plugin_id else {}
        )
        full_bleed = bool(plugin and plugin.manifest.get("render", {}).get("full_bleed"))
        # Per-cell padding override wins over both the page-level gap
        # and the ``full_bleed`` manifest flag when set (r/eink launch
        # feedback: users wanted per-cell breathing-room control
        # without touching the page gap that other cells share).
        pad_override = cell.get("padding_override")
        if isinstance(pad_override, int) and pad_override >= 0:
            left_pad = top_pad = right_pad = bottom_pad = pad_override
        else:
            # Full-bleed widgets (e.g. the tesserae_status bar) drop the
            # OUTER padding: any cell edge that touches the panel wall
            # renders at zero, so the widget goes edge-to-edge on those
            # sides. Inner padding (matting between this cell and a
            # neighbouring cell) still applies, so the user's gap slider
            # stays visible around the widgets that sit below or beside
            # the bar. Without this the 48 px bar either eats 3/4 of its
            # own height at large gaps (pre-v0.71.1) or has no visible
            # matting anywhere (v0.71.1's skip-everything shortcut).
            outer_left = 0 if full_bleed else outer_pad
            outer_top = 0 if full_bleed else outer_pad
            outer_right = 0 if full_bleed else outer_pad
            outer_bottom = 0 if full_bleed else outer_pad
            left_pad = outer_left if cell["x"] == 0 else inner_pad
            top_pad = outer_top if cell["y"] == 0 else inner_pad
            right_pad = outer_right if cell["x"] + cell["w"] == panel_w else inner_pad
            bottom_pad = outer_bottom if cell["y"] + cell["h"] == panel_h else inner_pad
        layout = {
            "x": cell["x"] + left_pad,
            "y": cell["y"] + top_pad,
            "w": max(1, cell["w"] - left_pad - right_pad),
            "h": max(1, cell["h"] - top_pad - bottom_pad),
        }
        cells_meta.append(
            {
                "cell": cell,
                "plugin_id": plugin_id,
                "resolved_options": resolved_options,
                "layout": layout,
                "font_family": cell_font_family,
                "full_bleed": full_bleed,
                # Widget-manifest default tap action (issue #49); the
                # per-cell ``on_tap`` override wins at emission time.
                "manifest_on_tap": plugin.manifest.get("on_tap") if plugin else None,
            }
        )

    # Second pass: fetch widget data in parallel. Before this, slow
    # upstreams (Open-Meteo, GitHub, …) added up, six 15s timeouts
    # in series is 90s, blowing past Playwright's navigation budget
    # and surfacing as a blank/timeout PNG. Workers share the Flask
    # app context the caller holds so each fetch can still read
    # SETTINGS_STORE / plugin registry from current_app.
    data_by_cell_index: dict[int, Any] = _parallel_fetch_plugin_data(
        cells_meta,
        panel_w,
        panel_h,
        preview,
        sample=sample,
        fresh=fresh,
        target_device_id=str(page_dict.get("target_device_id") or ""),
        page_name=str(page_dict.get("name") or ""),
        page_icon=str(page_dict.get("icon") or ""),
    )

    cells_out: list[dict[str, Any]] = []
    for idx, meta in enumerate(cells_meta):
        cell = meta["cell"]
        plugin_id = meta["plugin_id"]
        cells_out.append(
            {
                **cell,
                **meta["layout"],
                "plugin": plugin_id or "",
                "options": meta["resolved_options"],
                "data": data_by_cell_index.get(idx),
                "font_family": meta["font_family"],
                "full_bleed": meta["full_bleed"],
                # Touch attributes (issue #49): stamped on the cell
                # container so the render-time extractor picks the whole
                # cell up as a region. Cell override wins over the
                # widget-manifest default.
                "on_tap_attr": _touch_attr(cell.get("on_tap"), meta["manifest_on_tap"]),
                "on_swipe_attr": _touch_attr(cell.get("on_swipe"), None),
                "on_slide_attr": _touch_attr(cell.get("on_slide"), None),
            }
        )

    return {
        **page_dict,
        "cells": cells_out,
        "font_family": page_font_family,
        "font_face_css": _font_face_css(registry.fonts),
        "corner_radius": corner_radius,
    }


def _panel_override(w: str | None, h: str | None) -> tuple[int, int] | None:
    """Parse ?w=&h= into clamped panel dims, or None if absent/invalid."""
    if not w or not h:
        return None
    try:
        pw, ph = int(w), int(h)
    except ValueError:
        return None
    if pw < 1 or ph < 1:
        return None
    return min(pw, 10000), min(ph, 10000)


def _build_canvas_els(
    els: list[Any], cw: int, ch: int, *, target_device_id: str = ""
) -> list[dict[str, Any]]:
    """Shape a canvas's elements for ``panels_compose.html``: decorations pass
    their raw props (drawn client-side), widget elements get resolved options +
    fetched data (sample fallback), all in the authored ``cw x ch`` space.

    ``target_device_id`` is threaded to each widget fetch so per-device widgets
    (``tesserae_status`` battery / signal) reflect the panel receiving this
    render rather than a min-across-all-devices aggregate. The grid path carries
    this through ``page_dict["target_device_id"]``; canvas needs it passed in
    because :func:`_render_canvas` fetches server-side here."""
    from app.widget_samples import get_sample

    # Dedupe fetches across elements that resolve to the same widget +
    # options: a canvas often has several data primitives (temp, humidity,
    # wind) bound to the SAME weather widget, plus maybe the widget itself.
    # Fetching once per (widget, resolved-options) means one upstream call,
    # not one per element. Keyed on resolved options (not raw) so an
    # element inheriting the app-level location shares with one that picked
    # the same place explicitly.
    fetch_memo: dict[str, Any] = {}

    def _shared_fetch(plugin_id: str, opts: dict[str, Any], cell_w: int, cell_h: int) -> Any:
        memo_key = f"{plugin_id}::{json.dumps(opts, sort_keys=True, default=str)}"
        if memo_key in fetch_memo:
            return fetch_memo[memo_key]
        try:
            fetched = _fetch_plugin_data(
                plugin_id,
                opts,
                cw,
                ch,
                preview=False,
                cell_w=cell_w,
                cell_h=cell_h,
                target_device_id=target_device_id,
            )
        except Exception:
            fetched = None
        fetch_memo[memo_key] = fetched
        return fetched

    def _resolve_source(
        plugin_id: str, raw_options: dict[str, Any], cell_w: int, cell_h: int
    ) -> tuple[dict[str, Any], Any, str]:
        """Resolve options + fetch, classifying the result as ``live`` (real
        fetch), ``sample`` (demo fallback because nothing was configured), or
        ``error``. The classification is carried into the DOM so the render
        report can tell the agent which it got."""
        opts = _resolved_options(plugin_id, raw_options)
        data = _shared_fetch(plugin_id, opts, cell_w, cell_h)
        if isinstance(data, dict) and not data.get("error"):
            return opts, data, "live"
        if not _location_configured(raw_options):
            sample = get_sample(plugin_id)
            if isinstance(sample, dict):
                return opts, sample, "sample"
        return opts, data, "error"

    def _resolve_url_source(url: str, headers: dict[str, str]) -> tuple[Any, str]:
        """Fetch a raw URL data source for a code element through the SSRF
        guard, classifying as ``live`` (parsed JSON) or ``error``. The parsed
        body is delivered as-is at ``ctx.data[name]``; on failure an
        ``{"error": ...}`` payload is delivered so the element can show it."""
        from app.net_guard import BlockedURLError, fetch_json

        try:
            return fetch_json(url, headers=headers or None), "live"
        except BlockedURLError as err:
            return {"error": str(err)}, "error"
        except Exception as err:  # network / decode / size-cap
            return {"error": f"{type(err).__name__}: {err}"}, "error"

    def _apply_binds(el: Any, out: dict[str, Any]) -> None:
        """Evaluate an element's live data bindings and patch its props in place,
        so a bound shape reflects data on this render (see :mod:`app.bindings`).
        Each binding fetches through the shared memo, so a shape bound to the same
        widget as a data element on the canvas costs no extra fetch."""
        for b in getattr(el, "bind", None) or []:
            if not b.source:
                continue
            _, bdata, _ = _resolve_source(b.source, b.options, el.w, el.h)
            patch = apply_binding(b, bdata)
            if patch:
                out.update(patch)

    els_out: list[dict[str, Any]] = []
    for e in els:
        if e.visible is False:
            continue
        if e.kind == "data":
            # Data primitive: fetch its source widget's data (sample fallback),
            # same path a widget uses, so the field resolves at render time.
            ddata: Any = None
            dsrc = "none"
            if e.source:
                _, ddata, dsrc = _resolve_source(e.source, e.options, e.w, e.h)
            els_out.append(
                {
                    "id": e.id,
                    "kind": "data",
                    "source": e.source,
                    "field": e.field,
                    "display": e.display,
                    "format": e.format,
                    "unit": e.unit,
                    "precision": e.precision,
                    "label": e.label,
                    "color": e.color,
                    "align": e.align,
                    "size": e.size,
                    "opacity": e.opacity,
                    "rotate": e.rotate,
                    "x": e.x,
                    "y": e.y,
                    "w": e.w,
                    "h": e.h,
                    "data": ddata,
                    "data_source": dsrc,
                }
            )
            _apply_binds(e, els_out[-1])
            _stamp_touch(els_out[-1], e)
            continue
        if e.kind == "code":
            # Author HTML/CSS/JS fed by ANY number of widgets' data primitives.
            # Resolve each named source server-side (same shared fetch as data
            # elements, deduped), so the client renderer injects them as
            # ctx.data[name] into a scripts-enabled but origin-less,
            # network-blocked sandbox. The data is delivered, never fetched from
            # inside the frame.
            from app.state.panel_store import CodeSource

            srcs = list(getattr(e, "sources", None) or [])
            # Legacy single-source form: a bare ``source`` becomes one source.
            if e.source and not any(s.key == e.source for s in srcs):
                srcs.insert(0, CodeSource(key=e.source, options=e.options, name=e.source))
            cdata: dict[str, Any] = {}
            csrc = "none"
            for s in srcs:
                if getattr(s, "url", ""):
                    # Raw URL source: fetch server-side through the SSRF guard
                    # and deliver the parsed JSON directly at ctx.data[name].
                    sdata, sstate = _resolve_url_source(s.url, getattr(s, "headers", {}) or {})
                    cdata[s.name or "data"] = sdata
                elif s.key:
                    _, sdata, sstate = _resolve_source(s.key, s.options, e.w, e.h)
                    cdata[s.name or s.key] = sdata
                else:
                    continue
                # Roll up a single status: live if any is live, else the first.
                if csrc == "none" or sstate == "live":
                    csrc = sstate
            els_out.append(
                {
                    "id": e.id,
                    "kind": "code",
                    "sources": [s.model_dump() for s in srcs],
                    "field": e.field,
                    "html": e.html,
                    "css": e.css,
                    "js": e.js,
                    "opacity": e.opacity,
                    "rotate": e.rotate,
                    "x": e.x,
                    "y": e.y,
                    "w": e.w,
                    "h": e.h,
                    "data": cdata,
                    "data_source": csrc,
                }
            )
            _stamp_touch(els_out[-1], e)
            continue
        if e.kind and e.kind != "widget":
            els_out.append(
                {
                    "id": e.id,
                    "kind": e.kind,
                    "color": e.color,
                    "fill": e.fill,
                    "stroke": e.stroke,
                    "radius": e.radius,
                    "icon": e.icon,
                    "weight": e.weight,
                    "text": e.text,
                    "align": e.align,
                    "size": e.size,
                    "html": e.html,
                    "css": e.css,
                    "opacity": e.opacity,
                    "rotate": e.rotate,
                    "x": e.x,
                    "y": e.y,
                    "w": e.w,
                    "h": e.h,
                    "data_source": "static",
                }
            )
            _apply_binds(e, els_out[-1])
            _stamp_touch(els_out[-1], e)
            continue
        item: dict[str, Any] = {
            "id": e.id,
            "kind": "widget",
            "widget": e.widget,
            "fragment": e.fragment or "full",
            "rotate": e.rotate,
            "opacity": e.opacity,
            "parts": [p.model_dump() for p in e.parts],
            "crop": (
                e.crop.model_dump()
                if e.crop and any([e.crop.top, e.crop.right, e.crop.bottom, e.crop.left])
                else None
            ),
            "x": e.x,
            "y": e.y,
            "w": e.w,
            "h": e.h,
            "options": {},
            "data": None,
            "data_source": "none",
        }
        if e.widget:
            opts, data, wsrc = _resolve_source(e.widget, e.options, e.w, e.h)
            item["options"] = opts
            item["data"] = data
            item["data_source"] = wsrc
        _apply_binds(e, item)
        _stamp_touch(item, e)
        els_out.append(item)
    return els_out


def _render_canvas(layout: Any, *, target_w: int, target_h: int, target_device_id: str = "") -> str:
    """Render a canvas layout (authored at ``layout.w x layout.h``) scaled to fit
    a ``target_w x target_h`` panel, aspect preserved and centred. When the
    authored size already matches the target the scale is 1 (no transform).

    ``target_device_id`` names which bound device this render is for, so
    per-device widgets (``tesserae_status``) resolve to that device's telemetry."""
    cw = max(1, int(layout.w))
    ch = max(1, int(layout.h))
    scale = min(target_w / cw, target_h / ch)
    if scale <= 0:
        scale = 1.0
    ox = round((target_w - cw * scale) / 2)
    oy = round((target_h - ch * scale) / 2)
    registry = current_app.config["PLUGIN_REGISTRY"]
    font = _resolve_font(layout.font or None, registry)
    return render_template(
        "panels_compose.html",
        els=_build_canvas_els(layout.els, cw, ch, target_device_id=target_device_id),
        cw=cw,
        ch=ch,
        w=target_w,
        h=target_h,
        scale=scale,
        ox=ox,
        oy=oy,
        theme=layout.theme or "light",
        style=layout.style or "standard",
        font_family=font.name if font else "system-ui, sans-serif",
        bg=layout.bg or "",
        bg_image=layout.bg_image or "",
        bg_fit=layout.bg_fit or "cover",
        font_face_css=_font_face_css(registry.fonts),
        code_fonts=[{"id": f.id, "name": f.name} for f in registry.fonts.values()],
    )


@bp.get("/compose/_measure")
def compose_measure() -> str:
    """A minimal loopback page that loads every widget font and exposes
    ``window.__measure(items)`` for the MCP ``measure_text`` helper. Screenshotted
    by nobody; the headless inspector navigates here and evaluates the measure
    function so an agent can size a box to its content (preventing text clipping).
    Path is static under ``/compose/`` so it wins over ``/compose/<page_id>`` and
    skips the login gate like the other composer render targets."""
    registry = current_app.config["PLUGIN_REGISTRY"]
    return render_template("panels_measure.html", font_face_css=_font_face_css(registry.fonts))


def _preview_target_device(page: Page, devices: Any) -> str:
    """First bound device id that exists in the registry, or ``""``.

    A non-push preview render (the dashboards-list hover thumbnail, the live
    compose iframe) carries no ``?device_id``. Without a target, per-device
    widgets like ``tesserae_status`` fall back to a min-across-all-devices
    aggregate, so the preview shows the wrong (worst) battery / signal. Picking
    the page's first bound device makes the preview show a real device instead."""
    if devices is None:
        return ""
    for did in getattr(page, "device_ids", None) or []:
        if devices.devices.get(did) is not None:
            return str(did)
    return ""


@bp.get("/compose/<page_id>")
def compose(page_id: str) -> str:
    preview_cache: dict[str, Page] = current_app.config.get("PREVIEW_CACHE", {})
    page = preview_cache.get(page_id)
    if page is None:
        store: PageStore = current_app.config["PAGE_STORE"]
        page = store.get(page_id)
    if page is None:
        abort(404)
    for_push = request.args.get("for_push") == "1"
    preview_mode = request.args.get("preview") == "1" and not for_push
    # Inject the resolved panel before hydrate, _hydrate_page expects
    # page_dict["panel"] to always be present. An explicit ?w=&h= override
    # wins (the editor's per-aspect previews and the per-panel push render
    # at a specific size); otherwise fall back to the page's primary panel.
    page_dict = page.model_dump(mode="json", exclude_none=True)
    settings_store = current_app.config["SETTINGS_STORE"]
    devices = current_app.config.get("DEVICE_REGISTRY")
    override = _panel_override(request.args.get("w"), request.args.get("h"))
    if override is not None:
        panel_w, panel_h = override
    else:
        panel = resolve_panel_for_page(page, devices, settings_store)
        panel_w, panel_h = panel.w, panel.h

    # ``?device_id=<id>`` on the compose URL names which bound device this
    # render is for, so per-device widgets (tesserae_status battery / signal)
    # reflect the panel receiving the frame rather than a min-across-all-devices
    # aggregate. The push pipeline sets this when it fans out to devices sharing
    # a panel. A non-push preview (hover thumbnail / live iframe) carries no
    # device_id, so we default to the page's first bound device rather than
    # rendering the aggregate. Grid and canvas both consume it (grid via
    # page_dict, canvas via _render_canvas).
    explicit_device_id = (request.args.get("device_id") or "").strip()
    if explicit_device_id:
        target_device_id = explicit_device_id
    elif not for_push:
        target_device_id = _preview_target_device(page, devices)
    else:
        target_device_id = ""

    # Freeform (canvas) dashboards render the composer layout scaled to the
    # panel, and share this route so push / scheduler / rotation drive them by
    # page id exactly like a grid page. An unbound, un-overridden canvas renders
    # at its authored size.
    if page.layout_kind == "canvas" and page.canvas is not None:
        if override is None and not page.device_ids:
            target_w, target_h = page.canvas.w, page.canvas.h
        else:
            target_w, target_h = panel_w, panel_h
        return _render_canvas(
            page.canvas,
            target_w=target_w,
            target_h=target_h,
            target_device_id=target_device_id,
        )

    page_dict["panel"] = {"w": panel_w, "h": panel_h}
    if target_device_id:
        page_dict["target_device_id"] = target_device_id
    return render_template(
        "compose.html",
        page=_hydrate_page(page_dict, preview=not for_push),
        for_push=for_push,
        preview_mode=preview_mode,
    )


# Longest side of the cached hover-preview PNG. The dashboards list shows the
# image scaled well below this, so 800px is plenty sharp while keeping the
# screenshot cheap to render and small to cache.
PREVIEW_MAX_DIM: Final = 800


def preview_dims(page: Page, devices: Any, settings_store: Any) -> tuple[int, int]:
    """The dims to render a dashboard's hover preview at: its panel (or an
    unbound canvas's authored size), scaled so the longest side is
    ``PREVIEW_MAX_DIM`` with the aspect preserved. Mirrors ``compose``'s dim
    logic so the preview matches what a push would look like."""
    cv = (
        page.canvas
        if (page.layout_kind == "canvas" and page.canvas and not page.device_ids)
        else None
    )
    if cv is not None:
        pw, ph = int(cv.w), int(cv.h)
    else:
        panel = resolve_panel_for_page(page, devices, settings_store)
        pw, ph = int(panel.w), int(panel.h)
    pw, ph = max(1, pw), max(1, ph)
    longest = max(pw, ph)
    if longest > PREVIEW_MAX_DIM:
        scale = PREVIEW_MAX_DIM / longest
        pw, ph = max(1, round(pw * scale)), max(1, round(ph * scale))
    return pw, ph


def page_preview_token(page: Page, dims: tuple[int, int]) -> str:
    """A short content hash identifying this dashboard's rendered look.

    Changes whenever any render-affecting field of the page (or its resolved
    preview dims) changes, and stays stable otherwise, so a cached preview
    image can be reused until the dashboard is actually edited. Volatile
    concurrency metadata (``updated_at`` / ``updated_by``) is excluded so a
    no-op save doesn't needlessly invalidate the cache."""
    payload = page.model_dump(mode="json", exclude_none=True)
    payload.pop("updated_at", None)
    payload.pop("updated_by", None)
    blob = json.dumps(payload, sort_keys=True, default=str) + f"|{dims[0]}x{dims[1]}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@bp.get("/compose/<page_id>/preview.png")
def compose_preview(page_id: str) -> Response:
    """A cached PNG preview of a dashboard, for the dashboards-list hover.

    Rendered once per content version (the token) via the same headless path a
    push uses, cached on disk, and served immutably keyed by the token in the
    URL. The dashboards list embeds the current token, so an edit produces a new
    URL and a fresh render; unchanged dashboards keep hitting the cached image.

    The render is deliberately kept OFF this request thread: if the image isn't
    cached yet we enqueue a background render and return ``202`` so the client
    falls back to a live iframe meanwhile. Rendering inline would pin a waitress
    worker for up to ~105s while the screenshot self-requests ``/compose``,
    which a burst of hovers could turn into thread starvation.
    """
    preview_pages: dict[str, Page] = current_app.config.get("PREVIEW_CACHE", {}) or {}
    page = preview_pages.get(page_id) or current_app.config["PAGE_STORE"].get(page_id)
    if page is None:
        abort(404)
    devices = current_app.config.get("DEVICE_REGISTRY")
    settings_store = current_app.config["SETTINGS_STORE"]
    width, height = preview_dims(page, devices, settings_store)
    token = page_preview_token(page, (width, height))

    cache_dir = Path(current_app.config["DATA_ROOT"]) / "core" / "previews"
    cache_path = cache_dir / f"{page_id}__{token}.png"

    if not cache_path.exists():
        from app import preview_cache

        preview_cache.submit(
            key=f"{page_id}__{token}",
            base_url=request.host_url.rstrip("/"),
            page_id=page_id,
            width=width,
            height=height,
            cache_path=cache_path,
            pool=current_app.config.get("BROWSER_POOL"),
        )
        # Not ready yet: tell the client to fall back to the iframe now, and
        # don't let the browser cache this so it re-fetches once the render
        # lands. Any non-2xx / empty body fires the <img> error handler.
        resp = current_app.response_class(status=202)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # Immutable per token: the URL changes when the dashboard changes, so the
    # browser can hold it for a day and a matching If-None-Match short-circuits.
    if request.if_none_match and token in request.if_none_match:
        resp = current_app.response_class(status=304)
    else:
        resp = current_app.response_class(cache_path.read_bytes(), mimetype="image/png")
    resp.set_etag(token)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.get("/compose/canvas/<canvas_id>")
def compose_canvas(canvas_id: str) -> str:
    """Render target for a Panels canvas document (issue #60).

    Lives under ``/compose/`` so it inherits that path's loopback bypass (the
    headless renderer reaches it without the login gate). Gated by the
    ``composer`` experiment. Each element is a widget instance rendered as one
    fragment: this fetches its data with the element's resolved options (falling
    back to the dev-gallery sample so an unconfigured or erroring widget still
    paints), then hands the elements to ``panels_compose.html``, which mounts
    each as the real widget via ``composer.js`` with ``ctx.fragment`` set.
    """
    from app import experiments

    if not experiments.is_enabled("composer"):
        abort(404)
    store = current_app.config.get("PANEL_STORE")
    doc = store.get(canvas_id) if store is not None else None
    if doc is None:
        abort(404)
    return _render_canvas(doc.to_layout(), target_w=doc.w, target_h=doc.h)


@bp.get("/_test/render")
def test_render() -> str:
    """Mount one plugin into a known cell size, no Page needed.

    Available in debug or testing mode (the per-plugin smoke tests use it), and
    over loopback so the MCP widget render endpoint can screenshot a single widget
    on a production / HA instance. The renderer navigates via ``127.0.0.1`` (see
    ``to_loopback_url``), the same loopback trust boundary ``/compose`` relies on.
    """
    from app.auth import _is_loopback

    if not (current_app.debug or current_app.testing or _is_loopback()):
        abort(404)

    plugin_id = request.args.get("plugin")
    if not plugin_id:
        abort(400)

    size = request.args.get("size", "md")
    if size not in SIZE_DIMENSIONS:
        abort(400)
    # Explicit ``w,h`` (Screenshot Contract) override the size preset for the
    # cell dimensions; absent, the size mapping stands (default behaviour).
    dim_w = request.args.get("w")
    dim_h = request.args.get("h")

    sample_mode = request.args.get("sample") == "1"
    # ?theme=<id> picks one of the Spectra theme blocks
    # (light / dark / high-contrast / sepia / nord / cool-gray, plus the
    # three movement themes bauhaus / destijl / brutalist).
    theme_id = request.args.get("theme") or "light"
    # ?style=<id> picks one of the orthogonal Spectra style blocks
    # (standard / display / editorial / mono / elegant / condensed, plus
    # the three movement styles bauhaus / destijl / brutalist).
    style_id = request.args.get("style") or "standard"

    # ?fragment=<id> renders a single declared fragment of the widget
    # (``ctx.cell.fragment``); defaults to "full". Validated against the
    # widget's declared fragments so an unknown id 400s rather than silently
    # rendering the whole card.
    fragment = request.args.get("fragment") or "full"
    plugin = _registry().get(plugin_id)
    if fragment != "full" and plugin is not None:
        from app.panels_schema import fragments_of

        if fragment not in {f["id"] for f in fragments_of(plugin)}:
            abort(400)
    # ?fresh=1 bypasses caches so a just-edited server.py is reflected
    # immediately: it sets ``ctx["fresh"]`` (widgets opt in to skip their own
    # data_dir cache) and skips the render path's last-good fallback. Default off.
    fresh = request.args.get("fresh") in ("1", "true", "True")

    # Per-widget content zoom from the gallery's zoom picker. Same
    # 0.5–3.0 clamp the Cell model enforces; out-of-range or unparseable
    # values silently fall back to 1.0.
    try:
        zoom_val = float(request.args.get("zoom") or "1")
    except ValueError:
        zoom_val = 1.0
    zoom_val = max(0.5, min(3.0, zoom_val))

    # ?opts=<json> lets the dev widget-preview page inject cell options
    # (place label, units, API key, etc.) so the preview reflects what a
    # composed dashboard would actually show. Malformed JSON silently
    # falls through to the plugin's defaults via ``_resolved_options``.
    opts_raw = request.args.get("opts") or ""
    cell_options: dict[str, Any] = {}
    if opts_raw:
        try:
            parsed = json.loads(opts_raw)
            if isinstance(parsed, dict):
                cell_options = parsed
        except (json.JSONDecodeError, ValueError):
            cell_options = {}

    if dim_w is not None or dim_h is not None:
        try:
            cell_w = clamp_screenshot_dim(int(dim_w))  # type: ignore[arg-type]
            cell_h = clamp_screenshot_dim(int(dim_h))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            abort(400)
    else:
        cell_w, cell_h = SIZE_DIMENSIONS[size]
    page = {
        "id": "_test",
        "name": f"Test: {plugin_id} @ {size}",
        "panel": {"w": cell_w, "h": cell_h},
        "font": "default",
        "theme": theme_id,
        "style": style_id,
        "cells": [
            {
                "id": "test-cell",
                "x": 0,
                "y": 0,
                "w": cell_w,
                "h": cell_h,
                "plugin": plugin_id,
                "fragment": fragment,
                "options": cell_options,
                "zoom": zoom_val,
            }
        ],
    }
    return render_template(
        "compose.html",
        page=_hydrate_page(page, preview=True, sample=sample_mode, fresh=fresh),
        for_push=False,
        preview_mode=False,
    )


_MATRIX_THEMES: Final[list[dict[str, str]]] = [
    {"id": "light", "label": "Light", "group": "Spectra"},
    {"id": "dark", "label": "Dark", "group": "Spectra"},
    {"id": "high-contrast", "label": "High contrast", "group": "Spectra"},
    {"id": "sepia", "label": "Sepia", "group": "Spectra"},
    {"id": "nord", "label": "Nord", "group": "Spectra"},
    {"id": "cool-gray", "label": "Cool gray", "group": "Spectra"},
    {"id": "bauhaus", "label": "Bauhaus", "group": "Movement"},
    {"id": "destijl", "label": "De Stijl", "group": "Movement"},
    {"id": "brutalist", "label": "Brutalist", "group": "Movement"},
]
_MATRIX_STYLES: Final[list[dict[str, str]]] = [
    {"id": "standard", "label": "Standard"},
    {"id": "display", "label": "Display"},
    {"id": "editorial", "label": "Editorial"},
    {"id": "mono", "label": "Mono"},
    {"id": "elegant", "label": "Elegant"},
    {"id": "condensed", "label": "Condensed"},
    {"id": "bauhaus", "label": "Bauhaus"},
    {"id": "destijl", "label": "De Stijl"},
    {"id": "brutalist", "label": "Brutalist"},
]


@bp.get("/_test/matrix")
def test_theme_style_matrix() -> str:
    """Theme × style coverage matrix for one widget at a time.

    19 themes × 9 styles = 171 iframes, lazy-loaded, so opening this page
    doesn't fire 171 fetches up front. Each iframe drives ``/_test/render``
    with one ``(theme, style)`` pair so the combinations get eyeballed
    instead of trusted on faith. Dev-only, guarded behind debug/testing
    the same way ``/_test/widgets`` is.

    Query params:
      ?widget=<plugin_id>   widget to use as the test cell (default: first
                            registered widget alphabetically)
      ?size=xs|sm|md|lg     cell size (default md)
      ?sample=1             pass ``sample=1`` through to /_test/render so
                            widgets with hand-written fixtures don't
                            error-state
    """
    if not (current_app.debug or current_app.testing):
        abort(404)
    widgets = sorted(_registry().widgets(), key=lambda p: p.name.lower())
    if not widgets:
        abort(404)
    widget_id = request.args.get("widget") or widgets[0].id
    plugin = next((p for p in widgets if p.id == widget_id), None)
    if plugin is None:
        abort(400)
    size = request.args.get("size", "md")
    if size not in SIZE_DIMENSIONS:
        abort(400)
    sample = request.args.get("sample") == "1"
    cell_w, cell_h = SIZE_DIMENSIONS[size]
    return render_template(
        "theme_style_matrix.html",
        widget=plugin,
        widgets=widgets,
        size=size,
        cell_w=cell_w,
        cell_h=cell_h,
        themes=_MATRIX_THEMES,
        styles=_MATRIX_STYLES,
        sample=sample,
    )


# Panel presets the preview page exposes in its panel-size dropdown.
# Hand-ordered: the portrait 13.3" (which most of the dev work targets)
# leads; the rest follow native landscape sizes top-to-bottom. ``label``
# is the picker text; ``w`` / ``h`` are the composition dimensions the
# synthetic page renders at.
_PREVIEW_PANELS: Final[list[dict[str, Any]]] = [
    {"id": pid, "label": preset.label, "w": preset.w, "h": preset.h}
    for pid, preset in PANEL_PRESETS.items()
]
_PREVIEW_PANEL_IDS: Final[set[str]] = {p["id"] for p in _PREVIEW_PANELS}
_DEFAULT_PREVIEW_PANEL_ID: Final[str] = "waveshare_e6_13_3"


# Multi-cell layout for the preview synthetic page. Spiral halving:
# each cell takes half of the remaining region, alternating sides
# (top / left / bottom / right) so the layout spirals inward and each
# cell ends up at half the area of the previous one. The last cell is
# the leftover remainder so the unassigned placeholder sits in the
# tightest corner.
#
# On a 1200×1600 portrait panel the cells land at:
#   1: 1200×800 [LG]   2: 600×800  [MD]   3: 600×400 [MD]
#   4: 300×400 [SM]    5: 300×200  [SM]   6: 150×200 [XS]
#   7: 150×200 [XS, unassigned]
# Fractions stay relative so the same pattern reflows on any panel.
def _spiral_halving_cells(n: int) -> list[tuple[float, float, float, float]]:
    """Recursive halving spiral. ``n`` is the total cell count incl. the
    leftover remainder cell."""
    cells: list[tuple[float, float, float, float]] = []
    # (x, y, w, h) of the still-unallocated region, as panel fractions.
    rx, ry, rw, rh = 0.0, 0.0, 1.0, 1.0
    # Direction sequence, top first (the user's "half the page is used
    # on the top"), then left (their "half of the left hand side"),
    # then continue the spiral with bottom + right so successive cells
    # tessellate cleanly around the centre.
    dirs = ("top", "left", "bottom", "right")
    for i in range(n - 1):
        d = dirs[i % 4]
        half_w = rw / 2
        half_h = rh / 2
        if d == "top":
            cells.append((rx, ry, rw, half_h))
            ry += half_h
            rh = half_h
        elif d == "left":
            cells.append((rx, ry, half_w, rh))
            rx += half_w
            rw = half_w
        elif d == "bottom":
            cells.append((rx, ry + half_h, rw, half_h))
            rh = half_h
        else:  # right
            cells.append((rx + half_w, ry, half_w, rh))
            rw = half_w
    cells.append((rx, ry, rw, rh))
    return cells


_PREVIEW_CELLS_FRAC: Final[list[tuple[float, float, float, float]]] = _spiral_halving_cells(7)


def _size_label(w_px: int) -> str:
    """Bucket cell width into the same xs/sm/md/lg buckets widgets use
    in their container queries. Boundaries match the breakpoints in
    weather_now / weather_forecast (and the other Spectra widgets that
    do size-tiered layouts) so the preview's label tracks the layout
    the widget actually picks."""
    if w_px < 280:
        return "XS"
    if w_px < 440:
        return "SM"
    if w_px < 700:
        return "MD"
    return "LG"


def _build_preview_page(
    *,
    plugin_id: str,
    panel_w: int,
    panel_h: int,
    theme_id: str,
    style_id: str,
    cell_options: dict[str, Any],
) -> dict[str, Any]:
    """Compose the synthetic multi-cell page the widget preview renders.

    Cells 1-6 are assigned to ``plugin_id`` so the same widget paints at
    every size bucket (lg / md / sm / xs). Cell 7 has ``plugin=None`` so
    the composer paints the empty "pick a widget" placeholder beside the
    live cells. Coordinates round to integers because Page / Cell are
    pydantic-typed for ``int``, float fractions blow up at hydrate.
    ``size_label`` rides along on each cell so compose.html's preview-
    mode tag can show the bucket the widget is actually rendering in."""
    cells: list[dict[str, Any]] = []
    for idx, (x_frac, y_frac, w_frac, h_frac) in enumerate(_PREVIEW_CELLS_FRAC):
        is_unassigned = idx == len(_PREVIEW_CELLS_FRAC) - 1
        w_px = round(panel_w * w_frac)
        cells.append(
            {
                "id": f"preview-{idx + 1}",
                "x": round(panel_w * x_frac),
                "y": round(panel_h * y_frac),
                "w": w_px,
                "h": round(panel_h * h_frac),
                "plugin": None if is_unassigned else plugin_id,
                "options": {} if is_unassigned else cell_options,
                "zoom": 1.0,
                "size_label": _size_label(w_px),
            }
        )
    return {
        "id": "_test_preview",
        "name": f"Preview: {plugin_id}",
        "panel": {"w": panel_w, "h": panel_h},
        "font": "default",
        "theme": theme_id,
        "style": style_id,
        "cells": cells,
    }


def _parse_preview_args() -> dict[str, Any]:
    """Common querystring parse for the preview controls. Pulled into a
    helper so the parent page and the iframe-rendered synthetic page
    agree on defaults + validation."""
    widget_id = request.args.get("widget") or ""
    theme_id = request.args.get("theme") or "light"
    style_id = request.args.get("style") or "standard"
    sample_mode = request.args.get("sample") == "1"
    panel_id = request.args.get("panel") or _DEFAULT_PREVIEW_PANEL_ID
    if panel_id not in _PREVIEW_PANEL_IDS:
        panel_id = _DEFAULT_PREVIEW_PANEL_ID
    preset = PANEL_PRESETS[panel_id]
    opts_raw = request.args.get("opts") or ""
    cell_options: dict[str, Any] = {}
    if opts_raw:
        try:
            parsed = json.loads(opts_raw)
            if isinstance(parsed, dict):
                cell_options = parsed
        except (json.JSONDecodeError, ValueError):
            cell_options = {}
    return {
        "widget_id": widget_id,
        "theme_id": theme_id,
        "style_id": style_id,
        "sample": sample_mode,
        "panel_id": panel_id,
        "panel_w": preset.w,
        "panel_h": preset.h,
        "panel_label": preset.label,
        "cell_options": cell_options,
        "opts_raw": opts_raw,
    }


@bp.get("/_test/preview")
def test_widget_preview() -> str:
    """Interactive single-widget preview as a synthetic composed page.

    Renders the chosen widget across a 7-cell layout that exercises
    every size bucket (lg / md / sm / xs) plus one unassigned cell so
    the reviewer can compare a populated cell against the empty
    placeholder side by side. The dropdown drives panel dimensions so
    the same widget can be eyeballed at every Tesserae-supported
    panel (Inky / Waveshare presets) without composing a real page.

    Dev-only, guarded behind ``debug or testing`` like every other
    ``/_test/...`` route. Cell options post via ``?opts=<json>``;
    panel via ``?panel=<preset_id>``."""
    if not (current_app.debug or current_app.testing):
        abort(404)
    widgets = sorted(_registry().widgets(), key=lambda p: p.name.lower())
    if not widgets:
        abort(404)
    parsed = _parse_preview_args()
    plugin = next((p for p in widgets if p.id == parsed["widget_id"]), None)
    if plugin is None:
        plugin = widgets[0]

    # ``cell_options`` from the plugin manifest drive the form-builder.
    # Each entry shape: ``{name, type, label, default?, choices?, secret?}``
    # , same schema the page editor reads. Defaults override URL-supplied
    # values only when the URL omits a field, so reloading the page with
    # an explicit blank still wins over the manifest default.
    schema = list(plugin.manifest.get("cell_options") or [])
    supplied_opts: dict[str, Any] = parsed["cell_options"]
    form_values: dict[str, Any] = {}
    for spec in schema:
        name = spec.get("name")
        if not isinstance(name, str):
            continue
        if name in supplied_opts:
            form_values[name] = supplied_opts[name]
        else:
            form_values[name] = spec.get("default", "")

    return render_template(
        "widget_preview.html",
        widget=plugin,
        widgets=widgets,
        themes=_MATRIX_THEMES,
        styles=_MATRIX_STYLES,
        panels=_PREVIEW_PANELS,
        theme_id=parsed["theme_id"],
        style_id=parsed["style_id"],
        panel_id=parsed["panel_id"],
        panel_w=parsed["panel_w"],
        panel_h=parsed["panel_h"],
        panel_label=parsed["panel_label"],
        sample=parsed["sample"],
        schema=schema,
        form_values=form_values,
        opts_json=json.dumps(supplied_opts) if supplied_opts else "",
    )


@bp.get("/_test/preview/page")
def test_widget_preview_page() -> str:
    """Iframe target for ``/_test/preview``: renders the synthetic
    multi-cell page through ``compose.html`` with preview-mode badges
    so the cells get their "1 · widget_id" tags. The parent
    ``widget_preview.html`` embeds this in a single iframe.

    Dev-only, same gate as ``/_test/render``."""
    if not (current_app.debug or current_app.testing):
        abort(404)
    parsed = _parse_preview_args()
    plugin = _registry().get(parsed["widget_id"])
    # No widget picked yet, surface a blank synthetic page rather than
    # 404. Cells stay unassigned so the reviewer sees seven empty
    # placeholders instead of an opaque error.
    widget_id = "" if plugin is None else plugin.id
    page = _build_preview_page(
        plugin_id=widget_id,
        panel_w=parsed["panel_w"],
        panel_h=parsed["panel_h"],
        theme_id=parsed["theme_id"],
        style_id=parsed["style_id"],
        cell_options=parsed["cell_options"],
    )
    return render_template(
        "compose.html",
        page=_hydrate_page(page, preview=True, sample=parsed["sample"]),
        for_push=False,
        preview_mode=True,
    )


@bp.get("/_test/widgets")
def test_widget_gallery() -> str:
    """All widgets at every supported size, iframed via /_test/render.

    Dev-only review surface, lets you scan every widget's render in
    one place so you can spot regressions or queue tweaks. Each iframe
    is lazy-loaded so opening the page doesn't fire 100+ widget fetches
    at once.
    """
    if not (current_app.debug or current_app.testing):
        abort(404)
    widgets = sorted(_registry().widgets(), key=lambda p: p.name.lower())
    rows = []
    for plugin in widgets:
        supported = plugin.manifest.get("supports", {}).get("sizes") or ["md"]
        sizes = [s for s in ("xs", "sm", "md", "lg") if s in supported]
        rows.append(
            {
                "id": plugin.id,
                "name": plugin.name,
                "description": plugin.manifest.get("description") or "",
                "icon": plugin.manifest.get("icon"),
                "version": plugin.manifest.get("version") or "",
                "sizes": sizes,
            }
        )
    return render_template(
        "widget_gallery.html",
        widgets=rows,
        size_dims=SIZE_DIMENSIONS,
    )
