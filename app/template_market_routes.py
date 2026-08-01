"""Browse + install community templates (server-side proxy of api.tesserae.ink).

Routes under ``/plugins/templates``, gated on the ``templates`` experiment AND
the master online-features switch. The browser never talks to
api.tesserae.ink directly: the catalog is proxied (consistent with how widget
install counts are fetched) and installs re-fetch the doc server-side.

``GET /plugins/templates/`` is the dedicated Browse page: templates grouped
by resolution, each group labelled with the known devices at those dims, and
the user's own device resolutions pinned first.
"""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request, url_for

from app import experiments, online, template_market

bp = Blueprint("template_market", __name__, url_prefix="/plugins/templates")


@bp.before_request
def _gate() -> Response | tuple[Response, int] | None:
    if not experiments.is_enabled("templates"):
        return Response("Not found", status=404)
    # The HTML page renders its own friendly offline notice; the data/install
    # endpoints hard-fail so nothing silently no-ops.
    if request.endpoint == "template_market.page":
        return None
    settings = current_app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return jsonify({"error": "online features are disabled in Settings"}), 403
    return None


@bp.get("/")
def page() -> str:
    """The Templates page: client-rendered groups (resolution > devices) fed
    by ``/plugins/templates/index.json``; server supplies the device-name map
    and which resolutions the user actually owns."""
    settings = current_app.config.get("SETTINGS_STORE")
    devices = current_app.config.get("DEVICE_REGISTRY")
    return render_template(
        "templates_browse.html",
        online_enabled=online.online_enabled(settings),
        resolution_devices_json=json.dumps(template_market.resolution_device_labels()),
        my_resolutions_json=json.dumps(template_market.registered_device_resolutions(devices)),
    )


def _installed_records() -> dict[str, Any]:
    marketplace = current_app.config.get("MARKETPLACE")
    return marketplace.installed() if marketplace is not None else {}


@bp.get("/index.json")
def index() -> Response | tuple[Response, int]:
    """The approved-template catalog, proxied with each entry annotated with
    whether its requirements are already satisfied here."""
    if request.args.get("refresh") in ("1", "true"):
        online.clear_template_index_cache()
    payload = online.fetch_template_index()
    if payload is None:
        return jsonify({"error": "template catalog unreachable"}), 502
    registry = current_app.config.get("PLUGIN_REGISTRY")
    installed = _installed_records()
    api_base = online.API_BASE.rstrip("/")
    entries = []
    for entry in payload.get("templates") or []:
        entry = dict(entry)
        entry["missing_requires"] = template_market.missing_requirements(
            {"requires": entry.get("requires") or []}, installed, registry
        )
        preview = str(entry.get("preview_url") or "")
        if preview.startswith("/"):
            entry["preview_url"] = api_base + preview
        entries.append(entry)
    return jsonify({"templates": entries})


def _template_or_error(slug: str) -> tuple[dict[str, Any] | None, tuple[Response, int] | None]:
    """Fetch an approved template server-side, or the error to return."""
    try:
        payload = online.fetch_template_doc(slug)
    except online.TemplateRevokedError:
        return None, (jsonify({"error": "this template was removed by the moderators"}), 410)
    if payload is None or not isinstance(payload.get("template"), dict):
        return None, (jsonify({"error": "template could not be fetched"}), 502)
    return payload["template"], None


@bp.get("/<slug>/inputs")
def inputs_form(slug: str) -> Response | tuple[Response, int]:
    """The install form for one template, as an HTML fragment.

    Each declared input is resolved against THIS install's widget option
    schemas, so an entity question renders as a picker over the installer's own
    Home Assistant entities rather than a text box. See
    :func:`app.template_market.resolve_input_specs`."""
    template, err = _template_or_error(slug)
    if err is not None or template is None:
        return err or (jsonify({"error": "template could not be fetched"}), 502)
    specs = template_market.resolve_input_specs(template, current_app.config.get("PLUGIN_REGISTRY"))
    html = render_template("template_inputs_form.html", specs=specs)
    return current_app.response_class(html, mimetype="text/html")


@bp.post("/report")
def report() -> Response | tuple[Response, int]:
    """Ask for a template to be taken down. Anyone can file one, including the
    install that submitted it (how an author pulls their own work back); the
    request goes to the same human review queue rather than acting directly."""
    body = request.get_json(silent=True) or {}
    slug = str(body.get("slug") or "").strip()
    if not slug:
        return jsonify({"error": "slug is required"}), 400
    reason = str(body.get("reason") or "").strip()[:1000]
    sent = online.report_template(
        slug,
        reason,
        current_app.config.get("INSTALL_ID"),
        current_app.config.get("APP_VERSION"),
    )
    event_log = current_app.config.get("EVENT_LOG")
    if event_log is not None:
        event_log.record(
            type="telemetry",
            source="template_report",
            target=slug,
            status="sent" if sent else "failed",
            extra={"reason": reason[:200]},
        )
    if not sent:
        return jsonify({"error": "couldn't reach the template service"}), 502
    return jsonify({"status": "received"})


@bp.post("/install")
def install() -> Response | tuple[Response, int]:
    # Two shapes: the install modal posts the rendered form (so complex
    # controls demux through the shared coercion), while API/JSON callers post
    # already-typed values.
    if request.form:
        slug = str(request.form.get("slug") or "").strip()
        if not slug:
            return jsonify({"error": "slug is required"}), 400
        template, err = _template_or_error(slug)
        if err is not None or template is None:
            return err or (jsonify({"error": "template could not be fetched"}), 502)
        specs = template_market.resolve_input_specs(
            template, current_app.config.get("PLUGIN_REGISTRY")
        )
        inputs: dict[str, Any] = template_market.coerce_input_values(specs, request.form)
    else:
        body = request.get_json(silent=True) or {}
        slug = str(body.get("slug") or "").strip()
        if not slug:
            return jsonify({"error": "slug is required"}), 400
        raw_inputs = body.get("inputs") or {}
        if not isinstance(raw_inputs, dict):
            return jsonify({"error": "inputs must be an object"}), 400
        inputs = raw_inputs
    try:
        page = template_market.install_template(
            slug,
            inputs,
            pages_store=current_app.config["PAGE_STORE"],
            installed_records=_installed_records(),
            registry=current_app.config.get("PLUGIN_REGISTRY"),
        )
    except online.TemplateRevokedError:
        return jsonify({"error": "this template was removed by the moderators"}), 410
    except template_market.TemplateInstallError as err:
        message = str(err)
        status = 409 if message.startswith("missing required") else 502
        return jsonify({"error": message}), status

    # Anonymous install count + /events row, mirroring the widget flow.
    sent = online.report_template_install(
        slug, current_app.config.get("INSTALL_ID"), current_app.config.get("APP_VERSION")
    )
    event_log = current_app.config.get("EVENT_LOG")
    if event_log is not None:
        event_log.record(
            type="telemetry",
            source="template_install",
            target=slug,
            status="sent" if sent else "failed",
            extra={"page": page.id},
        )
    # Hand back the editor URL rather than letting the client assemble one:
    # a hardcoded path in JS silently 404s if the route ever moves.
    return jsonify(
        {
            "page_id": page.id,
            "name": page.name,
            "page_url": url_for("panels.editor", canvas_id=page.id),
        }
    )
