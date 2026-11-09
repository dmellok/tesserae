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
_SWITCH = re.compile(r"^(switch|light|input_boolean)\.[a-z0-9_]+$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def parse_status(payload: bytes) -> dict[str, Any]:
    """Map the poller's JSON (``screen``, ``screen_why``, ``power``, ``model``,
    ``last_upload``) onto the
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
    for key in ("screen", "screen_why", "power", "model", "last_upload"):
        if key in decoded:
            out[key] = decoded[key]
    return out


def valid_host(value: str) -> bool:
    # IPv4 only: the TV's REST and websocket URLs are built as host:port,
    # which an IPv6 literal would break.
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return bool(_HOSTNAME.match(value))


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Everything is optional so a half-configured TV still saves; the
    publisher skips a device with no address."""
    host = payload.get("host")
    if host not in (None, "") and (not isinstance(host, str) or not valid_host(host.strip())):
        return False, "TV address must be an IPv4 address or host name"
    in_use = payload.get("sleep_when_in_use")
    if in_use not in (None, "", "never", "30", "60", "120"):
        return False, "Still in use at quiet hours must be never, 30, 60 or 120"
    mode = payload.get("power_control")
    if mode not in (None, "", "tv", "switch", "none"):
        return False, "Power control must be tv, switch or none"
    switch = payload.get("power_switch")
    if switch not in (None, "") and (
        not isinstance(switch, str) or not _SWITCH.match(switch.strip())
    ):
        return False, "Power switch must be a Home Assistant entity id such as switch.tv_plug"
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
