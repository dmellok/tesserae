"""Push Tesserae frames to Fraimic canvases over their local REST API.

A Fraimic on battery spends most of its time in deep sleep, where it can't
be reached. It wakes on a tap, while charging, with "keep awake" on, or at
the times in its own wake schedule, and then answers its REST API for a
short window (``max_idle``, 120 s here) before sleeping again. Tesserae
can't wake it, so this module works around the frame's wakes:

* One background loop probes each frame's ``/api/info``: every
  ``IDLE_POLL_S`` normally (which alone always lands inside a 120 s
  window), and every ``HUNT_POLL_S`` from just before each wake the frame
  last reported, so an upload starts early in the window.
* When a frame answers, Tesserae records its status, writes the device's
  wake schedule to it if the frame's differs, uploads the latest
  ``fraimic_bin`` frame if the frame hasn't had it, and after a scheduled
  wake (not a tap) puts it back to sleep. The frame finishes drawing
  before it sleeps.
* A new frame from the push loop sets an event so a frame that is awake
  anyway (charging, keep awake) gets it straight away.

Everything happens on that one thread, so a push, a probe and an upload
never race. The frame takes 20-35 s to receive a 2.3 MB upload and answers
nothing else meanwhile.

Per-device state (the last frame sent, the frame's upcoming wakes) lives in
``<device data dir>/fraimic.json`` so a restart neither re-uploads nor loses
track of the next wake.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask

from app import background_loops
from app.device_loader import Device, DeviceRegistry

logger = logging.getLogger(__name__)

KIND = "fraimic"
STATE_FILE = "fraimic.json"

TICK_S = 2.0
IDLE_POLL_S = 60.0
HUNT_POLL_S = 3.0
# Start probing this long before a reported wake: the frame is reachable
# "within a few seconds" of the time and may come up a little early.
HUNT_LEAD_S = 15.0
WAKE_WINDOW_S = 120  # max_idle Tesserae asks for (Fraimic's default)
INFO_TIMEOUT_S = 3.0
UPLOAD_TIMEOUT_S = 120.0
ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class FraimicError(Exception):
    """The frame answered with an error (``{"error": code}``)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"HTTP {status}: {code}")
        self.status = status
        self.code = code


Opener = Callable[..., Any]


