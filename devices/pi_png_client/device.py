"""pi_client device contract.

Pi-side listener heartbeats are expected to be JSON. Tesserae doesn't
dictate the schema, whatever the listener publishes flows through and
gets surfaced in the UI. The few well-known keys (``state``, ``last_paint_at``,
``last_error``) are pulled out of the parsed payload by the settings template
when they're present; everything else is shown as a generic key/value pair.

The Pi client has no remote config, sleep / display tuning lives on the
device. We omit ``config_topic`` from the manifest so the settings page
doesn't render an empty config form for it.
"""

from __future__ import annotations

import json
from typing import Any


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
