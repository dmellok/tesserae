"""HTTP API for TRMNL-compatible clients.

TRMNL clients (jailbroken Kindles running the KOReader trmnl-display
plugin, TRMNL devices, any BYOS-compatible client) poll
``GET /api/display`` on a fixed cadence and paint whatever PNG the
server hands back. Authentication is a per-device ``access-token``
header, the user generates it in Tesserae's Settings → Devices and
pastes it into the client's config. No MAC, no pairing flow, no MQTT.

Three endpoints:

* ``GET /api/display``, polled by every client on every wake. Returns
  ``{image_url, filename, refresh_rate, ...}`` pointing at the latest
  rendered frame for the device. Also captures the client's status
  headers (battery, RSSI, panel dims, firmware version) into the same
  ``DEVICE_STATUS`` cache the MQTT devices feed, so Settings → Devices
  shows freshness + battery uniformly.

* ``GET /api/setup/``, first-boot flow. Native TRMNL clients call it
  to exchange a MAC for a token; the KOReader plugin skips it. We
  respond with whatever token the request brought (or a generated one
  if pairing-style discovery makes sense later) so the client doesn't
  give up.

* ``POST /api/log/``, optional client-side diagnostics. Native TRMNLs
  use it; the KOReader plugin doesn't. We accept anything, log the
  body, and return 200.

Until the renderer + per-device latest-render map land, ``/api/display``
returns a server-generated test pattern at the client's requested
dimensions (read from ``png-width`` / ``png-height`` headers, like the
recon server). That keeps the flow demonstrable end-to-end while the
production pipeline integration comes together.
"""

from __future__ import annotations

import io
import json
import logging
import time
from typing import Any

from flask import Blueprint, Flask, Response, current_app, jsonify, request, url_for

from app import device_poll
from app.device_loader import Device, DeviceRegistry
from app.discovery import record_trmnl_discovery

logger = logging.getLogger(__name__)

bp = Blueprint("trmnl_api", __name__)

# Device kind id that the API recognises. Lookup goes
# ``access_token header → device manifest → kind_of`` so a device
# instance with a different kind_of can't be addressed via /api/display
# even if the user pasted its token somewhere by mistake.
TRMNL_KIND_ID = "trmnl_client"


# -- registry helpers ---------------------------------------------------


def _devices() -> DeviceRegistry:
    return current_app.config["DEVICE_REGISTRY"]  # type: ignore[no-any-return]


def _device_status() -> dict[str, dict[str, Any]]:
    return current_app.config["DEVICE_STATUS"]  # type: ignore[no-any-return]


def _device_by_token(token: str) -> Device | None:
    """Find a TRMNL device instance by its ``access_token``.

    Token comparison is case-sensitive constant-time-ish (we iterate
    every instance, not just on match), which is fine for the
    typical single-digit-instance count. ``secrets.compare_digest``
    isn't strictly necessary at this volume but using it keeps the
    code honest if the fleet grows."""
    import secrets

    if not token:
        return None
    matched: Device | None = None
    for dev in _devices().all():
        if dev.kind_of != TRMNL_KIND_ID:
            continue
        stored = dev.manifest.get("access_token")
        if not isinstance(stored, str) or not stored:
            continue
        if secrets.compare_digest(stored, token):
            matched = dev
            # Don't break, finish the scan so timing leaks nothing.
    return matched


def _device_by_mac(mac: str) -> Device | None:
    """Find a TRMNL device instance by its stored ``mac``.

    Matches Terminus's primary identity model: native TRMNL firmware
    always sends its MAC in the ``Id`` header, the access token is
    informational. We look up by MAC first (case-insensitive, the
    XIAO firmware sends uppercase + colons, KOReader users don't
    set this at all)."""
    if not mac:
        return None
    target = mac.upper()
    for dev in _devices().all():
        if dev.kind_of != TRMNL_KIND_ID:
            continue
        stored = dev.manifest.get("mac")
        if not isinstance(stored, str) or not stored:
            continue
        if stored.upper() == target:
            return dev
    return None


