"""samsung_frame device contract.

A Samsung The Frame TV shown through its own Art Mode. ``app.samsung_frame``
pushes each new frame to the TV's Art app over its local websocket API and
polls the TV's REST info endpoint for the status shown on the device card.
There is no device firmware or heartbeat of its own: the poller hands
``parse_status`` a small JSON object it built from the TV's reply.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

KEEP_MIN, KEEP_MAX = 1, 10
MATTE_STYLES = {
    "none",
    "modernthin",
    "modern",
    "modernwide",
    "flexible",
    "shadowbox",
    "panoramic",
    "triptych",
    "mix",
    "squares",
}
MATTE_COLOURS = {
    "polar",
    "warm",
    "antique",
    "neutral",
    "sand",
    "black",
    "seafoam",
    "sage",
    "burgandy",
    "navy",
    "apricot",
    "byzantine",
    "lavender",
    "redorange",
    "skyblue",
    "turquoise",
}
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def parse_status(payload: bytes) -> dict[str, Any]:
    """Map the poller's JSON (``power``, ``model``, ``last_upload``) onto the
    common status shape. The TV has no battery."""
    out: dict[str, Any] = {"battery_pct": None}
    if not payload:
        return out
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        out["error"] = "status payload was not JSON"
        return out
    if not isinstance(decoded, dict):
        out["error"] = f"expected JSON object, got {type(decoded).__name__}"
        return out
    for key in ("power", "model", "last_upload"):
        if key in decoded:
            out[key] = decoded[key]
    return out


def valid_host(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(_HOSTNAME.match(value))


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Everything is optional so a half-configured TV still saves; the
    publisher skips a device with no address."""
    host = payload.get("host")
    if host not in (None, "") and (not isinstance(host, str) or not valid_host(host.strip())):
        return False, "TV address must be an IP address or host name"
    style = payload.get("matte_style")
    if style not in (None, "") and style not in MATTE_STYLES:
        return False, f"unknown mat {style!r}"
    colour = payload.get("matte_colour")
    if colour not in (None, "") and colour not in MATTE_COLOURS:
        return False, f"unknown mat colour {colour!r}"
    slideshow = payload.get("take_over_slideshow")
    if slideshow not in (None, "", "off", "keep"):
        return False, "TV's own slideshow must be off or keep"
    keep = payload.get("keep_on_tv")
    if keep not in (None, ""):
        try:
            keep_n = int(keep)
        except (TypeError, ValueError):
            return False, "Frames kept on the TV must be a whole number"
        if not KEEP_MIN <= keep_n <= KEEP_MAX:
            return False, f"Frames kept on the TV must be {KEEP_MIN} to {KEEP_MAX}"
    return True, None
