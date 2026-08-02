"""Companion API (``/api/app/v1``) for community-built client apps.

A small, separately versioned, stable adapter over the existing stores
(``DeviceRegistry``, ``PageStore``) and credential machinery, built for
the community iOS companion app (discussion #147). It is a *different
trust boundary* from the firmware device API at ``/api/v1/device/*``:
companion clients pair once for a revocable, scoped bearer token and
never touch firmware device tokens, the webhook credential, the MCP API,
or Flask session cookies.

This module covers the discovery, pairing, session, read-model, and write
surfaces of the contract: capability probe, pairing, session revocation,
device + dashboard read models, canonical History reads and resend, the
async write routes, and job polling. Writes return ``202`` with a persisted
job the client polls; quiet-hours suppression surfaces as a *successful*
``quiet`` outcome, never a failure. ``_tesserae._tcp`` discovery lives in
app.mdns.

Contract: ``charmmmz/tesserae-companion-ios`` ``Contracts/app-v1.openapi.yaml``
(OpenAPI 0.7.0). The vendored copy + fixtures under ``tests/companion/``
guard these shapes.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json as _json
import logging
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Flask, current_app, g, jsonify, request, url_for
from werkzeug.wrappers import Response

from app.companion_history import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    InvalidHistoryCursor,
    list_history,
    parse_history_id,
    retained_composition_for_history,
    retained_resend_composition_for_row,
)
from app.companion_jobs import CompanionJobs, JobOutcome
from app.device_loader import Device, DeviceRegistry
from app.device_preview import retained_device_preview
from app.net_guard import BlockedURLError, assert_public_url
from app.panel import device_panel, resolve_settings_panel
from app.quiet_hours import device_is_quiet
from app.state.companion_token_store import COMPANION_SCOPES, CompanionTokenStore
from app.state.event_log import EventLog, EventRow
from app.state.idempotency_store import IdempotencyStore
from app.state.idempotency_store import fingerprint as _idempotency_fingerprint
from app.state.job_store import JobKind, JobStore
from app.state.page_store import PageStore
from app.state.pairing_store import PairingStore
from app.state.personal_data_store import PersonalDataSnapshotStore
from app.state.settings_store import SettingsStore

logger = logging.getLogger(__name__)

# Register the HEIF/HEIC opener so Pillow can decode iPhone photos both for
# our edge/size validation and downstream in the renderer. pillow-heif is a
# hard dependency (pyproject.toml); guard the import only so a broken wheel
# degrades to "HEIC unsupported" rather than failing app import.
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    logger.warning("pillow-heif unavailable; HEIC/HEIF companion uploads will not decode")

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
IMAGE_FIT_MODES = ("fit", "fill", "blur", "stretch", "center")
# Contract 0.6 ``image_framing``: mandatory editor bound whenever the
# capability is advertised, so clients never hard-code a zoom range. An
# editor bound rather than a quality promise: the resolved crop is always
# clamped to source bounds, and a heavy crop of a small photo will be soft
# on a large panel regardless of what the editor allowed.
IMAGE_FRAMING_MAX_ZOOM = 4
JOB_RETENTION_SECONDS = 86_400
IDEMPOTENCY_RETENTION_SECONDS = 86_400

# Personal-data bridge (#176, contract 0.7): a snapshot is fresh until it is
# this old, then stale, then expired (deleted). ``max_ttl`` bounds how far a
# client may set expires_at past generated_at. Server-advertised so the app
# doesn't hard-code them.
PERSONAL_DATA_STALE_SECONDS = 86_400  # 24 h
PERSONAL_DATA_MAX_TTL_SECONDS = 172_800  # 48 h
PERSONAL_DATA_SOURCES = ("reminders.fridge",)
PERSONAL_DATA_SNAPSHOT_VERSION = "personal_data_bridge_v1"
_REMINDER_PRIORITIES = frozenset(("none", "low", "medium", "high"))

# Features this server serves. The client gates on this list rather than
# assuming the full set, so unshipped surfaces degrade cleanly.
FEATURES = (
    "devices",
    "dashboards",
    "dashboard_push",
    "image_push",
    "image_url_push",
    "jobs",
    "history",
    "image_framing",
    "personal_data_reminders",
)

_PAIRING_CODE_RE = re.compile(r"^[0-9]{6,12}$")
_IMAGE_CONTENT_TYPES = frozenset(IMAGE_CONTENT_TYPES)
_IMAGE_FIT_MODES = frozenset(IMAGE_FIT_MODES)


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
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Idempotency-Key"
    resp.headers["Access-Control-Expose-Headers"] = "Location, Retry-After, ETag"
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


def _jobs_store() -> JobStore:
    return current_app.config["JOB_STORE"]  # type: ignore[no-any-return]


def _idempotency() -> IdempotencyStore:
    return current_app.config["IDEMPOTENCY_STORE"]  # type: ignore[no-any-return]


def _job_runner() -> CompanionJobs:
    return current_app.config["COMPANION_JOBS"]  # type: ignore[no-any-return]


def _push_manager() -> Any:
    return current_app.config.get("PUSH_MANAGER")


def _events() -> EventLog:
    return current_app.config["EVENT_LOG"]  # type: ignore[no-any-return]


def _personal_data() -> PersonalDataSnapshotStore:
    return current_app.config["PERSONAL_DATA_STORE"]  # type: ignore[no-any-return]


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


def _features() -> list[str]:
    """Advertised features. ``previews`` is additive and only offered when a
    browser pool exists to prepare on-demand dashboard renders (device
    previews serve existing renders, but the pair is advertised together so
    the client enables the whole preview surface at once)."""
    feats = list(FEATURES)
    if current_app.config.get("BROWSER_POOL") is not None:
        # Both need a browser pool: previews to prepare on-demand renders,
        # webpage_push to screenshot an arbitrary URL server-side.
        feats.append("previews")
        feats.append("webpage_push")
    return feats


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
        "features": _features(),
        "limits": {
            "image_upload_bytes": IMAGE_UPLOAD_BYTES,
            "image_max_edge": IMAGE_MAX_EDGE,
            "image_content_types": list(IMAGE_CONTENT_TYPES),
            "image_fit_modes": list(IMAGE_FIT_MODES),
            # Required whenever ``image_framing`` is advertised (contract
            # 0.6): a capability that doesn't publish its own bounds just
            # relocates the hard-coding into the client.
            "image_framing_max_zoom": IMAGE_FRAMING_MAX_ZOOM,
            "job_retention_seconds": JOB_RETENTION_SECONDS,
            "idempotency_retention_seconds": IDEMPOTENCY_RETENTION_SECONDS,
            # Required whenever any personal-data capability is advertised
            # (contract 0.7): the client reads freshness/TTL bounds, not guesses.
            "personal_data_stale_after_seconds": PERSONAL_DATA_STALE_SECONDS,
            "personal_data_max_ttl_seconds": PERSONAL_DATA_MAX_TTL_SECONDS,
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


# -- personal data (#176, contract 0.7) ----------------------------------
#
# The phone stays the authority for Apple personal data. It publishes only an
# explicitly enabled, minimal, expiring snapshot; the server keeps the latest
# per source (no history), renders it in a widget, and deletes it on disable or
# expiry. This is synchronous state, not a job: no render / publish / History
# write happens here.


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _personal_data_state(generated_epoch: float, expires_epoch: float, now: float) -> str:
    if now >= expires_epoch:
        return "expired"
    if now >= generated_epoch + PERSONAL_DATA_STALE_SECONDS:
        return "stale"
    return "fresh"


def _source_status(
    source_id: str, generated_epoch: float, expires_epoch: float, now: float
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "state": _personal_data_state(generated_epoch, expires_epoch, now),
        "generated_at": _iso(generated_epoch),
        "stale_at": _iso(generated_epoch + PERSONAL_DATA_STALE_SECONDS),
        "expires_at": _iso(expires_epoch),
    }


def _validate_reminders_fridge(
    source_id: str, body: Any
) -> tuple[tuple[dict[str, Any], float, float] | None, tuple[Response, int] | None]:
    """Validate a ``reminders.fridge`` snapshot. Returns ``((snapshot, gen, exp),
    None)`` or ``(None, error)``. Strict: unknown fields are rejected, so the
    endpoint can never become arbitrary JSON storage."""

    def bad(msg: str) -> tuple[None, tuple[Response, int]]:
        return None, _error("invalid_snapshot", msg, 400)

    if not isinstance(body, dict):
        return bad("body must be a JSON object")
    if set(body) - {"version", "source_id", "generated_at", "expires_at", "data"}:
        return bad("snapshot has unexpected fields")
    if body.get("version") != PERSONAL_DATA_SNAPSHOT_VERSION:
        return bad("unsupported snapshot version")
    if body.get("source_id") != source_id:
        return bad("source_id does not match the path")
    gen = _parse_iso(body.get("generated_at"))
    exp = _parse_iso(body.get("expires_at"))
    if gen is None or exp is None:
        return bad("generated_at and expires_at must be ISO 8601")
    if exp <= gen:
        return bad("expires_at must be after generated_at")
    if exp - gen > PERSONAL_DATA_MAX_TTL_SECONDS:
        return bad("expires_at exceeds the maximum retention window")
    data = body.get("data")
    if not isinstance(data, dict) or set(data) - {"items"}:
        return bad("data must be an object with only items")
    items = data.get("items")
    if not isinstance(items, list):
        return bad("data.items must be an array")
    if len(items) > 200:
        return bad("too many items (max 200)")
    for item in items:
        if not isinstance(item, dict):
            return bad("each item must be an object")
        if set(item) - {"id", "title", "due_date", "priority", "completed"}:
            return bad("an item has unexpected fields")
        iid, title = item.get("id"), item.get("title")
        if not isinstance(iid, str) or not (1 <= len(iid) <= 256):
            return bad("item id must be a 1-256 char string")
        if not isinstance(title, str) or not (1 <= len(title) <= 512):
            return bad("item title must be a 1-512 char string")
        due = item.get("due_date")
        if due is not None and not isinstance(due, str):
            return bad("item due_date must be a date string or null")
        if item.get("priority") not in _REMINDER_PRIORITIES:
            return bad("item priority must be none/low/medium/high")
        if item.get("completed") is not False:
            return bad("item completed must be false")
    return (body, gen, exp), None


@bp.put("/personal-data/<source_id>")
@_require_companion("personal_data:write")
def put_personal_data(source_id: str) -> Any:
    """Replace the latest snapshot for one advertised source. Latest-only,
    synchronous, no render/Job/History. Ordering: identical timestamp + payload
    is idempotent; an older timestamp or a conflicting payload at the same
    timestamp is refused so delayed background work can't overwrite newer data."""
    if source_id not in PERSONAL_DATA_SOURCES:
        return _error("unsupported_personal_data_source", f"unknown source {source_id!r}", 400)
    result, err = _validate_reminders_fridge(source_id, request.get_json(silent=True))
    if err is not None:
        return err
    assert result is not None  # narrow for mypy; err is None here
    snapshot, gen, exp = result
    now = time.time()
    store = _personal_data()
    existing = store.get(source_id)
    if isinstance(existing, dict) and isinstance(existing.get("generated_epoch"), (int, float)):
        existing_gen = float(existing["generated_epoch"])
        if gen < existing_gen:
            return _error("snapshot_out_of_order", "a newer snapshot is already stored", 409)
        if gen == existing_gen:
            if existing.get("snapshot") != snapshot:
                return _error(
                    "snapshot_conflict", "a different snapshot exists at this timestamp", 409
                )
            # Identical retry: idempotent, don't rewrite.
            return jsonify(_source_status(source_id, gen, exp, now)), 200
    store.put(source_id, snapshot=snapshot, generated_epoch=gen, expires_epoch=exp)
    return jsonify(_source_status(source_id, gen, exp, now)), 200