def _device_by_request() -> Device | None:
    """Resolve the requesting device using BYOS auth precedence.

    MAC first (``Id`` header) since that's what native TRMNL firmware
    uses as its primary identity, then access token as the fallback
    for KOReader and any client without a MAC."""
    mac = request.headers.get("Id") or request.headers.get("id") or ""
    if mac:
        device = _device_by_mac(mac)
        if device is not None:
            return device
    token = _request_token()
    if token:
        return _device_by_token(token)
    return None


# Known TRMNL hardware models → panel pixel dimensions. The native
# firmware's ``buildSetupHeaders`` (see
# https://github.com/usetrmnl/trmnl-firmware/blob/main/lib/trmnl/src/api-client/request_headers.cpp)
# sends ``ID / Content-Type / FW-Version / Model`` on /api/setup; it
# does NOT include Width/Height (those land on /api/display). So we
# look up panel dims from the ``Model`` header at setup time instead
# of waiting for the first display poll, otherwise the device's
# stored panel size stays at the original-TRMNL 800x480 default and
# the composer designs at the wrong canvas size even though the
# rendered PNG comes out right (the /api/display path reads dims
# from the request headers).
#
# Models sourced from https://github.com/usetrmnl/trmnl-firmware
# (``include/config.h``, ``DEVICE_MODEL`` defines) and panel pixel
# counts from https://github.com/bitbank2/FastEPD (``BB_PANEL_*``
# entries in ``FastEPD.inl``). Add new entries as the lineup grows;
# unknown models fall through to the original-TRMNL default.
_TRMNL_MODEL_PANEL: dict[str, tuple[int, int]] = {
    # Original TRMNL (BOARD_OG and friends), 7.5" 800x480 e-paper.
    "TRMNL": (800, 480),
    "og": (800, 480),
    # TRMNL X (BOARD_TRMNL_X, BOARD_TRMNL_X_EPDIY), 13.3" 1872x1404.
    "x": (1872, 1404),
}


def _panel_dims_from_model(model: str | None) -> tuple[int, int]:
    """Resolve a panel pixel size from the ``Model`` header. Matches
    keys case-insensitively; unknown models default to the original
    TRMNL panel so legacy clients keep working unchanged."""
    if not model:
        return 800, 480
    key = model.strip()
    if key in _TRMNL_MODEL_PANEL:
        return _TRMNL_MODEL_PANEL[key]
    lowered = key.lower()
    if lowered in _TRMNL_MODEL_PANEL:
        return _TRMNL_MODEL_PANEL[lowered]
    return 800, 480


def _auto_provision_by_mac(mac: str) -> Device | None:
    """Auto-create a TRMNL instance for a fresh device polling with a
    novel MAC. Matches Terminus's first-touch behaviour, the device
    polls, the server creates a record, the device gets a frame on
    its very next iteration. Returns the new ``Device`` on success,
    ``None`` on auto-create failure (logged; caller falls back to
    the discovery-cache path)."""
    from app import device_service

    registry = current_app.config["DEVICE_REGISTRY"]
    data_root = current_app.config["DEVICE_DATA_ROOT"]
    renderers = current_app.config["RENDERER_REGISTRY"]

    mac_clean = "".join(c for c in mac.lower() if c.isalnum())
    instance_id = f"trmnl_{mac_clean[-6:]}" if mac_clean else "trmnl_new"
    model = request.headers.get("Model") or "TRMNL"
    # Width/Height headers are present on /api/display but not
    # /api/setup, so we look them up by Model. Width/Height still win
    # if a non-native client (e.g. KOReader with png-width / png-height
    # plumbed into setup) sends them, since the request is the source
    # of truth when it actually carries the data.
    panel_w_default, panel_h_default = _panel_dims_from_model(model)
    try:
        panel_w = int(
            request.headers.get("Width") or request.headers.get("png-width") or panel_w_default
        )
        panel_h = int(
            request.headers.get("Height") or request.headers.get("png-height") or panel_h_default
        )
    except ValueError:
        panel_w, panel_h = panel_w_default, panel_h_default
    result = device_service.create_instance(
        devices=registry,
        renderers=renderers,
        data_root=data_root,
        instance_id=instance_id,
        kind_id=TRMNL_KIND_ID,
        name=model,
        panel_overrides={"w": panel_w, "h": panel_h},
        mac=mac,
        api_key_strength="native",
    )
    if not result.ok or result.device is None:
        logger.warning(
            "trmnl: auto-provision failed for mac=%s: %s",
            mac,
            result.error,
        )
        return None
    logger.info(
        "trmnl: auto-provisioned device %r (mac=%s, model=%r)",
        result.device.id,
        mac,
        request.headers.get("Model"),
    )
    return result.device


