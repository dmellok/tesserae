"""opendisplay_ha device contract.

An OpenDisplay BLE tag driven through Home Assistant rather than a direct
BLE bridge. Tesserae renders a full-colour PNG, writes it into HA's media
folder, and calls the ``opendisplay.upload_image`` action (see
``app.opendisplay_ha``); HA's OpenDisplay integration owns the Bluetooth.

The device carries two config values: ``ha_device_id`` (which HA device to
target) and ``rotate``. Heartbeats are JSON, the same lenient shape the Pi
PNG client uses. These devices are push-driven, not polled, so there's no
sleep interval to honour.
"""

from __future__ import annotations

import json
from typing import Any

_ROTATIONS = {"0", "90", "180", "270"}


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode a heartbeat JSON blob; return ``{"raw": …}`` on non-JSON."""
    if not payload:
        return {"raw": ""}
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": payload.decode("utf-8", errors="replace")}
    return decoded if isinstance(decoded, dict) else {"raw": decoded}


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Validate the device config: an optional HA device id string and a
    rotation. Both are optional so a half-configured device still saves
    (the publisher just skips a device with no ``ha_device_id``)."""
    if "ha_device_id" in payload and not isinstance(payload["ha_device_id"], str):
        return False, "ha_device_id must be a string"
    rotate = payload.get("rotate")
    if rotate is not None and str(rotate) not in _ROTATIONS:
        return False, "rotate must be 0, 90, 180 or 270"
    return True, None