@bp.get("/personal-data/status")
@_require_companion("personal_data:write")
def personal_data_status() -> Any:
    """Freshness metadata only, never the stored values."""
    now = time.time()
    store = _personal_data()
    store.purge_expired(now)
    sources = []
    for source_id, rec in sorted(store.all().items()):
        gen, exp = rec.get("generated_epoch"), rec.get("expires_epoch")
        if isinstance(gen, (int, float)) and isinstance(exp, (int, float)):
            sources.append(_source_status(source_id, float(gen), float(exp), now))
    return jsonify({"sources": sources})


@bp.delete("/personal-data/<source_id>")
@_require_companion("personal_data:write")
def delete_personal_data(source_id: str) -> Any:
    """Idempotently drop a source's snapshot (on disable)."""
    _personal_data().delete(source_id)
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


def _device_view(
    device: Device,
    status: dict[str, Any] | None,
    push_manager: Any,
) -> dict[str, Any]:
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
    has_pending_render = False
    pending_render: dict[str, Any] | None = None
    if device.transport == "rest" and push_manager is not None:
        pending_lookup = getattr(push_manager, "has_pending_render", None)
        if callable(pending_lookup):
            has_pending_render = bool(pending_lookup(device.id))
        if has_pending_render:
            latest_lookup = getattr(push_manager, "latest_render_for", None)
            latest = latest_lookup(device.id) if callable(latest_lookup) else None
            if isinstance(latest, dict):
                revision = latest.get("digest")
                preview_digest = latest.get("preview_digest")
                if (
                    isinstance(revision, str)
                    and revision
                    and isinstance(preview_digest, str)
                    and preview_digest
                ):
                    pending_render = {
                        "revision": revision,
                        "preview_url": url_for(
                            ".device_preview",
                            device_id=device.id,
                            revision=revision,
                        ),
                    }
                    rendered_at = latest.get("timestamp")
                    if isinstance(rendered_at, (int, float)):
                        pending_render["rendered_at"] = datetime.fromtimestamp(
                            float(rendered_at), UTC
                        ).strftime("%Y-%m-%dT%H:%M:%SZ")

    view = {
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
        "has_pending_render": has_pending_render,
    }
    if pending_render is not None:
        view["pending_render"] = pending_render
    return view


