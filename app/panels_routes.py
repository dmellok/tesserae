"""Panels canvas editor routes (issue #60), behind the ``composer``
experiment flag.

A WYSIWYG editor where widgets are data sources and users bind individual
fields to freely-placed visual elements. This module serves the editor page,
the widget-catalog endpoint the Data panel consumes, and the canvas-document
CRUD (list / create / open / autosave). The canvas render path (screenshot +
quantise, reusing the grid pipeline and the issue #86 region map) lands in a
later phase.

Everything here is gated by :func:`app.experiments.is_enabled("composer")`,
checked per request so toggling the flag needs no restart. When the flag is
off the routes 404, so the feature stays invisible until switched on. Admin
auth still applies via the global before-request gate (these paths are not
loopback-exempt).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import json
import queue
import time
import uuid
from collections.abc import Iterator
from typing import Any

from flask import (
    Blueprint,
    Flask,
    abort,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app import experiments
from app.panels_schema import build_catalog
from app.plugin_loader import PluginRegistry
from app.state.page_store import Page, PageStore
from app.state.panel_store import CanvasPage, Element

# The freeform (canvas) editor now lives in the dashboards area and persists to
# the same PageStore as grid pages: a canvas is a Page with layout_kind="canvas"
# (issue #60). The internal ``/c/<id>`` path structure is kept so the editor JS
# and template are unchanged; ``<id>`` is a page id.
bp = Blueprint("panels", __name__, url_prefix="/pages/canvas")

_EXPERIMENT = "composer"


def _guard() -> None:
    """404 the whole blueprint unless the ``composer`` experiment is on."""
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)


def _registry() -> PluginRegistry:
    registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    return registry


def _pages() -> PageStore:
    store: PageStore = current_app.config["PAGE_STORE"]
    return store


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _get_canvas(page_id: str) -> Page | None:
    """The page for ``page_id`` iff it's a freeform (canvas) dashboard."""
    page = _pages().get(page_id)
    return page if (page is not None and page.layout_kind == "canvas") else None


def _canvas_pages() -> list[Page]:
    return [p for p in _pages().list() if p.layout_kind == "canvas"]


def _as_doc(page: Page) -> CanvasPage:
    """The CanvasPage-shaped document the editor hydrates from a canvas page
    (the editor's client contract predates the Page merge, so we keep it)."""
    from app.state.panel_store import CanvasLayout

    c = page.canvas or CanvasLayout()
    return CanvasPage(
        id=page.id,
        name=page.name,
        w=c.w,
        h=c.h,
        theme=c.theme,
        style=c.style,
        font=c.font,
        bg=c.bg,
        bg_image=c.bg_image,
        bg_fit=c.bg_fit,
        device_ids=list(page.device_ids),
        els=list(c.els),
    )


def _doc_to_page(doc: CanvasPage) -> Page:
    """Build a canvas Page from an editor document. Page-level theme/style/font
    mirror the canvas so the dashboards list reflects it."""
    return Page(
        id=doc.id,
        name=doc.name or "Untitled Panel",
        layout_kind="canvas",
        device_ids=list(doc.device_ids),
        theme=doc.theme,
        style=doc.style,
        font=doc.font or None,
        canvas=doc.to_layout(),
    )


def _new_canvas_page(name: str = "Untitled Panel") -> Page:
    return Page(id=_new_id(), name=name, layout_kind="canvas", canvas=_empty_layout())


def _empty_layout() -> Any:
    from app.state.panel_store import CanvasLayout

    return CanvasLayout()


@bp.get("/")
def index() -> Response | str:
    """Land on the most recent canvas dashboard, or mint a fresh one when there
    are none, so the editor always opens straight into an editable artboard."""
    _guard()
    docs = _canvas_pages()
    if not docs:
        page = _new_canvas_page()
        _pages().save(page)
        return redirect(url_for("panels.editor", canvas_id=page.id))
    return redirect(url_for("panels.editor", canvas_id=docs[0].id))


@bp.post("/new")
def create() -> Response:
    """Create a blank canvas dashboard and open it."""
    _guard()
    name = (request.form.get("name") or "Untitled Panel").strip() or "Untitled Panel"
    page = _new_canvas_page(name)
    _pages().save(page)
    return redirect(url_for("panels.editor", canvas_id=page.id))


