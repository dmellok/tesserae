"""Mirror Netgear's native weekly sleep events, with an explicit local fallback.

Netgear's public web scheduler uses devices/<id>/scheduler, events with
command/time/days, and the device's timezone and schedulerEnabled fields.
The LAN reference API exposes sleep status, not a schedule. No alarmTime
aliases are guessed. These contracts still need verification on a real frame.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask

from app.device_loader import Device
from app.meural_auth import HttpError, MeuralAuthError
from app.meural_cloud import get_cloud_client, resource_id

CACHE_S = 300
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _hhmm(value: Any, *, colon: bool = False) -> str:
    pattern = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]" if colon else r"(?:[01][0-9]|2[0-3])[0-5][0-9]"
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise ValueError("Use a valid 24-hour sleep time")
    return value.replace(":", "")


def _events(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    rows = data.get("events") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Meural did not return its sleep schedule")
    return rows


def _sleep_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for row in rows:
        if row.get("command") not in ("sleep", "wake"):
            continue
        days = row.get("days")
        if not isinstance(days, list) or not days or not all(day in DAYS for day in days):
            raise ValueError("Meural returned an unsupported sleep schedule")
        events.append({"command": row["command"], "time": _hhmm(row.get("time")), "days": days})
    # A lone sleep without any wake is unsafe to interpret as a recurring window.
    if events and {row["command"] for row in events} != {"sleep", "wake"}:
        raise ValueError("Meural did not return both sleep and wake events")
    return events


def in_sleep_window(state: dict[str, Any], now: datetime | None = None) -> bool:
    if not state.get("available") or not state.get("enabled"):
        return False
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(state["timezone"]))
    transitions = []
    for offset in range(8):
        date = local.date() - timedelta(days=offset)
        for event in state["events"]:
            if DAYS[date.weekday()] not in event["days"]:
                continue
            clock = event["time"]
            at = datetime(
                date.year, date.month, date.day, int(clock[:2]), int(clock[2:]), tzinfo=local.tzinfo
            )
            if at <= local:
                transitions.append((at, event["command"]))
    return bool(transitions and max(transitions)[1] == "sleep")


def _fallback(cached: dict[str, Any], scope: str) -> dict[str, Any]:
    return {
        "scope": scope,
        "source": "tesserae",
        "available": False,
        "can_edit": False,
        "enabled": None,
        "timezone": None,
        "days": [],
        "synced_at": cached.get("synced_at") if cached.get("scope") == scope else None,
        "checked_at": time.time(),
        "start": None,
        "end": None,
        "events": [],
        "message": "Frame schedule unavailable. Using Tesserae quiet hours. Change the frame schedule in the Meural app.",
    }


def sync_schedule(app: Flask, device: Device, *, force: bool = False) -> dict[str, Any]:
    store = app.config["SETTINGS_STORE"]
    cfg = (store.get_section("devices") or {}).get(device.id) or {}
    scope = f"{cfg.get('meural_account_id')}:{cfg.get('cloud_device_id')}"
    cached = cfg.get("meural_sleep_schedule") or {}
    if (
        not cfg.get("meural_refresh_token_secret")
        or cfg.get("meural_auth_failed")
        or not cfg.get("cloud_device_id")
    ):
        state = _fallback(cached, scope)
        device.manifest["_native_sleep_schedule"] = state
        return state
    if (
        not force
        and cached.get("scope") == scope
        and time.time() - float(cached.get("checked_at") or 0) < CACHE_S
    ):
        device.manifest["_native_sleep_schedule"] = cached
        return dict(cached)
    client = get_cloud_client(app, device.id)
    with client.lock:
        try:
            target = resource_id(cfg["cloud_device_id"])
            data = client.request("GET", f"devices/{target}")
            data = data.get("data", data) if isinstance(data, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get("schedulerEnabled"), bool):
                raise ValueError("Meural did not return its scheduler status")
            timezone = data.get("timezone")
            if not isinstance(timezone, str):
                raise ValueError("Meural did not return the frame time zone")
            ZoneInfo(timezone)
            events = _sleep_events(_events(client.request("GET", f"devices/{target}/scheduler")))
            starts = [row for row in events if row["command"] == "sleep"]
            ends = [row for row in events if row["command"] == "wake"]
            simple = len(starts) == len(ends) == 1 and starts[0]["days"] == ends[0]["days"]
            start = starts[0]["time"] if simple else None
            end = ends[0]["time"] if simple else None
            start = start[:2] + ":" + start[2:] if start else None
            end = end[:2] + ":" + end[2:] if end else None
            if not data["schedulerEnabled"] or not events:
                message = "Frame sleep schedule is off (from the frame)."
            elif simple:
                message = f"Frame sleeps {start}–{end} (from the frame)."
            else:
                message = "Frame sleep follows its weekly schedule (from the frame)."
            state = {
                "scope": scope,
                "source": "frame",
                "available": True,
                "can_edit": True,
                "synced_at": time.time(),
                "checked_at": time.time(),
                "timezone": timezone,
                "enabled": data["schedulerEnabled"],
                "events": events,
                "start": start,
                "end": end,
                "days": starts[0]["days"] if simple else [],
                "message": message,
            }
        except (MeuralAuthError, HttpError, OSError, ValueError, ZoneInfoNotFoundError):
            state = _fallback(cached, scope)
        device.manifest["_native_sleep_schedule"] = state
        store.update_item("devices", device.id, {"meural_sleep_schedule": state})
        return state


def edit_schedule(app: Flask, device: Device, data: dict[str, Any]) -> dict[str, Any]:
    start, end = _hhmm(data.get("start"), colon=True), _hhmm(data.get("end"), colon=True)
    days, enabled = data.get("days"), data.get("enabled")
    if (
        start == end
        or not isinstance(enabled, bool)
        or not isinstance(days, list)
        or not days
        or not all(day in DAYS for day in days)
    ):
        raise ValueError("Choose different sleep and wake times and at least one weekday")
    cfg = (app.config["SETTINGS_STORE"].get_section("devices") or {}).get(device.id) or {}
    target = resource_id(cfg.get("cloud_device_id"))
    client = get_cloud_client(app, device.id)
    with client.lock:
        if not sync_schedule(app, device, force=True)["available"]:
            raise ValueError("Could not read the frame schedule. Change it in the Meural app.")
        # Preserve the exact artwork events; this edit changes only sleep and wake.
        events = [
            row
            for row in _events(client.request("GET", f"devices/{target}/scheduler"))
            if row.get("command") not in ("sleep", "wake")
        ]
        events.extend(
            [
                {"command": "sleep", "time": start, "days": days},
                {"command": "wake", "time": end, "days": days},
            ]
        )
        client.request("PUT", f"devices/{target}/scheduler", json_data={"events": events})
        client.request("PUT", f"devices/{target}", json_data={"schedulerEnabled": enabled})
        client.request("POST", f"devices/{target}/sync")
        state = sync_schedule(app, device, force=True)
        if not state["available"]:
            raise ValueError(
                "Schedule sent, but it could not be re-read. Check it in the Meural app."
            )
        return state


def next_wake_at(state: dict[str, Any], now: datetime) -> datetime | None:
    local = now.astimezone(ZoneInfo(state["timezone"]))
    candidates = []
    for offset in range(8):
        date = local.date() + timedelta(days=offset)
        for event in state["events"]:
            if event["command"] != "wake" or DAYS[date.weekday()] not in event["days"]:
                continue
            clock = event["time"]
            at = datetime(
                date.year, date.month, date.day, int(clock[:2]), int(clock[2:]), tzinfo=local.tzinfo
            )
            if at > local:
                candidates.append(at)
    return min(candidates).astimezone(UTC) if candidates else None


def schedule_view(app: Flask, device: Device) -> dict[str, Any]:
    cfg = (app.config["SETTINGS_STORE"].get_section("devices") or {}).get(device.id) or {}
    scope = f"{cfg.get('meural_account_id')}:{cfg.get('cloud_device_id')}"
    cached = cfg.get("meural_sleep_schedule") or {}
    if (
        cfg.get("meural_refresh_token_secret")
        and not cfg.get("meural_auth_failed")
        and cached.get("scope") == scope
        and time.time() - float(cached.get("checked_at") or 0) < CACHE_S
    ):
        return dict(cached)
    return _fallback(cached, scope)
