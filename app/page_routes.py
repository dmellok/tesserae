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
from app.plugin_loader import Plugin, PluginRegistry
from app.state.page_store import Cell, Page, PageStore, Panel

bp = Blueprint("pages", __name__, url_prefix="/pages")

_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$")


def _store() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _plugins() -> PluginRegistry:
    return current_app.config["PLUGIN_REGISTRY"]  # type: ignore[no-any-return]


def _slug_from(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "page"


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
    positions = to_panel_pixels(layout, page.panel.w, page.panel.h)
    out: list[Cell] = []
    for i, (x, y, w, h) in enumerate(positions):
        if i < len(page.cells):
            existing = page.cells[i]
            out.append(existing.model_copy(update={"x": x, "y": y, "w": w, "h": h}))
        else:
            out.append(_new_cell(x=x, y=y, w=w, h=h))
    return out


def _editor_context(page: Page) -> dict[str, Any]:
    """Shared context for the editor."""
    panel_cells = [(c.x, c.y, c.w, c.h) for c in page.cells]
    active_layout = detect_layout(panel_cells, page.panel.w, page.panel.h)
    return {
        "page": page,
        "plugins": sorted(_plugins().widgets(), key=lambda p: p.name.lower()),
        "themes": sorted(_plugins().themes.values(), key=lambda t: t.name.lower()),
        "fonts": sorted(_plugins().fonts.values(), key=lambda f: f.name.lower()),
        "layouts": LAYOUTS,
        "active_layout": active_layout.slug if active_layout else None,
        "preview_scale": _preview_scale(page),
    }


def _preview_scale(page: Page) -> float:
    target = 720.0
    if page.panel.w <= target:
        return 1.0
    return target / page.panel.w


# -- page-level routes --------------------------------------------


@bp.get("")
def index() -> str:
    pages = _store().list()
    return render_template("pages_list.html", pages=pages)


@bp.get("/new")
def new() -> str:
    return render_template("page_new.html", layouts=LAYOUTS)


@bp.post("/new")
def create() -> Response:
    form = request.form
    name = (form.get("name") or "").strip()
    if not name:
        flash("Page name is required.", "error")
        return redirect(url_for("pages.new"))
    page_id = (form.get("id") or "").strip().lower() or _slug_from(name)
    if not _ID_RE.match(page_id):
        flash(f"Bad id {page_id!r} (snake_case only).", "error")
        return redirect(url_for("pages.new"))
    if _store().get(page_id) is not None:
        flash(f"A page with id {page_id!r} already exists.", "error")
        return redirect(url_for("pages.new"))

    panel_w = _coerce_int(form.get("panel_w"), 1600, lo=1)
    panel_h = _coerce_int(form.get("panel_h"), 1200, lo=1)
    layout_slug = (form.get("layout") or "1_cell").strip()
    layout = LAYOUTS_BY_SLUG.get(layout_slug, LAYOUTS_BY_SLUG["1_cell"])
    initial_cells = [
        _new_cell(x=x, y=y, w=w, h=h) for x, y, w, h in to_panel_pixels(layout, panel_w, panel_h)
    ]

    try:
        page = Page(
            id=page_id,
            name=name,
            panel=Panel(w=panel_w, h=panel_h),
            cells=initial_cells,
            theme=(form.get("theme") or None),
            font=(form.get("font") or None),
            gap=_coerce_int(form.get("gap"), 0, lo=0),
            corner_radius=_coerce_int(form.get("corner_radius"), 0, lo=0),
            bleed_color=(form.get("bleed_color") or "#ffffff"),
        )
    except ValidationError as exc:
        flash(f"Invalid page: {exc.errors()[0]['msg']}", "error")
        return redirect(url_for("pages.new"))

    _store().save(page)
    flash(f"Page {name!r} created with the {layout.name} layout.", "ok")
    return redirect(url_for("pages.edit", page_id=page.id))


@bp.get("/<page_id>")
def edit(page_id: str) -> str:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    return render_template("page_editor.html", **_editor_context(page))


@bp.post("/<page_id>")
def update(page_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    form = request.form
    try:
        updated = page.model_copy(
            update={
                "name": (form.get("name") or page.name).strip() or page.name,
                "panel": Panel(
                    w=_coerce_int(form.get("panel_w"), page.panel.w, lo=1),
                    h=_coerce_int(form.get("panel_h"), page.panel.h, lo=1),
                ),
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
    default_w = min(page.panel.w, 400)
    default_h = min(page.panel.h, 240)
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

    try:
        updated_cell = cell.model_copy(
            update={
                "plugin": new_plugin_id,
                "x": _coerce_int(form.get("x"), cell.x, lo=0, hi=page.panel.w - 1),
                "y": _coerce_int(form.get("y"), cell.y, lo=0, hi=page.panel.h - 1),
                "w": _coerce_int(form.get("w"), cell.w, lo=1, hi=page.panel.w),
                "h": _coerce_int(form.get("h"), cell.h, lo=1, hi=page.panel.h),
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
