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

import hashlib
import json as _json
import logging
import re
import secrets
import time
import urllib.parse
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Flask, current_app, g, jsonify, request, url_for
from pydantic import ValidationError
from werkzeug.wrappers import Response

from app import companion_albums, companion_gallery
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
from app.companion_lineups import (
    _web_only_reason,
    apply_patch,
    lineup_dict,
    lineup_etag,
    resolved_device_ids,
)
from app.data_change_refresh import (
    DataChangeEvent,
    personal_data_delete_event,
    personal_data_update_event,
)
from app.device_capability import capability_support_map, heartbeat_freshness
from app.device_loader import Device, DeviceRegistry
from app.device_preview import retained_device_preview
from app.device_upcoming import (
    DEFAULT_HOURS as DEVICE_TIMELINE_DEFAULT_HOURS,
)
from app.device_upcoming import (
    DEFAULT_LIMIT as DEVICE_TIMELINE_DEFAULT_LIMIT,
)
from app.device_upcoming import (
    MAX_HOURS as DEVICE_TIMELINE_MAX_HOURS,
)
from app.device_upcoming import (
    MAX_LIMIT as DEVICE_TIMELINE_MAX_EVENTS,
)
from app.health_summary import (
    HEALTH_SUMMARY_MAX_BYTES,
    HEALTH_SUMMARY_SOURCE_ID,
    InvalidHealthSummary,
    validate_health_summary,
)
from app.http_headers import HeaderError, validate_header_map
from app.image_upload import (
    IMAGE_CONTENT_TYPES,
    IMAGE_FIT_MODES,
    IMAGE_MAX_EDGE,
    IMAGE_UPLOAD_BYTES,
)
from app.image_upload import (
    decode_edge as _decode_edge,
)
from app.lineup_authoring import build_lineup
from app.net_guard import BlockedURLError, assert_public_url
from app.panel import device_panel, resolve_settings_panel
from app.quiet_hours import device_is_quiet, resolve_quiet_hours
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

bp = Blueprint("companion_api", __name__, url_prefix="/api/app/v1")

# -- limits advertised by the capability probe ---------------------------
#
# Server-advertised rather than baked into the client (discussion #147):
# tuning these needs no app release. The byte / edge caps are the ones the
# owner settled on (~25 MB, 8K max edge); job + idempotency retention are
# 24 h. These constants are reused by Phase 2's upload + job enforcement.
# Contract 0.6 ``image_framing``: mandatory editor bound whenever the
# capability is advertised, so clients never hard-code a zoom range. An
# editor bound rather than a quality promise: the resolved crop is always
# clamped to source bounds, and a heavy crop of a small photo will be soft
# on a large panel regardless of what the editor allowed.
IMAGE_FRAMING_MAX_ZOOM = 4
JOB_RETENTION_SECONDS = 86_400
IDEMPOTENCY_RETENTION_SECONDS = 86_400

# Personal-data bridge (#176, contract 0.7): a snapshot is fresh until it is
# this old, then stale, then expired (raw values redacted). ``max_ttl`` bounds
# how far a client may set expires_at past generated_at. Server-advertised so
# the app does not hard-code them.
PERSONAL_DATA_STALE_SECONDS = 86_400  # 24 h
PERSONAL_DATA_MAX_TTL_SECONDS = 172_800  # 48 h
PERSONAL_DATA_SOURCES = ("reminders", "reminders.fridge", HEALTH_SUMMARY_SOURCE_ID)
PERSONAL_DATA_SNAPSHOT_VERSION = "personal_data_bridge_v1"
PERSONAL_DATA_REMINDERS_MAX_LISTS = 20
PERSONAL_DATA_REMINDERS_MAX_ITEMS = 200
_REMINDER_PRIORITIES = frozenset(("none", "low", "medium", "high"))

# Features this server serves. The client gates on this list rather than
# assuming the full set, so unshipped surfaces degrade cleanly.
FEATURES = (
    "devices",
    "device_setup",
    "dashboards",
    "dashboard_push",
    "image_push",
    "image_url_push",
    "jobs",
    "history",
    "image_framing",
    "personal_data_reminders",
    "personal_data_health",
    # Read + control for Lineups (#205). Authoring is a separate capability
    # so a client can offer the controls without implying it can edit, which
    # it can't until the write path behind #204 exists.
    "lineups",
    "lineup_control",
    "lineup_authoring",
    # ``GET /session``: the client can read the scopes its credential
    # carries now rather than the ones it was issued, which is the only way
    # to know an operator has granted or withdrawn an optional one (#203).
    "session_read",
)

_PAIRING_CODE_RE = re.compile(r"^[0-9]{6,12}$")
_IMAGE_CONTENT_TYPES = frozenset(IMAGE_CONTENT_TYPES)
_IMAGE_FIT_MODES = frozenset(IMAGE_FIT_MODES)
_GALLERY_CONTENT_TYPES = frozenset(companion_gallery.GALLERY_IMAGE_CONTENT_TYPES)


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


def _firmware_pairings() -> PairingStore:
    return current_app.config["PAIRING_STORE"]  # type: ignore[no-any-return]


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


def _personal_data_publisher() -> tuple[str, str]:
    """Stable, non-PII publisher identity derived from the paired app install."""
    record = g.companion
    client = record.client if isinstance(record.client, dict) else {}
    installation_id = client.get("installation_id")
    stable_input = (
        installation_id
        if isinstance(installation_id, str) and installation_id
        else str(record.token_id)
    )
    digest = hashlib.sha256(f"companion-publisher:{stable_input}".encode()).hexdigest()[:24]
    return f"companion_{digest}", record.name


def _notify_data_change(event: DataChangeEvent | None) -> None:
    """Enqueue a post-ingestion refresh without changing API semantics."""
    if event is None:
        return
    coordinator = current_app.config.get("DATA_CHANGE_COORDINATOR")
    if coordinator is None:
        return
    try:
        coordinator.notify(event)
    except Exception:
        # Storage already succeeded. Refresh coordination is fire-and-forget
        # and must never turn an accepted snapshot into a failed upload.
        logger.exception("could not enqueue personal-data change event")


# -- error envelope ------------------------------------------------------


