"""When a display will actually get its next update.

Two things decide it, and a number that uses only one of them contradicts
the other. *When the server pushes*: the next lineup step, schedule, timer
advance or page refresh, which the scheduler's projection
(:mod:`app.device_upcoming`) already answers, held back by quiet hours or a
pause. *When the display can receive*: a frame holding a live connection (a
Visionect sign) or one that stays awake takes a push right away, while a
sleeping battery frame only gets it at its next check-in.

``compute_next_update`` combines them: the earliest moment a change can reach
the display is the push time, pushed out to the first expected check-in at or
after it for a display that sleeps. The Devices page, the device page and the
Lineups page all show this one number, so they agree.

The computation is pure (plain numbers and a ``Reach``); the ``*_for_device``
helpers below read the app's stores to fill it in.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any

MODE_CONNECTED = "connected"
MODE_SLEEPS = "sleeps"
MODE_UNKNOWN = "unknown"

# Bound on how many check-ins are stepped over when the grid is not a plain
# repeating gap (wake alignment). A day of 1-minute check-ins is 1440.
_MAX_STEPS = 2000


@dataclass(frozen=True)
class Reach:
    """When a display can receive a push.

    ``connected``: right away (a live connection, or a display that stays
    awake and polls). ``sleeps``: only at a check-in; ``next_checkin_at`` is
    the next expected one, ``gap_s`` the usual gap between them, ``step``
    (optional) the check-in that follows a given one when the grid is not a
    plain repeating gap. ``unknown``: nothing known, so the push time stands.
    ``live`` marks a live connection as opposed to an awake-and-polling one.
    """

    mode: str
    next_checkin_at: float | None = None
    gap_s: float | None = None
    live: bool = False
    step: Callable[[float], float | None] | None = None
    # True when ``gap_s`` is a cadence the device keeps (the operator set its
    # sleep interval, or the firmware states it), not a spacing seen between
    # heartbeats of a device that may simply be awake on power.
    cadence_stable: bool = False


@dataclass(frozen=True)
class Hold:
    """Automated pushes to this display are held. ``until`` is ``None`` for
    a hold with no end (the global automation pause, or quiet hours that
    never open within the week)."""

    until: float | None
    reason: str


@dataclass(frozen=True)
class NextUpdate:
    at: float | None  # when the change reaches the display; None = nothing scheduled
    push_at: float | None  # when the server pushes (after any hold)
    mode: str
    checkin_at: float | None  # the check-in it arrives on (sleeps), else the next one
    held_until: float | None = None
    held_reason: str | None = None
    held_indefinitely: bool = False


def _checkin_at_or_after(reach: Reach, t: float) -> float | None:
    """The first expected check-in at or after ``t``, stepping forward from
    the next one. ``None`` when no check-in is known or the grid can't be
    stepped."""
    c = reach.next_checkin_at
    if c is None:
        return None
    if c >= t:
        return c
    if reach.step is not None:
        for _ in range(_MAX_STEPS):
            nxt = reach.step(c)
            if nxt is None or nxt <= c:
                return None
            c = nxt
            if c >= t:
                return c
        return None
    gap = reach.gap_s
    if gap is None or gap <= 0:
        return None
    return c + math.ceil((t - c) / gap) * gap


def compute_next_update(
    push_at: float | None, reach: Reach, *, now: float, hold: Hold | None = None
) -> NextUpdate:
    """The earliest moment a scheduled change can reach the display:
    ``max(push time, earliest time the display can receive)``.

    * A hold (quiet hours, a pause) pushes the push time out to its end; a
      hold with no end means no automatic update at all.
    * A display that is connected (or stays awake) receives at the push time.
    * A sleeping display receives at its first expected check-in at or after
      the push time (so a push that falls between two check-ins waits for the
      later one). With no check-in estimate the push time stands.
    * With no push scheduled there is nothing to deliver: ``at`` is ``None``,
      but a sleeping display's next check-in is still reported.
    """
    held_until: float | None = None
    held_reason: str | None = None
    indefinite = False
    if hold is not None:
        held_reason = hold.reason
        if hold.until is None:
            indefinite = True
        elif hold.until > now:
            held_until = hold.until
        else:
            held_reason = None
    push = None if push_at is None else max(push_at, now)
    if push is not None and held_until is not None:
        push = max(push, held_until)
    floor = now if push is None else push
    checkin: float | None = None
    if reach.mode == MODE_SLEEPS:
        checkin = _checkin_at_or_after(reach, floor)
    at: float | None
    if indefinite or push is None:
        at = None
    elif reach.mode == MODE_SLEEPS and checkin is not None:
        at = checkin
    else:
        at = push
    return NextUpdate(
        at=at,
        push_at=push,
        mode=reach.mode,
        checkin_at=checkin,
        held_until=held_until,
        held_reason=held_reason,
        held_indefinitely=indefinite,
    )


def _clock(epoch: float, tz: tzinfo | None, now: float, cycle: str | None = None) -> str:
    """``18:24`` for today, ``Tue 07:00`` for another day; with ``cycle``
    ``h12`` the app's 12-hour form (``6:24 PM``, ``Tue 7 AM``)."""
    if cycle == "h12":
        from app.clock_text import epoch_label

        return epoch_label(epoch, tz=tz, now=now, cycle=cycle)
    moment = datetime.fromtimestamp(epoch, tz=tz)
    today = datetime.fromtimestamp(now, tz=tz).date()
    if moment.date() == today:
        return moment.strftime("%H:%M")
    return moment.strftime("%a %H:%M")


