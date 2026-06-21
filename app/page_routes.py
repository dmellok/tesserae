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

import logging
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
    if ftype == "number":
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


def _apply_layout_to_cells(layout_slug: str, page: Page) -> list[Cell]:
    """Build a new cells list by reusing existing cells (plugin + options)
    in order and repositioning them to match the layout's slots. Extra
    slots get unassigned cells; surplus existing cells are dropped."""
    layout = LAYOUTS_BY_SLUG.get(layout_slug)
    if layout is None:
        raise ValueError(f"unknown layout {layout_slug!r}")
    panel = resolve_panel_for_page(page, _devices(), _settings_store())
    positions = to_panel_pixels(layout, panel.w, panel.h)
    out: list[Cell] = []
    for i, (x, y, w, h) in enumerate(positions):
        if i < len(page.cells):
            existing = page.cells[i]
            out.append(existing.model_copy(update={"x": x, "y": y, "w": w, "h": h}))
        else:
            out.append(_new_cell(x=x, y=y, w=w, h=h))
    return out


def _materialize_cell_options(plugins: list[Any]) -> dict[str, list[dict[str, Any]]]:
    """For each widget plugin, return its cell_options spec with any
    ``choices_from`` fields swapped to concrete ``choices`` lists by
    calling the plugin's ``choices(name)`` function.

    Plugins that don't expose ``choices`` (or that raise) get an empty
    list, the editor will render an empty dropdown rather than break."""
    out: dict[str, list[dict[str, Any]]] = {}
    for plugin in plugins:
        options = list(plugin.manifest.get("cell_options", []))
        resolver = getattr(plugin.server_module, "choices", None) if plugin.server_module else None
        materialized: list[dict[str, Any]] = []
        for opt in options:
            spec = dict(opt)
            source = spec.pop("choices_from", None)
            if source and callable(resolver):
                try:
                    spec["choices"] = list(resolver(source))
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
    fitted = fit_cells_to_panel(coords, panel.w, panel.h)
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
    """Bucket pages by primary (first still-existing) device for the
    Dashboards list. Returns ordered ``(device_or_None, pages)`` tuples:
    bound device groups sorted by ``display_name`` (case-insensitive),
    then an "Unbound" group at the end when there are unbound pages.
    Pages within each group are alphabetical by name (case-insensitive,
    tie-break on id for stability). Empty groups are dropped, so a
    device with zero bound pages never renders a section head.

    Primary-device resolution skips ids that no longer resolve through
    the registry, so a half-deleted binding (`device_ids=["gone",
    "kitchen"]`) falls through to the next live device rather than
    landing in Unbound; a page with no live bindings goes to Unbound.

    Pulled out of ``index()`` so unit tests can hit the grouping logic
    without standing up a full Flask app + Page store."""
    bound: dict[str, tuple[Any, list[Page]]] = {}
    unbound: list[Page] = []
    for page in pages:
        primary = None
        if devices is not None:
            for did in page.device_ids:
                candidate = devices.devices.get(did)
                if candidate is not None:
                    primary = candidate
                    break
        if primary is None:
            unbound.append(page)
            continue
        slot = bound.setdefault(primary.id, (primary, []))
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
    return render_template(
        "pages_list.html",
        pages=pages,
        page_dims=page_dims,
        page_devices=page_devices,
        page_groups=page_groups,
        page_last_pushed_rel=page_last_pushed_rel,
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
def edit(page_id: str) -> str:
    page = _store().get(page_id)
    if page is None:
        abort(404)
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
        options: dict[str, Any] = plugin.cell_option_defaults() if plugin else {}
    elif plugin is not None:
        options = _cell_options_from_form(plugin, form)
    else:
        options = {}
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
            "zoom": _coerce_float(form.get("zoom"), cell.zoom, lo=0.5, hi=3.0),
        }
    )


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
    fitted = fit_cells_to_panel(coords, panel.w, panel.h)
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