@bp.get("/devices")
@_require_companion("devices:read")
def list_devices() -> Any:
    status_cache = _device_status()
    manager = _push_manager()
    devices = [
        _device_view(d, status_cache.get(d.id), manager)
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
                "icon": page.icon,
                "device_ids": list(dict.fromkeys(page.device_ids)),
                "updated_at": page.updated_at,
                "web_url": f"/pages/{page.id}",
            }
        )
    return jsonify({"dashboards": out})


# -- write routes: shared plumbing ---------------------------------------


def _resolve_tz() -> Any:
    raw = str((_settings().get_section("app") or {}).get("timezone") or "system").strip()
    if not raw or raw.lower() == "system":
        return None
    try:
        return ZoneInfo(raw)
    except ZoneInfoNotFoundError:
        return None


def _require_idempotency_key() -> tuple[str | None, tuple[Response, int] | None]:
    """Both write routes require an ``Idempotency-Key`` (16-128 chars) so a
    Share/Shortcut resubmit resolves to the same job."""
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not (16 <= len(key) <= 128):
        return None, _error(
            "invalid_request", "A 16-128 character Idempotency-Key header is required.", 400
        )
    return key, None


def _valid_target_ids(raw: Any) -> list[str] | None:
    """Coerce a request ``device_ids`` array to a unique, non-empty list of
    known instance ids, or None if any entry is unknown / the shape is
    wrong. The caller maps None to ``invalid_target``."""
    if not isinstance(raw, list) or not raw:
        return None
    seen: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item:
            return None
        device = _devices().get(item)
        if device is None or device.kind_of is None:
            return None
        if item not in seen:
            seen.append(item)
    return seen