def _error(code: str, message: str, status: int, **extras: Any) -> tuple[Response, int]:
    """Build the contract ``ErrorResponse`` envelope. ``request_id`` is
    always attached so a failed companion call is traceable from the app
    without leaking anything sensitive.

    ``extras`` carries the few codes that need more than a message to be
    actionable: ``claims`` names the album holding each contested display,
    ``device_ids`` lists the targets that became unsupported. Both are
    defined in the contract for those codes and absent everywhere else."""
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": f"req_{secrets.token_hex(8)}",
    }
    error.update(extras)
    return jsonify({"error": error}), status


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
                # 403, not 401: the credential is valid and re-pairing won't
                # help. An optional scope is an operator toggle in Settings,
                # so answering 401 sends a client off to re-pair and land in
                # exactly the same place (#207).
                return _error(
                    "forbidden",
                    "This app hasn't been given permission for that. "
                    "Grant it in Settings, Companion.",
                    403,
                )
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
        # Advertised separately from webpage_push even though it rides the same
        # route: a client that sends ``headers`` to a server predating #234 gets
        # a 400 from ``additionalProperties: false`` with no way to have known,
        # so the flag is how it finds out before trying.
        feats.append("webpage_headers")
    if companion_gallery.gallery_available():
        # The photo library is a plugin, and a plugin can be uninstalled.
        # Advertising Gallery on an instance without it would leave the
        # client offering a browse tab that 404s on every folder (#225).
        feats.append("gallery")
    if companion_albums.albums_available():
        # Offline Album authoring needs both halves: the Gallery plugin for
        # the source photos and the album store for the producer. Advertised
        # separately from ``gallery`` because browsing a library and making a
        # display play one on its own are different things to offer (#230).
        feats.append("offline_albums")
    if current_app.config.get("SCHEDULER") is not None:
        # The upcoming projection replays the running engine's own gates, so
        # it can only be offered where that engine exists. An install without
        # one would have to answer from the stored records alone, which is
        # the plausible-but-wrong timeline the feature was built to avoid
        # (#232).
        feats.append("device_timeline")
    return feats


def _gallery_limits() -> dict[str, Any]:
    """Gallery's own upload bounds, required whenever the capability is
    advertised and deliberately separate from the ``POST /images`` ones:
    a photo entering the library and a photo being sent to a panel are
    different actions with different ceilings."""
    if not companion_gallery.gallery_available():
        return {}
    return {
        "gallery_upload_bytes": companion_gallery.GALLERY_UPLOAD_BYTES,
        "gallery_image_content_types": list(companion_gallery.GALLERY_IMAGE_CONTENT_TYPES),
        "gallery_upload_batch_size": companion_gallery.GALLERY_UPLOAD_BATCH_SIZE,
    }


def _timeline_limits() -> dict[str, Any]:
    """The device timeline's query bounds, required whenever the capability
    is advertised so the client asks for a window the server will honour
    instead of discovering the ceiling through a 400."""
    if current_app.config.get("SCHEDULER") is None:
        return {}
    return {
        "device_timeline_max_hours": DEVICE_TIMELINE_MAX_HOURS,
        "device_timeline_max_events": DEVICE_TIMELINE_MAX_EVENTS,
    }


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
        # Source ids are advertised directly so clients can prefer the most
        # capable strict schema without growing one feature flag per source.
        # ``personal_data_reminders`` remains in ``features`` for compatibility
        # with clients that predate this source list.
        "personal_data": {"sources": list(PERSONAL_DATA_SOURCES)},
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
            **_timeline_limits(),
            **_gallery_limits(),
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


@bp.post("/device-pairings")
@_require_companion("device_setup:write")
def create_device_pairing() -> Any:
    """Mint a registration code for one physically-nearby display.

    Companion passes the code to firmware over its authenticated BLE setup
    channel. Firmware still redeems it through ``POST /api/v1/device/register``,
    so the app never receives a firmware access token and this route cannot
    directly create a device.
    """
    record = _firmware_pairings().issue(
        note=f"Companion: {g.companion.name}"[:64],
        owner_key=f"companion:{g.companion.token_id}",
    )
    return jsonify({"code": record.code, "expires_at": _iso(record.expires_at)}), 201


@bp.get("/session")
@_require_companion()
def read_session() -> Any:
    """What this credential currently carries.

    An optional scope can be granted or withdrawn from Settings long after
    pairing, and the pairing response is the only place a client ever saw
    its scope list, so the copy it persisted goes stale the moment an
    operator flips a toggle. Without this the app can't tell "authoring
    isn't granted" from "authoring is granted" until a save answers 403,
    which is the wrong moment to find out (#203).

    ``settings_url`` is where the operator does the granting, resolved from
    the routing table rather than spelled out, since a hardcoded path is
    how the Lineup web link shipped as a 404 for five releases.
    """
    record = g.companion
    return jsonify(
        {
            "token_id": record.token_id,
            "scopes": list(record.scopes),
            "settings_url": url_for("auth.companion_index"),
        }
    )


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
# expiry. This is synchronous state, not a job: ingestion itself performs no
# render / publish / History write. An accepted semantic change may enqueue a
# detached refresh that runs only after the response path is complete.