def reason_for(nu: NextUpdate, reach: Reach) -> str:
    """The one-phrase reason for a ``NextUpdate``: a hold wins (``held by
    quiet hours``, ``held by a pause``, ``held by the automation pause``),
    else ``connected`` (arrives right away), ``check-in`` (waits for the
    sleeping frame's next check-in) or ``scheduled`` (nothing known about
    reach, the push time stands)."""
    if nu.held_indefinitely:
        if nu.held_reason == "quiet hours":
            return "held by quiet hours"
        return "held by the automation pause"
    if nu.held_until is not None:
        return f"held by {nu.held_reason}"
    if nu.mode == MODE_CONNECTED:
        return "connected"
    if nu.mode == MODE_SLEEPS:
        return "check-in"
    return "scheduled"


def describe(
    nu: NextUpdate,
    reach: Reach,
    *,
    tz: tzinfo | None,
    now: float,
    cycle: str | None = None,
) -> dict[str, Any]:
    """Plain words for a ``NextUpdate``: ``when`` (``~18:24``), ``detail`` (the
    reason in brackets, short), ``reason`` (the one-phrase version) and
    ``text`` (the whole sentence). Times read 24-hour unless ``cycle`` is
    ``h12`` (``~6:24 PM``)."""
    parts: list[str] = []
    if nu.held_indefinitely:
        parts.append(
            "quiet hours all week" if nu.held_reason == "quiet hours" else "automation is paused"
        )
    elif nu.held_until is not None:
        parts.append(f"held until {_clock(nu.held_until, tz, now, cycle)} by {nu.held_reason}")
    if nu.mode == MODE_CONNECTED:
        parts.append(
            "frame connected, arrives right away"
            if reach.live
            else "frame stays awake, arrives on its next poll"
        )
    elif nu.mode == MODE_SLEEPS:
        if nu.checkin_at is not None:
            parts.append(f"frame sleeps; next check-in ~{_clock(nu.checkin_at, tz, now, cycle)}")
        else:
            parts.append("frame sleeps")
    detail = " · ".join(parts)
    when = f"~{_clock(nu.at, tz, now, cycle)}" if nu.at is not None else ""
    if nu.at is not None:
        text = f"Next update {when}" + (f" ({detail})" if detail else "")
    else:
        text = "No update scheduled" + (f" ({detail})" if detail else "")
    return {
        "at": nu.at,
        "when": when,
        "detail": detail,
        "text": text,
        "mode": nu.mode,
        "reason": reason_for(nu, reach),
        "held": nu.held_until is not None or nu.held_indefinitely,
    }


# -- reading the app ------------------------------------------------------


