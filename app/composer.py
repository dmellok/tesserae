"""Composer: builds the page that Playwright screenshots and the editor previews.

Reads pages from the file-backed ``PageStore``, resolves theme + font references
through the plugin registry, emits ``@font-face`` rules for every loaded font,
and renders one ``<div class="cell">`` per cell with its resolved palette
already applied as CSS custom properties (``--theme-*``).

For plugins that ship a ``server.py``, the composer calls ``fetch()`` and
embeds the result as ``data-data`` on the cell so client.js receives it via
``ctx.data``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Blueprint, abort, current_app, render_template, request

from app.panel import resolve_panel_for_page
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


# Fallback palette used only if no theme plugin is loaded. Kept neutral so a
# broken / missing themes_core doesn't render as an obvious "default theme"
# branded look — it should just work and look plain.
_BUILTIN_DEFAULT_PALETTE: dict[str, str] = {
    "bg": "#ffffff",
    "surface": "#f5f5f5",
    "surface2": "#e8e8e8",
    "fg": "#1a1a1a",
    "fgSoft": "#555555",
    "muted": "#888888",
    "accent": "#3060c0",
    "accentSoft": "#2148a0",
    "divider": "#c8c8c8",
    "danger": "#c44a3a",
    "warn": "#c89028",
    "ok": "#3a8848",
}


def _registry() -> PluginRegistry:
    registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    return registry


# Last-resort coordinates (Melbourne) so a location widget still renders
# before any global or per-cell location is set.
_FALLBACK_LAT = -37.8136
_FALLBACK_LON = 144.9631


def _global_location() -> tuple[float, float]:
    """The global default coordinates from the app settings (Settings →
    Server), with a built-in fallback so widgets never render blank."""
    store = current_app.config.get("SETTINGS_STORE")
    app_cfg = store.get_section("app") if store is not None else {}

    def _f(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    return _f(app_cfg.get("latitude"), _FALLBACK_LAT), _f(app_cfg.get("longitude"), _FALLBACK_LON)


def _resolved_options(plugin_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    plugin = _registry().get(plugin_id)
    if plugin is None:
        return dict(raw)
    merged: dict[str, Any] = plugin.cell_option_defaults()
    merged.update(raw)
    # Fill blank latitude/longitude from the global location so the
    # weather / sky / sunrise widgets don't each re-enter coordinates. An
    # explicit per-cell value always wins (it's non-blank here).
    needs_lat = "latitude" in merged and merged["latitude"] in (None, "")
    needs_lon = "longitude" in merged and merged["longitude"] in (None, "")
    if needs_lat or needs_lon:
        glat, glon = _global_location()
        if needs_lat:
            merged["latitude"] = glat
        if needs_lon:
            merged["longitude"] = glon
    return merged


def _resolve_palette(theme_id: str | None, registry: PluginRegistry) -> dict[str, str]:
    if theme_id:
        theme = registry.get_theme(theme_id)
        if theme is not None:
            return theme.palette
    fallback = registry.get_theme("default")
    if fallback is not None:
        return fallback.palette
    return dict(_BUILTIN_DEFAULT_PALETTE)


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
# misbehaving widgets — a single hung fetch shouldn't sink the dashboard.
# Sized to fit inside the renderer's 15s page.goto budget: if hydration
# blows past goto's timeout, Playwright reports a broken navigation and
# the screenshot captures an empty page. Per-widget cap is the safety
# net; the overall cap is what actually fires when an upstream is dead.
_HYDRATE_PER_WIDGET_TIMEOUT_S: float = 10.0
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
# users found themselves doing manually. Now they don't have to —
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

    indexed: list[tuple[int, str, dict[str, Any]]] = []
    for idx, meta in enumerate(cells_meta):
        plugin_id = meta["plugin_id"]
        if not plugin_id:
            continue
        plugin = _registry().get(plugin_id)
        if plugin is None or plugin.server_module is None:
            continue
        if getattr(plugin.server_module, "fetch", None) is None:
            continue
        indexed.append((idx, plugin_id, meta["resolved_options"]))

    if not indexed:
        return {}

    # Capture the live Flask app object so worker threads can push
    # ``app.app_context()`` themselves — ``current_app`` is a thread-
    # local proxy and won't follow us off the request thread.
    app = current_app._get_current_object()  # type: ignore[attr-defined]

    def _worker(plugin_id: str, options: dict[str, Any]) -> Any:
        with app.app_context():
            return _fetch_plugin_data(plugin_id, options, panel_w, panel_h, preview)

    results: dict[int, Any] = {}
    # Cells whose result was synthesised by US (executor caught an
    # exception, or the future never completed before the overall
    # timeout) rather than returned by the widget's own ``fetch()``.
    # Only these are candidates for the last-good fallback — a widget
    # that legitimately returns something error-shaped (e.g.
    # ``{"connected": false, "error": "Spotify not connected"}``) is
    # providing real data and must NOT get overridden by a stale prior
    # result.
    synthesised_errors: set[int] = set()
    max_workers = min(_HYDRATE_MAX_WORKERS, len(indexed))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_worker, plugin_id, options): idx for idx, plugin_id, options in indexed
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

    # Last-good fallback. Walk each cell's result; if it came back from
    # ``fetch()`` cleanly (whatever its shape — including widget-
    # returned error states), stash it under its (plugin, options,
    # panel) key. If we synthesised the error (executor exception or
    # overall timeout), try to serve the previous successful result.
    for idx, plugin_id, options in indexed:
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
    return results


def _fetch_plugin_data(
    plugin_id: str,
    options: dict[str, Any],
    panel_w: int,
    panel_h: int,
    preview: bool,
) -> Any:
    """Call the plugin's server.py fetch() if present. Returns None on miss."""
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
    try:
        return fetch_fn(
            options,
            settings,
            ctx={
                "panel_w": panel_w,
                "panel_h": panel_h,
                "preview": preview,
                "data_dir": str(plugin.data_dir),
            },
        )
    except Exception as err:
        # Surface the failure to the cell instead of failing the whole render.
        logger.warning("plugin %s fetch() raised: %s", plugin_id, err)
        return {"error": f"{type(err).__name__}: {err}"}