def _parse_iso(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


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
        if "due_date" not in item:
            return bad("item due_date is required")
        iid, title = item.get("id"), item.get("title")
        if not isinstance(iid, str) or not (1 <= len(iid) <= 256):
            return bad("item id must be a 1-256 char string")
        if not isinstance(title, str) or not (1 <= len(title) <= 512):
            return bad("item title must be a 1-512 char string")
        due = item.get("due_date")
        if due is not None and not _is_iso_date(due):
            return bad("item due_date must be an ISO date or null")
        if item.get("priority") not in _REMINDER_PRIORITIES:
            return bad("item priority must be none/low/medium/high")
        if item.get("completed") is not False:
            return bad("item completed must be false")
    return (body, gen, exp), None


def _validate_reminders(
    source_id: str, body: Any
) -> tuple[tuple[dict[str, Any], float, float] | None, tuple[Response, int] | None]:
    """Validate the generic, grouped Apple Reminders snapshot.

    The schema stays deliberately bounded and source-specific: list ids are
    opaque publication identifiers from Companion, not EventKit identifiers,
    and all items still use the minimal v1 reminder shape.
    """

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
    if not isinstance(data, dict) or set(data) - {"lists"}:
        return bad("data must be an object with only lists")
    lists = data.get("lists")
    if not isinstance(lists, list):
        return bad("data.lists must be an array")
    if len(lists) > PERSONAL_DATA_REMINDERS_MAX_LISTS:
        return bad(f"too many lists (max {PERSONAL_DATA_REMINDERS_MAX_LISTS})")

    seen_list_ids: set[str] = set()
    total_items = 0
    for reminder_list in lists:
        if not isinstance(reminder_list, dict):
            return bad("each list must be an object")
        if set(reminder_list) - {"id", "title", "items"}:
            return bad("a list has unexpected fields")
        list_id, list_title = reminder_list.get("id"), reminder_list.get("title")
        if not isinstance(list_id, str) or not (1 <= len(list_id) <= 256):
            return bad("list id must be a 1-256 char string")
        if list_id in seen_list_ids:
            return bad("list ids must be unique")
        seen_list_ids.add(list_id)
        if not isinstance(list_title, str) or not (1 <= len(list_title) <= 256):
            return bad("list title must be a 1-256 char string")
        items = reminder_list.get("items")
        if not isinstance(items, list):
            return bad("list items must be an array")
        total_items += len(items)
        if total_items > PERSONAL_DATA_REMINDERS_MAX_ITEMS:
            return bad(f"too many items across all lists (max {PERSONAL_DATA_REMINDERS_MAX_ITEMS})")
        for item in items:
            if not isinstance(item, dict):
                return bad("each item must be an object")
            if set(item) - {"id", "title", "due_date", "priority", "completed"}:
                return bad("an item has unexpected fields")
            if "due_date" not in item:
                return bad("item due_date is required")
            item_id, item_title = item.get("id"), item.get("title")
            if not isinstance(item_id, str) or not (1 <= len(item_id) <= 256):
                return bad("item id must be a 1-256 char string")
            if not isinstance(item_title, str) or not (1 <= len(item_title) <= 512):
                return bad("item title must be a 1-512 char string")
            due = item.get("due_date")
            if due is not None and not _is_iso_date(due):
                return bad("item due_date must be an ISO date or null")
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
    if source_id == HEALTH_SUMMARY_SOURCE_ID:
        content_length = request.content_length
        if content_length is not None and content_length > HEALTH_SUMMARY_MAX_BYTES:
            return _error("invalid_snapshot", "health.summary exceeds the 256 KiB limit", 400)
        if len(request.get_data(cache=True)) > HEALTH_SUMMARY_MAX_BYTES:
            return _error("invalid_snapshot", "health.summary exceeds the 256 KiB limit", 400)
    body = request.get_json(silent=True)
    result: tuple[dict[str, Any], float, float] | None
    err: tuple[Response, int] | None
    if source_id == HEALTH_SUMMARY_SOURCE_ID:
        try:
            result = validate_health_summary(
                source_id,
                body,
                active_timezone=_timezone(),
                snapshot_version=PERSONAL_DATA_SNAPSHOT_VERSION,
                maximum_ttl_seconds=PERSONAL_DATA_MAX_TTL_SECONDS,
            )
        except InvalidHealthSummary as exc:
            return _error("invalid_snapshot", str(exc), 400)
        err = None
    elif source_id == "reminders":
        result, err = _validate_reminders(source_id, body)
    else:
        result, err = _validate_reminders_fridge(source_id, body)
    if err is not None:
        return err
    assert result is not None  # narrow for mypy; err is None here
    snapshot, gen, exp = result
    now = time.time()
    store = _personal_data()
    publisher_id, publisher_name = _personal_data_publisher()
    existing = store.get(source_id, publisher_id=publisher_id)
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
    previous_snapshot = existing.get("snapshot") if isinstance(existing, dict) else None
    if not isinstance(previous_snapshot, dict):
        previous_snapshot = None
    event = personal_data_update_event(source_id, previous_snapshot, snapshot)
    store.put(
        source_id,
        snapshot=snapshot,
        generated_epoch=gen,
        expires_epoch=exp,
        publisher_id=publisher_id,
        publisher_name=publisher_name,
    )
    _notify_data_change(event)
    return jsonify(_source_status(source_id, gen, exp, now)), 200


@bp.get("/personal-data/status")
@_require_companion("personal_data:write")
def personal_data_status() -> Any:
    """Freshness metadata only, never the stored values."""
    now = time.time()
    store = _personal_data()
    publisher_id, _publisher_name = _personal_data_publisher()
    store.purge_expired(now)
    sources = []
    for source_id, rec in sorted(store.all(publisher_id=publisher_id).items()):
        gen, exp = rec.get("generated_epoch"), rec.get("expires_epoch")
        if isinstance(gen, (int, float)) and isinstance(exp, (int, float)):
            sources.append(_source_status(source_id, float(gen), float(exp), now))
    return jsonify({"sources": sources})


@bp.delete("/personal-data/<source_id>")
@_require_companion("personal_data:write")
def delete_personal_data(source_id: str) -> Any:
    """Idempotently drop a source's snapshot (on disable)."""
    store = _personal_data()
    publisher_id, _publisher_name = _personal_data_publisher()
    existing = store.get(source_id, publisher_id=publisher_id)
    previous_snapshot = existing.get("snapshot") if isinstance(existing, dict) else None
    if not isinstance(previous_snapshot, dict):
        previous_snapshot = None
    if store.delete(source_id, publisher_id=publisher_id):
        _notify_data_change(personal_data_delete_event(source_id, previous_snapshot))
    return Response(status=204)


# -- devices -------------------------------------------------------------


def _orientation(width: int, height: int) -> str:
    return "portrait" if height > width else "landscape"


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
    freshness, last_seen_at = heartbeat_freshness(device, status)

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
        # Bare Phosphor slug, the same identity the device pickers and
        # Settings cards use; kind default until the user overrides it
        # per instance, always resolved (#184).
        "icon": device.icon,
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
        # Support keyed by protocol capability, not by model: a client must
        # never carry its own list of which panels have an SD card, and a
        # target it can't tell apart from an eligible one is a target the
        # operator will bind and then wonder about (#225).
        "capability_support": capability_support_map(device, status),
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


# -- device timeline -----------------------------------------------------


def _scheduler() -> Any:
    return current_app.config.get("SCHEDULER")


def _current_frame_at(device: Device) -> str | None:
    """When the frame now on this display was handed over, or ``None``.

    For a REST display that's the delivery-side handover the push manager
    persists next to the served digest. For every other transport there is no
    handover to observe, so it's when the current frame was published. Neither
    is a claim that the panel finished repainting, and a display the server
    can't establish a baseline for gets ``None`` rather than a guess, which
    the client reads as "show the next update, no progress bar".
    """
    manager = _push_manager()
    if manager is None:
        return None
    if device.transport == "rest":
        lookup = getattr(manager, "last_served_render_for", None)
        served = lookup(device.id) if callable(lookup) else None
        at = served.get("served_at") if isinstance(served, dict) else None
        return _iso(float(at)) if isinstance(at, (int, float)) else None
    lookup = getattr(manager, "latest_render_for", None)
    latest = lookup(device.id) if callable(lookup) else None
    at = latest.get("timestamp") if isinstance(latest, dict) else None
    return _iso(float(at)) if isinstance(at, (int, float)) else None


def _bounded_query(name: str, default: int, lo: int, hi: int) -> int | None:
    """Read an integer query parameter, or ``None`` when it's out of range
    or not a number at all. Absent means the default."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if lo <= value <= hi else None


@bp.get("/devices/<device_id>/upcoming")
@_require_companion("devices:read", "dashboards:read", "lineups:read")
def device_upcoming(device_id: str) -> Any:
    """What is expected to visibly change or repaint this display next.

    Read-only, and deliberately only the part of the answer the scheduler
    genuinely owns: Lineup advances, Keep Fresh re-renders, and Home Return.
    Manual Send, webhooks, Home Assistant events, and data-change refreshes
    have no schedule to project and are not guessed at (#232).

    Pending delivery is not repeated here. ``has_pending_render`` on
    ``/devices`` already says a newer frame is waiting for a display to wake,
    and two representations of one state is how they end up disagreeing.
    """
    device = _devices().get(device_id)
    if device is None or device.kind_of is None:
        return _error("not_found", "No such display.", 404)
    scheduler = _scheduler()
    if scheduler is None:
        return _error("temporarily_unavailable", "The scheduler isn't running.", 503)

    hours = _bounded_query("hours", DEVICE_TIMELINE_DEFAULT_HOURS, 1, DEVICE_TIMELINE_MAX_HOURS)
    limit = _bounded_query("limit", DEVICE_TIMELINE_DEFAULT_LIMIT, 1, DEVICE_TIMELINE_MAX_EVENTS)
    if hours is None or limit is None:
        return _error(
            "invalid_request",
            f"hours must be 1-{DEVICE_TIMELINE_MAX_HOURS} and "
            f"limit 1-{DEVICE_TIMELINE_MAX_EVENTS}.",
            400,
        )

    now = datetime.now(UTC)
    quiet_window = resolve_quiet_hours(_settings().get_section("app") or {}, device)
    events = scheduler.upcoming_for_device(
        device_id,
        now=now,
        hours=hours,
        limit=limit,
        quiet_window=quiet_window,
    )
    return jsonify(
        {
            "device_id": device_id,
            "generated_at": _iso(now.timestamp()),
            "timezone": _timezone(),
            "through_at": _iso((now + timedelta(hours=hours)).timestamp()),
            "current_frame_at": _current_frame_at(device),
            "events": [
                {
                    "id": event.event_id(device_id),
                    "scheduled_at": _iso(event.scheduled_at.timestamp()),
                    "cause": event.cause,
                    "effect": event.effect,
                    "certainty": event.certainty,
                    "lineup_id": event.lineup_id,
                    "lineup_name": event.lineup_name,
                    "dashboard_id": event.dashboard_id,
                    "dashboard_name": event.dashboard_name,
                }
                for event in events
            ],
        }
    )


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


# -- lineups -------------------------------------------------------------


def _decks() -> Any:
    return current_app.config.get("DECK_STORE")


def _deck_nav() -> Any:
    return current_app.config.get("DECK_NAV_STORE")


def _lineup_views() -> list[dict[str, Any]]:
    """Every Lineup, in the shape the app renders.

    Page names are resolved here rather than by the client so a Lineup
    listing a since-deleted dashboard reports it as missing instead of
    showing a bare id."""
    store = _decks()
    if store is None:
        return []
    return [_lineup_view(deck) for deck in store.all()]


@bp.get("/lineups")
@_require_companion("lineups:read")
def list_lineups() -> Any:
    return jsonify({"lineups": _lineup_views()})


def _unknown_page_id(page_ids: Iterable[str]) -> str | None:
    """The first dashboard id that doesn't exist, or None.

    A step pointing at nothing can never render, so both authoring routes
    refuse it up front rather than letting it surface later as a missing
    row on a card (#203)."""
    known = {p.id for p in _pages().list()}
    for page_id in page_ids:
        if page_id not in known:
            return page_id
    return None


def _page_devices() -> dict[str, list[str]]:
    """Dashboard id -> the displays it's bound to. What a Lineup with no
    binding of its own is resolved against."""
    return {p.id: list(dict.fromkeys(p.device_ids)) for p in _pages().list()}


def _lineup_targets(deck: Any) -> list[str]:
    """The displays an action on this Lineup may touch."""
    return resolved_device_ids(deck, _page_devices())


def _lineup_view(deck: Any) -> dict[str, Any]:
    """One Lineup's projection, for the routes that already hold the record."""
    pages = _pages().list()
    page_names = {p.id: (p.name or p.id) for p in pages}
    page_devices = {p.id: list(dict.fromkeys(p.device_ids)) for p in pages}
    nav = _deck_nav()
    current: dict[str, str] = {}
    if nav is not None:
        # Over the resolved displays, not the authored ones: a schedule-style
        # Lineup binds nothing, and reporting no current dashboard for it
        # would be a gap the app can't fill from anywhere else.
        for device_id in resolved_device_ids(deck, page_devices):
            page_id = nav.current_page(device_id, deck.id)
            if page_id:
                current[device_id] = page_id
    return lineup_dict(
        deck,
        page_names=page_names,
        page_devices=page_devices,
        current_pages=current,
        web_url=url_for("decks.editor", deck_id=deck.id),
    )


def _lineup_response(deck: Any, status: int = 200) -> Any:
    """A Lineup plus its version tag. The ETag is what a later PATCH sends
    back as ``If-Match``, so every response that hands out a record also
    hands out the token needed to edit it safely."""
    resp = jsonify({"lineup": _lineup_view(deck)})
    resp.status_code = status
    resp.headers["ETag"] = f'"{lineup_etag(deck)}"'
    return resp


@bp.get("/lineups/<lineup_id>")
@_require_companion("lineups:read")
def get_lineup(lineup_id: str) -> Any:
    store = _decks()
    deck = store.get(lineup_id) if store is not None else None
    if deck is None:
        return _error("not_found", "No lineup with that id.", 404)
    return _lineup_response(deck)


@bp.post("/lineups")
@_require_companion("lineups:write")
def create_lineup() -> Any:
    """Author a Lineup from one of the four intents.

    Goes through the same builder the web wizard uses (#204), so a Lineup
    made from the app and one made from the web are the same record made
    the same way, and neither carries fields the other can't represent.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("invalid_request", "A JSON object body is required.", 400)
    store = _decks()
    if store is None:
        return _error("temporarily_unavailable", "Lineups are not available.", 503)

    name = str(body.get("name") or "").strip()
    page_ids = [str(p) for p in (body.get("page_ids") or []) if p]
    device_ids = _valid_target_ids(body.get("device_ids")) if "device_ids" in body else []
    if device_ids is None:
        return _error("invalid_target", "One or more target displays are unknown.", 400)
    missing = _unknown_page_id(page_ids)
    if missing is not None:
        return _error("not_found", f"No dashboard with id {missing!r}.", 404)

    try:
        deck = build_lineup(
            intent=str(body.get("intent") or "").strip().lower(),
            lineup_id=_unique_lineup_id(store, name),
            name=name or "Lineup",
            page_ids=page_ids,
            device_ids=device_ids,
            dwell_minutes={str(k): int(v) for k, v in (body.get("dwell_minutes") or {}).items()},
            interval_minutes=int(body.get("interval_minutes") or 30),
            fires_at=str(body.get("fires_at") or "") or None,
            anchor=str(body.get("anchor") or "00:00") or "00:00",
        )
    except (ValidationError, ValueError, TypeError) as err:
        return _error("invalid_request", f"Invalid lineup: {err}", 400)

    store.upsert(deck)
    # Binding an unassigned dashboard is the server's job, and opt-in: the
    # app asks for it rather than the server deciding, so a dashboard that
    # lives on another display is never quietly moved (#203).
    if body.get("bind_unassigned_dashboards") and deck.device_ids:
        display = deck.device_ids[0]
        pages = _pages()
        for page in (pages.get(p.page_id) for p in deck.pages):
            if page is not None and not page.device_ids:
                pages.save(page.model_copy(update={"device_ids": [display]}))
    return _lineup_response(deck, 201)


@bp.patch("/lineups/<lineup_id>")
@_require_companion("lineups:write")
def update_lineup(lineup_id: str) -> Any:
    """Edit a Lineup the app can represent completely.

    Two refusals matter more than the edit itself. A record using anything
    outside the four intents is web-only, because a partial write from a
    client that can't see those fields would flatten them. And a stale
    ``If-Match`` is rejected rather than applied, because the web editor is
    very likely the other writer.
    """
    store = _decks()
    deck = store.get(lineup_id) if store is not None else None
    if deck is None:
        return _error("not_found", "No lineup with that id.", 404)

    reason = _web_only_reason(deck)
    if reason is not None:
        return _error(
            "invalid_request",
            f"This lineup {reason} and can only be edited in the web editor.",
            400,
        )

    presented = (request.headers.get("If-Match") or "").strip().strip('"')
    if not presented:
        return _error("invalid_request", "If-Match is required to edit a lineup.", 400)
    if presented != lineup_etag(deck):
        return _error(
            "precondition_failed",
            "The lineup changed since you loaded it; re-read it and try again.",
            412,
        )

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("invalid_request", "A JSON object body is required.", 400)
    if "device_ids" in body and _valid_target_ids(body.get("device_ids")) is None:
        return _error("invalid_target", "One or more target displays are unknown.", 400)
    # Same refusal as create, and for the same reason. Only ids being added
    # are checked: a member dashboard deleted since the Lineup was built
    # already reports as ``missing``, and refusing the edit would leave the
    # app unable to remove it (#203).
    if "page_ids" in body and isinstance(body.get("page_ids"), list):
        members = {page.page_id for page in deck.pages}
        added = [str(p) for p in body["page_ids"] if p and str(p) not in members]
        missing = _unknown_page_id(added)
        if missing is not None:
            return _error("not_found", f"No dashboard with id {missing!r}.", 404)
    try:
        updated = apply_patch(deck, body)
    except (ValidationError, ValueError, TypeError) as err:
        return _error("invalid_request", f"Invalid lineup: {err}", 400)

    store.upsert(updated)
    return _lineup_response(updated)


def _unique_lineup_id(store: Any, name: str) -> str:
    """Slug the name, then suffix until it's free. Mirrors the web wizard so
    an id looks the same whichever surface made it."""
    import re as _re

    base = _re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_") or "lineup"
    taken = {d.id for d in store.all()}
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


# Actions that only change stored state, versus those that repaint a panel.
# The split decides the response: a config flip answers with the updated
# record, a repaint goes through the job pipeline like every other write.
_LINEUP_CONFIG_ACTIONS = ("enable", "disable")
_LINEUP_PAINT_ACTIONS = ("next", "previous", "play")


@bp.post("/lineups/<lineup_id>/actions")
@_require_companion("lineups:control")
def lineup_action(lineup_id: str) -> Any:
    """Enable / disable a Lineup, or move a display through it.

    Control only: nothing here edits the Lineup's definition, so an
    advanced record the app refuses to edit is still fully operable.
    """
    store = _decks()
    deck = store.get(lineup_id) if store is not None else None
    if deck is None:
        return _error("not_found", "No lineup with that id.", 404)

    body = request.get_json(silent=True)
    action = str(body.get("action") or "").strip().lower() if isinstance(body, dict) else ""
    if action not in _LINEUP_CONFIG_ACTIONS + _LINEUP_PAINT_ACTIONS:
        return _error(
            "invalid_request",
            "action must be one of: "
            + ", ".join(_LINEUP_CONFIG_ACTIONS + _LINEUP_PAINT_ACTIONS)
            + ".",
            400,
        )

    if action in _LINEUP_CONFIG_ACTIONS:
        # No panel is touched, so no job and no idempotency key: the flip is
        # idempotent by construction (enable twice is enabled).
        store.upsert(deck.model_copy(update={"enabled": action == "enable"}))
        for view in _lineup_views():
            if view["id"] == lineup_id:
                return jsonify({"lineup": view})
        return _error("not_found", "No lineup with that id.", 404)

    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None
    raw = request.get_data(cache=True)
    if not isinstance(body, dict) or not isinstance(body.get("override_quiet_hours"), bool):
        return _error("invalid_request", "override_quiet_hours (boolean) is required.", 400)
    override = bool(body["override_quiet_hours"])

    # Resolved rather than authored. A daily or interval Lineup carries no
    # display of its own and fires at whatever its dashboards are bound to,
    # so filtering against ``deck.device_ids`` made every one of them
    # unplayable from the app while the engine ran them fine (#203).
    bound = _lineup_targets(deck)
    targets = _valid_target_ids(body.get("device_ids")) if "device_ids" in body else None
    if "device_ids" in body and targets is None:
        return _error("invalid_target", "One or more target displays are unknown.", 400)
    if targets is None:
        targets = list(bound)
    # A Lineup that reaches no display, however it's bound, has no panel to move.
    targets = [d for d in targets if d in bound]
    if not targets:
        return _error("invalid_target", "The lineup has no bound displays.", 400)

    page_ids = deck.page_ids
    nav = _deck_nav()

    def _target_page(device_id: str) -> str:
        """Where this device lands. ``play`` is explicit; the steps walk from
        wherever the device actually is, so two panels on one Lineup step
        independently rather than being yanked to a shared index."""
        if action == "play":
            return str(body.get("page_id") or "")
        current = nav.current_page(device_id, deck.id) if nav is not None else None
        try:
            index = page_ids.index(current) if current else 0
        except ValueError:
            index = 0
        step = 1 if action == "next" else -1
        return str(page_ids[(index + step) % len(page_ids)])

    if action == "play":
        requested = str(body.get("page_id") or "")
        if requested not in page_ids:
            return _error("invalid_request", "page_id must be a dashboard in the lineup.", 400)

    deck_id = deck.id
    resolved_targets = list(targets)

    def _work() -> JobOutcome:
        active, _quiet = _partition_quiet(resolved_targets, override=override)
        if not active:
            return JobOutcome.quiet(resolved_targets, "all_targets_in_quiet_hours")
        manager = _push_manager()
        if manager is None:
            return JobOutcome.failed("temporarily_unavailable", "The push pipeline is offline.")
        painted: list[str] = []
        event_ids: list[str] = []
        for device_id in active:
            target_page = _target_page(device_id)
            promoter = getattr(manager, "promote_deck_page", None)
            if callable(promoter) and promoter(device_id, target_page):
                painted.append(device_id)
            else:
                result = manager.push(
                    target_page,
                    device_ids={device_id},
                    respect_quiet_hours=False,
                    source="companion",
                    bypass_coalesce=True,
                )
                if result.status not in _PUBLISHED_STATUSES:
                    continue
                painted.append(device_id)
                event_ids.extend(_history_event_ids(result) or ())
            if nav is not None:
                nav.set(device_id, deck_id, target_page)
        if not painted:
            return JobOutcome.failed("render_failed", "No display took the frame.")
        return JobOutcome.published(painted, history_event_ids=event_ids)

    return _reserve_and_run(
        # Its own kind so a client's activity view can identify explicit
        # Lineup control instead of presenting it as an ordinary dashboard
        # push. The History source stays "companion" (#203).
        kind="lineup_action",
        key=key,
        payload=raw,
        target_ids=resolved_targets,
        label=deck.name or deck.id,
        work=_work,
    )


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

    # Optional request headers (#234). The body is already JSON, so this is a
    # decoded object rather than the textarea string the Send form posts; both
    # go through the same validator. Safe at rest: ``_reserve_and_run``
    # fingerprints the payload with SHA-256 and stores only the digest, so a
    # bearer token here never lands on disk.
    headers: dict[str, str] = {}
    if isinstance(body, dict) and body.get("headers") is not None:
        candidate = body.get("headers")
        if not isinstance(candidate, dict):
            return _error("invalid_request", "headers must be a JSON object.", 400)
        try:
            headers = validate_header_map(candidate)
        except HeaderError as err:
            return _error("invalid_request", str(err), 400)

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
            page_bytes = manager.render_webpage_png(
                url,
                viewport_w=viewport_w,
                allow_local=False,
                headers=headers or None,
            )
        except Exception as err:
            # ``err`` can quote the failing request; it never carries the header
            # values, which live only in the closure and the render request.
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
    from app.composer import _preview_timezone_id, page_preview_token, preview_dims

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
            timezone_id=_preview_timezone_id(),
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


# -- gallery -------------------------------------------------------------
#
# Browse, create, upload, Send (#225). Deliberately the whole of the first
# slice: delete, rename and move are the operations where a mobile mis-tap
# is unrecoverable, and they arrive behind their own operator-granted scope
# rather than riding in on the pairing role.
#
# Sending a Gallery photo is not a new action here. A client reads
# ``content_url`` and posts those bytes to ``POST /images`` like any other
# photo, so Send keeps one code path and Gallery never becomes a second
# delivery mechanism with its own quiet-hours and framing behaviour.


class _PublishFailed(RuntimeError):
    """Storage refused an upload after the request validated."""


def _gallery_folder_or_404(folder_id: str) -> tuple[str | None, tuple[Response, int] | None]:
    try:
        folder = companion_gallery.folder_from_id(folder_id)
    except companion_gallery.GalleryUnavailable:
        return None, _error("not_found", "The photo Gallery isn't available.", 404)
    if folder is None:
        return None, _error("not_found", "No Gallery folder with that id.", 404)
    return folder, None


def _gallery_image_or_404(
    image_id: str,
) -> tuple[tuple[str, str] | None, tuple[Response, int] | None]:
    try:
        ref = companion_gallery.image_from_id(image_id)
    except companion_gallery.GalleryUnavailable:
        return None, _error("not_found", "The photo Gallery isn't available.", 404)
    if ref is None:
        return None, _error("not_found", "No Gallery image with that id.", 404)
    return ref, None


@bp.get("/gallery/folders")
@_require_companion("gallery:read")
def gallery_folders() -> Any:
    try:
        return jsonify({"folders": companion_gallery.list_folders()})
    except companion_gallery.GalleryUnavailable:
        return _error("not_found", "The photo Gallery isn't available.", 404)


@bp.post("/gallery/folders")
@_require_companion("gallery:write")
def create_gallery_folder() -> Any:
    """Create one internal folder.

    Only internal: linking an external folder hands the plugin a host
    directory to read from, which is an admin decision made in front of a
    filesystem picker, not something a paired phone should be able to do."""
    body = request.get_json(silent=True)
    raw_name = body.get("name") if isinstance(body, dict) else None
    if not isinstance(raw_name, str) or not (1 <= len(raw_name.strip()) <= 80):
        return _error("invalid_request", "A folder name of 1-80 characters is required.", 400)
    try:
        gallery = companion_gallery.gallery_module()
        folder = gallery.normalize_folder_name(raw_name)
        if folder is None:
            return _error(
                "invalid_request",
                "That name has no letters or digits Tesserae can make a folder from.",
                400,
            )
        if not gallery.create_internal_folder(folder):
            return _error("resource_conflict", "A folder with that name already exists.", 409)
        return jsonify(companion_gallery.folder_detail(folder)), 201
    except companion_gallery.GalleryUnavailable:
        return _error("not_found", "The photo Gallery isn't available.", 404)
    except OSError:
        return _error("temporarily_unavailable", "The folder could not be created.", 503)


@bp.get("/gallery/folders/<folder_id>")
@_require_companion("gallery:read")
def gallery_folder(folder_id: str) -> Any:
    folder, err = _gallery_folder_or_404(folder_id)
    if err is not None:
        return err
    assert folder is not None
    return jsonify(companion_gallery.folder_detail(folder))


@bp.post("/gallery/folders/<folder_id>/images")
@_require_companion("gallery:write")
def upload_gallery_image(folder_id: str) -> Any:
    """Store one photo in a writable folder.

    One image per request, not a batch: per-file progress and retry come
    for free, a photo that fails doesn't complicate the nine that
    succeeded, and there's no partial-batch result object to specify or
    version. The client owns its queue and concurrency; the advertised
    batch size is advice about how many to line up, not a request shape.

    A synchronous storage write, so no Job and no History entry: nothing
    was sent to a display, and an upload that shows up in the activity
    feed as a push is a lie about what happened."""
    key, err = _require_idempotency_key()
    if err is not None:
        return err
    assert key is not None

    folder, err = _gallery_folder_or_404(folder_id)
    if err is not None:
        return err
    assert folder is not None

    gallery = companion_gallery.gallery_module()
    if gallery.folder_is_external(folder):
        return _error(
            "resource_conflict",
            "This folder is linked from elsewhere on the host and is read-only.",
            409,
        )

    upload = request.files.get("image")
    if upload is None:
        return _error("invalid_request", "A multipart 'image' part is required.", 400)
    if (upload.mimetype or "").lower() not in _GALLERY_CONTENT_TYPES:
        return _error("unsupported_image", "Unsupported still-image media type.", 415)
    blob = upload.read()
    if not blob:
        return _error("invalid_request", "The 'image' part is empty.", 400)

    normalized = companion_gallery.normalize_upload(blob)
    if isinstance(normalized, str):
        status = 413 if normalized == "image_too_large" else 415
        message = (
            "The image exceeds the Gallery size limit."
            if status == 413
            else "The image could not be decoded."
        )
        return _error(normalized, message, status)

    filename = companion_gallery.stored_filename(upload.filename, normalized)

    def _publish() -> str:
        if not companion_gallery.publish_image(folder, filename, normalized):
            raise _PublishFailed(filename)
        return companion_gallery.image_id(folder, filename)

    try:
        resolved, _created, conflict = _idempotency().reserve(
            token_id=g.companion.token_id,
            key=key,
            fingerprint=_idempotency_fingerprint(
                "POST", f"/gallery/folders/{folder_id}/images", normalized.data
            ),
            make_job=_publish,
        )
    except _PublishFailed:
        return _error("temporarily_unavailable", "The image could not be stored.", 503)
    if conflict:
        return _error(
            "idempotency_conflict", "That Idempotency-Key was used for a different image.", 409
        )

    view = companion_gallery.image_view(folder, filename)
    if view is None:
        return _error("temporarily_unavailable", "The stored image could not be read back.", 503)
    response = jsonify({"image": view})
    response.status_code = 201
    response.headers["Location"] = view["content_url"]
    response.headers["ETag"] = view["etag"]
    logger.info("companion gallery upload: %s -> %s (%s)", resolved, filename, folder)
    return response


def _gallery_binary(path: Path, mimetype: str, etag: str, *, filename: str | None) -> Any:
    if request.if_none_match and etag.strip('"') in request.if_none_match:
        resp = current_app.response_class(status=304)
    else:
        resp = current_app.response_class(path.read_bytes(), mimetype=mimetype)
        if filename is not None:
            resp.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    resp.set_etag(etag.strip('"'))
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@bp.get("/gallery/images/<image_id>/thumbnail")
@_require_companion("gallery:read")
def gallery_image_thumbnail(image_id: str) -> Any:
    ref, err = _gallery_image_or_404(image_id)
    if err is not None:
        return err
    assert ref is not None
    folder, filename = ref
    gallery = companion_gallery.gallery_module()
    thumb = gallery.thumbnail_path(folder, filename)
    if thumb is None:
        return _error("not_found", "No thumbnail could be made for that image.", 404)
    return _gallery_binary(thumb, "image/jpeg", companion_gallery.etag_for(thumb), filename=None)


@bp.get("/gallery/images/<image_id>/content")
@_require_companion("gallery:read")
def gallery_image_content(image_id: str) -> Any:
    """The image, in a format the contract defines. A client passes these
    bytes into ``POST /images`` to send it, so Gallery adds no second
    delivery path and Send stays independent of whether the target caches
    frames.

    Usually the stored file. For a stored format the contract's media
    types don't cover (BMP, GIF) it's a cached PNG rendition of the same
    pixels; the stored file is never rewritten."""
    ref, err = _gallery_image_or_404(image_id)
    if err is not None:
        return err
    assert ref is not None
    folder, filename = ref
    served = companion_gallery.served_image(folder, filename)
    if served is None:
        return _error("not_found", "No Gallery image with that id.", 404)
    return _gallery_binary(
        served.path,
        served.content_type,
        companion_gallery.etag_for(served.path),
        filename=served.name,
    )


# -- offline albums ------------------------------------------------------
#
# Authoring the Offline Album producer over the Companion API (#230). One
# album per Gallery folder, so the resource is a nested singleton rather
# than a collection: albums are keyed by folder in the store, and a
# top-level route would advertise a many-per-folder world that doesn't
# exist.
#
# The Web form in the Picture Gallery plugin does the same job, and the
# two are held to the same rules deliberately: only ``unsupported``
# targets are refused, an ``unknown`` one stays selectable so a display
# asleep during setup can still be bound, taking a display off another
# album is an explicit act rather than a consequence of saving, and every
# affected display has its warmed frames dropped so the next manifest
# re-renders.


def _albums_or_404() -> tuple[Response, int] | None:
    """Refuse the whole surface when either half is missing."""
    if not companion_albums.albums_available():
        return _error("not_found", "Offline Albums aren't available.", 404)
    return None


def _album_response(album: Any, folder: str, status: int = 200) -> Any:
    """An album plus its version tag. Every response that hands out the
    record hands out the validator needed to edit it safely."""
    resp = jsonify(companion_albums.album_response(album, folder))
    resp.status_code = status
    resp.headers["ETag"] = f'"{companion_albums.album_etag(album)}"'
    return resp


def _album_draft(
    body: Any, folder: str, *, stored_id: str
) -> tuple[Any | None, tuple[Response, int] | None]:
    """Validate an ``OfflineAlbumDraft`` into an unsaved :class:`Album`.

    The folder identity comes from the path, never the body, so a client
    cannot author an album against a folder it didn't address."""
    from app.state.album_model import Album

    if not isinstance(body, dict):
        return None, _error("invalid_request", "A JSON object body is required.", 400)
    order = body.get("order")
    if not isinstance(order, list) or not all(isinstance(v, str) for v in order):
        return None, _error("invalid_request", "order must be a list of image ids.", 400)
    try:
        filenames = companion_albums.order_to_filenames(folder, order)
    except companion_albums.OrderRejected as exc:
        return None, _error("invalid_request", str(exc), 400)
    except companion_gallery.GalleryUnavailable:
        return None, _error("not_found", "The photo Gallery isn't available.", 404)
    device_ids = body.get("device_ids")
    if not isinstance(device_ids, list) or not all(isinstance(v, str) for v in device_ids):
        return None, _error("invalid_request", "device_ids must be a list of display ids.", 400)
    try:
        album = Album.model_validate(
            {
                "id": stored_id,
                "name": body.get("name"),
                "enabled": body.get("enabled"),
                "device_ids": device_ids,
                "source_folder": folder,
                "order": filenames,
                "fit": body.get("fit"),
                "playback": body.get("playback"),
            }
        )
    except ValidationError as exc:
        return None, _error(
            "invalid_request", f"Invalid album: {exc.error_count()} problem(s).", 400
        )
    return album, None


def _album_targets_or_error(album: Any) -> tuple[Response, int] | None:
    """Refuse a write naming a display that can't play an album.

    Unregistered ids are ``invalid_target``; a display whose latest report
    explicitly lacks the frame cache is ``offline_album_unsupported_targets``
    with the ids named, so a form that passed preflight and then went stale
    gets a reason rather than an opaque 400. ``unknown`` is allowed
    through: a sleeping display is not evidence of anything."""
    unknown_ids: list[str] = []
    unsupported: list[str] = []
    for device_id in album.device_ids:
        support = companion_albums.support_for(device_id)
        if support is None:
            unknown_ids.append(device_id)
        elif support.get("state") == "unsupported":
            unsupported.append(device_id)
    if unknown_ids:
        return _error("invalid_target", "One or more target displays are unknown.", 400)
    if unsupported:
        return _error(
            "offline_album_unsupported_targets",
            "One or more displays no longer support Offline Albums.",
            400,
            device_ids=unsupported,
        )
    return None


@bp.get("/gallery/folders/<folder_id>/offline-album")
@_require_companion("gallery:read", "devices:read")
def get_offline_album(folder_id: str) -> Any:
    err = _albums_or_404()
    if err is not None:
        return err
    folder, err = _gallery_folder_or_404(folder_id)
    if err is not None:
        return err
    assert folder is not None
    album = companion_albums.stored_album(folder)
    if album is None:
        return _error("not_found", "This folder has no Offline Album.", 404)
    return _album_response(album, folder)


@bp.put("/gallery/folders/<folder_id>/offline-album")
@_require_companion("offline_albums:write")
def put_offline_album(folder_id: str) -> Any:
    """Create or completely replace one folder's album.

    ``If-Match`` is required to replace an existing album and its absence
    means "create": two surfaces edit these records, and a phone that
    prepared its form before the Web form saved should be refused rather
    than silently overwrite what it never saw."""
    from app.state.album_store import AlbumConflict

    err = _albums_or_404()
    if err is not None:
        return err
    folder, err = _gallery_folder_or_404(folder_id)
    if err is not None:
        return err
    assert folder is not None

    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("album"), dict):
        return _error("invalid_request", "An album object is required.", 400)

    store = current_app.config["ALBUM_STORE"]
    existing = companion_albums.stored_album(folder)
    presented = (request.headers.get("If-Match") or "").strip().strip('"')
    if not presented:
        if existing is not None:
            return _error(
                "precondition_failed",
                "This folder already has an Offline Album; send If-Match to replace it.",
                412,
            )
    elif existing is None:
        return _error("precondition_failed", "This folder's Offline Album no longer exists.", 412)
    elif presented != companion_albums.album_etag(existing):
        return _error("precondition_failed", "The Offline Album changed since it was read.", 412)

    album, err = _album_draft(body["album"], folder, stored_id=companion_albums.stored_id(folder))
    if err is not None:
        return err
    assert album is not None
    err = _album_targets_or_error(album)
    if err is not None:
        return err

    try:
        store.upsert(album, replace=bool(body.get("replace_conflicts")))
    except AlbumConflict as conflict:
        return _error(
            "offline_album_conflict",
            "One or more displays are already playing another Offline Album.",
            409,
            claims=companion_albums.resolve_claims(conflict.claims),
        )

    previous = set(existing.device_ids) if existing is not None else set()
    companion_albums.clear_warmed_frames(previous | set(album.device_ids))
    logger.info(
        "companion offline album saved: %s -> %d display(s)", album.id, len(album.device_ids)
    )
    return _album_response(album, folder, 200 if existing is not None else 201)


@bp.delete("/gallery/folders/<folder_id>/offline-album")
@_require_companion("offline_albums:write")
def delete_offline_album(folder_id: str) -> Any:
    """Remove the producer and its bindings. Source photos are untouched:
    this unbinds a folder from a display, it doesn't delete a library.

    204 whenever the folder ends up without an album, including when it
    never had one, so a retried delete doesn't read as a missing folder."""
    err = _albums_or_404()
    if err is not None:
        return err
    folder, err = _gallery_folder_or_404(folder_id)
    if err is not None:
        return err
    assert folder is not None
    existing = companion_albums.stored_album(folder)
    if existing is None:
        return Response(status=204)
    current_app.config["ALBUM_STORE"].delete(existing.id)
    companion_albums.clear_warmed_frames(set(existing.device_ids))
    return Response(status=204)


@bp.post("/gallery/folders/<folder_id>/offline-album/preflight")
@_require_companion("offline_albums:write")
def preflight_offline_album(folder_id: str) -> Any:
    """Plan a draft against each target without saving or warming.

    Behind the write scope because it exists to serve an authoring form,
    and a saved album's plan is readable through GET anyway."""
    err = _albums_or_404()
    if err is not None:
        return err
    folder, err = _gallery_folder_or_404(folder_id)
    if err is not None:
        return err
    assert folder is not None
    album, err = _album_draft(
        request.get_json(silent=True), folder, stored_id=companion_albums.stored_id(folder)
    )
    if err is not None:
        return err
    assert album is not None
    return jsonify(companion_albums.preflight_response(album, folder))


def register(app: Flask) -> None:
    app.register_blueprint(bp)
