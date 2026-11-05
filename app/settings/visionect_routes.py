"""Visionect signs in the settings UI: the Add device card's Visionect tab,
the listener / sign status on a sign's device page, and the Report in /
Reboot / Full refresh actions.

The protocol itself is ``app.visionect``; the generic settings routes know
nothing of it (a sign's config goes through ``app.device_service``'s kind
hooks). Only the page builders in ``index_routes`` call into this module."""

from __future__ import annotations

import os
import re
import socket
import time
from typing import Any

from flask import current_app, flash, redirect, request, url_for
from werkzeug.wrappers import Response

from app import visionect
from app.clock_text import clock_of
from app.device_loader import Device
from app.visionect_firmware import NOTES_PAGE

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
    try:
        port = visionect.configured_port(settings_store())
        error = svc.start_error if svc is not None and not svc.running else None
    except ValueError as exc:
        port, error = None, str(exc)
    listener = {
        "running": bool(svc is not None and svc.running),
        "port": port,
        "error": error,
        "env_override": bool(os.environ.get("TESSERAE_VISIONECT_PORT")),
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
        band["firmware"] = _visionect_firmware(svc, device)
    return band


def _health_text(health: Any) -> str:
    """The last day of a frame's connections in a line; only what happened."""
    if not isinstance(health, dict) or not health.get("connections"):
        return ""

    def count(n: int, name: str) -> str:
        return f"{n} {name}{'' if n == 1 else 's'}"

    parts = [count(health["connections"], "connection")]
    for key, name in (
        ("protocol_errors", "protocol error"),
        ("connection_errors", "connection error"),
        ("reboots", "reboot"),
    ):
        if health.get(key):
            parts.append(count(health[key], name))
    parts.append(count(health.get("pictures", 0), "picture"))
    return ", ".join(parts)


_FLASH_PHASE_TEXT = {
    "pending": "Waiting for the frame to restart into its bootloader.",
    "verifying": "Firmware written. Waiting for the frame to restart on it.",
}


def _visionect_firmware(svc: Any, device: Device) -> dict[str, Any] | None:
    """The sign's firmware for the device page: installed and latest versions,
    the builds it could be flashed with (newest first; downgrades labelled),
    and the state of a flash that is running or just ended. Built from the
    cache only, so a repository that can't be reached just means no builds."""
    try:
        fv = svc.firmware_view(device.id)
    except Exception:  # never break the page over firmware
        current_app.logger.debug("visionect: firmware view failed", exc_info=True)
        return None
    if fv is None:
        return None
    options = []
    for b in fv["builds"]:
        tags = [
            t
            for t, on in (
                ("installed", b["installed"]),
                ("newest", b["newest"]),
                ("ignored", b.get("ignored")),
            )
            if on
        ]
        if b["downgrade"]:
            tags.append("downgrade")
        label = b["version"] + (f" ({', '.join(tags)})" if tags else "")
        options.append(
            {
                "value": b["checksum"],
                "label": label,
                "version": b["version"],
                "installed": b["installed"],
                "downgrade": b["downgrade"],
                "ignored": bool(b.get("ignored")),
                "notes": b.get("notes") or [],
            }
        )
    flash = fv["flash"]
    flash_view = None
    if flash:
        phase = flash["phase"]
        if phase == "running":
            text = (
                f"Block {flash['blocks_done']} of {flash['blocks_total']} ({flash['percent']}%)"
                if flash["blocks_total"]
                else "Starting the transfer."
            )
        else:
            text = flash["message"] or _FLASH_PHASE_TEXT.get(phase, "")
        flash_view = {
            "active": flash["active"],
            "phase": phase,
            "ok": flash["ok"],
            "failed": phase == "failed",
            "percent": flash["percent"],
            "text": text,
            "target": flash["target"],
        }
    return {
        "installed": fv["installed"],
        "latest": fv["latest"],
        "latest_checksum": fv.get("latest_checksum", ""),
        "update_available": fv["update_available"],
        "options": options,
        "can_flash": not fv["blocked"] and any(not o["installed"] for o in options),
        "blocked": fv["blocked"],
        "flash": flash_view,
        "list_known": fv["list_known"],
        "unknown_because": fv.get("unknown_because", ""),
        "min_battery": fv["min_battery"],
        "endpoint": url_for("auth.devices_visionect_firmware", instance_id=device.id),
        "ignore_endpoint": url_for("auth.visionect_firmware_ignore"),
        "notes_page": NOTES_PAGE,
    }


def _visionect_sign(view: dict[str, Any]) -> dict[str, Any]:
    """The bound sign's own report for the device page: rows of what it
    said in its last status and settings read, the last action asked of it,
    and whether it is connected (actions need it to be)."""
    from datetime import datetime

    from app.tz_resolve import app_timezone

    tz = app_timezone()

    def clock(ts: Any) -> str:
        try:
            moment = datetime.fromtimestamp(float(ts), tz=tz)
            return f"{moment:%b %d} {clock_of(moment)}"
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
    if st.get("hardware"):
        rows.append(("Hardware", str(st["hardware"])))
    if st.get("bootloader"):
        rows.append(("Bootloader", str(st["bootloader"])))
    if st.get("connectivity"):
        rows.append(("Radio", str(st["connectivity"])))
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
        choices = visionect.SIGN_SETTINGS["frame_sleep"][1]
        label = next((lab for _cv, wire, lab in choices if wire == sleep_off), str(sleep_off))
        rows.append(("Power saving mode", label))
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
    if action.get("kind") in ("status", "reboot", "refresh", "network"):
        what = {
            "status": "Report in",
            "reboot": "Reboot",
            "refresh": "Full refresh",
            "network": "Network details",
        }[action["kind"]]
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
        "heartbeat_min": heartbeat if isinstance(heartbeat, int) else st.get("next_status_min"),
        "settings": dict(view.get("settings") or {}),
        "health": _health_text(view.get("health")),
        "stuck": int((view.get("health") or {}).get("stuck") or 0),
        "network": dict(view.get("network") or {}),
        "note": note,
        "error": str(view.get("param_error") or ""),
    }


