"""Share a canvas dashboard to the template marketplace (api.tesserae.ink).

Two endpoints behind the ``templates`` experiment flag, both canvas-only:

  POST /panels/c/<id>/share/prepare   run the export sanitizer + lint + render
                                      quality gate; returns everything the
                                      Share dialog shows (redactions, suggested
                                      inputs, blocking problems, pseudonym).
  POST /panels/c/<id>/share/submit    rebuild + validate server-side (never
                                      trust the dialog's copy), render the
                                      preview PNG, submit to api.tesserae.ink.

Submissions are explicit user actions; the privacy contract lives in
:mod:`app.online` and docs/privacy.md.
"""

from __future__ import annotations

import base64
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, request

from app import experiments, online, template_export

bp = Blueprint("template_share", __name__)

_MAX_TAGS = 8


@bp.before_request
def _gate() -> Response | None:
    if not experiments.is_enabled("templates"):
        return Response("Not found", status=404)
    return None


def _get_canvas_page(page_id: str) -> Any | None:
    page = current_app.config["PAGE_STORE"].get(page_id)
    if page is None or page.layout_kind != "canvas" or page.canvas is None:
        return None
    return page


def _export(page: Any) -> dict[str, Any]:
    marketplace = current_app.config.get("MARKETPLACE")
    installed = marketplace.installed() if marketplace is not None else {}
    return template_export.build_template(
        page,
        registry=current_app.config["PLUGIN_REGISTRY"],
        installed_records=installed,
        data_root=current_app.config["DATA_ROOT"],
    )


def _quality(page_id: str, page: Any) -> dict[str, Any]:
    """Render-quality warnings for the dialog (overflow / dead touch / bad
    icons). Best-effort: a renderer hiccup shouldn't block sharing prep."""
    from app.mcp_api import build_render_report

    try:
        report = build_render_report(page_id, page, host_url=request.host_url)
    except Exception as err:
        return {"available": False, "error": f"{type(err).__name__}: {err}"}
    overflow = [
        e["id"] for e in report.get("elements") or [] if e.get("overflow_x") or e.get("overflow_y")
    ]
    return {
        "available": True,
        "overflow": overflow,
        "icon_invalid": report.get("icon_invalid") or [],
        "tap_invalid": report.get("tap_invalid") or [],
    }


@bp.post("/panels/c/<page_id>/share/prepare")
def prepare(page_id: str) -> Response | tuple[Response, int]:
    page = _get_canvas_page(page_id)
    if page is None:
        return jsonify({"error": "no such canvas dashboard"}), 404
    try:
        result = _export(page)
    except template_export.ExportBlocked as err:
        return jsonify({"blocking": err.problems, "template": None})
    settings = current_app.config.get("SETTINGS_STORE")
    enabled = online.online_enabled(settings)
    author = None
    if enabled:
        author = online.fetch_template_author(current_app.config.get("INSTALL_ID"))
    return jsonify(
        {
            "template": result["template"],
            "inputs_suggested": result["inputs_suggested"],
            "redactions": result["redactions"],
            "lint": result["lint"],
            "blocking": result["blocking"],
            "quality": _quality(page_id, page),
            "online": enabled,
            "author": author,
        }
    )


def _clean_inputs(raw: Any, template: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate the dialog's finalized inputs against the template's element
    ids and the shared schema shape. Returns (inputs, problems)."""
    problems: list[str] = []
    el_ids = {el.get("id") for el in template["canvas"].get("els") or []}
    inputs: list[dict[str, Any]] = []
    if not isinstance(raw, list) or len(raw) > 20:
        return [], ["inputs must be a list of at most 20 entries"]
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            problems.append("each input must be an object")
            continue
        name = str(item.get("name") or "")
        if not name or name in seen:
            problems.append(f"invalid or duplicate input name {name!r}")
            continue
        seen.add(name)
        targets = item.get("targets")
        if not isinstance(targets, list) or not targets:
            problems.append(f"input {name}: at least one target is required")
            continue
        for target in targets:
            if not isinstance(target, dict) or target.get("el") not in el_ids:
                problems.append(f"input {name}: target references an unknown element")
                break
        else:
            inputs.append(
                {
                    "name": name,
                    "label": str(item.get("label") or name),
                    "type": str(item.get("type") or "string"),
                    "secret": bool(item.get("secret")),
                    "required": bool(item.get("required")),
                    "default": item.get("default", ""),
                    "choices": item.get("choices") or [],
                    "targets": targets,
                }
            )
    return inputs, problems


@bp.post("/panels/c/<page_id>/share/submit")
def submit(page_id: str) -> Response | tuple[Response, int]:
    page = _get_canvas_page(page_id)
    if page is None:
        return jsonify({"error": "no such canvas dashboard"}), 404
    settings = current_app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return jsonify({"error": "online features are disabled in Settings"}), 403

    body = request.get_json(silent=True) or {}
    try:
        result = _export(page)  # rebuild server-side; never trust the dialog's copy
    except template_export.ExportBlocked as err:
        return jsonify({"error": "; ".join(err.problems)}), 400
    if result["blocking"]:
        return jsonify({"error": "; ".join(result["blocking"])}), 400

    template = result["template"]
    title = str(body.get("title") or template["title"]).strip()
    if not 1 <= len(title) <= 80:
        return jsonify({"error": "title must be 1-80 characters"}), 400
    template["title"] = title
    template["description"] = str(body.get("description") or "")[:1000]
    tags = body.get("tags") or []
    if not isinstance(tags, list) or len(tags) > _MAX_TAGS:
        return jsonify({"error": f"at most {_MAX_TAGS} tags"}), 400
    template["tags"] = [str(t).strip().lower() for t in tags if str(t).strip()]
    inputs, problems = _clean_inputs(body.get("inputs") or [], template)
    if problems:
        return jsonify({"error": "; ".join(problems)}), 400
    template["inputs"] = inputs

    # Schema validation: same contract the server enforces.
    import json as _json
    from pathlib import Path

    import jsonschema

    schema_path = Path(current_app.root_path).parent / "schema" / "template.schema.json"
    try:
        jsonschema.validate(template, _json.loads(schema_path.read_text(encoding="utf-8")))
    except jsonschema.ValidationError as err:
        return jsonify({"error": f"template failed validation: {err.message}"}), 400

    from app.mcp_api import _render_png

    try:
        png = _render_png(page_id, page.canvas)
    except Exception as err:
        return jsonify({"error": f"preview render failed: {type(err).__name__}"}), 502

    try:
        ack = online.submit_template(
            template,
            base64.b64encode(png).decode(),
            current_app.config.get("INSTALL_ID"),
            current_app.config.get("APP_VERSION"),
        )
    except online.TemplateSubmitError as err:
        return jsonify({"error": str(err)}), 502

    event_log = current_app.config.get("EVENT_LOG")
    if event_log is not None:
        event_log.record(
            type="telemetry",
            source="template_share",
            target=ack.get("slug") or page_id,
            status="submitted",
            extra={"page": page_id, "title": title},
        )
    return jsonify(ack)
