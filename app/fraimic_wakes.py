"""Wake planning for Fraimic canvases: which schedule to write to the frame.

The frame's own wake schedule takes an interval of an hour or more counted
from its midnight, or up to four daily ``HH:MM`` times, in its own clock.
Anything else (a shorter interval, more times, wakes that skip quiet hours,
a wake aimed at the next scheduled change) is done with a **chain**: at
each wake Tesserae writes the next four wake times, so the frame is led
along one wake at a time. The first of the four is the wake Tesserae
wants; the other three are the ones after it, so a wake Tesserae misses
(server down, network out) is followed by another instead of silence.

Chain facts, measured on a 31.5" canvas (firmware 0.2.31):

* a POST to ``/api/wake-schedule`` re-arms the frame at once;
* a time three to four minutes out is taken and fires 1-4 s late;
* the frame has to have been up for a while before it is put back to
  sleep, or the next alarm is never armed (``app.fraimic.MIN_AWAKE_S``).

The frame's clock is its own: it keeps a fixed UTC offset that need not be
the app's timezone (no daylight saving), so times are converted with the
offset the frame reports.

Pure module: no Flask, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Any

from app import wake_alignment
from app.quiet_hours import QuietHoursWindow, is_in_window
from app.wake_alignment import MIN_DELTA_S, WakeAlignment

ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WAKE_WINDOW_S = 120  # max_idle Tesserae asks for (Fraimic's default)

MODE_INTERVAL = "interval"
MODE_TIMES = "times"
MODE_CHANGES = "changes"
MODE_MANUAL = "manual"
WAKE_MODES = (MODE_INTERVAL, MODE_TIMES, MODE_CHANGES, MODE_MANUAL)

# What the frame's own schedule can express.
NATIVE_INTERVALS = (60, 90, 120, 180, 240, 360, 480, 720, 1440)
NATIVE_MAX_TIMES = 4
# What a chain adds below an hour.
CHAIN_INTERVALS = (5, 10, 15, 20, 30)
INTERVALS = CHAIN_INTERVALS + NATIVE_INTERVALS

CHAIN_SLOTS = 4
# The soonest wake a chain asks for: the shortest that was measured to
# fire (a time written three and a half minutes ahead, give or take).
MIN_FIRST_GAP_S = 180
MIN_SLOT_GAP_S = 180
# A page that declares its own wake interval is honoured down to this: the
# operator didn't pick it for a battery frame whose every picture is a
# full-panel redraw.
PAGE_MIN_INTERVAL_S = 900
DAY_S = 86400
# Times repeat daily on the frame, so a slot a day or more out would fire
# early, today. Slots this close to a full day ahead are dropped.
DAY_GUARD_S = 300
DEFAULT_CEILING_MINUTES = 360

# (battery percent below, factor on the gap between wakes)
LOW_BATTERY_STEPS = ((10, 4), (20, 2))


@dataclass(frozen=True)
class WakePlan:
    """``body`` is the ``/api/wake-schedule`` request to make the frame's
    schedule equal, or None for "leave what the frame has". ``chained``
    plans change from wake to wake; ``wakes`` are the instants a chain
    asks for. ``needs_clock`` means the plan can't be made until the
    frame's UTC offset is known."""

    body: dict[str, Any] | None
    chained: bool = False
    wakes: tuple[float, ...] = ()
    needs_clock: bool = False


def battery_stretch(percent: Any, enabled: bool) -> int:
    """How many times longer the gap between wakes should be on this charge."""
    if not enabled or isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return 1
    for below, factor in LOW_BATTERY_STEPS:
        if percent < below:
            return factor
    return 1


def frame_hhmm(epoch: float, offset_s: int) -> str:
    """``epoch`` on the frame's clock, as ``HH:MM``."""
    seconds = int(epoch + offset_s) % DAY_S
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def to_frame_times(times: Iterable[str], *, now: float, tz: tzinfo, offset_s: int) -> list[str]:
    """``HH:MM`` times in the app's timezone as the frame's own clock reads
    them today (sorted, de-duplicated)."""
    today = datetime.fromtimestamp(now, tz)
    out: set[str] = set()
    for raw in times:
        hm = wake_alignment.parse_hhmm(raw)
        if hm is None:
            continue
        local = today.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        out.add(frame_hhmm(local.timestamp(), offset_s))
    return sorted(out)


def on_schedule_clock(
    schedule: dict[str, Any], at: float, offset_s: int | None, *, early_s: int = 20
) -> bool:
    """Whether ``at`` is a moment the frame's schedule wakes it: within
    ``early_s`` before or a wake window after one of its times, or a point
    on its interval grid. Used to tell a scheduled wake from a tap when the
    wake isn't one Tesserae had on record (a chain slot that came round
    again a day later)."""
    if offset_s is None or not schedule.get("enabled"):
        return False
    second = int(at + offset_s) % DAY_S
    for raw in schedule.get("times") or []:
        hm = wake_alignment.parse_hhmm(raw)
        if hm is None:
            continue
        delta = (second - (hm[0] * 3600 + hm[1] * 60)) % DAY_S
        if delta <= WAKE_WINDOW_S or delta >= DAY_S - early_s:
            return True
    interval = schedule.get("interval_minutes")
    if isinstance(interval, int) and not isinstance(interval, bool) and interval > 0:
        return (second + early_s) % (interval * 60) <= WAKE_WINDOW_S + early_s
    return False


