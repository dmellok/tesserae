"""Drive Visionect e-paper signs directly, without the Visionect Software Suite.

A Visionect sign (the 32" Place & Play, Joan, the 13" boards) is a thin
client. It dials the server address stored in its flash on TCP 11113 and
speaks first; the server pushes it pre-encoded 4-bit framebuffers. There is no
handshake and no authentication: the 16-byte UUID in the first status packet
is the sign's whole identity. The wire protocol comes from ``pyvisionect``
(MIT, a clean-room reimplementation of the vendor gateway), so this module only
glues it to Tesserae:

* One process-wide listener (``pyvisionect.io.VisionectServer``, wrapped by
  ``app.visionect_listener.GuardedServer`` to bound what the open port
  accepts) runs on its own asyncio loop in a daemon thread, like
  ``app.embedded_broker``. It runs while a ``visionect`` device exists.
  ``register()`` re-runs on every transport rebuild: it re-attaches the push
  listener, rebinds signs and restarts the listener only when the configured
  address or port changed. The listener binds the same address as the web
  server (``TESSERAE_BIND_HOST``, all interfaces by default).
* Each sign has one connection Tesserae talks to. A re-dial replaces it; a
  same-UUID connection from another address is refused while it lives.
* Each sign has a small state machine, run one pass at a time
  (``_reconcile``): encode the device's latest renderer artifact
  (``trmnl_png_gray16``, already fitted, flipped and dithered onto the
  sign's 16 greys), send it if the sign hasn't acked that frame, and only once
  it has (and no render for it is under way), send a quiet-hours deep sleep.
  At most one frame or sleep is in flight. It is forgotten when the sign
  reconnects, and an ACK that never comes drops the connection so the sign
  re-dials. A NACK retries after a back-off.
* Status packets feed ``record_status_heartbeat``, so the device card, battery
  history and Home Assistant sensors work as for any other device.
* An unknown sign is bound to a visionect device with a blank UUID (within an
  hour of that device being added or its UUID cleared), or listed under
  Settings -> Devices as a discovered device to register.

What the sign shows is tracked from its ACKs. Its own ``DisplayStateCRC`` is
only trusted before the first ACK of a run (a Tesserae restart): firmware
6.17 reports the new value at its next status, up to 30 minutes later, so a
CRC read after a push is usually stale.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from flask import Flask

from app import background_loops, device_service, online, visionect_flash
from app.device_loader import Device, DeviceRegistry
from app.visionect_bootloader import BootloaderError, parse_hello
from app.visionect_firmware import (
    Build,
    FirmwareRepository,
    RepositoryError,
    compatible,
)

logger = logging.getLogger(__name__)

KIND = "visionect"
DEFAULT_PORT = 11113
# The listener's bind address when TESSERAE_BIND_HOST (which the web server
# also honours) is not set: the sign dials in from the LAN.
DEFAULT_HOST = "0.0.0.0"
# Whether register() starts the listener by itself. Tests turn it off (they
# start a listener on a port of their own where they need one).
AUTOSTART = True
# How often the loop re-runs every connected sign (quiet-hours sleep, retries).
TICK_S = 60.0
# Retry delays after the sign refuses a frame; a charging refusal is normal.
NACK_RETRY_S = 60.0
NACK_CHARGING_RETRY_S = 300.0
# Gap between attempts to bind the port after a failure (a timer retries).
START_RETRY_S = 30.0
# How long to hold off sleep while a size change repaints the frame.
REPAINT_WAIT_S = 300.0
# Firmware 6.17 reports the new DisplayStateCRC only at its next status,
# up to 30 minutes after the ACK; only after this long is a mismatch real.
CRC_CONVERGE_S = 40 * 60.0
# The longest single deep sleep we ask for, and the margin past the window's
# end so the sign wakes to the day's first frame. Nothing can wake a sleeping
# sign over the network, so one sleep is at most a day: a longer window is
# slept a day at a time (the sign wakes, reconnects and is put back to sleep).
MAX_SLEEP_MINUTES = 24 * 60
SLEEP_MARGIN_MINUTES = 2
# A frame or sleep command the sign never answers, counted from when its
# write finished (a full 32" frame, 1.8 MB, has taken 12 s to ACK over
# wifi). Then the connection is dropped, so the sign re-dials and gets the
# latest state, instead of everything waiting for an ACK that was lost.
IMAGE_ACK_TIMEOUT_S = 120.0
SLEEP_ACK_TIMEOUT_S = 60.0
# The longest a write may block: a half-open socket blocks drain() for as
# long as TCP keeps retrying.
WRITE_TIMEOUT_S = 90.0
# A second connection claiming a bound sign's UUID from another address is
# refused while the sign's own connection is alive. If that connection has
# been quiet for PROBE_QUIET_S it is asked for a status, and dropped if none
# comes back within PROBE_TIMEOUT_S, so a sign that really moved address
# (DHCP) gets back in at its next re-dial.
PROBE_QUIET_S = 30.0
PROBE_TIMEOUT_S = 20.0
# The address that last acknowledged a frame or sleep for a UUID is the sign's
# own: a connection from it takes the UUID back from a connection elsewhere
# (one that claimed it while the sign slept, say), and it is exempt from the
# listener's total connection cap. Another address's ACKs move that trust only
# once the trusted address has acknowledged nothing for TRUST_STALE_S (longer
# than the longest sleep), so whoever holds the UUID meanwhile can't take it.
TRUST_STALE_S = 25 * 3600.0
# Quiet-hours sleep waits while the push pipeline renders for the device (a
# push that started just before the window would otherwise land after the
# sign fell asleep on the old frame), checking every RENDER_HOLD_POLL_S, for
# at most RENDER_HOLD_MAX_S.
RENDER_HOLD_POLL_S = 5.0
RENDER_HOLD_MAX_S = 600.0
# A latest render that couldn't be read or encoded is tried again after
# FAILED_RETRY_S, doubling to at most FAILED_RETRY_MAX_S, and at once when it
# is published again.
FAILED_RETRY_S = 60.0
FAILED_RETRY_MAX_S = 15 * 60.0
# A blank device binds the first unknown sign that connects only within this
# long of being added or having its UUID cleared (the time is stored with the
# device's settings under AUTO_BIND_KEY); after that an unknown sign is listed
# as discovered instead. Anything on the LAN can claim to be a sign, so a
# blank device doesn't stay up for grabs.
AUTO_BIND_WINDOW_S = 60 * 60.0
AUTO_BIND_KEY = "uuid_armed_at"
# Unbound UUIDs tracked at once (more connections are dropped), discovered
# entries kept, and UUIDs remembered as already warned about.
MAX_UNKNOWN_SIGNS = 16
MAX_DISCOVERED = 8
MAX_WARNED_UNBOUND = 64
# A firmware update is refused below this battery level (an interrupted flash
# is not something anyone has seen recover).
FLASH_MIN_BATTERY_PCT = 30
# Where the firmware builds someone chose to ignore are kept (app settings).
FIRMWARE_IGNORED_KEY = "visionect_firmware_ignored"
# Longest to wait for a bootloader's complete hello once it has connected.
HELLO_READ_S = 10.0
# How long a reboot for a flash waits for an unanswered packet to be answered.
FLASH_REBOOT_WAIT_S = 45.0
# The sign's own settings (TCLV parameters), shown on the device page and set
# from it. This follows what Visionect's own Software Suite does on the wire:
# at most five ids to a packet, and never a second settings packet until the
# frame has answered the first (``_pump_params``). The Suite never reads
# settings just because a frame connected, so neither do we, beyond the first
# connection, a reboot, and every SETTINGS_REREAD_S to catch USB console changes.
# READ_PARAMS is its "Basic" set, which it reads in one packet, less the
# connection type and plus the SSID. Only these (and the flash save) are ever
# read or written. A wider read once came just before a real 32" (firmware 6.17)
# stopped taking anything from the server until it was restarted; that read sent
# two packets back to back and asked for ids the Suite has no entry for.
PARAM_HEARTBEAT = 29
PARAM_SCREENS = 49
PARAM_TOUCH = 50
PARAM_SLEEP_DISABLE = 52
PARAM_FLASH_SAVE = 53
PARAM_WIFI_SSID = 65
PARAM_WIFI_PSK = 67
READ_PARAMS = (PARAM_HEARTBEAT, PARAM_SCREENS, PARAM_TOUCH, PARAM_SLEEP_DISABLE, PARAM_WIFI_SSID)
READ_CHUNK = 5  # ids per settings packet: the Suite's MaxIssuedCommands
SETTINGS_REREAD_S = 6 * 3600.0
# Read only when asked from the device page, as the Suite reads them when its
# Device configuration page opens. Text as the frame sends it: "172.16.3.1".
NETWORK_INFO: dict[int, str] = {
    2: "Connection",
    13: "IP address",
    14: "Netmask",
    15: "Gateway",
    16: "DNS server",
    17: "IP mode",
    18: "Server",
    19: "Server port",
    30: "Network retry",
    36: "Battery cut-off",
}
# The Suite's Global.DeviceStatePolling: how often the server asks a frame for a
# status. A frame only sends its own heartbeat after that long without hearing
# from the server, so this is what sets the real check-in cadence unless the
# heartbeat is shorter.
STATUS_POLL_DEFAULT_MINUTES = 15
# A frame reconnecting this often with no picture taken is stuck, not just having
# one of the short bursts that clear themselves within a few minutes.
STUCK_CONNECTIONS = 10
STUCK_WINDOW_S = 15 * 60.0
STUCK_RECENT_S = 3 * 60.0
HEALTH_WINDOW_S = 24 * 3600.0
# The settings offered under "Advanced", worded and valued as the Suite words
# them. Config name -> (TCLV id, ((config value, wire value, label), ...)). One
# the sign answers with a read error (no touch panel) is never offered.
SIGN_SETTINGS: dict[str, tuple[int, tuple[tuple[str, int, str], ...]]] = {
    "frame_sleep": (
        PARAM_SLEEP_DISABLE,
        (
            ("sleep", 0, "CLI wake on accelerometer"),
            ("awake", 1, "CLI and CPU always on"),
            ("cli_off", 2, "CLI disabled while asleep"),
        ),
    ),
    "touch_mode": (PARAM_TOUCH, (("0", 0, "Off"), ("1", 1, "On"), ("3", 3, "On + Beep"))),
    "system_screens": (
        PARAM_SCREENS,
        (
            ("0", 0, "Disabled"),
            ("1", 1, "Battery"),
            ("2", 2, "Not connected"),
            ("3", 3, "Battery + Not connected"),
        ),
    ),
}
# Free-text settings: config name -> (TCLV id written, TCLV id whose answer
# shows the frame has it). The password can't be read back. The Suite marks these
# as not writable over the network, so a frame may refuse them.
TEXT_SETTINGS: dict[str, tuple[int, int]] = {
    "wifi_ssid": (PARAM_WIFI_SSID, PARAM_WIFI_SSID),
    "wifi_psk": (PARAM_WIFI_PSK, PARAM_WIFI_SSID),
}
# Settings the device page puts behind the USB-cable warning.
NETWORK_SETTINGS = ("wifi_ssid", "wifi_psk")
TEXT_PARAMS = frozenset({PARAM_WIFI_SSID})
# Written ids that can't be read back, and the id whose answer lets them be offered.
WRITE_GATE = {PARAM_WIFI_PSK: PARAM_WIFI_SSID}
WRITE_ONLY = frozenset(WRITE_GATE)


def text_setting_value(name: str, raw: Any) -> str | None:
    """A free-text setting's config value if it is one the sign could take
    (SSID 1-32 characters, password 8-64), else None."""
    text = str(raw or "")
    if not text.strip() or not text.isascii() or not text.isprintable():
        return None
    if name == "wifi_ssid":
        return text if len(text) <= 32 else None
    return text if 8 <= len(text) <= 64 else None


def network_info_value(pid: int, raw: bytes) -> str:
    """A NETWORK_INFO setting as the page shows it."""
    if pid in (13, 14, 15, 16, 18):
        return _text_value(raw)
    number = int.from_bytes(raw, "little")
    if pid == 2:
        return {6: "Wi-Fi"}.get(number, str(number))
    if pid == 17:
        return {0: "Static", 1: "DHCP"}.get(number, str(number))
    if pid == 30:
        return f"{number} min"
    if pid == 36:
        return f"{number} mV"
    return str(number)


def status_poll_minutes(cfg: dict[str, Any]) -> int:
    """How often to ask this frame for a status, from its device config."""
    try:
        minutes = int(str(cfg.get("status_poll_min") or "").strip())
    except ValueError:
        return STATUS_POLL_DEFAULT_MINUTES
    return minutes if 5 <= minutes <= HEARTBEAT_MAX_MINUTES else STATUS_POLL_DEFAULT_MINUTES


def connection_health(
    connections: Sequence[tuple[float, str]], pictures: Sequence[float], now: float
) -> dict[str, Any]:
    """The last 24 hours of a frame's connections and pictures, and whether it
    looks stuck: reconnecting again and again, still at it now, with no picture
    taken in that time. Short bursts stop by themselves and don't count."""
    day = [(at, reason) for at, reason in connections if now - at <= HEALTH_WINDOW_S]
    recent = [at for at, _reason in day if now - at <= STUCK_WINDOW_S]
    stuck = (
        len(recent) >= STUCK_CONNECTIONS
        and now - recent[-1] <= STUCK_RECENT_S
        and not any(at >= recent[0] for at in pictures)
    )
    return {
        "connections": len(day),
        "protocol_errors": sum(reason == "protocol error" for _at, reason in day),
        "connection_errors": sum(reason == "connection error" for _at, reason in day),
        "reboots": sum(reason == "reboot" for _at, reason in day),
        "pictures": sum(now - at <= HEALTH_WINDOW_S for at in pictures),
        "stuck": len(recent) if stuck else 0,
    }


def _text_value(raw: bytes) -> str:
    return raw.split(b"\0")[0].decode("ascii", "replace")


def sign_setting_labels(known: dict[int, Any]) -> dict[str, str]:
    """The settings the sign has answered, config name -> what it is set to
    (empty when it can't be read back, like the Wi-Fi password)."""
    out: dict[str, str] = {}
    for name, (pid, choices) in SIGN_SETTINGS.items():
        if pid not in known:
            continue
        label = next((lab for _cv, wire, lab in choices if wire == known[pid]), None)
        out[name] = label or f"{known[pid]}"
    for name, (_write_pid, gate) in TEXT_SETTINGS.items():
        if gate in known:
            out[name] = str(known[PARAM_WIFI_SSID]) if name == "wifi_ssid" else ""
    return out


