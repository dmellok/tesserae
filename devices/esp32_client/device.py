"""esp32_client device contract.

The firmware wakes on its sleep interval, fetches the current .bin frame,
publishes a heartbeat, paints any changed frame, then goes back to sleep. The
heartbeat carries the bits useful for surfacing battery + signal health
in the admin UI.

Expected status payload:

    {"battery_mv": int, "battery_pct": int, "rssi": int, "ip": "...",
     "temperature_c": float, "humidity_pct": float}

Environmental fields are optional. Temperature stays canonical in Celsius;
renderers with a unit preference can convert it at presentation time.

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
SLEEP_INTERVAL_MIN_S = 30
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
        "temperature_c": None,
        "humidity_pct": None,
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
        ("temperature_c", float),
        ("humidity_pct", float),
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


TOUCH_LINGER_MAX_S = 60

# Upper bound for the post-button stay-awake window (issue #123). Staying
# awake longer than this to catch repeat presses is never worth the battery.
BUTTON_WAKE_MAX_S = 60

# Bounds for the always-on poll cadence. The floor is well under
# ``SLEEP_INTERVAL_MIN_S`` on purpose: that floor exists to stop a typo
# flattening a battery, and a device that reports it can stay awake has
# already said it isn't on one. The ceiling is where staying awake stops
# buying anything over a deep-sleep cycle.
AWAKE_POLL_MIN_S = 5
AWAKE_POLL_MAX_S = 300


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
    # Button wake window (issue #123): optional, so the shared validator
    # accepts a form that predates the field. 0 disables the linger.
    if "button_wake_s" in payload:
        try:
            wake = int(payload["button_wake_s"])
        except (TypeError, ValueError):
            return False, "button_wake_s must be an integer"
        if not 0 <= wake <= BUTTON_WAKE_MAX_S:
            return False, f"button_wake_s must be 0..{BUTTON_WAKE_MAX_S} (got {wake})"
    # Touch fields (issue #49) only exist on kinds whose hardware entry
    # extends the schema with them (reTerminal E1003); optional here so
    # the shared protocol validator accepts every board's form.
    if "touch_enabled" in payload and not isinstance(payload["touch_enabled"], bool):
        return False, "touch_enabled must be a boolean"
    if "touch_linger_s" in payload:
        try:
            linger = int(payload["touch_linger_s"])
        except (TypeError, ValueError):
            return False, "touch_linger_s must be an integer"
        if not 0 <= linger <= TOUCH_LINGER_MAX_S:
            return False, f"touch_linger_s must be 0..{TOUCH_LINGER_MAX_S} (got {linger})"
    # Always-on (mains-powered panels): optional, so a form that predates
    # the field still validates.
    if "always_on" in payload and not isinstance(payload["always_on"], bool):
        return False, "always_on must be a boolean"
    if "awake_poll_s" in payload:
        try:
            poll = int(payload["awake_poll_s"])
        except (TypeError, ValueError):
            return False, "awake_poll_s must be an integer"
        if not AWAKE_POLL_MIN_S <= poll <= AWAKE_POLL_MAX_S:
            return (
                False,
                f"awake_poll_s must be {AWAKE_POLL_MIN_S}..{AWAKE_POLL_MAX_S} (got {poll})",
            )
    return True, None
