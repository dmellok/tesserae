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
(OpenAPI 0.4.0). The vendored copy + fixtures under ``tests/companion/``
guard these shapes.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import json as _json
import logging
import re
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, Flask, current_app, g, jsonify, request
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
from app.panel import device_panel, resolve_settings_panel
from app.quiet_hours import device_is_quiet
from app.state.companion_token_store import COMPANION_SCOPES, CompanionTokenStore
from app.state.event_log import EventLog, EventRow
from app.state.idempotency_store import IdempotencyStore
from app.state.idempotency_store import fingerprint as _idempotency_fingerprint
from app.state.job_store import JobKind, JobStore
from app.state.page_store import PageStore
from app.state.pairing_store import PairingStore
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
JOB_RETENTION_SECONDS = 86_400
IDEMPOTENCY_RETENTION_SECONDS = 86_400

# Features this server serves. The client gates on this list rather than
# assuming the full set, so unshipped surfaces degrade cleanly.
FEATURES = ("devices", "dashboards", "dashboard_push", "image_push", "jobs", "history")

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
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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
        feats.append("previews")
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
    event_id = getattr(result, "event_id", None)
    if isinstance(event_id, int) and event_id > 0:
        return [str(event_id)]
    return None


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
    """Latest logical full-frame PNG for a display.

    The preview includes the selected image-fit mode and logical panel
    geometry, but excludes hardware-only row-stride and mount compensation.
    A live partial-refresh patch may be newer than this last full frame.
    """
    device = _devices().get(device_id)
    if device is None or device.kind_of is None:
        return _error("not_found", "No display with that id.", 404)
    manager = _push_manager()
    latest = manager.latest_render_for(device_id) if manager is not None else None
    if not isinstance(latest, dict):
        return _error("not_found", "No frame has been rendered for this display yet.", 404)
    digest = latest.get("preview_digest")
    if digest is None:
        return _error("not_found", "No preview is available for the latest frame yet.", 404)

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