def _base(body: dict[str, Any]) -> dict[str, Any]:
    return {"enabled": True, "weekdays": list(ALL_DAYS), "max_idle": WAKE_WINDOW_S, **body}


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _grid(
    alignment: WakeAlignment,
    *,
    now: float,
    tz: tzinfo,
    interval_s: int,
    quiet: QuietHoursWindow | None,
    count: int,
) -> list[float]:
    """The next ``count`` points of ``alignment``'s grid, the first at least
    ``MIN_FIRST_GAP_S`` out, skipping quiet hours."""
    out: list[float] = []
    cursor = now + MIN_FIRST_GAP_S - MIN_DELTA_S
    for _ in range(count):
        point = wake_alignment.next_aligned_wake_epoch(
            alignment, now=cursor, tz=tz, interval_s=interval_s, quiet=quiet
        )
        if point is None:
            break
        out.append(point)
        cursor = point
    return out


def _chain(wakes: list[float], *, now: float, offset_s: int) -> WakePlan:
    """A chain for ``wakes`` (sorted): at most four, far enough apart, and
    none so far out that its daily repeat would fire first."""
    kept: list[float] = []
    for wake in wakes:
        if kept and wake - kept[-1] < MIN_SLOT_GAP_S:
            continue
        if kept and wake - now > DAY_S - DAY_GUARD_S:
            break
        kept.append(wake)
        if len(kept) == CHAIN_SLOTS:
            break
    if not kept:
        return WakePlan(None, chained=True)
    times = sorted({frame_hhmm(w, offset_s) for w in kept})
    return WakePlan(_base({"times": times}), chained=True, wakes=tuple(kept))


def plan_wakes(
    cfg: dict[str, Any],
    *,
    now: float,
    tz: tzinfo,
    offset_s: int | None,
    quiet: QuietHoursWindow | None = None,
    battery_pct: Any = None,
    changes: Iterable[float] = (),
    page_interval_s: int | None = None,
) -> WakePlan:
    """The schedule the device's settings ask for right now.

    ``offset_s`` is the frame's UTC offset, None until it has been seen.
    ``changes`` are the instants the scheduler next means to push to the
    frame (used by the "changes" mode). ``page_interval_s`` is the wake
    interval the page on the glass declares, if it declares one.
    """
    mode = str(cfg.get("wake_mode") or MODE_INTERVAL)
    if mode == MODE_MANUAL:
        return WakePlan(None)
    stretch = battery_stretch(battery_pct, cfg.get("low_battery_stretch", True) is not False)
    app_offset = datetime.fromtimestamp(now, tz).utcoffset()
    app_offset_s = int(app_offset.total_seconds()) if app_offset is not None else 0
    skew_s = 0 if offset_s is None else offset_s - app_offset_s

    if mode == MODE_TIMES:
        times = wake_alignment.parse_times_list(cfg.get("wake_times"))
        if not times:
            return WakePlan(None)
        if quiet is None and len(times) <= NATIVE_MAX_TIMES:
            if offset_s is None:
                return WakePlan(None, needs_clock=True)
            return WakePlan(
                _base({"times": to_frame_times(times, now=now, tz=tz, offset_s=offset_s)})
            )
        if offset_s is None:
            return WakePlan(None, chained=True, needs_clock=True)
        alignment = WakeAlignment(mode=wake_alignment.MODE_TIMES, times=tuple(times))
        wakes = _grid(alignment, now=now, tz=tz, interval_s=0, quiet=quiet, count=CHAIN_SLOTS)
        return _chain(wakes, now=now, offset_s=offset_s)

    if mode == MODE_CHANGES:
        if offset_s is None:
            return WakePlan(None, chained=True, needs_clock=True)
        ceiling_s = _int(cfg.get("wake_ceiling_minutes"), DEFAULT_CEILING_MINUTES) * 60 * stretch
        ceiling = WakeAlignment(mode=wake_alignment.MODE_INTERVAL, anchor="00:00")
        wakes = _grid(
            ceiling, now=now, tz=tz, interval_s=max(ceiling_s, 300), quiet=quiet, count=CHAIN_SLOTS
        )
        for change in changes:
            if change - now < MIN_FIRST_GAP_S or change - now > DAY_S - DAY_GUARD_S:
                continue
            # The frame's times are whole minutes: wake in the minute the
            # change falls in.
            minute = float(int(change) // 60 * 60)
            if minute - now < MIN_FIRST_GAP_S:
                continue
            if quiet is not None and is_in_window(quiet, datetime.fromtimestamp(minute, tz), tz):
                continue
            wakes.append(minute)
        return _chain(sorted(set(wakes)), now=now, offset_s=offset_s)

    minutes = _int(cfg.get("wake_interval_minutes"), 60)
    if minutes not in INTERVALS:
        minutes = 60
    anchor_hm = wake_alignment.parse_hhmm(str(cfg.get("wake_anchor") or "00:00")) or (0, 0)
    anchor = f"{anchor_hm[0]:02d}:{anchor_hm[1]:02d}"
    interval_s = minutes * 60
    if page_interval_s is not None and page_interval_s > 0:
        interval_s = max(PAGE_MIN_INTERVAL_S, round(page_interval_s / 60) * 60)
    native = (
        quiet is None
        and stretch == 1
        and interval_s == minutes * 60
        and minutes in NATIVE_INTERVALS
        and anchor == "00:00"
        # The frame counts from its own midnight: the same grid as the
        # app's only when the two clocks differ by a whole interval.
        and skew_s % interval_s == 0
    )
    if native:
        return WakePlan(_base({"interval_minutes": minutes}))
    if offset_s is None:
        return WakePlan(None, chained=True, needs_clock=True)
    alignment = WakeAlignment(mode=wake_alignment.MODE_INTERVAL, anchor=anchor)
    wakes = _grid(
        alignment, now=now, tz=tz, interval_s=interval_s * stretch, quiet=quiet, count=CHAIN_SLOTS
    )
    return _chain(wakes, now=now, offset_s=offset_s)
