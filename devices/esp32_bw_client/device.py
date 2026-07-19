"""esp32_bw_client device contract.

Sibling of :mod:`devices.esp32_client.device` for mono B/W e-paper
panels driven by the generic ESP32 BW firmware. The wire shape on
the status + config topics matches the ESP32 family, the physical-
side difference is that this kind binds to ``esp32_bw_bin`` (1-bpp
output) instead of ``esp32_bin`` (4-bpp Spectra 6).

The 4.2" Waveshare 400x300 is the canonical first target and the
manifest's default panel block, but the firmware reports its actual
panel dimensions in the heartbeat (``panel_w`` / ``panel_h``, with
``width`` / ``height`` accepted as aliases for firmware variants
that follow KOReader's naming). The discovery + registration path
picks those up automatically (``app.discovery`` reads
``parsed["panel_w"]`` / ``parsed["panel_h"]`` to pre-fill the
Discovered card's panel block), so a different-sized BW panel
registers in one click without manual editing.

Heartbeat payload::

    {"battery_mv": int, "battery_pct": int, "rssi": int, "ip": "...",
     "panel_w": int, "panel_h": int}

Config payload published from Tesserae::

    {"sleep_interval_s": int}

``validate_config`` rejects out-of-bounds sleep intervals before they
go on the wire. The firmware uses the value verbatim so a typoed
``sleep_interval_s = 10`` would burn the battery flat in hours.
"""

from __future__ import annotations

import json
from typing import Any

# Bounds match config_schema in device.json. Duplicated here on
# purpose: the manifest lives next to the form (UI affordance), the
# constants live next to validate_config (server-side guard), neither
# one trusts the other.
SLEEP_INTERVAL_MIN_S = 30
SLEEP_INTERVAL_MAX_S = 7 * 24 * 60 * 60

# Upper bound for the post-button stay-awake window (issue #123). Staying
# awake longer than this to catch repeat presses is never worth the battery.
BUTTON_WAKE_MAX_S = 60


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode and normalise the heartbeat. Always returns a dict with
    at least the well-known keys (None when missing), plus any extras
    the firmware decided to include.

    Identical to ``esp32_client.parse_status``; kept duplicated so the
    drop-a-folder device plugins stay self-contained instead of
    importing each other."""
    out: dict[str, Any] = {
        "battery_mv": None,
        "battery_pct": None,
        "rssi": None,
        "ip": None,
        # Panel dims: BW e-paper panels come in many sizes (2.9", 4.2",
        # 7.5", etc.) and the firmware reports its actual hardware
        # resolution on the heartbeat. ``app.discovery`` reads these
        # to pre-fill the Discovered card, so a 296x128 panel registers
        # at 296x128 instead of the manifest's default 400x300.
        "panel_w": None,
        "panel_h": None,
        # Optional smart-sync fields (issue #10). Firmware that
        # publishes either of these gives the server a more accurate
        # wake-time prediction than the configured ``sleep_interval_s``
        # fallback. ``sleep_until`` (unix seconds) is preferred over
        # ``next_sleep_s`` (relative seconds) because it bypasses
        # clock-skew math.
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
    # Accept ``width`` / ``height`` as aliases for ``panel_w`` /
    # ``panel_h`` so firmware variants following KOReader's naming
    # (and the TRMNL-family ``Width`` / ``Height`` headers) drop in
    # without translation. The canonical key wins if both are present.
    if "panel_w" not in decoded and "width" in decoded:
        decoded["panel_w"] = decoded["width"]
    if "panel_h" not in decoded and "height" in decoded:
        decoded["panel_h"] = decoded["height"]
    for key, coercer in (
        ("battery_mv", int),
        ("battery_pct", int),
        ("rssi", int),
        ("ip", str),
        ("panel_w", int),
        ("panel_h", int),
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
    # Button wake window (issue #123): optional, so the shared validator
    # accepts a form that predates the field. 0 disables the linger.
    if "button_wake_s" in payload:
        try:
            wake = int(payload["button_wake_s"])
        except (TypeError, ValueError):
            return False, "button_wake_s must be an integer"
        if not 0 <= wake <= BUTTON_WAKE_MAX_S:
            return False, f"button_wake_s must be 0..{BUTTON_WAKE_MAX_S} (got {wake})"
    return True, None