def _partition_quiet(target_ids: list[str], *, override: bool) -> tuple[list[str], list[str]]:
    """Split targets into (active, quiet). An explicit override sends to
    everything; otherwise devices currently inside their quiet-hours window
    are held back. Mirrors the server's own push policy so the app can tell
    "sent" from "held for quiet hours"."""
    if override:
        return list(target_ids), []
    app_settings = _settings().get_section("app") or {}
    now = datetime.now(UTC)
    tz = _resolve_tz()
    active: list[str] = []
    quiet: list[str] = []
    for did in target_ids:
        device = _devices().get(did)
        if device is not None and device_is_quiet(app_settings, device, now, tz):
            quiet.append(did)
        else:
            active.append(did)
    return active, quiet


_PUBLISHED_STATUSES = frozenset({"sent", "no_change"})


def _history_event_ids(result: Any) -> list[str] | None:
    """Exact canonical History correlation for a successful push result."""
    correlated: list[str] = []
    raw_event_ids = getattr(result, "event_ids", ())
    if isinstance(raw_event_ids, (list, tuple)):
        correlated.extend(
            str(event_id)
            for event_id in raw_event_ids
            if isinstance(event_id, int) and event_id > 0
        )
    event_id = getattr(result, "event_id", None)
    if isinstance(event_id, int) and event_id > 0:
        correlated.append(str(event_id))
    return list(dict.fromkeys(correlated)) or None


def _job_response(job: Any, status_code: int) -> Any:
    resp = jsonify({"job": job.public_dict()})
    resp.status_code = status_code
    if status_code == 202:
        resp.headers["Location"] = f"/api/app/v1/jobs/{job.id}"
        resp.headers["Retry-After"] = "2"
    return resp


def _reserve_and_run(
    *,
    kind: JobKind,
    key: str,
    payload: bytes,
    target_ids: list[str],
    label: str | None,
    work: Callable[[], JobOutcome],
) -> tuple[Response, int] | Any:
    """Resolve the idempotency key, mint-or-replay the job, and enqueue the
    work on first creation. Returns a 202 job response, or a 409 conflict
    envelope when the key was reused with a different payload."""
    token_id = str(g.companion.token_id)
    fp = _idempotency_fingerprint(request.method, request.path, payload)

    def _make_job() -> str:
        return _jobs_store().create(kind=kind, target_device_ids=target_ids, label=label).id

    job_id, created, conflict = _idempotency().reserve(
        token_id=token_id, key=key, fingerprint=fp, make_job=_make_job
    )
    if conflict:
        return _error(
            "idempotency_conflict",
            "The idempotency key was already used with a different payload.",
            409,
        )
    if created:
        _job_runner().enqueue(job_id, work)
    job = _jobs_store().get(job_id)
    if job is None:  # retention swept the replayed job; nothing to return
        return _error("not_found", "The job is no longer available.", 404)
    return _job_response(job, 202)


# -- dashboard push ------------------------------------------------------


@bp.post("/dashboards/<dashboard_id>/push")
@_require_companion("push:write")
def push_dashboard(dashboard_id: str) -> Any:
    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None

    raw = request.get_data(cache=True)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("override_quiet_hours"), bool):
        return _error("invalid_request", "override_quiet_hours (boolean) is required.", 400)
    override = bool(body["override_quiet_hours"])

    page = _pages().get(dashboard_id)
    if page is None:
        return _error("not_found", "No dashboard with that id.", 404)

    if "device_ids" in body:
        targets = _valid_target_ids(body.get("device_ids"))
        if targets is None:
            return _error("invalid_target", "One or more target displays are unknown.", 400)
    else:
        targets = list(dict.fromkeys(page.device_ids))
        if not targets:
            return _error("invalid_target", "The dashboard has no bound displays.", 400)

    page_id = page.id
    resolved_targets = list(targets)

    def _work() -> JobOutcome:
        active, _quiet = _partition_quiet(resolved_targets, override=override)
        if not active:
            return JobOutcome.quiet(resolved_targets, "all_targets_in_quiet_hours")
        manager = _push_manager()
        if manager is None:
            return JobOutcome.failed("temporarily_unavailable", "The push pipeline is offline.")
        result = manager.push(
            page_id,
            device_ids=set(active),
            respect_quiet_hours=False,
            source="companion",
            bypass_coalesce=True,
        )
        if result.status in _PUBLISHED_STATUSES:
            return JobOutcome.published(active, history_event_ids=_history_event_ids(result))
        return JobOutcome.failed("render_failed", result.error or f"push {result.status}")

    return _reserve_and_run(
        kind="dashboard_push",
        key=key,
        payload=raw,
        target_ids=resolved_targets,
        label=page.name or page.id,
        work=_work,
    )


# -- image push ----------------------------------------------------------


def _decode_edge(image_bytes: bytes) -> int | None:
    """Longest edge of the decoded image, or None if it can't be decoded."""
    from io import BytesIO

    try:
        from PIL import Image

        with Image.open(BytesIO(image_bytes)) as img:
            return max(int(img.width), int(img.height))
    except Exception:
        return None


