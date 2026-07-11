"""MCP API: a token-authenticated JSON surface for building canvas dashboards.

This is the *server side* of Tesserae's MCP integration. The stdio bridge an
agent connects to is a separate package, ``tesserae-mcp``
(https://github.com/dmellok/tesserae-mcp), which talks to this surface over HTTP.
It lets an AI agent do what the freeform canvas editor does, but by writing the
canvas document directly and rendering a preview PNG to see the result: list
widgets/devices, create + edit canvas dashboards, render a preview, push to a panel.

Everything here reuses the panels editor's own helpers (:mod:`app.panels_routes`)
so the agent path and the UI path stay identical; nothing is reimplemented.

Auth (checked in :func:`_gate`): the surface is reachable from loopback without a
token (a co-located agent needs zero config), OR with the stored MCP token
presented as ``Authorization: Bearer <token>`` (a remote agent). The whole
blueprint is gated behind the ``mcp`` experiment flag (opt-in), so it 404s until
switched on in Settings.
"""

from __future__ import annotations

import secrets
from typing import Any

from flask import Blueprint, Flask, abort, current_app, jsonify, request, url_for
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app import experiments
from app import panels_routes as _pr
from app.auth import _is_loopback
from app.panels_schema import build_catalog
from app.state.panel_store import CanvasLayout, CanvasPage
from app.state.settings_store import SettingsStore
from app.webhook_routes import _presented_token, generate_token

bp = Blueprint("mcp_api", __name__, url_prefix="/api/mcp")

_EXPERIMENT = "mcp"
# Where the MCP token lives in settings. ``_secret`` suffix → SecretBox-encrypted
# at rest, same convention as the webhook token.
_TOKEN_KEY = "mcp_token_secret"


# -- token storage ------------------------------------------------------


def _settings() -> SettingsStore:
    store: SettingsStore = current_app.config["SETTINGS_STORE"]
    return store


def mcp_token(settings: SettingsStore) -> str | None:
    """The stored MCP token, or None when one hasn't been generated yet."""
    raw = settings.get_section("app").get(_TOKEN_KEY) or ""
    return raw.strip() or None


def rotate_token(settings: SettingsStore) -> str:
    """Generate a fresh MCP token, persist it, and return it."""
    token = generate_token()
    settings.patch_section("app", {_TOKEN_KEY: token})
    return token


# -- auth + experiment gate ---------------------------------------------


@bp.before_request
def _gate() -> Response | None:
    """404 when the experiment is off; otherwise allow loopback or a valid token."""
    if not experiments.is_enabled(_EXPERIMENT):
        abort(404)
    if _is_loopback():
        return None
    stored = mcp_token(_settings())
    presented = _presented_token(request)
    if stored and presented and secrets.compare_digest(presented, stored):
        return None
    return _err(
        401,
        "unauthorized: present the MCP token as 'Authorization: Bearer <token>' "
        "(generate one in Settings → System → MCP), or call from localhost.",
    )


def _err(status: int, message: str, **extra: Any) -> Response:
    resp = jsonify({"error": message, **extra})
    resp.status_code = status
    return resp


# -- catalog / widget options -------------------------------------------


@bp.get("/catalog")
def catalog() -> Response:
    """Every renderable widget (with its fragments) plus theme/style/font options,
    so the agent knows what it can place and how to style the canvas."""
    return jsonify({"widgets": build_catalog(_pr._registry()), "appearance": _pr._appearance()})


@bp.get("/widgets/<key>/options")
def widget_options(key: str) -> Response:
    """The configurable options for one widget (its ``cell_options``), so the
    agent can set an element's ``options`` correctly (e.g. a weather location)."""
    plugin = _pr._registry().get(key)
    if plugin is None:
        return _err(404, f"unknown widget {key!r}")
    return jsonify({"key": key, "options": _pr._materialised_options(plugin)})


# -- devices ------------------------------------------------------------


@bp.get("/devices")
def devices() -> Response:
    """Registered display instances a canvas can be pushed to, with panel dims."""
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
            out.append(entry)
    out.sort(key=lambda x: str(x["name"]).lower())
    return jsonify({"devices": out})


# -- canvas pages -------------------------------------------------------


