"""Companion API (``/api/app/v1``) for community-built client apps.

A small, separately versioned, stable adapter over the existing stores
(``DeviceRegistry``, ``PageStore``) and credential machinery, built for
the community iOS companion app (discussion #147). It is a *different
trust boundary* from the firmware device API at ``/api/v1/device/*``:
companion clients pair once for a revocable, scoped bearer token and
never touch firmware device tokens, the webhook credential, the MCP API,
or Flask session cookies.

This module implements **Phase 1** of the contract: discovery, pairing,
session revocation, and the two read models (devices + dashboards). The
async job model and the two write routes (dashboard push, image push)
plus ``_tesserae._tcp`` discovery land in later slices; capabilities
advertises only the features this server actually serves so the client
degrades cleanly.

Contract: ``charmmmz/tesserae-companion-ios`` ``Contracts/app-v1.openapi.yaml``
(OpenAPI 0.2.0). The vendored copy + fixtures under ``tests/companion/``
guard these shapes.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from flask import Blueprint, Flask, current_app, g, jsonify, request
from werkzeug.wrappers import Response

from app.device_loader import Device, DeviceRegistry
from app.panel import device_panel, resolve_settings_panel
from app.state.companion_token_store import COMPANION_SCOPES, CompanionTokenStore
from app.state.page_store import PageStore
from app.state.pairing_store import PairingStore
from app.state.settings_store import SettingsStore

bp = Blueprint("companion_api", __name__, url_prefix="/api/app/v1")

# -- limits advertised by the capability probe ---------------------------
#
# Server-advertised rather than baked into the client (discussion #147):
# tuning these needs no app release. The byte / edge caps are the ones the
# owner settled on (~25 MB, 8K max edge); job + idempotency retention are
# 24 h. These constants are reused by Phase 2's upload + job enforcement.
IMAGE_UPLOAD_BYTES = 26_214_400  # 25 MiB
IMAGE_MAX_EDGE = 8192
IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png", "image/heic", "image/heif", "image/webp")
JOB_RETENTION_SECONDS = 86_400
IDEMPOTENCY_RETENTION_SECONDS = 86_400

# Features this server actually serves. Phase 2 adds
# dashboard_push / image_push / jobs as those routes land.
FEATURES = ("devices", "dashboards")

_PAIRING_CODE_RE = re.compile(r"^[0-9]{6,12}$")


# -- CORS ----------------------------------------------------------------
#
# Same rationale as app/rest_api.py: every non-discovery endpoint requires
# a bearer token, so the token is the security boundary, not the origin.
# A wildcard allow lets a browser-based companion (or the pairing QR flow)
# reach the API without leaking anything a caller without the token could
# use. DELETE is added for the session-revoke route.


@bp.before_request
def _cors_preflight() -> Response | None:
    if request.method == "OPTIONS":
        return Response(status=204)
    return None


@bp.after_request
def _cors_headers(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Idempotency-Key"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


# -- config accessors ----------------------------------------------------


def _tokens() -> CompanionTokenStore:
    return current_app.config["COMPANION_TOKENS"]  # type: ignore[no-any-return]


def _companion_pairings() -> PairingStore:
    return current_app.config["COMPANION_PAIRING_STORE"]  # type: ignore[no-any-return]


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _pages() -> PageStore:
    return current_app.config["PAGE_STORE"]  # type: ignore[no-any-return]


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _device_status() -> dict[str, dict[str, Any]]:
    return current_app.config.get("DEVICE_STATUS") or {}


# -- error envelope ------------------------------------------------------


def _error(code: str, message: str, status: int) -> tuple[Response, int]:
    """Build the contract ``ErrorResponse`` envelope. ``request_id`` is
    always attached so a failed companion call is traceable from the app
    without leaking anything sensitive."""
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": f"req_{secrets.token_hex(8)}",
        }
    }
    return jsonify(payload), status


# -- auth ----------------------------------------------------------------


def _bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer ") :].strip()
        return token or None
    return None


def _require_companion(*scopes: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Guard a route behind a valid companion token. Stashes the resolved
    record on ``flask.g.companion``. ``scopes`` are checked when supplied;
    the single Phase 1 role carries all scopes, but the check is wired now
    so narrowing later is a store change, not a routing change."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            token = _bearer_token()
            record = _tokens().lookup(token) if token else None
            if record is None:
                return _error("unauthorized", "Missing, invalid, or revoked credential.", 401)
            missing = [s for s in scopes if s not in record.scopes]
            if missing:
                return _error("unauthorized", "Credential lacks the required scope.", 401)
            g.companion = record
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# -- shared helpers ------------------------------------------------------


def _web_url() -> str:
    """Instance web URL for the client's "open in browser" links. The
    configured public URL when set, else a relative root (the app resolves
    it against the base URL it paired with)."""
    raw = str((_settings().get_section("app") or {}).get("public_url") or "").strip()
    return raw.rstrip("/") or "/"


def _timezone() -> str:
    tz = str((_settings().get_section("app") or {}).get("timezone") or "").strip()
    if tz and tz.lower() != "system":
        return tz
    return "UTC"


def _instance_name() -> str:
    app_section = _settings().get_section("app") or {}
    for key in ("instance_name", "site_name", "name"):
        raw = app_section.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return "Tesserae"


def _instance() -> dict[str, Any]:
    return {
        "id": str(current_app.config.get("INSTALL_ID") or "unknown"),
        "name": _instance_name(),
        "server_version": str(current_app.config.get("APP_VERSION") or "0.0.0"),
        "timezone": _timezone(),
        "web_url": _web_url(),
    }


# -- capabilities --------------------------------------------------------


def _capabilities() -> dict[str, Any]:
    return {
        "product": "tesserae",
        "server_version": str(current_app.config.get("APP_VERSION") or "0.0.0"),
        "api": {"name": "companion", "version": 1},
        "pairing": {
            "supported": True,
            "code_length": 6,
            "ttl_seconds": 600,
        },
        "features": list(FEATURES),
        "limits": {
            "image_upload_bytes": IMAGE_UPLOAD_BYTES,
            "image_max_edge": IMAGE_MAX_EDGE,
            "image_content_types": list(IMAGE_CONTENT_TYPES),
            "job_retention_seconds": JOB_RETENTION_SECONDS,
            "idempotency_retention_seconds": IDEMPOTENCY_RETENTION_SECONDS,
        },
        "web_url": _web_url(),
    }


@bp.get("")
@bp.get("/")
def get_capabilities() -> Any:
    """Unauthenticated capability probe. Leaks no household content, only
    versions, features, pairing availability, and advertised limits."""
    return jsonify(_capabilities())


# -- pairing -------------------------------------------------------------


def _valid_client(client: Any) -> dict[str, Any] | None:
    if not isinstance(client, dict):
        return None
    name = client.get("name")
    platform = client.get("platform")
    app_version = client.get("app_version")
    installation_id = client.get("installation_id")
    if not (isinstance(name, str) and 1 <= len(name) <= 80):
        return None
    if platform != "ios":
        return None
    if not isinstance(app_version, str) or not app_version:
        return None
    if not (isinstance(installation_id, str) and 16 <= len(installation_id) <= 128):
        return None
    return {
        "name": name,
        "platform": platform,
        "app_version": app_version,
        "installation_id": installation_id,
    }


@bp.post("/pair")
def pair() -> Any:
    """Exchange a single-use companion pairing code for a per-client
    bearer token. Companion codes come from a store distinct from firmware
    pairing, so a firmware code can't mint a companion token."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("invalid_request", "Body must be a JSON object.", 400)
    code = body.get("code")
    if not (isinstance(code, str) and _PAIRING_CODE_RE.match(code)):
        return _error("invalid_request", "A 6-12 digit pairing code is required.", 400)
    client = _valid_client(body.get("client"))
    if client is None:
        return _error("invalid_request", "A valid client descriptor is required.", 400)

    consumed = _companion_pairings().consume(code)
    if consumed is None:
        # Unknown / expired / already-used all read as "not present" from
        # the single-use store. Report expiry, the common case.
        return _error("pairing_expired", "The pairing code is expired or unknown.", 400)

    plaintext, record = _tokens().issue(client=client, scopes=COMPANION_SCOPES)
    payload = {
        "token": plaintext,
        "token_id": record.token_id,
        "scopes": list(record.scopes),
        "created_at": record.created_at,
        "instance": _instance(),
    }
    return jsonify(payload), 201