# -- request shape ------------------------------------------------------


def _request_token() -> str:
    """Pull the access token from the request headers, tolerating the
    handful of spellings BYOS clients use."""
    for k in ("access-token", "Access-Token", "Authorization"):
        v = request.headers.get(k)
        if v:
            # Authorization: Bearer <token>, strip the scheme if present.
            return v.removeprefix("Bearer ").strip()
    return ""


def _headers_as_dict() -> dict[str, str]:
    """Flatten request.headers to a plain dict for parse_status."""
    return dict(request.headers.items())


def _requested_panel_dims(device: Device) -> tuple[int, int]:
    """The dimensions the client wants the next PNG at.

    Tries the BYOS-spec headers (``png-width`` / ``png-height`` for
    KOReader, ``Width`` / ``Height`` for native TRMNL) before falling
    back to the device's manifest panel block. Lets a single TRMNL
    instance drive multiple physical panels of different sizes if the
    user paastes the same token into more than one client, each
    client tells us what size it wants and the server obliges."""
    for w_key, h_key in (("png-width", "png-height"), ("Width", "Height")):
        raw_w = request.headers.get(w_key)
        raw_h = request.headers.get(h_key)
        if raw_w and raw_h:
            try:
                return max(1, int(raw_w)), max(1, int(raw_h))
            except ValueError:
                continue
    panel = device.panel or {}
    return int(panel.get("w") or 800), int(panel.get("h") or 480)