def _valid_framing(
    spec: dict[str, Any], fit: str
) -> tuple[dict[str, float] | None, tuple[Response, int] | None]:
    """Validate the optional contract 0.6 ``framing`` object.

    Returns ``(framing, None)`` on success (``None`` when absent) or
    ``(None, error_response)``. Framing rides only ``fit: fill`` — for any
    other mode there is no "ordinary Fill crop" to refine, so the
    combination is rejected rather than silently ignored. Booleans are
    explicitly refused: ``bool`` is an ``int`` subclass, so ``zoom: true``
    would otherwise validate as ``1``.
    """
    raw = spec.get("framing")
    if raw is None:
        return None, None
    invalid = _error(
        "invalid_framing",
        "framing requires fit 'fill', numeric focus_x/focus_y in 0..1, "
        f"and zoom in 1..{IMAGE_FRAMING_MAX_ZOOM}.",
        400,
    )
    if not isinstance(raw, dict) or fit != "fill":
        return None, invalid
    values: dict[str, float] = {}
    for key in ("focus_x", "focus_y", "zoom"):
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, invalid
        values[key] = float(value)
    if not (
        0 <= values["focus_x"] <= 1
        and 0 <= values["focus_y"] <= 1
        and 1 <= values["zoom"] <= IMAGE_FRAMING_MAX_ZOOM
    ):
        return None, invalid
    return values, None


@bp.post("/images")
@_require_companion("media:write")
def push_image() -> Any:
    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None

    upload = request.files.get("image")
    if upload is None:
        return _error("invalid_request", "A multipart 'image' part is required.", 400)
    if (upload.mimetype or "").lower() not in _IMAGE_CONTENT_TYPES:
        return _error("unsupported_image", "Unsupported still-image media type.", 415)

    image_bytes = upload.read()
    if len(image_bytes) > IMAGE_UPLOAD_BYTES:
        return _error("image_too_large", "The encoded image exceeds the size limit.", 413)
    edge = _decode_edge(image_bytes)
    if edge is None:
        return _error("unsupported_image", "The image could not be decoded.", 415)
    if edge > IMAGE_MAX_EDGE:
        return _error("image_too_large", "The image exceeds the maximum edge length.", 413)

    request_raw = request.form.get("request") or ""
    try:
        spec = _json.loads(request_raw)
    except ValueError:
        return _error("invalid_request", "The 'request' part must be JSON.", 400)
    if not isinstance(spec, dict):
        return _error("invalid_request", "The 'request' part must be a JSON object.", 400)
    fit_value = spec.get("fit")
    if (
        not isinstance(fit_value, str)
        or fit_value not in _IMAGE_FIT_MODES
        or not isinstance(spec.get("override_quiet_hours"), bool)
    ):
        return _error("invalid_request", "fit and override_quiet_hours are required.", 400)
    targets = _valid_target_ids(spec.get("device_ids"))
    if targets is None:
        return _error("invalid_target", "One or more target displays are unknown.", 400)
    framing, framing_err = _valid_framing(spec, fit_value)
    if framing_err is not None:
        return framing_err

    fit = fit_value
    override = bool(spec["override_quiet_hours"])
    resolved_targets = list(targets)
    payload = image_bytes + b"\x00" + request_raw.encode("utf-8")

    def _work() -> JobOutcome:
        active, _quiet = _partition_quiet(resolved_targets, override=override)
        if not active:
            return JobOutcome.quiet(resolved_targets, "all_targets_in_quiet_hours")
        manager = _push_manager()
        if manager is None:
            return JobOutcome.failed("temporarily_unavailable", "The push pipeline is offline.")
        published: list[str] = []
        history_event_ids: list[str] = []
        for did in active:
            result = manager.push_image(
                image_bytes,
                source_label="Shared photo",
                device_id=did,
                fit=fit,
                bypass_coalesce=True,
                force_publish=True,
                # Resolved per target inside the push pipeline: the same
                # intent yields a different SourceCrop for each panel's
                # aspect, which is the point of normalized framing.
                framing=framing,
            )
            if result.status in _PUBLISHED_STATUSES:
                published.append(did)
                history_event_ids.extend(_history_event_ids(result) or [])
        if published:
            return JobOutcome.published(
                published,
                history_event_ids=history_event_ids or None,
            )
        return JobOutcome.failed("render_failed", "The image could not be published.")

    return _reserve_and_run(
        kind="image_push",
        key=key,
        payload=payload,
        target_ids=resolved_targets,
        label="Shared photo",
        work=_work,
    )


# -- link send (image URL + webpage) -------------------------------------


def _valid_remote_source_url(raw: Any) -> str | None:
    """Validate a contract ``RemoteSourceURL``: an absolute http(s) URL, no
    embedded credentials, 8-4096 chars, no whitespace. Returns the URL or
    ``None``. Shape only; the public-network policy is enforced separately
    (synchronously via ``assert_public_url`` and per-hop during fetch/render)."""
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if not (8 <= len(url) <= 4096) or any(ch.isspace() for ch in url):
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return url


