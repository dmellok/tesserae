"""Webhook push endpoints.

Two endpoints that let external automations (Home Assistant beyond the
existing MQTT discovery, n8n, GitHub Actions, Stream Deck, cron + curl,
your phone's Shortcuts app, …) put something on a panel:

* ``POST /api/v1/push`` renders and publishes a saved dashboard.
* ``POST /api/v1/push/image`` sends one image straight to named displays,
  for when there is no dashboard behind it: a photo, a chart another tool
  generated, an already-rendered frame. Added because the only scriptable
  image push was the Companion API, which is a paired-client contract and
  isn't in the public spec, so the obvious place to look didn't have it
  (discussion #231).

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
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.wrappers import Response

from app.image_upload import (
    IMAGE_CONTENT_TYPES,
    IMAGE_FIT_MODES,
    IMAGE_MAX_EDGE,
    IMAGE_ROTATE_MODES,
    IMAGE_UPLOAD_BYTES,
    TOO_LARGE,
    validate_image,
)
from app.push import PushManager
from app.quiet_hours import device_is_quiet
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
    """Build a fresh URL-safe token. Used by the Settings → System →
    Webhook "Generate webhook token" action and by tests."""
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


# Form encoders have no boolean type, so a flag arrives as whatever the
# caller's tooling produced. Accept the spellings people actually send and
# treat everything else, including the empty string, as off.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _TRUTHY


def _timezone(settings: SettingsStore) -> Any:
    """The configured display timezone, or None to follow the host. Quiet
    hours are wall-clock windows, so they need the same zone the scheduler
    and the Settings UI use."""
    raw = str((settings.get_section("app") or {}).get("timezone") or "system").strip()
    if not raw or raw.lower() == "system":
        return None
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return None


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


@bp.post("/push/image")
def push_image() -> tuple[Response, int] | Response:
    """Push a single image straight to one or more displays.

    The sibling of ``POST /push`` for the case where there is no saved
    dashboard: a photo, a chart something else generated, a pre-rendered
    frame. Same global token, same quiet-hours policy, so a script that
    already talks to ``/push`` needs no new credential (discussion #231).

    ``multipart/form-data``:

        image                  the file (JPEG / PNG / HEIC / HEIF / WebP)
        device_ids             comma-separated, or repeated; REQUIRED
        fit                    fit | fill | blur | stretch | center (optional)
        rotate                 auto | 90 | 180 | 270, clockwise (optional)
        label                  History label, default "Webhook image"
        override_quiet_hours   send anyway during quiet hours (optional)

    ``device_ids`` is required here, unlike ``/push``. A dashboard knows
    which displays it belongs to; a loose image does not, and defaulting
    to "every panel in the house" is the wrong way to find that out.

    ``override_quiet_hours`` exists because not every image is ambient.
    A doorbell snapshot or an alarm frame is worth a refresh at 3am, and
    without an opt-out the only way to get one was to turn quiet hours
    off for the display and remember to turn them back on. Off by
    default: the caller has to say that this particular image matters
    more than the household's sleep.

    ``rotate`` covers the case where the image and the panel disagree
    about which way is up: ``auto`` turns a portrait image a quarter to
    fill a landscape panel (and the reverse), the explicit angles turn it
    clockwise by that much regardless. Unset leaves the orientation alone,
    which is what an image carrying text wants.
    """
    settings = _settings()
    stored = _stored_token(settings)
    if not stored:
        return jsonify({"status": "disabled", "error": "webhook token not generated"}), 503
    presented = _presented_token(request)
    if not presented or not secrets.compare_digest(presented, stored):
        return jsonify({"status": "unauthorized"}), 401

    upload = request.files.get("image")
    if upload is None:
        return (
            jsonify({"status": "bad_request", "error": "an 'image' file part is required"}),
            400,
        )
    image_bytes = upload.read()
    problem = validate_image(image_bytes, upload.mimetype)
    if problem == TOO_LARGE:
        return (
            jsonify(
                {
                    "status": "bad_request",
                    "error": (
                        f"image exceeds the limits ({IMAGE_UPLOAD_BYTES} bytes encoded, "
                        f"{IMAGE_MAX_EDGE}px longest edge)"
                    ),
                }
            ),
            413,
        )
    if problem is not None:
        return (
            jsonify(
                {
                    "status": "bad_request",
                    "error": (
                        "unsupported or undecodable image; accepted types: "
                        + ", ".join(IMAGE_CONTENT_TYPES)
                    ),
                }
            ),
            415,
        )

    raw_devices = request.form.getlist("device_ids") or []
    if len(raw_devices) == 1:
        raw_devices = [d.strip() for d in raw_devices[0].split(",")]
    device_ids = [d.strip() for d in raw_devices if d.strip()]
    if not device_ids:
        return (
            jsonify({"status": "bad_request", "error": "device_ids is required"}),
            400,
        )
    registry = current_app.config.get("DEVICE_REGISTRY")
    unknown = [
        d
        for d in device_ids
        if registry is None or getattr(registry.get(d), "kind_of", None) is None
    ]
    if unknown:
        return (
            jsonify(
                {
                    "status": "not_found",
                    "error": f"unknown display(s): {', '.join(sorted(unknown))}",
                }
            ),
            404,
        )

    fit = (request.form.get("fit") or "").strip().lower() or None
    if fit is not None and fit not in IMAGE_FIT_MODES:
        return (
            jsonify(
                {
                    "status": "bad_request",
                    "error": f"fit must be one of {', '.join(IMAGE_FIT_MODES)}",
                }
            ),
            400,
        )
    rotate = (request.form.get("rotate") or "").strip().lower() or None
    if rotate is not None and rotate not in IMAGE_ROTATE_MODES:
        return (
            jsonify(
                {
                    "status": "bad_request",
                    "error": f"rotate must be one of {', '.join(IMAGE_ROTATE_MODES)}",
                }
            ),
            400,
        )
    label = (request.form.get("label") or "").strip() or "Webhook image"
    override = _truthy(request.form.get("override_quiet_hours"))

    # Quiet hours are honoured per display, matching /push: a webhook call
    # is automation rather than someone standing at the panel. Displays that
    # are quiet are reported back so the caller can tell "skipped" from
    # "failed" rather than inferring it from a count.
    manager = _push_manager()
    app_settings = settings.get_section("app") or {}
    now = datetime.now(UTC)
    tz = _timezone(settings)
    sent: list[str] = []
    quiet: list[str] = []
    failed: list[dict[str, str]] = []
    for device_id in device_ids:
        device = registry.get(device_id) if registry is not None else None
        if not override and device is not None and device_is_quiet(app_settings, device, now, tz):
            quiet.append(device_id)
            continue
        result = manager.push_image(
            image_bytes,
            source_label=label,
            device_id=device_id,
            fit=fit,
            rotate=rotate,
            source="webhook",
        )
        if result.status in ("sent", "no_change"):
            sent.append(device_id)
        else:
            failed.append({"device_id": device_id, "error": result.error or result.status})

    if failed:
        http_status = 500
        status = "failed"
    elif sent:
        http_status = 200
        status = "sent"
    else:
        http_status = 202
        status = "quiet"
    return (
        jsonify({"status": status, "sent": sent, "quiet": quiet, "failed": failed}),
        http_status,
    )


def register(app: Flask) -> None:
    app.register_blueprint(bp)