def _adopt_reported_panel(device: Device, w: int, h: int) -> None:
    """Persist the client's reported pixel size onto the device panel.

    BYOS clients declare the exact buffer they paint on every poll
    (KOReader sends ``png-width`` / ``png-height`` = the e-reader's
    physical screen, e.g. 758×1024 on a Paperwhite 2; native TRMNL
    firmware sends ``Width`` / ``Height``). The trmnl renderers need
    that as the panel's ``native_w / native_h`` so they can turn the
    composition onto the real screen: without it a portrait Kindle
    stretched every landscape frame across its long axis, which is
    exactly the squashed-landscape-into-portrait report.

    On the first adoption (no native block yet) the composition dims
    are seeded to the swapped pair matching the stored orientation, so
    "Rotation: 90°" is an exact 90° turn of the composition onto the
    reported buffer rather than a letterboxed stretch. Only the first
    adoption touches the composition dims (and only when they still
    equal the kind's defaults — a deliberate custom canvas wins);
    later polls only keep the native block in sync.

    Best-effort: any failure leaves the stored panel untouched and is
    caught by the caller."""
    # Tolerate the odd zero / garbage firmware header, cap e-reader
    # sizes generously (largest known: Scribe 1860×2480).
    if not w or not h or w <= 0 or h <= 0:
        return
    if w > 4096 or h > 4096:
        return
    panel = device.panel or {}
    try:
        cur_nw = int(panel["native_w"]) if panel.get("native_w") is not None else None
        cur_nh = int(panel["native_h"]) if panel.get("native_h") is not None else None
    except (TypeError, ValueError):
        cur_nw = cur_nh = None
    if cur_nw == w and cur_nh == h:
        # Already adopted / nothing to sync. Keep the write out of the
        # steady-state poll loop.
        return

    inst_file = device.path
    try:
        raw = json.loads(inst_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    block = dict(raw.get("panel") or {})
    if block.get("native_w") is None and block.get("native_h") is None:
        # First report from this client. Seed the composition canvas to
        # the swapped pair for the stored orientation, unless the user
        # deliberately changed the dims from the kind's defaults.
        defaults = _kind_default_panel_dims(device)
        try:
            cur_dims = (int(block["w"]), int(block["h"]))
        except (KeyError, TypeError, ValueError):
            cur_dims = None
        orientation = str(block.get("orientation") or "landscape").lower()
        # Composition canvas = the reported buffer re-ordered to the
        # stored orientation's aspect: landscape -> wide, portrait ->
        # tall. The renderer then turns that canvas onto the buffer (90°
        # only when the two disagree on aspect), so a landscape
        # orientation on a portrait Kindle seeds 1024×758, and a
        # landscape TRMNL OG (800×480 buffer) keeps 800×480.
        if orientation.startswith("portrait"):
            desired = (h, w) if w > h else (w, h)
        else:
            desired = (h, w) if h > w else (w, h)
        if cur_dims is None or cur_dims == defaults or cur_dims in ((w, h), (h, w)):
            block["w"], block["h"] = desired
    block["native_w"], block["native_h"] = w, h
    raw["panel"] = block
    try:
        inst_file.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return
    # Reload the instance in place so the push pipeline's
    # ``device_panel`` sees the native block on its next render. The
    # renderer clone set doesn't change (same kind, same renderers), so
    # no clone churn.
    devices = current_app.config.get("DEVICE_REGISTRY")
    data_root = current_app.config.get("DEVICE_DATA_ROOT")
    if devices is not None and data_root is not None:
        from app import device_loader

        devices.devices.pop(device.id, None)
        device_loader.load_instance_file(devices, inst_file=inst_file, data_root=data_root)


def _kind_default_panel_dims(device: Device) -> tuple[int, int] | None:
    """The kind's shipped panel template dims, or None when the kind
    doesn't declare a panel (so ``_adopt_reported_panel`` can tell a
    still-default canvas from a deliberately custom one)."""
    registry = current_app.config.get("DEVICE_REGISTRY")
    if registry is None:
        return None
    kind = registry.devices.get(str(getattr(device, "kind_of", "") or ""))
    if kind is None:
        return None
    kind_panel = kind.manifest.get("panel") or {}
    try:
        return int(kind_panel["w"]), int(kind_panel["h"])
    except (KeyError, TypeError, ValueError):
        return None


# -- status capture -----------------------------------------------------


def _update_status_from_headers(device: Device) -> dict[str, Any]:
    """Run the device's ``parse_status`` against the request headers
    and cache the result, same as the MQTT subscribers do on every
    heartbeat. Returns the merged parsed dict so the caller can log it.

    Mirrors ``transport_wiring._subscribe_device_status``: parsed fields
    are merged over the prior cached dict (so a header-light poll doesn't
    blank the last-known battery / RSSI), and the HA discovery instance -
    if running, is notified so TRMNL clients show up in Home Assistant
    alongside MQTT devices with the same Battery / Signal / IP sensors."""
    from app.transport_wiring import merge_status_parsed

    payload = json.dumps(_headers_as_dict()).encode("utf-8")
    parsed = device.parse_status(payload)
    cache = _device_status()
    prev = cache.get(device.id, {}).get("parsed", {})
    merged = merge_status_parsed(prev, parsed)
    received_at = time.time()
    cache[device.id] = {
        "received_at": received_at,
        "parsed": merged,
    }
    # Battery history: same hook the MQTT subscriber writes through, so
    # TRMNL poll-based devices accumulate the same drain-rate samples
    # the admin page + widget expect.
    #
    # Reads ``merged`` not ``parsed`` so the
    # ``merge_status_parsed``-derived ``battery_pct`` (from
    # ``battery_mv`` via the LiPo curve) is visible. TRMNL kit firmware
    # only sends the voltage header, so parse_status returns
    # ``battery_pct=None`` and the derivation runs during the merge.
    battery_history = current_app.config.get("BATTERY_HISTORY")
    if (
        battery_history is not None
        and "error" not in parsed
        and merged.get("battery_pct") is not None
    ):
        try:
            battery_history.record(
                device.id,
                pct=int(merged["battery_pct"]),
                battery_mv=(
                    int(merged["battery_mv"]) if merged.get("battery_mv") is not None else None
                ),
                timestamp=received_at,
            )
        except (TypeError, ValueError):
            pass
        except Exception:
            logger.exception("battery_history: record failed for %s", device.id)
    ha = current_app.config.get("HA_DISCOVERY")
    if ha is not None:
        try:
            ha.note_device_heartbeat(device.id, merged)
        except Exception:
            logger.exception("HA discovery: heartbeat notify failed for %s", device.id)
    return merged


# -- routes -------------------------------------------------------------


@bp.get("/api/display")
def display() -> Response | tuple[Response, int]:
    """Steady-state polling endpoint. The client polls; we hand back a
    JSON envelope pointing at the latest render artifact for the
    device the request identifies.

    Auth precedence matches Terminus's official BYOS contract: MAC
    address (``Id`` header) first, access token as a fallback. Native
    TRMNL firmware always sends the MAC; the token is informational
    on that path. KOReader doesn't send a MAC, so it stays
    token-authenticated."""
    device = _device_by_request()
    # If the polling device sent its MAC but we don't have a record
    # for it (common when the firmware cached a stale api_key in flash
    # and never re-calls /api/setup), auto-provision right here.
    # Terminus does the same: any MAC-bearing client polling /api/
    # display can self-register. The device's own stored token
    # doesn't matter on subsequent polls because MAC auth is primary.
    if device is None:
        mac = (request.headers.get("Id") or request.headers.get("id") or "").strip()
        if mac:
            device = _auto_provision_by_mac(mac)
    token = _request_token()  # Still used for the discovery-cache fallback below.
    if device is None:
        logger.info(
            "trmnl: rejected /api/display from %s (token=%r, ua=%r)",
            request.remote_addr,
            token[:8] + "…" if len(token) > 8 else token,
            request.headers.get("User-Agent"),
        )
        # Drop the unknown token into the discovery cache so the
        # Settings → Devices → Discovered strip can offer one-click
        # pairing, same UX as MQTT-side auto-discovery, just with
        # the token preserved (the user already has it on their
        # device; making them re-paste a fresh one would be silly).
        if token:
            cache = current_app.config.get("DISCOVERY_CACHE")
            if cache is not None:
                record_trmnl_discovery(
                    cache,
                    token=token,
                    headers=_headers_as_dict(),
                    remote_addr=request.remote_addr,
                )
        # Same status code TRMNL native uses for "unknown device".
        return (
            jsonify(
                {
                    "status": 404,
                    "error": "Unknown access token. Pair this device under Settings → Devices.",
                }
            ),
            404,
        )

    _update_status_from_headers(device)
    # ``refresh_rate`` is the next-poll cadence. The configured
    # ``refresh_rate_s`` is the ceiling; app.device_poll pulls it earlier
    # for the next projected dashboard change or a widget staleness hint,
    # reshapes it onto a wake-alignment grid, and stretches it to sleep
    # through a quiet window the device asked to sleep through. Same
    # decision the v1 REST path's ``next_poll_s`` uses. A fault anywhere
    # in that degrades to the static configured value.
    configured_refresh = _refresh_rate_for(device)
    try:
        refresh_rate = device_poll.next_poll_s(device, configured_s=configured_refresh)
    except Exception:
        logger.exception("trmnl: dynamic refresh_rate failed for device=%s", device.id)
        refresh_rate = configured_refresh
    w, h = _requested_panel_dims(device)
    # The client just told us the pixel buffer it paints (png-width /
    # png-height). Persist it as the panel's native dims so the trmnl
    # renderers rotate the composition onto the real screen instead of
    # serving a frame the client's scaler then stretches out of shape
    # (squashed landscape on a portrait Kindle). Best-effort: a failure
    # here must never break the poll.
    try:
        _adopt_reported_panel(device, w, h)
    except Exception:
        logger.exception("trmnl: panel adoption failed for device=%s", device.id)

    # Real render path: PushManager records the most recent successful
    # publish per device. Serve that artifact (already on disk under
    # /renders/<digest>.<ext>) so the client paints exactly what the
    # composer last produced. Falls back to the size-matched placeholder
    # when nothing's been rendered yet, fresh install, post-restart
    # before the first scheduled push, etc., so the client still has
    # something to paint instead of looping on an error.
    push_mgr = current_app.config.get("PUSH_MANAGER")
    latest = push_mgr.latest_render_for(device.id) if push_mgr is not None else None
    if latest:
        image_url = f"{request.url_root.rstrip('/')}/renders/{latest['filename']}"
        filename = latest["filename"]
    else:
        image_url = url_for(
            "trmnl_api.placeholder_png",
            device_id=device.id,
            width=w,
            height=h,
            _external=True,
        )
        filename = f"placeholder-{device.id}-{w}x{h}.png"

    # One line per successful poll, so a poll can be matched to the render
    # it collected and the cadence it was told to come back on. Rejected
    # and unknown-token polls were already logged; this is the other half.
    logger.info(
        "trmnl: served /api/display to device=%s filename=%s refresh_rate=%s",
        device.id,
        filename,
        refresh_rate,
    )

    # Envelope shape matches Terminus's official /api/display response
    # so any TRMNL-compatible firmware reads the same fields it would
    # against the upstream BYOS server. Fields populated:
    #
    #   filename, image_url           render artefact
    #   image_url_timeout             HTTP timeout (0 = firmware default)
    #   refresh_rate                  next poll cadence in seconds
    #   special_function              "sleep" (deep-sleep until next poll;
    #                                 matches Terminus's default)
    #   firmware_url, firmware_version, update_firmware, reset_firmware
    #                                 OTA hooks; Tier 2 (#11) wires them
    #   maximum_compatibility         false = let firmware use modern
    #                                 features (partial refresh, etc.).
    #                                 Matches Terminus's per-device flag.
    #   friendly_id                   stable human-readable id
    manifest_friendly = device.manifest.get("friendly_id")
    friendly = manifest_friendly if isinstance(manifest_friendly, str) else device.id
    return jsonify(
        {
            "status": 0,
            "filename": filename,
            "image_url": image_url,
            "image_url_timeout": 0,
            "refresh_rate": refresh_rate,
            "special_function": "sleep",
            "firmware_url": "",
            "firmware_version": "",
            "update_firmware": False,
            "reset_firmware": False,
            "maximum_compatibility": False,
            "friendly_id": friendly,
        }
    )


@bp.get("/api/setup/")
@bp.get("/api/setup")
def setup() -> Response:
    """First-boot / pairing endpoint.

    Implements the official Terminus BYOS contract:

    * Device sends its MAC in the ``Id`` header.
    * Server looks up an existing device by MAC. If found, returns its
      api_key + friendly_id.
    * If not found, auto-creates a fresh TRMNL instance keyed by MAC,
      mints a high-entropy api_key + friendly_id, persists, and
      returns them.

    Result: a box-fresh TRMNL device that's been pointed at a Tesserae
    URL (via the firmware's captive portal) self-provisions on first
    boot. No admin click, no token typed into a config screen, no
    TRMNL mobile app, the user just sees the new device appear in
    Settings → Devices a few seconds after it joins WiFi.

    KOReader / Kindle callers that don't send a MAC fall back to the
    pre-0.44.1 path: token preserved if provided, discovery cache
    entry written, admin clicks Register. This stays the simplest
    flow for the type-the-token-on-the-Kindle case.
    """
    mac = (request.headers.get("Id") or request.headers.get("id") or "").strip()
    # Known device by MAC? Hand back its existing credentials.
    if mac:
        existing = _device_by_mac(mac)
        if existing is not None:
            api_key = str(existing.manifest.get("access_token") or "")
            manifest_friendly = existing.manifest.get("friendly_id")
            friendly = manifest_friendly if isinstance(manifest_friendly, str) else existing.id
            return _setup_response(api_key, friendly, existing.id)

    # Known device by token? (Rare on /api/setup; mostly KOReader.)
    token = _request_token()
    if token:
        existing = _device_by_token(token)
        if existing is not None:
            api_key = token
            manifest_friendly = existing.manifest.get("friendly_id")
            friendly = manifest_friendly if isinstance(manifest_friendly, str) else existing.id
            return _setup_response(api_key, friendly, existing.id)

    # Unknown device. If we have a MAC, auto-create a TRMNL instance.
    # The device is implicitly trusted (it's on the LAN) and the
    # admin gets to see + manage it after the fact rather than having
    # to approve it up front.
    if mac:
        new_device = _auto_provision_by_mac(mac)
        if new_device is not None:
            api_key = str(new_device.manifest.get("access_token") or "")
            manifest_friendly = new_device.manifest.get("friendly_id")
            friendly = manifest_friendly if isinstance(manifest_friendly, str) else new_device.id
            return _setup_response(api_key, friendly, new_device.id)

    # No MAC, or auto-create failed. Fall back to the discovery flow:
    # mint a token, record, hand it to the device, let admin
    # one-click Register from the Discovered strip.
    from app.device_service import generate_access_token

    registry = current_app.config.get("DEVICE_REGISTRY")
    if registry is None:
        api_key = "registry-unavailable"
        friendly = "unpaired"
    else:
        api_key = generate_access_token(registry)
        cache = current_app.config.get("DISCOVERY_CACHE")
        if cache is not None:
            record_trmnl_discovery(
                cache,
                token=api_key,
                headers=_headers_as_dict(),
                remote_addr=request.remote_addr,
            )
        friendly = "pending"
    return _setup_response(api_key, friendly, friendly)


def _setup_response(api_key: str, friendly: str, image_slug: str) -> Response:
    """Build the BYOS /api/setup JSON response. ``image_slug`` keeps
    the placeholder PNG URL stable per device, falling back to the
    friendly_id for unpaired callers."""
    # Reflect the client's requested panel dims back in the hello image
    # so the setup-time fetch confirms end-to-end at the right size.
    # Native TRMNL firmware sends Width/Height, KOReader sends
    # png-width/png-height.
    try:
        w = int(request.headers.get("Width") or request.headers.get("png-width") or 800)
        h = int(request.headers.get("Height") or request.headers.get("png-height") or 480)
    except ValueError:
        w, h = 800, 480
    image_url = url_for(
        "trmnl_api.placeholder_png",
        device_id=image_slug,
        width=w,
        height=h,
        _external=True,
    )
    return jsonify(
        {
            "status": 200,
            "api_key": api_key,
            "friendly_id": friendly,
            "image_url": image_url,
            "filename": f"setup-{image_slug}-{w}x{h}.png",
            # Terminus includes a friendly welcome string. Mirror that
            # so a TRMNL firmware that parses it gets the same shape.
            "message": "Welcome to Tesserae.",
        }
    )


@bp.post("/api/log/")
@bp.post("/api/log")
def device_log() -> Response:
    """Client-side diagnostics POST.

    Terminus's official log payload is either::

        {"logs": [{"creation_timestamp": "...", "message": "...", ...}, ...]}

    or the nested form::

        {"log": {"logs_array": [...]}}

    Both carry one entry per device-side log line. We extract whichever
    shape the firmware sent, log each entry individually so they stay
    readable in journald / docker logs, and return 200. Bodies we
    can't parse as either shape still log as raw text rather than
    erroring, native TRMNL firmware refuses to continue polling if
    /api/log/ 404s (or 500s), so we always succeed.

    Per-device log persistence is a follow-up; for now the server log
    is the only surface."""
    body = request.get_data(cache=False) or b""
    token = _request_token()
    device = _device_by_token(token)
    target = device.id if device else "unknown"

    entries = _extract_log_entries(body)
    if entries:
        for idx, entry in enumerate(entries):
            ts = entry.get("creation_timestamp") if isinstance(entry, dict) else None
            ts_label = f"[{ts}] " if isinstance(ts, str | int) and ts else ""
            logger.info(
                "trmnl: /api/log/ from %s entry %d/%d %s%s",
                target,
                idx + 1,
                len(entries),
                ts_label,
                _safe_log_repr(entry),
            )
    else:
        try:
            decoded = body.decode("utf-8", errors="replace")[:1024]
        except Exception:
            decoded = repr(body[:1024])
        logger.info("trmnl: /api/log/ from %s (%d bytes): %s", target, len(body), decoded)
    return jsonify({"status": 200})


def _extract_log_entries(body: bytes) -> list[Any]:
    """Return the Terminus-shaped log entry list, or ``[]`` when the
    body isn't JSON or doesn't match either documented shape.

    Documented shapes (see Terminus's ``StoreDeviceLogRequest``):

    * ``{"logs": [...]}`` (flat list of entries)
    * ``{"log": {"logs_array": [...]}}`` (nested under ``log`` key)
    """
    if not body:
        return []
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, dict):
        return []
    flat = decoded.get("logs")
    if isinstance(flat, list):
        return flat
    nested = decoded.get("log")
    if isinstance(nested, dict):
        arr = nested.get("logs_array")
        if isinstance(arr, list):
            return arr
    return []


