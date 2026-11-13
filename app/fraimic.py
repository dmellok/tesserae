"""Push Tesserae frames to Fraimic canvases over their local REST API.

A Fraimic on battery spends most of its time in deep sleep, where it can't
be reached. It wakes on a tap, while charging, with "keep awake" on, or at
the times in its own wake schedule, and then answers its REST API for a
short window (``max_idle``, 120 s here) before sleeping again. Tesserae
can't wake it, so this module works around the frame's wakes:

* One background loop probes each frame's ``/api/info``: every
  ``IDLE_POLL_S`` normally (which alone always lands inside a 120 s
  window), and every ``HUNT_POLL_S`` from just before each wake the frame
  last reported and while a wake is being dealt with.
* When a frame answers, Tesserae records its status, uploads the latest
  ``fraimic_bin`` frame if the frame hasn't had it, writes the wake
  schedule the device's settings ask for (``app.fraimic_wakes``: the
  frame's own interval or times where those fit, otherwise a chain of the
  next four wakes, rewritten at every wake) and after a scheduled wake
  (not a tap) puts it back to sleep. The frame finishes drawing before it
  sleeps.
* The frame's next wake is handed to the telemetry store as a server
  directive, so smart sync renders just before it, the device page shows
  the next check-in, and a frame that wakes on time earns trust.
* A new frame from the push loop sets an event so a frame that is awake
  anyway (charging, keep awake) gets it straight away.

A wake is one **session**, told apart by the frame's boot time (now minus
its ``uptime_s``). Two things are done once per session: the heartbeat
that scores the wake against its prediction, and the tap action. A frame
is never put to sleep before it has been up ``MIN_AWAKE_S``: put down a
couple of seconds into a wake, it doesn't arm its next alarm.

Everything happens on that one thread, so a push, a probe and an upload
never race. The frame takes 20-35 s to receive a 2.3 MB upload and answers
nothing else meanwhile.

Per-device state (the last frame sent, the frame's upcoming wakes, its
clock offset and boot time) lives in ``<device data dir>/fraimic.json`` so
a restart neither re-uploads nor loses track of the next wake.
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

from app import background_loops, fraimic_wakes
from app.device_loader import Device, DeviceRegistry
from app.fraimic_wakes import WAKE_WINDOW_S, WakePlan

logger = logging.getLogger(__name__)

KIND = "fraimic"
ALL_DAYS = fraimic_wakes.ALL_DAYS
STATE_FILE = "fraimic.json"

TICK_S = 2.0
IDLE_POLL_S = 60.0
HUNT_POLL_S = 3.0
# Start probing this long before a reported wake: the frame is reachable
# "within a few seconds" of the time and may come up a little early.
HUNT_LEAD_S = 15.0
INFO_TIMEOUT_S = 3.0
UPLOAD_TIMEOUT_S = 120.0
# The frame must have been up this long before it is put back to sleep.
# Measured: slept 2 s into a scheduled wake it never woke again; slept
# after a 30 s stay (about 40 s of uptime) it woke six times out of six.
MIN_AWAKE_S = 40.0
# In "changes" mode the wake is aimed at a scheduled push whose render may
# still be running: give it this long to arrive before sleeping without it.
PICTURE_WAIT_S = 90.0
# Outside a scheduled wake the schedule is written once the frame has been
# up this long (its clock is valid by then; 10 s after first contact worked).
POST_MIN_UPTIME_S = 15.0
# Two readings of the frame's boot time this close are the same boot.
BOOT_SLACK_S = 15.0
# A wake first seen with more uptime than this isn't judged a tap: the
# frame was up for some other reason before Tesserae got to it.
TAP_MAX_UPTIME_S = 90.0
# How long a session that isn't a scheduled wake is probed quickly while
# its schedule still has to be written.
SETTLE_S = 150.0
REDRAW_PERIODS_S = {"daily": 86400, "weekly": 7 * 86400}
STEP_LOCK_TIMEOUT_S = 20.0
STEP_SETTLE_MAX_S = 45.0


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

    def refresh(self) -> dict[str, Any]:
        """Redraw the picture the frame already shows (clears ghosting)."""
        return self._call("POST", "/api/refresh")


ClientFactory = Callable[[str], FraimicClient]


def is_fraimic_device(device: Any) -> bool:
    """Whether an instance or kind is a Fraimic canvas: the folder kind, an
    instance of it, or a catalog SKU on its protocol (those share
    devices/fraimic/device.py, which carries the PROTOCOL marker)."""
    if getattr(device, "kind_of", None) == KIND or getattr(device, "id", None) == KIND:
        return True
    return getattr(getattr(device, "module", None), "PROTOCOL", None) == KIND


def fraimic_kinds(devices: DeviceRegistry) -> list[Device]:
    """The kinds a Fraimic can be added as: the generic kind first, then
    the catalog models by name."""
    kinds = [d for d in devices.devices.values() if not d.kind_of and is_fraimic_device(d)]
    return sorted(kinds, key=lambda d: (d.id != KIND, str(d.name).casefold()))


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


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def frame_offset_s(schedule: dict[str, Any] | None, info: dict[str, Any], now: float) -> int | None:
    """The frame's UTC offset in seconds: from the offset on its
    ``next_wakes``, else from its ``local_time`` against ours (rounded to a
    quarter hour). None while its clock isn't set."""
    for raw in (schedule or {}).get("next_wakes") or []:
        try:
            offset = datetime.fromisoformat(str(raw)).utcoffset()
        except ValueError:
            continue
        if offset is not None:
            return int(offset.total_seconds())
    device = info.get("device")
    if not isinstance(device, dict) or not device.get("time_synced"):
        return None
    try:
        local = datetime.fromisoformat(str(device.get("local_time")))
    except ValueError:
        return None
    if local.tzinfo is not None:
        offset = local.utcoffset()
        return int(offset.total_seconds()) if offset is not None else None
    skew = local.replace(tzinfo=UTC).timestamp() - now
    return round(skew / 900) * 900


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
        # One entry per frame that is awake now: see ``_session``.
        self._sessions: dict[str, dict[str, Any]] = {}
        # The wake last handed to telemetry, so it is noted once.
        self._directed: dict[str, float] = {}
        # Whether each frame was held awake at its last telemetry heartbeat.
        self._held_noted: dict[str, bool] = {}
        # Wakes from before this process started aren't counted as missed
        # by the frame: the server wasn't there to see them.
        self._started = clock()

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
        return [d for d in self._devices.devices.values() if d.kind_of and is_fraimic_device(d)]

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
        session = self._sessions.get(device.id)
        fast = self._window(state, now) is not None or bool(session and session["busy"])
        return now - last >= (HUNT_POLL_S if fast else IDLE_POLL_S)

    # -- the loop ---------------------------------------------------------

    def tick(self) -> None:
        for device in self._frames():
            try:
                now = self._clock()
                state = self.load_state(device)
                self._note_missed(device, state, now)
                if not self._due(device, state, now):
                    continue
                self._kicked.discard(device.id)
                self._last_probe[device.id] = now
                self._visit(device, state, now)
            except Exception:
                logger.exception("fraimic %s: visit failed", device.id)

    def _note_missed(self, device: Device, state: dict[str, Any], now: float) -> None:
        """Count reported wakes that came and went without the frame
        answering (flat battery, off the network, a chain that broke)."""
        if device.id in self._sessions:
            return  # awake across the wake: nothing was missed
        handled = set(state.get("handled_wakes") or [])
        after = _number(state.get("missed_after")) or 0.0
        missed = [
            w
            for w in state.get("next_wakes") or []
            if w > max(after, self._started)
            and w not in handled
            and w + WAKE_WINDOW_S + IDLE_POLL_S < now
        ]
        if not missed:
            return
        count = int(state.get("missed_wakes") or 0) + len(missed)
        self.update_state(device, {"missed_wakes": count, "missed_after": max(missed)})
        logger.warning(
            "fraimic %s: no answer at its wake of %s (%d missed since it last answered)",
            device.id,
            datetime.fromtimestamp(max(missed), UTC).isoformat(),
            count,
        )

    def _session(
        self, device: Device, state: dict[str, Any], info: dict[str, Any], now: float
    ) -> dict[str, Any]:
        """The frame's current wake. A new one starts when its boot time
        (now minus ``uptime_s``) moves; the boot time is kept in the state
        file so a server restart mid-wake doesn't start one."""
        raw = info.get("device")
        uptime = _number(raw.get("uptime_s")) if isinstance(raw, dict) else None
        boot = now - uptime if uptime is not None else None
        current = self._sessions.get(device.id)
        if current is not None and (boot is None or abs(boot - current["boot"]) <= BOOT_SLACK_S):
            return current
        known = _number(state.get("boot"))
        fresh = boot is None or known is None or abs(boot - known) > BOOT_SLACK_S
        start = boot if boot is not None else now
        session: dict[str, Any] = {
            "boot": start,
            "since": now,
            "fresh": fresh,
            "first_uptime": uptime,
            "seen_before": known is not None,
            "window": self._window(state, start) if fresh else None,
            # A wake picked up halfway (server restart) is neither slept
            # nor treated as a tap: nothing is known about how it began.
            "classified": not fresh,
            "scheduled": False,
            "tap": False,
            "tap_done": False,
            "scored": False,
            "uploaded": False,
            "redrawn": False,
            "slept": False,
            "busy": fresh,
            "missed": int(state.get("missed_wakes") or 0) if fresh else 0,
        }
        self._sessions[device.id] = session
        if fresh:
            self.update_state(device, {"boot": round(start), "missed_wakes": None})
        return session

    @staticmethod
    def _held_awake(info: dict[str, Any]) -> bool:
        """Charging or set to stay awake: the frame won't sleep by itself."""
        battery, settings = info.get("battery"), info.get("settings")
        charging = isinstance(battery, dict) and bool(battery.get("charging"))
        keep_awake = isinstance(settings, dict) and bool(settings.get("keep_awake"))
        return charging or keep_awake

    def _visit(self, device: Device, state: dict[str, Any], now: float) -> None:
        cfg = self._cfg(device.id)
        host = str(cfg.get("host") or "").strip()
        if not host:
            self._warn_once(device.id, "no frame address set (Settings -> Devices); skipping")
            return
        client = self._client_factory(host)
        info = client.info()
        if info is None:
            self._sessions.pop(device.id, None)
            return  # asleep: the usual case
        session = self._session(device, state, info, now)
        held = self._held_awake(info)
        schedule = self._read_schedule(device, client)
        offset = frame_offset_s(schedule, info, now)
        if offset is not None:
            self.update_state(device, {"utc_offset_s": offset})
        state = self.load_state(device)
        if offset is None and _number(state.get("utc_offset_s")) is not None:
            offset = int(state["utc_offset_s"])
        if not session["classified"] and schedule is not None:
            state = self._classify(device, session, schedule, offset, held, state, now)
        self._beat(device, info, session, held)
        if session["tap"] and not session["tap_done"]:
            session["tap_done"] = True
            self._tap_action(device, cfg)
        sent = self._send_pending(device, client, state)
        if sent:
            session["uploaded"] = True
        elif sent is None:
            self._maybe_redraw(device, cfg, client, session)
        now = self._clock()  # an upload takes half a minute
        state = self.load_state(device)
        uptime = now - session["boot"]
        plan = self._plan(device, cfg, info, offset, now)
        armed, state = self._apply_plan(device, client, plan, schedule, session, uptime, state)
        self._direct(device, state, held, now)
        mode = str(cfg.get("wake_mode") or fraimic_wakes.MODE_INTERVAL)
        waits_for_picture = mode == fraimic_wakes.MODE_CHANGES and not session["uploaded"]
        wants_sleep = (
            session["scheduled"]
            and not session["slept"]
            and not held
            and cfg.get("sleep_after_upload", True) is not False
        )
        if (
            wants_sleep
            and armed
            and sent is not False
            and uptime >= (PICTURE_WAIT_S if waits_for_picture else MIN_AWAKE_S)
        ):
            try:
                client.sleep()
                session["slept"] = True
            except (FraimicError, OSError) as exc:
                logger.info("fraimic %s: couldn't put the frame to sleep: %s", device.id, exc)
        # Probe quickly while there is still something to do in this wake;
        # after SETTLE_S the frame is left to its own idle timeout.
        unsettled = not session["classified"] or not armed or (wants_sleep and not session["slept"])
        session["busy"] = not held and unsettled and now - session["since"] < SETTLE_S
        extras: dict[str, Any] = {"awake": not session["slept"], "missed_wakes": session["missed"]}
        upcoming = [w for w in state.get("next_wakes") or [] if w > now]
        if upcoming:
            extras["next_wake"] = datetime.fromtimestamp(upcoming[0], UTC).isoformat()
        if state.get("last_upload"):
            extras["last_upload"] = state["last_upload"]
        self._status(device, {**info, **extras})

    def _read_schedule(self, device: Device, client: FraimicClient) -> dict[str, Any] | None:
        try:
            return client.get_schedule()
        except FraimicError as exc:
            if exc.status == 404:
                self._warn_once(
                    device.id,
                    "this firmware has no wake schedule; update the frame (0.2.31 or later)",
                )
            else:
                self._warn_once(device.id, f"couldn't read the wake schedule: {exc}")
        except OSError as exc:
            logger.info("fraimic %s: wake schedule check failed: %s", device.id, exc)
        return None

    def _classify(
        self,
        device: Device,
        session: dict[str, Any],
        schedule: dict[str, Any],
        offset: int | None,
        held: bool,
        state: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """Decide, once per wake, whether the frame's schedule woke it or
        somebody tapped it. A scheduled wake is one Tesserae had on record,
        or one that falls on the schedule the frame holds (a chain slot
        that came round again while the server was away)."""
        window = session["window"]
        scheduled = window is not None or fraimic_wakes.on_schedule_clock(
            schedule, session["boot"], offset
        )
        first = session["first_uptime"]
        session.update(
            classified=True,
            scheduled=scheduled,
            tap=(
                not scheduled
                and not held
                and session["seen_before"]
                and first is not None
                and first <= TAP_MAX_UPTIME_S
            ),
        )
        if window is None:
            return state
        handled = [w for w in state.get("handled_wakes") or [] if w > now - 86400]
        return self.update_state(device, {"handled_wakes": [*handled, window]})

    def _beat(
        self, device: Device, info: dict[str, Any], session: dict[str, Any], held: bool
    ) -> None:
        """The heartbeat that feeds telemetry, at most once per wake.

        Telemetry scores every heartbeat against the predicted wake, so
        only a scheduled wake may send one: a tap comes at no predicted
        time and would throw the frame's trust away, and a second beat in
        the same wake would be scored against the *next* wake. Also sent
        when the frame isn't known to telemetry yet, and when it starts or
        stops being held awake (which telemetry tracks as always-on)."""
        telemetry = self._app.config.get("DEVICE_TELEMETRY")
        entry = None
        if telemetry is not None:
            try:
                entry = telemetry.get(device.id)
            except Exception:
                entry = None
        due = session["scheduled"] and not session["scored"]
        if not due and telemetry is not None:
            due = entry is None or (
                bool(entry.always_on) != held and self._held_noted.get(device.id) != held
            )
        if not due:
            return
        session["scored"] = True
        self._held_noted[device.id] = held
        self._record(device, info)

    def _tap_action(self, device: Device, cfg: dict[str, Any]) -> None:
        """A tap on the frame as a button press: step the display on."""
        if str(cfg.get("tap_action") or "none") != "next":
            return
        buttons = self._app.config.get("BUTTON_SERVICE")
        if buttons is None:
            return
        try:
            with self._app.app_context():
                buttons.step(
                    device_id=device.id,
                    forward=True,
                    source="fraimic_tap",
                    lock_timeout_s=STEP_LOCK_TIMEOUT_S,
                    settle_max_s=STEP_SETTLE_MAX_S,
                )
        except Exception:
            logger.exception("fraimic %s: tap action failed", device.id)

    def _maybe_redraw(
        self, device: Device, cfg: dict[str, Any], client: FraimicClient, session: dict[str, Any]
    ) -> None:
        """Redraw the picture already on the glass when it has sat there
        for the configured period, on a scheduled wake with nothing new."""
        period = REDRAW_PERIODS_S.get(str(cfg.get("redraw") or "off"))
        if period is None or not session["scheduled"] or session["redrawn"]:
            return
        state = self.load_state(device)
        drawn = max(
            _number(state.get("last_upload")) or 0.0, _number(state.get("last_redraw")) or 0
        )
        now = self._clock()
        if not drawn or now - drawn < period:
            return
        session["redrawn"] = True
        try:
            client.refresh()
        except (FraimicError, OSError) as exc:
            logger.info("fraimic %s: redraw refused: %s", device.id, exc)
            return
        self.update_state(device, {"last_redraw": int(now)})
        logger.info("fraimic %s: redrew the picture to clear ghosting", device.id)

    def _plan(
        self,
        device: Device,
        cfg: dict[str, Any],
        info: dict[str, Any],
        offset: int | None,
        now: float,
    ) -> WakePlan:
        from app.quiet_hours import resolve_quiet_hours
        from app.tz_resolve import app_timezone

        mode = str(cfg.get("wake_mode") or fraimic_wakes.MODE_INTERVAL)
        battery = info.get("battery")
        percent = battery.get("percent") if isinstance(battery, dict) else None
        changes: list[float] = []
        page_interval: int | None = None
        with self._app.app_context():
            tz = app_timezone()
            quiet = resolve_quiet_hours(self._settings.get_section("app") or {}, device)
            if mode == fraimic_wakes.MODE_CHANGES:
                scheduler = self._app.config.get("SCHEDULER")
                upcoming = getattr(scheduler, "upcoming_for_device", None)
                if callable(upcoming):
                    try:
                        events = upcoming(
                            device.id,
                            now=datetime.fromtimestamp(now, UTC),
                            limit=fraimic_wakes.CHAIN_SLOTS * 2,
                            quiet_window=quiet,
                        )
                        # A dashboard's own Updates refresh is not a lineup
                        # change, so it doesn't spend one of the frame's wakes.
                        changes = [
                            e.scheduled_at.timestamp()
                            for e in events
                            if getattr(e, "cause", None) != "dashboard_refresh"
                        ][: fraimic_wakes.CHAIN_SLOTS]
                    except Exception:
                        logger.exception("fraimic %s: reading upcoming pushes failed", device.id)
            elif mode == fraimic_wakes.MODE_INTERVAL:
                try:
                    from app import page_cadence

                    page_interval = page_cadence.page_sleep_interval_s(device)
                except Exception:
                    logger.exception("fraimic %s: page cadence failed", device.id)
        return fraimic_wakes.plan_wakes(
            cfg,
            now=now,
            tz=tz,
            offset_s=offset,
            quiet=quiet,
            battery_pct=percent,
            changes=changes,
            page_interval_s=page_interval,
        )

    def _apply_plan(
        self,
        device: Device,
        client: FraimicClient,
        plan: WakePlan,
        schedule: dict[str, Any] | None,
        session: dict[str, Any],
        uptime: float,
        state: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """Make the frame's schedule the planned one. Returns whether the
        frame now holds a schedule it can be put to sleep on.

        On a scheduled wake the write waits until the frame may be slept,
        so it is followed by the sleep at once: write-then-sleep after a
        stay is the order that was measured to work. Until then the
        frame still holds the previous chain's later slots."""
        armed = plan.body is None and not plan.needs_clock
        if plan.body is not None and schedule is not None:
            if schedule_matches(schedule, plan.body):
                armed = True
            else:
                ready = uptime >= (MIN_AWAKE_S if session["scheduled"] else POST_MIN_UPTIME_S)
                if plan.chained and not schedule.get("time_valid"):
                    ready = False  # its clock isn't set: the times would mean nothing
                if ready:
                    try:
                        schedule = client.set_schedule(plan.body)
                        armed = True
                        self._warned.pop(device.id, None)
                        logger.info("fraimic %s: wake schedule set to %s", device.id, plan.body)
                    except FraimicError as exc:
                        self._warn_once(device.id, f"couldn't set the wake schedule: {exc}")
                    except OSError as exc:
                        logger.info("fraimic %s: wake schedule write failed: %s", device.id, exc)
        if schedule is not None:
            wakes = wake_epochs(schedule)
            if wakes or not schedule.get("enabled"):
                state = self.update_state(device, {"next_wakes": wakes})
        return armed, state

    def _direct(self, device: Device, state: dict[str, Any], held: bool, now: float) -> None:
        """Tell telemetry when the frame next wakes, as wake alignment does
        for a device that polls: smart sync aims its render at it and the
        wake is scored against it."""
        telemetry = self._app.config.get("DEVICE_TELEMETRY")
        if telemetry is None or held:
            return
        upcoming = [w for w in state.get("next_wakes") or [] if w > now + 5]
        if not upcoming or self._directed.get(device.id) == upcoming[0]:
            return
        try:
            noted = telemetry.note_server_directive(
                device.id, wake_at=upcoming[0], interval_s=int(upcoming[0] - now)
            )
        except Exception:
            logger.exception("fraimic %s: wake directive note failed", device.id)
            return
        if noted is not None:
            self._directed[device.id] = upcoming[0]

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

    def _status(self, device: Device, heartbeat: dict[str, Any]) -> None:
        """Refresh the device's cached status without a telemetry sample
        (see ``_beat`` for why most visits must not send one)."""
        cache = self._app.config.get("DEVICE_STATUS")
        if cache is None:
            return
        from app.transport_wiring import merge_status_parsed

        try:
            parsed = device.parse_status(json.dumps(heartbeat).encode("utf-8"))
            prev = cache.get(device.id) or {}
            merged = merge_status_parsed(prev.get("parsed") or {}, parsed)
            cache[device.id] = {**prev, "received_at": time.time(), "parsed": merged}
        except Exception:
            logger.exception("fraimic %s: status update failed", device.id)

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