@bp.delete("/session")
@_require_companion()
def revoke_session() -> Any:
    """Revoke the presented companion credential."""
    token = _bearer_token()
    if token:
        _tokens().revoke_token(token)
    return Response(status=204)


# -- devices -------------------------------------------------------------


def _orientation(width: int, height: int) -> str:
    return "portrait" if height > width else "landscape"


def _freshness_threshold_s(device: Device) -> float:
    """How long since last heartbeat before a device reads as ``stale``.

    E-ink clients sleep for long stretches by design, so the threshold
    tracks the device's own poll cadence: a few wake cycles of silence,
    with a floor so a chatty device isn't marked stale on a single skipped
    beat."""
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    interval = 60
    if isinstance(stored, dict) and isinstance(stored.get("sleep_interval_s"), int):
        interval = int(stored["sleep_interval_s"])
    else:
        schema = device.config_schema or {}
        spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
        if isinstance(spec, dict) and isinstance(spec.get("default"), int):
            interval = int(spec["default"])
    return max(interval * 3, 300)


def _nullable_int(value: Any, *, lo: int | None = None, hi: int | None = None) -> int | None:
    if isinstance(value, bool):  # bool is an int subclass; never a metric
        return None
    if not isinstance(value, (int, float)):
        return None
    out = int(value)
    if lo is not None and out < lo:
        return None
    if hi is not None and out > hi:
        return None
    return out


