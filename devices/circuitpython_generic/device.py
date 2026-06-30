"""circuitpython_generic device contract.

REST-polled e-paper client for CircuitPython microcontrollers
(Pico W, Pico 2 W, Feather S2/S3, ESP32-S3 builds running
CircuitPython, etc.). The wire format is an indexed PNG produced
by the circuitpython_png renderer, which adafruit_imageload can
stream-decode without an on-device dither pass.

Pairing follows the v1 REST flow documented in
docs/dev/client-protocol.md:
    1. POST /api/v1/device/discover (no auth) with kind, MAC, panel dims
    2. Admin clicks Register in Settings → Devices
    3. Firmware retries discover; server returns a device_token

Steady state:
    * GET /api/v1/device/<id>/frame to find the latest render
    * Download the PNG bytes from the returned URL (no auth)
    * Paint, then POST /api/v1/device/<id>/status with battery / rssi /
      ip / sleep hints; the response carries next_poll_s

The kind is intentionally broad. A maintainer who ships a board-
specific client (e.g. ``circuitpython_pico_w_inky_73``) is welcome
to add a tighter kind alongside; this one is the catch-all so a
generic CircuitPython firmware registers cleanly out of the box.
"""

from __future__ import annotations

import json
from typing import Any

SLEEP_INTERVAL_MIN_S = 30
SLEEP_INTERVAL_MAX_S = 7 * 24 * 60 * 60


# Status field names the v1 REST protocol uses. Anything not in this
# allow-list still rides along in the parsed dict (the Settings card
# renders unknown keys as generic key/value pairs); the allow-list
# just gives the well-known fields a typed pass through int/float
# coercion so a stringy "3850" still ends up as a number on the
# device card.
_INT_FIELDS = ("battery_mv", "battery_pct", "rssi", "next_sleep_s", "panel_w", "panel_h")
_FLOAT_FIELDS = ("sleep_until",)
_STR_FIELDS = ("ip", "mac", "fw_version", "model")


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode a /status heartbeat body into the shape the device card
    expects.

    Empty bodies (a client that paints and immediately sleeps without
    posting status) return an empty dict rather than raising, so the
    last-seen timestamp still ticks on every poll even when there's
    nothing to report. Non-JSON bodies surface as ``{"raw": ...}`` so
    a firmware bug doesn't silently swallow the heartbeat."""
    if not payload:
        return {}
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": payload.decode("utf-8", errors="replace")}
    if not isinstance(decoded, dict):
        return {"raw": decoded}

    out: dict[str, Any] = dict(decoded)
    for k in _INT_FIELDS:
        if k in out:
            out[k] = _maybe_int(out[k])
    for k in _FLOAT_FIELDS:
        if k in out:
            out[k] = _maybe_float(out[k])
    for k in _STR_FIELDS:
        if k in out and out[k] is not None and not isinstance(out[k], str):
            out[k] = str(out[k])
    return out


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Check the config form's payload before Tesserae echoes it back
    in the next /status response. A typoed cadence here would pin the
    firmware to that value until the next manual fix, so the bounds
    match the manifest's config_schema."""
    if "sleep_interval_s" not in payload:
        return False, "missing 'sleep_interval_s'"
    try:
        interval = int(payload["sleep_interval_s"])
    except (TypeError, ValueError):
        return False, "sleep_interval_s must be an integer"
    if interval < SLEEP_INTERVAL_MIN_S:
        return False, f"sleep_interval_s must be >= {SLEEP_INTERVAL_MIN_S} (got {interval})"
    if interval > SLEEP_INTERVAL_MAX_S:
        return False, f"sleep_interval_s must be <= {SLEEP_INTERVAL_MAX_S} (got {interval})"
    return True, None


def _maybe_int(value: Any) -> int | None:
    """Coerce stringy numbers ("3850") to int; drop genuinely bad values
    to None so the device card doesn't show "NaN" or similar."""
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
