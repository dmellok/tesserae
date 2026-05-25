"""Dashboard editor — single-page form-driven editor with auto-save.

The /pages/<id> endpoint serves the whole editor: page metadata, layout
picker, every cell's options, and the live preview iframe. The client
auto-saves any form change via fetch POST to the same endpoints used
for full-page submits, then asks the preview iframe to reload.

Cells whose ``plugin`` is None are valid — they're slots a layout
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
import re
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

from app.layouts import LAYOUTS, LAYOUTS_BY_SLUG, detect_layout, to_panel_pixels
from app.panel import fit_cells_to_panel, resolve_page_panel, resolve_settings_panel
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


def _slug_from(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "page"


def _unique_id(base: str, taken: set[str]) -> str:
    """Append _2, _3, … to base until it's not in ``taken``. Used wherever
    we auto-generate ids from a free-text name — the user shouldn't have
    to think about ids being globally unique."""
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


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


def _palette_overrides_from_form(form: Any) -> dict[str, str] | None:
    from app.state.user_themes import PALETTE_TOKENS

    out: dict[str, str] = {}
    for token in PALETTE_TOKENS:
        # Override is opt-in via a checkbox so the colour picker's
        # always-set default doesn't accidentally override every token.
        if form.get(f"override_{token}_enabled") in ("on", "true", "1"):
            raw = (form.get(f"override_{token}") or "").strip()
            if raw and raw.startswith("#"):
                out[token] = raw
    return out or None


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
    panel = resolve_page_panel(page.panel, _settings_store())
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
    list — the editor will render an empty dropdown rather than break."""
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
    """If the saved cell coords don't fit the current panel (e.g. user
    flipped to portrait after designing in landscape), auto-rotate and
    rescale them to the new panel, persist, and return the updated page."""
    if not page.cells:
        return page
    coords = [(c.x, c.y, c.w, c.h) for c in page.cells]
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
    panel = resolve_page_panel(page.panel, _settings_store())
    page = _ensure_cells_fit_panel(page, panel)
    panel_cells = [(c.x, c.y, c.w, c.h) for c in page.cells]
    active_layout = detect_layout(panel_cells, panel.w, panel.h)
    widgets = sorted(_plugins().widgets(), key=lambda p: p.name.lower())
    return {
        "page": page,
        "panel": panel,
        "plugins": widgets,
        "plugin_cell_options": _materialize_cell_options(widgets),
        "themes": sorted(_plugins().themes.values(), key=lambda t: t.name.lower()),
        "fonts": sorted(_plugins().fonts.values(), key=lambda f: f.name.lower()),
        "layouts": LAYOUTS,
        "active_layout": active_layout.slug if active_layout else None,
        "preview_scale": _preview_scale(panel.w, panel.h),
    }


def _preview_scale(panel_w: int, panel_h: int = 0) -> float:
    """Pick a scale that fits both axes inside a roughly 720x720 box so
    portrait panels don't blow the editor column out vertically."""
    target = 720.0
    sw = target / panel_w if panel_w > target else 1.0
    sh = target / panel_h if panel_h and panel_h > target else 1.0
    return min(sw, sh)


# -- page-level routes --------------------------------------------