class FraimicClient:
    """The few REST calls Tesserae makes. ``info`` returns None for a frame
    that doesn't answer (asleep, off the network); the others raise."""

    def __init__(self, host: str, *, opener: Opener = urllib.request.urlopen) -> None:
        self.host = host
        self._open = opener

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        timeout: float = INFO_TIMEOUT_S,
    ) -> dict[str, Any]:
        req = urllib.request.Request(f"http://{self.host}{path}", data=body, method=method)
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with self._open(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            try:
                code = str(json.loads(exc.read().decode("utf-8")).get("error") or exc.reason)
            except (ValueError, AttributeError, OSError):
                code = str(exc.reason)
            raise FraimicError(exc.code, code) from exc
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def info(self) -> dict[str, Any] | None:
        try:
            return self._call("GET", "/api/info")
        except (FraimicError, OSError):
            return None

    def get_schedule(self) -> dict[str, Any]:
        return self._call("GET", "/api/wake-schedule")

    def set_schedule(self, schedule: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST",
            "/api/wake-schedule",
            body=json.dumps(schedule).encode("utf-8"),
            content_type="application/json",
        )

    def upload(self, data: bytes) -> dict[str, Any]:
        return self._call(
            "POST",
            "/api/image",
            body=data,
            content_type="application/octet-stream",
            timeout=UPLOAD_TIMEOUT_S,
        )

    def sleep(self) -> dict[str, Any]:
        return self._call("POST", "/api/sleep")


ClientFactory = Callable[[str], FraimicClient]


def is_fraimic_device(device: Any) -> bool:
    return getattr(device, "kind_of", None) == KIND


def desired_schedule(cfg: dict[str, Any], device: Device) -> dict[str, Any] | None:
    """The wake schedule the device's settings ask for, as the frame's
    ``/api/wake-schedule`` body; None for "leave the frame's own"."""
    mode = str(cfg.get("wake_mode") or "interval")
    if mode == "manual":
        return None
    body: dict[str, Any] = {"enabled": True, "weekdays": ALL_DAYS, "max_idle": WAKE_WINDOW_S}
    if mode == "times":
        parse = getattr(device.module, "parse_times", None)
        times = parse(str(cfg.get("wake_times") or "")) if callable(parse) else None
        if not times:
            return None
        body["times"] = times
    else:
        try:
            body["interval_minutes"] = int(cfg.get("wake_interval_minutes") or 60)
        except (TypeError, ValueError):
            body["interval_minutes"] = 60
    return body


def schedule_matches(current: dict[str, Any], desired: dict[str, Any]) -> bool:
    if not current.get("enabled") or current.get("max_idle") != desired["max_idle"]:
        return False
    if sorted(current.get("weekdays") or []) != sorted(desired["weekdays"]):
        return False
    if "times" in desired:
        return bool(sorted(current.get("times") or []) == desired["times"])
    return bool(current.get("interval_minutes") == desired["interval_minutes"])


def wake_epochs(schedule: dict[str, Any]) -> list[float]:
    """The frame's ``next_wakes`` (ISO times with offset) as epochs."""
    out: list[float] = []
    for raw in schedule.get("next_wakes") or []:
        try:
            stamp = datetime.fromisoformat(str(raw))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            continue  # no offset: can't place it safely
        out.append(stamp.timestamp())
    return sorted(out)


class FraimicPublisher:
    def __init__(
        self,
        *,
        app: Flask,
        devices: DeviceRegistry,
        settings: Any,
        renders_dir: Path,
        latest_render_fn: Callable[[str], dict[str, Any] | None],
        client_factory: ClientFactory = FraimicClient,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._app = app
        self._devices = devices
        self._settings = settings
        self._renders_dir = Path(renders_dir)
        self._latest = latest_render_fn
        self._client_factory = client_factory
        self._clock = clock
        self._state_lock = threading.RLock()
        self.wakeup = threading.Event()
        self._last_probe: dict[str, float] = {}
        self._kicked: set[str] = set()
        self._warned: dict[str, str] = {}

    # -- push reaction ----------------------------------------------------

    def on_push(self, _result: Any = None) -> None:
        """PushManager listener: probe every frame on the next tick, so one
        that is awake anyway gets the new frame now."""
        frames = self._frames()
        if not frames:
            return
        self._kicked.update(d.id for d in frames)
        self.wakeup.set()

    # -- helpers ----------------------------------------------------------

    def _frames(self) -> list[Device]:
        return [d for d in self._devices.devices.values() if is_fraimic_device(d)]

    def _cfg(self, device_id: str) -> dict[str, Any]:
        section = self._settings.get_section("devices") or {}
        cfg = section.get(device_id) if isinstance(section, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    def _warn_once(self, device_id: str, message: str) -> None:
        if self._warned.get(device_id) != message:
            self._warned[device_id] = message
            logger.warning("fraimic %s: %s", device_id, message)

    @staticmethod
    def _state_path(device: Device) -> Path:
        return Path(device.data_dir) / STATE_FILE

    def load_state(self, device: Device) -> dict[str, Any]:
        with self._state_lock:
            try:
                state = json.loads(self._state_path(device).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            return state if isinstance(state, dict) else {}

    def update_state(self, device: Device, changes: dict[str, Any]) -> dict[str, Any]:
        with self._state_lock:
            state = self.load_state(device)
            for key, value in changes.items():
                if value is None:
                    state.pop(key, None)
                else:
                    state[key] = value
            path = self._state_path(device)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(state, indent=2) + "\n")
                os.replace(tmp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
            return state

    def _artifact(self, device: Device) -> tuple[str, Path] | None:
        """The device's latest ``fraimic_bin`` artifact on disk, if any."""
        latest = self._latest(device.id) or {}
        digest = str(latest.get("digest") or "")
        if not digest or str(latest.get("ext") or "bin") != "bin":
            return None
        name = Path(str(latest.get("filename") or f"{digest}.bin")).name
        path = self._renders_dir / name
        return (digest, path) if path.is_file() else None

    def _window(self, state: dict[str, Any], now: float) -> float | None:
        """The reported wake whose window ``now`` is in, if any."""
        handled = set(state.get("handled_wakes") or [])
        for wake in state.get("next_wakes") or []:
            if wake in handled:
                continue
            if wake - HUNT_LEAD_S <= now <= wake + WAKE_WINDOW_S:
                return float(wake)
        return None

    def _due(self, device: Device, state: dict[str, Any], now: float) -> bool:
        if device.id in self._kicked:
            return True
        last = self._last_probe.get(device.id)
        if last is None:
            return True
        gap = HUNT_POLL_S if self._window(state, now) is not None else IDLE_POLL_S
        return now - last >= gap

    # -- the loop ---------------------------------------------------------

    def tick(self) -> None:
        for device in self._frames():
            try:
                now = self._clock()
                state = self.load_state(device)
                if not self._due(device, state, now):
                    continue
                self._kicked.discard(device.id)
                self._last_probe[device.id] = now
                self._visit(device, state, now)
            except Exception:
                logger.exception("fraimic %s: visit failed", device.id)

    def _visit(self, device: Device, state: dict[str, Any], now: float) -> None:
        cfg = self._cfg(device.id)
        host = str(cfg.get("host") or "").strip()
        if not host:
            self._warn_once(device.id, "no frame address set (Settings -> Devices); skipping")
            return
        client = self._client_factory(host)
        info = client.info()
        if info is None:
            return  # asleep: the usual case
        wake = self._window(state, now)
        state = self._sync_schedule(device, cfg, client, state)
        sent = self._send_pending(device, client, state)
        extras: dict[str, Any] = {"awake": True}
        if wake is not None:
            handled = [w for w in state.get("handled_wakes") or [] if w > now - 86400]
            state = self.update_state(device, {"handled_wakes": [*handled, wake]})
            raw_battery, raw_settings = info.get("battery"), info.get("settings")
            battery: dict[str, Any] = raw_battery if isinstance(raw_battery, dict) else {}
            keep_awake = isinstance(raw_settings, dict) and bool(raw_settings.get("keep_awake"))
            if (
                sent is not False
                and cfg.get("sleep_after_upload", True) is not False
                and not battery.get("charging")
                and not keep_awake
            ):
                try:
                    client.sleep()
                    extras["awake"] = False
                except (FraimicError, OSError) as exc:
                    logger.info("fraimic %s: couldn't put the frame to sleep: %s", device.id, exc)
        upcoming = [w for w in state.get("next_wakes") or [] if w > self._clock()]
        if upcoming:
            extras["next_wake"] = datetime.fromtimestamp(upcoming[0], UTC).isoformat()
        if state.get("last_upload"):
            extras["last_upload"] = state["last_upload"]
        self._record(device, {**info, **extras})

    def _sync_schedule(
        self, device: Device, cfg: dict[str, Any], client: FraimicClient, state: dict[str, Any]
    ) -> dict[str, Any]:
        desired = desired_schedule(cfg, device)
        try:
            current = client.get_schedule()
            if desired is not None and not schedule_matches(current, desired):
                current = client.set_schedule(desired)
                logger.info("fraimic %s: wake schedule set to %s", device.id, desired)
        except FraimicError as exc:
            if exc.status == 404:
                self._warn_once(
                    device.id,
                    "this firmware has no wake schedule; update the frame (0.2.31 or later)",
                )
            else:
                self._warn_once(device.id, f"couldn't set the wake schedule: {exc}")
            return state
        except OSError as exc:
            logger.info("fraimic %s: wake schedule check failed: %s", device.id, exc)
            return state
        wakes = wake_epochs(current)
        if wakes or not current.get("enabled"):
            state = self.update_state(device, {"next_wakes": wakes})
        return state

    def _send_pending(
        self, device: Device, client: FraimicClient, state: dict[str, Any]
    ) -> bool | None:
        """Upload the latest frame if the frame hasn't had it. None when
        there was nothing to send, else whether the upload worked."""
        artifact = self._artifact(device)
        if artifact is None:
            return None
        digest, path = artifact
        if state.get("last_digest") == digest:
            return None
        data = path.read_bytes()
        try:
            client.upload(data)
        except FraimicError as exc:
            if exc.code == "buffer_not_ready":
                self._kicked.add(device.id)  # busy drawing: try again next tick
            else:
                self._warn_once(device.id, f"frame refused the picture: {exc}")
            return False
        except OSError as exc:
            self._warn_once(device.id, f"upload to {client.host} failed: {exc}")
            return False
        self._warned.pop(device.id, None)
        self.update_state(device, {"last_digest": digest, "last_upload": int(self._clock())})
        logger.info("fraimic %s: sent frame %s (%d KB)", device.id, digest, len(data) // 1024)
        return True

    def _record(self, device: Device, heartbeat: dict[str, Any]) -> None:
        events = self._app.config.get("EVENT_LOG")
        status_cache = self._app.config.get("DEVICE_STATUS")
        if events is None or status_cache is None:
            return
        from app.transport_wiring import record_status_heartbeat

        try:
            with self._app.app_context():
                record_status_heartbeat(
                    app=self._app,
                    device=device,
                    payload=json.dumps(heartbeat).encode("utf-8"),
                    status_cache=status_cache,
                    event_log=events,
                    event_target=f"fraimic://{device.id}",
                )
        except Exception:
            logger.exception("fraimic %s: recording status failed", device.id)


class FraimicPoller:
    def __init__(self, publisher: FraimicPublisher, *, tick_s: float = TICK_S) -> None:
        self._publisher = publisher
        self._tick_s = tick_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="fraimic-poll", daemon=True)
        self._thread.start()
        background_loops.track(self)

    def stop(self) -> None:
        self._stop.set()
        self._publisher.wakeup.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._publisher.tick()
            except Exception:
                logger.exception("fraimic: tick failed")
            self._publisher.wakeup.wait(self._tick_s)
            self._publisher.wakeup.clear()


def register(app: Flask) -> None:
    """Build the publisher, wire it to the push loop and start the poller.
    Runs again on every transport rebuild, so the old poller stops first."""
    push_mgr = app.config.get("PUSH_MANAGER")
    devices = app.config.get("DEVICE_REGISTRY")
    settings = app.config.get("SETTINGS_STORE")
    renders_dir = app.config.get("RENDERS_DIR")
    if push_mgr is None or devices is None or settings is None or renders_dir is None:
        return
    under_test = bool(
        app.testing or app.config.get("TESTING") or os.environ.get("PYTEST_CURRENT_TEST")
    )
    pub = FraimicPublisher(
        app=app,
        devices=devices,
        settings=settings,
        renders_dir=renders_dir,
        latest_render_fn=push_mgr.latest_render_for,
    )
    push_mgr.add_listener(pub.on_push)
    app.config["FRAIMIC_PUBLISHER"] = pub
    old = app.config.get("FRAIMIC_POLLER")
    if old is not None:
        with contextlib.suppress(Exception):
            old.stop()
    poller = FraimicPoller(pub)
    if not under_test:
        poller.start()
    app.config["FRAIMIC_POLLER"] = poller
