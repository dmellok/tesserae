"""Status and configuration for stock Meural frames on the local network."""

from __future__ import annotations

import json
from typing import Any

from app.meural import valid_host


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    host = payload.get("host")
    if host not in (None, "") and not (isinstance(host, str) and valid_host(host.strip())):
        return False, "Frame address must be an IPv4 address or host name"
    value = payload.get("refresh_interval_s")
    if value not in (None, ""):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return False, "Preview renewal must be a whole number of seconds"
        try:
            seconds = int(value)
        except ValueError:
            return False, "Preview renewal must be a whole number of seconds"
        if not 5 <= seconds <= 3600:
            return False, "Preview renewal must be between 5 and 3600 seconds"
    return True, None


def parse_status(payload: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {"battery_pct": None}
    try:
        raw = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return {**out, "error": "status payload was not JSON"}
    if not isinstance(raw, dict):
        return {**out, "error": "expected a JSON object"}
    for key in (
        "fw_version",
        "orientation",
        "rssi",
        "lux",
        "backlight",
        "free_space",
        "screen",
        "screen_why",
        "error",
    ):
        if key in raw and isinstance(raw[key], (str, int, float)):
            out[key] = raw[key][:120] if isinstance(raw[key], str) else raw[key]
    return out
