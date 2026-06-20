"""REST API transport for non-MQTT clients.

The MQTT-as-default story has too much setup tax for new users (run a
broker, edit config, paste creds into every firmware). This module
adds an HTTP-polled alternative under ``/api/v1/device/*`` that maps
one-to-one to the existing MQTT contract:

* ``GET /api/v1/device/<id>/frame``     <- MQTT subscribe to retained frame topic
* ``POST /api/v1/device/<id>/status``   <- MQTT publish to status_topic
* ``POST /api/v1/device/<id>/log``      <- optional client diagnostics
* ``POST /api/v1/device/register``      <- first-boot pairing flow (no MQTT analogue;
                                           MQTT relies on the user knowing the
                                           broker creds out of band)

Auth: per-device bearer token, same primitive TRMNL devices already
use through ``/api/display``. The token is stored on the device
instance manifest as ``access_token`` and the same constant-time
compare pattern checks it.

Frame fetch uses ``If-None-Match`` for cache busting: a freshly-woken
deep-sleep client whose composition hasn't changed gets a 304 and
skips the .bin fetch + panel paint entirely (saves ~10s of Spectra 6
refresh and the matching battery hit). The MQTT retained-message
analogue would deliver the same payload again whether anything changed
or not.

The status POST's response piggybacks the latest server-side device
config AND a ``next_poll_s`` field telling the firmware when to wake
again. One round-trip per wake; the firmware doesn't need to make a
separate config poll. The ``next_poll_s`` value is the device's
configured ``sleep_interval_s`` (when one exists in the kind's
config_schema), or a reasonable per-transport default.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.wrappers import Response

from app.device_loader import Device, DeviceRegistry
from app.device_service import create_instance, generate_access_token
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog
from app.state.pairing_store import PairingStore
from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

bp = Blueprint("rest_api", __name__, url_prefix="/api/v1/device")


# -- registry helpers ----------------------------------------------------


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _renderers() -> RendererRegistry:
    return current_app.config["RENDERER_REGISTRY"]  # type: ignore[no-any-return]


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _events() -> EventLog | None:
    return current_app.config.get("EVENT_LOG")


def _pairings() -> PairingStore:
    return current_app.config["PAIRING_STORE"]  # type: ignore[no-any-return]


def _device_status_cache() -> dict[str, dict[str, Any]]:
    return current_app.config["DEVICE_STATUS"]  # type: ignore[no-any-return]


def _data_root() -> Path:
    return current_app.config["DATA_ROOT"]  # type: ignore[no-any-return]


# -- auth ----------------------------------------------------------------


def _request_token() -> str:
    """Pull the bearer token out of the request. Two header forms are
    accepted: ``Authorization: Bearer <token>`` (the canonical REST
    form) and ``X-Tesserae-Token: <token>`` (a fallback for firmware
    libs whose HTTP client makes Authorization headers awkward, e.g.
    very small embedded HTTP stacks)."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-Tesserae-Token", "").strip()


def _device_by_token(token: str) -> Device | None:
    """Find a device instance whose ``access_token`` matches.

    Iterates every instance (not just on first match) so a timing
    side-channel can't disclose whether a given prefix is close to a
    valid token. ``secrets.compare_digest`` handles the per-pair
    constant-time compare."""
    if not token:
        return None
    matched: Device | None = None
    for dev in _devices().all():
        # Built-in kinds (kind_of is None) don't have an access_token
        # of their own; tokens live on instances. Skip the kinds.
        if dev.kind_of is None:
            continue
        stored = dev.manifest.get("access_token")
        if not isinstance(stored, str) or not stored:
            continue
        if secrets.compare_digest(stored, token):
            matched = dev
            # Don't break, finish the scan so timing leaks nothing.
    return matched


