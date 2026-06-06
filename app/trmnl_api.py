"""HTTP API for TRMNL-compatible clients.

TRMNL clients (jailbroken Kindles running the KOReader trmnl-display
plugin, native TRMNL hardware, any BYOS-compatible client) poll
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
    cache[device.id] = {
        "received_at": time.time(),
        "parsed": merged,
    }
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
    device the token identifies."""
    token = _request_token()
    device = _device_by_token(token)
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
    refresh_rate = _refresh_rate_for(device)
    w, h = _requested_panel_dims(device)

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

    return jsonify(
        {
            "status": 0,
            "image_url": image_url,
            "filename": filename,
            "refresh_rate": refresh_rate,
            "reset_firmware": False,
            "update_firmware": False,
            "firmware_url": "",
            "special_function": "none",
        }
    )


@bp.get("/api/setup/")
@bp.get("/api/setup")
def setup() -> Response:
    """First-boot / pairing endpoint.

    Native TRMNL firmware calls this on first wake to exchange its MAC
    for a server-issued ``api_key``. The KOReader plugin doesn't -
    its user pastes the token in directly. We respond with whatever
    token the client brought (or a placeholder) so any client looking
    for a setup ACK gets one.

    Real pairing, recognising a new MAC, creating a pending device,
    surfacing it in Settings → Devices for one-click register, is a
    follow-up. For now the user pre-creates the device + token in
    Tesserae and pastes the token into the client."""
    token = _request_token()
    device = _device_by_token(token)
    friendly = device.id if device else "unpaired"
    api_key = token if device else "paste-a-server-issued-token-into-your-client"
    # Reflect the client's requested panel dims back in the hello image
    # so the setup-time fetch confirms end-to-end at the right size.
    w_key, h_key = "png-width", "png-height"
    try:
        w = int(request.headers.get(w_key) or 800)
        h = int(request.headers.get(h_key) or 480)
    except ValueError:
        w, h = 800, 480
    image_url = url_for(
        "trmnl_api.placeholder_png",
        device_id=friendly,
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
            "filename": f"setup-{friendly}-{w}x{h}.png",
        }
    )


@bp.post("/api/log/")
@bp.post("/api/log")
def device_log() -> Response:
    """Optional client-side diagnostics POST. We accept anything,
    log the body for the admin, and return 200. Some BYOS clients
    refuse to continue polling if /api/log/ 404s, so the endpoint
    exists even though Tesserae doesn't use the bodies (yet)."""
    body = request.get_data(cache=False) or b""
    token = _request_token()
    device = _device_by_token(token)
    target = device.id if device else "unknown"
    try:
        decoded = body.decode("utf-8", errors="replace")[:1024]
    except Exception:
        decoded = repr(body[:1024])
    logger.info("trmnl: /api/log/ from %s (%d bytes): %s", target, len(body), decoded)
    return jsonify({"status": 200})


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
    default as fallback."""
    from app.state.settings_store import SettingsStore

    store: SettingsStore = current_app.config["SETTINGS_STORE"]
    fields = [{"name": "refresh_rate_s", "type": "number", "default": 900}]
    values = store.get_for_runtime("devices", device.id, fields)
    try:
        return int(values.get("refresh_rate_s") or 900)
    except (TypeError, ValueError):
        return 900


def register(app: Flask) -> None:
    app.register_blueprint(bp)
