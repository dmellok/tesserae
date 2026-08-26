"""pico_bin_client device contract.

The Pico Plus 2 firmware (RP2350) wakes on its sleep interval, fetches
the retained .bin frame off ``tesserae/<device>/frame/bin``, paints the
landscape buffer onto the Pimoroni Inky-style Spectra 6 panel via SPI
(rotating + splitting the buffer between the panel's two controllers
on-device), publishes a heartbeat, then deep-sleeps. The heartbeat
carries the bits useful for surfacing battery + signal health in the
admin UI.

The parse / validate contract is identical to ``esp32_client`` (same
sleep_interval_s bounds, same well-known heartbeat keys, same smart-sync
optional fields). The kinds split because the renderer side differs:
pico_bin packs landscape-native (1600x1200 for the 13.3" Spectra 6)
while esp32_bin packs portrait-native (1200x1600). Sharing the
``parse_status`` + ``validate_config`` contract by code-cloning instead
of importing keeps the drop-a-folder plugin model intact, every device
is self-contained on disk.

Expected status payload:

    {"battery_mv": int, "battery_pct": int, "rssi": int, "ip": "..."}

Config payload (published from Tesserae, subscribed by the firmware):

    {"sleep_interval_s": int}

``validate_config`` rejects out-of-bounds sleep intervals before they go
on the wire, the firmware uses the value verbatim so a typoed
``sleep_interval_s = 10`` would burn the battery flat in hours.
"""

from __future__ import annotations

import json
from typing import Any

# Bounds match config_schema in device.json. Duplicated here on purpose:
# the manifest lives next to the form (UI affordance), the constants live
# next to validate_config (server-side guard), neither one trusts the other.
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
        # Optional smart-sync fields (issue #10). Firmware that publishes
        # either of these gives the server a more accurate wake-time
        # prediction than the configured ``sleep_interval_s`` fallback.
        # ``sleep_until`` (unix seconds) is preferred over ``next_sleep_s``
        # (relative seconds) because it bypasses clock-skew math.
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
    # Pull the well-known fields with light coercion. Unknown fields
    # pass through so the UI can display them too.
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
