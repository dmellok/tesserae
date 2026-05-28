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


def _resolved_options(plugin_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    plugin = _registry().get(plugin_id)
    if plugin is None:
        return dict(raw)
    merged: dict[str, Any] = plugin.cell_option_defaults()
    merged.update(raw)
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

    cells_out: list[dict[str, Any]] = []
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
        cells_out.append(
            {
                **cell,
                "x": cell["x"] + left_pad,
                "y": cell["y"] + top_pad,
                "w": max(1, cell["w"] - left_pad - right_pad),
                "h": max(1, cell["h"] - top_pad - bottom_pad),
                "plugin": plugin_id or "",
                "options": resolved_options,
                "data": (
                    _fetch_plugin_data(plugin_id, resolved_options, panel_w, panel_h, preview)
                    if plugin_id
                    else None
                ),
                "palette": cell_palette,
                "font_family": cell_font_family,
                "full_bleed": full_bleed,
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
    # page_dict["panel"] to always be present. A page-level panel
    # (legacy override) wins; otherwise we pull dims from settings.
    page_dict = page.model_dump(mode="json", exclude_none=True)
    settings_store = current_app.config["SETTINGS_STORE"]
    devices = current_app.config.get("DEVICE_REGISTRY")
    panel = resolve_panel_for_page(page, devices, settings_store)
    page_dict["panel"] = {"w": panel.w, "h": panel.h}
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

    cell_w, cell_h = SIZE_DIMENSIONS[size]
    page = {
        "id": "_test",
        "name": f"Test: {plugin_id} @ {size}",
        "panel": {"w": cell_w, "h": cell_h},
        "theme": "default",
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
    return render_template(
        "widget_gallery.html",
        widgets=rows,
        size_dims=SIZE_DIMENSIONS,
    )
