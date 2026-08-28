"""Synchronized wake times: align a fleet's paints to the wall clock.

A device's ``next_poll_s`` normally counts from whenever it happened to
check in, so two panels on the same interval drift apart and repaint at
arbitrary moments. Alignment pins the wake grid to the wall clock
instead: every device configured to the same grid wakes (and therefore
paints) together, with no cross-device coordination — the clock is the
coordinator.

Two modes, stored per device in ``settings.devices.<id>``:

* ``interval`` — keep ``sleep_interval_s`` as the period, but land each
  wake on ``anchor + k * interval`` in local time (anchor ``HH:MM``,
  default midnight). A 15-minute panel anchored at ``00:05`` wakes at
  :05 / :20 / :35 / :50.
* ``times`` — wake only at an explicit list of ``HH:MM`` times. The
  sleep interval stops mattering; the device sleeps until the next
  listed time.

The math is all server-side: the REST ``/status`` response's
``next_poll_s`` becomes "seconds until the next grid point", recomputed
on every check-in so error never accumulates. ``lead_s`` shifts the wake
earlier by the device's measured wake-to-checkin latency (Wi-Fi connect
plus fetch plus the panel refresh) so the *paint* lands on the grid, not
just the radio.

Grid points inside the device's quiet-hours window are skipped: an
aligned wake there would spin the radio up for a frame that automation
was told not to change.

Pure module — no Flask, no stores — so the grid math is unit-testable
with a plain tz + clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from app.quiet_hours import QuietHoursWindow, is_in_window

MODE_OFF = "off"
MODE_INTERVAL = "interval"
MODE_TIMES = "times"
MODES = (MODE_OFF, MODE_INTERVAL, MODE_TIMES)

# Never schedule an aligned wake closer than this. The firmware clamps
# sleeps below 30 s anyway (SLEEP_INTERVAL_MIN_S), and a device that
# checked in just before a grid point shouldn't be told to come straight
# back for it — it skips to the next one.
MIN_DELTA_S = 30

# Ceiling on the lead compensation. The lead is a measured EWMA of
# wake-to-checkin latency; anything above this is a network fault, not a
# panel refresh, and pulling the whole fleet minutes early over it would
# be worse than painting late once.
LEAD_MAX_S = 120

# How far ahead to search for a grid point before giving up (quiet hours
# can swallow points; a misconfigured window shouldn't hang the search
# or strand the device). Callers fall back to the plain interval.
HORIZON_S = 48 * 3600

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_hhmm(value: object) -> tuple[int, int] | None:
    """``'HH:MM'`` → ``(hour, minute)``, or ``None`` when malformed."""
    if not isinstance(value, str):
        return None
    m = _HHMM_RE.match(value.strip())
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_times_list(raw: object) -> list[str]:
    """Normalise a stored or submitted times value to a sorted, deduped
    list of ``HH:MM`` strings.

    Accepts a list of strings or one comma/space/newline-separated
    string (the form ships a single text input). Malformed entries are
    dropped rather than rejected so one typo doesn't invalidate the
    rest; the save handler checks the *result* is non-empty when the
    mode needs it."""
    if isinstance(raw, str):
        parts = [p for p in re.split(r"[,\s]+", raw) if p]
    elif isinstance(raw, list):
        parts = [p for p in raw if isinstance(p, str)]
    else:
        return []
    out: set[str] = set()
    for part in parts:
        hm = parse_hhmm(part)
        if hm is not None:
            out.add(f"{hm[0]:02d}:{hm[1]:02d}")
    return sorted(out)


@dataclass(frozen=True)
class WakeAlignment:
    """A device's resolved alignment config. ``times`` is only
    meaningful in times mode; ``anchor`` only in interval mode."""

    mode: str
    anchor: str = "00:00"
    times: tuple[str, ...] = ()


def alignment_from_stored(stored: object) -> WakeAlignment | None:
    """Read the alignment block off a ``settings.devices.<id>`` dict.

    Returns ``None`` when alignment is off, absent, or unusable — the
    caller keeps today's relative-interval behaviour, so a hand-edited
    settings file can never strand a device."""
    if not isinstance(stored, dict):
        return None
    mode = str(stored.get("wake_align_mode") or MODE_OFF).strip().lower()
    if mode == MODE_INTERVAL:
        anchor_raw = str(stored.get("wake_align_anchor") or "00:00")
        hm = parse_hhmm(anchor_raw)
        anchor = f"{hm[0]:02d}:{hm[1]:02d}" if hm is not None else "00:00"
        return WakeAlignment(mode=MODE_INTERVAL, anchor=anchor)
    if mode == MODE_TIMES:
        times = parse_times_list(stored.get("wake_align_times"))
        if not times:
            return None
        return WakeAlignment(mode=MODE_TIMES, times=tuple(times))
    return None


def _local_day_time(day: datetime, hour: int, minute: int, tz: tzinfo) -> datetime:
    """``day``'s date at ``HH:MM`` local. Built through ``replace`` on a
    tz-aware local datetime so DST resolution follows zoneinfo's fold
    rules rather than naive arithmetic."""
    return day.astimezone(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)


def next_aligned_wake_epoch(
    alignment: WakeAlignment,
    *,
    now: float,
    tz: tzinfo,
    interval_s: int,
    quiet: QuietHoursWindow | None = None,
    lead_s: int = 0,
) -> float | None:
    """Epoch seconds the device should next *wake* at, or ``None`` when
    no grid point exists inside the search horizon.

    The returned instant is the grid point minus ``lead_s`` (clamped to
    ``LEAD_MAX_S``), so the paint — not the radio — lands on the grid.
    Grid points whose wake would arrive sooner than ``MIN_DELTA_S`` from
    now are skipped forward, as are points inside the quiet window."""
    lead = max(0, min(int(lead_s), LEAD_MAX_S))
    now_dt = datetime.fromtimestamp(now, tz)

    def usable(grid: datetime) -> bool:
        if grid.timestamp() - lead < now + MIN_DELTA_S:
            return False
        return quiet is None or not is_in_window(quiet, grid, tz)

    if alignment.mode == MODE_INTERVAL:
        if interval_s <= 0:
            return None
        hm = parse_hhmm(alignment.anchor) or (0, 0)
        anchor = _local_day_time(now_dt, hm[0], hm[1], tz)
        if anchor > now_dt:
            anchor -= timedelta(days=1)
        elapsed = now - anchor.timestamp()
        k = int(elapsed // interval_s) + 1
        grid = datetime.fromtimestamp(anchor.timestamp() + k * interval_s, tz)
        while grid.timestamp() - now <= HORIZON_S:
            if usable(grid):
                return grid.timestamp() - lead
            grid = datetime.fromtimestamp(grid.timestamp() + interval_s, tz)
        return None

    if alignment.mode == MODE_TIMES:
        parsed = [hm for hm in (parse_hhmm(t) for t in alignment.times) if hm is not None]
        if not parsed:
            return None
        for day_offset in range(3):  # today, tomorrow, day after (quiet skips)
            day = now_dt + timedelta(days=day_offset)
            for hour, minute in sorted(parsed):
                grid = _local_day_time(day, hour, minute, tz)
                if usable(grid):
                    return grid.timestamp() - lead
        return None

    return None
