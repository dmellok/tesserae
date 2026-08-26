"""picpak_client device contract.

The PicPak firmware wakes on its sleep interval, fetches the current frame
over REST (or MQTT), paints the 400x300 4-colour BWRY panel via SPI,
publishes a heartbeat, and deep-sleeps. Panel bytes come from the stock
``esp32_bin`` renderer with ``gamut: "bwry_4"`` (native 2 bpp packer) and
``vflip: true`` (bottom-to-top hardware scan).

Expected status payload:

    {"battery_mv": int, "battery_pct": int, "rssi": int, "ip": "...",
     "fw_version": "...", "kind": "picpak_client",
     "panel_w": 400, "panel_h": 300,
     "sleep_interval_s": int, "next_sleep_s": int?,
     "sleep_until": int?, "wake_reason": "..."}

Config payload (published from Tesserae, subscribed by the firmware):

    {"sleep_interval_s": int}

Firmware source lives at https://github.com/varanu5/picpak-tesserae-client
and defines the wire schema this module normalises.
"""

from __future__ import annotations

import json
from typing import Any

# Bounds match config_schema in device.json. Duplicated on purpose:
# the manifest lives next to the form (UI affordance), the constants
# live next to validate_config (server-side guard), neither one trusts
# the other. Firmware SLEEP_INTERVAL_MIN_S is 30s (guards against a
# runaway 1s-poll loop draining the battery in hours); MAX is 7 days
# so a monthly-refresh dashboard doesn't accidentally slip out.
SLEEP_INTERVAL_MIN_S = 5
SLEEP_INTERVAL_MAX_S = 7 * 24 * 60 * 60


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode and normalise the heartbeat. Always returns a dict with at
    least the well-known keys (None when missing), plus any extras the
    firmware decided to include."""
    out: dict[str, Any] = {
        "battery_mv": None,
        "battery_pct": None,
        "rssi": None,
        "ip": None,
        # Smart-sync fields (issue #10). ``sleep_until`` (unix seconds)
        # is preferred over ``next_sleep_s`` (relative seconds) because
        # it bypasses clock-skew math.
        "sleep_until": None,
        "next_sleep_s": None,
    }
    if not payload:
        return out
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        out["error"] = "payload was not JSON"
        return out
    if not isinstance(decoded, dict):
        out["error"] = f"expected JSON object, got {type(decoded).__name__}"
        return out
    for key, coercer in (
        ("battery_mv", int),
        ("battery_pct", int),
        ("rssi", int),
        ("ip", str),
        ("sleep_until", float),
        ("next_sleep_s", int),
    ):
        if key in decoded:
            try:
                out[key] = coercer(decoded[key])
            except (TypeError, ValueError):
                out[key] = decoded[key]
    # Pass unknown fields through so the UI can display fw_version,
    # kind, panel_w, panel_h, wake_reason etc. without this module
    # having to know about each one.
    for key, value in decoded.items():
        if key not in out:
            out[key] = value
    return out


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Check the payload makes sense before it goes on the wire."""
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
