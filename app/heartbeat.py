"""Daily heartbeat to api.tesserae.ink.

A once-a-day, best-effort ping that reports low-cardinality, aggregate facts
about this install (version, platform family, deployment kind, transport, the
set of registered device kinds, and a bucketed device count) so the maintainer
can see how many installs are active and what to prioritise.

Privacy: gated by the master online-features switch (nothing is sent when it's
off); no personal data, no exact device counts, no exact timestamps (the server
buckets to the day). The cadence is deliberately daily, with jitter, and
deduped on disk so a server that restarts many times a day still pings once. See
the privacy page for the exact payload.
"""

from __future__ import annotations

import contextlib
import json
import logging
import platform
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask

from app import online

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 24 * 60 * 60
_JITTER_SECONDS = 2 * 60 * 60
_RETRY_SECONDS = 60 * 60
_CHECK_INTERVAL_SECONDS = 60 * 60

_OS_MAP = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}
# (inclusive upper bound, label); anything above the last bound is "10+".
_DEVICE_BUCKETS = ((0, "0"), (1, "1"), (3, "2-3"), (9, "4-9"))


def _state_path(data_root: Path) -> Path:
    return data_root / "core" / "heartbeat.json"


def _next_due(data_root: Path) -> float | None:
    try:
        raw = json.loads(_state_path(data_root).read_text(encoding="utf-8"))
        val = raw.get("next_due")
        return float(val) if isinstance(val, (int, float)) else None
    except Exception:
        return None


def _save_next_due(data_root: Path, ts: float) -> None:
    try:
        path = _state_path(data_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"next_due": ts}), encoding="utf-8")
    except Exception:
        logger.debug("heartbeat: could not persist next_due", exc_info=True)


def _arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine.startswith("arm"):
        return "arm"
    return "other"


def _devices_bucket(count: int) -> str:
    for upper, label in _DEVICE_BUCKETS:
        if count <= upper:
            return label
    return "10+"


def _deploy() -> str:
    """Coarse deployment substrate. Most specific wins."""
    from app import ha_options

    try:
        if ha_options.is_ha_addon():
            return "ha_addon"
    except Exception:
        pass
    if Path("/.dockerenv").exists():
        return "docker"
    try:
        if "lxc" in Path("/proc/1/environ").read_bytes().decode("utf-8", "ignore"):
            return "lxc"
    except Exception:
        pass
    try:
        from app.main import REPO_ROOT

        if (REPO_ROOT / ".git").exists():
            return "source"
    except Exception:
        pass
    return "pip"


def build_payload(app: Flask) -> dict[str, Any]:
    """Assemble the heartbeat body from app state. Every value is a family,
    enum, or bucket; nothing here identifies a person or a schedule."""
    settings = app.config.get("SETTINGS_STORE")
    devices: list[Any] = []
    try:
        registry = app.config.get("DEVICE_REGISTRY")
        if registry is not None:
            devices = registry.all()
    except Exception:
        devices = []

    kinds = sorted({d.kind_of for d in devices if getattr(d, "kind_of", None)})[:32]

    transports: set[str] = set()
    for device in devices:
        with contextlib.suppress(Exception):
            transports.add(device.transport)
    if {"mqtt", "rest"} <= transports:
        transport = "both"
    elif "mqtt" in transports:
        transport = "mqtt"
    elif "rest" in transports:
        transport = "rest"
    else:
        transport = "none"

    ha = False
    try:
        app_section = settings.get_section("app") if settings is not None else {}
        ha = bool(app_section.get("ha_discovery_enabled"))
    except Exception:
        ha = False

    return {
        "install": app.config.get("INSTALL_ID") or "",
        "version": app.config.get("APP_VERSION") or "",
        "channel": "stable",
        "os": _OS_MAP.get(platform.system(), "other"),
        "arch": _arch(),
        "py": f"{sys.version_info.major}.{sys.version_info.minor}",
        "deploy": _deploy(),
        "transport": transport,
        "devices": _devices_bucket(len(devices)),
        "device_kinds": kinds,
        "ha": ha,
    }


def maybe_send(app: Flask, *, now: float | None = None) -> bool:
    """Send a heartbeat if online features are on and one is due.

    Best-effort and idempotent per day (the on-disk ``next_due`` gate means a
    restart storm sends at most one). Returns whether a heartbeat was sent.
    """
    settings = app.config.get("SETTINGS_STORE")
    if not online.online_enabled(settings):
        return False
    data_root = app.config.get("DATA_ROOT")
    if data_root is None:
        return False
    now = time.time() if now is None else now
    due = _next_due(data_root)
    if due is not None and now < due:
        return False

    payload = build_payload(app)
    sent = online.send_heartbeat(payload)
    # ~daily with jitter on success; retry sooner on a transient failure.
    if sent:
        nxt = now + _INTERVAL_SECONDS + random.uniform(-_JITTER_SECONDS, _JITTER_SECONDS)
    else:
        nxt = now + _RETRY_SECONDS
    _save_next_due(data_root, nxt)

    event_log = app.config.get("EVENT_LOG")
    if event_log is not None:
        with contextlib.suppress(Exception):
            event_log.record(
                type="telemetry",
                source="heartbeat",
                target="api.tesserae.ink",
                status="sent" if sent else "failed",
                extra={
                    "endpoint": "heartbeat",
                    "version": payload["version"],
                    "deploy": payload["deploy"],
                    "devices": payload["devices"],
                },
            )
    return sent


def start(app: Flask, *, check_interval: float = _CHECK_INTERVAL_SECONDS) -> None:
    """Start the daily-heartbeat daemon thread. No-op under testing."""
    if app.config.get("TESTING"):
        return

    def _loop() -> None:
        # Small random initial delay so a fleet that upgrades at once doesn't
        # ping in lockstep at boot.
        time.sleep(random.uniform(30, 300))
        while True:
            try:
                maybe_send(app)
            except Exception:
                logger.debug("heartbeat: loop iteration failed", exc_info=True)
            time.sleep(check_interval)

    threading.Thread(target=_loop, name="tesserae-heartbeat", daemon=True).start()