def _safe_log_repr(entry: Any) -> str:
    """Render a single log entry as a single-line string for journald /
    docker, capping length so a misbehaving client can't flood disk."""
    if isinstance(entry, dict):
        try:
            text = json.dumps(entry, separators=(",", ":"))
        except (TypeError, ValueError):
            text = repr(entry)
    else:
        text = repr(entry)
    return text[:1024]


@bp.post("/api/log/level/")
@bp.post("/api/log/level")
def device_log_level() -> Response:
    """BYOS log-level config endpoint.

    Some TRMNL native firmwares query this on boot to learn what log
    verbosity the server wants. Tesserae doesn't drive remote log
    levels (we just record whatever the device sends), but the
    firmware refuses to continue polling if this 404s, so we
    acknowledge with a sensible default and log the body."""
    body = request.get_data(cache=False) or b""
    token = _request_token()
    device = _device_by_token(token)
    target = device.id if device else "unknown"
    try:
        decoded = body.decode("utf-8", errors="replace")[:512]
    except Exception:
        decoded = repr(body[:512])
    logger.info(
        "trmnl: /api/log/level from %s (%d bytes): %s",
        target,
        len(body),
        decoded,
    )
    return jsonify({"status": 200, "log_level": "info"})


# -- placeholder image (until the renderer lands) -----------------------