def _app_cycle() -> str:
    from app.clock_text import app_cycle

    return app_cycle()


def _tz() -> tzinfo:
    from app.tz_resolve import app_timezone

    return app_timezone()


def reach_for_device(device: Any, *, now: float | None = None) -> Reach:
    """What is known about when ``device`` can receive, from live connection
    state and telemetry."""
    from flask import current_app

    from app import visionect
    from app.device_service import awake_poll_interval_s

    now = time.time() if now is None else now
    config = current_app.config
    settings = config.get("SETTINGS_STORE")
    stored: Any = None
    if settings is not None:
        section = settings.get_section("devices") or {}
        stored = section.get(device.id) if isinstance(section, dict) else None
    telemetry = config.get("DEVICE_TELEMETRY")
    entry = None
    if telemetry is not None:
        try:
            entry = telemetry.get(device.id)
        except Exception:
            entry = None

    sign_connected = False
    if visionect.is_visionect_device(device):
        svc = visionect.service()
        uuid = svc.device_uuid(device.id) if svc is not None else ""
        view = svc.sign_view(uuid) if svc is not None and uuid else None
        if view and view.get("connected"):
            asleep = view.get("asleep_until")
            sign_connected = not (isinstance(asleep, (int, float)) and asleep > now)
    if sign_connected:
        return Reach(MODE_CONNECTED, live=True)
    if (entry is not None and entry.always_on) or awake_poll_interval_s(stored) is not None:
        return Reach(MODE_CONNECTED)
    predicted = getattr(entry, "predicted_next_wake_at", None)
    if isinstance(predicted, (int, float)):
        gap = getattr(entry, "last_sleep_interval_s", None)
        return Reach(
            MODE_SLEEPS,
            next_checkin_at=float(predicted),
            gap_s=float(gap) if isinstance(gap, (int, float)) and gap > 0 else None,
            step=_aligned_step(device, entry, stored),
            cadence_stable=getattr(entry, "prediction_source", None)
            in ("configured", "firmware", "server"),
        )
    if visionect.is_visionect_device(device):
        return Reach(MODE_SLEEPS)
    return Reach(MODE_UNKNOWN)


def _aligned_step(device: Any, entry: Any, stored: Any) -> Callable[[float], float | None] | None:
    """The check-in following a given one, for a device on a wake-alignment
    grid (its gap is not constant: the next wake is the next grid point)."""
    from flask import current_app

    from app import wake_alignment
    from app.quiet_hours import resolve_quiet_hours

    alignment = wake_alignment.alignment_from_stored(stored)
    if alignment is None:
        return None
    configured = stored.get("sleep_interval_s") if isinstance(stored, dict) else None
    try:
        interval_s = int(configured) if configured is not None else 0
    except (TypeError, ValueError):
        interval_s = 0
    lead = getattr(entry, "wake_lead_ewma_s", None)
    lead_s = round(lead) if isinstance(lead, (int, float)) else 0
    settings = current_app.config.get("SETTINGS_STORE")
    quiet = resolve_quiet_hours((settings.get_section("app") if settings else None) or {}, device)
    tz = _tz()

    def step(after: float) -> float | None:
        return wake_alignment.next_aligned_wake_epoch(
            alignment, now=after, tz=tz, interval_s=interval_s, quiet=quiet, lead_s=lead_s
        )

    return step


def hold_for_device(device: Any, *, now: float | None = None) -> Hold | None:
    """Why automated pushes to ``device`` are held right now, if they are:
    quiet hours, the display's own pause, or the global automation pause.
    When several apply the one that lasts longest wins."""
    from flask import current_app

    from app.quiet_hours import is_in_window, quiet_ends_at, resolve_quiet_hours

    now = time.time() if now is None else now
    now_dt = datetime.fromtimestamp(now, UTC)
    config = current_app.config
    holds: list[Hold] = []
    settings = config.get("SETTINGS_STORE")
    window = resolve_quiet_hours((settings.get_section("app") if settings else None) or {}, device)
    if window is not None:
        tz = _tz()
        if is_in_window(window, now_dt, tz):
            ends = quiet_ends_at(window, now_dt, tz)
            holds.append(Hold(ends.timestamp() if ends is not None else None, "quiet hours"))
    scheduler = config.get("SCHEDULER")
    pause_fn = getattr(scheduler, "pause_hold_for_device", None)
    if callable(pause_fn):
        try:
            paused, until = pause_fn(device.id, now_dt)
        except Exception:
            paused, until = False, None
        if paused:
            holds.append(Hold(until.timestamp() if until is not None else None, "a pause"))
    if not holds:
        return None
    for h in holds:
        if h.until is None:
            return h
    return max(holds, key=lambda h: h.until or 0.0)


