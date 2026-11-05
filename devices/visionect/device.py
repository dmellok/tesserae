"""visionect device contract.

A Visionect sign is pushed to by ``app.visionect``, which hosts the sign's own
TCP protocol on port 11113. Heartbeats are the JSON object that module builds
from each status packet (battery, signal, temperature, firmware, uptime), so
``parse_status`` only has to decode and sanity-check it. The one config value
is the sign's UUID, which binds a physical sign to this device.
"""

from __future__ import annotations

import json
from typing import Any

# Marks the protocol for app.visionect: hardware-catalog SKUs (Joan 6, ...)
# share this module, so it identifies every Visionect device without a
# registry lookup.
PROTOCOL = "visionect"

_NUMERIC = ("battery_pct", "battery_mv", "rssi", "temperature_c", "uptime_s")


def _uuid_ok(raw: str) -> bool:
    text = raw.strip().lower().replace("-", "")
    if len(text) != 32:
        return False
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode a heartbeat built by ``app.visionect``; drop non-numeric
    readings rather than failing the beat."""
    if not payload:
        return {}
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "status payload was not JSON"}
    if not isinstance(decoded, dict):
        return {"error": f"expected JSON object, got {type(decoded).__name__}"}
    out: dict[str, Any] = {}
    for key in _NUMERIC:
        value = decoded.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[key] = value
    # The next-wake estimate (app.visionect.status_heartbeat): an absolute time
    # or seconds of sleep for telemetry, and the stays-awake flag.
    for key in ("next_sleep_s", "sleep_until"):
        value = decoded.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            out[key] = value
    if decoded.get("stays_awake") is True:
        out["stays_awake"] = True
    for key in ("fw_version", "uuid", "connect_reason"):
        value = decoded.get(key)
        if isinstance(value, str) and value:
            out[key] = value[:64]
    return out


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """The UUID is optional (blank binds the first unknown sign within an
    hour), but when set it must be a 32-hex-digit UUID, with or without
    dashes."""
    minutes = payload.get("heartbeat_min")
    if minutes not in (None, "") and (
        isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440
    ):
        return False, "heart beat interval must be 1 to 1440 minutes"
    poll = payload.get("status_poll_min")
    if poll not in (None, "") and (
        isinstance(poll, bool) or not isinstance(poll, int) or not 5 <= poll <= 1440
    ):
        return False, "status check interval must be 5 to 1440 minutes"
    for key, label, low, high in (
        ("wifi_ssid", "Wi-Fi network name", 1, 32),
        ("wifi_psk", "Wi-Fi password", 8, 64),
    ):
        text = payload.get(key)
        if text not in (None, ""):
            if not isinstance(text, str) or not text.isascii() or not text.isprintable():
                return False, f"{label} must be plain ASCII text"
            if not low <= len(text) <= high:
                return False, f"{label} must be {low} to {high} characters"
    raw = payload.get("uuid")
    if raw is None or raw == "":
        return True, None
    if not isinstance(raw, str) or not _uuid_ok(raw):
        return False, "uuid must look like 36002000-1247-3830-3933-303600000000"
    return True, None
