"""Visionect signs in the settings UI: the Add device card's Visionect tab,
the listener / sign status on a sign's device page, and the Report in /
Reboot / Full refresh actions.

The protocol itself is ``app.visionect``; the generic settings routes know
nothing of it (a sign's config goes through ``app.device_service``'s kind
hooks). Only the page builders in ``index_routes`` call into this module."""

from __future__ import annotations

import time
from typing import Any

from flask import flash, redirect, url_for
from werkzeug.wrappers import Response

from app import visionect
from app.device_loader import Device

from ._shared import bp, device_kinds, devices, settings_store

is_visionect_device = visionect.is_visionect_device

_VISIONECT_DOCS = {
    "label": "Visionect quickstart",
    "url": "https://docs.tesserae.ink/quickstart/visionect/",
}


def add_card_context() -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Model choices and listener state for the Add device card's
    Visionect tab: the folder kind plus every catalog SKU on its protocol."""
    kinds = sorted(
        (d for d in device_kinds() if visionect.is_visionect_device(d) and not d.kind_of),
        key=lambda d: (d.id != visionect.KIND, str(d.name).casefold()),
    )
    options = [{"value": d.id, "label": str(d.name)} for d in kinds]
    svc = visionect.service()
    listener = {
        "running": bool(svc is not None and svc.running),
        "port": svc.port if svc is not None else visionect.configured_port(settings_store()),
        "error": svc.start_error if svc is not None and not svc.running else None,
    }
    return options, listener


def setup_band(device: Device) -> dict[str, Any] | None:
    """Status band for a Visionect sign's card: whether Tesserae is
    listening for signs, and what the bound sign is doing; plus, once a
    sign is bound, what it last reported and the actions it can be asked
    for. None for every other kind."""
    band = _visionect_band(device)
    svc = visionect.service()
    if band is None or svc is None or not svc.running:
        return band
    uuid = svc.device_uuid(device.id)
    if uuid:
        band["sign"] = _visionect_sign(svc.sign_view(uuid) or {})
    return band


def _visionect_sign(view: dict[str, Any]) -> dict[str, Any]:
    """The bound sign's own report for the device page: rows of what it
    said in its last status and settings read, the last action asked of it,
    and whether it is connected (actions need it to be)."""
    from datetime import datetime

    from app.tz_resolve import app_timezone

    tz = app_timezone()

    def clock(ts: Any) -> str:
        try:
            return datetime.fromtimestamp(float(ts), tz=tz).strftime("%b %d %H:%M")
        except (TypeError, ValueError, OverflowError, OSError):
            return ""

    st = view.get("status") or {}
    rows: list[tuple[str, str]] = []
    if st.get("firmware"):
        rows.append(("Firmware", str(st["firmware"])))
    battery = [f"{st['battery_pct']}%"] if "battery_pct" in st else []
    if "battery_mv" in st:
        battery.append(f"{st['battery_mv'] / 1000:.2f} V")
    if st.get("charging"):
        battery.append(str(st["charging"]))
    if battery:
        rows.append(("Battery", " · ".join(battery)))
    wifi = [str(view["wifi"])] if view.get("wifi") else []
    if "rssi" in st:
        wifi.append(f"{st['rssi']} dBm")
    if wifi:
        rows.append(("Wi-Fi", " · ".join(wifi)))
    if "temperature_c" in st:
        rows.append(("Temperature", f"{st['temperature_c']} °C"))
    if "uptime_min" in st:
        minutes = int(st["uptime_min"])
        days, rest = divmod(minutes, 24 * 60)
        rows.append(("Up for", (f"{days} d " if days else "") + f"{rest // 60} h {rest % 60} min"))
    heartbeat = view.get("heartbeat_min")
    if isinstance(heartbeat, int):
        rows.append(("Check-in interval", f"Every {heartbeat} min"))
    elif "next_status_min" in st:
        rows.append(("Next check-in", f"in {st['next_status_min']} min"))
    sleep_off = view.get("sleep_disabled")
    if isinstance(sleep_off, int):
        rows.append(("Sleep between check-ins", "Off (stays awake)" if sleep_off else "On"))
    if view.get("connect_reason") and view.get("connected"):
        since = clock(view.get("connected_at"))
        rows.append(
            (
                "Connected",
                f"{since}, reason “{view['connect_reason']}”"
                if since
                else str(view["connect_reason"]),
            )
        )
    note = ""
    action = view.get("action") or {}
    if action.get("kind") in ("status", "reboot", "refresh"):
        what = {"status": "Report in", "reboot": "Reboot", "refresh": "Full refresh"}[
            action["kind"]
        ]
        note = f"{what} asked {clock(action.get('at'))}."
    arrows = {"out": "→", "in": "←"}
    log = []
    for at, way, text in view.get("exchanges") or []:
        try:
            when = datetime.fromtimestamp(float(at), tz=tz).strftime("%b %d %H:%M:%S")
        except (TypeError, ValueError, OverflowError, OSError):
            when = "?"
        log.append(f"{when}  {arrows.get(way, '·')}  {text}")
    return {
        "rows": rows,
        "log": log,
        "connected": bool(view.get("connected")),
        "note": note,
        "error": str(view.get("param_error") or ""),
    }


def _visionect_band(device: Device) -> dict[str, Any] | None:
    if not device.kind_of or not visionect.is_visionect_device(device):
        return None
    svc = visionect.service()
    port = svc.port if svc is not None else visionect.configured_port(settings_store())
    if svc is None or not svc.running:
        reason = f" ({svc.start_error})" if svc is not None and svc.start_error else ""
        return {
            "ok": False,
            "title": f"Not listening on port {port}",
            "body": (
                f"Tesserae couldn't open TCP {port} for Visionect frames{reason}. If the old "
                "Visionect server still runs on this host, stop it; Tesserae retries every "
                "half minute."
            ),
            "link": _VISIONECT_DOCS,
        }
    uuid = svc.device_uuid(device.id)
    if not uuid:
        if svc.auto_bind_open(device):
            body = (
                f"Listening on TCP {port}. Point the frame at this server; the first unknown "
                "frame that connects within an hour of adding this device is bound to it."
            )
        else:
            body = (
                f"Listening on TCP {port}. Unknown frames are only bound automatically within "
                "an hour of adding a device or clearing its UUID, so one that connects now is "
                "listed under discovered devices. Paste its UUID here to bind it."
            )
        return {"ok": True, "title": "Waiting for a frame", "body": body, "link": _VISIONECT_DOCS}
    view = svc.sign_view(uuid) or {}
    refused = view.get("refused") or {}
    refused_note = ""
    refused_at = refused.get("at")
    if isinstance(refused_at, (int, float)) and time.time() - refused_at < 24 * 3600:
        from datetime import datetime

        from app.tz_resolve import app_timezone

        when = datetime.fromtimestamp(refused_at, tz=app_timezone()).strftime("%H:%M")
        refused_note = (
            f" At {when} a connection from {refused.get('remote') or 'another address'} "
            "claimed this frame's UUID and was refused."
        )
    if view.get("connected"):
        where = f" from {view['remote']}" if view.get("remote") else ""
        if view.get("sending"):
            state = "Sending the latest picture."
        elif view.get("in_sync"):
            state = "It shows the latest picture."
        else:
            state = "Waiting for a picture to send."
        problem = view.get("last_error") or ""
        body = f"Frame {uuid} is connected{where}. {state}"
        if problem:
            body += f" Last problem: {problem}"
        body += refused_note
        return {
            "ok": not problem and not refused_note,
            "title": "Frame connected",
            "body": body,
            "link": _VISIONECT_DOCS,
        }
    asleep = view.get("asleep_until")
    if isinstance(asleep, (int, float)) and asleep > time.time():
        from datetime import datetime

        from app.tz_resolve import app_timezone

        until = datetime.fromtimestamp(asleep, tz=app_timezone()).strftime("%H:%M")
        return {
            "ok": True,
            "title": "Frame asleep",
            "body": (
                f"Quiet hours: frame {uuid} sleeps until about {until} and gets the latest "
                "frame when it wakes."
            ),
            "link": _VISIONECT_DOCS,
        }
    return {
        "ok": True,
        "title": "Frame not connected",
        "body": (
            f"Listening on TCP {port} for frame {uuid}. New pictures wait until it next checks in."
        ),
        "link": _VISIONECT_DOCS,
    }


_VISIONECT_ACTION_DONE = {
    "status": "Asked {name} to report in; its status shows here once it does.",
    "reboot": "Sent {name} a reboot. It reconnects in a minute or two and gets the latest picture.",
    "refresh": "{name} repaints its picture from scratch now.",
}


@bp.post("/settings/devices/<instance_id>/visionect/<action>")
def devices_visionect_action(instance_id: str, action: str) -> Response:
    """Report in, Reboot or Full refresh for a Visionect sign (the device
    page's Overview). Only a connected sign can be asked anything."""
    redirect_to = redirect(url_for("auth.device_page", instance_id=instance_id, _anchor="overview"))
    device = devices().get(instance_id)
    if device is None or not visionect.is_visionect_device(device):
        flash(f"Unknown Visionect device {instance_id!r}.", "error")
        return redirect_to
    svc = visionect.service()
    problem = (
        svc.request_action(device.id, action)
        if svc is not None
        else "Tesserae isn't listening for Visionect frames right now."
    )
    if problem:
        flash(problem, "error")
    else:
        flash(_VISIONECT_ACTION_DONE[action].format(name=device.name), "ok")
    return redirect_to
