"""Dashboard editor, single-page form-driven editor with auto-save.

The /pages/<id> endpoint serves the whole editor: page metadata, layout
picker, every cell's options, and the live preview iframe. The client
auto-saves any form change via fetch POST to the same endpoints used
for full-page submits, then asks the preview iframe to reload.

Cells whose ``plugin`` is None are valid, they're slots a layout
template created that the user hasn't filled in yet. The composer
renders them as a placeholder; the editor shows a plugin dropdown.

URLs:

  GET  /pages                          page list
  GET  /pages/new                      new-page form
  POST /pages/new                      create page (optionally seeds
                                       cells from a layout template)
  GET  /pages/<id>                     editor (single page)
  POST /pages/<id>                     update page metadata (autosave)
  POST /pages/<id>/delete              delete the page
  POST /pages/<id>/layout              apply a layout template
  POST /pages/<id>/cells               add an empty cell at the end
  POST /pages/<id>/cells/<cid>         update one cell (autosave)
  POST /pages/<id>/cells/<cid>/delete  remove one cell
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app.composer import _hydrate_page
from app.layouts import LAYOUTS, LAYOUTS_BY_SLUG, detect_layout, to_panel_pixels
from app.panel import (
    fit_cells_to_panel,
    preview_groups_for_page,
    resolve_panel_for_page,
    resolve_settings_panel,
)
from app.plugin_loader import Plugin, PluginRegistry
from app.state.page_store import Cell, Page, PageStore
from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)


bp = Blueprint("pages", __name__, url_prefix="/pages")


def _store() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _plugins() -> PluginRegistry:
    return current_app.config["PLUGIN_REGISTRY"]  # type: ignore[no-any-return]


def _settings_store() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _devices() -> Any:
    """Device registry (or None if no devices/ dir was loaded). Used by
    resolve_panel_for_page so a page can inherit its device's panel."""
    return current_app.config.get("DEVICE_REGISTRY")


# -- touch Interaction picker data (issue #49) ---------------------------
# The grid editor's per-cell Interaction controls need the same picker
# data the canvas editor uses (all dashboards for "go to page", HA
# services + entities). The canvas editor's equivalents live on the
# composer-gated panels blueprint; these twins are ungated so the grid
# editor works without the composer experiment.


@bp.get("/dashboards.json")
def dashboards_json() -> Response:
    """All saved dashboards as ``{id, name, kind}`` for the "go to page"
    touch-action picker."""
    rows = [
        {"id": p.id, "name": p.name or p.id, "kind": p.layout_kind or "grid"}
        for p in _store().list()
    ]
    rows.sort(key=lambda r: str(r["name"]).lower())
    return jsonify({"pages": rows})


@bp.get("/ha-actions.json")
def ha_actions_json() -> Response:
    """Home Assistant services + entities for the HA touch-action form.
    ``{"configured": false}`` when HA isn't set up."""
    from app.ha_actions import fetch_ha_actions

    return jsonify(fetch_ha_actions())


def _clean_device_ids(raw: list[str]) -> list[str]:
    """Keep only known registered-instance ids from the form, deduped in
    order. Drops unknown ids and built-in kinds so a page can't bind to
    something unbindable."""
    registry = _devices()
    if registry is None:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for did in raw:
        device = registry.devices.get(did)
        if device is None or device.kind_of is None or did in seen:
            continue
        seen.add(did)
        out.append(did)
    return out


def _random_page_id(taken: set[str]) -> str:
    """An opaque random id for a new page. Decoupled from the name on
    purpose, the id is a stable storage key + URL + schedule target, so
    it must never change when the user renames. It's hidden from the UI,
    so there's nothing to gain from a readable slug (and a name-derived
    slug just goes stale, e.g. 'untitled_dashboard_2')."""
    while True:
        pid = uuid.uuid4().hex[:12]
        if pid not in taken:
            return pid


_COPY_SUFFIX_RE = re.compile(r"\s+copy(?:\s+\d+)?$")


def _duplicate_name(original: str, taken: set[str]) -> str:
    """Compute the name for a duplicated page: ``"<original> copy"`` when
    the slot's free, else ``"<original> copy 2"``, ``"<original> copy 3"``,
    etc. on collision.

    A trailing ``" copy"`` or ``" copy N"`` is stripped off the source name
    first, so duplicating ``"Home copy"`` lands on ``"Home copy 2"`` rather
    than stacking to ``"Home copy copy"``. Matches the Finder / Google Docs
    convention. Falls back to a random suffix only after a hundred
    collisions (effectively never)."""
    stripped = _COPY_SUFFIX_RE.sub("", original).rstrip()
    base = f"{stripped} copy"
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base} {n}"
        if candidate not in taken:
            return candidate
    return f"{base} {uuid.uuid4().hex[:6]}"


def _coerce_float(
    raw: str | None, default: float, *, lo: float | None = None, hi: float | None = None
) -> float:
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _coerce_int(
    raw: str | None, default: int, *, lo: int | None = None, hi: int | None = None
) -> int:
    if raw is None or raw == "":
        value = default
    else:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


