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

Two routes sit outside that mapping, for clients that can't walk the
frame envelope's JSON-then-fetch hop:

* ``GET /api/v1/device/<id>/frame.bmp``   <- the image bytes themselves
* ``GET /api/v1/device/<id>/frames.json`` <- a small on-device picker

See the "sleep screen" section below for what constrains them.

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
config_schema), or ``awake_poll_s`` on a device set to stay awake, or a
reasonable per-transport default.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from flask import Blueprint, Flask, current_app, jsonify, request
from werkzeug.wrappers import Response

from app.button_service import ButtonService, TouchStroke
from app.device_loader import Device, DeviceRegistry
from app.device_service import (
    AWAKE_POLL_MIN_S,
    awake_poll_interval_s,
    create_instance,
    generate_access_token,
    panel_geometry_from_report,
    parse_rotation,
    usable_mac,
)
from app.panel import device_panel
from app.render_signing import sign_render_query
from app.renderer_loader import RendererRegistry
from app.state.event_log import EventLog
from app.state.pairing_store import PairingStore
from app.state.settings_store import SettingsStore
from app.touch_spec import PRIMITIVE_KINDS

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
    resp.headers["Access-Control-Expose-Headers"] = "ETag, Content-Location"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp


# -- registry helpers ----------------------------------------------------


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _renderers() -> RendererRegistry:
    return current_app.config["RENDERER_REGISTRY"]  # type: ignore[no-any-return]


def _settings() -> SettingsStore:
    return current_app.config["SETTINGS_STORE"]  # type: ignore[no-any-return]


def _deleted_device_markers() -> Any:
    """Lazily-built marker store for v0.69.2 (issue #48) MAC-differs
    auto-wipe. One instance per request is cheap; the store itself is
    just a wrapper around a small JSON file."""
    from app.state.deleted_device_markers import DeletedDeviceMarkers

    return DeletedDeviceMarkers(_data_root())


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


def _device_data_root() -> Path:
    """Where instance manifests live (``data/devices/``). The loader scans
    this directory, so ``create_instance`` / ``update_instance_renderer``
    have to write here, not into the parent ``DATA_ROOT``. Writing to the
    parent orphans the file: it never loads on restart and blocks re-pair
    with an "instance file already exists" 400 (issue #127)."""
    return current_app.config["DEVICE_DATA_ROOT"]  # type: ignore[no-any-return]


# -- auth ----------------------------------------------------------------


# Query-parameter name for the device token on the routes that opt into
# it. Deliberately short: it is embedded in a URL that has to survive
# being typed, QR-scanned, or stored in a tiny on-device list.
TOKEN_QUERY_PARAM = "k"


def _request_token(*, allow_query: bool = False) -> str:
    """Pull the bearer token out of the request. Two header forms are
    accepted: ``Authorization: Bearer <token>`` (the canonical REST
    form) and ``X-Tesserae-Token: <token>`` (a fallback for firmware
    libs whose HTTP client makes Authorization headers awkward, e.g.
    very small embedded HTTP stacks).

    ``allow_query`` additionally accepts ``?k=<token>``, and is opt-in per
    route rather than global. Some clients fetch a URL from a declarative
    handler that owns the request and cannot be given headers at all: the
    URL is the only channel. That convenience costs real secrecy (a query
    string lands in access logs, proxy logs, and browser history in a way
    a header does not), so it stays confined to the routes that genuinely
    have no alternative. Headers still win when both are present.
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = request.headers.get("X-Tesserae-Token", "").strip()
    if header or not allow_query:
        return header
    return request.args.get(TOKEN_QUERY_PARAM, "").strip()


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


def _auth_device(
    device_id: str, *, allow_query_token: bool = False
) -> tuple[Device | None, Response | None]:
    """Resolve + authorise a request against a URL-path device id.

    Returns ``(device, None)`` on success, ``(None, response)`` to be
    returned directly when auth fails. Centralises the
    "token-must-match-the-id-in-the-URL" check. ``allow_query_token``
    forwards to :func:`_request_token`; see there for why it is per-route.

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
    token = _request_token(allow_query=allow_query_token)
    if not token:
        return None, _error(401, "missing bearer token")
    device = _device_by_token(token)
    if device is None:
        return None, _error(401, "invalid bearer token")
    # Ids are stored lowercased at registration, so compare + look up
    # against the canonical form. Without this a device that kept a
    # mixed-case id pairs fine but 404s on every frame fetch (#128).
    canonical = _canonical_id(device_id)
    if device.id != canonical:
        # Distinguish "id doesn't exist" (404) from "id exists but
        # token belongs to a different device" (403). Registry lookup
        # is O(1) on the dict; keep the constant-time token-compare
        # dance above so timing side-channels stay closed on the auth
        # side.
        target = _devices().get(canonical)
        if target is None or target.kind_of is None:
            return None, _error(404, f"no device with id {device_id!r}")
        return None, _error(403, "token not valid for this device")
    return device, None


def _canonical_id(device_id: Any) -> str:
    """Normalise a device id the way ``create_instance`` stores it
    (``strip().lower()``). Registration lowercases ids on write, so
    every lookup has to normalise the same way or a device that keeps a
    mixed-case id 404s on every request (issue #128)."""
    return str(device_id or "").strip().lower()


def _error(status: int, message: str, **extra: Any) -> Response:
    """JSON error envelope. Status code is also set on the response so
    firmware can branch on either."""
    body: dict[str, Any] = {"status": status, "error": message}
    body.update(extra)
    resp = jsonify(body)
    resp.status_code = status
    return resp


# -- frame ---------------------------------------------------------------


def _button_service() -> ButtonService | None:
    """Return the app-wide ButtonService or None if buttons aren't
    wired (early boot, some test paths). Callers no-op on None."""
    svc = current_app.config.get("BUTTON_SERVICE")
    if isinstance(svc, ButtonService):
        return svc
    return None


def _parse_button_event_id(raw: Any) -> int | None:
    """Firmware sends button_event_id as a uint. Accept ints and
    numeric strings so a client can't tank the parse by using JSON
    numbers vs strings; anything unparseable returns None (falls
    through to the same-button-within-N dedup fallback)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        try:
            v = int(raw.strip())
        except ValueError:
            return None
        return v if v >= 0 else None
    return None


def _wants_first_byte(range_header: str | None) -> bool:
    """Whether this is the single-byte reachability probe ``bytes=0-0``.

    Deliberately narrow. General range support on a frame nobody caches would
    be machinery for one caller; this recognises the one form a client
    actually sends and lets every other range fall through to the full body,
    which is a valid answer to any range request a client can make."""
    if not range_header:
        return False
    return range_header.replace(" ", "").lower() == "bytes=0-0"


def _dispatch_query_button(device: Device, *, route: str) -> Any | None:
    """Dispatch a ``?button=`` wake through the button service, or None when
    there is no button on this request.

    Shared by ``/frame`` and ``/frame.bmp`` so the two cannot drift. Both
    dispatch BEFORE selecting the artefact: a rotate or refresh action
    publishes synchronously, and the point is that the response carries the
    frame the button just produced rather than the previous one. Without it a
    route that only serves the newest stored render returns the same bytes
    for ever.

    Errors are swallowed by design. The contract is best-effort action plus
    always-serve-a-frame, so a wake still returns a valid response when the
    action path fails.
    """
    svc = _button_service()
    button_raw = request.args.get("button", "").strip()
    if svc is None or not button_raw:
        return None
    try:
        # ``button_event_id`` is the canonical client-protocol parameter.
        # Firmware through v1.5.0 sent the same monotonic counter as ``event``
        # on /frame, so that stays a compatibility alias; a present canonical
        # parameter always wins, even when malformed.
        raw_event_id = request.args.get("button_event_id")
        if raw_event_id is None:
            raw_event_id = request.args.get("event")
        return svc.handle_button(
            device_id=device.id,
            button=button_raw,
            event_id=_parse_button_event_id(raw_event_id),
        )
    except Exception:
        current_app.logger.exception(
            "rest %s: button dispatch failed for device=%s button=%s",
            route,
            device.id,
            button_raw,
        )
        return None


def _parse_coord(raw: Any) -> int | None:
    """A touch coordinate: int or numeric string, >= 0."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        try:
            v = int(raw.strip())
        except ValueError:
            return None
        return v if v >= 0 else None
    return None


def _normalize_digest(raw: str) -> str:
    """The firmware holds the frame digest as its ETag; be lenient
    about clients echoing the header verbatim (``\"abc\"``) vs the bare
    digest (``abc``)."""
    return raw.strip().strip('"')


def _parse_touch_query() -> TouchStroke | None:
    """Touch stroke from ``/frame`` query params (issue #49):
    ``touch_x0``/``touch_y0`` (required), ``touch_x1``/``touch_y1``
    (optional, default to the start point so tap-only clients send two
    params), ``touch_ms`` (optional duration). Returns None when the
    wake carries no touch, or the coordinates don't parse."""
    x0 = _parse_coord(request.args.get("touch_x0"))
    y0 = _parse_coord(request.args.get("touch_y0"))
    if x0 is None or y0 is None:
        return None
    x1 = _parse_coord(request.args.get("touch_x1"))
    y1 = _parse_coord(request.args.get("touch_y1"))
    ms = _parse_button_event_id(request.args.get("touch_ms"))
    return TouchStroke(
        x0=x0,
        y0=y0,
        x1=x1 if x1 is not None else x0,
        y1=y1 if y1 is not None else y0,
        duration_ms=ms,
    )


def _device_awake_poll_s(device: Device) -> int | None:
    """This device's always-on poll cadence, or None when it deep-sleeps.

    Read live from settings on every heartbeat, so changing the cadence in
    Settings takes effect on the device's next poll with no reboot."""
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    return awake_poll_interval_s(stored)


def _configured_poll_s(device: Device) -> int:
    """The device's configured wake interval. Reads the stored
    ``sleep_interval_s`` from settings when present (matches the
    MQTT-side config_topic value), then the schema default, then a
    transport-wide 60 s fallback for kinds that don't carry a sleep
    interval.

    A device set to stay awake answers from ``awake_poll_s`` instead: it
    isn't on the sleep grid, so its sleep interval says nothing about
    when it comes back.

    A panel's ``refresh_floor_s`` deliberately does NOT clamp this. That
    field is how fast the *glass* can be repainted; this is how often the
    device *asks*. A poll is a conditional GET that answers 304 whenever
    the frame is unchanged, and 304 never reaches the panel. Clamping the
    ask to the repaint limit made an E1003 (floor 60) poll once a minute
    however low its awake cadence was set, which is the whole always-on
    feature defeated by a field about something else. The repaint limit
    belongs on the path that decides to send a new frame.
    """
    awake = _device_awake_poll_s(device)
    if awake is not None:
        return awake
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    if isinstance(stored, dict) and isinstance(stored.get("sleep_interval_s"), int):
        return int(stored["sleep_interval_s"])
    schema = device.config_schema or {}
    spec = schema.get("sleep_interval_s") if isinstance(schema, dict) else None
    if isinstance(spec, dict) and isinstance(spec.get("default"), int):
        return int(spec["default"])
    return 60


# Seconds to add to a projected content change before telling the client to
# poll. The scheduler fires at the projected instant and the render itself is
# a browser compose plus a quantize, so a client polling at exactly that
# moment races the render and collects the *previous* frame. A margin costs
# nothing (the device is asleep for it) and turns a guaranteed miss into a
# hit.
_CONTENT_POLL_MARGIN_S: int = 20

# Never ask a client to poll faster than this, however close the next change
# is. Itself clamped by the configured interval below, so a deliberately
# hot-polling panel (sleep_interval_s < this) isn't slowed down.
_MIN_CONTENT_POLL_S: int = 30

# Certainties worth waking for. ``estimated`` events are the engine's own
# guess at an unanchored cadence, so waking early for one trades a real wake
# for a maybe; the configured interval is the better answer there.
_WAKE_WORTHY_CERTAINTIES = frozenset({"scheduled", "conditional"})