def _auth_device(device_id: str) -> tuple[Device | None, Response | None]:
    """Resolve + authorise a request against a URL-path device id.

    Returns ``(device, None)`` on success, ``(None, response)`` to be
    returned directly when auth fails. Centralises the
    "token-must-match-the-id-in-the-URL" check, refusing requests
    whose token resolves to a different instance than the URL claims."""
    token = _request_token()
    if not token:
        return None, _error(401, "missing bearer token")
    device = _device_by_token(token)
    if device is None:
        return None, _error(401, "invalid bearer token")
    if device.id != device_id:
        # Token is valid but for a different device. Don't leak which
        # device the token belongs to.
        return None, _error(403, "token not valid for this device")
    return device, None


def _error(status: int, message: str, **extra: Any) -> Response:
    """JSON error envelope. Status code is also set on the response so
    firmware can branch on either."""
    body: dict[str, Any] = {"status": status, "error": message}
    body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


# -- frame ---------------------------------------------------------------


def _next_poll_s(device: Device) -> int:
    """How many seconds until the firmware should poll again. Reads
    the configured ``sleep_interval_s`` from settings when present
    (matches the MQTT-side config_topic value), then the schema
    default, then a transport-wide 60 s fallback for kinds that
    don't carry a sleep interval."""
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    if isinstance(stored, dict) and isinstance(stored.get("sleep_interval_s"), int):
        return int(stored["sleep_interval_s"])
    schema = device.config_schema or {}
    spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
    if isinstance(spec, dict) and isinstance(spec.get("default"), int):
        return int(spec["default"])
    return 60


def _current_config(device: Device) -> dict[str, Any]:
    """Per-device config as it stands right now. Same source the MQTT
    config_topic publisher reads from; piggybacking in the status
    response means firmware never needs a separate config poll."""
    section = _settings().get_section("devices") or {}
    if not isinstance(section, dict):
        return {}
    stored = section.get(device.id)
    if isinstance(stored, dict):
        # Copy so the caller can mutate without disturbing the store.
        return dict(stored)
    # No stored values, fall back to schema defaults so the firmware
    # always sees a usable config block.
    out: dict[str, Any] = {}
    for key, spec in (device.config_schema or {}).items():
        if isinstance(spec, dict) and "default" in spec:
            out[key] = spec["default"]
    return out


@bp.get("/<device_id>/frame")
def get_frame(device_id: str) -> Response:
    """Latest frame URL for this device.

    Returns 200 + JSON ``{url, format, panel_w, panel_h, render_id}``
    when a frame has been rendered since the last process restart,
    304 when the caller's ``If-None-Match`` already matches the
    current frame, 204 when no frame has been rendered yet."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    push_mgr = current_app.config.get("PUSH_MANAGER")
    latest = push_mgr.latest_render_for(device.id) if push_mgr is not None else None
    if latest is None:
        return _error(204, "no frame rendered yet for this device")

    # Use the artifact digest as the ETag; identical bytes always
    # produce the same digest (content-addressed renders).
    etag = f'"{latest["digest"]}"'
    if_none_match = request.headers.get("If-None-Match", "")
    if if_none_match and if_none_match == etag:
        resp = Response(status=304)
        resp.headers["ETag"] = etag
        return resp

    image_url = f"{request.url_root.rstrip('/')}/renders/{latest['filename']}"
    panel = device.manifest.get("panel") or {}
    payload = {
        "url": image_url,
        "format": latest.get("ext", ""),
        "panel_w": int(panel.get("w", 0) or 0),
        "panel_h": int(panel.get("h", 0) or 0),
        "render_id": latest["digest"],
        "renderer_id": latest.get("renderer_id", ""),
    }
    resp = jsonify(payload)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# -- status --------------------------------------------------------------


@bp.post("/<device_id>/status")
def post_status(device_id: str) -> Response:
    """Device heartbeat. Body JSON is parsed through the device kind's
    ``parse_status`` (same hook the MQTT path uses), merged into the
    in-memory ``DEVICE_STATUS`` cache, and responded to with the
    current config + next poll cadence."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    raw = request.get_data() or b""
    try:
        parsed = device.parse_status(raw)
    except Exception:
        logger.exception("rest_api: %s.parse_status raised", device.id)
        parsed = {"error": "parse_status raised"}

    # Merge with the existing cache so partial heartbeats (only RSSI,
    # only IP) preserve previously-known values.
    from app.transport_wiring import merge_status_parsed

    cache = _device_status_cache()
    prev = dict(cache.get(device.id) or {})
    merged = merge_status_parsed(prev, parsed)
    merged["last_seen"] = time.time()
    merged["transport"] = "rest"  # so the Devices page can show "via REST"
    cache[device.id] = merged

    return jsonify(
        {
            "status": 200,
            "config": _current_config(device),
            "next_poll_s": _next_poll_s(device),
            "server_time": time.time(),
        }
    )