@bp.get("/c/<canvas_id>")
def editor(canvas_id: str) -> str:
    """The full-window canvas editor for one dashboard."""
    _guard()
    if _get_canvas(canvas_id) is None:
        abort(404)
    from app.composer import _font_face_css

    return render_template(
        "panels_editor.html",
        canvas_id=canvas_id,
        font_face_css=_font_face_css(_registry().fonts),
        code_fonts=[{"id": f.id, "name": f.name} for f in _registry().fonts.values()],
    )


def _stamp(page: Page, actor: str) -> Page:
    """Record who last wrote this page and when, for the MCP drift guard. Called
    on every canvas write path (UI autosave → "ui", MCP → "mcp") so an agent can
    tell its own last write apart from a UI edit that landed between its calls."""
    from datetime import UTC, datetime

    page.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    page.updated_by = actor
    return page


def _canvas_rev(page: Page) -> str:
    """A short content hash of a canvas page's layout. The live-sync stream and
    the editor use it to tell an external change (the MCP agent) apart from the
    editor's own autosave: same rev = our save, echo it and ignore."""
    layout = page.canvas.model_dump(mode="json") if page.canvas is not None else {}
    blob = json.dumps(layout, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@bp.get("/c/<canvas_id>/doc.json")
def doc(canvas_id: str) -> Response:
    """The canvas document the editor hydrates from."""
    _guard()
    page = _get_canvas(canvas_id)
    if page is None:
        abort(404)
    out = _as_doc(page).model_dump(mode="json")
    out["rev"] = _canvas_rev(page)
    return jsonify(out)


# Live-sync keepalive. The stream holds a worker thread for the editor's
# lifetime (sync WSGI), and a dead client is only noticed on the next yield, so
# keep it modest. Canvas edits are infrequent, so a change always arrives via
# the queue, not the keepalive.
_STREAM_KEEPALIVE_S: float = 10.0


@bp.get("/c/<canvas_id>/stream")
def stream(canvas_id: str) -> Response:
    """Server-Sent Events feed that fires when this canvas is saved, so an open
    editor can reflect external edits live (e.g. the MCP agent building it).

    Emits ``event: changed`` with the new rev whenever this page's layout hash
    changes; the editor ignores a rev that matches its own last save."""
    _guard()
    if _get_canvas(canvas_id) is None:
        abort(404)
    store = _pages()
    q: queue.Queue[int] = queue.Queue(maxsize=64)

    def on_change() -> None:
        # The listener is page-agnostic; wake the generator, which recomputes
        # this page's rev and only emits when it actually changed.
        try:
            q.put_nowait(1)
        except queue.Full:
            return

    def current_rev() -> str:
        page = store.get(canvas_id)
        return _canvas_rev(page) if page is not None else ""

    store.add_listener(on_change)

    def generate() -> Iterator[str]:
        yield ":connected\n\n"
        last_rev = current_rev()
        last_send = time.monotonic()
        try:
            while True:
                timeout = max(0.1, _STREAM_KEEPALIVE_S - (time.monotonic() - last_send))
                try:
                    q.get(timeout=timeout)
                    rev = current_rev()
                    if rev and rev != last_rev:
                        last_rev = rev
                        yield f"event: changed\ndata: {rev}\n\n"
                        last_send = time.monotonic()
                except queue.Empty:
                    yield ":keepalive\n\n"
                    last_send = time.monotonic()
        finally:
            store.remove_listener(on_change)

    return current_app.response_class(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.post("/c/<canvas_id>/save")
def save(canvas_id: str) -> Response:
    """Autosave a canvas document into its Page. Body is the full doc JSON
    (name, panel dims, appearance, elements); validated as a CanvasPage before
    it's folded into the page so a malformed element can't corrupt the store."""
    _guard()
    if _get_canvas(canvas_id) is None:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    body["id"] = canvas_id  # the path owns the id; ignore any body override
    try:
        doc = CanvasPage.model_validate(body)
    except ValidationError as err:
        return _error(400, f"invalid canvas document: {err.error_count()} problem(s)")
    page = _stamp(_doc_to_page(doc), "ui")
    _pages().save(page)
    return jsonify(
        {"status": "ok", "id": canvas_id, "elements": len(doc.els), "rev": _canvas_rev(page)}
    )


@bp.get("/c/<canvas_id>/preview.png")
def preview(canvas_id: str) -> Response:
    """Render the canvas to a PNG at its authored dims and return it.

    Screenshots the loopback ``/compose/<page_id>`` page (the shared render
    target grid pages use too) via the same Playwright path a device push uses,
    so the preview is pixel-faithful to what a panel would paint."""
    _guard()
    page = _get_canvas(canvas_id)
    if page is None or page.canvas is None:
        abort(404)
    from app.renderer import RenderRequest, render_to_png, to_loopback_url

    path = url_for("composer.compose", page_id=canvas_id)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    png = render_to_png(
        RenderRequest(url=url, viewport_w=page.canvas.w, viewport_h=page.canvas.h), pool=None
    )
    return current_app.response_class(png, mimetype="image/png")


@bp.post("/c/<canvas_id>/generate-bg")
def generate_bg(canvas_id: str) -> Response:
    """Generate an AI background image (fal.ai) for a canvas and set it as
    ``bg_image``. Body: ``{prompt (required), model?, style?, fit?}``. The image
    is stored as a local render asset; the canvas elements composite on top
    (Approach A), so the data never passes through the image model. Returns
    ``{status, bg_image, bg_fit, rev}``. 400 without a prompt or fal key; 502 on
    a fal failure."""
    from app import fal_backgrounds as fb
    from app.state.panel_store import CanvasLayout

    _guard()
    page = _get_canvas(canvas_id)
    if page is None:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return _error(400, "prompt is required")
    api_key = fb.resolve_fal_key(_registry(), current_app.config.get("SETTINGS_STORE"))
    if not api_key:
        return _error(
            400,
            "no fal.ai API key configured: set one on an installed fal-image widget, "
            "under app.fal.api_key, or in the FAL_KEY environment variable",
        )
    layout = page.canvas or CanvasLayout()
    fit = str(body.get("fit") or layout.bg_fit or "cover").strip().lower()
    if fit not in ("cover", "contain", "stretch"):
        fit = "cover"
    try:
        png = fb.generate(
            prompt,
            api_key=api_key,
            model=str(body.get("model") or fb.DEFAULT_MODEL).strip(),
            style=str(body.get("style") or "none").strip(),
            width=int(layout.w),
            height=int(layout.h),
        )
    except fb.FalError as err:
        return _error(502, f"background generation failed: {err}")
    url = fb.store_background(current_app.config["RENDERS_DIR"], png)
    layout.bg_image = url
    layout.bg_fit = fit
    page.canvas = layout
    page = _stamp(page, "ui")
    _pages().save(page)
    return jsonify({"status": "ok", "bg_image": url, "bg_fit": fit, "rev": _canvas_rev(page)})


@bp.get("/canvases.json")
def canvases() -> Response:
    """All canvas dashboards (for the editor's canvas switcher)."""
    _guard()
    out = [
        {
            "id": p.id,
            "name": p.name,
            "w": (p.canvas.w if p.canvas else 0),
            "h": (p.canvas.h if p.canvas else 0),
            "elements": (len(p.canvas.els) if p.canvas else 0),
        }
        for p in _canvas_pages()
    ]
    out.sort(key=lambda x: str(x["name"]).lower())
    return jsonify({"canvases": out})


@bp.post("/c/<canvas_id>/rename")
def rename(canvas_id: str) -> Response:
    """Rename a canvas dashboard."""
    _guard()
    page = _get_canvas(canvas_id)
    if page is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if name:
        page.name = name
        _pages().save(page)
    return jsonify({"status": "ok", "name": page.name})


@bp.post("/c/<canvas_id>/duplicate")
def duplicate(canvas_id: str) -> Response:
    """Duplicate a canvas dashboard into a new page and return its id."""
    _guard()
    page = _get_canvas(canvas_id)
    if page is None:
        abort(404)
    copy = page.model_copy(deep=True)
    copy.id = _new_id()
    copy.name = f"{page.name} copy"
    copy.device_ids = []  # a copy starts unbound
    _pages().save(copy)
    return jsonify({"status": "ok", "id": copy.id})


@bp.post("/c/<canvas_id>/delete")
def delete(canvas_id: str) -> Response:
    """Delete a canvas dashboard (only canvas pages via this route)."""
    _guard()
    deleted = _get_canvas(canvas_id) is not None and _pages().delete(canvas_id)
    return jsonify({"status": "ok", "deleted": bool(deleted)})


# -- per-dashboard image assets (the editor's Assets drawer) --------------
#
# Session-authed twins of the MCP /pages/<id>/assets endpoints, keyed on the
# canvas id (which IS the page id). Both edit the same folder, so an image the
# agent caches shows up in the drawer and vice versa. Delete cascades with the
# page (PageStore delete listener).


def _assets_data_root() -> Any:
    return current_app.config["DATA_ROOT"]


def _asset_err(message: str, status: int = 422) -> Response:
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


@bp.get("/c/<canvas_id>/assets.json")
def list_assets(canvas_id: str) -> Response:
    _guard()
    if _get_canvas(canvas_id) is None:
        abort(404)
    from app import page_assets

    return jsonify({"assets": page_assets.list_assets(_assets_data_root(), canvas_id)})


@bp.post("/c/<canvas_id>/assets")
def add_asset(canvas_id: str) -> Response:
    """Cache an image into this dashboard's folder. Either a multipart ``file``
    upload, or JSON ``{url}`` to fetch a remote image through the SSRF guard.
    Returns the stored ``{name, url, bytes}`` so the editor can drop the local
    URL into a code element instead of hotlinking."""
    _guard()
    if _get_canvas(canvas_id) is None:
        abort(404)
    from app import page_assets

    try:
        upload = request.files.get("file")
        if upload is not None:
            rec = page_assets.save_bytes(
                _assets_data_root(),
                canvas_id,
                upload.read(),
                upload.mimetype or "application/octet-stream",
            )
        else:
            body = request.get_json(silent=True) or {}
            url = str(body.get("url") or "").strip()
            if not url:
                return _asset_err("provide a 'url' or a 'file'")
            rec = page_assets.cache_url(_assets_data_root(), canvas_id, url)
    except page_assets.AssetError as err:
        return _asset_err(str(err))
    return jsonify(rec)


@bp.post("/c/<canvas_id>/assets/<name>/delete")
def delete_asset(canvas_id: str, name: str) -> Response:
    _guard()
    if _get_canvas(canvas_id) is None:
        abort(404)
    from app import page_assets

    try:
        removed = page_assets.delete_asset(_assets_data_root(), canvas_id, name)
    except page_assets.AssetError as err:
        return _asset_err(str(err))
    return jsonify({"status": "ok", "deleted": bool(removed)})


@bp.get("/devices.json")
def devices() -> Response:
    """Registered device instances a canvas can be sent to (toolbar picker).

    Each entry carries the device's real panel dims (``w``/``h``) so picking a
    target also sets the canvas resolution to match that panel, plus
    ``touch: true`` on panels with a digitizer (issue #49) so the editor can
    tell which targets a dashboard's touch actions will actually fire on."""
    _guard()
    from app.panel import device_panel

    reg = current_app.config.get("DEVICE_REGISTRY")
    out: list[dict[str, Any]] = []
    if reg is not None:
        for d in reg.all():
            if d.kind_of is None:  # instances only, not built-in kinds
                continue
            entry: dict[str, Any] = {"id": d.id, "name": str(d.manifest.get("name") or d.id)}
            panel = device_panel(d)
            if panel is not None:
                entry["w"] = panel.w
                entry["h"] = panel.h
            if d.manifest.get("touch") is True:
                entry["touch"] = True
            out.append(entry)
    out.sort(key=lambda x: str(x["name"]).lower())
    return jsonify({"devices": out})


@bp.post("/c/<canvas_id>/send")
def send(canvas_id: str) -> Response:
    """Render the canvas and push it to its bound device(s).

    Renders once at the canvas dims (shared render target) and hands the PNG
    to :meth:`PushManager.push_image` per device, which fits/quantises/packs
    and publishes through the same pipeline the Send page uses. The selected
    devices are persisted on the doc so a later push targets the same set."""
    _guard()
    page = _get_canvas(canvas_id)
    if page is None or page.canvas is None:
        abort(404)
    body = request.get_json(silent=True) or {}
    picked = body.get("device_ids")
    if isinstance(picked, list):
        page.device_ids = [str(x) for x in picked if isinstance(x, str) and x]
        _pages().save(page)
    if not page.device_ids:
        return _error(400, "no device selected")

    from app.renderer import RenderRequest, render_to_png, to_loopback_url

    path = url_for("composer.compose", page_id=canvas_id)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    png = render_to_png(
        RenderRequest(url=url, viewport_w=page.canvas.w, viewport_h=page.canvas.h), pool=None
    )

    push = current_app.config.get("PUSH_MANAGER")
    if push is None:
        return _error(503, "push pipeline unavailable")
    sent: list[str] = []
    errors: list[dict[str, str]] = []
    for did in page.device_ids:
        try:
            result = push.push_image(png, source_label=f"panels:{canvas_id}", device_id=did)
        except Exception as err:  # a renderer / broker fault shouldn't 500 the route
            errors.append({"device": did, "error": f"{type(err).__name__}: {err}"})
            continue
        if getattr(result, "status", "") in ("sent", "no_change"):
            sent.append(did)
        else:
            errors.append(
                {
                    "device": did,
                    "error": str(
                        getattr(result, "error", None) or getattr(result, "status", "failed")
                    ),
                }
            )
    return jsonify({"sent": sent, "errors": errors})


def _appearance() -> dict[str, Any]:
    """Theme / style / font options for the editor's appearance pickers,
    reusing the grid editor's registries so the lists match everywhere."""
    from app.composer import _MATRIX_STYLES
    from app.state.theme_registry import build_registry, picker_options

    user_store = current_app.config.get("USER_THEMES_STORE")
    user_themes = [t.to_registry_theme() for t in user_store.list_all()] if user_store else None
    comm_store = current_app.config.get("COMMUNITY_THEMES_STORE")
    community_themes = (
        [t.to_registry_theme() for t in comm_store.list_all()] if comm_store else None
    )
    settings = current_app.config.get("SETTINGS_STORE")
    disabled_raw = settings.get_section("app").get("disabled_theme_ids") or [] if settings else []
    disabled = {str(x) for x in disabled_raw if isinstance(x, str)}
    themes = picker_options(
        build_registry(user_themes=user_themes, community_themes=community_themes),
        disabled_ids=disabled,
    )
    fonts = [
        {"id": f.id, "name": f.name}
        for f in sorted(_registry().fonts.values(), key=lambda x: x.name.lower())
    ]
    return {"themes": themes, "styles": _MATRIX_STYLES, "fonts": fonts}


@bp.get("/catalog.json")
def catalog() -> Response:
    """Widget palette (every renderable widget with its fragments) plus the
    canvas appearance options (themes / styles / fonts) the editor's pickers
    consume."""
    _guard()
    return jsonify({"widgets": build_catalog(_registry()), "appearance": _appearance()})


@bp.post("/data.json")
def widget_data() -> Response:
    """Live data for one widget instance, so the editor previews the real
    render instead of the static sample. Body: ``{widget, options, w?, h?}``.
    Mirrors the canvas compose path exactly (resolved options -> fetch() ->
    sample fallback on error), so what the editor shows matches a Send."""
    _guard()
    body = request.get_json(silent=True) or {}
    # Raw URL source (code-element API source): fetch through the SSRF guard so
    # the editor preview matches a Send, which resolves URL sources the same way.
    url = body.get("url")
    if isinstance(url, str) and url:
        from app.net_guard import BlockedURLError, fetch_json

        raw_headers = body.get("headers")
        headers = (
            {str(k): str(v) for k, v in raw_headers.items()}
            if isinstance(raw_headers, dict)
            else None
        )
        try:
            return jsonify({"data": fetch_json(url, headers=headers)})
        except BlockedURLError as err:
            return jsonify({"data": {"error": str(err)}})
        except Exception as err:
            return jsonify({"data": {"error": f"{type(err).__name__}: {err}"}})
    widget = body.get("widget")
    if not isinstance(widget, str) or not widget:
        return jsonify({"data": None})
    options = body.get("options")
    options = options if isinstance(options, dict) else {}
    w = int(body["w"]) if isinstance(body.get("w"), int) else 600
    h = int(body["h"]) if isinstance(body.get("h"), int) else 400

    from app.composer import _fetch_plugin_data, _resolved_options
    from app.widget_samples import get_sample

    result: Any = None
    try:
        opts = _resolved_options(widget, options)
        result = _fetch_plugin_data(widget, opts, w, h, preview=False, cell_w=w, cell_h=h)
    except Exception:
        result = None
    if not isinstance(result, dict) or result.get("error"):
        sample = get_sample(widget)
        result = sample if isinstance(sample, dict) else result
    return jsonify({"data": result if isinstance(result, dict) else None})


def _materialised_options(plugin: Any) -> list[dict[str, Any]]:
    """Resolve a widget's ``cell_options`` (swapping ``choices_from`` for
    concrete choices) using the grid editor's shared machinery, so the canvas
    source-config form renders identical controls."""
    from app.page_routes import _materialize_cell_options

    return _materialize_cell_options([plugin]).get(plugin.id, [])


@bp.post("/source-form")
def source_form() -> Response:
    """Render a widget's ``cell_options`` as an HTML form fragment for the
    source-config drawer. Body: ``{key, sid, options}``. Reuses the grid
    editor's ``auto_field`` macros so the controls (location search, entity
    overrides, selects) stay identical to per-cell config."""
    _guard()
    body = request.get_json(silent=True) or {}
    key = body.get("key")
    plugin = _registry().get(key) if isinstance(key, str) else None
    if plugin is None:
        abort(404)
    values = body.get("options")
    html = render_template(
        "panels_source_form.html",
        opts=_materialised_options(plugin),
        values=values if isinstance(values, dict) else {},
        sid=str(body.get("sid") or "new"),
    )
    return current_app.response_class(html, mimetype="text/html")


@bp.post("/source-options")
def source_options() -> Response:
    """Parse a submitted source-config form into a normalised options dict,
    reusing the grid editor's per-cell coercion so complex field types
    (location search, entity overrides, multiselect) demux identically.
    Form field ``key`` names the widget; ``opt_*`` fields carry values."""
    _guard()
    from app.page_routes import _cell_options_from_form

    key = request.form.get("key")
    plugin = _registry().get(key) if isinstance(key, str) else None
    if plugin is None:
        abort(404)
    return jsonify({"options": _cell_options_from_form(plugin, request.form)})


@bp.get("/pages.json")
def pages_list() -> Response:
    """Every saved dashboard (grid AND canvas) as ``{id, name, kind}``
    rows, for the Interaction section's "Go to page" picker (issue #49).
    A tap action may target any dashboard, not just canvases."""
    _guard()
    rows = [
        {"id": p.id, "name": p.name or p.id, "kind": p.layout_kind or "grid"}
        for p in _pages().list()
    ]
    rows.sort(key=lambda r: str(r["name"]).lower())
    return jsonify({"pages": rows})


@bp.get("/ha-actions.json")
def ha_actions() -> Response:
    """Home Assistant picker data for the Interaction section (issue #49
    phase 3): the callable services and the entity list, via the ha_core
    plugin's shared connection. ``{"configured": false}`` when HA isn't
    set up, so the editor can show a hint instead of empty pickers."""
    _guard()
    from app.ha_actions import fetch_ha_actions

    return jsonify(fetch_ha_actions())


@bp.get("/c/<canvas_id>/touch-regions.json")
def touch_regions(canvas_id: str) -> Response:
    """The touch region map this canvas would produce, extracted from a
    fresh headless render of the shared ``/compose/<id>`` page (issue
    #49). Powers the editor's "Show touch targets" overlay: boxes are in
    canvas pixels (the compose render runs at the authored dims), and
    ``dangling`` lists ``@name`` references with no matching entry in a
    code element's actions map."""
    _guard()
    page = _get_canvas(canvas_id)
    if page is None or page.canvas is None:
        abort(404)
    from app.renderer import InspectRequest, RenderRequest, inspect_composed, to_loopback_url
    from app.touch_regions import EXTRACT_REGIONS_JS, normalize_regions

    path = url_for("composer.compose", page_id=canvas_id)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    try:
        raw = inspect_composed(
            InspectRequest(
                render=RenderRequest(url=url, viewport_w=page.canvas.w, viewport_h=page.canvas.h),
                script=EXTRACT_REGIONS_JS,
            ),
            pool=current_app.config.get("BROWSER_POOL"),
        )
    except Exception as err:
        return _error(502, f"extraction failed: {type(err).__name__}: {err}")
    regions = normalize_regions(raw)
    dangling = sorted({name for r in regions for name in r.get("dangling", [])})
    invalid = [
        {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"], **bad}
        for r in regions
        for bad in r.get("invalid", [])
    ]
    return jsonify({"regions": regions, "dangling": dangling, "invalid": invalid})


def _error(status: int, message: str) -> Response:
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


# Re-exported for tests that build a document without going through HTTP.
__all__ = ["CanvasPage", "Element", "bp", "register"]


def register(app: Flask) -> None:
    app.register_blueprint(bp)
