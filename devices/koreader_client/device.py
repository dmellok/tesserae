"""koreader_client device contract.

E-readers running KOReader with the Tesserae plugin
(https://github.com/dmellok/tesserae-koreader). They speak the REST device
protocol under ``/api/v1/device``: pair once with a claim code, then on every
wake fetch the frame envelope (``If-None-Match`` so an unchanged dashboard
costs one small request), download the packed bytes, decode them on the
device, and heartbeat through ``/status``. ``sleep_interval_s`` from the
config schema is echoed back as ``next_poll_s`` so the plugin knows when to
wake.

The plugin decodes the same packed layouts the ESP32 firmware paints, so this
kind reuses the ``esp32_gray_bin`` (16-level, 4 bpp) renderer by default and
falls back to the 4-level and 1-bit packers for screens whose width does not
divide evenly for 4 bpp.

``parse_status`` is only reached through the REST status POST: the plugin
sends a small JSON object, and whatever it sends is surfaced on the device
card.
"""

from __future__ import annotations

import json
from typing import Any

SLEEP_INTERVAL_MIN_S = 60
SLEEP_INTERVAL_MAX_S = 7 * 24 * 60 * 60


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode the heartbeat JSON. Returns whatever the plugin sent.

    Non-JSON or empty payloads come back under a single ``raw`` key so the
    UI still has something to show.
    """
    if not payload:
        return {"raw": ""}
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": payload.decode("utf-8", errors="replace")}
    if not isinstance(decoded, dict):
        return {"raw": decoded}
    return decoded


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Guard the cadence before it is echoed back to the e-reader."""
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
