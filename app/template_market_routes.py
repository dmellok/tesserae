"""Browse + install community templates (server-side proxy of api.tesserae.ink).

Routes under ``/plugins/templates``, gated on the ``templates`` experiment AND
the master online-features switch. The browser never talks to
api.tesserae.ink directly: the catalog is proxied (consistent with how widget
install counts are fetched) and installs re-fetch the doc server-side.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from app import experiments, online, template_market

bp = Blueprint("template_market", __name__, url_prefix="/plugins/templates")


@bp.before_request
def _gate() -> Response | tuple[Response, int] | None:
    if not experiments.is_enabled("templates"):
        return Response("Not found", status=404)
    settings = current_app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return jsonify({"error": "online features are disabled in Settings"}), 403
    return None


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


@bp.post("/install")
def install() -> Response | tuple[Response, int]:
    body = request.get_json(silent=True) or {}
    slug = str(body.get("slug") or "").strip()
    if not slug:
        return jsonify({"error": "slug is required"}), 400
    inputs = body.get("inputs") or {}
    if not isinstance(inputs, dict):
        return jsonify({"error": "inputs must be an object"}), 400
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
    return jsonify({"page_id": page.id, "name": page.name})