def scheduled_push_at(device: Any, *, now: float | None = None) -> float | None:
    """When the server next means to push to ``device``, from the
    scheduler's projection (lineup steps, schedules, timer advances, page
    refreshes). ``None`` when nothing is lined up in the next day."""
    from flask import current_app

    from app.quiet_hours import resolve_quiet_hours

    scheduler = current_app.config.get("SCHEDULER")
    fn = getattr(scheduler, "upcoming_for_device", None)
    if not callable(fn):
        return None
    now = time.time() if now is None else now
    settings = current_app.config.get("SETTINGS_STORE")
    window = resolve_quiet_hours((settings.get_section("app") if settings else None) or {}, device)
    try:
        events = fn(device.id, now=datetime.fromtimestamp(now, UTC), limit=1, quiet_window=window)
    except Exception:
        return None
    return events[0].scheduled_at.timestamp() if events else None


def pause_hold_at(window: Any, push_at: float, tz: tzinfo | None) -> Hold | None:
    """The pause-hours hold on a push at epoch ``push_at``: held to the end
    of the quiet hours it lands in, or ``None`` when it lands outside them.
    ``window`` is a ``quiet_hours.QuietHoursWindow``."""
    from app.quiet_hours import is_in_window, quiet_ends_at

    when = datetime.fromtimestamp(push_at, UTC)
    if not is_in_window(window, when, tz):
        return None
    ends = quiet_ends_at(window, when, tz)
    return Hold(ends.timestamp() if ends is not None else None, "quiet hours")


def _quiet_window(device: Any) -> Any:
    from flask import current_app

    from app.quiet_hours import resolve_quiet_hours

    settings = current_app.config.get("SETTINGS_STORE")
    return resolve_quiet_hours((settings.get_section("app") if settings else None) or {}, device)


class DeviceTiming:
    """One display's reach and hold, read once so a page that shows several
    lineups for it asks the stores once."""

    def __init__(self, device: Any, *, now: float | None = None) -> None:
        self.device = device
        self.now = time.time() if now is None else now
        self.tz = _tz()
        self.reach = reach_for_device(device, now=self.now)
        self.hold = hold_for_device(device, now=self.now)
        self._push: float | None = None
        self._push_read = False
        self._window = _quiet_window(device)

    def view(self, push_at: float | None | str = "scheduler") -> dict[str, Any]:
        """The ``describe`` dict for a push at ``push_at`` epoch, ``None`` for
        nothing scheduled, or (default) the scheduler's next push."""
        if push_at == "scheduler":
            if not self._push_read:
                self._push = scheduled_push_at(self.device, now=self.now)
                self._push_read = True
            push_at = self._push
        push = push_at if isinstance(push_at, (int, float)) else None
        hold = self.hold
        if hold is None and push is not None and self._window is not None:
            # A push that lands inside quiet hours that have not started yet
            # is held to their end, the same as one that lands during them.
            hold = pause_hold_at(self._window, push, self.tz)
        nu = compute_next_update(push, self.reach, now=self.now, hold=hold)
        out = describe(nu, self.reach, tz=self.tz, now=self.now)
        # ``when`` / ``detail`` / ``text`` stay 24-hour: Home Assistant's
        # sensors publish them. The pages read the ``*_display`` twins, in the
        # app's clock style.
        shown = describe(nu, self.reach, tz=self.tz, now=self.now, cycle=_app_cycle())
        for key in ("when", "detail", "text"):
            out[f"{key}_display"] = shown[key]
        return out

    def next_checkin(self) -> float | None:
        """The next time the display is expected to check in, or ``None``
        when it is reachable all the time (a live connection, an awake and
        polling frame) or nothing is known. This is what "wake" means."""
        if self.reach.mode != MODE_SLEEPS:
            return None
        return _checkin_at_or_after(self.reach, self.now)


