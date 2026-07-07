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

import json
import logging
from typing import Any, Final

from flask import Blueprint, abort, current_app, render_template, request

from app.panel import PANEL_PRESETS, resolve_panel_for_page
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
    location = merged.get("location")
    if not (isinstance(location, dict) and location):
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
                plugin_id, options, panel_w, panel_h, preview, cell_w=cell_w, cell_h=cell_h
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
        fallback = _LAST_GOOD_DATA.get(key)
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
    page_dict: dict[str, Any], *, preview: bool = False, sample: bool = False
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
        left_pad = outer_pad if cell["x"] == 0 else inner_pad
        top_pad = outer_pad if cell["y"] == 0 else inner_pad
        right_pad = outer_pad if cell["x"] + cell["w"] == panel_w else inner_pad
        bottom_pad = outer_pad if cell["y"] + cell["h"] == panel_h else inner_pad
        cells_meta.append(
            {
                "cell": cell,
                "plugin_id": plugin_id,
                "resolved_options": resolved_options,
                "layout": {
                    "x": cell["x"] + left_pad,
                    "y": cell["y"] + top_pad,
                    "w": max(1, cell["w"] - left_pad - right_pad),
                    "h": max(1, cell["h"] - top_pad - bottom_pad),
                },
                "font_family": cell_font_family,
                "full_bleed": full_bleed,
            }
        )

    # Second pass: fetch widget data in parallel. Before this, slow
    # upstreams (Open-Meteo, GitHub, …) added up, six 15s timeouts
    # in series is 90s, blowing past Playwright's navigation budget
    # and surfacing as a blank/timeout PNG. Workers share the Flask
    # app context the caller holds so each fetch can still read
    # SETTINGS_STORE / plugin registry from current_app.
    data_by_cell_index: dict[int, Any] = _parallel_fetch_plugin_data(
        cells_meta, panel_w, panel_h, preview, sample=sample
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
    page_dict["panel"] = {"w": panel_w, "h": panel_h}
    return render_template(
        "compose.html",
        page=_hydrate_page(page_dict, preview=not for_push),
        for_push=for_push,
        preview_mode=preview_mode,
    )


@bp.get("/_test/render")
def test_render() -> str:
    """Mount one plugin into a known cell size, no Page needed.

    Available when the app is in debug or testing mode. Used by the per-plugin
    smoke tests that ship with every widget.
    """
    if not (current_app.debug or current_app.testing):
        abort(404)

    plugin_id = request.args.get("plugin")
    if not plugin_id:
        abort(400)

    size = request.args.get("size", "md")
    if size not in SIZE_DIMENSIONS:
        abort(400)

    sample_mode = request.args.get("sample") == "1"
    # ?theme=<id> picks one of the Spectra theme blocks
    # (light / dark / high-contrast / sepia / nord / cool-gray, plus the
    # three movement themes bauhaus / destijl / brutalist).
    theme_id = request.args.get("theme") or "light"
    # ?style=<id> picks one of the orthogonal Spectra style blocks
    # (standard / display / editorial / mono / elegant / condensed, plus
    # the three movement styles bauhaus / destijl / brutalist).
    style_id = request.args.get("style") or "standard"

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
                "options": cell_options,
                "zoom": zoom_val,
            }
        ],
    }
    return render_template(
        "compose.html",
        page=_hydrate_page(page, preview=True, sample=sample_mode),
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