@bp.get("/api/trmnl/placeholder/<device_id>/<int:width>x<int:height>.png")
def placeholder_png(device_id: str, width: int, height: int) -> Response:
    """Return a server-generated test pattern at the requested dims.

    Stand-in for the real render artifact, used until the trmnl_png
    renderer + latest-render map ship. Same shape as the recon
    server's hello image: thick border, diagonal X, centre crosshairs,
    corner brackets. Visible on any panel.

    Constrained to <= 4096px per axis so a malicious or buggy client
    can't OOM the box by requesting an enormous canvas."""
    width = max(1, min(width, 4096))
    height = max(1, min(height, 4096))

    from PIL import Image, ImageDraw

    img = Image.new("L", (width, height), 255)  # 8-bit greyscale, white
    draw = ImageDraw.Draw(img)

    # Thick border (scales with the smaller dim).
    border = max(4, min(width, height) // 60)
    draw.rectangle((0, 0, width - 1, border - 1), fill=0)
    draw.rectangle((0, height - border, width - 1, height - 1), fill=0)
    draw.rectangle((0, 0, border - 1, height - 1), fill=0)
    draw.rectangle((width - border, 0, width - 1, height - 1), fill=0)

    # Diagonal X corner-to-corner.
    draw.line((0, 0, width - 1, height - 1), fill=0, width=3)
    draw.line((width - 1, 0, 0, height - 1), fill=0, width=3)

    # Centre crosshair.
    cx, cy = width // 2, height // 2
    draw.line((0, cy, width - 1, cy), fill=0, width=1)
    draw.line((cx, 0, cx, height - 1), fill=0, width=1)

    # Device id stamped near top-left so the panel reads as
    # "this is YOUR device", not a generic test pattern.
    draw.text(
        (border + 8, border + 8),
        f"{device_id}\n{width} x {height}\nplaceholder, awaiting first render",
        fill=0,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    body = buf.getvalue()
    return Response(body, mimetype="image/png", headers={"Content-Length": str(len(body))})


# -- internals ----------------------------------------------------------


def _refresh_rate_for(device: Device) -> int:
    """The cadence the device should poll at, read from its saved
    settings (Settings → Devices → refresh_rate_s) with the manifest
    default as fallback.

    The fallback must come from the kind's config_schema, not a
    constant: the settings card renders the schema default in the
    form, so serving anything else means an instance that has never
    been saved polls at a rate the UI never showed. That skew is
    invisible until someone compares the form against the device's
    reported ``Refresh-Rate`` header and concludes the setting
    "reverted"."""
    from app.state.settings_store import SettingsStore

    store: SettingsStore = current_app.config["SETTINGS_STORE"]
    schema = device.config_schema or {}
    spec = schema.get("refresh_rate_s") if isinstance(schema, dict) else None
    default = spec.get("default") if isinstance(spec, dict) else None
    if not isinstance(default, int) or default <= 0:
        default = 900
    fields = [{"name": "refresh_rate_s", "type": "number", "default": default}]
    values = store.get_for_runtime("devices", device.id, fields)
    try:
        return int(values.get("refresh_rate_s") or default)
    except (TypeError, ValueError):
        return default


def register(app: Flask) -> None:
    app.register_blueprint(bp)
