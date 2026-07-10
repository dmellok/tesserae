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

import uuid

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
from app.state.panel_store import CanvasPage, CanvasStore, Element

bp = Blueprint("panels", __name__, url_prefix="/experiments/composer")

_EXPERIMENT = "composer"


def _guard() -> None:
    """404 the whole blueprint unless the ``composer`` experiment is on."""
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)


def _registry() -> PluginRegistry:
    registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    return registry


def _store() -> CanvasStore:
    store: CanvasStore = current_app.config["PANEL_STORE"]
    return store


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@bp.get("/")
def index() -> Response | str:
    """Land on the most recent canvas, or mint a fresh one when there are
    none, so the flag always opens straight into an editable artboard."""
    _guard()
    docs = _store().list()
    if not docs:
        doc = CanvasPage(id=_new_id())
        _store().save(doc)
        return redirect(url_for("panels.editor", canvas_id=doc.id))
    return redirect(url_for("panels.editor", canvas_id=docs[0].id))


@bp.post("/new")
def create() -> Response:
    """Create a blank canvas and open it."""
    _guard()
    name = (request.form.get("name") or "Untitled Panel").strip() or "Untitled Panel"
    doc = CanvasPage(id=_new_id(), name=name)
    _store().save(doc)
    return redirect(url_for("panels.editor", canvas_id=doc.id))


@bp.get("/c/<canvas_id>")
def editor(canvas_id: str) -> str:
    """The full-window canvas editor for one document."""
    _guard()
    doc = _store().get(canvas_id)
    if doc is None:
        abort(404)
    return render_template("panels_editor.html", canvas_id=doc.id)


@bp.get("/c/<canvas_id>/doc.json")
def doc(canvas_id: str) -> Response:
    """The canvas document the editor hydrates from."""
    _guard()
    found = _store().get(canvas_id)
    if found is None:
        abort(404)
    return jsonify(found.model_dump(mode="json"))


@bp.post("/c/<canvas_id>/save")
def save(canvas_id: str) -> Response:
    """Autosave a canvas document. Body is the full doc JSON (name, panel
    dims, sources, elements); validated before it lands so a malformed
    element can't corrupt the store."""
    _guard()
    if _store().get(canvas_id) is None:
        abort(404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    body["id"] = canvas_id  # the path owns the id; ignore any body override
    try:
        doc = CanvasPage.model_validate(body)
    except ValidationError as err:
        return _error(400, f"invalid canvas document: {err.error_count()} problem(s)")
    unknown = [e.type for e in doc.els if not e.type_is_known()]
    if unknown:
        return _error(400, f"unknown element type(s): {sorted(set(unknown))}")
    _store().save(doc)
    return jsonify({"status": "ok", "id": canvas_id, "elements": len(doc.els)})


@bp.get("/catalog.json")
def catalog() -> Response:
    """Widget catalog for the editor's Data panel + bind list: every widget
    that declares a ``data_schema``, in the ``{key,name,icon,color,desc,
    fields,sample}`` shape the editor expects."""
    _guard()
    return jsonify({"widgets": build_catalog(_registry())})


def _error(status: int, message: str) -> Response:
    resp = jsonify({"error": message})
    resp.status_code = status
    return resp


# Re-exported for tests that build a document without going through HTTP.
__all__ = ["CanvasPage", "Element", "bp", "register"]


def register(app: Flask) -> None:
    app.register_blueprint(bp)