def _visionect_band(device: Device) -> dict[str, Any] | None:
    if not device.kind_of or not visionect.is_visionect_device(device):
        return None
    svc = visionect.service()
    try:
        port = visionect.configured_port(settings_store())
    except ValueError as exc:
        return {
            "ok": False,
            "title": "Visionect port needs attention",
            "body": str(exc),
            "link": _VISIONECT_DOCS,
        }
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

        when = clock_of(datetime.fromtimestamp(refused_at, tz=app_timezone()))
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

        until = clock_of(datetime.fromtimestamp(asleep, tz=app_timezone()))
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


@bp.post("/settings/visionect/port")
def visionect_port_save() -> Response:
    """One app-wide port for every Visionect sign; rebind immediately."""
    redirect_to = redirect(url_for("auth.settings_area", area="devices"))
    if os.environ.get("TESSERAE_VISIONECT_PORT"):
        flash(
            "Server port is controlled by TESSERAE_VISIONECT_PORT (or the Home Assistant App option). Change it there and restart Tesserae.",
            "error",
        )
        return redirect_to
    try:
        port = visionect.validate_port(request.form.get("port", ""))
        svc = visionect.service()
        if svc is not None and svc.firmware_update_running():
            raise ValueError("A firmware update is running; change the port after it ends.")
        # An unchanged, live listener already owns this socket. Otherwise
        # probe before saving, so an occupied port leaves settings untouched.
        if svc is None or not svc.running or svc.port != port:
            host = visionect.configured_host()
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            try:
                with socket.create_server((host, port), family=family):
                    pass
            except OSError as exc:
                raise ValueError(
                    f"Cannot listen on Server port {port}: it may be in use or unavailable ({exc})."
                ) from exc
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect_to
    settings_store().patch_section("app", {"visionect_port": port})
    visionect.register(current_app._get_current_object())  # type: ignore[attr-defined]
    svc = visionect.service()
    if svc is not None and svc.autostart and not svc.running:
        flash(
            f"Saved Server port {port}, but the listener could not bind: {svc.start_error or 'still starting'}. Tesserae retries every half minute.",
            "error",
        )
    else:
        flash(
            f"Saved Server port {port}. The listener rebinds immediately when a Visionect device exists; reconnect every sign using this port. Update container port mappings too.",
            "ok",
        )
    return redirect_to


_VISIONECT_ACTION_DONE = {
    "status": "Asked {name} to report in; its status shows here once it does.",
    "reboot": "Sent {name} a reboot. It reconnects in a minute or two and gets the latest picture.",
    "refresh": "{name} repaints its picture from scratch now.",
    "network": "Asked {name} for its network details. Reload in a few seconds to see them.",
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


@bp.post("/settings/firmware/visionect-ignore")
def visionect_firmware_ignore() -> Response:
    """Ignore a firmware build, or stop ignoring it. An ignored build stops being
    offered as an update on any page; it can still be chosen by hand."""
    redirect_to = redirect(url_for("auth.firmware_index"))
    ignore = request.form.get("ignore", "").strip().lower()
    restore = request.form.get("restore", "").strip().lower()
    text = ignore or restore
    if not re.fullmatch(r"[0-9a-f]{1,8}", text):
        flash("Unknown firmware build.", "error")
        return redirect_to
    store = settings_store()
    raw = store.get_section("app").get(visionect.FIRMWARE_IGNORED_KEY)
    current = [str(x) for x in raw] if isinstance(raw, list) else []
    wanted = int(text, 16)
    kept = [x for x in current if int(x, 16) != wanted]
    store.patch_section(
        "app", {visionect.FIRMWARE_IGNORED_KEY: [*kept, f"{wanted:08x}"] if ignore else kept}
    )
    return redirect_to


@bp.post("/settings/devices/<instance_id>/visionect-firmware")
def devices_visionect_firmware(instance_id: str) -> Response:
    """Flash a chosen firmware build onto a Visionect sign (from the Firmware
    page). Explicit only: the build is validated and downloaded, then the sign is
    rebooted into its bootloader."""
    redirect_to = redirect(url_for("auth.firmware_index"))
    device = devices().get(instance_id)
    if device is None or not visionect.is_visionect_device(device):
        flash(f"Unknown Visionect device {instance_id!r}.", "error")
        return redirect_to
    svc = visionect.service()
    problem = (
        svc.request_firmware_update(device.id, request.form.get("checksum", ""))
        if svc is not None
        else "Tesserae isn't listening for Visionect frames right now."
    )
    if problem:
        flash(problem, "error")
    else:
        flash(
            f"Updating {device.name}: it restarts into its bootloader and takes the new "
            "firmware, about a minute in all. Progress shows in its row; don't power it off.",
            "ok",
        )
    return redirect_to
