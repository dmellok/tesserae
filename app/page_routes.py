"""Dashboard editor — pages + cells CRUD.

Two layers:

* **Page** = a panel-sized canvas with metadata (name, panel dims, theme,
  font, gap, corner radius, bleed colour) and an ordered list of cells.
* **Cell** = one widget on the panel: which plugin renders it, its
  position + size in panel pixels, optional theme / font override, and
  the plugin's ``cell_options`` filled in.

URL shape:

  GET  /pages                     list every saved page
  GET  /pages/new                 new-page form
  POST /pages/new                 create
  GET  /pages/<id>                page edit (metadata + cells list + iframe)
  POST /pages/<id>                update page metadata
  POST /pages/<id>/delete         delete the page
  POST /pages/<id>/cells          create a cell (picks the plugin, lands
                                  on the cell edit page for the rest)
  GET  /pages/<id>/cells/<cid>    edit one cell
  POST /pages/<id>/cells/<cid>    update one cell
  POST /pages/<id>/cells/<cid>/delete

The live preview iframe points at ``/compose/<page_id>?preview=1`` —
same route Playwright screenshots from. The auth gate (M14 change)
now lets authed sessions hit /compose so the iframe works over the LAN.

Plugin choice is locked at cell creation. Changing a cell's plugin
means deleting + re-adding — the option schemas don't line up between
plugins anyway, so a swap would always drop the cell_options.
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
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response

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
    """Mirror of settings_routes._coerce_form_value but scoped to widget
    cell_option types (string / textarea / number / boolean / select /
    color). Form keys are prefixed with ``opt_`` so they can coexist
    with the cell's position / theme fields under one submit."""
    ftype = field.get("type", "string")
    if ftype == "boolean":
        # Checkboxes: present in form == True, absent == False. Match the
        # prefixed name (opt_<field>) the template renders.
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
    # string / textarea
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
        raw = (form.get(f"override_{token}") or "").strip()
        if raw and raw != "":
            out[token] = raw
    return out or None


# -- page-level routes ---------------------------------------------------


@bp.get("")
def index() -> str:
    pages = _store().list()
    return render_template("pages_list.html", pages=pages)