@bp.get("")
def index() -> str:
    pages = _store().list()
    return render_template("pages_list.html", pages=pages)


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
    optional — if no fields are POSTed (e.g. the "New dashboard" button
    in the nav) we seed an "Untitled" 1-cell dashboard so the editor is
    the only place the user has to think.

    Panel dims come from app settings — pages aren't panel-specific."""
    form = request.form
    name = (form.get("name") or "").strip() or "Untitled dashboard"

    taken = {p.id for p in _store().list()}
    page_id = _unique_id(_slug_from(name), taken)

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
            # panel left None on purpose — derived from settings at render
            # time so changing the panel in settings updates every page.
            cells=initial_cells,
            theme=(form.get("theme") or None),
            font=(form.get("font") or None),
            gap=_coerce_int(form.get("gap"), 0, lo=0),
            corner_radius=_coerce_int(form.get("corner_radius"), 0, lo=0),
            bleed_color=(form.get("bleed_color") or "#ffffff"),
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
    the form — the editor no longer exposes them."""
    page = _store().get(page_id)
    if page is None:
        abort(404)
    form = request.form
    try:
        updated = page.model_copy(
            update={
                "name": (form.get("name") or page.name).strip() or page.name,
                "theme": (form.get("theme") or None),
                "font": (form.get("font") or None),
                "gap": _coerce_int(form.get("gap"), page.gap, lo=0),
                "corner_radius": _coerce_int(form.get("corner_radius"), page.corner_radius, lo=0),
                "bleed_color": (form.get("bleed_color") or page.bleed_color),
            }
        )
    except ValidationError as exc:
        return _flash_save(False, f"Invalid page: {exc.errors()[0]['msg']}")
    _store().save(updated)
    return _flash_save(True, "Page saved.")


@bp.post("/<page_id>/delete")
def delete(page_id: str) -> Response:
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
    return _flash_save(True, f"{LAYOUTS_BY_SLUG[layout_slug].name} layout applied.")


# -- cell routes -------------------------------------------------


@bp.post("/<page_id>/cells")
def create_cell(page_id: str) -> Response:
    """Add an unassigned cell at the end. The user picks the plugin from
    the cell card afterwards."""
    page = _store().get(page_id)
    if page is None:
        abort(404)
    panel = resolve_page_panel(page.panel, _settings_store())
    default_w = min(panel.w, 400)
    default_h = min(panel.h, 240)
    cell = _new_cell(x=0, y=0, w=default_w, h=default_h)
    updated = page.model_copy(update={"cells": [*page.cells, cell]})
    _store().save(updated)
    return _flash_save(True, "Cell added.")


@bp.post("/<page_id>/cells/<cell_id>")
def update_cell(page_id: str, cell_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    cell = next((c for c in page.cells if c.id == cell_id), None)
    if cell is None:
        abort(404)

    form = request.form
    # Plugin can change. When it does, drop existing options and reseed
    # from the new plugin's manifest defaults — the option schemas don't
    # line up between plugins so a swap would always produce a stale form.
    new_plugin_id = (form.get("plugin") or "").strip() or None
    plugin_changed = new_plugin_id != cell.plugin
    plugin = _plugins().get(new_plugin_id) if new_plugin_id else None

    if plugin_changed:
        options: dict[str, Any] = plugin.cell_option_defaults() if plugin else {}
    elif plugin is not None:
        options = _cell_options_from_form(plugin, form)
    else:
        options = {}

    panel = resolve_page_panel(page.panel, _settings_store())
    try:
        updated_cell = cell.model_copy(
            update={
                "plugin": new_plugin_id,
                "x": _coerce_int(form.get("x"), cell.x, lo=0, hi=panel.w - 1),
                "y": _coerce_int(form.get("y"), cell.y, lo=0, hi=panel.h - 1),
                "w": _coerce_int(form.get("w"), cell.w, lo=1, hi=panel.w),
                "h": _coerce_int(form.get("h"), cell.h, lo=1, hi=panel.h),
                "theme": (form.get("theme") or None),
                "font": (form.get("font") or None),
                "options": options,
                "palette_overrides": _palette_overrides_from_form(form),
            }
        )
    except ValidationError as exc:
        return _flash_save(False, f"Invalid cell: {exc.errors()[0]['msg']}")

    new_cells = [updated_cell if c.id == cell_id else c for c in page.cells]
    _store().save(page.model_copy(update={"cells": new_cells}))
    msg = "Plugin changed — options reset." if plugin_changed and plugin else "Cell saved."
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


def register(app: Flask) -> None:
    app.register_blueprint(bp)