def _device_view(device: Device, status: dict[str, Any] | None) -> dict[str, Any]:
    panel = device_panel(device) or resolve_settings_panel(_settings())
    parsed = status.get("parsed", {}) if isinstance(status, dict) else {}
    received_at = status.get("received_at") if isinstance(status, dict) else None

    last_seen_at: str | None = None
    freshness = "unknown"
    if isinstance(received_at, (int, float)):
        last_seen_at = datetime.fromtimestamp(float(received_at), UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        age = time.time() - float(received_at)
        freshness = "fresh" if age <= _freshness_threshold_s(device) else "stale"

    fw = parsed.get("fw_version") if isinstance(parsed, dict) else None

    return {
        "id": device.id,
        "name": device.display_name,
        "kind": str(device.kind_of or ""),
        "panel": {
            "width": int(panel.w),
            "height": int(panel.h),
            "gamut": str(panel.gamut),
            "orientation": _orientation(int(panel.w), int(panel.h)),
        },
        "freshness": freshness,
        "last_seen_at": last_seen_at,
        "battery_percent": _nullable_int(
            parsed.get("battery_pct") if isinstance(parsed, dict) else None, lo=0, hi=100
        ),
        "rssi_dbm": _nullable_int(parsed.get("rssi") if isinstance(parsed, dict) else None),
        "firmware_version": (str(fw) if isinstance(fw, (str, int, float)) else None),
    }


@bp.get("/devices")
@_require_companion("devices:read")
def list_devices() -> Any:
    status_cache = _device_status()
    devices = [
        _device_view(d, status_cache.get(d.id))
        for d in _devices().all()
        if d.kind_of is not None  # instances only, never built-in kinds
    ]
    return jsonify({"devices": devices})


# -- dashboards ----------------------------------------------------------


@bp.get("/dashboards")
@_require_companion("dashboards:read")
def list_dashboards() -> Any:
    out: list[dict[str, Any]] = []
    for page in _pages().list():
        kind = page.layout_kind if page.layout_kind in ("grid", "canvas") else "grid"
        out.append(
            {
                "id": page.id,
                "name": page.name or page.id,
                "kind": kind,
                "device_ids": list(dict.fromkeys(page.device_ids)),
                "updated_at": page.updated_at,
                "web_url": f"/pages/{page.id}",
            }
        )
    return jsonify({"dashboards": out})


def register(app: Flask) -> None:
    app.register_blueprint(bp)
