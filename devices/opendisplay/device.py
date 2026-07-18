"""opendisplay device contract.

An OpenDisplay BLE tag driven by the tesserae-opendisplay bridge. The
bridge polls the REST frame endpoint, pulls the rendered PNG, and pushes
it to the tag over Bluetooth LE. Its heartbeats are JSON, the same shape
the Pi PNG client uses: Tesserae doesn't dictate the schema, whatever the
bridge publishes flows through and gets surfaced in the UI. The few
well-known keys (``state``, ``last_paint_at``, ``last_error``) are pulled
out of the parsed payload by the settings template when present.

REST-polled instances honour ``sleep_interval_s`` from the device config:
Tesserae echoes the configured cadence back to the client in the
``/api/v1/device/<id>/status`` response (``next_poll_s``), and
``_next_poll_s`` in ``app/rest_api.py`` pulls the value from settings.
MQTT-driven instances ignore the field (they wake on retained-frame
publishes), but the form is still presented so the user has one place to
configure cadence regardless of transport.
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
    """Decode the heartbeat JSON. Returns whatever the listener sent.

    On non-JSON payloads (or empty bytes), return a single ``raw`` key so
    the UI can still display something useful.
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
    """Check the payload makes sense before it goes back to the client.

    REST clients pick the new value up on the next ``/api/v1/device/<id>/
    status`` poll; a typoed cadence would otherwise pin the device to a
    minutes-long wake cycle until the next manual fix.
    """
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
