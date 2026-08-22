"""crosspoint_gray device contract.

A CrossPoint e-reader painting a Tesserae dashboard as its sleep screen,
over the LAN, with no cloud component.

The panel is a four-level greyscale ramp: the firmware draws the image
twice, through its ``GRAYSCALE_LSB`` and ``GRAYSCALE_MSB`` planes, and
quantises each pixel to 0-3. That is the ``gray_4`` gamut, which is what
this kind's manifest declares. Registering as a generic CircuitPython
client left the panel on a mono default, and the extra levels were
discarded server-side before the file was written, so the reader painted
a 1-bit image on a panel that can show four.

The renderer is shared with the CircuitPython kinds rather than
duplicated. Its palette comes from the bound panel's gamut, so the same
pixel pipeline emits four levels here and one bit elsewhere; the wire
format (uncompressed indexed BMP) is identical, and forking it would mean
two copies of the quantiser to keep in step.

Pairing follows the v1 REST flow in docs/dev/client-protocol.md:

    1. POST /api/v1/device/discover with the reader's derived MAC. It has
       no real NIC, so it announces a stable address in the locally
       administered range (``02:``, derived from its efuse serial):
       unique and stable per device, and unable to collide with real
       hardware.
    2. Admin clicks Register in Settings -> Devices.
    3. The reader retries discover and receives a device_token.

    The 6-digit pairing code via POST /api/v1/device/register is the
    fallback when discovery is inconvenient.

Steady state is not a poll loop. The reader pulls
``GET /api/v1/device/<id>/frame.bmp`` when a book closes, in a single
request with no redirects, and POSTs a heartbeat to ``/status`` so the
device reads as alive and the scheduler keeps rendering for it. Plain
``http://`` on the LAN is required: the client refuses an https -> http
redirect.
"""

from __future__ import annotations

import json
from typing import Any

# Matches the manifest's config_schema. The reader fetches on book close
# rather than on a timer, so this bounds how long it may be silent before
# the Devices card calls it stale, not how often it wakes.
REFRESH_INTERVAL_MIN_S = 300
REFRESH_INTERVAL_MAX_S = 7 * 24 * 60 * 60


# Well-known status fields get typed coercion so a stringy "84" lands as a
# number on the device card. Anything else still rides along in the parsed
# dict and renders as a generic key/value pair.
_INT_FIELDS = ("battery_pct", "battery_mv", "rssi", "panel_w", "panel_h", "next_sleep_s")
_FLOAT_FIELDS = ("sleep_until",)
_STR_FIELDS = ("ip", "mac", "fw_version", "model")


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode a /status heartbeat body into the shape the device card expects.

    An empty body returns an empty dict rather than raising: the reader
    heartbeats on book close and may have nothing to report, and the
    last-seen timestamp should still tick. A non-JSON body surfaces as
    ``{"raw": ...}`` so a firmware bug is visible instead of swallowed.
    """
    if not payload:
        return {}
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": payload.decode("utf-8", errors="replace")}
    if not isinstance(decoded, dict):
        return {"raw": decoded}

    out: dict[str, Any] = dict(decoded)
    for key in _INT_FIELDS:
        if key in out:
            out[key] = _maybe_int(out[key])
    for key in _FLOAT_FIELDS:
        if key in out:
            out[key] = _maybe_float(out[key])
    for key in _STR_FIELDS:
        if key in out and out[key] is not None and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Check the config form before Tesserae echoes it back to the device."""
    if "sleep_interval_s" not in payload:
        return False, "missing 'sleep_interval_s'"
    try:
        interval = int(payload["sleep_interval_s"])
    except (TypeError, ValueError):
        return False, "sleep_interval_s must be an integer"
    if interval < REFRESH_INTERVAL_MIN_S:
        return False, f"sleep_interval_s must be >= {REFRESH_INTERVAL_MIN_S} (got {interval})"
    if interval > REFRESH_INTERVAL_MAX_S:
        return False, f"sleep_interval_s must be <= {REFRESH_INTERVAL_MAX_S} (got {interval})"
    return True, None


def _maybe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
