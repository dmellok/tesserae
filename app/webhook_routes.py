"""Webhook push endpoint.

A single ``POST /api/v1/push`` endpoint that lets external automations
(Home Assistant beyond the existing MQTT discovery, n8n, GitHub
Actions, Stream Deck, cron + curl, your phone's Shortcuts app, …)
trigger a render-and-push of a saved dashboard.

Auth model: one global token, stored in ``settings.app.webhook_token``
(masked in the Settings UI like other ``_secret`` values). The caller
sends it as ``Authorization: Bearer <token>`` or
``X-Tesserae-Token: <token>``. The token is generated on demand from
the Settings UI; until generated, every webhook call returns 401.

Request body (JSON or form):

    {
        "page_id": "hallway",
        "device_ids": ["pi_kitchen", "pi_bedroom"]   // optional
    }

``device_ids`` is optional. When omitted, the push fans out to every
device the page is bound to (the same default behaviour the Send page
uses).

Quiet hours apply. A webhook call is automation, not user intent, so
the underlying push goes through with ``respect_quiet_hours=True``;
when every bound device is currently quiet the response is a 202 with
``status="quiet"`` rather than a 200 ``"sent"``. The caller can
distinguish.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.wrappers import Response

from app.push import PushManager
from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

bp = Blueprint("webhook", __name__, url_prefix="/api/v1")


# How many bytes of entropy the generated token carries. 32 hex chars
# (16 bytes) is plenty for a non-public-facing API key and short
# enough to be copy-pasteable.
TOKEN_BYTES = 16


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _push_manager() -> PushManager:
    return current_app.config["PUSH_MANAGER"]  # type: ignore[no-any-return]


def generate_token() -> str:
    """Build a fresh URL-safe token. Used by the Settings → Server →
    App "Generate webhook token" action and by tests."""
    return secrets.token_hex(TOKEN_BYTES)


def _stored_token(settings: SettingsStore) -> str | None:
    """Read the token off settings. ``get_section`` returns raw on-disk
    keys (with the ``_secret`` suffix intact for secret fields), so we
    look up ``webhook_token_secret`` directly."""
    section = settings.get_section("app")
    raw = section.get("webhook_token_secret") or section.get("webhook_token") or ""
    return raw.strip() or None


def _presented_token(req: Any) -> str | None:
    """Pull a token from either ``Authorization: Bearer …`` (preferred
    by most automation tools) or ``X-Tesserae-Token`` (simpler when
    you can't customise the auth header)."""
    auth = (req.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    direct = (req.headers.get("X-Tesserae-Token") or "").strip()
    return direct or None


def _body() -> dict[str, Any]:
    """Accept either JSON or form-encoded, many automation tools
    can't easily switch their content type."""
    if request.is_json:
        data = request.get_json(silent=True) or {}
        return data if isinstance(data, dict) else {}
    return dict(request.form)


@bp.post("/push")
def push() -> tuple[Response, int] | Response:
    """Render + publish a saved Page. Authenticated by token; honours
    per-device quiet hours."""
    settings = _settings()
    stored = _stored_token(settings)
    if not stored:
        return jsonify({"status": "disabled", "error": "webhook token not generated"}), 503
    presented = _presented_token(request)
    if not presented or not secrets.compare_digest(presented, stored):
        # Generic 401, the response shouldn't help an attacker work
        # out whether they're hitting the right host with the wrong
        # token, vs hitting the wrong host entirely.
        return jsonify({"status": "unauthorized"}), 401

    body = _body()
    page_id = (body.get("page_id") or "").strip()
    if not page_id:
        return jsonify({"status": "bad_request", "error": "page_id is required"}), 400

    raw_devices = body.get("device_ids")
    device_ids: set[str] | None = None
    if raw_devices is not None:
        if isinstance(raw_devices, str):
            raw_devices = [d.strip() for d in raw_devices.split(",") if d.strip()]
        if not isinstance(raw_devices, list):
            return (
                jsonify({"status": "bad_request", "error": "device_ids must be a list"}),
                400,
            )
        device_ids = {str(d).strip() for d in raw_devices if str(d).strip()}
        if not device_ids:
            device_ids = None

    result = _push_manager().push(
        page_id,
        device_ids=device_ids,
        respect_quiet_hours=True,
        source="webhook",
    )

    # Map PushResult.status → HTTP code so callers can branch on the
    # response.status_code without parsing the body. ``quiet`` is a
    # successful skip, not a failure, so it gets 202 Accepted.
    http_status = {
        "sent": 200,
        "quiet": 202,
        "busy": 409,
        "not_found": 404,
        "failed": 500,
    }.get(result.status, 500)
    payload = {
        "status": result.status,
        "page_id": result.page_id,
        "duration_s": result.duration_s,
        "event_id": result.event_id,
        "error": result.error,
    }
    return jsonify(payload), http_status


def register(app: Flask) -> None:
    app.register_blueprint(bp)