def pickup_label(
    at: float, *, reach: Reach, tz: tzinfo | None, now: float, cycle: str | None = None
) -> str:
    """``~3:48 PM`` / ``~15:48`` (``Tue`` for another day): the pickup time a
    queued frame is told with, in the app's clock style. The same words the
    push notice (:mod:`app.push_delivery`) and the Lineups header use; ``~``
    marks a predicted check-in."""
    from app.push_delivery import _clock_cycle, _time_label

    label = _time_label(at, tz=tz, now=now, cycle=cycle or _clock_cycle())
    return ("~" if reach.mode == MODE_SLEEPS else "") + label


def pending_delivery(device: Any, *, now: float | None = None) -> dict[str, Any]:
    """Whether a newer image is queued for a pull device, and when it lands.

    A REST frame fetches its picture at its own check-in, so after a push
    the server holds a render the frame has not fetched yet. That is
    ``queued``: the newest render (the parked one while a patch divert holds
    the live slot) differs from the frame the device last fetched. Devices
    that are not REST pull devices (MQTT, push, relay) are never queued here;
    they receive a push on publish and record no fetch to compare against.

    Returns ``{queued, queued_at, expected_at, expected_when, live, mode}``:

    * ``queued_at``: epoch the queued render was made, ``None`` when not queued;
    * ``expected_at``: epoch it should reach the frame (the same estimate as
      the Next update tile: the next check-in for a sleeping frame, right away
      for a connected one, after any quiet-hours hold), ``None`` when unknown
      or held with no end; ``expected_when`` is its ``~3:48 PM`` label
      (:func:`pickup_label`);
    * ``live``: the frame holds a live connection ("delivering now");
    * ``mode``: ``connected`` / ``sleeps`` / ``unknown``.

    Reads the push manager and device timing; changes nothing.
    """
    from flask import current_app

    idle: dict[str, Any] = {
        "queued": False,
        "queued_at": None,
        "expected_at": None,
        "expected_when": "",
        "live": False,
        "mode": MODE_UNKNOWN,
    }
    push_mgr = current_app.config.get("PUSH_MANAGER")
    if push_mgr is None or getattr(device, "transport", "") != "rest":
        return idle
    live_entry = push_mgr.latest_render_for(device.id)
    held_fn = getattr(push_mgr, "held_render_for", None)
    newest = (held_fn(device.id) if callable(held_fn) else None) or live_entry
    if not newest or not newest.get("digest"):
        return idle
    served = (live_entry or {}).get("last_served_digest")
    if newest.get("digest") == served:
        return idle
    stamp = newest.get("timestamp")
    out = {**idle, "queued": True}
    out["queued_at"] = float(stamp) if isinstance(stamp, (int, float)) and stamp > 0 else None
    now = time.time() if now is None else now
    try:
        timing = DeviceTiming(device, now=now)
        view = timing.view(now)
    except Exception:
        return out
    at = view.get("at")
    out["expected_at"] = at
    if isinstance(at, (int, float)):
        out["expected_when"] = pickup_label(at, reach=timing.reach, tz=timing.tz, now=now)
    out["live"] = bool(timing.reach.live)
    out["mode"] = timing.reach.mode
    return out


def ha_state_for_device(device: Any) -> dict[str, Any]:
    """What Home Assistant's *Next update* and *Next wake* sensors publish:
    the ``view`` dict plus ``checkin_at`` (epoch of the next check-in, or
    ``None`` when the frame needs none)."""
    timing = DeviceTiming(device)
    out = timing.view()
    out["checkin_at"] = timing.next_checkin()
    return out


def view_for_device(device: Any) -> dict[str, Any] | None:
    """The next-update dict for a device, or ``None`` if it can't be built."""
    try:
        return DeviceTiming(device).view()
    except Exception:
        return None