def _projected_poll_s(device: Device, configured_s: int) -> int | None:
    """Seconds until the device's next *projected content change*, plus the
    render margin, or ``None`` when there's nothing to project.

    ``next_poll_s`` is documented to the client as "new content is probably
    available at" (discussion #190), but historically it only ever echoed the
    configured interval, which knows nothing about when the dashboard
    actually changes. The projection engine already answers that question for
    the Companion API and the scheduler; this reads the same answer onto the
    device REST path.

    Only ever returns something *sooner* than ``configured_s``; the caller
    keeps that as the ceiling. Manual Send, webhooks, Home Assistant events
    and data-change refreshes have no schedule to project, so a device that
    slept past its configured interval would go blind to all of them.
    """
    scheduler = current_app.config.get("SCHEDULER")
    if scheduler is None:
        return None

    from datetime import UTC, datetime

    from app.device_upcoming import MAX_HOURS
    from app.quiet_hours import resolve_quiet_hours

    now = datetime.now(UTC)
    # Anything past the configured interval gets capped to it anyway, so
    # there's no point walking the record set further than that.
    hours = max(1, min(MAX_HOURS, -(-configured_s // 3600)))
    quiet_window = resolve_quiet_hours(_settings().get_section("app") or {}, device)
    events = scheduler.upcoming_for_device(
        device.id,
        now=now,
        hours=hours,
        limit=4,
        quiet_window=quiet_window,
    )
    for event in events:
        if event.certainty not in _WAKE_WORTHY_CERTAINTIES:
            continue
        delta = (event.scheduled_at - now).total_seconds()
        if delta < 0:
            continue
        return int(delta) + _CONTENT_POLL_MARGIN_S
    return None


def _widget_change_poll_s(device: Device) -> int | None:
    """Seconds until a widget on this device said its own output goes
    stale, plus the render margin (#243).

    Schedules and rotation steps are what ``project_upcoming`` can see. A
    meeting ending, a bin going out, a countdown hitting zero are none of
    those: only the widget knows, and only once it has fetched. The
    composer records the soonest hint per device on every render.
    """
    from app import widget_next_change

    app = current_app._get_current_object()  # type: ignore[attr-defined]
    ts = widget_next_change.peek(app, device.id)
    if ts is None:
        return None
    delta = ts - time.time()
    if delta < 0:
        return None
    return int(delta) + _CONTENT_POLL_MARGIN_S


def _refresh_if_widget_change_elapsed(device: Device, push_mgr: Any) -> None:
    """Re-render before serving when a widget's declared change has passed
    and the frame on the panel predates it (#243).

    Waking the device at the right moment achieves nothing on its own:
    ``/frame`` hands back the last rendered artefact, so a panel told to
    come back at 15:00:20 would collect the frame composed at 14:45 and
    keep showing the meeting that just ended.

    Bounded by construction. It fires only while a recorded change sits in
    the past *and* the artefact is older than it, and the hint is dropped
    on the way out, so a device cannot loop on this: at most one
    synchronous render per declared change.
    """
    if push_mgr is None:
        return
    from app import widget_next_change

    app = current_app._get_current_object()  # type: ignore[attr-defined]
    ts = widget_next_change.peek(app, device.id)
    if ts is None or ts > time.time():
        return
    try:
        latest = push_mgr.latest_render_for(device.id)
        rendered_at = (latest or {}).get("timestamp")
        # No timestamp means we cannot prove the frame is stale, and
        # re-rendering on every poll would be worse than one stale wake.
        if not isinstance(rendered_at, (int, float)) or rendered_at >= ts:
            widget_next_change.clear(app, device.id)
            return
        page_id = (latest or {}).get("page_id")
        widget_next_change.clear(app, device.id)
        if not page_id:
            return
        push_mgr.push(str(page_id), device_ids={device.id}, source="widget_change")
    except Exception:
        logger.exception("rest /frame: widget-change refresh failed for device=%s", device.id)
        widget_next_change.clear(app, device.id)


def _next_poll_s(device: Device) -> int:
    """How many seconds until the firmware should poll again.

    The configured wake interval is the ceiling: it's the staleness the
    operator signed up for, and it's the only thing covering the update
    causes that can't be projected. Within that, the soonest known change
    pulls the wake earlier so the client lands on the new frame instead of
    on an arbitrary point of a fixed grid.

    Two sources feed it: the scheduler's projection of schedules and
    rotation steps, and a widget's own declaration of when its data turns
    over (#243). Either can be absent; a fault in one must not lose the
    other, so they're gathered independently.
    """
    configured = _configured_poll_s(device)
    candidates: list[int] = []
    try:
        projected = _projected_poll_s(device, configured)
    except Exception:
        logger.exception("rest: next_poll_s projection failed for device=%s", device.id)
        projected = None
    if projected is not None:
        candidates.append(projected)
    try:
        declared = _widget_change_poll_s(device)
    except Exception:
        logger.exception("rest: next_poll_s widget hint failed for device=%s", device.id)
        declared = None
    if declared is not None:
        candidates.append(declared)
    if not candidates:
        return configured
    # Ceiling: the configured interval. Floor: _MIN_CONTENT_POLL_S, itself
    # capped by the configured interval so a hot-polling panel keeps its
    # cadence.
    #
    # An always-on panel floors at the awake minimum instead. The 30 s floor
    # exists to stop a sleeping device spinning its radio up for a change it
    # could have waited for; a device that never sleeps is already associated,
    # so the cost of an early poll is one conditional GET. Holding it at 30 s
    # would also throw away the pull-forward it is here to deliver: told a
    # change lands in 8 s, an awake panel should ask in 8 s.
    floor = AWAKE_POLL_MIN_S if _device_awake_poll_s(device) is not None else _MIN_CONTENT_POLL_S
    return max(min(min(candidates), configured), min(configured, floor))


def _button_wake_s(device: Device) -> int:
    """Seconds the firmware should stay awake after a button wake to catch
    rapid repeat presses (#123): the configured per-device value, then the
    schema default, then 0 (deep-sleep immediately). Delivered on the
    ``/frame`` response a button wake already fetches, so the firmware
    decides how long to linger without a cached config read."""
    section = _settings().get_section("devices") or {}
    stored = section.get(device.id) if isinstance(section, dict) else None
    if isinstance(stored, dict) and isinstance(stored.get("button_wake_s"), int):
        return int(stored["button_wake_s"])
    schema = device.config_schema or {}
    spec = schema.get("button_wake_s") if isinstance(schema, dict) else None
    if isinstance(spec, dict) and isinstance(spec.get("default"), int):
        return int(spec["default"])
    return 0


def _maybe_switch_wire_format(device_id: str, body: dict[str, Any]) -> None:
    """Apply a client-declared wire-format switch on an already-registered
    device, if it declared one that resolves to a different renderer.

    Lets a device move between ``png`` and ``bmp`` after registration by
    just re-declaring ``format`` on its next ``/register`` or ``/discover``
    (a memory-constrained CircuitPython client dropping PNG's zlib inflate
    for the uncompressed BMP path), no delete + re-create. When the
    renderer actually moves, the device's cached render is invalidated so
    ``/frame`` reports 204 until the next push repaints it in the new
    format, rather than serving the stale old-format frame. Best-effort:
    any failure is logged and swallowed so it never breaks the poll."""
    wire_format = body.get("format")
    if not wire_format:
        return
    try:
        from app.device_service import update_instance_renderer

        _result, changed = update_instance_renderer(
            devices=_devices(),
            renderers=_renderers(),
            data_root=_device_data_root(),
            instance_id=device_id,
            wire_format=str(wire_format),
        )
        if changed:
            push_mgr = current_app.config.get("PUSH_MANAGER")
            if push_mgr is not None:
                push_mgr.invalidate_latest_render(device_id)
    except Exception:
        current_app.logger.exception("rest: wire-format switch failed for device=%s", device_id)


def _maybe_heal_kind(device_id: str, body: dict[str, Any]) -> None:
    """Move an already-registered device to the kind its firmware now
    declares, when that differs from the stored one.

    Heals the stale-kind case: a device that first paired under a generic
    protocol kind (``esp32_client``) comes back running a board build that
    declares its hardware-catalog SKU (``seeed_reterminal_e1004``).
    Without this, the idempotent re-register path pins the instance to
    the old kind forever, which silently exempts the device from per-kind
    OTA rollouts (releases are keyed by the SKU kind, and the firmware
    verifies descriptors against its own kind anyway).

    Same-protocol siblings only; the service layer enforces that. When
    the instance actually moves, the cached render is invalidated (the
    new kind may carry different panel dims / renderer) so ``/frame``
    reports 204 until the next push repaints. Best-effort: any failure
    is logged and swallowed so it never breaks pairing."""
    declared = body.get("kind")
    if not isinstance(declared, str) or not declared.strip():
        return
    try:
        from app.device_service import update_instance_kind

        _result, changed = update_instance_kind(
            devices=_devices(),
            renderers=_renderers(),
            data_root=_device_data_root(),
            instance_id=device_id,
            kind_id=declared.strip(),
        )
        if changed:
            push_mgr = current_app.config.get("PUSH_MANAGER")
            if push_mgr is not None:
                push_mgr.invalidate_latest_render(device_id)
    except Exception:
        current_app.logger.exception("rest: kind heal failed for device=%s", device_id)


def _deck_store() -> Any:
    return current_app.config.get("DECK_STORE")


def _deck_nav_store() -> Any:
    return current_app.config.get("DECK_NAV_STORE")


def _bound_deck(device_id: str) -> Any:
    """The enabled deck bound to this device, or None. Thin wrapper so
    every deck-cache path resolves the deck the same way."""
    store = _deck_store()
    if store is None:
        return None
    from app.deck_sync import bound_deck_for

    return bound_deck_for(store, device_id)


def _album_store() -> Any:
    return current_app.config.get("ALBUM_STORE")


def _gallery_module() -> Any:
    reg = current_app.config.get("PLUGIN_REGISTRY")
    plugin = reg.get("picture_gallery") if reg is not None else None
    return plugin.server_module if plugin is not None else None


def _bound_album(device_id: str) -> Any:
    """The enabled album bound to this device, or None. Thin wrapper so every
    collection-cache path resolves the album the same way."""
    store = _album_store()
    if store is None:
        return None
    from app.collection_sync import bound_album_for

    return bound_album_for(store, device_id)


def _collection_frames(album: Any) -> list[tuple[str, str]]:
    """The album's ordered ``(frame_id, filename)`` frames, resolved against the
    live gallery folder. Empty when the gallery plugin or folder is missing."""
    gallery = _gallery_module()
    if gallery is None:
        return []
    from app.collection_sync import ordered_frames

    files = gallery.list_folder_files(album.source_folder)
    return ordered_frames(album, files)


def _collection_image_loader(album: Any) -> Any:
    """A ``filename -> bytes | None`` loader for the album's source folder."""
    gallery = _gallery_module()

    def load(filename: str) -> bytes | None:
        if gallery is None:
            return None
        path = gallery.resolve_image_path(album.source_folder, filename)
        if path is None:
            return None
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    return load


def _collection_resync_token(device_id: str) -> str | None:
    """This device's pending resync token, or None. Read on BOTH the /status
    version check and the manifest build; if the two ever disagree the device
    re-syncs on every beat, so failures here fall back to None rather than
    guessing."""
    store = current_app.config.get("COLLECTION_RESYNC_STORE")
    if store is None:
        return None
    try:
        token = store.token(device_id)
    except Exception:
        current_app.logger.exception("rest: resync token lookup failed for device=%s", device_id)
        return None
    return token if isinstance(token, str) and token else None


def _collection_caps(device_id: str) -> tuple[int | None, int | None]:
    """``(capacity_bytes, max_frames)`` this device advertised, as
    ``(None, None)`` when it has said nothing useful.

    Read from the STICKY status entry rather than a beat's own body, and read
    that way by both the /status envelope and the manifest endpoint. The caps
    feed the collection version, so two readers taking them from different
    places would recreate the mismatch this function exists to avoid (#247).
    ``record_status_heartbeat`` has already merged the current beat into the
    sticky entry by the time the envelope is built."""
    status = (current_app.config.get("DEVICE_STATUS") or {}).get(device_id)
    cache_cap = status.get("frame_cache") if isinstance(status, dict) else None
    if not isinstance(cache_cap, dict):
        return None, None
    capacity = cache_cap.get("capacity_bytes")
    max_frames = cache_cap.get("max_frames")
    return (
        capacity if isinstance(capacity, int) and capacity > 0 else None,
        max_frames if isinstance(max_frames, int) and max_frames > 0 else None,
    )


def _collection_status_envelope(device: Device, body: dict[str, Any]) -> dict[str, Any] | None:
    """``{"id", "kind", "version"}`` for the /status response, when this device
    advertised the frame-cache capability on this beat AND has a bound album.
    Absent otherwise, so /status stays byte-identical for devices without the
    feature.

    The version is the SAME computation the manifest endpoint serves under, and
    the caps come from the same sticky status entry it reads. Two versions
    computed from different inputs is what made a cold album unsyncable
    (#247)."""
    from app import collection_sync

    if collection_sync.advertised_frame_cache(body) is None:
        return None
    album = _bound_album(device.id)
    if album is None:
        return None
    version = collection_sync.current_version(
        album,
        _collection_frames(album),
        resync_token=_collection_resync_token(device.id),
    )
    return {"id": f"album:{album.id}", "kind": "album", "version": version}


def _deck_status_envelope(device: Device, body: dict[str, Any]) -> dict[str, Any] | None:
    """``{"version": ...}`` for the /status response, when this device
    advertised the deck-cache capability on this beat AND has a bound
    deck. Absent otherwise, so /status stays byte-identical for devices
    without the feature. Computed without warming (cheap); the manifest
    endpoint is where cold pages get rendered."""
    from app import deck_sync

    if deck_sync.advertised_deck_cache(body) is None:
        return None
    deck = _bound_deck(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if deck is None or push_mgr is None or renders_dir is None:
        return None
    version = deck_sync.current_version(deck, device.id, push_mgr=push_mgr, renders_dir=renders_dir)
    return {"version": version}


def _ingest_deck_report(device: Device, page_id: Any) -> None:
    """Record the page a capable device painted from its local cache
    (``deck_page_id`` on /status bodies and /frame query params), so the
    server's nav position and UI stay truthful about what's on glass.

    This is also the nav-authority handoff in practice: firmware that
    handled a nav locally reports the page instead of sending the
    button/touch event, so the server never double-navigates; any event
    the server DOES receive is one the firmware wants handled here (no
    link match, stale cache), and the report has already moved the
    position the server resolves that event from. Best-effort: bad ids
    are dropped, failures never break the wake."""
    if not isinstance(page_id, str) or not page_id.strip():
        return
    try:
        deck = _bound_deck(device.id)
        nav = _deck_nav_store()
        if deck is None or nav is None:
            return
        wanted = page_id.strip()
        if wanted not in {p.page_id for p in deck.pages}:
            return
        nav.set(device.id, deck.id, wanted)
        # The reported page is what's physically on glass, so it also
        # becomes the device's live frame server-side: ETag polling
        # keeps 304ing (a poll must never "un-navigate" the panel back
        # to the pre-nav frame) and touch stale-checks accept strokes
        # against it. Skip when already aligned.
        #
        # RECENCY GUARD: a heartbeat report describes what WAS painted,
        # and a fresh push may be sitting in the live slot awaiting
        # delivery. Promoting an older deck frame over it would silently
        # revert the push (the device polls, sees nothing newer, and the
        # new dashboard never lands). What's on glass only wins when it
        # is not older than the live slot.
        push_mgr = current_app.config.get("PUSH_MANAGER")
        if push_mgr is not None:
            info = push_mgr.deck_render_for(device.id, wanted)
            latest = push_mgr.latest_render_for(device.id)
            if (
                info is not None
                and (latest is None or latest.get("digest") != info.get("digest"))
                and not _render_older_than(info, latest)
            ):
                push_mgr.promote_deck_page(device.id, wanted)
    except Exception:
        current_app.logger.exception("rest: deck report failed for device=%s", device.id)


def _render_older_than(info: dict[str, Any], latest: dict[str, Any] | None) -> bool:
    """True when render ``info`` is strictly older than ``latest`` by
    the fan-out timestamps. Missing timestamps compare as 0, so legacy
    entries without one never block a promotion."""
    if latest is None:
        return False
    try:
        return float(info.get("timestamp") or 0) < float(latest.get("timestamp") or 0)
    except (TypeError, ValueError):
        return False


def _reset_event_counter(device_id: str) -> None:
    """Best-effort: forget the device's button/touch dedup high-water
    mark on a re-pair (see ButtonService.reset_event_counter). Failures
    never break pairing."""
    try:
        svc = _button_service()
        if svc is not None:
            svc.reset_event_counter(device_id)
    except Exception:
        current_app.logger.exception("rest: event counter reset failed for %s", device_id)


def _current_config(device: Device) -> dict[str, Any]:
    """Per-device config as it stands right now. Same source the MQTT
    config_topic publisher reads from; piggybacking in the status
    response means firmware never needs a separate config poll."""
    from app.device_service import device_config_doc

    return device_config_doc(_settings(), device)


def _advertised_ota_schema(body: dict[str, Any]) -> int | None:
    """The OTA schema version a device advertises support for, from the
    ``ota`` capability object in its register/status body
    (``{"ota": {"schema": 1}}``). None when absent or malformed."""
    if not isinstance(body, dict):
        return None
    cap = body.get("ota")
    if not isinstance(cap, dict):
        return None
    schema = cap.get("schema")
    return int(schema) if isinstance(schema, int) else None


def _staged_ota(device: Device, advertised: int) -> dict[str, str] | None:
    """A per-device staged descriptor (the explicit one-off override), gated on
    schema + kind. Highest precedence: an admin staged this exact device."""
    store = current_app.config.get("OTA_STAGING")
    if store is None:
        return None
    entry = store.get(device.id)
    if not isinstance(entry, dict):
        return None
    schema_version = entry.get("schema_version")
    if not isinstance(schema_version, int) or schema_version > advertised:
        return None
    kind = device.kind_of or device.id
    if entry.get("device_kind") not in (None, kind):
        return None
    descriptor = entry.get("descriptor")
    if isinstance(descriptor, dict) and "payload" in descriptor and "signature" in descriptor:
        return {"payload": str(descriptor["payload"]), "signature": str(descriptor["signature"])}
    return None


def _released_ota(device: Device, body: dict[str, Any]) -> dict[str, str] | None:
    """A per-kind release descriptor for this device: the kind has a release,
    the device is eligible (promoted, or a canary), and the release firmware is
    newer than the version the device reports. Keyed by the device's kind, so it
    inherently targets the right board."""
    store = current_app.config.get("OTA_RELEASE")
    if store is None:
        return None
    kind = device.kind_of or device.id
    entry = store.get(kind)
    if entry is None:
        return None
    descriptor: dict[str, str] | None = store.descriptor_for(kind, device.id)
    if descriptor is None:
        return None
    # Firmware-version gate: only offer when the release is strictly newer than
    # what the device reports, so a device that already applied it isn't nagged.
    reported = body.get("fw_version") if isinstance(body, dict) else None
    if not isinstance(reported, str) or not reported.strip():
        return None
    from app.ota.release import is_newer

    if not is_newer(str(entry.get("fw_version") or ""), reported):
        return None
    return descriptor


def _pending_ota(device: Device, body: dict[str, Any]) -> dict[str, str] | None:
    """The signed OTA descriptor to hand this device on /status, or None.

    Only offered when the device advertised an OTA schema (capability handshake).
    A per-device staged descriptor wins (explicit one-off); otherwise the
    device's kind release applies (manual promote + canary). Re-offering an
    already-applied update is harmless: the firmware's ``already_current`` guard
    skips it, and the release path also gates on the reported firmware version."""
    advertised = _advertised_ota_schema(body)
    if advertised is None:
        return None
    return _staged_ota(device, advertised) or _released_ota(device, body)


@bp.get("/<device_id>/frame")
def get_frame(device_id: str) -> Response:
    """Latest frame URL for this device.

    Returns 200 + JSON ``{url, format, panel_w, panel_h, render_id, rotation?}``
    when a frame has been rendered since the last process restart,
    304 when the caller's ``If-None-Match`` already matches the
    current frame, 204 when no frame has been rendered yet.

    Physical button wakes carry ``?button=left|right|refresh|...`` in
    the query string. The button is dispatched through the button
    service before the frame lookup so a rotate / page action can push
    the new page synchronously and the returned frame reflects the
    new state on this same wake."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    # Deck cache: ingest the displayed-page report BEFORE any touch or
    # button dispatch on this same wake, so a stroke against a locally-
    # painted deck frame is validated against what's actually on glass.
    _ingest_deck_report(device, request.args.get("deck_page_id"))

    # Button dispatch, if the firmware indicated one. Errors here are
    # non-fatal: the goal is best-effort action + always-serve-a-frame,
    # so the wake still returns a valid response even if the button
    # path blew up.
    button_result = None
    button_svc = _button_service()
    button_raw = request.args.get("button", "").strip()
    touch_stroke = _parse_touch_query()
    if button_svc is not None:
        if button_raw:
            button_result = _dispatch_query_button(device, route="/frame")
        elif touch_stroke is not None:
            # Touch wake (issue #49): same shape as a button wake. The
            # stroke dispatches through the region map before the frame
            # lookup, so a page/rotate action's repaint comes back on
            # this same response. Stale strokes (digest mismatch) are
            # dropped and the wake degrades to a plain frame poll.
            try:
                touch_result = button_svc.handle_touch(
                    device_id=device.id,
                    stroke=touch_stroke,
                    frame_digest=_normalize_digest(request.args.get("touch_digest", "")),
                    event_id=_parse_button_event_id(request.args.get("touch_event_id")),
                )
                button_result = touch_result.base
            except Exception:
                current_app.logger.exception(
                    "rest /frame: touch dispatch failed for device=%s",
                    device.id,
                )
                button_result = None
        else:
            # No button on this wake, still attach a read-only rotation
            # snapshot so the firmware always knows where it is.
            try:
                button_result = button_svc.snapshot(device.id)
            except Exception:
                button_result = None

    push_mgr = current_app.config.get("PUSH_MANAGER")
    _refresh_if_widget_change_elapsed(device, push_mgr)
    latest = push_mgr.latest_render_for(device.id) if push_mgr is not None else None
    if latest is None:
        return _error(204, "no frame rendered yet for this device")

    # Use the artifact digest as the ETag; identical bytes always
    # produce the same digest (content-addressed renders).
    etag = f'"{latest["digest"]}"'
    # ``Content-Location`` carries the canonical URL for the current
    # frame on both 200 and 304 responses. HTTP forbids a body on 304
    # (RFC 7230 §3.3.3), so a client that boots without a cached URL
    # (typical on non-e-ink panels that don't retain state through a
    # power cycle) can still learn where to re-fetch the image
    # without needing to have persisted the URL alongside its ETag.
    # RFC 7231 §3.1.4.2 explicitly permits Content-Location here as
    # "the specific resource location that would be returned". Cost
    # to existing clients: zero (they ignore unknown headers).
    # Sign the render path so a device on a public host can fetch the
    # artifact even though ``/renders/`` is otherwise LAN/session-gated
    # (issue #151). The signature is bound to this exact path + a timestamp;
    # on a LAN install it's harmless (the private-client check passes first).
    render_path = f"/renders/{latest['filename']}"
    image_url = f"{request.url_root.rstrip('/')}{render_path}"
    sig_query = sign_render_query(current_app.secret_key, render_path)
    if sig_query:
        image_url = f"{image_url}?{sig_query}"
    # Resend intent (#119): a "resend" from History flags the frame so a
    # REST client re-fetches once even when the bytes are identical to
    # what it already shows (MQTT gets this via force_publish). Skip the
    # 304 this once, then clear the flag so the next poll of the same
    # frame goes back to 304.
    force_refetch = bool(latest.get("force_refetch"))
    # ``fetch_latest`` is deliberately narrower than ``refresh``: do not
    # render or publish anything, but bypass the conditional GET for this
    # response so the client downloads the latest artefact already on disk.
    # Carrying the intent through ButtonHandleResult also makes the action
    # work for clients that keep their ETag on button/touch requests.
    force_download = bool(button_result is not None and button_result.force_download)
    if_none_match = request.headers.get("If-None-Match", "")
    if if_none_match and if_none_match == etag and not (force_refetch or force_download):
        resp = Response(status=304)
        resp.headers["ETag"] = etag
        resp.headers["Content-Location"] = image_url
        if push_mgr is not None:
            # A matching 304 confirms the device already holds this frame.
            # Advancing last-served here clears Companion's pending badge
            # without forcing an otherwise unnecessary download.
            push_mgr.record_frame_served(device.id, latest)
        return resp
    if force_refetch and push_mgr is not None:
        push_mgr.consume_force_refetch(device.id)
    panel = device.manifest.get("panel") or {}
    payload = {
        "url": image_url,
        "format": latest.get("ext", ""),
        "panel_w": int(panel.get("w", 0) or 0),
        "panel_h": int(panel.get("h", 0) or 0),
        "render_id": latest["digest"],
        "renderer_id": latest.get("renderer_id", ""),
    }
    # Devices whose manifest declares a framebuffer (a client that sent a
    # ``rotation`` at registration, or a hardware manifest with a
    # ``native_w``/``native_h`` block) get those dims echoed back
    # alongside the composer-orientation ones, so the client can
    # sanity-check the image it's about to paint without having to know
    # which way the dashboard is turned (issue #200). Absent for panels
    # with no declared stride, and additive either way: a client that
    # doesn't read the keys is unaffected.
    if panel.get("native_w") and panel.get("native_h"):
        payload["native_w"] = int(panel["native_w"])
        payload["native_h"] = int(panel["native_h"])
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
    # Interaction manifest pointer (protocol v2): devices whose sticky
    # capability advertises proto >= 2 learn the manifest digest on
    # every frame response, so an unchanged layout never costs a
    # manifest re-fetch. v1 responses stay byte-identical.
    if _device_proto_v(device.id) >= 2:
        try:
            manifest_doc = _build_manifest_for(device, str(latest["digest"]))
        except Exception:
            current_app.logger.exception(
                "rest /frame: manifest build failed for device=%s", device.id
            )
            manifest_doc = None
        if manifest_doc is not None:
            payload["manifest"] = {
                "digest": manifest_doc["manifest_digest"],
                "url": (f"/api/v1/device/{device.id}/frame/manifest?digest={latest['digest']}"),
            }

    # Rotation envelope: current position, page id, and manual-override
    # state so the firmware (and any admin UI polling this endpoint)
    # can display where the device is. Omitted when the device isn't
    # bound to any rotation.
    if button_result is not None and button_result.rotation_id is not None:
        payload["rotation"] = button_result.to_envelope()

    # Button-wake window (#123): emitted only for kinds whose schema declares
    # it, so a button wake learns how long to stay awake for repeat presses
    # from this same response. 0 means deep-sleep immediately after painting.
    if "button_wake_s" in (device.config_schema or {}):
        payload["button_wake_s"] = _button_wake_s(device)

    resp = jsonify(payload)
    resp.headers["ETag"] = etag
    resp.headers["Content-Location"] = image_url
    resp.headers["Cache-Control"] = "no-cache"
    if push_mgr is not None:
        push_mgr.record_frame_served(device.id, latest)
    return resp


# -- sleep screen: one request, image bytes, no redirect -----------------
#
# The normal frame path is a two-hop envelope: GET /frame returns JSON
# naming a content-addressed artifact under /renders/, and the client
# fetches that. Some clients cannot walk it. An e-reader that pulls a
# dashboard as its sleep screen does so from a declarative download
# handler wired into its sleep transition: exactly one HTTP request, no
# redirect following, and no way to attach headers of its own. For that
# shape the URL has to be stable and answer with the image itself.
#
# The overriding rule on this path is that a 2xx must always carry a
# complete image. Such a client streams the response to a temporary file
# and renames it over the live sleep screen on any 2xx, so a 204, or a
# 200 with an empty body, silently replaces a working screen with a
# zero-byte file that renders as nothing. Every "no image right now"
# outcome here is therefore a 404 and every failure a 5xx: on a non-2xx
# the client keeps whatever it already had, which is the correct
# degradation.

# Renderer whose output this route serves. BMP rather than the device's
# own configured format on purpose: an uncompressed indexed BMP needs no
# decoder at all on the client, and pinning it here means adding this
# route cannot change the bytes any existing client of the same device
# receives.
_SLEEP_SCREEN_RENDERER = "circuitpython_bmp"

# Hard ceiling on the body. The target firmware caps an event download at
# 1 MB, and an over-size body fails the download rather than truncating
# into a smaller picture. Serving one anyway would be a coin-flip on the
# exact corruption this route is shaped to avoid, so it errors instead —
# loudly, and with the measured size, because the fix is a configuration
# one (panel dims or gamut) and the number is the diagnosis.
_SLEEP_SCREEN_MAX_BYTES = 1024 * 1024

# One rendered BMP per device, keyed by everything that changes the
# bytes. The quantise is a real cost (fit, contrast, dither, pack) and
# nothing about this route is rate-limited: a client left polling it, or
# a dashboard embedding it, would otherwise re-run the whole pipeline per
# request for an image that hasn't moved. In memory on purpose, like
# every other derived-frame cache here — a restart re-derives on the
# next request, which is the same answer at a slightly higher cost.
_sleep_screen_cache: dict[str, tuple[str, bytes]] = {}
_sleep_screen_lock = threading.Lock()


def _sleep_screen_renderer(device: Device) -> Any | None:
    """The BMP renderer to transform with, preferring this device's own
    clone so per-device dither / contrast tuning applies.

    Clones are ``<base>__<instance>`` and only exist for devices whose
    kind actually consumes the renderer, so a panel on some other format
    falls through to the base renderer and its manifest defaults.
    """
    registry = _renderers()
    for clone in registry.for_device(device.id):
        if clone.id.split("__", 1)[0] == _SLEEP_SCREEN_RENDERER:
            return clone
    return registry.get(_SLEEP_SCREEN_RENDERER)


def _composition_png_bytes(latest: dict[str, Any]) -> bytes | None:
    """The composition PNG behind a latest-render entry, or None.

    This is what Playwright wrote before any per-renderer transform, so
    it is the right input to re-transform from regardless of what the
    device's own artifact ended up being (a packed ``.bin`` can't be
    re-rendered into anything).
    """
    renders_dir = current_app.config.get("RENDERS_DIR")
    if renders_dir is None:
        return None
    comp_digest = latest.get("composition_digest")
    if not isinstance(comp_digest, str) or not comp_digest:
        # Pre-0.8.6 latest-render entries predate the composition digest.
        # They repopulate on the device's next push; until then there is
        # no PNG to transform and the caller reports "nothing to serve"
        # rather than inventing something.
        return None
    path = Path(renders_dir) / f"{comp_digest}.png"
    try:
        return path.read_bytes()
    except OSError:
        return None


def _sleep_screen_bmp(device: Device, latest: dict[str, Any]) -> tuple[bytes | None, str]:
    """Render the device's current frame as an indexed BMP.

    Returns ``(bytes, "")`` on success or ``(None, reason)``, where the
    reason distinguishes "nothing to serve" from "this went wrong" so the
    caller can pick 404 vs 5xx.
    """
    comp_png = _composition_png_bytes(latest)
    if comp_png is None:
        return None, "no composition available for the current frame"
    panel = device_panel(device)
    if panel is None:
        return None, "device declares no panel block"
    renderer = _sleep_screen_renderer(device)
    if renderer is None:
        return None, f"renderer {_SLEEP_SCREEN_RENDERER!r} is not installed"
    try:
        settings = _settings().get_for_runtime(
            "renderers", renderer.id, renderer.manifest.get("settings", [])
        )
    except Exception:
        current_app.logger.exception(
            "rest /frame.bmp: settings lookup failed for renderer=%s", renderer.id
        )
        settings = {}

    signature = hashlib.sha256(
        json.dumps(
            {
                "comp": latest.get("composition_digest"),
                "renderer": renderer.id,
                "panel": panel.model_dump(),
                "settings": settings,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:16]
    with _sleep_screen_lock:
        cached = _sleep_screen_cache.get(device.id)
    if cached is not None and cached[0] == signature:
        return cached[1], ""

    try:
        body = renderer.transform(comp_png, panel=panel, settings=settings)
    except Exception:
        current_app.logger.exception(
            "rest /frame.bmp: transform failed for device=%s renderer=%s",
            device.id,
            renderer.id,
        )
        return None, "frame conversion failed"
    if not body:
        # Belt and braces. A renderer returning nothing would otherwise
        # become a 200 with an empty body, which is the one response this
        # route must never produce.
        return None, "frame conversion produced no bytes"
    with _sleep_screen_lock:
        _sleep_screen_cache[device.id] = (signature, body)
    return body, ""


@bp.get("/<device_id>/frame.bmp")
def get_frame_bmp(device_id: str) -> Response:
    """The current frame as an uncompressed indexed BMP, bytes in the body.

    A single-request alternative to the ``/frame`` envelope for clients
    that cannot follow it: the URL is stable, the response is the image,
    and nothing here redirects. Auth is the same per-device token as every
    other device route, additionally accepted as ``?k=<token>`` because
    the clients this exists for cannot set headers.

    Serves BMP whatever the device's configured frame format is, so a
    panel painting ``.bin`` frames keeps painting them; this route just
    re-transforms the same composition into a second, decoder-free
    container.

    ``404`` when there is no frame to serve, ``401`` / ``403`` on auth,
    ``5xx`` on a conversion failure. Never ``204``, and never a 2xx
    without a complete body: the client renames its download over the
    live sleep screen on any 2xx.
    """
    device, err = _auth_device(device_id, allow_query_token=True)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    push_mgr = current_app.config.get("PUSH_MANAGER")
    if push_mgr is None:
        return _error(503, "push manager unavailable")
    # Button dispatch FIRST, exactly like the /frame poll, and for the reason
    # this route exists: /frame hands back the last rendered artefact and the
    # re-render hangs off the poll, so a route that only serves the newest
    # stored render would return identical bytes for ever. ``?button=refresh``
    # publishes synchronously and the new frame is what gets served below.
    _dispatch_query_button(device, route="/frame.bmp")
    # Same freshness pass the envelope route runs: a widget that declared
    # its output goes stale at a given moment gets one re-render here, so
    # a sleep screen pulled just after that moment isn't the pre-change
    # frame. Bounded to at most one render per declared change.
    _refresh_if_widget_change_elapsed(device, push_mgr)
    latest = push_mgr.latest_render_for(device.id)
    if latest is None:
        # 404, NOT the 204 the envelope route uses for this case.
        return _error(404, "no frame rendered yet for this device")

    body, reason = _sleep_screen_bmp(device, latest)
    if body is None:
        if reason.startswith("no composition"):
            return _error(404, reason)
        return _error(500, reason)
    if len(body) > _SLEEP_SCREEN_MAX_BYTES:
        current_app.logger.warning(
            "rest /frame.bmp: %s bytes exceeds the %s byte client cap for device=%s "
            "(panel %sx%s, gamut %s)",
            len(body),
            _SLEEP_SCREEN_MAX_BYTES,
            device.id,
            (device.panel or {}).get("w"),
            (device.panel or {}).get("h"),
            (device.panel or {}).get("gamut"),
        )
        return _error(
            500,
            "frame exceeds the client's download cap",
            bytes=len(body),
            max_bytes=_SLEEP_SCREEN_MAX_BYTES,
        )

    # A reachability probe sends ``Range: bytes=0-0`` to confirm the route
    # answers without pulling the whole image through a client-side HTTP relay
    # that may cap at 32 KiB. Answering it exactly costs one branch. HEAD is
    # deliberately NOT the mechanism for this: a correct HEAD promises a
    # Content-Length with no body, which such a relay reports as a truncated
    # response. Only the single-byte prefix form is honoured; anything else
    # falls through to the full body, which is always valid.
    if _wants_first_byte(request.headers.get("Range")):
        probe = Response(body[:1], status=206, mimetype="image/bmp")
        probe.headers["Content-Range"] = f"bytes 0-0/{len(body)}"
        probe.headers["Accept-Ranges"] = "bytes"
        probe.headers["Cache-Control"] = "no-store"
        return probe

    resp = Response(body, mimetype="image/bmp")
    resp.headers["ETag"] = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    resp.headers["Accept-Ranges"] = "bytes"
    resp.headers["Cache-Control"] = "no-store"
    # Deliberately no ``record_frame_served``: that is delivery bookkeeping
    # for the device's own frame slot, and this route may be pulled by
    # something other than the panel that owns the id (a second screen
    # borrowing the dashboard, a browser checking the output). Advancing
    # the slot from here would clear a pending badge for a panel that
    # never received anything.
    return resp


@bp.get("/<device_id>/frames.json")
def get_frames_index(device_id: str) -> Response:
    """A tiny list of pullable frames, for an on-device picker.

    Lets a reader fetch a dashboard on demand without a computer in the
    loop. Each ``url`` is absolute and directly downloadable under the
    same one-request, no-redirect rule as ``/frame.bmp``, and carries the
    device token as a query parameter because the on-device downloader
    that consumes it cannot attach headers.

    ``items`` is always a JSON array. Clients cast it to one, so an object
    here would silently yield an empty picker.
    """
    device, err = _auth_device(device_id, allow_query_token=True)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    token = _request_token(allow_query=True)
    base = request.url_root.rstrip("/")
    # ``button=refresh`` so picking this item re-renders rather than handing
    # back whatever was last stored. Without it the picker is a link to a
    # frame that may be hours old, which reads as the download being broken.
    # ``button_event_id`` is the dedup counter; a wall-clock stamp is
    # monotonic across requests, which is all the dedup needs.
    params: dict[str, Any] = {"button": "refresh", "button_event_id": int(time.time())}
    if token:
        params[TOKEN_QUERY_PARAM] = token
    frame_url = f"{base}/api/v1/device/{device.id}/frame.bmp?{urlencode(params)}"
    # ASCII only in the display strings: the target's e-ink UI font has no
    # guaranteed coverage for arrows, dashes or symbols, and a missing
    # glyph is a tofu box on a screen with no way to report it.
    return jsonify(
        {
            "items": [
                {
                    "id": "current",
                    "title": "Current dashboard",
                    "subtitle": "Download as sleep screen",
                    "url": frame_url,
                }
            ]
        }
    )


# -- tap -----------------------------------------------------------------


# Protocol v2 wire outcomes for region reports. Everything that
# dispatched (or legitimately resolved to nothing to do) is "ok" — the
# device goes quiet on ok and its follow-up behaviour keys off the
# manifest's action type, not the outcome. Failures map to SPECIFIC
# strings (the firmware logs non-ok outcomes verbatim, so "error" hides
# the diagnosis; bench, 2026-07-25). "stale" / "deduped" / "ha_failed"
# pass through as the firmware vocabulary already names them.
_V2_OK_OUTCOMES = frozenset(
    {"ha_dispatched", "dispatched", "webhook_dispatched", "fetched", "noop"}
)
_V2_OUTCOME_NAMES = {
    "no_target": "no_action_for_region",
    "error": "action_error",
    "blocked": "provenance_blocked",
    "no_frame": "no_frame",
}


def _v2_outcome(outcome: str) -> str:
    if outcome in _V2_OK_OUTCOMES:
        return "ok"
    return _V2_OUTCOME_NAMES.get(outcome, outcome)


@bp.post("/<device_id>/tap")
def post_tap(device_id: str) -> Response:
    """Standalone touch-stroke dispatch (issue #49) for continuously
    powered clients that poll ``/frame`` separately (CircuitPython
    touch boards). Deep-sleep firmware should prefer the ``touch_*``
    query params on ``GET /frame`` so the action's repaint comes back
    on the same wake.

    Body JSON: ``{"x0": int, "y0": int, "x1"?: int, "y1"?: int,
    "duration_ms"?: int, "digest": str, "event_id"?: int}``. ``x``/``y``
    are accepted as aliases for ``x0``/``y0``. ``digest`` is the frame
    digest the client is displaying (its ETag, quotes optional) and is
    required: a stroke is only dispatched against the exact frame the
    finger touched.

    Returns 200 with ``{outcome, gesture, action_spec, description,
    rotation?}``. Guard-chain exits (``stale`` / ``no_frame`` /
    ``no_target`` / ``deduped``) are 200s, not errors: the client's
    correct move in every case is the same, re-poll ``/frame`` and
    carry on."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    svc = _button_service()
    if svc is None:
        return _error(503, "touch dispatch unavailable")
    body = request.get_json(silent=True, force=True) or {}
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    digest = _normalize_digest(str(body.get("digest") or ""))
    if not digest:
        return _error(400, "digest is required (the frame ETag being displayed)")
    # Protocol v2 report: the device hit-tested locally against its
    # interaction manifest and names the region instead of coordinates.
    region_id = body.get("region_id")
    if isinstance(region_id, str) and region_id.strip():
        x0d = _parse_coord(body.get("x0")) or 0
        y0d = _parse_coord(body.get("y0")) or 0
        try:
            report = svc.handle_region_report(
                device_id=device.id,
                region_id=region_id.strip()[:96],
                gesture=str(body.get("gesture") or "tap").strip(),
                frame_digest=digest,
                value=_parse_button_event_id(body.get("value")),
                event_id=_parse_button_event_id(body.get("event_id")),
                stroke=TouchStroke(x0=x0d, y0=y0d, x1=x0d, y1=y0d),
            )
        except Exception:
            # Still a 200: the device's correct move is identical to any
            # non-ok outcome (log + re-poll), and the specific string
            # reaches the serial log instead of a mute 500.
            current_app.logger.exception("rest /tap: region report failed for device=%s", device.id)
            return jsonify({"outcome": "resolver_exception", "gesture": None})
        payload_v2: dict[str, Any] = {
            "outcome": _v2_outcome(report.outcome),
            "gesture": report.gesture,
            "action_spec": report.action_spec,
            "description": report.base.action_description,
        }
        if report.base.rotation_id is not None:
            payload_v2["rotation"] = report.base.to_envelope()
        return jsonify(payload_v2)
    x0 = _parse_coord(body.get("x0", body.get("x")))
    y0 = _parse_coord(body.get("y0", body.get("y")))
    if x0 is None or y0 is None:
        return _error(400, "x0 and y0 are required non-negative integers")
    x1 = _parse_coord(body.get("x1"))
    y1 = _parse_coord(body.get("y1"))
    stroke = TouchStroke(
        x0=x0,
        y0=y0,
        x1=x1 if x1 is not None else x0,
        y1=y1 if y1 is not None else y0,
        duration_ms=_parse_button_event_id(body.get("duration_ms")),
    )
    try:
        result = svc.handle_touch(
            device_id=device.id,
            stroke=stroke,
            frame_digest=digest,
            event_id=_parse_button_event_id(body.get("event_id")),
        )
    except Exception:
        current_app.logger.exception("rest /tap: dispatch failed for device=%s", device.id)
        return _error(500, "touch dispatch failed")
    payload: dict[str, Any] = {
        "outcome": result.outcome,
        "gesture": result.gesture,
        "action_spec": result.action_spec,
        "description": result.base.action_description,
    }
    if result.base.rotation_id is not None:
        payload["rotation"] = result.base.to_envelope()
    return jsonify(payload)


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
        # Deck cache: ingest the displayed-page report BEFORE the button
        # dispatch below, so a button carried on the same heartbeat
        # resolves from the deck position actually on glass.
        _ingest_deck_report(device, body.get("deck_page_id"))

    # Button dispatch, if the firmware included it in the body. Same
    # non-fatal contract as the /frame path: errors here don't break
    # the heartbeat response. Dedup against ``last_button_event_id``
    # means /frame + /status carrying the same event on one wake
    # doesn't fire the action twice.
    button_result = None
    button_svc = _button_service()
    if button_svc is not None and isinstance(body, dict):
        raw_button = body.get("button")
        button_name = raw_button.strip() if isinstance(raw_button, str) else ""
        if button_name:
            try:
                button_result = button_svc.handle_button(
                    device_id=device.id,
                    button=button_name,
                    event_id=_parse_button_event_id(body.get("button_event_id")),
                )
            except Exception:
                current_app.logger.exception(
                    "rest /status: button dispatch failed for device=%s button=%s",
                    device.id,
                    button_name,
                )
                button_result = None
        else:
            try:
                button_result = button_svc.snapshot(device.id)
            except Exception:
                button_result = None
    elif button_svc is not None:
        try:
            button_result = button_svc.snapshot(device.id)
        except Exception:
            button_result = None

    response = {
        "status": 200,
        "config": _current_config(device),
        "next_poll_s": _next_poll_s(device),
        # Integer epoch, NOT a float: CircuitPython / MicroPython clients parse a
        # JSON float into a single-precision float32, whose resolution near 1.78e9
        # is ~128s, so a float server_time rounds to the nearest ~2 minutes (#143).
        # An integer literal parses exactly on a longint-capable client.
        "server_time": int(time.time()),
        **_resolve_local_time_fields(request_tz),
    }
    if button_result is not None and button_result.rotation_id is not None:
        response["rotation"] = button_result.to_envelope()
    # OTA (#121): hand back a staged, signed descriptor when the device
    # advertised a compatible ``ota`` capability in this heartbeat. Absent
    # otherwise, so /status stays byte-identical for devices without OTA.
    ota = _pending_ota(device, body if isinstance(body, dict) else {})
    if ota is not None:
        response["ota"] = ota
    # Deck cache: repeat the bound deck's current version so a capable
    # device knows when its SD cache is stale and re-syncs the manifest.
    # Gated on the capability being advertised in THIS body, so /status
    # stays byte-identical for everything else.
    if isinstance(body, dict):
        try:
            deck_env = _deck_status_envelope(device, body)
        except Exception:
            current_app.logger.exception(
                "rest /status: deck envelope failed for device=%s", device.id
            )
            deck_env = None
        if deck_env is not None:
            response["deck"] = deck_env
        # Frame-cache collections (#177): repeat the bound album's id + current
        # version so a capable device knows when its cached collection is stale
        # and re-syncs the manifest. Same current-state gating as the deck
        # envelope; absent means no collection is active (drop local playback).
        try:
            collection_env = _collection_status_envelope(device, body)
        except Exception:
            current_app.logger.exception(
                "rest /status: collection envelope failed for device=%s", device.id
            )
            collection_env = None
        if collection_env is not None:
            response["collection"] = collection_env
        # Overlay values piggyback (hybrid render mode): a capable device
        # gets its live frame's values on every heartbeat for free, so
        # slots refresh on every wake even outside a linger window.
        from app.overlay_sync import advertised_overlay

        # Gate on the STICKY capability (already merged from this body by
        # record_status_heartbeat above), not the body alone: a protocol
        # v2 firmware may advertise only ``proto`` on a beat, and losing
        # the envelopes would darken values + patch corrections for every
        # deep-sleep v2 device (bench, 2026-07-25).
        sticky = (current_app.config.get("DEVICE_STATUS") or {}).get(device.id)
        overlay_cap = advertised_overlay(body)
        if (
            overlay_cap is None
            and isinstance(sticky, dict)
            and isinstance(sticky.get("overlay"), dict)
        ):
            overlay_cap = dict(sticky["overlay"])
        proto_v = _device_proto_v(device.id)
        if overlay_cap is not None or proto_v >= 2:
            try:
                push_mgr = current_app.config.get("PUSH_MANAGER")
                latest = push_mgr.latest_render_for(device.id) if push_mgr else None
                latest_digest = str(latest.get("digest") or "") if latest else ""
                values = _overlay_values_doc(device, latest_digest) if latest_digest else None
            except Exception:
                current_app.logger.exception(
                    "rest /status: overlay values failed for device=%s", device.id
                )
                values = None
                latest_digest = ""
            if values is not None:
                response["overlay_values"] = values
            # Patch document piggyback (overlay schema 2 / proto v2): a
            # pending post-action patch rides the heartbeat as a sibling
            # of ``overlay_values``, so a device that beats before its
            # next /frame/data poll catches up one hop earlier.
            schema = int((overlay_cap or {}).get("schema") or 0)
            if (schema >= 2 or proto_v >= 2) and latest_digest:
                patches = _frame_patches_doc(device.id, latest_digest)
                if patches is not None:
                    response["overlay_patches"] = patches
    return jsonify(response)


# -- deck cache sync (on-device SD frame cache) ---------------------------


@bp.get("/<device_id>/deck")
def get_deck_manifest(device_id: str) -> Response:
    """The bound deck's sync manifest for this device: page frame
    digests + byte sizes + TTLs and the link graph, so capable firmware
    can fill its SD cache and navigate locally. Contract documented in
    docs/dev/client-protocol.md.

    Cold pages are warmed (rendered) on demand so the manifest ships
    complete and its version is stable until the next refresh; expect a
    few seconds on the first call after a deck edit. 204 when no deck is
    bound (mirror of /frame's no-frame-yet semantics)."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    deck = _bound_deck(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if deck is None or push_mgr is None or renders_dir is None:
        return _error(204, "no deck bound to this device")

    from app.deck_sync import build_manifest

    # The device's advertised card capacity (current-state, from its
    # heartbeats); pages beyond it get cache=false rather than letting
    # the firmware overfill its card mid-sync.
    status = (current_app.config.get("DEVICE_STATUS") or {}).get(device.id)
    cache_cap = status.get("deck_cache") if isinstance(status, dict) else None
    capacity = cache_cap.get("capacity_bytes") if isinstance(cache_cap, dict) else None

    manifest = build_manifest(
        deck,
        device.id,
        push_mgr=push_mgr,
        renders_dir=renders_dir,
        warm_missing=True,
        touch=device.manifest.get("touch") is True,
        regions_lookup=push_mgr.touch_regions_for,
        capacity_bytes=capacity if isinstance(capacity, int) and capacity > 0 else None,
    )
    return jsonify({"status": 200, **manifest})


@bp.get("/<device_id>/deck/frame/<digest>")
def get_deck_frame(device_id: str, digest: str) -> Response:
    """One deck frame by digest, for the SD cache fill. Digest-addressed
    (content never changes under a digest), so firmware can cache
    forever and only fetch digests its manifest diff says are new. 404
    on an unknown digest means the client's manifest is stale: re-sync."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    deck = _bound_deck(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if deck is None or push_mgr is None or renders_dir is None:
        return _error(404, "no deck bound to this device")

    from app.deck_sync import frame_entry_by_digest

    info = frame_entry_by_digest(deck, device.id, _normalize_digest(digest), push_mgr=push_mgr)
    if info is None:
        return _error(404, "unknown frame digest; re-fetch the deck manifest")
    path = Path(renders_dir) / str(info.get("filename") or "")
    if not path.is_file():
        return _error(404, "frame artifact missing; re-fetch the deck manifest")
    from flask import send_file

    resp = send_file(path, mimetype="application/octet-stream")
    resp.headers["ETag"] = f'"{info["digest"]}"'
    resp.headers["Cache-Control"] = "immutable, max-age=31536000"
    return resp


@bp.get("/<device_id>/collection")
def get_collection_manifest(device_id: str) -> Response:
    """The bound collection's sync manifest for this device: per-frame ids,
    positions, digests, byte sizes, ttls, cache eligibility, and the opaque
    producer block, so capable firmware can fill its cache and play back
    locally. Contract in docs/dev/frame-cache.md.

    The first producer is the offline photo album (#177). Cold frames are
    rendered on demand so the manifest ships complete; expect a few seconds on
    the first call after an album edit. 204 when nothing is bound.

    Responses are paged (``?cursor=<next_cursor>`` continues) so a large album
    stays inside constrained firmware receive buffers; every ``cache: true``
    frame is in page one as long as the advertised ``max_frames`` fits a page."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    album = _bound_album(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if album is None or push_mgr is None or renders_dir is None:
        return _error(204, "no collection bound to this device")

    from app.collection_sync import build_manifest, paged_manifest

    # The device's advertised card capacity + frame cap; frames beyond either
    # get cache=false. Read through the same helper the /status envelope uses,
    # because these feed the version and the two must not diverge (#247).
    capacity, max_frames = _collection_caps(device.id)

    manifest = build_manifest(
        album,
        device.id,
        push_mgr=push_mgr,
        renders_dir=renders_dir,
        frames=_collection_frames(album),
        image_loader=_collection_image_loader(album),
        device_id_for_url=device.id,
        warm_missing=True,
        capacity_bytes=capacity,
        max_frames=max_frames,
        resync_token=_collection_resync_token(device.id),
    )
    manifest = paged_manifest(manifest, request.args.get("cursor"))
    # The album path was previously silent on success, so a device that
    # never asked and a device that asked and got nothing looked
    # identical in the journal (#247).
    logger.info(
        "rest /collection/manifest: device=%s album=%s version=%s frames=%d cursor=%s",
        device.id,
        getattr(album, "id", "?"),
        manifest.get("version"),
        len(manifest.get("frames") or []),
        request.args.get("cursor") or "-",
    )
    return jsonify({"status": 200, **manifest})


@bp.get("/<device_id>/collection/frame/<digest>")
def get_collection_frame(device_id: str, digest: str) -> Response:
    """One collection frame by digest, for the cache fill. Digest-addressed
    (content never changes under a digest), so firmware can cache forever and
    only fetch digests its manifest diff says are new. 404 on an unknown digest
    means the client's manifest is stale: re-sync."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]

    album = _bound_album(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if album is None or push_mgr is None or renders_dir is None:
        return _error(404, "no collection bound to this device")

    from app.collection_sync import frame_entry_by_digest

    info = frame_entry_by_digest(
        device.id,
        _normalize_digest(digest),
        push_mgr=push_mgr,
        frames=_collection_frames(album),
    )
    if info is None:
        return _error(404, "unknown frame digest; re-fetch the collection manifest")
    path = Path(renders_dir) / str(info.get("filename") or "")
    if not path.is_file():
        return _error(404, "frame artifact missing; re-fetch the collection manifest")
    from flask import send_file

    resp = send_file(path, mimetype="application/octet-stream")
    resp.headers["ETag"] = f'"{info["digest"]}"'
    resp.headers["Cache-Control"] = "immutable, max-age=31536000"
    return resp


# -- interaction manifest (protocol v2) ------------------------------------


def _frame_info_for_digest(device: Device, wanted: str) -> dict[str, Any] | None:
    """Render info for a digest the device may be showing: its live
    frame, a just-superseded frame still inside the grace window (a
    device mid-linger on the old digest keeps its manifest and values
    until its next /frame poll moves it forward), or a deck-cached one
    (deck-painted pages get manifests too)."""
    push_mgr = current_app.config.get("PUSH_MANAGER")
    if push_mgr is None or not wanted:
        return None
    info = push_mgr.latest_render_for(device.id)
    if info and str(info.get("digest") or "") == wanted:
        return dict(info)
    previous_fn = getattr(push_mgr, "previous_render_for", None)
    prev = previous_fn(device.id) if callable(previous_fn) else None
    if prev and str(prev.get("digest") or "") == wanted:
        return dict(prev)
    deck = _bound_deck(device.id)
    if deck is not None:
        from app.deck_sync import frame_entry_by_digest

        entry = frame_entry_by_digest(deck, device.id, wanted, push_mgr=push_mgr)
        if entry is not None:
            return dict(entry)
    return None


def _atlas_provider_for(device: Device) -> Any:
    """The manifest builder's atlas source: build (or reuse) the glyph
    strip for a (px, weight) pair and hand back its device-scoped URL."""
    renders_dir = current_app.config.get("RENDERS_DIR")
    rasterizer = current_app.config.get("OVERLAY_ATLAS_RASTERIZER")
    url_root = request.url_root

    def provider(px: int, weight: int) -> dict[str, Any] | None:
        if renders_dir is None:
            return None
        from app.overlay_sync import browser_rasterizer, build_atlas

        rasterize = rasterizer or browser_rasterizer(url_root)
        atlas = build_atlas(px, weight, renders_dir=Path(renders_dir), rasterize=rasterize)
        if atlas is None:
            return None
        atlas["url"] = f"/api/v1/device/{device.id}/frame/overlay/atlas/{atlas['digest']}"
        return atlas

    return provider


def _build_manifest_for(device: Device, wanted: str) -> dict[str, Any] | None:
    """The interaction manifest for one served frame, or None only when
    the digest resolves to no known frame (or the frame has no
    composition at all, e.g. an image push).

    A manifest with empty ``regions`` + ``text`` is VALID and served: a
    proto-2 device that gets a /frame 200 without a manifest block
    treats the server as v1 and latches out of region dispatch (bench,
    2026-07-25), so a non-interactive dashboard must say "nothing
    tappable" explicitly rather than go silent.

    The last successfully-built manifest per device is cached; when the
    frame's sidecar can't reproduce one (sidecar lost, geometry hiccup)
    but the composition digest still matches the cache, the cached
    document is re-anchored to ``wanted`` so a re-render can never
    strand the device without its manifest."""
    info = _frame_info_for_digest(device, wanted)
    if info is None:
        return None
    push_mgr = current_app.config.get("PUSH_MANAGER")
    comp_digest = str(info.get("composition_digest") or "")
    if push_mgr is None or not comp_digest:
        return None
    cache: dict[str, dict[str, Any]] = current_app.config.setdefault("MANIFEST_CACHE", {})
    cached = cache.get(device.id)

    def _reanchored() -> dict[str, Any] | None:
        if cached is None or cached.get("comp") != comp_digest:
            return None
        doc = dict(cached["doc"])
        doc["frame_digest"] = wanted
        return doc

    regions = push_mgr.touch_regions_for(comp_digest)
    slots = push_mgr.overlay_slots_for(comp_digest)
    if not regions and not slots:
        # Sidecar empty OR lost. Same composition as the cached build
        # means lost: re-anchor rather than downgrade the device.
        recovered = _reanchored()
        if recovered is not None:
            current_app.logger.warning(
                "rest: manifest sidecar missing for comp=%s; re-anchored cached "
                "manifest for device=%s",
                comp_digest,
                device.id,
            )
            return recovered
    from app.manifest import build_interaction_manifest

    status = (current_app.config.get("DEVICE_STATUS") or {}).get(device.id)
    overlay_cap = status.get("overlay") if isinstance(status, dict) else None
    max_targets = overlay_cap.get("max_targets", 32) if isinstance(overlay_cap, dict) else 32
    doc = build_interaction_manifest(
        frame_digest=wanted,
        regions=regions,
        slots=slots,
        panel=device.panel or {},
        atlas_provider=_atlas_provider_for(device) if slots else None,
        max_regions=int(max_targets),
    )
    if doc is None:
        return _reanchored()
    page_id = str(info.get("page_id") or "")
    if (
        not doc["regions"]
        and not doc["text"]
        and cached is not None
        and page_id
        and cached.get("page_id") == page_id
        and (cached["doc"]["regions"] or cached["doc"]["text"])
    ):
        # An interactive page cannot legitimately rebuild to an empty
        # manifest between redraws — that's the extraction race losing a
        # render's regions. A structurally-valid 0-region manifest kills
        # touch on the device until the next good redraw (bench,
        # 2026-07-25), so serve the last populated manifest for this
        # page re-anchored to the new frame, and keep it cached.
        current_app.logger.warning(
            "rest: empty manifest rebuild for interactive page=%s (comp=%s); "
            "serving last populated manifest for device=%s",
            page_id,
            comp_digest,
            device.id,
        )
        recovered = dict(cached["doc"])
        recovered["frame_digest"] = wanted
        return recovered
    cache[device.id] = {"comp": comp_digest, "doc": dict(doc), "page_id": page_id}
    return doc


def _device_proto_v(device_id: str) -> int:
    status = (current_app.config.get("DEVICE_STATUS") or {}).get(device_id)
    cap = status.get("proto") if isinstance(status, dict) else None
    v = cap.get("v") if isinstance(cap, dict) else None
    return v if isinstance(v, int) and not isinstance(v, bool) else 1


@bp.get("/<device_id>/frame/manifest")
def get_frame_manifest(device_id: str) -> Response:
    """The protocol-v2 interaction manifest for one served frame
    (``?digest=<frame_digest>``): wire-space region rects with stable
    ids, gesture + tier + feedback declarations, and device-rendered
    text regions. Action payloads never leave the server; the device
    reports region ids back on /tap. 404 = no manifest for this digest
    (unknown frame, no interactivity), which the firmware treats as
    feature-off."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    doc = _build_manifest_for(device, _normalize_digest(request.args.get("digest", "")))
    if doc is None:
        return _error(404, "no manifest for this frame")
    return jsonify(doc)


def _canvas_for_page(page_id: str) -> Any:
    """The CanvasLayout of a canvas page, or None for a grid page / unknown id."""
    page_store = current_app.config.get("PAGE_STORE")
    page = page_store.get(page_id) if page_store is not None and page_id else None
    if page is not None and page.layout_kind == "canvas" and page.canvas is not None:
        return page.canvas
    return None


@bp.get("/<device_id>/frame/spec")
def get_frame_spec(device_id: str) -> Response:
    """The touch-v3 spec for the device's current frame: typed primitives the
    firmware draws and hit-tests locally. Anchored to a layout digest stable
    across data-only redraws, so a clock tick doesn't invalidate touch. An empty
    ``primitives`` list is valid (a non-interactive dashboard).

    ``?layout=<digest>`` is ADVISORY, not a long-poll: the firmware passes the
    digest it currently holds, but the endpoint always builds and returns the
    current spec immediately and never blocks waiting for the layout to change.
    (The firmware compares ``layout_digest`` itself and repaints only on a
    change.) The response never drives a synchronous browser render either: glyph
    atlases are attached only when already cached, and warmed out-of-band
    otherwise, so a device poll can't stall behind the render queue."""
    # Read (and ignore) the advisory layout hint so the contract is explicit:
    # the endpoint is non-blocking regardless of what the device holds.
    _ = request.args.get("layout")
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    push_mgr = current_app.config.get("PUSH_MANAGER")
    latest = push_mgr.latest_render_for(device.id) if push_mgr is not None else None
    if not latest:
        return _error(404, "no frame")
    info = _frame_info_for_digest(device, str(latest.get("digest") or ""))
    page_id = str(info.get("page_id") or "") if info else ""

    from app.touch_spec import build_frame_spec, wire_transform

    canvas = _canvas_for_page(page_id)
    if canvas is None:
        return jsonify(build_frame_spec([]))
    wire = wire_transform(device.panel or {}, int(canvas.w), int(canvas.h))
    spec = build_frame_spec(canvas.els, wire=wire)
    _attach_touch_atlases(spec, device)
    return jsonify(spec)


# Roles currently being warmed in a background thread, so concurrent /frame/spec
# polls don't each spawn a duplicate build for the same (static) atlas.
_touch_atlas_warming: set[str] = set()
_touch_atlas_warm_lock = threading.Lock()


def _attach_touch_atlases(spec: dict[str, Any], device: Device) -> None:
    """Attach the glyph atlases the spec's primitive text refs reference
    (label / value_text), CACHE-ONLY. A device poll must never drive a Playwright
    render inline (the render queue is single-browser and serialized, so an
    inline build can stall the request for seconds or hang behind other work).
    Cached atlases are attached; any that are missing are omitted (the firmware
    draws chrome without text, per the contract) and warmed in the background so
    the next poll carries them."""
    roles: set[str] = set()
    for prim in spec.get("primitives", []):
        for key in ("label", "value_text"):
            ref = prim.get(key)
            if isinstance(ref, dict) and isinstance(ref.get("atlas"), str):
                roles.add(ref["atlas"])
    renders_dir = current_app.config.get("RENDERS_DIR")
    if not roles or renders_dir is None:
        return
    from app.touch_atlas import build_touch_atlas

    atlases: list[dict[str, Any]] = []
    missing: list[str] = []
    for role in sorted(roles):
        # rasterize=None -> cache-only, returns None on a cold cache without
        # touching the browser.
        desc = build_touch_atlas(role, renders_dir=Path(renders_dir), rasterize=None)
        if desc is not None:
            desc["url"] = f"/api/v1/device/{device.id}/atlas/{desc['digest']}"
            atlases.append(desc)
        else:
            missing.append(role)
    if atlases:
        spec["atlases"] = atlases
    if missing:
        _schedule_touch_atlas_warm(missing)


def _schedule_touch_atlas_warm(roles: list[str]) -> None:
    """Kick a daemon thread to build the given (static) touch atlases so a later
    poll finds them cached. De-duped against in-flight roles; never blocks."""
    with _touch_atlas_warm_lock:
        todo = [r for r in roles if r not in _touch_atlas_warming]
        _touch_atlas_warming.update(todo)
    if not todo:
        return
    app_obj = current_app._get_current_object()  # type: ignore[attr-defined]
    url_root = request.url_root
    thread = threading.Thread(
        target=_warm_touch_atlases, args=(app_obj, todo, url_root), daemon=True
    )
    thread.start()


def _warm_touch_atlases(app_obj: Flask, roles: list[str], url_root: str) -> None:
    """Background: build each touch atlas role through the real rasterizer (the
    serialized render queue) so it lands in the cache. Runs off the request path,
    so the browser render can take as long as it needs without stalling a poll."""
    try:
        with app_obj.app_context():
            renders_dir = app_obj.config.get("RENDERS_DIR")
            if renders_dir is None:
                return
            from app.overlay_sync import browser_rasterizer
            from app.touch_atlas import build_touch_atlas

            rasterize = app_obj.config.get("OVERLAY_ATLAS_RASTERIZER") or browser_rasterizer(
                url_root
            )
            for role in roles:
                try:
                    build_touch_atlas(role, renders_dir=Path(renders_dir), rasterize=rasterize)
                except Exception:
                    logger.exception("touch atlas warm failed role=%s", role)
    finally:
        with _touch_atlas_warm_lock:
            _touch_atlas_warming.difference_update(roles)


@bp.get("/<device_id>/atlas/<digest>")
def get_touch_atlas(device_id: str, digest: str) -> Response:
    """The 4bpp-gray glyph strip for a touch-v3 atlas digest: content-addressed
    and immutable, so the firmware fetches it once per digest to render primitive
    labels and value readouts."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    safe = _normalize_digest(digest)
    if not safe or len(safe) > 40 or any(c not in "0123456789abcdef" for c in safe):
        return _error(404, "no atlas")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if renders_dir is None:
        return _error(404, "no atlas")
    path = Path(renders_dir) / f"touch-atlas-{safe}.bin"
    if not path.is_file():
        return _error(404, "no atlas")
    return Response(
        path.read_bytes(),
        mimetype="application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


def _entity_from_value_key(value_key: str) -> str:
    """The HA entity id in a ``ha:<entity>[:<attr>]`` binding, or ""."""
    if not value_key.startswith("ha:"):
        return ""
    return value_key[3:].split(":", 1)[0]


def _action_for_primitive(el: Any) -> str | dict[str, Any] | None:
    """The action spec to dispatch for a touch-v3 primitive. A button uses its
    ``on_tap``; a switch toggles the entity in ``value_key``; a slider/stepper
    uses its ``on_slide`` action (with ``{value}`` substituted at dispatch)."""
    if el.kind == "button":
        return el.on_tap or None
    if el.kind == "switch":
        entity = _entity_from_value_key(el.value_key)
        if not entity:
            return None
        return {
            "action": "ha",
            "domain": entity.split(".", 1)[0],
            "service": "toggle",
            "data": {"entity_id": entity},
        }
    if el.kind in ("slider", "stepper"):
        slide = el.on_slide if isinstance(el.on_slide, dict) else None
        return slide.get("action") if slide else None
    return None


@bp.post("/<device_id>/interact")
def post_interact(device_id: str) -> Response:
    """Device-owned touch report: the firmware hit-tested a primitive locally and
    reports ``{primitive_id, interaction, value}``. The server resolves the
    primitive's action and dispatches it (payloads never left the server).
    Always 200 with an ``outcome`` (never an error status), mirroring /tap; the
    device already showed feedback and doesn't branch on the response."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    primitive_id = str(body.get("primitive_id") or "")
    raw_value = body.get("value")
    value = (
        int(raw_value)
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool)
        else None
    )
    raw_event = body.get("event_id")
    event_id = (
        int(raw_event)
        if isinstance(raw_event, (int, float)) and not isinstance(raw_event, bool)
        else None
    )

    push_mgr = current_app.config.get("PUSH_MANAGER")
    latest = push_mgr.latest_render_for(device.id) if push_mgr is not None else None
    if not latest:
        return jsonify({"outcome": "no_frame", "primitive_id": primitive_id})
    info = _frame_info_for_digest(device, str(latest.get("digest") or ""))
    page_id = str(info.get("page_id") or "") if info else ""
    canvas = _canvas_for_page(page_id)
    els = canvas.els if canvas is not None else []
    el = next(
        (e for e in els if getattr(e, "id", "") == primitive_id and e.kind in PRIMITIVE_KINDS),
        None,
    )
    if el is None:
        return jsonify({"outcome": "no_target", "primitive_id": primitive_id})
    spec = _action_for_primitive(el)
    svc = current_app.config.get("BUTTON_SERVICE")
    if spec is None or svc is None:
        return jsonify({"outcome": "noop", "primitive_id": primitive_id})
    result = svc.dispatch_touch_spec(
        device_id=device.id,
        spec=spec,
        value=value,
        event_id=event_id,
        region_box={"x": el.x, "y": el.y, "w": el.w, "h": el.h},
    )
    return jsonify({"outcome": result.outcome, "primitive_id": primitive_id})


# -- device event stream (protocol v2) --------------------------------------

# SSE cadence knobs. The stream is an optimisation over the 1 s linger
# poll, not a correctness surface: events derive from a periodic scan of
# the same server state /frame/data and /status expose, so a device that
# can't hold the connection loses nothing but latency.
_STREAM_SCAN_S = 2.0
# Keepalive doubles as the dead-peer detector: the generator only learns the
# client is gone when a write fails, and between writes it just sleeps holding
# a waitress thread. At 25 s a device that reconnected every ~1.6 s (bench,
# 2026-08-22) kept ~16 of 24 threads parked on connections nobody was reading,
# and the admin UI queued behind them for 9-23 s. Five seconds bounds that
# window; the frames are one comment line each.
_STREAM_KEEPALIVE_S = 5.0

# One live stream per device. Without this a firmware reconnect loop stacks a
# new thread per attempt, each still scanning (and issuing Home Assistant
# queries) every _STREAM_SCAN_S for a client that has hung up.
_stream_gen_lock = threading.Lock()


def _claim_device_stream(app_obj: Any, device_id: str) -> int:
    """Register a new stream for this device and retire any earlier one.

    Returns the generation token the generator must keep presenting; a
    superseded generator sees a newer number and exits on its next tick."""
    with _stream_gen_lock:
        gens = app_obj.config.setdefault("DEVICE_STREAM_GENERATIONS", {})
        token = int(gens.get(device_id, 0)) + 1
        gens[device_id] = token
        return token


def _stream_is_current(app_obj: Any, device_id: str, token: int) -> bool:
    with _stream_gen_lock:
        gens = app_obj.config.get("DEVICE_STREAM_GENERATIONS") or {}
        return int(gens.get(device_id, 0)) == token


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _touch_value_key_slots(app_obj: Any, page_id: str) -> list[dict[str, Any]]:
    """Minimal value slots for the touch primitives on a canvas page, so the SSE
    values stream carries live state for switches/sliders/steppers keyed by
    value_key (which the firmware maps back to its primitives). Runs outside a
    request context, so the page store is read through ``app_obj.config``."""
    if not page_id:
        return []
    store = app_obj.config.get("PAGE_STORE")
    page = store.get(page_id) if store is not None else None
    if page is None or page.layout_kind != "canvas" or page.canvas is None:
        return []
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in page.canvas.els:
        vk = getattr(el, "value_key", "")
        if el.kind in ("switch", "slider", "stepper") and vk and vk not in seen:
            seen.add(vk)
            slots.append({"key": vk})
    return slots


def _stream_events(
    app_obj: Any,
    device: Device,
    *,
    max_ticks: int | None = None,
    scan_s: float = _STREAM_SCAN_S,
    keepalive_s: float = _STREAM_KEEPALIVE_S,
    generation: int | None = None,
) -> Any:
    """The SSE generator for one device: ``values`` / ``patches`` /
    ``sync`` events on change, comment keepalives in between. Runs
    outside any request context (waitress streams it on its own
    thread), so everything is read through ``app_obj.config`` directly.
    ``max_ticks`` bounds the loop for tests.

    ``generation`` is the token from :func:`_claim_device_stream`; when the
    same device opens a newer stream this one stops scanning immediately
    rather than waiting for a failed write to notice nobody is reading."""
    last_values: str | None = None
    last_patch_seq = 0
    last_sync: tuple[str, str] | None = None
    last_ka = time.monotonic()
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        if generation is not None and not _stream_is_current(app_obj, device.id, generation):
            logging.getLogger(__name__).info(
                "device stream superseded for %s; closing the older connection", device.id
            )
            return
        try:
            push_mgr = app_obj.config.get("PUSH_MANAGER")
            latest = push_mgr.latest_render_for(device.id) if push_mgr else None
            digest = str(latest.get("digest") or "") if latest else ""
            if push_mgr is not None and digest:
                # Patches: newest-wins by seq, so emit each staged doc once.
                doc = push_mgr.frame_patches_for(device.id, digest)
                if doc is not None and int(doc.get("seq") or 0) > last_patch_seq:
                    last_patch_seq = int(doc.get("seq") or 0)
                    yield _sse("patches", doc)
                # Values: emit when the resolved strings change. Overlay value
                # slots plus the current page's touch-primitive bindings, so a
                # switch/slider reflects HA changing externally (touch-v3).
                comp = str(latest.get("composition_digest") or "") if latest else ""
                slots = push_mgr.overlay_slots_for(comp) if comp else []
                page_id = str(latest.get("page_id") or "") if latest else ""
                all_slots = list(slots) + _touch_value_key_slots(app_obj, page_id)
                if all_slots:
                    registry = app_obj.config.get("PLUGIN_REGISTRY")
                    plugin = registry.get("ha_core") if registry is not None else None
                    mod = getattr(plugin, "server_module", None) if plugin is not None else None
                    if mod is not None and getattr(mod, "is_configured", lambda: False)():
                        from app.overlay_sync import values_document

                        vals = values_document(
                            all_slots, ha_get_state=mod.get_state, now=time.time()
                        )
                        fingerprint = json.dumps(vals.get("values") or {}, sort_keys=True)
                        if fingerprint != last_values:
                            last_values = fingerprint
                            yield _sse("values", vals)
                # Sync: digest pointers, emitted on change.
                bundle = _bundle_digest_for(app_obj, device)
                sync_now = (digest, bundle)
                if sync_now != last_sync:
                    last_sync = sync_now
                    sync_payload: dict[str, Any] = {
                        "frame_digest": digest,
                        "seq": int(time.time() * 1000),
                    }
                    if bundle:
                        sync_payload["bundle_digest"] = bundle
                    yield _sse("sync", sync_payload)
        except GeneratorExit:
            raise
        except Exception:
            logging.getLogger(__name__).exception("device stream scan failed for %s", device.id)
        now = time.monotonic()
        if now - last_ka >= keepalive_s:
            last_ka = now
            yield ":ka\n\n"
        if max_ticks is None or ticks < max_ticks:
            time.sleep(scan_s)


def _bundle_digest_for(app_obj: Any, device: Device) -> str:
    """The device's current state-bundle version, or empty (no deck
    bound, nothing warmed). The SSE sync event's change detector."""
    try:
        from app.bundle_sync import bundle_digest_for

        deck_store = app_obj.config.get("DECK_STORE")
        decks = deck_store.for_device(device.id) if deck_store is not None else []
        return bundle_digest_for(
            decks[0] if decks else None,
            device.id,
            push_mgr=app_obj.config.get("PUSH_MANAGER"),
        )
    except Exception:
        return ""


@bp.get("/<device_id>/bundle")
def get_bundle(device_id: str) -> Response:
    """The protocol-v2 state bundle for this device: every warmed deck
    page as a digest-addressed ``frame`` state plus the navigation
    links table, so capable firmware fills its SD cache and navigates
    tier-0. Content-hash keyed; the SSE ``sync`` event repeats the
    bundle digest so the device knows when to re-diff. 204 when no deck
    is bound or nothing is warmed yet."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    deck = _bound_deck(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if deck is None or push_mgr is None or renders_dir is None:
        return _error(204, "no bundle for this device")
    from app.bundle_sync import build_bundle

    doc = build_bundle(deck, device.id, push_mgr=push_mgr, renders_dir=Path(renders_dir))
    if doc is None:
        return _error(204, "no bundle for this device")
    return jsonify({"status": 200, **doc})


@bp.get("/<device_id>/bundle/frame/<digest>")
def get_bundle_frame(device_id: str, digest: str) -> Response:
    """One bundle frame by digest (raw wire framebuffer bytes).
    Digest-addressed and immutable; 404 means the client's bundle
    manifest is stale — re-sync."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    deck = _bound_deck(device.id)
    push_mgr = current_app.config.get("PUSH_MANAGER")
    renders_dir = current_app.config.get("RENDERS_DIR")
    if deck is None or push_mgr is None or renders_dir is None:
        return _error(404, "no bundle for this device")
    from app.deck_sync import frame_entry_by_digest

    info = frame_entry_by_digest(deck, device.id, _normalize_digest(digest), push_mgr=push_mgr)
    if info is None:
        return _error(404, "unknown frame digest; re-fetch the bundle")
    path = Path(renders_dir) / str(info.get("filename") or "")
    if not path.is_file():
        return _error(404, "frame artifact missing; re-fetch the bundle")
    from flask import send_file

    resp = send_file(path, mimetype="application/octet-stream")
    resp.headers["ETag"] = f'"{info["digest"]}"'
    resp.headers["Cache-Control"] = "immutable, max-age=31536000"
    return resp


@bp.get("/<device_id>/stream")
def get_device_stream(device_id: str) -> Response:
    """Protocol v2 push channel: a Server-Sent Events feed of ``values``
    / ``patches`` / ``sync`` envelopes (identical payloads to the
    /status piggybacks and /frame/data), with a comment keepalive every
    ``_STREAM_KEEPALIVE_S``. At most ONE live stream per device: opening a
    second retires the first, so a firmware reconnect loop cannot stack
    threads. Deep-sleep clients should keep polling instead. Reverse proxies
    must not buffer this route (Caddy: ``flush_interval -1``)."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    app_obj = current_app._get_current_object()  # type: ignore[attr-defined]
    generation = _claim_device_stream(app_obj, device.id)
    resp = Response(
        _stream_events(app_obj, device, generation=generation),
        mimetype="text/event-stream",
    )
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# -- overlay atlases + frame data (hybrid render mode) ---------------------
# The schema-1 overlay-spec endpoint (GET /frame/overlay/<digest>) was
# removed with protocol v2 (docs/protocol-v2-touch.md): firmware that
# probes it gets a 404, which the v1 client contract already defines as
# feature-off for the frame. Atlases, values, and patches remain.


@bp.get("/<device_id>/frame/overlay/atlas/<digest>")
def get_overlay_atlas(device_id: str, digest: str) -> Response:
    """One glyph atlas strip by digest (raw 4bpp-gray bytes). Content-
    addressed, so immutable caching applies; 404 means the client's
    spec is stale."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    renders_dir = current_app.config.get("RENDERS_DIR")
    wanted = _normalize_digest(digest)
    if renders_dir is None or not wanted or not wanted.isalnum():
        return _error(404, "unknown atlas digest")
    path = Path(renders_dir) / f"overlay-atlas-{wanted}.bin"
    if not path.is_file():
        return _error(404, "unknown atlas digest")
    from flask import send_file

    resp = send_file(path, mimetype="application/octet-stream")
    resp.headers["ETag"] = f'"{wanted}"'
    resp.headers["Cache-Control"] = "immutable, max-age=31536000"
    return resp


@bp.get("/<device_id>/frame/patch/<digest>")
def get_frame_patch_blob(device_id: str, digest: str) -> Response:
    """One post-action patch blob by digest (raw rect row data in the
    frame's own packing, see the patch document's ``rects`` offsets).
    Content-addressed, so immutable caching applies; 404 means the
    client's patch document is stale (superseded or restart-swept):
    drop it and fall back to a normal frame poll."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    renders_dir = current_app.config.get("RENDERS_DIR")
    wanted = _normalize_digest(digest)
    if renders_dir is None or not wanted or not wanted.isalnum():
        return _error(404, "unknown patch digest")
    path = Path(renders_dir) / f"overlay-patch-{wanted}.bin"
    if not path.is_file():
        return _error(404, "unknown patch digest")
    from flask import send_file

    resp = send_file(path, mimetype="application/octet-stream")
    resp.headers["ETag"] = f'"{wanted}"'
    resp.headers["Cache-Control"] = "immutable, max-age=31536000"
    return resp


def _ha_get_state() -> Any | None:
    """``get_state(entity_id)`` from the ha_core plugin, or None when HA
    isn't configured. Same resolution path as app.ha_actions."""
    registry = current_app.config.get("PLUGIN_REGISTRY")
    plugin = registry.get("ha_core") if registry is not None else None
    mod = getattr(plugin, "server_module", None) if plugin is not None else None
    if mod is None or not getattr(mod, "is_configured", lambda: False)():
        return None
    return mod.get_state


def _overlay_values_doc(device: Device, frame_digest: str) -> dict[str, Any] | None:
    """The values document for a device's frame, or None when the frame
    is unknown, has no slots, or HA isn't configured. Resolution shares
    ``_frame_info_for_digest`` (live / grace-window / deck), so a 1 s
    linger poll against a just-superseded digest keeps its values."""
    push_mgr = current_app.config.get("PUSH_MANAGER")
    if push_mgr is None or not frame_digest:
        return None
    info = _frame_info_for_digest(device, frame_digest)
    if info is None:
        return None
    slots = push_mgr.overlay_slots_for(str(info.get("composition_digest") or ""))
    if not slots:
        return None
    get_state = _ha_get_state()
    if get_state is None:
        return None
    from app.overlay_sync import values_document

    return values_document(slots, ha_get_state=get_state, now=time.time())


@bp.get("/<device_id>/frame/data")
def get_frame_data(device_id: str) -> Response:
    """Live data for a served frame (``?digest=<frame>``): the overlay
    slot values, plus any pending post-action patch document under
    ``patches``, both anchored to the exact frame the client says it is
    showing. Polled at 1-2 s cadence during the touch-linger window.

    Any KNOWN digest (live, grace-window, or deck-cached) answers 200,
    with an empty ``values`` document when the frame has no slots and
    nothing is staged: a 404 reads as data-off and a device that latched
    it mid-linger would then miss the patch a tap stages a second later
    (bench, 2026-07-25). 404 = genuinely unknown digest only."""
    device, err = _auth_device(device_id)
    if err is not None or device is None:
        return err  # type: ignore[return-value]
    digest = _normalize_digest(request.args.get("digest", ""))
    doc = _overlay_values_doc(device, digest)
    patches = _frame_patches_doc(device.id, digest)
    if doc is None and patches is None and _frame_info_for_digest(device, digest) is None:
        return _error(404, "unknown frame digest")
    out: dict[str, Any] = doc if doc is not None else {"seq": int(time.time() * 1000), "values": {}}
    if patches is not None:
        out["patches"] = patches
    return jsonify(out)


def _frame_patches_doc(device_id: str, frame_digest: str) -> dict[str, Any] | None:
    """The pending patch document for a device's served frame, or None.
    Anchoring (document digest == the digest the client asked about) is
    enforced by the push manager; a patch for any other frame is never
    handed out."""
    if not frame_digest:
        return None
    push_mgr = current_app.config.get("PUSH_MANAGER")
    patches_fn = getattr(push_mgr, "frame_patches_for", None) if push_mgr is not None else None
    if not callable(patches_fn):
        return None
    try:
        doc = patches_fn(device_id, frame_digest)
    except Exception:
        current_app.logger.exception("rest: frame patch lookup failed for %s", device_id)
        return None
    return doc if isinstance(doc, dict) else None


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


def _log_pairing_outcome(endpoint: str, resp: Response) -> None:
    """Log the result of a ``/register`` or ``/discover`` attempt so a
    failed pairing is debuggable from the server, not just from the
    firmware's discarded HTTP response or a packet capture (issue #126).

    Rejections log at WARNING with the status, device id, client IP, and
    the returned error string; successes log at INFO. The pairing code
    itself is never logged (it's a header, and this only reads the body +
    the response envelope), so nothing sensitive lands in the log."""
    status = resp.status_code
    req_body = request.get_json(silent=True)
    device_id = ""
    if isinstance(req_body, dict):
        device_id = _canonical_id(req_body.get("device_id"))
    if status >= 400:
        reason: Any = None
        try:
            payload = json.loads(resp.get_data(as_text=True))
            if isinstance(payload, dict):
                reason = payload.get("error")
        except (ValueError, TypeError):
            reason = None
        logger.warning(
            "rest %s: rejected device=%s ip=%s status=%d: %s",
            endpoint,
            device_id or "?",
            _client_id_for_rate_limit(),
            status,
            reason or "(no reason)",
        )
    else:
        logger.info(
            "rest %s: device=%s ip=%s status=%d ok",
            endpoint,
            device_id or "?",
            _client_id_for_rate_limit(),
            status,
        )


def _claim_device_by_mac(mac: str) -> Device | None:
    """Look for a registered device instance whose stored MAC matches
    the given MAC. Used by the discover-then-claim flow: when admin
    clicks Register on a discovered device, the resulting instance
    carries the discovered MAC; subsequent ``/discover`` POSTs from
    that firmware match the MAC and receive the device's access token
    without needing a pairing code.

    Case-insensitive compare; spec-MAC formats (with or without colons,
    upper or lower case) normalise to lower-no-separators.

    Instances carrying a placeholder MAC are skipped rather than matched.
    Installs that paired before v0.300.1 may have ``"None"`` or an
    all-zero MAC persisted from a client that formatted its null into the
    body; matching one would hand the announcing device someone else's
    token, so they're treated as MAC-less here (issue #226). No migration
    needed: the stored value stays put and simply stops being claimable."""
    target = (usable_mac(mac) or "").lower().replace(":", "").replace("-", "")
    if not target:
        return None
    for dev in _devices().all():
        if dev.kind_of is None:
            continue
        stored = usable_mac(dev.manifest.get("mac"))
        if not stored:
            continue
        normalised = stored.lower().replace(":", "").replace("-", "")
        if normalised == target:
            return dev
    return None


@bp.post("/discover")
def post_discover() -> Response:
    resp = _discover()
    _log_pairing_outcome("discover", resp)
    return resp


def _discover() -> Response:
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
         "panel_h": int, "rotation": int, "fw_version": "...",
         "mac": "..." }

    ``device_id`` and ``mac`` are required (the MAC is what the claim
    path matches on); everything else is optional.

    ``rotation`` is optional; see
    :func:`app.device_service.panel_geometry_from_report` for what
    declaring it changes.

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

    # A body that doesn't parse used to fall through to an empty dict and
    # surface as "device_id is required", which sends a client author
    # hunting for a field they did send. Name the real problem: the most
    # common cause is a client formatting a Python repr (single quotes,
    # bare None) instead of serialising JSON (issue #226).
    parsed_body = request.get_json(silent=True)
    if parsed_body is None and request.get_data():
        return _error(400, "body is not valid JSON")
    body = parsed_body or {}
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    device_id = _canonical_id(body.get("device_id"))
    if not device_id:
        return _error(400, "device_id is required")
    mac = usable_mac(body.get("mac"))
    if not mac:
        # The whole flow keys off the MAC: the announce lands in the
        # Discovered strip, the admin clicks Register, and the NEXT
        # discover POST matches the stored MAC and receives the token.
        # A MAC-less announce has nothing to match on later, so it used
        # to register cleanly in the UI and then poll forever on
        # ``registered: false`` with no way to tell why (issue #226).
        # Reject it up front instead of accepting a pairing that can
        # never complete; a client that genuinely has no MAC to report
        # pairs via ``/register`` with a 6-digit code.
        #
        # A placeholder counts as absent, see :func:`usable_mac`. Taking
        # ``"None"`` at face value is the worse failure of the two: two
        # clients sending the same placeholder collide on one instance
        # and the second is handed the first one's access token.
        return _error(
            400,
            "mac is required: /discover hands back the device token by matching "
            "this MAC against a registered device. Send the client's real MAC "
            '(a placeholder such as null, "None" or 00:00:00:00:00:00 is '
            "rejected, since two devices sharing one would claim each other's "
            "token), or pair with a 6-digit code via /api/v1/device/register.",
        )

    # MAC-match claim: if admin already registered a device whose
    # manifest carries this MAC, hand back the token + config so the
    # firmware can stop polling discover and start its real wake loop.
    claimed = _claim_device_by_mac(mac)
    if claimed is not None:
        token = claimed.manifest.get("access_token")
        if isinstance(token, str) and token:
            # Honour a re-declared kind + wire format on reconnect,
            # same as /register. A re-flashed device whose NVS was
            # wiped lands here (MAC match), so this is the path that
            # heals a stale generic kind after a board-build upgrade.
            # Both reload the instance, so re-fetch it.
            _maybe_heal_kind(claimed.id, body)
            _maybe_switch_wire_format(claimed.id, body)
            # Wiped NVS also means a restarted wake-event counter:
            # forget the dedup high-water mark or every future
            # button/touch reads as a duplicate.
            _reset_event_counter(claimed.id)
            current = _devices().get(claimed.id) or claimed
            # The MAC is this path's identity key, so a client that
            # announces a different device_id than the one on the matched
            # instance is handed the stored id and its token. That's
            # deliberate (it's how a re-flash with wiped settings
            # re-acquires), but nothing said so: the only tell was
            # diffing the echoed device_id, and a client that missed it
            # kept talking about an id the server doesn't have (#239).
            # Say it in the body and in a header, so a client can branch
            # on the header without reading the JSON at all.
            changed = current.id != device_id
            claim_payload: dict[str, Any] = {
                "status": 200,
                "registered": True,
                "device_token": token,
                "device_id": current.id,
                "device_id_changed": changed,
                "config": _current_config(current),
                "server_time": int(time.time()),
            }
            if changed:
                claim_payload["announced_device_id"] = device_id
                logger.warning(
                    "rest discover: mac %s matched device=%s but the client "
                    "announced device_id=%s; returning the stored id. The "
                    "client should adopt %s (or the device should be deleted "
                    "and re-paired under the new id).",
                    mac,
                    current.id,
                    device_id,
                    current.id,
                )
            resp = jsonify(claim_payload)
            # Always sent on a claim, so a client can tell "this server
            # doesn't report it" (header absent) from "nothing changed"
            # (header present, false).
            resp.headers["X-Tesserae-Device-Id"] = current.id
            resp.headers["X-Tesserae-Device-Id-Changed"] = "true" if changed else "false"
            return resp

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
    resp = _register()
    _log_pairing_outcome("register", resp)
    return resp


def _register() -> Response:
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

    # Same reasoning as /discover: an unparseable body says so rather
    # than blaming the first field it can't find (issue #226).
    parsed_body = request.get_json(silent=True)
    if parsed_body is None and request.get_data():
        return _error(400, "body is not valid JSON")
    body = parsed_body or {}
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")

    device_id = _canonical_id(body.get("device_id"))
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
    #
    # v0.69.1 (issue #41) extends this with an optional ``gamut``
    # field so a generic CircuitPython kind can serve every panel
    # shape from one manifest; the value is canonicalised via
    # :func:`app.quantizer.canonicalise_gamut` so aliases
    # (``spectra_6`` → ``waveshare_e6``, ``acep_7colour`` →
    # ``inky_7colour``) collapse cleanly.
    panel_overrides: dict[str, Any] | None = None
    try:
        w = int(body.get("panel_w") or 0)
        h = int(body.get("panel_h") or 0)
    except (TypeError, ValueError):
        w = h = 0
    # Optional ``rotation`` (0 / 90 / 180 / 270, issue #200): declares that
    # panel_w / panel_h are the client's framebuffer and names the turn
    # from that buffer to the dashboard canvas. Absent, the dims are the
    # canvas and only the aspect is inferred, which is what every client
    # written before the field existed means.
    geometry, reported_orientation = panel_geometry_from_report(
        w=w, h=h, rotation=parse_rotation(body.get("rotation"))
    )
    if w > 0 and h > 0:
        panel_overrides = {"w": w, "h": h, **geometry}
    declared_gamut = body.get("gamut")
    if isinstance(declared_gamut, str) and declared_gamut.strip():
        from app.quantizer import canonicalise_gamut

        panel_overrides = panel_overrides or {}
        panel_overrides["gamut"] = canonicalise_gamut(declared_gamut.strip())

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
        # Honour a re-declared kind (generic protocol kind -> hardware
        # SKU sibling) and wire format so a device can move without a
        # delete + re-register. Both reload the instance in place, so
        # re-fetch it for the config echo.
        _maybe_heal_kind(device_id, body)
        _maybe_switch_wire_format(device_id, body)
        # A re-pair means the firmware's NVS (and its wake-event
        # counter) may have been wiped; forget the dedup high-water
        # mark or every future button/touch reads as a duplicate.
        _reset_event_counter(device_id)
        current = devices_registry.get(device_id) or existing
        return jsonify(
            {
                "status": 200,
                "device_token": token,
                "device_id": current.id,
                "server_time": int(time.time()),
                "config": _current_config(current),
                "reused_existing": True,
            }
        )

    # v0.69.2 (issue #48): a previous device with this id may have been
    # deleted without ticking the "also wipe" checkbox. If we recorded
    # the previous MAC and the incoming MAC differs (or is missing),
    # treat this as a different physical device that happens to reuse
    # the id and auto-wipe the leftovers before create_instance runs
    # so the new device starts pristine. Matching MACs keep state
    # (same physical device came back).
    # Placeholders are dropped rather than persisted: a stored ``"None"``
    # would become claimable by any other client sending the same string
    # on /discover (issue #226). Pairing still succeeds, the device just
    # has no MAC to auto-claim with later, which is the honest state.
    incoming_mac = usable_mac(body.get("mac"))
    markers = _deleted_device_markers()
    if markers.mac_differs(device_id, incoming_mac):
        from app import device_cleanup as _cleanup

        _cleanup.wipe_orphan_state(
            device_id=device_id,
            page_store=current_app.config["PAGE_STORE"],
            event_log=current_app.config["EVENT_LOG"],
            settings_store=_settings(),
            data_root=_data_root(),
            devices=_devices(),
        )
    markers.clear(device_id)

    # Wire format the client asked for (png / bmp), resolved to a
    # renderer of the chosen kind. Lets a memory-constrained
    # CircuitPython client pin the uncompressed-BMP renderer at pairing
    # time, matching the discover-and-claim path.
    from app.device_service import renderer_id_for_format

    renderer_id_arg = renderer_id_for_format(_renderers(), kind, body.get("format"))
    result = create_instance(
        devices=devices_registry,
        renderers=_renderers(),
        data_root=_device_data_root(),
        instance_id=device_id,
        kind_id=kind_id,
        name=str(body.get("name") or "").strip(),
        panel_overrides=panel_overrides,
        orientation=reported_orientation,
        access_token=None,  # let create_instance mint one
        mac=incoming_mac,
        renderer_id=renderer_id_arg,
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
            "device_id": result.device.id,
            "server_time": int(time.time()),
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

    manifest_path = _device_data_root() / f"{device.id}.json"
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