HEARTBEAT_MAX_MINUTES = 24 * 60
# Gaps between consecutive statuses that count as a check-in cadence (see
# ``observed_status_gap_s``), and how many status times are kept to measure it.
STATUS_GAP_MIN_S = 30.0
STATUS_GAP_MAX_S = HEARTBEAT_MAX_MINUTES * 60.0
STATUS_TIMES_KEPT = 5
# Lines kept per frame of what went each way, for the device page.
EXCHANGE_LOG_LINES = 60
# How long a picture waits for the frame to answer a settings packet.
PARAM_WAIT_S = 10.0
PARAM_NAMES = {
    PARAM_HEARTBEAT: "check-in interval",
    PARAM_SLEEP_DISABLE: "sleep mode",
    PARAM_FLASH_SAVE: "flash save",
}
PARAM_NAMES.update({pid: name.replace("_", " ") for name, (pid, _c) in SIGN_SETTINGS.items()})
PARAM_NAMES.update({pid: name.replace("_", " ") for name, (pid, _g) in TEXT_SETTINGS.items()})
# pyvisionect.devices.enums.ParamControl.
PARAM_READ = 0
PARAM_READ_ERROR = 2
PARAM_WRITE_ERROR = 3
# What the device page can ask a connected sign to do (request_action).
ACTIONS = ("status", "reboot", "refresh", "network")
# pyvisionect.imaging.constants Encoding.FOUR_BIT and Dithering.NONE. The
# renderer has already dithered onto the 16-grey ramp, and NONE keeps n*17
# levels exact.
ENCODING_4BPP = 4
DITHERING_NONE = 1
DISCOVERY_PREFIX = "visionect_"


def normalise_uuid(raw: object) -> str:
    """Canonical lower-case 8-4-4-4-12 UUID, or "" when ``raw`` isn't one."""
    text = str(raw or "").strip().lower().replace("-", "")
    if len(text) != 32:
        return ""
    try:
        bytes.fromhex(text)
    except ValueError:
        return ""
    return f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"


def uuid_bytes(uuid: str) -> bytes:
    return bytes.fromhex(uuid.replace("-", ""))


def discovery_id(uuid: str) -> str:
    """Stable device id for an unregistered sign: 80 bits of the UUID's hash,
    so two signs don't collide, and 30 characters, inside DEVICE_ID_RE."""
    return DISCOVERY_PREFIX + hashlib.blake2s(uuid.encode("ascii"), digest_size=10).hexdigest()


def status_heartbeat(
    fields: dict[str, Any],
    *,
    uuid: str,
    connect_reason: str,
    interval_min: int | None = None,
    stays_awake: bool = False,
    sleep_until: float | None = None,
    observed_gap_s: float | None = None,
) -> dict[str, Any]:
    """Map a decoded status packet onto Tesserae's heartbeat keys.

    ``SignalStrength`` is already negated by pyvisionect; ``DeviceUptime`` is in
    minutes; ``Firmware`` is a ``{major, minor, ...}`` dict.

    The next-wake estimate rides along as the keys telemetry already reads:
    ``sleep_until`` when Tesserae has just told the sign to sleep until then,
    else ``next_sleep_s``: the gap actually observed between the sign's recent
    statuses (``observed_gap_s``) when there is one, because a real 32" reports
    about every 15 min whatever its check-in setting says; otherwise the sign's
    own check-in interval (``interval_min``, TCLV 29; the status packet's
    ``NextStatus`` when that hasn't been read yet). A sign that stays awake gets
    ``stays_awake`` and no estimate: it has no wake to predict."""
    out: dict[str, Any] = {"uuid": uuid, "connect_reason": connect_reason}

    def _int(name: str) -> int | None:
        value = fields.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    battery = _int("BatteryLevel")
    if battery is not None and 0 <= battery <= 100:
        out["battery_pct"] = battery
    mv = _int("BatteryVoltage")
    if mv is not None and 0 < mv < 10_000:
        out["battery_mv"] = mv
    rssi = _int("SignalStrength")
    if rssi is not None and -150 < rssi < 0:
        out["rssi"] = rssi
    temp = _int("AverageTemperature")
    if temp is not None and -60 < temp < 100:
        out["temperature_c"] = temp
    uptime = _int("DeviceUptime")
    if uptime is not None and uptime >= 0:
        out["uptime_s"] = uptime * 60
    fw = fields.get("Firmware")
    if isinstance(fw, dict):
        parts = [
            str(fw[k]) for k in ("major", "minor", "revision", "build") if fw.get(k) is not None
        ]
        if parts:
            out["fw_version"] = ".".join(parts)
    if stays_awake:
        out["stays_awake"] = True
    elif sleep_until is not None:
        out["sleep_until"] = sleep_until
    elif observed_gap_s is not None and STATUS_GAP_MIN_S <= observed_gap_s <= STATUS_GAP_MAX_S:
        out["next_sleep_s"] = round(observed_gap_s)
    else:
        minutes = interval_min if interval_min else _int("NextStatus")
        if minutes is not None and 1 <= minutes <= HEARTBEAT_MAX_MINUTES:
            out["next_sleep_s"] = minutes * 60
    return out


def observed_status_gap_s(times: Sequence[float]) -> float | None:
    """The typical gap between a sign's consecutive statuses, from the wall
    clock times it was recorded at (oldest first): the median of the gaps
    that look like check-ins. Gaps under ``STATUS_GAP_MIN_S`` are a
    reconnect's second status or a requested one; over ``STATUS_GAP_MAX_S``
    is a sign that was off. ``None`` until there is one such gap."""
    gaps = [b - a for a, b in pairwise(times)]
    usable = sorted(g for g in gaps if STATUS_GAP_MIN_S <= g <= STATUS_GAP_MAX_S)
    if not usable:
        return None
    mid = len(usable) // 2
    return usable[mid] if len(usable) % 2 else (usable[mid - 1] + usable[mid]) / 2


def desired_params(cfg: dict[str, Any]) -> dict[int, int | str]:
    """The sign settings a device's config asks for, as TCLV id to value:
    ``heartbeat_min`` (minutes between check-ins) and the ``SIGN_SETTINGS``
    choices (``frame_sleep`` is "sleep", "awake" or "cli_off"). Blank, absent or junk
    leaves the sign's own setting."""
    out: dict[int, int | str] = {}
    try:
        minutes = int(str(cfg.get("heartbeat_min") or "").strip())
    except ValueError:
        minutes = 0
    if 1 <= minutes <= HEARTBEAT_MAX_MINUTES:
        out[PARAM_HEARTBEAT] = minutes
    for name, (pid, choices) in SIGN_SETTINGS.items():
        chosen = str(cfg.get(name) or "").strip()
        for config_value, wire, _label in choices:
            if chosen == config_value:
                out[pid] = wire
    for name, (pid, _gate) in TEXT_SETTINGS.items():
        text = text_setting_value(name, cfg.get(name))
        if text is not None:
            out[pid] = text
    return out


def status_summary(status: Any) -> dict[str, Any]:
    """What the device page shows from a sign's last status packet."""
    if status is None:
        return {}
    try:
        fields = status.fields()
    except Exception:
        return {}
    out: dict[str, Any] = {}
    fw = getattr(status, "firmware_version", None)
    if fw:
        out["firmware"] = ".".join(str(n) for n in fw)
    for key, name in (
        ("battery_pct", "BatteryLevel"),
        ("battery_mv", "BatteryVoltage"),
        ("rssi", "SignalStrength"),
        ("temperature_c", "AverageTemperature"),
        ("uptime_min", "DeviceUptime"),
        ("next_status_min", "NextStatus"),
    ):
        value = fields.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            out[key] = value
    for key, name in (("hardware", "Hardware"), ("bootloader", "Bootloader")):
        version = fields.get(name)
        if isinstance(version, dict):
            parts = [
                str(version[k])
                for k in ("major", "minor", "revision")
                if version.get(k) is not None
            ]
            if parts:
                out[key] = ".".join(parts)
    used = fields.get("ConnectivityUsed")
    if isinstance(used, dict):
        used = used.get("name")
    if used:
        out["connectivity"] = str(used)
    charging = fields.get("ChargingStatus")
    if isinstance(charging, dict):
        charging = charging.get("name")
    if charging:
        out["charging"] = f"charger {charging}"
    return out


def firmware_identity(status: Any) -> dict[str, Any]:
    """What a status packet says about the sign's firmware and hardware: the
    fields firmware is matched on. Anything the sign didn't report is None."""
    if status is None:
        return {}
    try:
        fields = status.fields()
    except Exception:
        return {}

    def _num(value: Any) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value == 0xFFFFFFFF:
            return None
        return value

    def _triple(name: str) -> tuple[int, int, int] | None:
        raw = fields.get(name)
        if not isinstance(raw, dict):
            return None
        nums = [_num(raw.get(k)) for k in ("major", "minor", "revision")]
        return (nums[0], nums[1], nums[2]) if all(n is not None for n in nums) else None  # type: ignore[return-value]

    return {
        "firmware": _triple("Firmware"),
        "hardware": _triple("Hardware"),
        "hw_name_id": _num(fields.get("HardwareNameID")),
        "display_id": _num(fields.get("DisplayType")),
        "checksum": _num(fields.get("FirmwareID")),
    }


def status_line(status: Any) -> str:
    """A status packet as one exchange-log line."""
    st = status_summary(status)
    parts = [f"status ({getattr(status, 'connect_reason_name', '') or '?'})"]
    if "battery_pct" in st:
        parts.append(f"battery {st['battery_pct']}%")
    if "rssi" in st:
        parts.append(f"{st['rssi']} dBm")
    crc = getattr(status, "display_state_crc", None)
    if crc is not None:
        parts.append(f"shows CRC {crc}")
    if "next_status_min" in st:
        parts.append(f"next in {st['next_status_min']} min")
    return ", ".join(parts)


def params_line(packet: Any) -> str:
    """A settings answer as one exchange-log line."""
    names = {**PARAM_NAMES, PARAM_WIFI_SSID: "Wi-Fi"}
    parts = []
    for item in getattr(packet, "items", ()):
        pid, control = int(item.id), int(item.control)
        name = names.get(pid, f"TCLV {pid}")
        if control in (PARAM_READ_ERROR, PARAM_WRITE_ERROR):
            parts.append(f"{name}: {'read' if control == PARAM_READ_ERROR else 'write'} refused")
        elif pid in TEXT_PARAMS:
            parts.append(f"{name} {_text_value(item.value)}")
        elif item.value:
            parts.append(f"{name} {int.from_bytes(item.value, 'little')}")
        else:
            parts.append(f"{name} ok")
    return "settings: " + (", ".join(parts) or "empty")


def artifact_path(renders_dir: Path, latest: dict[str, Any]) -> Path | None:
    """The renderer artifact a ``latest_render_for`` entry points at, kept
    inside ``renders_dir`` whatever the entry says."""
    digest = str(latest.get("digest") or "")
    name = str(
        latest.get("filename") or (f"{digest}.{latest.get('ext') or 'png'}" if digest else "")
    )
    name = Path(name).name
    if not name or name in (".", ".."):
        return None
    return renders_dir / name


def resend_stamp(latest: dict[str, Any] | None) -> float:
    """The timestamp of a History "resend" the latest render carries (its
    one-shot ``force_refetch`` flag), or 0.0. Each resend writes a new
    entry, so the timestamp tells one resend of the same frame from the
    next."""
    if not latest or not latest.get("force_refetch"):
        return 0.0
    try:
        return float(latest.get("timestamp") or 0.0) or 1.0
    except (TypeError, ValueError):
        return 1.0


def perturbed_checksum(checksum: int) -> int:
    """A frame checksum the sign can't already hold, so it treats an
    unchanged frame as new and repaints it (pyvisionect's ``force_full``
    lever, computed here so the ACKed state checksum is known)."""
    return ((checksum ^ 0x8000_0000) & 0xFFFF_FFFF) or 1


@dataclass(frozen=True)
class _Context:
    app: Flask
    devices: DeviceRegistry
    settings: Any
    renders_dir: Path
    latest_render_fn: Callable[[str], dict[str, Any] | None]
    # PushManager.render_in_progress: a push for this device is under way.
    render_busy_fn: Callable[[str], bool] = lambda _device_id: False
    # PushManager.record_frame_served: the sign took this render. Blocking
    # (it takes the manager's lock and may write latest_renders.json), so it
    # runs on an executor thread, never on the loop.
    record_served_fn: Callable[[str, dict[str, Any]], None] = lambda _device_id, _render: None


@dataclass
class _Inflight:
    """A frame or sleep command written to one connection, awaiting its ACK."""

    kind: str  # "image" or "sleep"
    packet_id: int
    conn: Any  # the DeviceConnection it went out on
    writer: Any
    digest: str = ""
    render: dict[str, Any] = field(default_factory=dict)  # the latest-render entry behind digest
    checksum: int = 0
    minutes: int = 0
    deadline: float = math.inf  # monotonic; set once the write has finished
    resend_stamp: float = 0.0  # the History resend this frame answers, if any


@dataclass
class _Sign:
    """Loop-thread state for one sign UUID."""

    uuid: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    dirty: bool = False
    # The one connection Tesserae talks to for this UUID, which isn't always
    # the library's latest: a same-UUID connection from another address is
    # refused while this one lives. Events from any other connection are
    # ignored, and geometry and CRC come from this connection's status only.
    conn: Any = None  # pyvisionect DeviceConnection
    writer: Any = None  # its asyncio StreamWriter
    status: Any = None  # its last StatusPacket
    heard_at: float = 0.0  # monotonic; its last status or ACK
    probe_at: float = 0.0  # monotonic; a status request sent to check it lives
    refused: dict[str, Any] | None = None  # the last same-UUID connection refused
    trusted_ip: str = ""  # the address that last acknowledged a frame or sleep
    trusted_at: float = 0.0  # wall clock of that acknowledgement
    frame: Any = None  # EncodedFrame for frame_digest
    frame_digest: str = ""
    frame_render: dict[str, Any] = field(default_factory=dict)  # the render entry frame came from
    failed_digest: str = ""  # a latest render that couldn't be read or encoded
    failed_stamp: Any = None  # that render's publication timestamp
    failed_retry_at: float = 0.0  # monotonic; when to try it again
    failed_tries: int = 0
    failed_logged: str = ""  # the failing digest already logged (once each)
    resend_stamp: float = 0.0  # the last History resend honoured (its timestamp)
    hold_since: float = 0.0  # monotonic; sleep held for a render in progress
    shown_digest: str = ""  # acked by the sign during this run
    shown_checksum: int | None = None  # its state checksum (status tag 9)
    shown_at: float = 0.0  # wall clock of that ACK
    # (device id, render entry) of the newest ACK not yet handed to the push
    # manager, and the task draining it: latest wins, one executor job at a time.
    pending_served: tuple[str, dict[str, Any]] | None = None
    served_task: asyncio.Task[None] | None = None
    crc_corrected: bool = False  # one corrective resend per mismatch
    corrected_digest: str = ""  # the frame that corrective resend was for
    inflight: _Inflight | None = None  # at most one frame or sleep at a time
    retry_at: float = 0.0  # monotonic; after a refused frame
    sleep_retry_at: float = 0.0  # monotonic; after a refused sleep
    retry_handle: asyncio.TimerHandle | None = None
    retry_when: float = 0.0  # loop time retry_handle fires
    repaint_until: float = 0.0  # monotonic; a size change is repainting
    conformed: bool = False  # panel conformance tried this run
    asleep_until: float = 0.0
    # The sign's check-in interval (minutes) and sleep-mode switch as last read.
    # Unlike ``params`` they outlive the connection, so the status that opens
    # the next connection (before its settings are read again) is already
    # recorded with the sign's own cadence.
    known_interval_min: int | None = None
    known_sleep_disabled: bool | None = None
    # Every setting the sign has answered (id -> value), and the ids it
    # refused to read. Both outlive the connection so the device page keeps
    # offering what the frame supports while it sleeps.
    known_params: dict[int, Any] = field(default_factory=dict)
    params_unsupported: set[int] = field(default_factory=set)
    known_written: dict[int, Any] = field(default_factory=dict)  # write-only settings sent
    # Settings packets waiting to go, one at a time (``_pump_params``): the one
    # out now, and when the sign's settings were last read.
    param_queue: deque[tuple[str, Any]] = field(default_factory=deque)
    # Connections (when, why) and pictures taken over the last day, and the
    # network details last read. In memory: they start over with Tesserae.
    connections: deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=4000))
    pictures: deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    network: dict[int, str] = field(default_factory=dict)
    stuck_warned: bool = False
    param_out: str = ""
    param_out_values: dict[int, Any] = field(default_factory=dict)  # the write that is out
    # Writes the frame never answered, as id -> (value, when). Kept across its
    # connections, so a frame that reconnects often isn't sent them each time.
    unanswered: dict[int, tuple[Any, float]] = field(default_factory=dict)
    settings_read_at: float = 0.0
    # Wall clock times of the sign's recent statuses, newest last. The gap
    # between them is its real check-in cadence, which the configured interval
    # (TCLV 29) is not: a 32" set to 30 min reports about every 15.
    status_times: deque[float] = field(default_factory=lambda: deque(maxlen=STATUS_TIMES_KEPT))
    remote: str = ""  # the connection's peer address
    pending_status: tuple[Any, Any] | None = None  # (device, StatusPacket) to record
    status_task: asyncio.Task[None] | None = None  # records pending_status, latest wins
    connected_at: float = 0.0
    connect_reason: str = ""  # why the sign dialed this connection (reboot, wakeup...)
    last_seen: float = 0.0
    last_error: str = ""
    # The sign's settings as read on this connection (READ_PARAMS), and the
    # writes asked of it: ``params_asked`` once the read went out,
    # ``params_written`` the settings last written (never written twice in a
    # row, so a sign that won't take them can't loop), ``param_error`` why it
    # refused.
    params: dict[int, Any] = field(default_factory=dict)
    params_asked: bool = False
    params_wait_until: float = 0.0  # monotonic; pictures wait for a settings answer till then
    params_written: tuple[tuple[int, int | str], ...] = ()
    param_error: str = ""
    force_refresh: bool = False  # Full refresh asked from the device page
    # What went to and came from the frame, newest last: (wall time,
    # "out" / "in" / "note", text). Kept across its connections.
    exchanges: deque[tuple[float, str, str]] = field(
        default_factory=lambda: deque(maxlen=EXCHANGE_LOG_LINES)
    )
    action: dict[str, Any] | None = None  # the last action sent ({kind, at}), for the device page