def _coerce_cell_option(field: dict[str, Any], raw: str | None, all_form: Any) -> Any:
    ftype = field.get("type", "string")
    if ftype == "boolean":
        return f"opt_{field['name']}" in all_form
    if ftype == "number" or ftype == "slider":
        if raw is None or raw == "":
            return field.get("default")
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return field.get("default")
    if ftype == "select":
        if raw is None:
            return field.get("default")
        for choice in field.get("choices", []):
            if str(choice.get("value")) == raw:
                return choice["value"]
        return raw
    if ftype == "multiselect":
        # Persist path: ``all_form`` is a Werkzeug MultiDict (getlist gives
        # every checked box). Preview path: ``all_form`` is a plain dict
        # where the demux already promoted >1 values to a list, but a
        # single check stays a scalar and zero checks is absent. Normalise
        # all three to a clean list of non-empty strings.
        key = f"opt_{field['name']}"
        if hasattr(all_form, "getlist"):
            values: Any = all_form.getlist(key)
        elif isinstance(raw, list):
            values = raw
        elif raw in (None, ""):
            values = []
        else:
            values = [raw]
        return [str(v) for v in values if str(v).strip()]
    if ftype == "color":
        if raw and raw.startswith("#"):
            return raw
        return field.get("default", "")
    if ftype == "location_search":
        # Stored as a JSON-encoded dict in the hidden input. The shape
        # mirrors Open-Meteo's geocoding response:
        #   {name, country, admin1, latitude, longitude}
        # Empty / malformed strings fall back to {}, not the manifest
        # default (which is "" by convention; we want a real dict so
        # downstream code can do ``loc.get("latitude")`` without a
        # type check).
        if raw is None or raw == "":
            return {}
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(parsed, dict):
            return {}
        # Extract just the fields we care about and coerce types, so a
        # malicious / drifted payload can't slide unexpected keys
        # through to the renderer.
        out_loc: dict[str, Any] = {}
        for key in ("name", "country", "admin1"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                out_loc[key] = val.strip()
        import contextlib

        for key in ("latitude", "longitude"):
            val = parsed.get(key)
            if val is None:
                continue
            # missing or malformed coord, just omit it; ``_resolved_options``
            # then falls back to the global location or constants.
            with contextlib.suppress(TypeError, ValueError):
                out_loc[key] = float(val)
        return out_loc
    return raw if raw is not None else ""


def _cell_options_from_form(plugin: Plugin, form: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for opt in plugin.manifest.get("cell_options", []):
        name = str(opt["name"])
        raw = form.get(f"opt_{name}")
        out[name] = _coerce_cell_option(opt, raw, form)
    return out


def _flash_save(ok: bool, message: str) -> Response:
    """Return value for autosave endpoints: JSON for fetch callers, a
    redirect for native form submits (e.g. JS-disabled fallback)."""
    is_xhr = (request.headers.get("X-Requested-With") or "").lower() == "fetch"
    if is_xhr:
        return jsonify({"ok": ok, "message": message})
    flash(message, "ok" if ok else "error")
    return redirect(request.referrer or url_for("pages.index"))


# -- helpers for the editor view -----------------------------------


def _new_cell(*, x: int, y: int, w: int, h: int) -> Cell:
    return Cell(id=uuid.uuid4().hex[:8], plugin=None, x=x, y=y, w=w, h=h, options={})


def _status_bar_cell_index(page: Page) -> int | None:
    """Return the index of the auto-managed status bar cell within
    ``page.cells``, or None when the bar is off / the id doesn't
    resolve. Used to tell ``fit_cells_to_panel`` which cell is the
    orientation-fixed top strip (v0.71.1: without this, a page bound
    to a portrait panel rotates the top strip onto the right edge)."""
    if not page.status_bar_enabled or not page.status_bar_cell_id:
        return None
    for i, cell in enumerate(page.cells):
        if cell.id == page.status_bar_cell_id:
            return i
    return None


# v0.71.0: fixed bar height used when auto-inserting the status_bar
# cell. Matches the design handoff's "bar mode: 48 px tall" absolute
# measure; e-ink panels vary in resolution but 48 px reads well on
# every tier-1 panel we support (200 dpi Spectra 6, mono Inky4,
# TRMNL). If a panel is shorter than about 200 px this would eat too
# much of the layout, so the toggle refuses when the panel would end
# up with less than STATUS_BAR_MIN_REMAINING_PX for the rest of the
# cells.
STATUS_BAR_HEIGHT_PX = 48
STATUS_BAR_MIN_REMAINING_PX = 120


def _rescale_cells_below_bar(
    cells: list[Cell], panel_h: int, bar_h: int, direction: str
) -> list[Cell]:
    """Rescale existing cells to make room for (``direction="down"``)
    or reclaim room from (``direction="up"``) the status bar.

    ``direction="down"`` (toggling on): existing cells originally span
    (0, panel_h); after inserting the bar they need to fit in
    (bar_h, panel_h). y_new = bar_h + y_old * scale, h_new = h_old *
    scale, where scale = (panel_h - bar_h) / panel_h.

    ``direction="up"`` (toggling off): reverse. y_new = (y_old - bar_h)
    / scale, h_new = h_old / scale.
    """
    if direction not in ("down", "up"):
        raise ValueError(f"bad direction {direction!r}")
    if panel_h <= 0:
        return cells
    scale = (panel_h - bar_h) / panel_h
    if scale <= 0:
        return cells
    out: list[Cell] = []
    for cell in cells:
        if direction == "down":
            new_y = bar_h + round(cell.y * scale)
            new_h = max(1, round(cell.h * scale))
        else:
            new_y = max(0, round((cell.y - bar_h) / scale))
            new_h = max(1, round(cell.h / scale))
        out.append(cell.model_copy(update={"y": new_y, "h": new_h}))
    return out


def _refit_after_status_bar_removal(cells: list[Cell], *, panel_w: int, panel_h: int) -> list[Cell]:
    """After removing the auto-managed status bar cell, project the
    remaining cells onto the full panel via ``fit_cells_to_panel`` so
    they absorb the vacated top band.

    Two-step: shift cells up so their min-y sits at 0 (removes the
    top gap left by the bar), then fit-to-panel scales the rest to
    fill the height. Simple reverse-rescale doesn't do this when the
    layout has been edited since enabling (cells resized, moved).
    """
    if not cells:
        return []
    coords = [(c.x, c.y, c.w, c.h) for c in cells]
    min_y = min(y for _, y, _, _ in coords)
    shifted = [(x, y - min_y, w, h) for (x, y, w, h) in coords]
    fitted = fit_cells_to_panel(shifted, panel_w, panel_h)
    return [
        cell.model_copy(update={"x": nx, "y": ny, "w": nw, "h": nh})
        for cell, (nx, ny, nw, nh) in zip(cells, fitted, strict=True)
    ]


def _default_status_bar_options(page_name: str) -> dict[str, Any]:
    """Sensible defaults for the auto-inserted status_bar cell. Match
    the widget's plugin.json default values so the first paint looks
    the same as the design handoff (dark panelBg, bar mode, icon+text
    chips, common ambient stats on).

    v0.72.0: ``check_for_updates`` flipped on by default. Enabling the
    status bar is an implicit opt-in to the update-indicator chip; the
    fetch is rate-limited to once per hour and only paints when the
    latest release is strictly newer than the running build."""
    return {
        "mode": "bar",
        "chipMode": "icon-text",
        "dashboardName": "",
        "leadingIcon": True,
        "panelBg": "#1B1A16",
        "show_time": True,
        "time_format": "24h",
        "show_temperature": True,
        "show_humidity": True,
        "units": "metric",
        "show_battery": True,
        "show_wifi": True,
        "show_broker": True,
        "broker_label": "HA",
        "check_for_updates": True,
        "show_firmware_updates": True,
    }


def _apply_layout_to_cells(layout_slug: str, page: Page) -> list[Cell]:
    """Build a new cells list by reusing existing cells (plugin + options)
    in order and repositioning them to match the layout's slots. Extra
    slots get unassigned cells; surplus existing cells are dropped.

    When ``page.status_bar_enabled`` is true, the auto-managed status
    bar cell is preserved (kept at (0, 0, panel.w, STATUS_BAR_HEIGHT_PX))
    and the preset's cells are rescaled to fit into the space below it
    so switching layouts doesn't drop the status bar or shove it into
    a random slot."""
    layout = LAYOUTS_BY_SLUG.get(layout_slug)
    if layout is None:
        raise ValueError(f"unknown layout {layout_slug!r}")
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    bar_cell = None
    reusable = list(page.cells)
    if page.status_bar_enabled and page.status_bar_cell_id:
        bar_cell = next((c for c in page.cells if c.id == page.status_bar_cell_id), None)
        if bar_cell is not None:
            reusable = [c for c in reusable if c.id != bar_cell.id]
    positions = to_panel_pixels(layout, panel.w, panel.h)
    if bar_cell is not None:
        # Rescale the preset's positions into (STATUS_BAR_HEIGHT_PX, panel.h).
        bar_h = STATUS_BAR_HEIGHT_PX
        remaining = max(1, panel.h - bar_h)
        scale = remaining / panel.h
        positions = [
            (x, bar_h + round(y * scale), w, max(1, round(h * scale))) for (x, y, w, h) in positions
        ]
    out: list[Cell] = []
    for i, (x, y, w, h) in enumerate(positions):
        if i < len(reusable):
            existing = reusable[i]
            out.append(existing.model_copy(update={"x": x, "y": y, "w": w, "h": h}))
        else:
            out.append(_new_cell(x=x, y=y, w=w, h=h))
    if bar_cell is not None:
        # Re-anchor the bar to full-width + fixed height in case the panel
        # size changed since it was last positioned.
        anchored = bar_cell.model_copy(
            update={"x": 0, "y": 0, "w": panel.w, "h": STATUS_BAR_HEIGHT_PX}
        )
        out.insert(0, anchored)
    return out


def _materialize_cell_options(plugins: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """For each widget plugin, return its cell_options spec with any
    ``choices_from`` fields swapped to concrete ``choices`` lists by
    calling the plugin's ``choices(name)`` function.

    Plugins that don't expose ``choices`` (or that raise) get an empty
    list, the editor will render an empty dropdown rather than break.

    Exception: ``SecretBoxError`` (the plugin's settings include a
    secret-marked field that can't be decrypted with the current
    SecretBox key) gets a single sentinel choice with a label
    explaining the user needs to re-enter the secret. The bare
    empty-list fallback silently rendered an empty picker which was
    impossible to diagnose from the UI, see issue #29."""
    from app.secret_box import SecretBoxError

    out: dict[str, list[dict[str, Any]]] = {}
    for plugin in plugins:
        options = list(plugin.manifest.get("cell_options", []))
        resolver = getattr(plugin.server_module, "choices", None) if plugin.server_module else None
        materialized: list[dict[str, Any]] = []
        plugin_label = str(plugin.manifest.get("name") or plugin.id)
        for opt in options:
            spec = dict(opt)
            source = spec.pop("choices_from", None)
            if source and callable(resolver):
                try:
                    spec["choices"] = list(resolver(source))
                except SecretBoxError:
                    logger.warning(
                        "plugin %s: choices(%r) hit SecretBoxError, the stored "
                        "secret can't be decrypted with the current key; "
                        "surfacing a re-enter sentinel",
                        plugin.id,
                        source,
                    )
                    spec["choices"] = [
                        {
                            "value": "",
                            "label": (
                                f"Stored secret for {plugin_label} can't be "
                                "decrypted, re-enter it under Settings → "
                                f"Widgets → {plugin_label}"
                            ),
                        }
                    ]
                except Exception:
                    logger.exception(
                        "plugin %s: choices(%r) raised; rendering as empty", plugin.id, source
                    )
                    spec["choices"] = []
            materialized.append(spec)
        out[plugin.id] = materialized
    return out


def _ensure_cells_fit_panel(page: Page, panel: Any) -> Page:
    """If the saved cell coords genuinely don't fit the current panel,
    auto-rotate and rescale them to the new panel, persist, and return
    the updated page.

    "Don't fit" means:

    * Orientation flip — the design was laid out in landscape but the
      current panel is portrait (or vice-versa). A 90° rotation is
      required for the coords to make geometric sense.
    * Any cell overflows the panel — ``x + w > panel.w`` or
      ``y + h > panel.h``. Without a rescale those cells would render
      with clipped edges.

    Just changing panel dimensions (e.g. binding a second device with
    a different aspect ratio in the same orientation) used to
    non-uniformly rescale every cell here, and the round-trip through
    that scaling destroys edge alignment — adjacent cells gain gaps
    or overlap, the layout editor's edge-detection loses the handles,
    and the layout has to be rebuilt by hand. With this branch the
    canonical saved coords stay intact; render-time scaling via
    ``fit_cells_to_panel`` still runs on push, and the user can
    manually call POST /<page>/refit if they actually want the saved
    coords rescaled to a new panel.
    """
    if not page.cells:
        return page
    coords = [(c.x, c.y, c.w, c.h) for c in page.cells]
    design_w = max(x + w for x, y, w, h in coords)
    design_h = max(y + h for x, y, w, h in coords)
    in_bounds = all(x + w <= panel.w and y + h <= panel.h for x, y, w, h in coords)
    orientation_changed = (design_w >= design_h) != (panel.w >= panel.h)
    if in_bounds and not orientation_changed:
        return page
    top_strip_index = _status_bar_cell_index(page)
    fitted = fit_cells_to_panel(coords, panel.w, panel.h, top_strip_index=top_strip_index)
    if fitted == coords:
        return page
    new_cells = [
        cell.model_copy(update={"x": nx, "y": ny, "w": nw, "h": nh})
        for cell, (nx, ny, nw, nh) in zip(page.cells, fitted, strict=True)
    ]
    updated = page.model_copy(update={"cells": new_cells})
    _store().save(updated)
    return updated


def _editor_context(page: Page) -> dict[str, Any]:
    """Shared context for the editor."""
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    page = _ensure_cells_fit_panel(page, panel)
    panel_cells = [(c.x, c.y, c.w, c.h) for c in page.cells]
    active_layout = detect_layout(panel_cells, panel.w, panel.h)
    widgets = sorted(_plugins().widgets(), key=lambda p: p.name.lower())
    plugins = _plugins()

    # Cells in a JSON-safe shape for the interactive layout editor JS.
    layout_editor_cells = [
        {"id": c.id, "x": c.x, "y": c.y, "w": c.w, "h": c.h, "plugin": c.plugin} for c in page.cells
    ]

    # Device picker options. Only user-registered instances qualify -
    # binding a page to a built-in kind is ambiguous (the kind is a
    # template, not a physical display) and instances are also the
    # only thing the user explicitly created in Settings → Devices.
    # Anything without a panel block is skipped (the editor wouldn't
    # know what size to render at).
    device_registry = _devices()
    selected = set(page.device_ids)
    device_options: list[dict[str, Any]] = []
    if device_registry is not None:
        for dev in sorted(device_registry.devices.values(), key=lambda d: d.name.lower()):
            if dev.kind_of is None:
                continue  # built-in kind, not a bindable target
            if dev.panel is None:
                continue
            device_options.append(
                {
                    "id": dev.id,
                    "name": dev.name,
                    "icon": dev.icon,
                    # display_name collapses the auto "Kind (id)" default
                    # to just the id; dims stay so same-panel instances
                    # remain tellable apart.
                    "label": dev.display_name,
                    "dims": f"{dev.panel['w']}×{dev.panel['h']}",
                    "w": int(dev.panel["w"]),
                    "h": int(dev.panel["h"]),
                    "checked": dev.id in selected,
                }
            )

    # One preview per distinct aspect ratio among the selected devices
    # (or a single virtual-panel preview when none are selected). Each
    # carries its own dims so the preview iframe renders at that aspect
    # and the layout grid scales correctly.
    preview_groups = [
        {**g, "scale": _preview_scale(g["w"], g["h"])}
        for g in preview_groups_for_page(page, device_registry, _settings_store())
    ]

    # Theme picker options come from the central registry so the page
    # editor's "Theme" dropdown stays in lockstep with what's actually
    # available (bundled + later, user-saved). Previously hardcoded
    # twice in the template, once for the page-level picker and once
    # for the per-cell override, which drifted on every theme add.
    from app.state.theme_registry import build_registry, picker_options

    # Pull user-saved themes (data/themes/user.json) and any community
    # themes installed via the marketplace into the registry so the
    # editor's picker lists them alongside the bundled themes. Without
    # this the picker silently drops every non-bundled theme even
    # though the cascade is loading their CSS.
    user_themes_store = current_app.config.get("USER_THEMES_STORE")
    user_themes = (
        [t.to_registry_theme() for t in user_themes_store.list_all()]
        if user_themes_store is not None
        else None
    )
    community_themes_store = current_app.config.get("COMMUNITY_THEMES_STORE")
    community_themes = (
        [t.to_registry_theme() for t in community_themes_store.list_all()]
        if community_themes_store is not None
        else None
    )
    # Honour the per-user "hide this theme from the picker" list
    # (settings.app.disabled_theme_ids). Themes the user opted out of
    # disappear from BOTH the page-level select and the per-cell
    # override picker. The cascade still loads every CSS block, so
    # pages already bound to a disabled theme keep rendering — only
    # the pickers narrow.
    disabled_ids_raw = _settings_store().get_section("app").get("disabled_theme_ids") or []
    disabled_ids = (
        {str(x) for x in disabled_ids_raw if isinstance(x, str)}
        if isinstance(disabled_ids_raw, list)
        else set()
    )
    theme_options = picker_options(
        build_registry(user_themes=user_themes, community_themes=community_themes),
        disabled_ids=disabled_ids,
    )

    # Schedules pinned to this page. The editor renders them in a card
    # under the live preview so you can see (and create) what fires
    # this dashboard without leaving the composer. The full schedules
    # page stays the canonical edit / day-mask / smart-sync surface.
    schedule_store = current_app.config.get("SCHEDULE_STORE")
    schedules_for_page = (
        [s for s in schedule_store.all() if s.page_id == page.id]
        if schedule_store is not None
        else []
    )

    return {
        "page": page,
        "panel": panel,
        "plugins": widgets,
        "plugin_cell_options": _materialize_cell_options(widgets),
        "fonts": sorted(plugins.fonts.values(), key=lambda f: f.name.lower()),
        "layouts": LAYOUTS,
        "active_layout": active_layout.slug if active_layout else None,
        "preview_scale": _preview_scale(panel.w, panel.h),
        "preview_groups": preview_groups,
        "layout_editor_cells": layout_editor_cells,
        "device_options": device_options,
        "theme_options": theme_options,
        "schedules_for_page": schedules_for_page,
    }


def _preview_scale(panel_w: int, panel_h: int = 0) -> float:
    """Pick a scale that fits both axes inside a roughly 720x720 box so
    portrait panels don't blow the editor column out vertically."""
    target = 720.0
    sw = target / panel_w if panel_w > target else 1.0
    sh = target / panel_h if panel_h and panel_h > target else 1.0
    return min(sw, sh)


# -- page-level routes --------------------------------------------


def _group_pages_for_index(pages: list[Page], devices: Any) -> list[tuple[Any, list[Page]]]:
    """Bucket pages under every bound (still-existing) device for the
    Dashboards list. A page bound to N live devices appears in N groups,
    once under each device's section head. Returns ordered ``(device_or_None,
    pages)`` tuples: bound device groups sorted by ``display_name``
    (case-insensitive), then an "Unbound" group at the end when there
    are unbound pages. Pages within each group are alphabetical by name
    (case-insensitive, tie-break on id for stability). Empty groups are
    dropped, so a device with zero bound pages never renders a section
    head.

    Pre-v0.69.6 (issue #52 item 1): a page went to its FIRST live device
    only, so a dashboard bound to "living-room" + "kitchen" appeared
    once under whichever sorted first and never surfaced from the other
    device's section. The current shape mirrors the behaviour the user
    expects: each device's section head owns every dashboard pushed to
    it, with no hidden "primary" concept.

    Half-deleted bindings (``device_ids=["gone", "kitchen"]``) still
    resolve down to the live subset; a page with no live bindings at
    all goes to Unbound.

    Pulled out of ``index()`` so unit tests can hit the grouping logic
    without standing up a full Flask app + Page store."""
    bound: dict[str, tuple[Any, list[Page]]] = {}
    unbound: list[Page] = []
    for page in pages:
        matched: list[Any] = []
        if devices is not None:
            for did in page.device_ids:
                candidate = devices.devices.get(did)
                if candidate is not None:
                    matched.append(candidate)
        if not matched:
            unbound.append(page)
            continue
        for device in matched:
            slot = bound.setdefault(device.id, (device, []))
            slot[1].append(page)

    def page_sort_key(p: Page) -> tuple[str, str]:
        return (p.name.casefold(), p.id)

    out: list[tuple[Any, list[Page]]] = []
    for _device_id, (device, pages_in_group) in sorted(
        bound.items(), key=lambda kv: (kv[1][0].display_name.casefold(), kv[0])
    ):
        pages_in_group.sort(key=page_sort_key)
        out.append((device, pages_in_group))
    if unbound:
        unbound.sort(key=page_sort_key)
        out.append((None, unbound))
    return out


def _humanise_age(seconds: float) -> str:
    """Compact relative-time formatter for the dashboards list. Same
    flavour as the device-card freshness text: "2 min ago",
    "yesterday", "3 days ago"."""
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)} min ago"
    if seconds < 86400:
        hrs = int(seconds / 3600)
        return f"{hrs} hour{'' if hrs == 1 else 's'} ago"
    if seconds < 172_800:
        return "yesterday"
    if seconds < 30 * 86400:
        return f"{int(seconds / 86400)} days ago"
    if seconds < 365 * 86400:
        return f"{int(seconds / (30 * 86400))} months ago"
    return f"{int(seconds / (365 * 86400))} years ago"


@bp.get("")
def index() -> str:
    pages = _store().list()
    devices = _devices()
    settings = _settings_store()
    # Resolve each page's panel so the list can show its size (a page
    # bound to a device inherits that device's panel; otherwise the
    # virtual panel from settings).
    page_dims = {p.id: resolve_panel_for_page(p, devices, settings) for p in pages}

    def _device_names(page: Page) -> list[str]:
        """Friendly display names of the devices a page targets (skipping
        any that no longer exist). Empty = unbound / virtual panel."""
        if devices is None:
            return []
        names: list[str] = []
        for did in page.device_ids:
            device = devices.devices.get(did)
            if device is not None:
                names.append(device.display_name)
        return names

    page_devices = {p.id: _device_names(p) for p in pages}
    page_groups = _group_pages_for_index(pages, devices)
    # "Last pushed" per page for the redesigned Dashboards list. One
    # SQL roundtrip aggregates MAX(timestamp) per target across every
    # successful push row; targets with no row are absent (and
    # surface as "never" in the template).
    event_log = current_app.config.get("EVENT_LOG")
    page_last_pushed: dict[str, float] = {}
    if event_log is not None:
        try:
            page_last_pushed = event_log.last_event_by_target(
                type="push", targets=[p.id for p in pages]
            )
        except Exception:
            current_app.logger.exception(
                "event_log: last_event_by_target failed; rendering without last_pushed"
            )
    page_last_pushed_rel = {
        pid: _humanise_age(time.time() - ts) for pid, ts in page_last_pushed.items()
    }
    from app import experiments
    from app.composer import page_preview_token, preview_dims

    # Content token per page, so the hover preview's <img> URL changes only
    # when the dashboard changes (and otherwise reuses the cached render).
    page_preview_tokens = {
        p.id: page_preview_token(p, preview_dims(p, devices, settings)) for p in pages
    }

    return render_template(
        "pages_list.html",
        pages=pages,
        page_dims=page_dims,
        page_devices=page_devices,
        page_groups=page_groups,
        page_last_pushed_rel=page_last_pushed_rel,
        page_preview_tokens=page_preview_tokens,
        composer_enabled=experiments.is_enabled("composer"),
    )


@bp.get("/new")
def new() -> Response:
    """Visiting /new in a browser short-circuits straight to a freshly
    created dashboard so the user lands in the editor without a
    metadata form to fill in first."""
    return create()


@bp.post("/new")
def create() -> Response:
    """Create a saved dashboard and drop the user into the editor.

    The id is auto-generated from the name (slug + numeric suffix on
    collision); the user never sees it as a form input. The form is
    optional, if no fields are POSTed (e.g. the "New dashboard" button
    in the nav) we seed an "Untitled" 1-cell dashboard so the editor is
    the only place the user has to think.

    Panel dims come from app settings, pages aren't panel-specific."""
    form = request.form
    name = (form.get("name") or "").strip() or "Untitled dashboard"

    taken = {p.id for p in _store().list()}
    page_id = _random_page_id(taken)

    # Freeform (canvas) dashboards skip the grid layout entirely and drop the
    # user into the composer. Gated by the composer experiment so the option
    # only reaches installs that have it enabled.
    from app import experiments

    if (form.get("layout_kind") or "grid").strip() == "canvas" and experiments.is_enabled(
        "composer"
    ):
        from app.state.panel_store import CanvasLayout

        page = Page(
            id=page_id,
            name=name,
            layout_kind="canvas",
            theme=(form.get("theme") or "light"),
            style=(form.get("style") or "standard"),
            font=(form.get("font") or None),
            canvas=CanvasLayout(),
        )
        _store().save(page)
        return redirect(url_for("panels.editor", canvas_id=page.id))

    # Panel dims come from app settings; the new page inherits them.
    panel = resolve_settings_panel(_settings_store())
    layout_slug = (form.get("layout") or "1_cell").strip()
    layout = LAYOUTS_BY_SLUG.get(layout_slug, LAYOUTS_BY_SLUG["1_cell"])
    initial_cells = [
        _new_cell(x=x, y=y, w=w, h=h) for x, y, w, h in to_panel_pixels(layout, panel.w, panel.h)
    ]

    try:
        page = Page(
            id=page_id,
            name=name,
            # panel left None on purpose, derived from settings at render
            # time so changing the panel in settings updates every page.
            cells=initial_cells,
            font=(form.get("font") or None),
            theme=(form.get("theme") or "light"),
            style=(form.get("style") or "standard"),
            gap=_coerce_int(form.get("gap"), 0, lo=0),
            corner_radius=_coerce_int(form.get("corner_radius"), 0, lo=0),
            bleed_color=(form.get("bleed_color") or ""),
        )
    except ValidationError as exc:
        flash(f"Could not create dashboard: {exc.errors()[0]['msg']}", "error")
        return redirect(url_for("pages.index"))

    _store().save(page)
    return redirect(url_for("pages.edit", page_id=page.id))


@bp.get("/<page_id>")
def edit(page_id: str) -> Response | str:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    # Freeform dashboards open in the composer, not the grid editor, so any
    # /pages/<id> link (list card, deep link) reaches the right editor.
    if page.layout_kind == "canvas":
        return redirect(url_for("panels.editor", canvas_id=page.id))
    return render_template("page_editor.html", **_editor_context(page))


@bp.post("/<page_id>")
def update(page_id: str) -> Response:
    """Update page metadata. Panel dims come from settings, not from
    the form, the editor no longer exposes them.

    Merge, don't replace: only fields actually present in the POST are
    updated; absent fields keep their stored value. The editor posts two
    *separate* forms to this endpoint, the header rename form (just
    ``name``) and the dashboard form (font / icon / device_ids / …). If
    absent fields were reset to defaults, the rename form would wipe the
    device bindings (and vice-versa), so each must touch only what it
    carries."""
    page = _store().get(page_id)
    if page is None:
        abort(404)
    form = request.form
    updates: dict[str, Any] = {}
    if "name" in form:
        updates["name"] = (form.get("name") or page.name).strip() or page.name
    if "theme" in form:
        updates["theme"] = form.get("theme") or "light"
    if "style" in form:
        updates["style"] = form.get("style") or "standard"
    if "font" in form:
        updates["font"] = form.get("font") or None
    if "refresh_minutes" in form:
        updates["refresh_minutes"] = _coerce_int(
            form.get("refresh_minutes"), page.refresh_minutes, lo=0, hi=1440
        )
    if "gap" in form:
        updates["gap"] = _coerce_int(form.get("gap"), page.gap, lo=0)
    if "corner_radius" in form:
        updates["corner_radius"] = _coerce_int(form.get("corner_radius"), page.corner_radius, lo=0)
    if "bleed_color" in form:
        updates["bleed_color"] = form.get("bleed_color") or page.bleed_color
    if "icon" in form:
        updates["icon"] = (form.get("icon") or None) or None
    # Device checkboxes vanish from the POST when all are unticked, so a
    # hidden ``device_ids_present`` sentinel marks the form that owns the
    # device list. That lets the dashboard form clear it to [] while the
    # name-only rename form leaves the bindings untouched.
    if "device_ids_present" in form or "device_ids" in form:
        updates["device_ids"] = _clean_device_ids(form.getlist("device_ids"))
    try:
        updated = page.model_copy(update=updates)
    except ValidationError as exc:
        return _flash_save(False, f"Invalid page: {exc.errors()[0]['msg']}")
    _store().save(updated)
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    return _flash_save(True, "Page saved.")


@bp.post("/<page_id>/delete")
def delete(page_id: str) -> Response:
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    if _store().delete(page_id):
        flash("Page deleted.", "ok")
    else:
        flash(f"No page with id {page_id!r}.", "error")
    return redirect(url_for("pages.index"))


@bp.post("/bulk/delete")
def bulk_delete() -> Response:
    """Delete several dashboards at once (multi-select on the Dashboards page).
    Body: repeated ``page_ids`` form fields. Deletes each and flashes a count."""
    ids = [i.strip() for i in request.form.getlist("page_ids") if i.strip()]
    cache = current_app.config.get("PREVIEW_CACHE", {})
    deleted = 0
    for pid in ids:
        cache.pop(pid, None)
        if _store().delete(pid):
            deleted += 1
    if deleted:
        flash(f"Deleted {deleted} dashboard{'s' if deleted != 1 else ''}.", "ok")
    else:
        flash("No dashboards deleted.", "error")
    return redirect(url_for("pages.index"))


@bp.post("/<page_id>/duplicate")
def duplicate(page_id: str) -> Response:
    """Clone an existing dashboard into a new one and drop the user into
    the editor for the copy. Preserves cells, theme, font, style, device
    bindings, panel override, and per-cell options; regenerates ids
    (page + every cell) so the copy is fully independent of the source."""
    src = _store().get(page_id)
    if src is None:
        flash(f"No page with id {page_id!r}.", "error")
        return redirect(url_for("pages.index"))

    pages = _store().list()
    taken_ids = {p.id for p in pages}
    taken_names = {p.name for p in pages}
    new_id = _random_page_id(taken_ids)
    new_name = _duplicate_name(src.name, taken_names)

    # Fresh cell ids so the copy is independent: a cell-level update on
    # the copy must never reach into the source's pages.json entry.
    new_cells = [c.model_copy(update={"id": uuid.uuid4().hex[:8]}) for c in src.cells]
    copy = src.model_copy(update={"id": new_id, "name": new_name, "cells": new_cells})
    _store().save(copy)
    flash(f"Duplicated as {new_name!r}.", "ok")
    return redirect(url_for("pages.edit", page_id=new_id))


@bp.post("/<page_id>/status-bar/toggle")
def status_bar_toggle(page_id: str) -> Response:
    """Toggle the page-level status bar (v0.71.0).

    On: insert an auto-managed status_bar cell at (0, 0, panel.w, 48),
    shift + rescale existing cells to fit below it, remember the
    inserted cell's id on ``page.status_bar_cell_id``.

    Off: find the auto-managed cell by id, remove it, reverse the
    shift + rescale so the layout goes back to its pre-toggle shape.

    Refuses on panels below ``STATUS_BAR_MIN_REMAINING_PX + 48`` in
    height (bar would eat too much of the layout to leave usable
    space for other cells).
    """
    page = _store().get(page_id)
    if page is None:
        abort(404)
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    if page.status_bar_enabled:
        # Toggle OFF: remove the managed cell, then rescale everything
        # else up to fill the vacated top band. Uses fit_cells_to_panel
        # so leftover space (e.g. from a user-modified cell that's
        # shorter than its shifted footprint) is absorbed rather than
        # left as an awkward strip of matting.
        bar_id = page.status_bar_cell_id
        remaining = [c for c in page.cells if c.id != bar_id] if bar_id else list(page.cells)
        restored = _refit_after_status_bar_removal(remaining, panel_w=panel.w, panel_h=panel.h)
        _store().save(
            page.model_copy(
                update={
                    "cells": restored,
                    "status_bar_enabled": False,
                    "status_bar_cell_id": None,
                }
            )
        )
        current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
        return _flash_save(True, "Status bar removed.")
    # Toggle ON.
    if panel.h < STATUS_BAR_HEIGHT_PX + STATUS_BAR_MIN_REMAINING_PX:
        return _flash_save(
            False,
            (
                f"Panel is too short ({panel.h} px) to fit a status bar and "
                f"still leave room for the rest of the dashboard."
            ),
        )
    # v0.71.x: grow the auto-inserted cell so it accommodates the
    # gap-derived padding. Composer applies ``outer_pad = gap // 2``
    # on the top edge (touches panel wall) and ``inner_pad = gap // 4``
    # on the bottom edge (touches other cells). Bumping the cell's h
    # by (outer_pad + inner_pad) keeps the visible content area at
    # STATUS_BAR_HEIGHT_PX regardless of the user's gap setting AND
    # keeps the gap slider's matting visible around the bar on all
    # four sides. If the user later changes the gap, existing bar
    # cells stay at their original size; toggling off + on re-sizes
    # to the new gap.
    outer_pad = max(0, page.gap) // 2
    inner_pad = max(0, page.gap) // 4
    bar_cell_h = STATUS_BAR_HEIGHT_PX + outer_pad + inner_pad
    shifted = _rescale_cells_below_bar(
        list(page.cells), panel_h=panel.h, bar_h=bar_cell_h, direction="down"
    )
    bar_cell = Cell(
        id=uuid.uuid4().hex[:8],
        plugin="tesserae_status",
        x=0,
        y=0,
        w=panel.w,
        h=bar_cell_h,
        options=_default_status_bar_options(page.name),
    )
    _store().save(
        page.model_copy(
            update={
                "cells": [bar_cell, *shifted],
                "status_bar_enabled": True,
                "status_bar_cell_id": bar_cell.id,
            }
        )
    )
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    return _flash_save(True, "Status bar added at the top.")


@bp.post("/<page_id>/layout")
def apply_layout(page_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    layout_slug = (request.form.get("layout") or "").strip()
    if layout_slug not in LAYOUTS_BY_SLUG:
        return _flash_save(False, f"Unknown layout {layout_slug!r}.")
    new_cells = _apply_layout_to_cells(layout_slug, page)
    _store().save(page.model_copy(update={"cells": new_cells}))
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    return _flash_save(True, f"{LAYOUTS_BY_SLUG[layout_slug].name} layout applied.")


# -- cell routes -------------------------------------------------


@bp.post("/<page_id>/cells")
def create_cell(page_id: str) -> Response:
    """Add an unassigned cell at the end. The user picks the plugin from
    the cell card afterwards."""
    page = _store().get(page_id)
    if page is None:
        abort(404)
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    default_w = min(panel.w, 400)
    default_h = min(panel.h, 240)
    cell = _new_cell(x=0, y=0, w=default_w, h=default_h)
    updated = page.model_copy(update={"cells": [*page.cells, cell]})
    _store().save(updated)
    return _flash_save(True, "Cell added.")


def _apply_cell_form(cell: Cell, form: Any, panel: Any) -> Cell:
    """Build an updated Cell from a form-shaped dict. Shared between
    the persistent update_cell endpoint and the in-memory draft
    preview path so the two stay in lock-step."""
    new_plugin_id = (form.get("plugin") or "").strip() or None
    plugin_changed = new_plugin_id != cell.plugin
    plugin = _plugins().get(new_plugin_id) if new_plugin_id else None
    if plugin_changed:
        if plugin is None:
            options: dict[str, Any] = {}
        else:
            # v0.69.17 (issue #52 follow-up): a widget-type change used
            # to wipe every per-cell option override. Preserve any
            # override whose option name also exists on the new
            # plugin's ``cell_options`` schema so shared knobs like
            # ``location`` on weather_* variants, or a ``feeds_filter``
            # shared across calendar_* widgets, survive the swap.
            # Anything the new plugin doesn't declare drops off (it
            # would have no effect there anyway, but keeping it would
            # leave stale keys in the persisted options).
            new_option_names = {
                str(opt["name"]) for opt in plugin.manifest.get("cell_options", []) if "name" in opt
            }
            preserved = {
                name: value for name, value in cell.options.items() if name in new_option_names
            }
            options = {**plugin.cell_option_defaults(), **preserved}
    elif plugin is not None:
        options = _cell_options_from_form(plugin, form)
    else:
        options = {}
    if plugin_changed:
        # The opt-in belongs to the old widget dependency, not merely the box.
        # A newly selected widget always starts at the contract default (off).
        update_on_change = False
    else:
        update_on_change = _update_on_change_from_form(
            form,
            cell.update_on_change,
            supported=bool(plugin and plugin.on_change_updates),
        )
    return cell.model_copy(
        update={
            "plugin": new_plugin_id,
            "x": _coerce_int(form.get("x"), cell.x, lo=0, hi=panel.w - 1),
            "y": _coerce_int(form.get("y"), cell.y, lo=0, hi=panel.h - 1),
            "w": _coerce_int(form.get("w"), cell.w, lo=1, hi=panel.w),
            "h": _coerce_int(form.get("h"), cell.h, lo=1, hi=panel.h),
            "theme": (form.get("theme") or None),
            "style": (form.get("style") or None),
            "font": (form.get("font") or None),
            "options": options,
            "update_on_change": update_on_change,
            "zoom": _coerce_float(form.get("zoom"), cell.zoom, lo=0.5, hi=3.0),
            "padding_override": _padding_override_from_form(form, cell.padding_override),
            "dither": _dither_override_from_form(form, cell.dither),
            **_touch_from_form(form, cell),
        }
    )


def _update_on_change_from_form(form: Any, current: bool, *, supported: bool) -> bool:
    """Parse the host-owned per-placement update policy.

    The hidden ``update_on_change_present`` marker distinguishes a complete
    widget form with an unchecked checkbox from partial autosave payloads that
    do not carry this control. Widgets without a manifest declaration can never
    retain or enable the policy.
    """
    if not supported:
        return False
    if "update_on_change_present" not in form:
        return current
    return form.get("update_on_change") in ("1", "true", "on")


def _touch_from_form(form: Any, cell: Cell) -> dict[str, Any]:
    """Parse the Advanced pane's ``touch_json`` field (issue #49) into the
    cell's ``on_tap`` / ``on_swipe`` / ``on_slide`` overrides.

    The touch Interaction editor serialises its state to a single hidden
    field ``{on_tap?, on_swipe?, on_slide?}``. Absent field (a partial-
    form autosave that didn't include it) keeps the current values; an
    empty object clears all three. Each branch is validated loosely: a
    tap is a string or a structured dict, swipe a direction->spec map,
    slide an ``{axis, action}`` object. Anything malformed is dropped
    rather than raising, so a bad blob can't 500 the save."""
    raw = form.get("touch_json")
    if raw is None:
        return {
            "on_tap": cell.on_tap,
            "on_swipe": cell.on_swipe,
            "on_slide": cell.on_slide,
        }
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    tap = parsed.get("on_tap")
    on_tap = tap if isinstance(tap, (str, dict)) and tap else None

    swipe = parsed.get("on_swipe")
    on_swipe: dict[str, str] | None = None
    if isinstance(swipe, dict):
        cleaned = {
            k: v
            for k, v in swipe.items()
            if k in ("up", "down", "left", "right") and isinstance(v, str) and v
        }
        on_swipe = cleaned or None

    slide = parsed.get("on_slide")
    on_slide: dict[str, Any] | None = None
    if isinstance(slide, dict) and slide.get("action"):
        axis = slide.get("axis")
        on_slide = {"axis": axis if axis in ("x", "y") else "y", "action": slide["action"]}

    return {"on_tap": on_tap, "on_swipe": on_swipe, "on_slide": on_slide}


def _padding_override_from_form(form: Any, current: int | None) -> int | None:
    """Parse the "Layout tweaks" pane's padding-override pair.

    UI shape is a checkbox (``padding_override_enabled``) gating a
    slider (``padding_override``). Checkbox off -> None (inherit the
    page gap). Checkbox on -> clamp the slider value to the same
    0..80 envelope the page-level corner-radius slider uses. Missing
    checkbox is treated as "not on this form" (keep current value)
    so partial-form autosave paths don't wipe the setting."""
    if "padding_override_enabled" not in form and "padding_override" not in form:
        return current
    enabled = form.get("padding_override_enabled") in ("1", "true", "on")
    if not enabled:
        return None
    return _coerce_int(
        form.get("padding_override"), current if current is not None else 0, lo=0, hi=80
    )


def _dither_override_from_form(form: Any, current: str | None) -> str | None:
    """Parse the Advanced pane's dither-override pair (issue #86).

    UI shape mirrors the padding override: a checkbox
    (``dither_override_enabled``) gates a select (``dither_mode``, ``none``
    for flat / ``auto`` for smooth). Checkbox off -> None (inherit the
    widget's ``render.dither`` manifest hint, the default). A missing
    checkbox means the pair wasn't on this form (partial-form autosave), so
    keep the current value rather than wiping it."""
    if "dither_override_enabled" not in form and "dither_mode" not in form:
        return current
    enabled = form.get("dither_override_enabled") in ("1", "true", "on")
    if not enabled:
        return None
    mode = (form.get("dither_mode") or "").strip()
    if mode in ("none", "auto"):
        return mode
    return current if current in ("none", "auto") else "none"


@bp.post("/<page_id>/preview")
def preview(page_id: str) -> Response:
    """Build an in-memory draft Page from aggregated editor form data
    and stash it in PREVIEW_CACHE so the next /compose/<id> hit
    renders the draft instead of the persisted version.

    Form field convention (set by editor.js):
      ``name``, ``font``, ``bleed_color``, ``gap``,
      ``corner_radius``, page-level overrides.
      ``cell_<id>__<field>``, per-cell overrides (double-underscore
      separates the cell id from the field name to disambiguate cell
      ids containing underscores)."""
    page = _store().get(page_id)
    if page is None:
        abort(404)
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    form = request.form

    cell_buckets: dict[str, dict[str, Any]] = {}
    for key in form:
        if key.startswith("cell_"):
            try:
                rest = key[len("cell_") :]
                cid, field = rest.split("__", 1)
            except ValueError:
                continue
            bucket = cell_buckets.setdefault(cid, {})
            bucket[field] = form.get(key)
    # multi-value form fields (e.g. <select multiple>), pick up lists
    for cid, bucket in cell_buckets.items():
        for field in list(bucket.keys()):
            values = form.getlist(f"cell_{cid}__{field}")
            if len(values) > 1:
                bucket[field] = values

    new_cells: list[Cell] = []
    for cell in page.cells:
        cell_bucket = cell_buckets.get(cell.id)
        if cell_bucket is None:
            new_cells.append(cell)
            continue
        try:
            new_cells.append(_apply_cell_form(cell, cell_bucket, panel))
        except ValidationError:
            new_cells.append(cell)

    try:
        draft = page.model_copy(
            update={
                "name": (form.get("name") or page.name).strip() or page.name,
                "font": (form.get("font") or None),
                "theme": (form.get("theme") or page.theme),
                "style": (form.get("style") or page.style),
                "gap": _coerce_int(form.get("gap"), page.gap, lo=0),
                "corner_radius": _coerce_int(form.get("corner_radius"), page.corner_radius, lo=0),
                "bleed_color": (
                    form.get("bleed_color") if "bleed_color" in form else page.bleed_color
                ),
                "icon": (form.get("icon") or None) or None,
                "device_ids": _clean_device_ids(form.getlist("device_ids")),
                "cells": new_cells,
            }
        )
    except ValidationError:
        draft = page.model_copy(update={"cells": new_cells})

    current_app.config.setdefault("PREVIEW_CACHE", {})[page_id] = draft

    # Hydrate the draft for each panel size the editor's iframes care
    # about. The client uses these to compute postMessage patches so a
    # gentle edit (gap nudge, single-cell option) doesn't require a full
    # iframe reload, see static/pages/editor.js.
    # ``panels`` form field carries one or more ``WxH`` strings; missing
    # ⇒ no hydrated state returned and the client falls back to full
    # iframe reload, same behaviour as before this change.
    hydrated_groups: list[dict[str, Any]] = []
    requested_panels = form.getlist("panels[]") or form.getlist("panels")
    for spec in requested_panels:
        try:
            w_str, h_str = str(spec).lower().split("x", 1)
            pw, ph = int(w_str), int(h_str)
        except (ValueError, AttributeError):
            continue
        if pw <= 0 or ph <= 0:
            continue
        page_dict = draft.model_dump(mode="json", exclude_none=True)
        page_dict["panel"] = {"w": pw, "h": ph}
        try:
            hydrated = _hydrate_page(page_dict, preview=True)
        except Exception:
            current_app.logger.exception("preview hydrate failed for %sx%s", pw, ph)
            continue
        hydrated_groups.append({"w": pw, "h": ph, "state": hydrated})

    # AJAX caller (the editor) wants JSON; non-AJAX (rare, direct curl
    # for debugging) keeps the existing flash-redirect behaviour.
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify({"ok": True, "groups": hydrated_groups})
    return _flash_save(True, "preview-ready")


@bp.post("/<page_id>/cells/<cell_id>")
def update_cell(page_id: str, cell_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    cell = next((c for c in page.cells if c.id == cell_id), None)
    if cell is None:
        abort(404)

    form = request.form
    new_plugin_id = (form.get("plugin") or "").strip() or None
    plugin_changed = new_plugin_id != cell.plugin
    plugin = _plugins().get(new_plugin_id) if new_plugin_id else None
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    try:
        updated_cell = _apply_cell_form(cell, form, panel)
    except ValidationError as exc:
        return _flash_save(False, f"Invalid cell: {exc.errors()[0]['msg']}")

    new_cells = [updated_cell if c.id == cell_id else c for c in page.cells]
    _store().save(page.model_copy(update={"cells": new_cells}))
    # Persisted page is now authoritative; drop any draft preview.
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    msg = "Plugin changed, options reset." if plugin_changed and plugin else "Cell saved."
    return _flash_save(True, msg)


@bp.post("/<page_id>/cells/<cell_id>/delete")
def delete_cell(page_id: str, cell_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    if not any(c.id == cell_id for c in page.cells):
        return _flash_save(False, f"No cell {cell_id!r}.")
    new_cells = [c for c in page.cells if c.id != cell_id]
    _store().save(page.model_copy(update={"cells": new_cells}))
    return _flash_save(True, "Cell deleted.")


@bp.post("/<page_id>/cells/batch")
def batch_cells(page_id: str) -> Response:
    """Apply a set of resize / create / delete cell mutations atomically.

    The layout editor uses this to push multi-cell rearrangements (e.g.
    dragging a shared boundary updates two cells at once; inserting a
    new cell shrinks the source and adds a new one) in a single save.

    Body: JSON
      {
        "updates": [{"id": "abc", "x": 0, "y": 0, "w": 100, "h": 100}, ...],
        "creates": [{"x": 0, "y": 0, "w": 100, "h": 100}, ...],
        "deletes": ["cell_id_1", "cell_id_2"]
      }

    Only x/y/w/h are mutable here, plugin assignment / options stay on
    the per-cell form path. Returns JSON {ok, cells: [...]} so the
    client can refresh without a full page reload.
    """
    page = _store().get(page_id)
    if page is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    updates = body.get("updates") or []
    creates = body.get("creates") or []
    deletes = set(body.get("deletes") or [])

    panel = resolve_panel_for_page(page, _devices(), _settings_store())

    def _clamp_geom(g: dict[str, Any], existing: Cell | None) -> dict[str, int]:
        return {
            "x": _coerce_int(g.get("x"), existing.x if existing else 0, lo=0, hi=panel.w - 1),
            "y": _coerce_int(g.get("y"), existing.y if existing else 0, lo=0, hi=panel.h - 1),
            "w": _coerce_int(g.get("w"), existing.w if existing else 1, lo=1, hi=panel.w),
            "h": _coerce_int(g.get("h"), existing.h if existing else 1, lo=1, hi=panel.h),
        }

    by_id = {c.id: c for c in page.cells}
    for upd in updates:
        cid = str(upd.get("id") or "")
        if cid not in by_id:
            continue
        cell = by_id[cid]
        geom = _clamp_geom(upd, cell)
        by_id[cid] = cell.model_copy(update=geom)

    for cid in deletes:
        by_id.pop(cid, None)

    # Preserve original cell order; new cells append.
    remaining = [by_id[c.id] for c in page.cells if c.id in by_id]
    for spec in creates:
        geom = _clamp_geom(spec, None)
        remaining.append(_new_cell(**geom))

    _store().save(page.model_copy(update={"cells": remaining}))
    # Drop any stale draft preview, the layout is now persisted.
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    return jsonify(
        {
            "ok": True,
            "cells": [
                {"id": c.id, "x": c.x, "y": c.y, "w": c.w, "h": c.h, "plugin": c.plugin}
                for c in remaining
            ],
        }
    )


@bp.post("/<page_id>/refit")
def refit_cells(page_id: str) -> Response:
    """Explicitly rescale every cell so its proportions match the
    current primary panel. Power-user escape hatch for the case where
    you've intentionally swapped to a new panel size and want the
    saved coords scaled (with the implicit acceptance that
    non-uniform scaling will mangle pixel-perfect alignments).

    The edit page handler refuses to do this automatically — see
    ``_ensure_cells_fit_panel`` for the reasoning — so the editor
    surfaces a "Refit to current panel" button that posts here."""
    page = _store().get(page_id)
    if page is None:
        abort(404)
    if not page.cells:
        return _flash_save(True, "Nothing to refit.")
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    coords = [(c.x, c.y, c.w, c.h) for c in page.cells]
    top_strip_index = _status_bar_cell_index(page)
    fitted = fit_cells_to_panel(coords, panel.w, panel.h, top_strip_index=top_strip_index)
    if fitted == coords:
        return _flash_save(True, "Cells already fit the current panel.")
    new_cells = [
        cell.model_copy(update={"x": nx, "y": ny, "w": nw, "h": nh})
        for cell, (nx, ny, nw, nh) in zip(page.cells, fitted, strict=True)
    ]
    _store().save(page.model_copy(update={"cells": new_cells}))
    current_app.config.get("PREVIEW_CACHE", {}).pop(page_id, None)
    return _flash_save(True, f"Refitted {len(new_cells)} cell(s) to {panel.w}×{panel.h}.")


def register(app: Flask) -> None:
    app.register_blueprint(bp)