def _link_send_common(body: Any) -> tuple[str, str, bool, list[str]] | tuple[Response, int]:
    """Shared validation for the image-URL / webpage routes: url, fit,
    override_quiet_hours, device_ids, and the synchronous strict URL
    pre-check. Returns ``(url, fit, override, targets)`` or an error tuple."""
    if not isinstance(body, dict):
        return _error("invalid_request", "A JSON body is required.", 400)
    url = _valid_remote_source_url(body.get("url"))
    if url is None:
        return _error("invalid_request", "A valid absolute http(s) url is required.", 400)
    fit_value = body.get("fit")
    if (
        not isinstance(fit_value, str)
        or fit_value not in _IMAGE_FIT_MODES
        or not isinstance(body.get("override_quiet_hours"), bool)
    ):
        return _error("invalid_request", "fit and override_quiet_hours are required.", 400)
    targets = _valid_target_ids(body.get("device_ids"))
    if targets is None:
        return _error("invalid_target", "One or more target displays are unknown.", 400)
    # Strict public-only pre-check. Redirect hops are re-validated during the
    # fetch (fetch_bytes) / render (the Chromium interceptor), so a redirect to
    # a private host still fails the job even though this check passes.
    try:
        assert_public_url(url)
    except BlockedURLError:
        return _error("url_blocked", "That URL isn't a public destination.", 400)
    return url, fit_value, bool(body["override_quiet_hours"]), list(targets)


def _fan_out_bytes(
    manager: Any,
    image_bytes: bytes,
    *,
    source: str,
    label: str,
    fit: str,
    active: list[str],
) -> tuple[list[str], list[str]]:
    """Push already-fetched / rendered bytes to each active target, tagging the
    canonical History ``source`` (``url`` / ``webpage``). Returns
    ``(published_device_ids, history_event_ids)``."""
    published: list[str] = []
    history_event_ids: list[str] = []
    for did in active:
        result = manager.push_image(
            image_bytes,
            source_label=label,
            device_id=did,
            fit=fit,
            bypass_coalesce=True,
            force_publish=True,
            source=source,
        )
        if result.status in _PUBLISHED_STATUSES:
            published.append(did)
            history_event_ids.extend(_history_event_ids(result) or [])
    return published, history_event_ids


@bp.post("/image-urls")
@_require_companion("push:write")
def push_image_url() -> Any:
    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None

    raw = request.get_data(cache=True)
    common = _link_send_common(request.get_json(silent=True))
    if isinstance(common, tuple) and len(common) == 2 and isinstance(common[1], int):
        return common  # error envelope
    url, fit, override, resolved_targets = common

    def _work() -> JobOutcome:
        active, _quiet = _partition_quiet(resolved_targets, override=override)
        if not active:
            return JobOutcome.quiet(resolved_targets, "all_targets_in_quiet_hours")
        manager = _push_manager()
        if manager is None:
            return JobOutcome.failed("temporarily_unavailable", "The push pipeline is offline.")
        # Fetch once through the strict policy, then fan the bytes out per
        # target (no per-display re-fetch). A redirect to a private host is
        # refused here (fetch_bytes re-validates every hop).
        try:
            image_bytes = manager.fetch_remote_image_strict(url)
        except BlockedURLError:
            return JobOutcome.failed("url_blocked", "The URL (or a redirect) wasn't public.")
        except Exception as err:
            return JobOutcome.failed("fetch_failed", f"The image URL could not be fetched: {err}")
        published, history_event_ids = _fan_out_bytes(
            manager, image_bytes, source="url", label=url, fit=fit, active=active
        )
        if published:
            return JobOutcome.published(published, history_event_ids=history_event_ids or None)
        return JobOutcome.failed("render_failed", "The image could not be published.")

    return _reserve_and_run(
        kind="image_url_push",
        key=key,
        payload=raw,
        target_ids=resolved_targets,
        label=url,
        work=_work,
    )


@bp.post("/webpages")
@_require_companion("push:write")
def push_webpage_url() -> Any:
    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None

    if current_app.config.get("BROWSER_POOL") is None:
        return _error("temporarily_unavailable", "Webpage rendering isn't available.", 503)

    body = request.get_json(silent=True)
    # viewport_w is optional (server owns the logical height); validate before
    # the shared checks so a bad value fails fast with invalid_request.
    viewport_w = 1280
    if isinstance(body, dict) and "viewport_w" in body:
        vw = body.get("viewport_w")
        if not isinstance(vw, int) or isinstance(vw, bool) or not (200 <= vw <= 4096):
            return _error("invalid_request", "viewport_w must be an integer 200-4096.", 400)
        viewport_w = vw

    raw = request.get_data(cache=True)
    common = _link_send_common(body)
    if isinstance(common, tuple) and len(common) == 2 and isinstance(common[1], int):
        return common  # error envelope
    url, fit, override, resolved_targets = common

    def _work() -> JobOutcome:
        active, _quiet = _partition_quiet(resolved_targets, override=override)
        if not active:
            return JobOutcome.quiet(resolved_targets, "all_targets_in_quiet_hours")
        manager = _push_manager()
        if manager is None:
            return JobOutcome.failed("temporarily_unavailable", "The push pipeline is offline.")
        # Strict pre-check refused the initial URL synchronously; the renderer's
        # interceptor holds every hop Chromium follows to the same policy. Render
        # once at the logical viewport, then fan the bytes out per target.
        try:
            page_bytes = manager.render_webpage_png(url, viewport_w=viewport_w, allow_local=False)
        except Exception as err:
            return JobOutcome.failed("render_failed", f"The webpage could not be rendered: {err}")
        published, history_event_ids = _fan_out_bytes(
            manager, page_bytes, source="webpage", label=url, fit=fit, active=active
        )
        if published:
            return JobOutcome.published(published, history_event_ids=history_event_ids or None)
        return JobOutcome.failed("render_failed", "The webpage could not be published.")

    return _reserve_and_run(
        kind="webpage_push",
        key=key,
        payload=raw,
        target_ids=resolved_targets,
        label=url,
        work=_work,
    )