def _hydrate_page(page_dict: dict[str, Any], *, preview: bool = False) -> dict[str, Any]:
    """Resolve options, themes, fonts, server-side data, and visual layout."""
    registry = _registry()
    page_palette = _resolve_palette(page_dict.get("theme"), registry)
    page_font = _resolve_font(page_dict.get("font"), registry)
    page_font_family = page_font.name if page_font else "system-ui"

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
    fitted = fit_cells_to_panel(raw_coords, panel_w, panel_h)
    page_dict = {
        **page_dict,
        "cells": [
            {**cell, "x": nx, "y": ny, "w": nw, "h": nh}
            for cell, (nx, ny, nw, nh) in zip(page_dict["cells"], fitted, strict=True)
        ],
    }

    # First pass: assemble the layout, palette, font, options for each
    # cell synchronously. These are all in-memory operations (registry
    # lookups + token resolution); the slow part — server-side widget
    # data fetches — is split out so it can run in parallel below.
    cells_meta: list[dict[str, Any]] = []
    for cell in page_dict["cells"]:
        cell_palette = dict(
            _resolve_palette(cell["theme"], registry) if cell.get("theme") else page_palette
        )
        for token, hex_value in (cell.get("palette_overrides") or {}).items():
            if isinstance(hex_value, str) and hex_value:
                cell_palette[token] = hex_value
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
                "palette": cell_palette,
                "font_family": cell_font_family,
                "full_bleed": full_bleed,
            }
        )

    # Second pass: fetch widget data in parallel. Before this, slow
    # upstreams (Open-Meteo, GitHub, …) added up — six 15s timeouts
    # in series is 90s, blowing past Playwright's navigation budget
    # and surfacing as a blank/timeout PNG. Workers share the Flask
    # app context the caller holds so each fetch can still read
    # SETTINGS_STORE / plugin registry from current_app.
    data_by_cell_index: dict[int, Any] = _parallel_fetch_plugin_data(
        cells_meta, panel_w, panel_h, preview
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
                "palette": meta["palette"],
                "font_family": meta["font_family"],
                "full_bleed": meta["full_bleed"],
            }
        )

    return {
        **page_dict,
        "cells": cells_out,
        "palette": page_palette,
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
    # Inject the resolved panel before hydrate — _hydrate_page expects
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
    """Mount one plugin into a known cell size — no Page needed.

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

    # Theme picker on the gallery passes ?theme=<id> so a reviewer can
    # eyeball every widget against any installed theme without saving
    # a page. Unknown ids fall back to default rather than 400ing —
    # keeps the gallery resilient when a theme gets renamed or removed
    # from the plugin manifest mid-session.
    theme_id = request.args.get("theme") or "default"
    theme_registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    if theme_id not in theme_registry.themes:
        theme_id = "default"

    cell_w, cell_h = SIZE_DIMENSIONS[size]
    page = {
        "id": "_test",
        "name": f"Test: {plugin_id} @ {size}",
        "panel": {"w": cell_w, "h": cell_h},
        "theme": theme_id,
        "font": "default",
        "cells": [
            {
                "id": "test-cell",
                "x": 0,
                "y": 0,
                "w": cell_w,
                "h": cell_h,
                "plugin": plugin_id,
                "options": {},
            }
        ],
    }
    return render_template(
        "compose.html",
        page=_hydrate_page(page, preview=True),
        for_push=False,
        preview_mode=False,
    )


@bp.get("/_test/widgets")
def test_widget_gallery() -> str:
    """All widgets at every supported size, iframed via /_test/render.

    Dev-only review surface — lets you scan every widget's render in
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
    # Theme picker lets a reviewer scan every widget against any
    # installed theme without saving a page. ``default`` always exists
    # (it's the seed theme themes_core ships with) and leads so the
    # gallery loads with the same look the composer defaults to.
    theme_registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    themes = sorted(
        ({"id": t.id, "name": t.name, "mode": t.mode} for t in theme_registry.themes.values()),
        key=lambda t: (t["id"] != "default", t["mode"], t["name"].lower()),
    )
    return render_template(
        "widget_gallery.html",
        widgets=rows,
        size_dims=SIZE_DIMENSIONS,
        themes=themes,
    )
