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

import json
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


# -- CORS ----------------------------------------------------------------
#
# The REST API was originally designed for server-to-server flows (the
# Pi clients, the ESP32 firmware) which don't care about CORS. From
# v0.63.11 the same API is callable from a browser (the in-browser
# device emulator at emulator.tesserae.ink, future "Test push" UI on
# the device card, anything else browser-based that needs to pair +
# poll). Browsers refuse cross-origin requests unless the response
# carries the ``Access-Control-Allow-*`` headers below; pre-v0.63.11
# the emulator just got a CORS error and the fetch never reached our
# code.
#
# ``Access-Control-Allow-Origin: *`` is safe here because every
# endpoint already requires a Bearer token. The token is the security
# boundary, not the origin. Browser-based callers that don't have the
# token can't extract anything from a wildcard allow.
#
# Routes outside this blueprint (admin UI, settings, plugins/browse)
# aren't affected — those are same-origin from the admin page and
# don't want random origins poking at them anyway.


@bp.before_request
def _cors_preflight():  # type: ignore[no-untyped-def]
    """Short-circuit OPTIONS preflight with a 204 + the headers the
    ``after_request`` hook will paint on it. Without this, OPTIONS
    requests fall through to the route handlers (which expect GET /
    POST) and return 405 — Chrome / Safari then refuse to fire the
    real request."""
    if request.method == "OPTIONS":
        return Response(status=204)
    return None