# -- History -------------------------------------------------------------


def _history_label(row: EventRow) -> str:
    """Resolve the same friendly target labels the admin History page uses."""
    if row.source == "button":
        device = _devices().get(row.target)
        if device is not None and device.kind_of is not None:
            return device.display_name
    page = _pages().get(row.target)
    if page is not None:
        return page.name or page.id
    return row.target or row.source


def _history_row(history_id: str) -> EventRow | None:
    try:
        event_id = parse_history_id(history_id)
    except InvalidHistoryCursor:
        return None
    row = _events().get(event_id)
    return row if row is not None and row.type == "push" else None


@bp.get("/history")
@_require_companion("devices:read")
def get_history() -> Any:
    before_id = request.args.get("before_id")
    raw_limit = request.args.get("limit")
    limit = DEFAULT_HISTORY_LIMIT
    if raw_limit is not None:
        try:
            limit = int(raw_limit)
        except ValueError:
            return _error("invalid_request", "limit must be an integer from 1 to 100.", 400)
        if limit < 1 or limit > MAX_HISTORY_LIMIT:
            return _error("invalid_request", "limit must be an integer from 1 to 100.", 400)
    try:
        body = list_history(
            _events(),
            Path(current_app.config["RENDERS_DIR"]),
            before_id=before_id,
            limit=limit,
            label_resolver=_history_label,
        )
    except InvalidHistoryCursor:
        return _error("invalid_request", "before_id is not a valid History cursor.", 400)
    return jsonify(body)


@bp.get("/history/<history_id>/preview")
@_require_companion("devices:read")
def history_preview(history_id: str) -> Any:
    retained = retained_composition_for_history(
        _events(),
        Path(current_app.config["RENDERS_DIR"]),
        history_id,
    )
    if retained is None:
        return _error("not_found", "No retained preview for that History row.", 404)
    if request.if_none_match and retained.etag in request.if_none_match:
        resp = current_app.response_class(status=304)
    else:
        try:
            payload = retained.path.read_bytes()
        except OSError:
            return _error("not_found", "The retained preview is no longer available.", 404)
        resp = current_app.response_class(payload, mimetype="image/png")
    resp.set_etag(retained.etag)
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp


@bp.post("/history/<history_id>/resend")
@_require_companion("push:write")
def resend_history(history_id: str) -> Any:
    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None

    raw = request.get_data(cache=True)
    body = request.get_json(silent=True)
    if (
        not isinstance(body, dict)
        or set(body) != {"override_quiet_hours"}
        or not isinstance(body.get("override_quiet_hours"), bool)
    ):
        return _error("invalid_request", "override_quiet_hours (boolean) is required.", 400)

    row = _history_row(history_id)
    if row is None:
        return _error("not_found", "No History row with that id.", 404)
    retained = retained_resend_composition_for_row(
        row,
        Path(current_app.config["RENDERS_DIR"]),
    )
    original_targets = list(
        dict.fromkeys(
            value for value in row.extra.get("device_ids", []) if isinstance(value, str) and value
        )
    )
    if retained is None or not original_targets:
        return _error(
            "not_resendable",
            "The original composition or target snapshot is no longer available.",
            409,
        )
    targets = _valid_target_ids(original_targets)
    if targets is None:
        return _error(
            "not_resendable",
            "One or more original target displays are no longer available.",
            409,
        )

    override = bool(body["override_quiet_hours"])
    event_id = row.id
    resolved_targets = list(targets)

    def _work() -> JobOutcome:
        active, _quiet = _partition_quiet(resolved_targets, override=override)
        if not active:
            return JobOutcome.quiet(resolved_targets, "all_targets_in_quiet_hours")
        manager = _push_manager()
        if manager is None:
            return JobOutcome.failed("temporarily_unavailable", "The push pipeline is offline.")
        result = manager.republish(event_id, device_ids=set(active))
        if result.status in _PUBLISHED_STATUSES:
            return JobOutcome.published(
                active,
                history_event_ids=_history_event_ids(result),
            )
        return JobOutcome.failed("render_failed", result.error or f"resend {result.status}")

    return _reserve_and_run(
        kind="history_resend",
        key=key,
        payload=raw,
        target_ids=resolved_targets,
        label=_history_label(row),
        work=_work,
    )


# -- jobs ----------------------------------------------------------------


@bp.get("/jobs/<job_id>")
@_require_companion()
def get_job(job_id: str) -> Any:
    job = _jobs_store().get(job_id)
    if job is None:
        return _error("not_found", "No job with that id.", 404)
    resp = jsonify({"job": job.public_dict()})
    if not job.terminal:
        resp.headers["Retry-After"] = "2"
    return resp


# -- previews ------------------------------------------------------------
#
# Read-only PNG previews for the app's Displays / Dashboards cards
# (additive `previews` feature, discussion #147). Both reuse the same
# render machinery the internal `/preview/<device>` and
# `/compose/<page>/preview.png` routes use, but under companion bearer auth
# and off the LAN/session-gated internal routes. No token appears in a URL.