def sign_panel(status: Any) -> Any:
    """pyvisionect panel geometry for a sign's status (its DisplayType)."""
    from pyvisionect.devices.panels import panel_for

    display_type = getattr(status, "display_type", None)
    return panel_for(display_type if display_type is not None else -1)


class VisionectService:
    """The listener plus the glue between it and the push pipeline."""

    def __init__(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.start_error: str | None = None
        self.autostart = True
        self._ctx: _Context | None = None
        self._ctx_gen = 0
        # Lifecycle. start(), stop(), maybe_start() and the retry timer run
        # on request, push and timer threads; they serialise on this lock,
        # and a run never starts while the previous run's thread is alive,
        # so two runs never share the state below.
        self._lifecycle = threading.RLock()
        self._lifecycle_gen = 0  # bumped by stop(): a retry armed before it is void
        self._stopping = False  # stop() timed out joining; that thread still winds down
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None  # pyvisionect.io.VisionectServer
        self._store: Any = None  # pyvisionect.session.DeviceStateStore
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()
        self._last_start_attempt = 0.0
        # Per run, replaced by start(). Touched on the loop thread only.
        self._signs: dict[str, _Sign] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._warned_unbound: dict[str, None] = {}  # insertion-ordered, capped
        self._discovered: dict[str, None] = {}  # discovery ids we listed, oldest first
        self._retry_timer: threading.Timer | None = None
        # Firmware flashes, one job per UUID (the newest; a finished one stays
        # for the device page). Guarded by _flash_lock: request threads create
        # jobs, the loop thread runs them.
        self._flash_lock = threading.Lock()
        self._flash_jobs: dict[str, visionect_flash.FlashJob] = {}
        self.firmware_repo: FirmwareRepository | None = None
        # Read by Flask request threads; written on the loop thread.
        self._snapshot_lock = threading.Lock()
        self._snapshot: dict[str, dict[str, Any]] = {}

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive() and self._ready.is_set()

    def attach(self, ctx: _Context) -> None:
        """Point the service at a (possibly rebuilt) app. Signs already
        connected are rebound and re-run, so a device registered while its
        sign waits gets its frame without waiting for the next check-in."""
        self._ctx = ctx
        self._ctx_gen += 1
        self._call_soon(self._rescan)

    def auto_bind_open(self, device: Device) -> bool:
        """Whether this blank device still binds the next unknown sign:
        within AUTO_BIND_WINDOW_S of being added or having its UUID cleared."""
        armed = self._device_cfg(device.id).get(AUTO_BIND_KEY)
        if isinstance(armed, bool) or not isinstance(armed, (int, float)) or armed <= 0:
            return False
        return time.time() - float(armed) < AUTO_BIND_WINDOW_S

    def start(self, timeout: float = 5.0) -> bool:
        """Bind the listener on a daemon thread. Returns whether it is up."""
        with self._lifecycle:
            thread = self._thread
            if thread is not None and thread.is_alive():
                if self._stopping:
                    # The last stop() gave up waiting for it. A new run now
                    # would race its teardown for the port and the state.
                    self.start_error = "the previous listener is still shutting down"
                    return False
                return self._ready.is_set()
            self._stopping = False
            self._last_start_attempt = time.monotonic()
            self.start_error = None
            # Fresh per-run state: the finished run's thread cleared its own.
            ready = threading.Event()
            self._ready = ready
            self._signs = {}
            self._tasks = set()
            self._warned_unbound = {}
            self._discovered = {}
            with self._snapshot_lock:
                self._snapshot = {}
            thread = threading.Thread(
                target=self._run,
                args=(ready, self._signs, self._snapshot),
                name="visionect-listener",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            background_loops.track(self)
            # Return as soon as the bind succeeds or the thread dies (port
            # busy), not after the full timeout: register() runs on every
            # rebuild.
            deadline = time.monotonic() + timeout
            while not ready.is_set() and thread.is_alive():
                if time.monotonic() >= deadline:
                    break
                ready.wait(0.05)
            return ready.is_set()

    def maybe_start(self) -> bool:
        """Start if wanted and not running, at most every START_RETRY_S, so a
        port held by the old Visionect server is retried without hammering."""
        with self._lifecycle:
            if self.running or not self.autostart or not self._visionect_devices():
                return self.running
            if time.monotonic() - self._last_start_attempt < START_RETRY_S:
                return False
            ok = self.start()
            if not ok:
                logger.warning(
                    "visionect: can't listen on port %d (%s); retrying. Is the old Visionect "
                    "server still running?",
                    self.port,
                    self.start_error or "timed out",
                )
                self._arm_retry()
            return ok

    def _arm_retry(self) -> None:
        """Retry the bind on a timer, independent of pushes, until it works
        or no Visionect device is left."""
        with self._lifecycle:
            timer = self._retry_timer
            if timer is not None and timer.is_alive():
                return
            timer = threading.Timer(START_RETRY_S, self._retry_fire, args=(self._lifecycle_gen,))
            timer.daemon = True
            self._retry_timer = timer
            timer.start()

    def _retry_fire(self, gen: int) -> None:
        with self._lifecycle:
            if gen != self._lifecycle_gen:
                return  # stopped since this retry was armed
            self._retry_timer = None
            self._last_start_attempt = 0.0
            self.maybe_start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lifecycle:
            self._lifecycle_gen += 1
            timer, self._retry_timer = self._retry_timer, None
            if timer is not None:
                timer.cancel()
            self._ready.clear()  # not "running" while it winds down
            loop, stop_event = self._loop, self._stop_event
            if loop is not None and stop_event is not None and not loop.is_closed():
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(stop_event.set)
            thread = self._thread
            if thread is None:
                return
            if thread is not threading.current_thread():
                thread.join(timeout)
            if thread.is_alive():
                # Still closing sockets or draining its executor. Keep the
                # handle: start() refuses until this thread has exited.
                self._stopping = True
                logger.warning(
                    "visionect: listener still shutting down after %.0fs; it is not "
                    "restarted until it has",
                    timeout,
                )
                return
            self._thread = None
            self._stopping = False

    def _run(
        self,
        ready: threading.Event,
        signs: dict[str, _Sign],
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """The listener thread. Tears down only what this run created: the
        arguments are its own, and shared handles are cleared only while
        they are still the ones it set."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve(ready))
        except Exception as exc:
            self.start_error = str(exc)
            logger.debug("visionect: listener stopped: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            if self._loop is loop:
                self._loop = None
            ready.clear()
            signs.clear()
            with self._snapshot_lock:
                snapshot.clear()

    async def _serve(self, ready: threading.Event) -> None:
        from app.visionect_listener import GuardedServer, PrunableStore

        stop_event = asyncio.Event()
        store = PrunableStore()
        server = GuardedServer(
            on_events=self._on_events, store=store, host=self.host, port=self.port
        )
        server.keep_identity = self._keeps_identity
        server.trusted_peer = self._trusted_peer
        server.bootloader_wants = self._flash_wants
        server.bootloader_handler = self._flash_connection
        self._stop_event, self._store, self._server = stop_event, store, server
        await server.start()
        logger.info("visionect: listening for signs on %s:%d", self.host, self.port)
        ready.set()
        try:
            while not stop_event.is_set():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop_event.wait(), timeout=TICK_S)
                if not stop_event.is_set():
                    self._rescan()
        finally:
            await self._shutdown(server)

    async def _shutdown(self, server: Any) -> None:
        """Close the listener and every sign's socket, then the tasks.

        A sign holds its socket open, and on Python < 3.13 asyncio.Server has
        no close_clients()/abort_clients(), so wait_closed() would wait for
        it forever: abort the transports ourselves first."""
        self._flash_stop_all()
        writers = getattr(server, "_writers", None)
        owned = [sign.writer for sign in list(self._signs.values()) if sign.writer is not None]
        if isinstance(writers, dict):
            owned.extend(writers.values())
        for writer in owned:
            with contextlib.suppress(Exception):
                writer.transport.abort()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server.close(abort=True), timeout=3.0)
        current = asyncio.current_task()
        pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=3.0
                )

    def _call_soon(self, fn: Callable[[], None]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed() or not self.running:
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(fn)

    # -- push side (called from the push thread) ---------------------------

    def on_push(self, _result: Any = None) -> None:
        """PushManager listener: re-run every bound sign on the loop, which
        re-reads its latest render and sends it if it changed."""
        if not self.running:
            self.maybe_start()
            return
        self._call_soon(self._rescan)

    def sync_soon(self) -> None:
        """Re-run the signs without ever waiting on the caller's thread: the
        device kind hooks call this while a config save holds
        ``CONFIG_SAVE_LOCK``, and :meth:`maybe_start` can wait for the bind
        (up to ``start``'s timeout). A running listener is rescanned on its
        loop; a stopped one is started on a short-lived thread."""
        if self.running:
            self._call_soon(self._rescan)
            return
        threading.Thread(target=self.maybe_start, name="visionect-start", daemon=True).start()

    # -- device lookup -----------------------------------------------------

    def _visionect_devices(self) -> list[Device]:
        ctx = self._ctx
        if ctx is None:
            return []
        return visionect_devices(ctx.devices)

    def _device_cfg(self, device_id: str) -> dict[str, Any]:
        ctx = self._ctx
        if ctx is None:
            return {}
        section = ctx.settings.get_section("devices") or {}
        cfg = section.get(device_id) if isinstance(section, dict) else None
        return dict(cfg) if isinstance(cfg, dict) else {}

    def device_uuid(self, device_id: str) -> str:
        return normalise_uuid(self._device_cfg(device_id).get("uuid"))

    def _device_for_uuid(self, uuid: str) -> Device | None:
        for device in self._visionect_devices():
            if self.device_uuid(device.id) == uuid:
                return device
        return None

    def _bind(self, uuid: str) -> Device | None:
        """Bind a sign we don't know to the first visionect device (by id)
        with no UUID whose auto-bind window is open. Persisted so the binding
        survives a restart; a device registered from the discovered banner
        already carries its UUID."""
        ctx = self._ctx
        if ctx is None:
            return None
        blank = [
            d
            for d in sorted(self._visionect_devices(), key=lambda d: d.id)
            if not self.device_uuid(d.id) and self.auto_bind_open(d)
        ]
        if not blank:
            return None
        chosen = next((d for d in blank if bind_sign(ctx.settings, ctx.devices, d, uuid)), None)
        if chosen is None:
            return None
        self._forget_discovery(uuid)
        logger.info("visionect: bound sign %s to device %s", uuid, chosen.id)
        return chosen

    def _announce_unknown(self, uuid: str, status: Any) -> None:
        """List an unbound sign under Settings -> Devices -> discovered."""
        ctx = self._ctx
        cache = ctx.app.config.get("DISCOVERY_CACHE") if ctx is not None else None
        if cache is None:
            return
        width, height = canvas_size(sign_panel(status)) or (1440, 2560)
        beat = status_heartbeat(
            status.fields(), uuid=uuid, connect_reason=str(status.connect_reason_name)
        )
        payload: dict[str, Any] = {
            "kind": self._model_for((width, height)),
            "transport": "visionect",
            "uuid": uuid,
            "name": "Visionect sign",
            "panel_w": width,
            "panel_h": height,
            "ip": self._signs[uuid].remote if uuid in self._signs else "",
        }
        if beat.get("fw_version"):
            payload["fw_version"] = beat["fw_version"]
        entry_id = discovery_id(uuid)
        previous = cache.get(entry_id)
        if previous is not None and previous.parsed.get("uuid") != uuid:
            cache.forget(entry_id)  # another sign's entry: don't merge into it
        cache.record(entry_id, json.dumps(payload).encode("utf-8"))
        self._discovered.pop(entry_id, None)
        self._discovered[entry_id] = None
        while len(self._discovered) > MAX_DISCOVERED:
            oldest = next(iter(self._discovered))
            del self._discovered[oldest]
            cache.forget(oldest)

    def _model_for(self, canvas: tuple[int, int]) -> str:
        """The catalog model whose native size is the sign's, when exactly
        one is (a Joan 6, the 32"); else the generic kind. Preselects the
        Register picker; the panel conforms to the sign either way."""
        ctx = self._ctx
        if ctx is None:
            return KIND
        models = [
            d.id
            for d in list(ctx.devices.devices.values())
            if d.kind_of is None
            and d.id != KIND
            and is_visionect_device(d)
            and native_size(d) == canvas
        ]
        return models[0] if len(models) == 1 else KIND

    def _forget_discovery(self, uuid: str) -> None:
        ctx = self._ctx
        cache = ctx.app.config.get("DISCOVERY_CACHE") if ctx is not None else None
        self._discovered.pop(discovery_id(uuid), None)
        if cache is not None:
            cache.forget(discovery_id(uuid))

    # -- loop side ---------------------------------------------------------

    def _sign(self, uuid: str) -> _Sign:
        sign = self._signs.get(uuid)
        if sign is None:
            sign = _Sign(uuid=uuid)
            self._signs[uuid] = sign
        return sign

    @staticmethod
    def _alive(sign: _Sign) -> bool:
        """Whether the sign's connection can still carry a frame. A socket
        the library is closing, or one it forgot to remove, counts as gone."""
        from app.visionect_listener import is_closing

        conn, writer = sign.conn, sign.writer
        return (
            conn is not None
            and writer is not None
            and not getattr(conn, "closed", False)
            and not is_closing(writer)
        )

    def _disconnected(self, sign: _Sign) -> None:
        """Forget the sign's connection; nothing is in flight on a dead one."""
        if sign.conn is not None:
            self._note(sign, "note", "connection closed")
        sign.conn = sign.writer = None
        sign.inflight = None
        sign.probe_at = 0.0

    def _drop(self, sign: _Sign) -> None:
        """Close the sign's connection; it re-dials."""
        from app.visionect_listener import abort

        abort(sign.writer)
        self._disconnected(sign)

    def _rescan(self) -> None:
        """Re-run every connected sign, binding any that still need a device,
        and notice connections that went away (the library raises no event
        for that)."""
        if self._server is None:
            return
        for uuid, sign in list(self._signs.items()):
            if sign.conn is not None and not self._alive(sign):
                self._disconnected(sign)
            bound = self._device_for_uuid(uuid) is not None
            if sign.conn is None and not bound:
                self._forget_sign(uuid)  # an unbound sign that left: keep nothing
                continue
            if sign.conn is not None and (bound or self._bind(uuid) is not None):
                self._schedule(uuid)
            self._publish(sign)

    def _forget_sign(self, uuid: str) -> None:
        """Drop all per-UUID state for a sign. Its discovered entry stays (a
        sleeping sign must stay listed to be registered); those are capped."""
        sign = self._signs.pop(uuid, None)
        if sign is not None and sign.retry_handle is not None:
            sign.retry_handle.cancel()
        with self._snapshot_lock:
            self._snapshot.pop(uuid, None)
        key = uuid_bytes(uuid)
        discard = getattr(self._store, "discard", None)
        if callable(discard):
            discard(key)
        stats = getattr(self._server, "stats", None)
        last_seen = getattr(stats, "last_seen", None)
        if isinstance(last_seen, dict):
            last_seen.pop(key, None)

    def _trusted_peer(self, ip: str) -> bool:
        """A bound sign's own address: exempt from the total connection cap."""
        return bool(ip) and any(
            (sign.trusted_ip or sign.remote) == ip
            for uuid, sign in list(self._signs.items())
            if self._device_for_uuid(uuid) is not None
        )

    def _note_trusted(self, sign: _Sign) -> None:
        """An acknowledgement from the sign's connection: see TRUST_STALE_S."""
        ip, now = sign.remote, time.time()
        if ip and (
            not sign.trusted_ip or ip == sign.trusted_ip or now - sign.trusted_at > TRUST_STALE_S
        ):
            sign.trusted_ip, sign.trusted_at = ip, now

    def _keeps_identity(self, device_id: bytes) -> bool:
        """The listener keeps library state only for UUIDs tracked here."""
        return normalise_uuid(device_id.hex()) in self._signs

    def _unknown_signs(self) -> int:
        return sum(1 for uuid in self._signs if self._device_for_uuid(uuid) is None)

    def _schedule(self, uuid: str, *, delay: float = 0.0) -> None:
        sign = self._signs.get(uuid)
        if sign is None:
            return
        loop = asyncio.get_running_loop()
        if delay > 0:
            # One timer per sign, the earliest asked for: the pass it runs
            # re-arms whatever is due next.
            when = loop.time() + delay
            if sign.retry_handle is not None:
                if sign.retry_when <= when:
                    return
                sign.retry_handle.cancel()
            sign.retry_handle = loop.call_at(when, self._retry_due, uuid)
            sign.retry_when = when
            return
        if sign.lock.locked():
            sign.dirty = True
            return
        task = loop.create_task(self._reconcile(sign))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _retry_due(self, uuid: str) -> None:
        sign = self._signs.get(uuid)
        if sign is not None:
            sign.retry_handle = None
        self._schedule(uuid)

    async def _reconcile(self, sign: _Sign) -> None:
        """One pass at a time per sign; a request during a pass re-runs it."""
        async with sign.lock:
            while True:
                sign.dirty = False
                try:
                    await self._reconcile_once(sign)
                except Exception:
                    logger.exception("visionect: reconcile failed for %s", sign.uuid)
                self._publish(sign)
                if not sign.dirty:
                    return

    def _expire(self, sign: _Sign) -> None:
        """Give up on an ACK or a liveness probe that is overdue."""
        now = time.monotonic()
        inflight = sign.inflight
        if inflight is not None and now >= inflight.deadline:
            sign.inflight = None
            what = "picture" if inflight.kind == "image" else "sleep command"
            self._note(sign, "note", f"no answer to the {what}; dropping the connection")
            sign.last_error = (
                f"The frame never confirmed the {what}, so Tesserae dropped the connection; "
                "it re-dials and gets the latest state."
            )
            logger.warning(
                "visionect: sign %s never acked the %s; dropping the connection so it "
                "re-dials and gets the latest state",
                sign.uuid,
                what,
            )
            if inflight.conn is sign.conn:
                self._drop(sign)
        if sign.probe_at and now >= sign.probe_at + PROBE_TIMEOUT_S:
            probed, sign.probe_at = sign.probe_at, 0.0
            if sign.conn is not None and sign.heard_at < probed:
                logger.warning(
                    "visionect: sign %s's connection from %s didn't answer a status request; "
                    "dropping it",
                    sign.uuid,
                    sign.remote or "?",
                )
                self._drop(sign)
        # Come back when the next of them is due.
        due = [math.inf]
        if sign.inflight is not None:
            due.append(sign.inflight.deadline)
        if sign.probe_at:
            due.append(sign.probe_at + PROBE_TIMEOUT_S)
        soonest = min(due)
        if soonest < math.inf:
            self._schedule(sign.uuid, delay=max(0.05, soonest - now + 0.05))

    async def _reconcile_once(self, sign: _Sign) -> None:
        ctx, gen = self._ctx, self._ctx_gen
        device = self._device_for_uuid(sign.uuid)
        if ctx is None or device is None or self._server is None:
            return
        loop = asyncio.get_running_loop()
        if self._flash_active(sign.uuid):
            return  # no pictures to a frame that is being flashed
        self._expire(sign)
        status = sign.status  # from the sign's own connection only
        panel = sign_panel(status) if status is not None else None

        # 0. The sign's own DisplayType is the truth about its buffer (a
        #    Joan 6 is 1024x758, the 32" is 4 x 1440x640): conform the
        #    device's native size to it and re-render before sending.
        if status is not None and not sign.conformed:
            canvas = canvas_size(panel)
            if canvas is not None and native_size(device) != canvas:
                sign.conformed = True  # once per run: never a repaint loop
                changed = await loop.run_in_executor(
                    None,
                    conform_panel,
                    device,
                    canvas,
                    kind_default_dims(ctx.devices, device),
                    functools.partial(ctx.devices.devices.get, device.id),
                )
                if changed:
                    sign.frame, sign.frame_digest = None, ""
                    sign.repaint_until = time.monotonic() + REPAINT_WAIT_S
                    push_mgr = ctx.app.config.get("PUSH_MANAGER")
                    if push_mgr is not None:
                        await loop.run_in_executor(
                            None,
                            functools.partial(
                                push_mgr.invalidate_and_repaint, device.id, reencode=False
                            ),
                        )
                    return  # the repaint's push re-runs us

        latest = ctx.latest_render_fn(device.id)
        digest = str((latest or {}).get("digest") or "")

        # 1. Have the latest frame encoded. Geometry comes from the sign's
        #    own DisplayType, so wait for its first status. A render that
        #    can't be read or encoded is noted once and skipped: the sign
        #    keeps (and may sleep on) the frame it has, rather than this
        #    pass failing every minute all night.
        stamp = (latest or {}).get("timestamp")
        backing_off = (
            digest == sign.failed_digest
            and stamp == sign.failed_stamp
            and time.monotonic() < sign.failed_retry_at
        )
        if (
            latest
            and digest
            and digest != sign.frame_digest
            and not backing_off
            and status is not None
        ):
            path = artifact_path(ctx.renders_dir, latest)
            try:
                if path is None or not path.exists():
                    raise FileNotFoundError(f"{path} is missing")
                frame = await loop.run_in_executor(None, _encode, path, panel)
            except Exception as exc:
                self._render_failed(sign, device, digest, stamp, exc)
            else:
                current = ctx.latest_render_fn(device.id) or {}
                if (
                    self._ctx_gen != gen
                    or str(current.get("digest") or "") != digest
                    or self._device_for_uuid(sign.uuid) is not device
                    or getattr(sign.status, "display_type", None) != status.display_type
                ):
                    # Superseded while encoding (a newer render, another
                    # device, a different panel); start over. A new status
                    # with the same geometry doesn't count.
                    sign.dirty = True
                    return
                sign.frame, sign.frame_digest = frame, digest
                sign.frame_render = dict(latest)
                sign.failed_digest = sign.failed_logged = ""
                sign.failed_tries = 0
                sign.repaint_until = 0.0
                sign.last_error = ""
                checksum = int(frame.state_checksum)
                # The newest status (same geometry, checked above): one that
                # arrived during the encode may report a different CRC.
                reported = getattr(sign.status, "display_state_crc", None)
                if not sign.shown_digest and reported in (checksum, perturbed_checksum(checksum)):
                    # Before our first ACK this run (a restart) the sign's CRC
                    # is the best evidence: it already shows this frame (as a
                    # History resend left it, perhaps). Its checksum and time
                    # arm the glass check, as an ACK would.
                    sign.shown_digest = digest
                    sign.shown_checksum = int(reported)
                    sign.shown_at = time.time()
                    self._served(sign, device.id, sign.frame_render)

        # A flash may have been asked for during the encode.
        if not self._alive(sign) or sign.inflight is not None or self._flash_active(sign.uuid):
            return
        conn, writer = sign.conn, sign.writer

        # A settings read or write is out: hold the picture until the frame
        # answers (or PARAM_WAIT_S passes). A picture sent on top of an
        # unanswered settings packet made the 32" frame drop the connection
        # with "protocol error", over and over.
        wait = sign.params_wait_until - time.monotonic()
        if wait > 0:
            self._schedule(sign.uuid, delay=wait + 0.05)
            return

        # 2. Send the frame the sign hasn't acked, one at a time. A History
        #    "resend" of the frame it already shows, or Full refresh on the
        #    device page, is honoured once, with a checksum the sign can't
        #    hold, so it really repaints (ghosting).
        resend = resend_stamp(latest) if digest and digest == sign.frame_digest else 0.0
        repaint = (bool(resend) and resend != sign.resend_stamp) or sign.force_refresh
        if sign.frame is not None and (sign.frame_digest != sign.shown_digest or repaint):
            wait = sign.retry_at - time.monotonic()
            if wait > 0:
                self._schedule(sign.uuid, delay=wait)
                return
            checksum = int(sign.frame.state_checksum)
            resending = sign.frame_digest == sign.shown_digest
            if resending:
                # Any checksum other than the one the sign holds (after an
                # earlier resend that is the perturbed one).
                checksum = perturbed_checksum(checksum)
                if checksum == sign.shown_checksum:
                    checksum = int(sign.frame.state_checksum)
                packet_id = int(conn.send_image(sign.frame, checksum_override=checksum))
            else:
                packet_id = int(conn.send_image(sign.frame))
            sign.force_refresh = False
            inflight = _Inflight(
                kind="image",
                packet_id=packet_id,
                conn=conn,
                writer=writer,
                digest=sign.frame_digest,
                render=sign.frame_render,
                checksum=checksum,
                resend_stamp=resend,
            )
            sign.inflight = inflight
            self._note(
                sign,
                "out",
                f"picture {sign.frame_digest[:12]}"
                + (" (full repaint)" if resending else "")
                + (
                    f", {len(sign.frame.rectangles)} rectangles"
                    if hasattr(sign.frame, "rectangles")
                    else ""
                ),
            )
            self._publish(sign)  # "sending" while the write is under way
            logger.info(
                "visionect %s: sending frame %s to sign %s%s",
                device.id,
                sign.frame_digest[:12],
                sign.uuid,
                " (resend)" if resending else "",
            )
            await self._send(sign, inflight, IMAGE_ACK_TIMEOUT_S)
            return  # the ACK re-runs us; sleep must wait for it

        # 2b. The frame's own settings, only with the latest picture on the
        #     glass and nothing in flight: read them once per connection, then
        #     write (and save to flash) what the device's config asks for.
        if await self._sync_params(sign, device):
            return  # its answer re-runs us; sleep waits for it

        # 3. Only with the latest frame on the glass: quiet-hours sleep, once
        #    (the sign takes a moment to drop the socket after acking it).
        if sign.asleep_until > time.time():
            return
        if sign.frame is None and sign.repaint_until > time.monotonic():
            return  # the size change's repaint hasn't produced a frame yet
        wait = sign.sleep_retry_at - time.monotonic()
        if wait > 0:
            self._schedule(sign.uuid, delay=wait)
            return
        minutes = self._sleep_minutes(device)
        if minutes is None:
            sign.hold_since = 0.0
            return
        # The frame to sleep on may still be rendering (a push that started
        # just before the window): wait for it, but not all night.
        now = time.monotonic()
        if ctx.render_busy_fn(device.id):
            if not sign.hold_since:
                sign.hold_since = now
            if now - sign.hold_since < RENDER_HOLD_MAX_S:
                self._schedule(sign.uuid, delay=RENDER_HOLD_POLL_S)
                return
            logger.warning(
                "visionect %s: still rendering after %.0f min; the sign sleeps on the frame it has",
                device.id,
                RENDER_HOLD_MAX_S / 60,
            )
        # A render that landed since this pass read the latest goes first.
        if str((ctx.latest_render_fn(device.id) or {}).get("digest") or "") != digest:
            sign.dirty = True
            return
        sign.hold_since = 0.0
        inflight = _Inflight(
            kind="sleep",
            packet_id=int(conn.sleep(minutes)),
            conn=conn,
            writer=writer,
            minutes=minutes,
        )
        sign.inflight = inflight
        self._note(sign, "out", f"sleep {minutes} min (quiet hours)")
        logger.info("visionect: quiet hours, sign %s sleeps for %d min", sign.uuid, minutes)
        await self._send(sign, inflight, SLEEP_ACK_TIMEOUT_S)

    def _render_failed(
        self, sign: _Sign, device: Device, digest: str, stamp: Any, exc: Exception
    ) -> None:
        """Note a latest render that couldn't be read or encoded. It is tried
        again with a back-off, or at once if it is published again, and
        logged once (a traceback only for an encode error)."""
        if digest != sign.failed_digest:
            sign.failed_tries = 0
        sign.failed_digest, sign.failed_stamp = digest, stamp
        sign.failed_tries = min(sign.failed_tries + 1, 32)
        backoff = min(FAILED_RETRY_S * 2 ** min(sign.failed_tries - 1, 16), FAILED_RETRY_MAX_S)
        sign.failed_retry_at = time.monotonic() + backoff
        sign.last_error = "The latest render couldn't be read, so the frame keeps its last picture."
        if sign.failed_logged == digest:
            logger.debug(
                "visionect %s: render %s still unreadable: %s", device.id, digest[:12], exc
            )
            return
        sign.failed_logged = digest
        logger.warning(
            "visionect %s: can't send render %s (%s); the sign keeps its last frame and it is "
            "tried again",
            device.id,
            digest[:12],
            exc,
            exc_info=not isinstance(exc, FileNotFoundError),
        )

    async def _send(self, sign: _Sign, inflight: _Inflight, ack_timeout: float) -> None:
        """Put what ``inflight`` queued on the wire, then start its ACK clock."""
        ok = await self._write(inflight.conn, inflight.writer)
        if sign.inflight is not inflight:
            return  # answered, or the sign reconnected, during the write
        if not ok:
            sign.inflight = None
            sign.last_error = (
                "The frame stopped taking data, so Tesserae dropped the connection; it re-dials."
            )
            if inflight.conn is sign.conn:
                self._drop(sign)
            return
        inflight.deadline = time.monotonic() + ack_timeout
        self._schedule(sign.uuid, delay=ack_timeout + 0.05)

    async def _write(self, conn: Any, writer: Any) -> bool:
        """Write what ``conn`` has queued to ``writer``, waiting at most
        WRITE_TIMEOUT_S for it to drain. False when it couldn't: the socket
        is dead or stuck, and a stuck one has been dropped."""
        from app.visionect_listener import abort, peer_ip

        data = conn.drain()
        if not data:
            return True
        try:
            writer.write(data)
            await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_S)
        except TimeoutError:
            logger.warning(
                "visionect: a write to %s blocked for %.0fs; dropping the connection",
                peer_ip(writer) or "?",
                WRITE_TIMEOUT_S,
            )
            abort(writer)
            return False
        except (ConnectionError, OSError, RuntimeError):
            return False
        stats = getattr(self._server, "stats", None)
        if stats is not None:
            stats.bytes_out += len(data)
        return True

    @staticmethod
    def _note(sign: _Sign, way: str, text: str) -> None:
        """One line of the frame's exchange log ("out", "in" or "note")."""
        sign.exchanges.append((time.time(), way, text))

    # -- the sign's own settings and actions ---------------------------------

    def _queue_read(self, sign: _Sign, ids: Sequence[int]) -> None:
        for start in range(0, len(ids), READ_CHUNK):
            sign.param_queue.append(("read", list(ids[start : start + READ_CHUNK])))

    def _apply_status_poll(self, sign: _Sign, device: Device) -> None:
        """Keep this connection's status poll at what the device page asks for
        (the library's connections otherwise share one setting)."""
        conn = sign.conn
        config = getattr(conn, "config", None)
        minutes = status_poll_minutes(self._device_cfg(device.id))
        if config is None or config.device_state_polling_minutes == minutes:
            return
        conn.config = replace(config, device_state_polling_minutes=minutes)
        logger.info(
            "visionect %s: asking sign %s for a status every %d min", device.id, sign.uuid, minutes
        )

    async def _pump_params(self, sign: _Sign) -> bool:
        """Send the next settings packet once the last has been answered, the
        way the Suite does. True while one is out (the caller waits for it). A
        frame that doesn't answer gets nothing more: above all no flash save
        after a write it may not have taken. The save itself may have no answer
        to wait for, so running out of time on that one is not a fault."""
        conn, writer = sign.conn, sign.writer
        if sign.param_out:
            if time.monotonic() < sign.params_wait_until:
                return True
            if sign.param_out != "save":
                sign.param_queue.clear()
                sign.param_error = "The frame didn't answer a settings request; Tesserae stopped."
                logger.warning("visionect: sign %s didn't answer %s", sign.uuid, sign.param_out)
            if sign.param_out == "write":
                # Whether it took them is unknown: read them next time it connects,
                # and don't send them again meanwhile.
                now = time.time()
                sign.unanswered.update({pid: (v, now) for pid, v in sign.param_out_values.items()})
                sign.settings_read_at = 0.0
            sign.param_out = ""
        if not sign.param_queue:
            return False
        kind, value = sign.param_queue.popleft()
        try:
            if kind == "read":
                conn.read_params(value)
            elif kind == "write":
                # The Wi-Fi ids are ones the Suite won't write over the network;
                # the library only guards them, and only ids from the tables
                # above ever get here.
                conn.write_params(value, allow_read_only=True)
            else:
                conn.write_params({PARAM_FLASH_SAVE: 1})
        except Exception as exc:
            sign.param_queue.clear()
            sign.param_error = f"Tesserae couldn't send the frame's settings: {exc}"
            logger.warning("visionect: settings for %s failed: %s", sign.uuid, exc)
            return False
        sign.param_out = kind
        sign.param_out_values = dict(value) if kind == "write" else {}
        self._await_params(sign)
        await self._write(conn, writer)
        return True

    async def _sync_params(self, sign: _Sign, device: Device) -> bool:
        """Read the sign's settings when they're due; once they're in, write
        the ones the device's config sets differently, save them to flash and
        read them back, one packet at a time. A set of writes goes out at most
        once per connection, so a sign that refuses one doesn't get it every
        pass. True when it sent something (the caller waits for the answer)."""
        self._apply_status_poll(sign, device)
        if await self._pump_params(sign):
            return True
        if not sign.params_asked:
            sign.params_asked = True
            self._note(sign, "out", "read settings")
            self._queue_read(sign, READ_PARAMS)
            return await self._pump_params(sign)
        # Only settings this sign has answered are written (the password by the
        # setting that shows the frame has it): one it doesn't have is skipped,
        # not allowed to hold up the rest.
        change = []
        for pid, v in desired_params(self._device_cfg(device.id)).items():
            if WRITE_GATE.get(pid, pid) not in sign.params:
                continue
            if sign.params.get(pid) == v or (pid, v) in sign.params_written:
                continue
            if pid in WRITE_ONLY and sign.known_written.get(pid) == v:
                continue  # can't be read back: written once, not on every connection
            tried = sign.unanswered.get(pid)
            if tried and tried[0] == v and time.time() - tried[1] < SETTINGS_REREAD_S:
                sign.param_error = (
                    f"The frame didn't answer when Tesserae set "
                    f"{PARAM_NAMES.get(pid, f'setting {pid}')}, so it may not have taken it. "
                    "Tesserae tries again in a few hours, or when you change it."
                )
                continue
            change.append((pid, v))
        # A (setting, value) pair goes out once per connection, so a stale
        # value still showing while a read back is in flight isn't rewritten.
        change = sorted(change, key=lambda item: item[0])
        if not change:
            return False
        sign.params_written = tuple(sorted({*sign.params_written, *change}, key=lambda i: i[0]))
        for pid, v in change:
            if pid in WRITE_ONLY:
                sign.known_written[pid] = v
        shown = ", ".join(
            f"{PARAM_NAMES.get(pid, pid)}={'(hidden)' if pid == PARAM_WIFI_PSK else v}"
            for pid, v in change
        )
        for start in range(0, len(change), READ_CHUNK):
            sign.param_queue.append(("write", dict(change[start : start + READ_CHUNK])))
        sign.param_queue.append(("save", None))
        self._queue_read(sign, READ_PARAMS)
        logger.info("visionect %s: setting sign %s %s", device.id, sign.uuid, shown)
        self._note(sign, "out", f"write {shown}, save to flash, read back")
        return await self._pump_params(sign)

    def _await_params(self, sign: _Sign) -> None:
        sign.params_wait_until = time.monotonic() + PARAM_WAIT_S
        self._schedule(sign.uuid, delay=PARAM_WAIT_S + 0.05)

    def _params_received(self, sign: _Sign, packet: Any) -> None:
        """The sign's answer to a settings packet, which ends the wait for it.
        A write it refused entirely isn't saved to flash."""
        items = list(getattr(packet, "items", ()))
        if items:
            if sign.param_out == "write" and all(
                int(i.control) == PARAM_WRITE_ERROR for i in items
            ):
                sign.param_queue = deque(q for q in sign.param_queue if q[0] != "save")
            sign.param_out = ""
            sign.params_wait_until = 0.0
        for item in items:
            pid, control = int(item.id), int(item.control)
            if control == PARAM_WRITE_ERROR:
                sign.param_error = (
                    f"The frame refused the {PARAM_NAMES.get(pid, f'setting {pid}')} "
                    "Tesserae wrote; it keeps its own."
                )
                logger.warning("visionect: sign %s refused a write to TCLV %d", sign.uuid, pid)
                continue
            if pid in NETWORK_INFO and pid not in READ_PARAMS:
                if control == PARAM_READ and item.value:
                    sign.network[pid] = network_info_value(pid, item.value)
                continue
            if control == PARAM_READ_ERROR and pid in READ_PARAMS:
                sign.params_unsupported.add(pid)  # this firmware or frame doesn't have it
                sign.known_params.pop(pid, None)
                sign.params.pop(pid, None)
                continue
            if control != PARAM_READ or pid not in READ_PARAMS or not item.value:
                continue  # a write's echo, an error, or nothing to read
            sign.params_unsupported.discard(pid)
            sign.settings_read_at = time.time()
            if pid in TEXT_PARAMS:
                sign.params[pid] = _text_value(item.value)
            else:
                sign.params[pid] = int.from_bytes(item.value, "little")
                if pid == PARAM_HEARTBEAT:
                    sign.known_interval_min = sign.params[pid]
                elif pid == PARAM_SLEEP_DISABLE:
                    # 0 and 2 both sleep; only 1 keeps the CPU awake.
                    sign.known_sleep_disabled = sign.params[pid] == 1
            sign.known_params[pid] = sign.params[pid]
            # It holds what an unanswered write asked for: that write did take.
            if sign.unanswered.get(pid, (None,))[0] == sign.params[pid]:
                del sign.unanswered[pid]

    def request_action(self, device_id: str, action: str) -> str | None:
        """Ask the device's sign, from a request thread, to report its status
        now ("status"), reboot ("reboot") or repaint the frame it shows from
        scratch ("refresh"). Returns why it can't, or None once queued."""
        if action not in ACTIONS:
            return "Unknown action."
        if not self.running:
            return "Tesserae isn't listening for Visionect frames right now."
        uuid = self.device_uuid(device_id)
        if not uuid:
            return "No frame is bound to this device yet."
        view = self.sign_view(uuid) or {}
        if not view.get("connected"):
            return "The frame isn't connected right now (asleep, or between connections); try again when it is."
        self._call_soon(functools.partial(self._do_action, uuid, action))
        return None

    def _do_action(self, uuid: str, action: str) -> None:
        sign = self._signs.get(uuid)
        if sign is None or not self._alive(sign):
            return
        if action == "refresh":
            sign.force_refresh = True
            self._schedule(uuid)
        elif action == "network":
            self._queue_read(sign, tuple(NETWORK_INFO))
            self._note(sign, "out", "read network details")
            self._schedule(uuid)
        else:
            try:
                if action == "reboot":
                    sign.conn.reboot()
                else:
                    sign.conn.request_status()
            except Exception:
                logger.warning("visionect: %s for sign %s failed", action, uuid, exc_info=True)
                return
            self._spawn(self._write(sign.conn, sign.writer))
            self._note(sign, "out", "reboot" if action == "reboot" else "status request")
            logger.info("visionect: %s sent to sign %s", action, uuid)
        sign.action = {"kind": action, "at": time.time()}
        self._publish(sign)

    def _sleep_minutes(self, device: Device) -> int | None:
        """Minutes until this device's quiet window ends, when it is inside a
        window that asked for sleep-through; else None. Recomputed every time,
        so a sleep refused at night is never replayed in the morning."""
        ctx = self._ctx
        if ctx is None:
            return None
        from app.quiet_hours import quiet_ends_at, resolve_quiet_hours
        from app.tz_resolve import app_timezone

        with ctx.app.app_context():
            window = resolve_quiet_hours(ctx.settings.get_section("app") or {}, device)
            if window is None or not window.sleep_through:
                return None
            now = datetime.now(UTC)
            ends = quiet_ends_at(window, now, app_timezone())
        if ends is None or ends <= now:
            return None
        minutes = math.ceil((ends - now).total_seconds() / 60) + SLEEP_MARGIN_MINUTES
        return max(1, min(minutes, MAX_SLEEP_MINUTES))

    async def _on_events(self, conn: Any, events: list[Any]) -> None:
        """pyvisionect callback, on the loop. Updates state synchronously and
        schedules work as tasks: awaiting here would stall the sign's read
        loop, so its ACKs couldn't arrive. The library doesn't catch
        exceptions from here, so the whole body is guarded."""
        try:
            touched: set[str] = set()
            for event in events:
                uuid = self._handle_event(conn, event)
                if uuid:
                    touched.add(uuid)
            for uuid in touched:
                if self._device_for_uuid(uuid) is not None:
                    self._schedule(uuid)
        except Exception:
            logger.exception("visionect: error handling sign events")

    def _handle_event(self, conn: Any, event: Any) -> str:
        """Apply one event; return the UUID to re-run, or ""."""
        from pyvisionect.session import (
            Acked,
            DeviceConnected,
            Nacked,
            ParamsReceived,
            StatusReceived,
        )

        uuid = normalise_uuid(event.uuid or (conn.device_id.hex() if conn.device_id else ""))
        if not uuid:
            return ""
        if isinstance(event, DeviceConnected):
            return self._connected(conn, uuid, event.status)
        sign = self._signs.get(uuid)
        if sign is None or sign.conn is not conn:
            return ""  # a superseded or refused connection: not the sign's
        now = time.monotonic()
        if isinstance(event, StatusReceived):
            self._note(sign, "in", status_line(event.status))
            sign.heard_at = now
            sign.probe_at = 0.0
            sign.status = event.status
            sign.last_seen = time.time()
            self._check_glass(sign, event.status)
            device = self._device_for_uuid(uuid)
            if device is not None:
                self._queue_status(sign, device, event.status)
            self._publish(sign)
            return uuid
        if isinstance(event, ParamsReceived):
            self._note(sign, "in", params_line(event.params))
            sign.heard_at = now
            self._params_received(sign, event.params)
            self._publish(sign)
            return uuid
        inflight = sign.inflight
        answered = (
            inflight is not None
            and inflight.conn is conn
            and inflight.packet_id == getattr(event, "packet_id", None)
        )
        if isinstance(event, Acked):
            if answered and inflight is not None:
                self._note(sign, "in", f"ack: {'picture' if inflight.kind == 'image' else 'sleep'}")
            sign.heard_at = now
            if answered and inflight is not None:
                sign.inflight = None
                self._note_trusted(sign)
                if inflight.kind == "image":
                    sign.shown_digest = inflight.digest
                    sign.shown_checksum = inflight.checksum
                    sign.shown_at = time.time()
                    device = self._device_for_uuid(uuid)
                    if device is not None:
                        self._served(sign, device.id, inflight.render)
                    if inflight.digest != sign.corrected_digest:
                        # A new desired frame gets its own corrective resend;
                        # the ACK of that resend itself doesn't re-arm it.
                        sign.crc_corrected = False
                        sign.corrected_digest = ""
                    sign.retry_at = 0.0
                    sign.last_error = ""
                    if inflight.resend_stamp:
                        sign.resend_stamp = inflight.resend_stamp
                        self._consume_resend(uuid)
                    sign.pictures.append(time.time())
                    logger.info("visionect: sign %s acked frame %s", uuid, inflight.digest[:12])
                else:
                    sign.asleep_until = time.time() + inflight.minutes * 60
                    self._spawn(self._note_sleep(sign, inflight.minutes))
            return uuid
        if isinstance(event, Nacked):
            if answered and inflight is not None:
                what = "picture" if inflight.kind == "image" else "sleep"
                self._note(
                    sign, "in", f"refused the {what}{' (charging)' if event.charging else ''}"
                )
            sign.heard_at = now
            if answered and inflight is not None:
                sign.inflight = None
                delay = NACK_CHARGING_RETRY_S if event.charging else NACK_RETRY_S
                if inflight.kind == "image":
                    sign.retry_at = now + delay
                    sign.last_error = (
                        "The frame refused a picture while charging; Tesserae retries in 5 minutes."
                        if event.charging
                        else "The frame refused a picture; Tesserae retries in a minute."
                    )
                    level = logging.DEBUG if event.charging else logging.WARNING
                    logger.log(level, "visionect: sign %s refused a frame; retrying", uuid)
                else:
                    # Back off like a refused frame; the duration is recomputed then.
                    sign.sleep_retry_at = now + delay
            return uuid
        return ""

    def _connected(self, conn: Any, uuid: str, status: Any) -> str:
        """A connection identified as ``uuid``. Usually the sign re-dialing,
        which supersedes its old connection; but a second connection from
        another address while a bound sign's own connection is alive is
        refused: the UUID is the protocol's only identity."""
        from app.visionect_listener import abort, peer_ip

        writer = self._writer_for(uuid, conn)
        if writer is None:
            # A newer connection for the UUID registered before this event
            # was dispatched; that one's DeviceConnected follows. This one
            # can't be written to, and the listener's identify deadline
            # drops it.
            return ""
        remote = peer_ip(writer)
        sign = self._signs.get(uuid)
        device = self._device_for_uuid(uuid)
        if (
            sign is not None
            and sign.conn is not None
            and sign.conn is not conn
            and device is not None
            and self._alive(sign)
            and sign.remote
            and remote != sign.remote
        ):
            if not sign.trusted_ip or remote != sign.trusted_ip:
                self._refuse(sign, writer, remote)
                return ""
            # The sign's own address takes the UUID back from whoever
            # claimed it from elsewhere (while it slept, say).
            logger.warning(
                "visionect: sign %s re-dialed from %s, the address that last acknowledged its "
                "frames; dropping the connection from %s that held its UUID",
                uuid,
                remote,
                sign.remote,
            )
            sign.refused = {"remote": sign.remote, "at": time.time()}
        if sign is None:
            device = device or self._bind(uuid)
            if device is None and self._unknown_signs() >= MAX_UNKNOWN_SIGNS:
                abort(writer)
                logger.debug("visionect: dropped unknown sign %s: too many unknown signs", uuid)
                return ""
            sign = self._sign(uuid)
        old = sign.writer if sign.conn is not conn else None
        sign.conn, sign.writer, sign.remote = conn, writer, remote
        if old is not None and old is not writer:
            abort(old)  # the old socket is dead or about to be; free it
        mark = getattr(self._server, "mark_identified", None)
        if writer is not None and callable(mark):
            mark(writer)
        sign.status = status
        sign.heard_at = time.monotonic()
        sign.probe_at = 0.0
        sign.failed_digest = ""  # worth another try, maybe at another geometry
        # Whatever was in flight on the old connection is lost.
        sign.inflight = None
        sign.asleep_until = 0.0
        sign.connected_at = time.time()
        sign.connect_reason = str(getattr(status, "connect_reason_name", "") or "")
        sign.connections.append((time.time(), sign.connect_reason))
        self._note(
            sign, "in", f"connected from {remote or '?'} ({sign.connect_reason or 'no reason'})"
        )
        self._flash_reconnected(sign, status)
        # What was read earlier still stands unless the frame rebooted or it's
        # been a while (a USB console may have changed it). A new connection may
        # write again, and anything queued on the old one is gone.
        fresh = (
            bool(sign.known_params)
            and sign.connect_reason != "reboot"
            and time.time() - sign.settings_read_at < SETTINGS_REREAD_S
        )
        sign.params = dict(sign.known_params) if fresh else {}
        sign.params_asked, sign.params_wait_until = fresh, 0.0
        sign.param_queue.clear()
        sign.param_out = ""
        sign.params_written, sign.param_error = (), ""
        device = device or self._bind(uuid)
        self._check_glass(sign, status)
        if device is None:
            self._announce_unknown(uuid, status)
            if uuid not in self._warned_unbound:
                self._warned_unbound[uuid] = None
                while len(self._warned_unbound) > MAX_WARNED_UNBOUND:
                    del self._warned_unbound[next(iter(self._warned_unbound))]
                logger.warning(
                    "visionect: sign %s connected but no Visionect device is free; "
                    "it is listed under Settings -> Devices to register",
                    uuid,
                )
            self._publish(sign)
            return ""
        return uuid

    def _writer_for(self, uuid: str, conn: Any) -> Any:
        """The StreamWriter of ``conn``. The library registers it just
        before dispatching DeviceConnected, so it is still the UUID's entry;
        None if a newer connection has already replaced it."""
        server = self._server
        key = uuid_bytes(uuid)
        if server is None or server.connection_for(key) is not conn:
            return None
        writers = getattr(server, "_writers", None)
        return writers.get(key) if isinstance(writers, dict) else None

    def _refuse(self, sign: _Sign, writer: Any, remote: str) -> None:
        from app.visionect_listener import abort

        abort(writer)
        sign.refused = {"remote": remote, "at": time.time()}
        logger.warning(
            "visionect: refused a connection from %s claiming sign %s, which is connected from %s",
            remote or "?",
            sign.uuid,
            sign.remote,
        )
        self._probe(sign)
        self._publish(sign)

    def _probe(self, sign: _Sign) -> None:
        """Ask a quiet connection for a status, to learn whether it lives."""
        now = time.monotonic()
        if sign.probe_at or now - sign.heard_at < PROBE_QUIET_S or not self._alive(sign):
            return
        try:
            sign.conn.request_status()
            sign.writer.write(sign.conn.drain())
            self._note(sign, "out", "status request (is it still there?)")
        except Exception:
            logger.debug("visionect: status request to %s failed", sign.uuid, exc_info=True)
            return
        sign.probe_at = now
        self._schedule(sign.uuid, delay=PROBE_TIMEOUT_S + 0.05)

    def _check_glass(self, sign: _Sign, status: Any) -> None:
        """Re-send when the sign reports showing something other than the
        frame it acked: its framebuffer changed while we weren't looking.
        Only once its CRC has had time to converge (firmware 6.17 reports it
        a status late), and only one corrective resend per mismatch, so a
        sign whose CRC never matches can't cause a redraw loop."""
        reported = getattr(status, "display_state_crc", None)
        sending = sign.inflight is not None and sign.inflight.kind == "image"
        if reported is None or sign.shown_checksum is None or sending:
            return
        if reported == sign.shown_checksum:
            return  # (a match doesn't re-arm the correction: only a new frame's ACK does)
        if time.time() - sign.shown_at < CRC_CONVERGE_S or sign.crc_corrected:
            return
        sign.crc_corrected = True
        sign.corrected_digest = sign.shown_digest
        logger.info(
            "visionect: sign %s reports state %s, not the acked %s; re-sending",
            sign.uuid,
            reported,
            sign.shown_checksum,
        )
        sign.shown_digest = ""

    def _served(self, sign: _Sign, device_id: str, render: dict[str, Any]) -> None:
        """Tell the push manager the sign holds ``render`` (it ACKed the
        picture, or its CRC says it already shows it), so the Lineups and
        device pages show what is on the glass and call a newer render queued
        until the sign takes it. Persisted by the manager next to the latest
        render, so a restart before the next ACK still knows. Latest wins, one
        executor job per sign at a time, in ACK order."""
        if not render.get("digest"):
            return
        sign.pending_served = (device_id, dict(render))
        if sign.served_task is None or sign.served_task.done():
            task = asyncio.get_running_loop().create_task(self._drain_served(sign))
            sign.served_task = task
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _drain_served(self, sign: _Sign) -> None:
        loop = asyncio.get_running_loop()
        while sign.pending_served is not None:
            ctx = self._ctx
            device_id, render = sign.pending_served
            sign.pending_served = None
            if ctx is None:
                return
            try:
                await loop.run_in_executor(None, ctx.record_served_fn, device_id, render)
            except Exception:
                logger.exception("visionect: recording %s's shown frame failed", device_id)

    def _consume_resend(self, uuid: str) -> None:
        """Clear the latest render's one-shot resend flag once the sign has
        repainted for it, so a restart doesn't honour it again."""
        ctx = self._ctx
        device = self._device_for_uuid(uuid)
        push_mgr = ctx.app.config.get("PUSH_MANAGER") if ctx is not None else None
        consume = getattr(push_mgr, "consume_force_refetch", None)
        if device is None or not callable(consume):
            return

        async def _consume() -> None:
            try:
                await asyncio.get_running_loop().run_in_executor(None, consume, device.id)
            except Exception:
                logger.exception("visionect: clearing the resend flag for %s failed", device.id)

        self._spawn(_consume())

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _queue_status(self, sign: _Sign, device: Device, status: Any) -> None:
        """Record a status as heartbeat telemetry, one at a time per sign and
        latest wins, so a flood of status packets queues at most one
        executor job per sign rather than one per packet."""
        sign.pending_status = (device, status)
        if sign.status_task is None or sign.status_task.done():
            task = asyncio.get_running_loop().create_task(self._drain_statuses(sign))
            sign.status_task = task
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _drain_statuses(self, sign: _Sign) -> None:
        while sign.pending_status is not None:
            device, status = sign.pending_status
            sign.pending_status = None
            await self._record_status(device, sign, status)

    async def _record_status(self, device: Device, sign: _Sign, status: Any) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        sign.status_times.append(time.time())
        heartbeat = status_heartbeat(
            status.fields(),
            uuid=sign.uuid,
            connect_reason=str(status.connect_reason_name),
            interval_min=sign.known_interval_min,
            stays_awake=sign.known_sleep_disabled is True,
            sleep_until=sign.asleep_until if sign.asleep_until > time.time() else None,
            observed_gap_s=observed_status_gap_s(sign.status_times),
        )
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _record_heartbeat, ctx.app, device, heartbeat)
        except Exception:
            logger.exception("visionect: recording status for %s failed", device.id)

    async def _note_sleep(self, sign: _Sign, minutes: int) -> None:
        """The sign acked a quiet-hours sleep: its next wake is the end of
        that sleep, not a check-in interval from now. The status that opened
        the connection recorded the interval estimate; let it land first so
        this one is not overwritten by it."""
        ctx = self._ctx
        device = self._device_for_uuid(sign.uuid)
        if ctx is None or device is None:
            return
        task = sign.status_task
        if task is not None and not task.done():
            await asyncio.wait({task})
        wake_at = sign.asleep_until
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, _note_sleep_wake, ctx.app, device.id, wake_at, minutes * 60
            )
        except Exception:
            logger.exception("visionect: recording %s's sleep failed", device.id)

    # -- UI snapshot (read from request threads) ---------------------------

    def _publish(self, sign: _Sign) -> None:
        inflight = sign.inflight
        health = connection_health(sign.connections, sign.pictures, time.time())
        if health["stuck"] and not sign.stuck_warned:
            logger.warning(
                "visionect: sign %s looks stuck: %d connections in %d min and no picture taken",
                sign.uuid,
                health["stuck"],
                STUCK_WINDOW_S // 60,
            )
            self._note(sign, "note", "looks stuck: reconnecting and taking no pictures")
        sign.stuck_warned = bool(health["stuck"])
        view = {
            "uuid": sign.uuid,
            "connected": self._alive(sign),
            "remote": sign.remote,
            "connected_at": sign.connected_at or None,
            "last_seen": sign.last_seen or None,
            "in_sync": bool(sign.frame_digest) and sign.frame_digest == sign.shown_digest,
            "sending": inflight is not None and inflight.kind == "image",
            "asleep_until": sign.asleep_until or None,
            "last_error": sign.last_error,
            "refused": dict(sign.refused) if sign.refused else None,
            "connect_reason": sign.connect_reason,
            "status": status_summary(sign.status),
            "heartbeat_min": sign.params.get(PARAM_HEARTBEAT, sign.known_interval_min),
            "checkin_gap_s": observed_status_gap_s(sign.status_times)
            or (sign.known_interval_min * 60 if sign.known_interval_min else None),
            "sleep_disabled": sign.params.get(PARAM_SLEEP_DISABLE),
            "settings": sign_setting_labels(sign.known_params),
            "health": health,
            "network": {NETWORK_INFO[pid]: v for pid, v in sign.network.items()},
            "wifi": sign.params.get(PARAM_WIFI_SSID),
            "param_error": sign.param_error,
            "action": dict(sign.action) if sign.action else None,
            "exchanges": list(sign.exchanges),
            "identity": firmware_identity(sign.status),
        }
        with self._snapshot_lock:
            self._snapshot[sign.uuid] = view

    def sign_view(self, uuid: str) -> dict[str, Any] | None:
        with self._snapshot_lock:
            view = self._snapshot.get(normalise_uuid(uuid))
            return dict(view) if view is not None else None

    # -- firmware flashing -------------------------------------------------
    #
    # Only ever on an explicit request (request_firmware_update). The frame is
    # told to reboot; its bootloader then dials this listener on a new
    # connection and opens with a hello (app.visionect_bootloader). The
    # listener hands that connection here only while a flash is pending for
    # the address, and only a hello from the frame the flash is for, on
    # matching hardware, is answered. Everything else is as it always was.

    def _repo(self) -> FirmwareRepository | None:
        if self.firmware_repo is None:
            ctx = self._ctx
            root = ctx.app.config.get("DATA_ROOT") if ctx is not None else None
            if root:
                self.firmware_repo = FirmwareRepository(Path(root) / "visionect_firmware")
        return self.firmware_repo

    @staticmethod
    def _flash_expired(job: visionect_flash.FlashJob) -> bool:
        """A pending job past its timeout, whether or not its timer ran (a
        listener restart or a dropped callback leaves none). Never claimed,
        and it holds nothing up."""
        return (
            job.phase == visionect_flash.PENDING
            and time.time() - (job.reboot_at or job.requested_at)
            > visionect_flash.PENDING_TIMEOUT_S + visionect_flash.PENDING_GRACE_S
        )

    def _flash_active(self, uuid: str) -> bool:
        job = self._flash_jobs.get(uuid)
        return job is not None and job.active and not self._flash_expired(job)

    def _flash_any_active(self) -> bool:
        return any(
            job.active and not self._flash_expired(job) for job in list(self._flash_jobs.values())
        )

    def firmware_update_running(self) -> bool:
        """Whether a frame is being flashed, or about to be. Stopping the listener
        now would cut the flash off."""
        return self._flash_any_active()

    def _flash_stop_all(self) -> None:
        """The listener is stopping: no active job survives it, since its timer
        and the frame's connection go too. A running flash is ended by its own
        task being cancelled."""
        for job in list(self._flash_jobs.values()):
            if job.timer is not None:
                job.timer.cancel()
                job.timer = None
            if job.phase in (visionect_flash.PENDING, visionect_flash.VERIFYING):
                job.message = (
                    "Tesserae stopped before the frame called its bootloader, so nothing was "
                    "flashed."
                    if job.phase == visionect_flash.PENDING
                    else "Tesserae stopped before the frame came back from its flash. Check that "
                    "it reconnects on the new firmware."
                )
                job.phase = visionect_flash.FAILED
                job.finished_at = time.time()
                job.touch()

    def flash_job(self, device_id: str) -> visionect_flash.FlashJob | None:
        uuid = self.device_uuid(device_id)
        return self._flash_jobs.get(uuid) if uuid else None

    def firmware_token(self, device_id: str) -> str:
        """Changes whenever what the page shows about firmware does (a flash
        step, a new list); empty when there is nothing to show. The device
        page polls it with its other live regions."""
        uuid = self.device_uuid(device_id)
        if not uuid:
            return ""
        job = self._flash_jobs.get(uuid)
        repo = self.firmware_repo
        parts = []
        if job is not None:
            parts.append(f"{job.phase}{job.version}")
        if repo is not None and repo.generation:
            parts.append(f"g{repo.generation}")
        return ".".join(parts)

    def _flash_blocked(self, view: dict[str, Any] | None) -> str:
        """Why a flash can't start now (plain words), or ""."""
        if not self.running:
            return "Tesserae isn't listening for Visionect frames right now."
        if view is None or not view.get("connected"):
            return "The frame isn't connected right now (asleep, or between connections)."
        if self._flash_any_active():
            return "A firmware update is already running."
        battery = (view.get("status") or {}).get("battery_pct")
        if not isinstance(battery, int):
            return "The frame hasn't reported its battery level, so it isn't safe to update."
        if battery < FLASH_MIN_BATTERY_PCT:
            return (
                f"The frame's battery is at {battery}%. Charge it above "
                f"{FLASH_MIN_BATTERY_PCT}% before updating its firmware."
            )
        return ""

    def _firmware_ignored(self) -> set[int]:
        """Checksums of the builds someone chose to ignore. An ignored build is
        no longer offered as an update, and can still be picked by hand."""
        ctx = self._ctx
        settings = ctx.app.config.get("SETTINGS_STORE") if ctx is not None else None
        raw = settings.get_section("app").get(FIRMWARE_IGNORED_KEY) if settings is not None else ()
        out: set[int] = set()
        for text in raw if isinstance(raw, list) else ():
            with contextlib.suppress(TypeError, ValueError):
                out.add(int(str(text), 16))
        return out

    def _compatible_builds(self, view: dict[str, Any] | None) -> list[Build] | None:
        """Builds for exactly this frame's hardware and display, newest first,
        from the cached list; None when the list or what the frame reports
        doesn't allow telling."""
        repo = self._repo()
        ident = (view or {}).get("identity") or {}
        if repo is None:
            return None
        builds = repo.builds()
        hardware, name_id, display = (
            ident.get("hardware"),
            ident.get("hw_name_id"),
            ident.get("display_id"),
        )
        if builds is None or hardware is None or name_id is None or display is None:
            return None
        return compatible(builds, hardware=hardware, hardware_name_id=name_id, display_id=display)

    def firmware_view(self, device_id: str) -> dict[str, Any] | None:
        """What the device page shows about the sign's firmware, from the
        cache and the last status only (never the network: a stale list
        triggers a background refresh). None for a device with no sign."""
        uuid = self.device_uuid(device_id)
        if not uuid:
            return None
        view = self.sign_view(uuid)
        job = self._flash_jobs.get(uuid)
        repo = self._repo()
        ctx = self._ctx
        settings = ctx.app.config.get("SETTINGS_STORE") if ctx is not None else None
        # Looking for builds is optional traffic, so the app's online switch
        # covers it. Downloading a chosen build is always something asked for.
        if repo is not None and online.online_enabled(settings):
            repo.refresh_in_background()
            repo.refresh_notes_in_background()
        ident = (view or {}).get("identity") or {}
        installed = ident.get("firmware")
        checksum = ident.get("checksum")
        builds = self._compatible_builds(view)
        ignored = self._firmware_ignored()
        latest = next((b for b in builds or [] if b.checksum not in ignored), None)
        rows: list[dict[str, Any]] = []
        for b in builds or []:
            is_installed = (
                b.checksum == checksum if checksum is not None else b.version == installed
            )
            rows.append(
                {
                    "checksum": b.checksum_hex,
                    "version": b.version_text,
                    "installed": is_installed,
                    "newest": b is latest,
                    "ignored": b.checksum in ignored,
                    "downgrade": installed is not None and b.version < installed,
                    "notes": repo.notes_for(b.version_text) if repo is not None else [],
                }
            )
        update = bool(
            latest is not None
            and installed is not None
            and latest.version > installed
            and (checksum is None or latest.checksum != checksum)
        )
        return {
            "installed": ".".join(map(str, installed)) if installed else "",
            "latest": latest.version_text if latest is not None else "",
            "latest_checksum": latest.checksum_hex if latest is not None else "",
            "update_available": update,
            "builds": rows,
            "blocked": self._flash_blocked(view),
            "flash": job.view() if job is not None else None,
            "list_known": builds is not None,
            "unknown_because": self._firmware_unknown_because(view, builds, repo),
            "min_battery": FLASH_MIN_BATTERY_PCT,
        }

    @staticmethod
    def _firmware_unknown_because(
        view: dict[str, Any] | None, builds: list[Build] | None, repo: Any
    ) -> str:
        """Why the page has no latest version to show, in plain words."""
        if builds:
            return ""
        if builds is not None:
            return "Visionect lists no firmware for this frame's hardware and display"
        if repo is not None and repo.builds() is None:
            return "Visionect's firmware list hasn't been fetched yet, or can't be reached (it needs online features on)"
        if (view or {}).get("identity"):
            return "Waiting for the frame to report its hardware and display"
        return "Waiting for the frame's next check-in"

    def request_firmware_update(self, device_id: str, checksum_hex: str) -> str | None:
        """Flash the chosen build onto the device's sign, from a request
        thread. Returns why it can't, or None once the reboot is queued.

        Order: validate, download (a file that doesn't walk cleanly is never
        used), re-check that the frame is connected, charged and not already
        being flashed, mark the flash pending, then reboot the frame."""
        uuid = self.device_uuid(device_id)
        if not uuid:
            return "No frame is bound to this device yet."
        view = self.sign_view(uuid)
        blocked = self._flash_blocked(view)
        if blocked:
            return blocked
        text = str(checksum_hex or "").strip().lower()
        try:
            wanted = int(text, 16)
        except ValueError:
            return "Unknown firmware build."
        builds = self._compatible_builds(view)
        if builds is None:
            return "The list of firmware builds for this frame isn't available."
        chosen = next((b for b in builds if b.checksum == wanted), None)
        if chosen is None:
            return "That build isn't for this frame's hardware and display."
        ident = (view or {}).get("identity") or {}
        if ident.get("checksum") == chosen.checksum or ident.get("firmware") == chosen.version:
            return "The frame already runs that firmware."
        repo = self._repo()
        assert repo is not None
        try:
            data = repo.fetch(chosen)
        except RepositoryError as exc:
            return str(exc)
        view = self.sign_view(uuid)
        job = visionect_flash.FlashJob(
            uuid=uuid,
            device_id=device_id,
            build=chosen,
            data=data,
            peer_ip=str((view or {}).get("remote") or ""),
            from_version=".".join(map(str, ident.get("firmware") or ())),
            from_checksum=ident.get("checksum"),
        )
        with self._flash_lock:
            blocked = self._flash_blocked(view)
            if blocked:
                return blocked
            self._flash_jobs[uuid] = job
        logger.info(
            "visionect: firmware %s (%s) requested for sign %s, now %s",
            chosen.version_text,
            chosen.checksum_hex,
            uuid,
            job.from_version or "unknown",
        )
        self._call_soon(functools.partial(self._flash_begin, job))
        return None

    def _flash_note(self, job: visionect_flash.FlashJob, text: str, way: str = "note") -> None:
        logger.info("visionect: firmware flash of sign %s: %s", job.uuid, text)
        sign = self._signs.get(job.uuid)
        if sign is not None:
            self._note(sign, way, f"firmware: {text}")
            self._publish(sign)

    def _flash_end(self, job: visionect_flash.FlashJob, ok: bool, message: str) -> None:
        """Record how a flash ended, and let pictures resume."""
        if job.timer is not None:
            job.timer.cancel()
            job.timer = None
        job.phase = visionect_flash.DONE if ok else visionect_flash.FAILED
        job.message = message
        job.finished_at = time.time()
        job.touch()
        if ok:
            self._flash_note(job, message)
        else:
            logger.warning("visionect: firmware flash of sign %s failed: %s", job.uuid, message)
            sign = self._signs.get(job.uuid)
            if sign is not None:
                self._note(sign, "note", f"firmware: {message}")
                self._publish(sign)
        self._schedule(job.uuid)

    def _flash_arm(self, job: visionect_flash.FlashJob, seconds: float) -> None:
        if job.timer is not None:
            job.timer.cancel()
        loop = asyncio.get_running_loop()
        job.timer = loop.call_later(seconds, self._flash_timeout, job, job.phase)

    def _flash_timeout(self, job: visionect_flash.FlashJob, phase: str) -> None:
        job.timer = None
        if job.phase != phase or not job.active:
            return
        if phase == visionect_flash.PENDING:
            self._flash_end(
                job,
                False,
                "The frame didn't contact its bootloader within "
                f"{visionect_flash.PENDING_TIMEOUT_S / 60:.0f} minutes of the reboot, so nothing "
                "was flashed. It may still be restarting; check it, then try again.",
            )
        elif phase == visionect_flash.VERIFYING:
            self._flash_end(
                job,
                False,
                "The flash finished but the frame hasn't reconnected within "
                f"{visionect_flash.VERIFY_TIMEOUT_S / 60:.0f} minutes. Power-cycle it. "
                + visionect_flash.USB_HINT,
            )

    def _flash_begin(self, job: visionect_flash.FlashJob) -> None:
        """On the loop: send the reboot and wait for the bootloader."""
        if job.phase != visionect_flash.PENDING:
            return
        sign = self._signs.get(job.uuid)
        if sign is None or not self._alive(sign) or self._flash_jobs.get(job.uuid) is not job:
            self._flash_end(job, False, "The frame disconnected before it could be rebooted.")
            return
        # Never on top of an unanswered picture or settings packet: that is what
        # made this frame drop connections before. Nothing new goes out now, so
        # one being answered is a matter of seconds; past the wait, it never will be.
        waited = time.time() - job.requested_at
        if (sign.inflight is not None or sign.param_out) and waited < FLASH_REBOOT_WAIT_S:
            asyncio.get_running_loop().call_later(1.0, self._flash_begin, job)
            return
        self._flash_send_reboot(job, sign)

    def _flash_send_reboot(self, job: visionect_flash.FlashJob, sign: _Sign) -> None:
        """Send the reboot on the frame's current connection and wait for its
        bootloader."""
        try:
            sign.conn.reboot()
        except Exception as exc:
            logger.warning("visionect: reboot for a firmware flash failed", exc_info=True)
            self._flash_end(job, False, f"Couldn't send the reboot command: {exc}")
            return
        job.peer_ip = job.peer_ip or sign.remote
        job.reboots += 1
        job.reboot_at = time.time()
        self._spawn(self._write(sign.conn, sign.writer))
        sign.inflight = None
        self._flash_arm(job, visionect_flash.PENDING_TIMEOUT_S)
        self._flash_note(
            job, f"reboot sent; waiting for the bootloader to offer {job.build.version_text}", "out"
        )

    def _flash_wants(self, ip: str) -> bool:
        """Listener hook: a flash is waiting for a bootloader from ``ip``."""
        return any(
            job.phase == visionect_flash.PENDING
            and not self._flash_expired(job)
            and (not job.peer_ip or job.peer_ip == ip)
            for job in list(self._flash_jobs.values())
        )

    async def _flash_connection(
        self, reader: Any, writer: Any, head: bytes, on_claim: Callable[[], None]
    ) -> bytes | None:
        """Listener hook: a connection opened like a bootloader hello. Take it
        over when it is the pending frame's; otherwise give the bytes back."""
        from app.visionect_listener import abort, peer_ip

        ip = peer_ip(writer)
        data = await visionect_flash.read_hello(reader, head, timeout=HELLO_READ_S)
        try:
            hello = parse_hello(data)
        except BootloaderError:
            logger.info("visionect: %s opened like a bootloader but sent no valid hello", ip or "?")
            return data
        uuid = normalise_uuid(hello.device_id.hex())
        job = self._flash_jobs.get(uuid)
        if (
            job is None
            or job.phase != visionect_flash.PENDING
            or self._flash_expired(job)
            or (job.peer_ip and job.peer_ip != ip)
        ):
            logger.info(
                "visionect: bootloader hello from %s (%s), no flash pending for it; "
                "handled as before",
                ip or "?",
                uuid,
            )
            return data
        on_claim()
        job.phase = visionect_flash.RUNNING
        job.touch()
        if job.timer is not None:
            job.timer.cancel()
            job.timer = None
        loop = asyncio.get_running_loop()
        try:
            await visionect_flash.run_session(
                reader,
                writer,
                data,
                job,
                on_change=lambda text: self._flash_note(job, text, "in"),
                abort=abort,
                clock=loop.time,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never leave a job stuck running
            logger.exception("visionect: the firmware flash crashed")
            abort(writer)
            job.wrote_records = job.wrote_records or job.blocks_done > 0
            self._flash_end(
                job, False, visionect_flash.failure_message(str(exc), job.wrote_records)
            )
            return None
        if job.phase == visionect_flash.VERIFYING:
            self._flash_arm(job, visionect_flash.VERIFY_TIMEOUT_S)
            self._flash_note(
                job, "all blocks written; waiting for the frame to restart on the new firmware"
            )
        else:
            self._flash_end(job, False, job.message)
        return None

    def _flash_reconnected(self, sign: _Sign, status: Any) -> None:
        """A sign connected normally: it is either the flashed frame back on
        its new firmware, or one that rebooted without calling the bootloader."""
        job = self._flash_jobs.get(sign.uuid)
        if job is None or not job.active:
            return
        reason = sign.connect_reason
        try:
            if job.phase == visionect_flash.VERIFYING:
                reported = firmware_identity(status).get("firmware")
                shown = ".".join(map(str, reported)) if reported else "no version"
                if reported == job.build.version:
                    self._flash_end(
                        job,
                        True,
                        f"The frame restarted ({reason or 'no reason'}) on firmware {shown}, "
                        "as requested.",
                    )
                else:
                    self._flash_end(
                        job,
                        False,
                        f"The frame came back reporting firmware {shown}, not the "
                        f"{job.build.version_text} that was flashed.",
                    )
            elif job.phase == visionect_flash.PENDING and reason == "reboot":
                self._flash_end(
                    job,
                    False,
                    "The frame restarted without calling its bootloader, so nothing was flashed.",
                )
            elif (
                job.phase == visionect_flash.PENDING
                and job.reboots
                and job.reboots < visionect_flash.REBOOT_TRIES
            ):
                # It came back without restarting, so it never got the reboot.
                self._flash_send_reboot(job, sign)
        except Exception:
            logger.exception("visionect: checking a flashed frame failed")


def is_visionect_device(device: Any) -> bool:
    """Whether an instance or kind speaks the Visionect protocol, without a
    registry lookup: catalog SKUs share devices/visionect/device.py, which
    carries the PROTOCOL marker."""
    if getattr(device, "kind_of", None) == KIND or getattr(device, "id", None) == KIND:
        return True
    return getattr(getattr(device, "module", None), "PROTOCOL", None) == KIND


def visionect_devices(devices: DeviceRegistry) -> list[Device]:
    """Every device instance driven over the Visionect protocol."""
    return [d for d in list(devices.devices.values()) if d.kind_of and is_visionect_device(d)]


def device_bound_to(
    devices: DeviceRegistry, settings: Any, uuid: str, *, exclude: str = ""
) -> str | None:
    """The id of the Visionect device (other than ``exclude``) whose stored
    UUID is ``uuid``, or None. One sign drives one device."""
    want = normalise_uuid(uuid)
    if not want:
        return None
    section = settings.get_section("devices") or {}
    for device in sorted(visionect_devices(devices), key=lambda d: d.id):
        if device.id == exclude:
            continue
        cfg = section.get(device.id) if isinstance(section, dict) else None
        if isinstance(cfg, dict) and normalise_uuid(cfg.get("uuid")) == want:
            return device.id
    return None


# Every write of a Visionect device's UUID checks that no other device has the
# sign and stores it under this lock (``device_service.save_device_config`` and
# the kind hooks registered at the bottom, and ``bind_sign`` for the
# listener's auto-bind), so two saves (or a save and the auto-bind) can't bind
# one sign to two devices.
_BINDING_LOCK = device_service.CONFIG_SAVE_LOCK


def binding_conflict(
    devices: DeviceRegistry,
    settings: Any,
    device: Any,
    values: dict[str, Any],
    *,
    exclude: str = "",
) -> str | None:
    """Why ``values`` can't be stored for ``device`` (a device or a kind):
    another device is already bound to the sign UUID in them. None when they
    can, or when ``device`` isn't a Visionect sign or no UUID is given."""
    if not is_visionect_device(device):
        return None
    uuid = normalise_uuid(values.get("uuid"))
    if not uuid:
        return None
    other = device_bound_to(devices, settings, uuid, exclude=exclude)
    if other is None:
        return None
    return f"Sign {uuid} is already bound to device {other!r}."


def _stored_config(settings: Any, device_id: str) -> dict[str, Any]:
    section = settings.get_section("devices") or {}
    cfg = section.get(device_id) if isinstance(section, dict) else None
    return dict(cfg) if isinstance(cfg, dict) else {}


def arm_auto_bind(settings: Any, device_id: str) -> None:
    """Open a blank device's auto-bind window now (it was just added, or its
    UUID cleared)."""
    settings.update_for_namespace(
        "devices", device_id, {AUTO_BIND_KEY: time.time()}, [{"name": AUTO_BIND_KEY}]
    )


def bind_sign(settings: Any, devices: DeviceRegistry, device: Device, uuid: str) -> bool:
    """Store ``uuid`` on ``device`` if it is still blank and no other device
    has that sign (the listener's auto-bind). Only these keys change, merged
    under the settings store's lock; the window closes."""
    with _BINDING_LOCK:
        if normalise_uuid(_stored_config(settings, device.id).get("uuid")):
            return False
        if device_bound_to(devices, settings, uuid, exclude=device.id) is not None:
            return False
        settings.update_for_namespace(
            "devices",
            device.id,
            {"uuid": uuid, AUTO_BIND_KEY: None},
            [{"name": "uuid"}, {"name": AUTO_BIND_KEY}],
        )
    return True


def canvas_size(panel: Any) -> tuple[int, int] | None:
    """The sign's full canvas (w, h) from its pyvisionect panel geometry."""
    from pyvisionect.imaging.panel import Panel as ImagingPanel

    try:
        imaging_panel = ImagingPanel.adapt(panel)
        return int(imaging_panel.canvas_width), int(imaging_panel.canvas_height)
    except (TypeError, ValueError):
        return None


def native_size(device: Device) -> tuple[int, int] | None:
    block = device.panel or {}
    try:
        return int(block["native_w"]), int(block["native_h"])
    except (KeyError, TypeError, ValueError):
        return None


def kind_default_dims(devices: DeviceRegistry, device: Device) -> tuple[int, int] | None:
    kind = devices.devices.get(str(device.kind_of or ""))
    block = (kind.panel if kind is not None else None) or {}
    try:
        return int(block["w"]), int(block["h"])
    except (KeyError, TypeError, ValueError):
        return None


def conform_panel(
    device: Device,
    canvas: tuple[int, int],
    defaults: tuple[int, int] | None,
    current: Callable[[], Device | None] | None = None,
) -> bool:
    """Set the device's native buffer to the sign's canvas (executor thread).

    Same shape as the TRMNL size adoption: the instance file's panel block
    gets ``native_w / native_h``, and a composition canvas still at the
    kind's defaults (or the old buffer's size) is re-seeded to the new one,
    ordered for the stored orientation. Unlike TRMNL this isn't one-shot:
    the bound sign's DisplayType is authoritative. Returns True when it
    wrote.

    The read and the write happen under the instance-file lock every
    instance-file writer holds (a rename, a panel save, a delete), so
    neither side drops the other's change, and the file is replaced
    atomically, so readers never see half-written JSON and only the size
    keys change. ``current`` returns the registry's device object now, which
    a reload may have replaced; it is patched too. A device deleted, or
    reloaded from another file, since the caller looked it up is left alone
    rather than written back to life."""
    from app.device_service import INSTANCE_FILE_LOCK

    with INSTANCE_FILE_LOCK:
        live = current() if current is not None else device
        if live is None or live.path != device.path or not device.path.exists():
            return False
        return _write_conformed_panel(device, live, canvas, defaults)


def _write_conformed_panel(
    device: Device, live: Device, canvas: tuple[int, int], defaults: tuple[int, int] | None
) -> bool:
    width, height = canvas
    try:
        raw = json.loads(device.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    block = dict(raw.get("panel") or {})
    old_native: tuple[int, int] | None = None
    with contextlib.suppress(KeyError, TypeError, ValueError):
        old_native = (int(block["native_w"]), int(block["native_h"]))
    if old_native == canvas:
        return False
    try:
        cur = (int(block["w"]), int(block["h"]))
    except (KeyError, TypeError, ValueError):
        cur = None
    seeds = {defaults} | ({old_native, old_native[::-1]} if old_native else set())
    if cur is None or cur in seeds:
        short, long = min(width, height), max(width, height)
        portrait = str(block.get("orientation") or "").lower().startswith("portrait")
        block["w"], block["h"] = (short, long) if portrait else (long, short)
    block["native_w"], block["native_h"] = width, height
    raw["panel"] = block
    tmp = device.path.with_name(f".{device.path.name}.visionect.tmp")
    try:
        tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, device.path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        return False
    device.manifest["panel"] = dict(block)
    if live is not device:
        live.manifest["panel"] = dict(block)
    logger.info(
        "visionect: device %s now uses the sign's %dx%d buffer (canvas %sx%s)",
        device.id,
        width,
        height,
        block.get("w"),
        block.get("h"),
    )
    return True


def _encode(path: Path, panel: Any) -> Any:
    """Encode a renderer artifact for the sign (executor thread)."""
    from PIL import Image
    from pyvisionect.imaging import encode_frame
    from pyvisionect.imaging.panel import Panel as ImagingPanel

    imaging_panel = ImagingPanel.adapt(panel)
    with Image.open(path) as img:
        canvas = img.convert("L")
    want = (imaging_panel.canvas_width, imaging_panel.canvas_height)
    if canvas.size != want:
        canvas = canvas.resize(want)
    return encode_frame(
        canvas, panel=imaging_panel, encoding=ENCODING_4BPP, dithering=DITHERING_NONE
    )


def _record_heartbeat(app: Flask, device: Device, heartbeat: dict[str, Any]) -> None:
    events = app.config.get("EVENT_LOG")
    status_cache = app.config.get("DEVICE_STATUS")
    if events is None or status_cache is None:
        return
    from app.transport_wiring import record_status_heartbeat

    with app.app_context():
        record_status_heartbeat(
            app=app,
            device=device,
            payload=json.dumps(heartbeat).encode("utf-8"),
            status_cache=status_cache,
            event_log=events,
            event_target=f"visionect://{device.id}/status",
        )


def _note_sleep_wake(app: Flask, device_id: str, wake_at: float, interval_s: int) -> None:
    """Tell telemetry the server put the sign to sleep until ``wake_at``."""
    telemetry = app.config.get("DEVICE_TELEMETRY")
    if telemetry is not None:
        telemetry.note_server_directive(device_id, wake_at=wake_at, interval_s=interval_s)


# -- wiring ---------------------------------------------------------------

_SERVICE: VisionectService | None = None
_SERVICE_LOCK = threading.Lock()


def validate_port(raw: Any) -> int:
    """Validate an unprivileged listener port without truncating floats."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise ValueError("Visionect Server port must be an integer from 1024 to 65535.")
    try:
        port = int(raw)
    except ValueError:
        raise ValueError("Visionect Server port must be an integer from 1024 to 65535.") from None
    if not 1024 <= port <= 65535:
        raise ValueError("Visionect Server port must be an integer from 1024 to 65535.")
    web_raw = os.environ.get("TESSERAE_BIND_PORT", "").strip()
    web_port = int(web_raw) if web_raw.isdigit() else 8765
    if port == web_port:
        raise ValueError(
            f"Visionect Server port {port} is Tesserae's web port; choose another port."
        )
    return port


def configured_port(settings: Any) -> int:
    """Environment (including the optional HA option), app setting, then default.

    Invalid explicit values stop the listener with a visible configuration
    error, rather than silently listening on a different port.
    """
    raw: Any = os.environ.get("TESSERAE_VISIONECT_PORT")
    if not raw:
        try:
            raw = (settings.get_section("app") or {}).get("visionect_port")
        except Exception:
            raw = None
    return validate_port(raw if raw not in (None, "") else DEFAULT_PORT)


def configured_host() -> str:
    """The address the listener binds: ``TESSERAE_BIND_HOST``, the same
    variable the web server's bind address comes from (``app.main`` sets it
    from ``--host``), else all interfaces."""
    return os.environ.get("TESSERAE_BIND_HOST", "").strip() or DEFAULT_HOST


def service() -> VisionectService | None:
    return _SERVICE


def register(app: Flask) -> None:
    """Wire the listener to the (possibly rebuilt) push manager. Starts it
    when a visionect device exists, stops it when none does, and moves it
    when the configured address or port changed. No-op without a push manager
    (bare test setups)."""
    global _SERVICE
    push_mgr = app.config.get("PUSH_MANAGER")
    devices = app.config.get("DEVICE_REGISTRY")
    settings = app.config.get("SETTINGS_STORE")
    renders_dir = app.config.get("RENDERS_DIR")
    if push_mgr is None or devices is None or settings is None or renders_dir is None:
        return
    wanted = bool(visionect_devices(devices))
    host = configured_host()
    with _SERVICE_LOCK:
        svc = _SERVICE
        try:
            port = configured_port(settings)
        except ValueError as exc:
            if svc is not None:
                svc.autostart = False
                push_mgr.remove_listener(svc.on_push)
                svc.stop()
            _SERVICE = None
            app.config["VISIONECT_SERVICE"] = None
            logger.error("visionect: %s", exc)
            return
        if svc is not None and (svc.port != port or svc.host != host):
            # Retired for good: a push still reaching its listener must not
            # bring it back up on the old address.
            svc.autostart = False
            push_mgr.remove_listener(svc.on_push)
            svc.stop()
            svc = None
        if svc is None:
            if not wanted:
                _SERVICE = None
                app.config["VISIONECT_SERVICE"] = None
                return
            svc = VisionectService(host=host, port=port)
            _SERVICE = svc
        svc.autostart = AUTOSTART
        svc.attach(
            _Context(
                app=app,
                devices=devices,
                settings=settings,
                renders_dir=Path(renders_dir),
                latest_render_fn=push_mgr.latest_render_for,
                render_busy_fn=getattr(push_mgr, "render_in_progress", lambda _d: False),
                record_served_fn=getattr(
                    push_mgr, "record_frame_served", lambda _device_id, _render: None
                ),
            )
        )
        push_mgr.add_listener(svc.on_push)
        app.config["VISIONECT_SERVICE"] = svc
        if not svc.autostart:
            return
        if wanted and not svc.running:
            svc._last_start_attempt = 0.0  # a rebuild is an explicit retry
            svc.maybe_start()
        elif not wanted and svc.running:
            svc.stop()


# -- device kind hooks ------------------------------------------------------
#
# What the settings routes, the Home Assistant config setter and the add /
# register / remove flows ask of a Visionect sign, through the neutral hooks
# in ``app.device_service`` (they never import this module).


def _hook_config_conflict(
    devices: DeviceRegistry, settings: Any, device: Any, values: dict[str, Any], exclude: str
) -> str | None:
    return binding_conflict(devices, settings, device, values, exclude=exclude)


def _hook_config_saved(
    settings: Any, device: Any, values: dict[str, Any], previous: dict[str, Any]
) -> None:
    """Clearing a bound sign's UUID opens the auto-bind window; and a
    connected sign gets the changed settings now."""
    if (
        "uuid" in values
        and normalise_uuid(previous.get("uuid"))
        and not normalise_uuid(values.get("uuid"))
    ):
        arm_auto_bind(settings, device.id)
    svc = service()
    if svc is not None:
        # Not on_push(): this runs under CONFIG_SAVE_LOCK, and starting the
        # listener there would hold every other config save for the bind.
        svc.sync_soon()


def _hook_device_added(settings: Any, device: Any) -> None:
    """A blank sign device binds the next unknown sign, for a while."""
    if not normalise_uuid(_stored_config(settings, device.id).get("uuid")):
        arm_auto_bind(settings, device.id)


def _hook_discovered_config(parsed: dict[str, Any]) -> dict[str, Any]:
    """A discovered sign announces its UUID; the device registered from it
    is bound to that sign."""
    uuid = parsed.get("uuid")
    return {"uuid": uuid} if isinstance(uuid, str) and uuid else {}


def _hook_device_removed(_device: Any) -> None:
    """Re-run the signs so the removed device's sign is released now rather
    than at the next rescan."""
    svc = service()
    if svc is not None and svc.running:
        svc.on_push()


device_service.register_kind_hooks(
    KIND,
    device_service.KindHooks(
        applies_to=is_visionect_device,
        config_conflict=_hook_config_conflict,
        config_saved=_hook_config_saved,
        device_added=_hook_device_added,
        discovered_config=_hook_discovered_config,
        device_removed=_hook_device_removed,
    ),
)