@bp.get("/new")
def new() -> str:
    return render_template("page_new.html")


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

    try:
        page = Page(
            id=page_id,
            name=name,
            panel=Panel(
                w=_coerce_int(form.get("panel_w"), 1600, lo=1),
                h=_coerce_int(form.get("panel_h"), 1200, lo=1),
            ),
            cells=[],
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
    flash(f"Page {name!r} created.", "ok")
    return redirect(url_for("pages.edit", page_id=page.id))


@bp.get("/<page_id>")
def edit(page_id: str) -> str:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    return render_template(
        "page_edit.html",
        page=page,
        plugins=_plugins().widgets(),
        themes=sorted(_plugins().themes.values(), key=lambda t: t.name.lower()),
        fonts=sorted(_plugins().fonts.values(), key=lambda f: f.name.lower()),
        preview_scale=_preview_scale(page),
    )


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
        flash(f"Invalid page: {exc.errors()[0]['msg']}", "error")
        return redirect(url_for("pages.edit", page_id=page_id))
    _store().save(updated)
    flash("Page metadata saved.", "ok")
    return redirect(url_for("pages.edit", page_id=page_id))


@bp.post("/<page_id>/delete")
def delete(page_id: str) -> Response:
    if _store().delete(page_id):
        flash("Page deleted.", "ok")
    else:
        flash(f"No page with id {page_id!r}.", "error")
    return redirect(url_for("pages.index"))


# -- cell routes ----------------------------------------------------


@bp.post("/<page_id>/cells")
def create_cell(page_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    plugin_id = (request.form.get("plugin") or "").strip()
    plugin = _plugins().get(plugin_id)
    if plugin is None or plugin.kind != "widget":
        flash("Pick a widget plugin first.", "error")
        return redirect(url_for("pages.edit", page_id=page_id))

    # Position: bottom-left of the panel by default so a fresh cell
    # doesn't overlap existing ones. Caller can resize on the cell edit
    # page.
    default_w = min(page.panel.w, 400)
    default_h = min(page.panel.h, 240)
    cell_id = uuid.uuid4().hex[:8]
    cell = Cell(
        id=cell_id,
        plugin=plugin_id,
        x=0,
        y=0,
        w=default_w,
        h=default_h,
        options=plugin.cell_option_defaults(),
    )
    updated = page.model_copy(update={"cells": [*page.cells, cell]})
    _store().save(updated)
    flash(f"Added {plugin.name} cell.", "ok")
    return redirect(url_for("pages.edit_cell", page_id=page_id, cell_id=cell_id))


@bp.get("/<page_id>/cells/<cell_id>")
def edit_cell(page_id: str, cell_id: str) -> str:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    cell = next((c for c in page.cells if c.id == cell_id), None)
    if cell is None:
        abort(404)
    plugin = _plugins().get(cell.plugin)
    return render_template(
        "cell_edit.html",
        page=page,
        cell=cell,
        plugin=plugin,
        themes=sorted(_plugins().themes.values(), key=lambda t: t.name.lower()),
        fonts=sorted(_plugins().fonts.values(), key=lambda f: f.name.lower()),
        preview_scale=_preview_scale(page),
    )


@bp.post("/<page_id>/cells/<cell_id>")
def update_cell(page_id: str, cell_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    cell = next((c for c in page.cells if c.id == cell_id), None)
    if cell is None:
        abort(404)
    plugin = _plugins().get(cell.plugin)
    if plugin is None:
        flash(f"Cell's plugin {cell.plugin!r} is no longer loaded.", "error")
        return redirect(url_for("pages.edit", page_id=page_id))

    form = request.form
    try:
        updated_cell = cell.model_copy(
            update={
                "x": _coerce_int(form.get("x"), cell.x, lo=0, hi=page.panel.w - 1),
                "y": _coerce_int(form.get("y"), cell.y, lo=0, hi=page.panel.h - 1),
                "w": _coerce_int(form.get("w"), cell.w, lo=1, hi=page.panel.w),
                "h": _coerce_int(form.get("h"), cell.h, lo=1, hi=page.panel.h),
                "theme": (form.get("theme") or None),
                "font": (form.get("font") or None),
                "options": _cell_options_from_form(plugin, form),
                "palette_overrides": _palette_overrides_from_form(form),
            }
        )
    except ValidationError as exc:
        flash(f"Invalid cell: {exc.errors()[0]['msg']}", "error")
        return redirect(url_for("pages.edit_cell", page_id=page_id, cell_id=cell_id))

    new_cells = [updated_cell if c.id == cell_id else c for c in page.cells]
    _store().save(page.model_copy(update={"cells": new_cells}))
    flash("Cell saved.", "ok")
    return redirect(url_for("pages.edit_cell", page_id=page_id, cell_id=cell_id))


@bp.post("/<page_id>/cells/<cell_id>/delete")
def delete_cell(page_id: str, cell_id: str) -> Response:
    page = _store().get(page_id)
    if page is None:
        abort(404)
    if not any(c.id == cell_id for c in page.cells):
        flash(f"No cell {cell_id!r} on this page.", "error")
        return redirect(url_for("pages.edit", page_id=page_id))
    new_cells = [c for c in page.cells if c.id != cell_id]
    _store().save(page.model_copy(update={"cells": new_cells}))
    flash("Cell deleted.", "ok")
    return redirect(url_for("pages.edit", page_id=page_id))


# -- helpers ------------------------------------------------------


def _preview_scale(page: Page) -> float:
    """Pick a CSS transform scale so the iframe fits in a ~640px-wide
    preview slot regardless of the panel's actual pixel size."""
    target = 640.0
    if page.panel.w <= target:
        return 1.0
    return target / page.panel.w


def register(app: Flask) -> None:
    app.register_blueprint(bp)