@bp.get("/devices/<device_id>/preview")
@_require_companion("devices:read")
def device_preview(device_id: str) -> Any:
    """Logical full-frame PNG for a display.

    Without ``revision``, REST polling devices use the frame most recently
    returned by ``/frame``; transports without a reliable served signal fall
    back to server-latest. A ``revision`` advertised by ``pending_render``
    selects that exact server-latest render, including during the normal
    superseded-render grace window.

    The preview includes the selected image-fit mode and logical panel geometry,
    but excludes hardware-only row-stride and mount compensation. A live
    partial-refresh patch may be newer than this last full frame.
    """
    device = _devices().get(device_id)
    if device is None or device.kind_of is None:
        return _error("not_found", "No display with that id.", 404)
    manager = _push_manager()
    render: dict[str, Any] | None = None
    if manager is not None:
        revision = request.args.get("revision", "").strip()
        if revision:
            latest_lookup = getattr(manager, "latest_render_for", None)
            latest = latest_lookup(device_id) if callable(latest_lookup) else None
            if isinstance(latest, dict) and latest.get("digest") == revision:
                render = latest
            else:
                previous_lookup = getattr(manager, "previous_render_for", None)
                previous = previous_lookup(device_id) if callable(previous_lookup) else None
                if isinstance(previous, dict) and previous.get("digest") == revision:
                    render = previous
        elif device.transport == "rest":
            served_lookup = getattr(manager, "last_served_render_for", None)
            if callable(served_lookup):
                render = served_lookup(device_id)
        else:
            render = manager.latest_render_for(device_id)
    if not isinstance(render, dict):
        if request.args.get("revision", "").strip():
            message = "No retained preview exists for that display revision."
        else:
            message = (
                "No frame has been served to this display yet."
                if device.transport == "rest"
                else "No frame has been rendered for this display yet."
            )
        return _error("not_found", message, 404)
    digest = render.get("preview_digest")
    if digest is None:
        return _error("not_found", "No preview is available for the served frame yet.", 404)

    retained = retained_device_preview(Path(current_app.config["RENDERS_DIR"]), digest)
    if retained is None:
        return _error("not_found", "The rendered frame is no longer available.", 404)

    if request.if_none_match and request.if_none_match.contains_weak(retained.etag):
        resp = current_app.response_class(status=304)
        resp.set_etag(retained.etag)
        resp.headers["Cache-Control"] = "private, no-cache"
        return resp

    resp = current_app.response_class(retained.path.read_bytes(), mimetype="image/png")
    resp.set_etag(retained.etag)
    # Content-addressed by digest, but the "latest" pointer moves, so the
    # client must revalidate rather than hold it blindly.
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp


def _preview_dims_for_device(device: Device) -> tuple[int, int]:
    """Preview render dims for an explicit target display: its panel scaled
    so the longest edge is PREVIEW_MAX_DIM, matching the composer's own
    preview sizing."""
    from app.composer import PREVIEW_MAX_DIM

    panel = device_panel(device) or resolve_settings_panel(_settings())
    pw, ph = max(1, int(panel.w)), max(1, int(panel.h))
    longest = max(pw, ph)
    if longest > PREVIEW_MAX_DIM:
        scale = PREVIEW_MAX_DIM / longest
        pw, ph = max(1, round(pw * scale)), max(1, round(ph * scale))
    return pw, ph


@bp.get("/dashboards/<dashboard_id>/preview")
@_require_companion("dashboards:read")
def dashboard_preview(dashboard_id: str) -> Any:
    """Cached composition PNG for a dashboard. Never rendered while listing:
    on a cache miss a background render is enqueued and 202 + Retry-After is
    returned, so the client re-polls. ETag is the content+dims token, so an
    unchanged dashboard revalidates to 304. Optional `device_id` query picks
    the target dimensions; otherwise the dashboard's resolved preview target
    (or the virtual panel) is used.

    A faithful cached visual preview, invalidated by layout / config /
    target-dimension changes; it does not promise live widget data."""
    from app import preview_cache
    from app.composer import page_preview_token, preview_dims

    page = _pages().get(dashboard_id)
    if page is None:
        return _error("not_found", "No dashboard with that id.", 404)

    devices = _devices()
    dev_q = request.args.get("device_id")
    if dev_q:
        device = devices.get(dev_q)
        if device is None or device.kind_of is None:
            return _error("invalid_target", "Unknown target display.", 400)
        width, height = _preview_dims_for_device(device)
    else:
        width, height = preview_dims(page, devices, _settings())

    token = page_preview_token(page, (width, height))
    cache_path = (
        Path(current_app.config["DATA_ROOT"]) / "core" / "previews" / f"{dashboard_id}__{token}.png"
    )

    if not cache_path.exists():
        preview_cache.submit(
            key=f"{dashboard_id}__{token}",
            base_url=request.host_url.rstrip("/"),
            page_id=dashboard_id,
            width=width,
            height=height,
            cache_path=cache_path,
            pool=current_app.config.get("BROWSER_POOL"),
        )
        resp = current_app.response_class(status=202)
        resp.headers["Retry-After"] = "2"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    if request.if_none_match and token in request.if_none_match:
        resp = current_app.response_class(status=304)
    else:
        resp = current_app.response_class(cache_path.read_bytes(), mimetype="image/png")
    resp.set_etag(token)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


def register(app: Flask) -> None:
    app.register_blueprint(bp)
