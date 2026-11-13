"""fraimic device contract.

A Fraimic canvas has a local REST API and no Tesserae firmware: on battery
it deep-sleeps and is unreachable, waking on a tap or at the times in its
own wake schedule (``/api/wake-schedule``). ``app.fraimic`` sets that
schedule, catches each wake, uploads the latest ``fraimic_bin`` frame and
records the frame's ``/api/info`` reply as the device's status, with a few
keys of its own (``next_wake``, ``last_upload``, ``awake``) merged in.
"""

from __future__ import annotations

import ipaddress
import json
import re
from typing import Any

# Catalog SKUs (hardware/fraimic/*.json) share this module; the marker lets
# ``app.fraimic.is_fraimic_device`` recognise them without a registry lookup.
PROTOCOL = "fraimic"

# The frame's own schedule takes an interval of 60 minutes or more, or up to
# four times a day (REST API guide, "Wake Schedule"). Shorter intervals and
# longer lists are done by rewriting the schedule at every wake
# (``app.fraimic_wakes``).
INTERVALS = (5, 10, 15, 20, 30, 60, 90, 120, 180, 240, 360, 480, 720, 1440)
CEILINGS = (60, 120, 180, 240, 360, 480, 720, 1440)
MAX_TIMES = 48
WAKE_MODES = ("interval", "times", "changes", "manual")
TAP_ACTIONS = ("none", "next")
REDRAWS = ("off", "daily", "weekly")

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


def parse_times(raw: Any) -> list[str] | None:
    """``"06:00, 18:00"`` -> ``["06:00", "18:00"]`` (sorted, de-duplicated);
    None when any entry isn't a 24-hour ``HH:MM``, or there are none or
    more than ``MAX_TIMES``."""
    if not isinstance(raw, str):
        return None
    parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
    if not parts or len(parts) > MAX_TIMES or not all(_TIME.match(p) for p in parts):
        return None
    times = sorted(set(parts))
    return times if len(times) <= MAX_TIMES else None


def valid_host(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value), ipaddress.IPv4Address)
    except ValueError:
        return bool(_HOSTNAME.match(value))


def parse_status(payload: bytes) -> dict[str, Any]:
    """Map ``/api/info`` (plus the publisher's own keys) onto the common
    status shape. The 31.5" has a two-cell pack, so ``battery_mv`` is not
    passed on: the shared LiPo curve would misread it."""
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
    battery = decoded.get("battery") if isinstance(decoded.get("battery"), dict) else {}
    pct = battery.get("percent")
    if isinstance(pct, int | float) and not isinstance(pct, bool) and 0 <= pct <= 100:
        out["battery_pct"] = int(pct)
    if isinstance(battery.get("voltage_mv"), int):
        out["voltage_mv"] = battery["voltage_mv"]
    for key in ("charging", "cable_connected"):
        if isinstance(battery.get(key), bool):
            out[key] = battery[key]
    wifi = decoded.get("wifi") if isinstance(decoded.get("wifi"), dict) else {}
    if isinstance(wifi.get("rssi"), int):
        out["rssi"] = wifi["rssi"]
    if isinstance(wifi.get("ip"), str):
        out["ip"] = wifi["ip"][:64]
    if isinstance(decoded.get("firmware_version"), str):
        out["fw_version"] = decoded["firmware_version"][:32]
    if isinstance(battery.get("sleep_delta_mv"), int):
        # Voltage change over the last deep sleep: what a wake cycle costs.
        out["sleep_delta_mv"] = battery["sleep_delta_mv"]
    settings = decoded.get("settings") if isinstance(decoded.get("settings"), dict) else {}
    if isinstance(settings.get("keep_awake"), bool):
        # On charge the frame doesn't sleep either, so new frames reach it
        # at once and there is no wake to predict.
        out["stays_awake"] = settings["keep_awake"] or battery.get("charging") is True
    display = decoded.get("display") if isinstance(decoded.get("display"), dict) else {}
    for key in ("last_refresh", "next_refresh"):
        if isinstance(display.get(key), str):
            out[f"frame_{key}"] = display[key][:40]
    for key in ("next_wake", "last_upload", "awake", "schedule", "missed_wakes"):
        if key in decoded:
            out[key] = decoded[key]
    return out


def validate_config(payload: dict[str, Any]) -> tuple[bool, str | None]:
    """Every field is optional so the add form (address only) saves; what
    is present has to be something the frame will accept."""
    host = payload.get("host")
    if host not in (None, "") and (not isinstance(host, str) or not valid_host(host.strip())):
        return False, "Frame address must be an IPv4 address or host name"
    mode = payload.get("wake_mode")
    if mode not in (None, "") and mode not in WAKE_MODES:
        return False, "Wake schedule must be interval, times or manual"
    interval = payload.get("wake_interval_minutes")
    if interval not in (None, ""):
        try:
            minutes = int(interval)
        except (TypeError, ValueError):
            return False, "Wake every must be a whole number of minutes"
        if minutes not in INTERVALS:
            return False, "Wake every must be one of " + ", ".join(map(str, INTERVALS)) + " minutes"
    ceiling = payload.get("wake_ceiling_minutes")
    if ceiling not in (None, ""):
        try:
            if int(ceiling) not in CEILINGS:
                raise ValueError
        except (TypeError, ValueError):
            return False, "And at least every must be one of " + ", ".join(map(str, CEILINGS))
    anchor = payload.get("wake_anchor")
    if anchor not in (None, "") and not (isinstance(anchor, str) and _TIME.match(anchor.strip())):
        return False, "Counted from must be a 24-hour time such as 00:00"
    times = payload.get("wake_times")
    if (mode == "times" or times not in (None, "")) and parse_times(times) is None:
        return False, "Wake times must be 24-hour times such as 06:00, 18:00"
    if payload.get("tap_action") not in (None, "", *TAP_ACTIONS):
        return False, "On a tap must be none or next"
    if payload.get("redraw") not in (None, "", *REDRAWS):
        return False, "Redraw must be off, daily or weekly"
    return True, None