@bp.get("/pages")
def list_pages() -> Response:
    """All canvas (freeform) dashboards, with size, element count, and targets."""
    out = [
        {
            "id": p.id,
            "name": p.name,
            "w": (p.canvas.w if p.canvas else 0),
            "h": (p.canvas.h if p.canvas else 0),
            "elements": (len(p.canvas.els) if p.canvas else 0),
            "device_ids": list(p.device_ids),
            "created_by": p.created_by,
        }
        for p in _pr._canvas_pages()
    ]
    out.sort(key=lambda x: str(x["name"]).lower())
    return jsonify({"pages": out})


@bp.post("/pages")
def create_page() -> Response:
    """Create an empty canvas dashboard. Body: ``{name?, w?, h?}``. The page is
    marked ``created_by="mcp"`` so it's flagged as agent-made in the UI."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "Untitled Panel").strip() or "Untitled Panel"
    page = _pr._new_canvas_page(name)
    page.created_by = "mcp"
    if page.canvas is not None:
        w, h = body.get("w"), body.get("h")
        if isinstance(w, int) and w > 0:
            page.canvas.w = w
        if isinstance(h, int) and h > 0:
            page.canvas.h = h
    _pr._pages().save(page)
    return jsonify({"id": page.id, "name": page.name})


@bp.get("/pages/<page_id>/canvas")
def get_canvas(page_id: str) -> Response:
    """The full canvas document (artboard size, appearance, and every element)."""
    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    return jsonify(_pr._as_doc(page).model_dump(mode="json"))


@bp.put("/pages/<page_id>/canvas")
def set_canvas(page_id: str) -> Response:
    """Replace a canvas dashboard's document. Body is the canvas layout
    (``{w,h,theme,style,font,bg,bg_image,bg_fit,els[],name?}``). ``id`` and bound
    devices are preserved from the server; an invalid document returns 422 with
    field-level messages so the agent can correct it."""
    page = _pr._get_canvas(page_id)
    if page is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _err(400, "body must be a JSON object")
    # Force server-owned fields; the agent supplies the layout only.
    doc_in = {**body, "id": page_id, "name": str(body.get("name") or page.name)}
    doc_in.setdefault("device_ids", list(page.device_ids))
    try:
        doc = CanvasPage.model_validate(doc_in)
    except ValidationError as exc:
        return _err(422, "invalid canvas document", details=exc.errors(include_url=False))
    new_page = _pr._doc_to_page(doc)
    new_page.created_by = page.created_by  # preserve provenance
    _pr._pages().save(new_page)
    return jsonify(_pr._as_doc(new_page).model_dump(mode="json"))


# -- preview + push -----------------------------------------------------


def _render_png(page_id: str, layout: CanvasLayout) -> bytes:
    """Screenshot the shared ``/compose/<id>`` target at the canvas dims, the same
    path a device push and the editor preview use."""
    from app.renderer import RenderRequest, render_to_png, to_loopback_url

    path = url_for("composer.compose", page_id=page_id)
    url = to_loopback_url(request.host_url.rstrip("/") + path)
    return render_to_png(
        RenderRequest(url=url, viewport_w=layout.w, viewport_h=layout.h),
        pool=current_app.config.get("BROWSER_POOL"),
    )


@bp.get("/pages/<page_id>/preview.png")
def preview(page_id: str) -> Response:
    """Render the canvas to a PNG at its authored dims (the agent's feedback loop)."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    png = _render_png(page_id, page.canvas)
    return current_app.response_class(png, mimetype="image/png")


@bp.post("/pages/<page_id>/push")
def push(page_id: str) -> Response:
    """Render the canvas and push it to explicit devices. Body: ``{device_ids: []}``
    (required — push is never implicit). Returns which devices got it and errors."""
    page = _pr._get_canvas(page_id)
    if page is None or page.canvas is None:
        return _err(404, f"no canvas dashboard {page_id!r}")
    body = request.get_json(silent=True) or {}
    picked = body.get("device_ids")
    device_ids = (
        [str(x) for x in picked if isinstance(x, str) and x] if isinstance(picked, list) else []
    )
    if not device_ids:
        return _err(400, "device_ids is required (push is always explicit)")

    manager = current_app.config.get("PUSH_MANAGER")
    if manager is None:
        return _err(503, "push pipeline unavailable")
    png = _render_png(page_id, page.canvas)
    sent: list[str] = []
    errors: list[dict[str, str]] = []
    for did in device_ids:
        try:
            result = manager.push_image(png, source_label=f"mcp:{page_id}", device_id=did)
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


def register(app: Flask) -> None:
    app.register_blueprint(bp)
