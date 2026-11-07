"""pi_client device contract: the unified Raspberry Pi client.

One kind for every Pi running dmellok/tesserae-device-pi, whichever Inky
panel it drives. The client reports the panel it detected when it pairs
(``panel_w`` / ``panel_h``, ``gamuts``), and the kind's ``colour_depths``
block does the rest: a Spectra 6 Impression that advertises ``spectra_6``
(stored canonically as ``waveshare_e6``) is served ``pi_bin``, the
panel-native 4-bpp buffer packed here and painted on the Pi with no
quantise; every other panel stays on ``pi_png``, which the Pi quantises
with the ``inky`` library.

The client must advertise only a gamut it has positively detected:
:func:`app.quantizer.canonicalise_gamut` maps an unknown label to
``waveshare_e6``, which would move a panel it can't paint onto ``pi_bin``.
ACeP 7-colour (``inky_7colour``) panels stay on PNG for now, although the
packer can target them: ``colour_depths`` holds one gamut per renderer.

The two older kinds (``pi_bin_client``, ``pi_png_client``) stay for the
installs already paired as them; the unified client paints whichever
format the server sends, so those keep working unchanged.

Heartbeats are JSON and flow through as-is, like the older Pi kinds: the
well-known keys (``state``, ``last_paint_at``, ``last_error``) are picked
out by the settings template and everything else shows as key/value.

``validate_config`` checks the fields the device card can push. A Pi is
mains-powered, so ``always_on`` defaults to on; the other keys mirror the
ESP firmware kinds' so the same server code drives both.
"""

from __future__ import annotations

import json
from typing import Any

# Bounds match config_schema in device.json (duplicated on purpose: the
# manifest drives the form, these guard what goes back to the client).
SLEEP_INTERVAL_MIN_S = 5
SLEEP_INTERVAL_MAX_S = 7 * 24 * 60 * 60
BUTTON_WAKE_MAX_S = 60
AWAKE_POLL_MIN_S = 5
AWAKE_POLL_MAX_S = 300


def parse_status(payload: bytes) -> dict[str, Any]:
    """Decode the heartbeat JSON. Returns whatever the client sent.

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


def _int_in(payload: dict[str, Any], key: str, lo: int, hi: int) -> str | None:
    """An error message when ``payload[key]`` is not a whole number in
    ``lo..hi``; ``None`` when it is (or the key is absent)."""
    if key not in payload:
        return None
    value = payload[key]
    if isinstance(value, bool):
        return f"{key} must be an integer"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return f"{key} must be an integer"
    if not lo <= n <= hi:
        return f"{key} must be {lo}..{hi} (got {n})"
    return None


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Check the payload makes sense before it goes back to the client."""
    if "sleep_interval_s" not in payload:
        return False, "missing 'sleep_interval_s'"
    for key, lo, hi in (
        ("sleep_interval_s", SLEEP_INTERVAL_MIN_S, SLEEP_INTERVAL_MAX_S),
        ("awake_poll_s", AWAKE_POLL_MIN_S, AWAKE_POLL_MAX_S),
        ("button_wake_s", 0, BUTTON_WAKE_MAX_S),
    ):
        err = _int_in(payload, key, lo, hi)
        if err is not None:
            return False, err
    for key in ("always_on", "touch_enabled"):
        if key in payload and not isinstance(payload[key], bool):
            return False, f"{key} must be a boolean"
    return True, None