# -- log -----------------------------------------------------------------


@bp.post("/<device_id>/log")
def post_log(device_id: str) -> Response:
    """Optional client-side log line. Persisted to the EventLog so the
    Events page surfaces it alongside server-side events. Bounded:
    the EventLog's own cap evicts older entries."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    raw = request.get_data() or b""
    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {"raw": str(body)[:512]}

    events = _events()
    if events is not None:
        level = str(body.get("level") or "info")[:32]
        msg = str(body.get("msg") or "")[:512]
        extra = {k: v for k, v in body.items() if k not in ("level", "msg")}
        events.record(
            type="device",
            source=device.id,
            target="client_log",
            status=level,
            error=msg if level in ("error", "warn") else None,
            extra={"client_log": True, "msg": msg, "level": level, **extra},
        )
    return jsonify({"status": 200, "bytes": len(raw)})


# -- register ------------------------------------------------------------


_VALID_KIND_RE = __import__("re").compile(r"^[a-z][a-z0-9_]*$")


@bp.post("/register")
def post_register() -> Response:
    """First-boot device pairing.

    The firmware POSTs with an ``X-Pairing-Code`` header (the 6-digit
    code the user generated in the admin UI) plus a JSON body
    declaring its chosen id + kind + panel dims + firmware version.
    On success the server creates the device instance, mints a per-
    device ``access_token``, and returns it. The pairing code is
    burned (single-use)."""
    code = request.headers.get("X-Pairing-Code", "").strip()
    if not code:
        return _error(400, "missing X-Pairing-Code header")

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")

    device_id = str(body.get("device_id") or "").strip().lower()
    kind_id = str(body.get("kind") or "").strip()
    if not device_id or not kind_id:
        return _error(400, "device_id and kind are required")
    if not _VALID_KIND_RE.match(kind_id):
        return _error(400, "kind id must match [a-z][a-z0-9_]*")

    # Validate kind exists FIRST so a typoed kind doesn't burn the
    # user's pairing code.
    devices_registry = _devices()
    kind = devices_registry.get(kind_id)
    if kind is None or kind.kind_of is not None:
        return _error(400, f"unknown device kind {kind_id!r}")

    # Now consume the code. Doing this before instance creation means
    # a transient failure further down doesn't strand the user with a
    # used code AND no device; we re-issue on failure.
    pairing = _pairings().consume(code)
    if pairing is None:
        return _error(403, "invalid or expired pairing code")

    # Build panel overrides from the body if the firmware reported
    # dims (used for kinds whose default panel doesn't match the
    # connected hardware, e.g. swapping a 7.3" for a 13.3" Inky).
    panel_overrides: dict[str, Any] | None = None
    try:
        w = int(body.get("panel_w") or 0)
        h = int(body.get("panel_h") or 0)
    except (TypeError, ValueError):
        w = h = 0
    if w > 0 and h > 0:
        panel_overrides = {"w": w, "h": h}

    # Existing device id? Re-register without overwriting (idempotent
    # for the firmware retry case). Return the existing token so the
    # device can keep working.
    existing = devices_registry.get(device_id)
    if existing is not None and existing.kind_of is not None:
        token = existing.manifest.get("access_token")
        if not isinstance(token, str) or not token:
            # Existing instance but no token (created via admin UI for
            # the MQTT path). Mint one now and persist.
            token = generate_access_token(devices_registry)
            _persist_token(existing, token)
        return jsonify(
            {
                "status": 200,
                "device_token": token,
                "server_time": time.time(),
                "config": _current_config(existing),
                "reused_existing": True,
            }
        )

    result = create_instance(
        devices=devices_registry,
        renderers=_renderers(),
        data_root=_data_root(),
        instance_id=device_id,
        kind_id=kind_id,
        name=str(body.get("name") or "").strip(),
        panel_overrides=panel_overrides,
        access_token=None,  # let create_instance mint one
        mac=str(body.get("mac") or "").strip() or None,
        api_key_strength="typeable",  # ignored for REST devices, see below
        # Mark the instance REST-mode so the push pipeline skips
        # broker publishes and the admin UI shows the right transport
        # column. Persists on the manifest as ``transport: "rest"``.
        transport="rest",
    )
    if result.error is not None or result.device is None:
        # The pairing code was already consumed. Re-issue it so the
        # user doesn't have to round-trip through the admin UI for a
        # fresh code. Note: this slightly weakens the single-use
        # guarantee on a failed-then-retried registration, but a
        # failed create_instance is rare and the better UX wins.
        _pairings()._codes[pairing.code] = pairing
        return _error(400, result.error or "create_instance failed")

    token = result.device.manifest.get("access_token")
    if not isinstance(token, str) or not token:
        # Belt and braces: ensure a token exists even if
        # create_instance changed shape later.
        token = generate_access_token(devices_registry)
        _persist_token(result.device, token)

    resp = jsonify(
        {
            "status": 201,
            "device_token": token,
            "server_time": time.time(),
            "config": _current_config(result.device),
            "reused_existing": False,
        }
    )
    resp.status_code = 201
    return resp


def _persist_token(device: Device, token: str) -> None:
    """Write a freshly-minted token back to the instance manifest on
    disk so it survives a process restart. The same JSON file
    create_instance wrote at registration time."""
    import json

    manifest_path = _data_root() / "devices" / f"{device.id}.json"
    try:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = dict(device.manifest)
    existing["access_token"] = token
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    # Also update the in-memory manifest so subsequent requests see it.
    device.manifest["access_token"] = token


# -- admin: pairing-code issue + list ------------------------------------
#
# These endpoints live under /api/v1/device/admin/* and are gated by
# the admin session (the Flask cookie), NOT by a per-device bearer
# token. The session check has to be inline because /api/v1/ is on
# the auth gate's open-paths list (so per-device endpoints aren't
# bounced to /login). They're the wire-protocol stand-in for the
# "Pair new device" button that will land on Settings -> Devices.


def _require_admin_session() -> Response | None:
    from app.auth import is_authed

    if is_authed():
        return None
    return _error(401, "admin session required")


@bp.post("/admin/pairing/issue")
def admin_pairing_issue() -> Response:
    """Mint a fresh 6-digit pairing code. Used by the admin UI's
    forthcoming "Pair new device" flow and by curl-from-terminal
    testing. Body is optional JSON ``{"note": "for bedroom Pico"}``."""
    err = _require_admin_session()
    if err is not None:
        return err
    body = request.get_json(silent=True) or {}
    note = str(body.get("note") or "").strip() if isinstance(body, dict) else ""
    record = _pairings().issue(note=note)
    return jsonify(
        {
            "code": record.code,
            "expires_at": record.expires_at,
            "ttl_s": int(record.expires_at - record.issued_at),
            "note": record.note,
        }
    )


@bp.get("/admin/pairing/pending")
def admin_pairing_pending() -> Response:
    """List pending (unredeemed, unexpired) pairing codes. Lets the
    admin UI show "you have a code waiting" so a second issue doesn't
    create accidental duplicates."""
    err = _require_admin_session()
    if err is not None:
        return err
    pending = [
        {
            "code": p.code,
            "issued_at": p.issued_at,
            "expires_at": p.expires_at,
            "note": p.note,
        }
        for p in _pairings().list_pending()
    ]
    return jsonify({"pending": pending})


# -- blueprint registration ---------------------------------------------


def register(app: Flask) -> None:
    app.register_blueprint(bp)
