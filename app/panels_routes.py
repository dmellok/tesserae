"""Panels canvas editor routes (issue #60), behind the ``composer``
experiment flag.

A WYSIWYG editor where widgets are data sources and users bind individual
fields to freely-placed visual elements. This module is the server shell for
the experimental build: it serves the editor page and the widget-catalog
endpoint the editor's Data panel consumes. The editor front-end, canvas page
persistence, and the canvas render path land in later phases.

Everything here is gated by :func:`app.experiments.is_enabled("composer")`,
checked per request so toggling the flag needs no restart. When the flag is
off the routes 404, so the feature stays invisible until switched on. Admin
auth still applies via the global before-request gate (these paths are not
loopback-exempt).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from flask import Blueprint, Flask, abort, current_app, jsonify, render_template
from werkzeug.wrappers import Response

from app import experiments
from app.panels_schema import build_catalog
from app.plugin_loader import PluginRegistry

bp = Blueprint("panels", __name__, url_prefix="/experiments/composer")

_EXPERIMENT = "composer"


def _guard() -> None:
    """404 the whole blueprint unless the ``composer`` experiment is on."""
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)


def _registry() -> PluginRegistry:
    registry: PluginRegistry = current_app.config["PLUGIN_REGISTRY"]
    return registry


@bp.get("/")
def editor() -> str:
    """The full-window canvas editor shell."""
    _guard()
    return render_template("panels_editor.html")


@bp.get("/catalog.json")
def catalog() -> Response:
    """Widget catalog for the editor's Data panel + bind list: every widget
    that declares a ``data_schema``, in the ``{key,name,icon,color,desc,
    fields,sample}`` shape the editor expects."""
    _guard()
    return jsonify({"widgets": build_catalog(_registry())})


def register(app: Flask) -> None:
    app.register_blueprint(bp)