@bp.after_request
def _cors_headers(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = (
        # Authorization + Content-Type + If-None-Match are the standard
        # ones every endpoint uses. The two ``X-Tesserae-*`` / ``X-Pairing-*``
        # headers are Tesserae-specific: X-Tesserae-Token is the bearer-
        # token alternative form firmware can use when an Authorization
        # header is awkward, and X-Pairing-Code is how /register receives
        # the 6-digit code (see ``post_register`` below).
        "Authorization, Content-Type, If-None-Match, X-Tesserae-Token, X-Pairing-Code"
    )
    resp.headers["Access-Control-Expose-Headers"] = "ETag"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


# -- registry helpers ----------------------------------------------------


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _renderers() -> RendererRegistry:
    return current_app.config["RENDERER_REGISTRY"]  # type: ignore[no-any-return]


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _resolve_local_time_fields(request_tz: str) -> dict[str, Any]:
    """Build the local-time field set that gets added to the /status
    response so memory-constrained clients (CircuitPython, MicroPython,
    bare-metal MCU firmware) don't have to carry the IANA tzdata or
    a DST rule engine.

    Precedence for the effective timezone:

    1. ``request_tz`` if the device sent one in its heartbeat
    2. ``settings.app.timezone`` set during onboarding
    3. Server's auto-detected TZ (``TZ`` env var or ``/etc/localtime``)
    4. ``UTC`` as the last-resort fallback

    Returns four fields:

    * ``local_time`` (ISO 8601 with offset) for clients without an RTC
      that just want to set / display the moment
    * ``tz`` echoes which IANA name was actually used (so a client can
      detect that its sent tz was invalid and fell back)
    * ``tz_offset_seconds`` + ``dst_active`` for clients WITH an RTC,
      to derive local time on intermediate wakes without round-trips

    See ``docs/dev/client-protocol.md`` for the client-side contract.
    """
    import zoneinfo
    from datetime import datetime, timedelta

    from app.tz_resolve import _resolve_iana_timezone

    settings_app = _settings().get_section("app") or {}
    server_app_tz = str(settings_app.get("timezone", ""))

    resolved = _resolve_iana_timezone(request_tz or server_app_tz or "system") or "UTC"
    try:
        zone = zoneinfo.ZoneInfo(resolved)
    except zoneinfo.ZoneInfoNotFoundError:
        # Belt and braces. ``_resolve_iana_timezone`` already validates
        # against ``available_timezones()`` so this should only fire if
        # tzdata is being live-refreshed under us.
        resolved = "UTC"
        zone = zoneinfo.ZoneInfo("UTC")

    now = datetime.now(tz=zone)
    offset = now.utcoffset() or timedelta(0)
    return {
        "local_time": now.isoformat(timespec="seconds"),
        "tz": resolved,
        "tz_offset_seconds": int(offset.total_seconds()),
        "dst_active": bool(now.dst()),
    }


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
    "token-must-match-the-id-in-the-URL" check.

    Three distinct failure shapes:

    * **401**, missing or unrecognised token. Firmware bug or a
      client that forgot to include the bearer header.
    * **404**, the URL device id doesn't map to any registered
      instance. The client probably has a stale / mis-typed id in
      its config (the bug that landed this branch: a firmware
      forgot to substitute ``device_id`` into its URL template and
      sent a literal ``{id}`` at us). Device ids are admin-chosen,
      not attacker-controlled, so returning a distinct 404 here
      leaks nothing meaningful and saves the caller five minutes of
      "is it me or the server" debugging.
    * **403**, the URL device id DOES exist but the caller's token
      belongs to a different device. This is the only genuine-auth
      failure of the three; keep the vague message so a rogue
      client can't map "which id owns this token"."""
    token = _request_token()
    if not token:
        return None, _error(401, "missing bearer token")
    device = _device_by_token(token)
    if device is None:
        return None, _error(401, "invalid bearer token")
    if device.id != device_id:
        # Distinguish "id doesn't exist" (404) from "id exists but
        # token belongs to a different device" (403). Registry lookup
        # is O(1) on the dict; keep the constant-time token-compare
        # dance above so timing side-channels stay closed on the auth
        # side.
        target = _devices().get(device_id)
        if target is None or target.kind_of is None:
            return None, _error(404, f"no device with id {device_id!r}")
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
    # Merge the renderer's MQTT-frozen payload (rotate / scale / bg /
    # saturation for pi_png, palette_signature for the .bin renderers,
    # etc.) so REST clients get the same fields their MQTT-subscribed
    # cousins have always received. Without this, the pi_png reference
    # client logs ``download/paint failed: payload missing 'rotate'``
    # because the response only carried the REST-specific shape.
    renderer = _renderers().get(str(latest.get("renderer_id", "")))
    if renderer is not None:
        try:
            settings = _settings().get_for_runtime(
                "renderers", renderer.id, renderer.manifest.get("settings", [])
            )
            base_url = request.url_root.rstrip("/")
            extra = renderer.payload(latest["digest"], base_url, settings=settings)
            # REST-shape keys win (the renderer.payload() ``url`` matches
            # ``image_url`` anyway since base_url is the same).
            for k, v in extra.items():
                payload.setdefault(k, v)
        except Exception:
            current_app.logger.exception(
                "rest /frame: renderer.payload() failed for renderer %s",
                renderer.id,
            )
    resp = jsonify(payload)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# -- status --------------------------------------------------------------


@bp.post("/<device_id>/status")
def post_status(device_id: str) -> Response:
    """Device heartbeat. Body JSON parsed through the device kind's
    ``parse_status`` (same hook MQTT uses) and fed through the shared
    ``record_status_heartbeat`` so the live status cache, smart-sync
    telemetry, battery history, event log, and HA discovery all get
    the same updates the MQTT path produces. Response carries the
    current per-device config + next poll cadence."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    # Shared with the MQTT subscriber so the side effects don't drift.
    # Pre-v0.53.1 this path wrote a flat dict with a ``last_seen``
    # field, which the Devices UI's ``_status_view`` doesn't read
    # (it looks for ``received_at`` + ``parsed``), so REST device
    # "last seen" was stuck at epoch 0. Telemetry + battery history
    # were also skipped.
    from app.transport_wiring import record_status_heartbeat

    events = _events()
    if events is not None:
        record_status_heartbeat(
            app=current_app._get_current_object(),  # type: ignore[attr-defined]
            device=device,
            payload=request.get_data() or b"",
            status_cache=_device_status_cache(),
            event_log=events,
            # MQTT path uses the status_topic here; REST devices
            # generally still have one on the manifest (kinds derive
            # it), but for events the conventional shape is "rest://
            # <device_id>/status" so an Events page row can be told
            # apart from the MQTT version at a glance.
            event_target=f"rest://{device.id}/status",
        )

    # Optional ``tz`` field in the heartbeat body lets a client tell
    # the server its IANA timezone so the response can carry resolved
    # local-time fields back. Memory-constrained CircuitPython /
    # MicroPython clients use this in place of carrying the IANA db
    # locally; the response shape is documented in client-protocol.md.
    # ``force=True`` accepts any content-type that's still valid JSON
    # so a client that sends ``text/plain; charset=utf-8`` (some HTTP
    # libs default to that) still gets parsed.
    body = request.get_json(silent=True, force=True) or {}
    request_tz = ""
    if isinstance(body, dict):
        raw_tz = body.get("tz", "")
        if isinstance(raw_tz, str):
            request_tz = raw_tz.strip()

    response = {
        "status": 200,
        "config": _current_config(device),
        "next_poll_s": _next_poll_s(device),
        "server_time": time.time(),
        **_resolve_local_time_fields(request_tz),
    }
    return jsonify(response)


# -- log -----------------------------------------------------------------


# Max stored msg length. Tracebacks easily exceed the pre-v0.64.20
# 512-byte cap (a typical MicroPython traceback is 1-3 KB); 4 KB
# covers them without giving a noisy client room to flood the
# EventLog one entry at a time.
_LOG_MSG_CAP = 4096


def _coerce_log_msg(raw: Any) -> str:
    """Accept either a string or a list of strings (typically a
    pre-split traceback from ``traceback.format_exception()``).

    Memory-constrained MicroPython / CircuitPython clients can pass
    the traceback list directly rather than allocating a single
    joined string on-device, which is most useful exactly when the
    device is mid-exception and the heap is tightest. Joined
    server-side with newlines so the EventLog still holds one string
    per row.

    Backwards-compatible with the pre-v0.64.20 ``str``-only shape:
    a string passes through ``str()`` unchanged. The 512-byte cap
    was raised to 4 KB at the same time, see ``_LOG_MSG_CAP``."""
    if isinstance(raw, list):
        # ``rstrip("\n")`` per line covers both shapes the wild
        # produces. ``traceback.format_exception()`` already
        # terminates each line with ``\n``, so a naive
        # ``"\n".join(...)`` would emit double newlines and look
        # like blank lines between every traceback row in the
        # Events page (flagged by bablokb in
        # https://github.com/dmellok/tesserae/issues/26 discussion).
        # Hand-crafted lists without trailing newlines see no
        # change (rstrip is a no-op then).
        joined = "\n".join(str(line).rstrip("\n") for line in raw if line is not None)
        return joined[:_LOG_MSG_CAP]
    if raw is None:
        return ""
    return str(raw)[:_LOG_MSG_CAP]


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
        msg = _coerce_log_msg(body.get("msg"))
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


def _client_id_for_rate_limit() -> str:
    """The key the rate limiter buckets attempts by. Uses
    ``X-Forwarded-For`` when a reverse proxy is in front (Nginx Proxy
    Manager, Caddy, Cloudflare Tunnel) and falls back to the direct
    peer address. Trailing whitespace stripped; first value only when
    multiple are present (we trust only the closest proxy)."""
    forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.remote_addr or "unknown"


def _claim_device_by_mac(mac: str) -> Device | None:
    """Look for a registered device instance whose stored MAC matches
    the given MAC. Used by the discover-then-claim flow: when admin
    clicks Register on a discovered device, the resulting instance
    carries the discovered MAC; subsequent ``/discover`` POSTs from
    that firmware match the MAC and receive the device's access token
    without needing a pairing code.

    Case-insensitive compare; spec-MAC formats (with or without colons,
    upper or lower case) normalise to lower-no-separators."""
    target = mac.strip().lower().replace(":", "").replace("-", "")
    if not target:
        return None
    for dev in _devices().all():
        if dev.kind_of is None:
            continue
        stored = dev.manifest.get("mac")
        if not isinstance(stored, str) or not stored:
            continue
        normalised = stored.strip().lower().replace(":", "").replace("-", "")
        if normalised == target:
            return dev
    return None


@bp.post("/discover")
def post_discover() -> Response:
    """Unauthenticated discovery + auto-claim.

    Two paths depending on whether the admin has already registered a
    device with the firmware's MAC:

    * **Already registered (MAC matches a stored instance)**: return
      the existing device token + config. Firmware persists the token
      and proceeds to the normal wake loop. No pairing code typing
      needed.
    * **Not yet registered**: cache the announce in the Discovered
      strip on Settings -> Devices so the admin can one-click Register.
      Response carries ``registered: false`` + a ``retry_after_s`` hint
      telling the firmware when to poll again. Firmware sleeps,
      reconnects, retries discover. On the wake cycle after admin
      clicks Register, the MAC match path fires and the firmware
      receives its token.

    Body shape:
        {"device_id": "...", "kind": "...", "panel_w": int,
         "panel_h": int, "fw_version": "...", "mac": "..." }

    Rate-limited per client IP using the same limiter as ``/register``.
    Successful claims do NOT release the bucket (a misconfigured
    firmware spamming announces would otherwise pummel the server)."""
    limiter = current_app.config.get("REGISTER_RATE_LIMITER")
    client_id = _client_id_for_rate_limit()
    if limiter is not None:
        decision = limiter.check_and_consume(client_id)
        if not decision.allowed:
            resp = _error(429, "too many discovery attempts; slow down")
            resp.headers["Retry-After"] = str(decision.retry_after_s)
            return resp

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    device_id = str(body.get("device_id") or "").strip().lower()
    if not device_id:
        return _error(400, "device_id is required")
    mac = str(body.get("mac") or "").strip()

    # MAC-match claim: if admin already registered a device whose
    # manifest carries this MAC, hand back the token + config so the
    # firmware can stop polling discover and start its real wake loop.
    if mac:
        claimed = _claim_device_by_mac(mac)
        if claimed is not None:
            token = claimed.manifest.get("access_token")
            if isinstance(token, str) and token:
                return jsonify(
                    {
                        "status": 200,
                        "registered": True,
                        "device_token": token,
                        "device_id": claimed.id,
                        "config": _current_config(claimed),
                        "server_time": time.time(),
                    }
                )

    # Not yet registered: add to the Discovered strip so admin can
    # one-click register. The transport hint lives in the body so the
    # admin UI knows to create a REST instance (not an MQTT one) when
    # the admin clicks Register on this entry.
    body_with_hint = dict(body)
    body_with_hint["transport"] = "rest"
    discovery_cache = current_app.config.get("DISCOVERY_CACHE")
    if discovery_cache is None:
        return _error(503, "discovery cache not configured")

    payload = json.dumps(body_with_hint).encode("utf-8")
    entry = discovery_cache.record(device_id, payload)
    if entry is None:
        return _error(400, "device_id rejected (malformed or empty payload)")
    return jsonify(
        {
            "status": 200,
            "registered": False,
            "discovered": True,
            "retry_after_s": 30,
            "next_step": "Open Settings -> Devices and click Register on this device's card. The firmware should retry discover; the next POST will return device_token.",
        }
    )


@bp.post("/register")
def post_register() -> Response:
    """First-boot device pairing.

    The firmware POSTs with an ``X-Pairing-Code`` header (the 6-digit
    code the user generated in the admin UI) plus a JSON body
    declaring its chosen id + kind + panel dims + firmware version.
    On success the server creates the device instance, mints a per-
    device ``access_token``, and returns it. The pairing code is
    burned (single-use).

    Rate-limited per client IP: 6-digit codes have only 20 bits of
    entropy, so an unrate-limited brute force on the LAN could try
    millions of codes per minute and crack a random code in seconds.
    The limiter caps FAILED attempts (successful registrations
    release the bucket since they burned a code; the attacker
    can't grind a single IP without being noticed)."""
    limiter = current_app.config.get("REGISTER_RATE_LIMITER")
    client_id = _client_id_for_rate_limit()
    if limiter is not None:
        decision = limiter.check_and_consume(client_id)
        if not decision.allowed:
            resp = _error(429, "too many failed pairing attempts; slow down")
            resp.headers["Retry-After"] = str(decision.retry_after_s)
            return resp

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
        if limiter is not None:
            limiter.record_success(client_id)
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

    if limiter is not None:
        limiter.record_success(client_id)
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

    # JSON envelope for 4xx / 5xx errors on /api/v1/device paths.
    # Blueprint-level errorhandlers only fire for errors raised INSIDE a
    # matched route; a 404 from "no route matched at all" hits Flask's
    # app-level handler instead. Firmware clients don't render HTML, so
    # a stray 404 (typo in URL, POST to /status without the id, etc.)
    # landing on Flask's default HTML 404 leaves them staring at a
    # bytes-of-markup blob. Register app-level handlers that check the
    # request path so we only re-shape errors under our own prefix and
    # don't accidentally JSON-ify the admin UI's 404s.
    from werkzeug.exceptions import HTTPException

    api_prefix = "/api/v1/device"

    def _json_if_api(err: HTTPException) -> Response:
        if not request.path.startswith(api_prefix):
            # Let Werkzeug's default HTML response fall through for the
            # admin UI. Re-raising the exception would land on the WSGI
            # layer as an unhandled 500; ``err.get_response()`` returns
            # the pre-rendered default HTML body Flask would have sent
            # if we hadn't registered a handler here.
            return err.get_response()
        status = err.code or 500
        message = err.description or err.name or "error"
        return _error(status, str(message))

    for status_code in (404, 405, 500):
        app.register_error_handler(status_code, _json_if_api)
