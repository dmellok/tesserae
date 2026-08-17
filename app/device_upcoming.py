"""Device-centric projection of the next visible panel updates (#232).

Answers one question, for one display: *what is expected to visibly change
or repaint this panel next, when, and why?*

This is not the Schedules page's timeline. That one walks a record's own
timing fields to draw a strip, and says so in its docstring: it knows
nothing about conditions, quiet hours, minimum hold, Smart Sync, Home
Return, or two Lineups pointed at the same display. A phone app showing a
countdown needs all of those, because every one of them can move or cancel
the moment the glass actually changes.

So this module replays the scheduler's own gates forward in time instead of
re-deriving them. It takes a snapshot of the runtime state the tick loop
holds (last fired, last step, last dwell window, manual overrides) and walks
the same decisions the tick would make, in the same order:

* cycle records advance on the dwell grid, gated by minimum hold exactly as
  ``find_due_rotations`` gates them, including the window-to-window base that
  makes a hold equal to the dwell stop swallowing every other window;
* interval records fire on their cooldown inside their day-of-week mask and
  time-of-day window, and resume at the window edge when a candidate falls
  outside it;
* daily records fire once per local day, with the same backfill suppression
  the tick applies;
* Home Return lands one timeout after the last interaction, deferred to the
  end of quiet hours rather than dropped, because that is what repeatedly
  retrying every tick amounts to;
* anything landing inside the display's quiet hours is left out entirely: it
  is a fire that will not repaint this panel, which is the only thing being
  described here.

Everything that survives is then coalesced per tick: several records due at
the same instant fire in one pass, and the panel shows the last frame to
land, so the timeline reports that winner rather than the intermediate
candidates the operator will never see.

What this deliberately does NOT do is predict event-driven updates. Manual
Send, webhooks, Home Assistant events, and the data-change refresh in #176
have no schedule to project, and inventing one would make ``certainty``
meaningless for the events that do. Dashboard and widget refresh timings are
left out for the same reason: the timed subset is projectable and the
event-driven subset is not, and a timeline that quietly covers half of a
mechanism is worse than one that says it does not cover it.

Pure functions over a snapshot: no stores, no clock, no locks. The caller
(``Scheduler.upcoming_for_device``) assembles the snapshot under its own
lock and this module does the arithmetic, so the projection is testable
without a running scheduler and cannot mutate what it reads.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, tzinfo

from app.quiet_hours import QuietHoursWindow, is_in_window
from app.scheduler import compute_step_window
from app.state.rotation_model import Rotation
from app.state.schedule_model import Schedule

# What a projected event says about the panel.
CAUSES = ("daily", "interval", "cycle", "home_return", "dashboard_refresh", "widget_refresh")
EFFECTS = ("change_screen", "refresh_screen")
CERTAINTIES = ("scheduled", "conditional", "estimated")

# Query bounds, advertised by the capability probe so the client doesn't
# carry its own copy.
DEFAULT_HOURS = 24
MAX_HOURS = 168
DEFAULT_LIMIT = 6
MAX_LIMIT = 20

# Walk guards. A record can't contribute more than this many candidates to
# one response, which bounds a 1-minute cycle over a 168-hour window long
# before it bounds anything a person configured on purpose.
_MAX_CANDIDATES_PER_RECORD = 200
_MAX_WALK_STEPS = 2_000
# How far ahead a dormant record's next activation is searched for, in days.
# A day-of-week mask can leave at most a 7-day gap, so 9 covers it with slack.
_ACTIVATION_SEARCH_DAYS = 9


@dataclass(frozen=True)
class UpcomingEvent:
    """One projected visible update, in the order the panel will see them."""

    scheduled_at: datetime
    cause: str
    effect: str
    certainty: str
    lineup_id: str | None
    lineup_name: str | None
    dashboard_id: str | None
    dashboard_name: str | None

    def event_id(self, device_id: str) -> str:
        """Stable identity for this event: display, record, instant.

        Re-projecting an unchanged schedule yields the same id, so the client
        can tell "the same update, re-read" from "a different update".
        """
        record = self.lineup_id or self.cause
        stamp = self.scheduled_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{device_id}:{record}:{stamp}"


@dataclass(frozen=True)
class CycleRecord:
    """A cycle (step-advance) record plus the tick state gating it."""

    rotation: Rotation
    last_step: int | None = None
    last_pushed_at: float | None = None
    last_window_start: float | None = None
    forced: tuple[datetime, int] | None = None
    # Which of this record's dashboards actually reach the display being
    # projected. ``None`` for a record with its own display binding, where
    # every step paints every bound panel. A record without one falls through
    # to dashboard bindings, so a step whose dashboard isn't on this panel
    # advances the record without repainting it, and reporting that step
    # would put an update on the timeline the glass never shows.
    device_pages: frozenset[str] | None = None


@dataclass(frozen=True)
class TimedRecord:
    """An interval / daily record plus the tick state gating it."""

    schedule: Schedule
    last_fired: float | None = None
    first_seen: float | None = None


@dataclass(frozen=True)
class HomeReturnRecord:
    """A deck whose panel is off its home card with the timeout running."""

    deck_id: str
    deck_name: str
    home_page_id: str
    idle_since: float
    timeout_minutes: int


@dataclass(frozen=True)
class ProjectionInputs:
    """Everything the projection reads, snapshotted by the caller."""

    device_id: str
    now: datetime
    through: datetime
    tz: tzinfo | None = None
    tick_seconds: int = 30
    cycles: Sequence[CycleRecord] = ()
    timed: Sequence[TimedRecord] = ()
    home_returns: Sequence[HomeReturnRecord] = ()
    page_names: Mapping[str, str] = field(default_factory=dict)
    # What the panel is showing now, when it can be established. Seeds the
    # change_screen / refresh_screen decision; ``None`` means every projected
    # update is reported as a change, which is the safe way to be unsure.
    current_page_id: str | None = None
    quiet_window: QuietHoursWindow | None = None


@dataclass(frozen=True)
class _Candidate:
    """One record's projected fire, before coalescing."""

    at: datetime
    # Tick landing order, ascending: the LAST to fire wins the panel. Mirrors
    # ``Scheduler._tick_once``, where cycle records land before timed ones at
    # equal priority, and Home Return runs after the whole fire pass.
    rank: int
    priority: int
    record_id: str
    cause: str
    certainty: str
    lineup_id: str | None
    lineup_name: str | None
    page_id: str | None


_RANK_CYCLE = 0
_RANK_TIMED = 2
_RANK_HOME_RETURN = 3


# -- time helpers --------------------------------------------------------


def _local(moment: datetime, tz: tzinfo | None) -> datetime:
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _window_contains(start_raw: str | None, end_raw: str | None, current: time) -> bool:
    """Same wrap-aware time-of-day test the scheduler applies, kept here so
    the projection can't drift from it by importing half of it."""
    if start_raw is None and end_raw is None:
        return True
    start = _parse_hhmm(start_raw) if start_raw else time(0, 0)
    end = _parse_hhmm(end_raw) if end_raw else time(23, 59, 59)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _at_local_time(moment: datetime, at: time, tz: tzinfo | None) -> datetime:
    """``moment``'s local day at wall-clock ``at``, back in UTC."""
    local = _local(moment, tz)
    return local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0).astimezone(UTC)


def _next_local_time_on_days(
    after: datetime,
    at: time,
    days_of_week: Sequence[int],
    tz: tzinfo | None,
) -> datetime | None:
    """The next instant strictly after ``after`` whose local wall clock is
    ``at`` on one of ``days_of_week``. ``None`` when the mask is empty."""
    if not days_of_week:
        return None
    local = _local(after, tz)
    for offset in range(_ACTIVATION_SEARCH_DAYS):
        day = local + timedelta(days=offset)
        if day.weekday() not in days_of_week:
            continue
        candidate = day.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if candidate > local:
            return candidate.astimezone(UTC)
    return None


# -- quiet hours ---------------------------------------------------------


def _is_quiet(inputs: ProjectionInputs, moment: datetime) -> bool:
    if inputs.quiet_window is None:
        return False
    return is_in_window(inputs.quiet_window, moment, inputs.tz)


def _quiet_ends_after(inputs: ProjectionInputs, moment: datetime) -> datetime | None:
    """When the display leaves the quiet window it is in at ``moment``.

    A minute past the window's end, because the window test is inclusive of
    its end minute. ``None`` when quiet hours aren't configured, or when the
    window is wide enough that a minute past its end is still inside it: a
    display configured to be quiet all day never leaves, and answering with
    the next midnight would put an update on the card that can't happen.
    """
    window = inputs.quiet_window
    if window is None:
        return None
    end = (datetime.combine(datetime(2000, 1, 1).date(), window.end) + timedelta(minutes=1)).time()
    resumed = _next_local_time_on_days(moment, end, tuple(range(7)), inputs.tz)
    if resumed is None or _is_quiet(inputs, resumed):
        return None
    return resumed


# -- cycle records -------------------------------------------------------


def _cycle_step_state(
    record: CycleRecord, moment: datetime, tz: tzinfo | None
) -> tuple[datetime, int] | None:
    """``(window_start, step_index)`` for the dwell window containing
    ``moment``, honouring any manual force override, or ``None`` when the
    record is dormant then. Delegates to the scheduler's own math."""
    state = compute_step_window(record.rotation, moment, tz, forced=record.forced)
    if state is None:
        return None
    return state.step_started_at.astimezone(UTC), state.step_index


def _cycle_windows(record: CycleRecord, inputs: ProjectionInputs) -> list[tuple[datetime, int]]:
    """The dwell windows this record opens between now and the horizon,
    starting with the one already in progress.

    The in-progress window is included on purpose: when the scheduler hasn't
    fired it yet (a fresh start, a re-enable, a step the minimum-hold gate
    was still holding), the very next tick will, and "about to change" is the
    single most useful thing the card can say.
    """
    rotation = record.rotation
    windows: list[tuple[datetime, int]] = []
    cursor = inputs.now
    anchor = _parse_hhmm(rotation.anchor)
    steps = 0
    while cursor <= inputs.through and len(windows) < _MAX_CANDIDATES_PER_RECORD:
        steps += 1
        if steps > _MAX_WALK_STEPS:
            break
        state = _cycle_step_state(record, cursor, inputs.tz)
        if state is None:
            # Dormant: outside the day-of-week mask, before today's anchor,
            # or past end_at. Resume at the next anchor it can wake on.
            nxt = _next_local_time_on_days(cursor, anchor, rotation.days_of_week, inputs.tz)
            if nxt is None or nxt <= cursor:
                break
            cursor = nxt
            continue
        window_start, step_index = state
        if not windows or windows[-1][0] != window_start:
            windows.append((window_start, step_index))
        dwell = rotation.steps[step_index].dwell_minutes
        nxt = window_start + timedelta(minutes=dwell)
        if nxt <= cursor:
            break  # zero-length dwell; the model forbids it, but don't spin
        cursor = nxt
    return windows


def _project_cycle(record: CycleRecord, inputs: ProjectionInputs) -> list[_Candidate]:
    """Replay ``find_due_rotations``' gates over this record's dwell grid."""
    rotation = record.rotation
    if not rotation.enabled or not rotation.steps:
        return []
    conditional = any(step.conditions for step in rotation.steps)
    if rotation.smart_sync:
        # The fire waits for a bound display to approach its predicted wake,
        # so the dwell boundary is the earliest it can land, not the moment.
        certainty = "estimated"
    elif conditional:
        certainty = "conditional"
    else:
        certainty = "scheduled"
    # In priority mode conditions choose the step outright, so which
    # dashboard lands is genuinely unknown ahead of time. In scheduled mode
    # they only skip a step, so the time-based one is the honest answer with
    # the conditional flag carrying the caveat.
    unknown_dashboard = conditional and rotation.mode == "priority"

    min_hold_s = rotation.min_hold_minutes * 60
    prev_step = record.last_step
    prev_fire_ts = record.last_pushed_at
    base_ts = record.last_window_start
    out: list[_Candidate] = []
    for window_start, step_index in _cycle_windows(record, inputs):
        fire_at = max(window_start, inputs.now)
        if fire_at > inputs.through:
            break
        if prev_step == step_index:
            # A record rotating onto itself re-fires once per new dwell
            # window (the single-step "keep this page fresh" shape) and
            # skips the hold gate: the index isn't moving, so there's no
            # condition flap to guard against.
            if prev_fire_ts is not None and prev_fire_ts >= window_start.timestamp():
                continue
        elif min_hold_s > 0 and prev_fire_ts is not None:
            hold_base = base_ts if base_ts is not None else prev_fire_ts
            if prev_fire_ts < window_start.timestamp():
                if window_start.timestamp() < hold_base + min_hold_s:
                    continue
            elif fire_at.timestamp() < prev_fire_ts + min_hold_s:
                continue
        # The fire happens either way; whether this panel sees it doesn't
        # change the record's own state, so the gates advance regardless.
        prev_step = step_index
        prev_fire_ts = fire_at.timestamp()
        base_ts = window_start.timestamp()
        step_page = rotation.steps[step_index].page_id
        if record.device_pages is not None and step_page not in record.device_pages:
            continue
        if _is_quiet(inputs, fire_at):
            continue
        page_id = None if unknown_dashboard else step_page
        out.append(
            _Candidate(
                at=fire_at,
                rank=_RANK_CYCLE,
                priority=rotation.priority,
                record_id=rotation.id,
                cause="cycle",
                certainty=certainty,
                lineup_id=rotation.id,
                lineup_name=rotation.name,
                page_id=page_id,
            )
        )
    return out


# -- timed records -------------------------------------------------------


def _timed_certainty(schedule: Schedule) -> str:
    if schedule.smart_sync:
        return "estimated"
    if schedule.conditions:
        return "conditional"
    return "scheduled"


def _timed_page(schedule: Schedule) -> str | None:
    """Which dashboard a timed fire will land on.

    Unknown when conditions can route it to a fallback: both outcomes are
    real, and naming one would be a guess the card would render as fact.
    """
    if schedule.conditions and schedule.fallback_page_id:
        return None
    return schedule.page_id


def _project_interval(record: TimedRecord, inputs: ProjectionInputs) -> list[_Candidate]:
    schedule = record.schedule
    interval = schedule.interval_minutes
    if interval is None:
        return []
    step = timedelta(minutes=interval)
    cursor = inputs.now
    if record.last_fired is not None:
        cursor = max(cursor, datetime.fromtimestamp(record.last_fired, UTC) + step)
    out: list[_Candidate] = []
    walked = 0
    while cursor <= inputs.through and len(out) < _MAX_CANDIDATES_PER_RECORD:
        walked += 1
        if walked > _MAX_WALK_STEPS:
            break
        local = _local(cursor, inputs.tz)
        in_mask = local.weekday() in schedule.days_of_week
        in_window = _window_contains(
            schedule.time_of_day_start, schedule.time_of_day_end, local.time()
        )
        if not (in_mask and in_window):
            # Outside the mask or the window, the record simply waits. The
            # cooldown has long lapsed by the time it reopens, so the first
            # tick inside fires: that edge is the next candidate.
            opens_at = _parse_hhmm(schedule.time_of_day_start or "00:00")
            nxt = _next_local_time_on_days(cursor, opens_at, schedule.days_of_week, inputs.tz)
            if nxt is None or nxt <= cursor:
                break
            cursor = nxt
            continue
        if not _is_quiet(inputs, cursor):
            out.append(
                _Candidate(
                    at=cursor,
                    rank=_RANK_TIMED,
                    priority=schedule.priority,
                    record_id=schedule.id,
                    cause="interval",
                    certainty=_timed_certainty(schedule),
                    lineup_id=schedule.id,
                    lineup_name=schedule.name,
                    page_id=_timed_page(schedule),
                )
            )
        cursor = cursor + step
    return out


def _project_daily(record: TimedRecord, inputs: ProjectionInputs) -> list[_Candidate]:
    schedule = record.schedule
    if schedule.fires_at is None:
        return []
    fires_at = schedule.fires_at.time()
    today_target = _at_local_time(inputs.now, fires_at, inputs.tz)
    local_now = _local(inputs.now, inputs.tz)
    fired_today = (
        record.last_fired is not None
        and _local(datetime.fromtimestamp(record.last_fired, UTC), inputs.tz).date()
        == local_now.date()
    )
    instants: list[datetime] = []
    if local_now.weekday() in schedule.days_of_week:
        if today_target > inputs.now:
            instants.append(today_target)
        elif (
            not fired_today
            and record.first_seen is not None
            # Today's target has passed and the record has been observed
            # since before it, so the next tick fires it. A record first seen
            # AFTER the target is suppressed for today, matching the
            # backfill guard the tick applies.
            and record.first_seen <= today_target.timestamp()
        ):
            instants.append(inputs.now)
    cursor = today_target
    while len(instants) < _MAX_CANDIDATES_PER_RECORD:
        nxt = _next_local_time_on_days(cursor, fires_at, schedule.days_of_week, inputs.tz)
        if nxt is None or nxt > inputs.through:
            break
        instants.append(nxt)
        cursor = nxt
    return [
        _Candidate(
            at=moment,
            rank=_RANK_TIMED,
            priority=schedule.priority,
            record_id=schedule.id,
            cause="daily",
            certainty=_timed_certainty(schedule),
            lineup_id=schedule.id,
            lineup_name=schedule.name,
            page_id=_timed_page(schedule),
        )
        for moment in instants
        if moment <= inputs.through and not _is_quiet(inputs, moment)
    ]


def _project_timed(record: TimedRecord, inputs: ProjectionInputs) -> list[_Candidate]:
    if not record.schedule.enabled:
        return []
    if record.schedule.type == "interval":
        return _project_interval(record, inputs)
    return _project_daily(record, inputs)


# -- home return ---------------------------------------------------------


def _project_home_return(record: HomeReturnRecord, inputs: ProjectionInputs) -> list[_Candidate]:
    """When an idle panel goes back to its deck's home card.

    Concrete under the scheduler's current state, which is what
    ``scheduled`` means here: a fresh tap restarts the timer, but so does
    any state change to any other record on this list.
    """
    due = datetime.fromtimestamp(record.idle_since + record.timeout_minutes * 60, UTC)
    at = max(due, inputs.now)
    if _is_quiet(inputs, at):
        # The return pass checks quiet hours before touching the live slot
        # and simply retries, so it lands when the window ends rather than
        # being skipped for good.
        resumed = _quiet_ends_after(inputs, at)
        if resumed is None:
            return []
        at = resumed
    if at > inputs.through:
        return []
    return [
        _Candidate(
            at=at,
            rank=_RANK_HOME_RETURN,
            priority=0,
            record_id=record.deck_id,
            cause="home_return",
            certainty="scheduled",
            lineup_id=record.deck_id,
            lineup_name=record.deck_name,
            page_id=record.home_page_id,
        )
    ]


# -- coalescing ----------------------------------------------------------


def _coalesce(candidates: list[_Candidate], tick_seconds: int) -> list[_Candidate]:
    """Reduce candidates the same tick would fire together to the one the
    panel is left showing.

    E-ink holds the most recent frame, and the tick fires its whole due list
    in ascending ``(priority, kind, id)`` order, so the winner is the maximum
    of that key. Reporting the losers would describe the scheduler's
    internals rather than the display's behaviour.
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: (c.at, c.priority, c.rank, c.record_id))
    window = timedelta(seconds=max(1, tick_seconds))
    out: list[_Candidate] = []
    bucket: list[_Candidate] = [ordered[0]]
    for candidate in ordered[1:]:
        if candidate.at - bucket[0].at < window:
            bucket.append(candidate)
            continue
        out.append(max(bucket, key=lambda c: (c.priority, c.rank, c.record_id)))
        bucket = [candidate]
    out.append(max(bucket, key=lambda c: (c.priority, c.rank, c.record_id)))
    return out


def project_upcoming(
    inputs: ProjectionInputs, *, limit: int = DEFAULT_LIMIT
) -> list[UpcomingEvent]:
    """Every visible update expected on this display between now and the
    horizon, soonest first, at most ``limit`` of them."""
    candidates: list[_Candidate] = []
    for cycle in inputs.cycles:
        candidates.extend(_project_cycle(cycle, inputs))
    for timed in inputs.timed:
        candidates.extend(_project_timed(timed, inputs))
    for home in inputs.home_returns:
        candidates.extend(_project_home_return(home, inputs))

    showing = inputs.current_page_id
    events: list[UpcomingEvent] = []
    for candidate in _coalesce(candidates, inputs.tick_seconds):
        if len(events) >= limit:
            break
        # An update onto the dashboard already on the glass repaints it; one
        # onto a different dashboard replaces it. An unknown target is
        # reported as a change: it may well be, and a countdown that promises
        # a repaint and delivers a different screen is the worse mistake.
        effect = "refresh_screen" if candidate.page_id == showing else "change_screen"
        if candidate.page_id is None:
            effect = "change_screen"
        else:
            showing = candidate.page_id
        events.append(
            UpcomingEvent(
                scheduled_at=candidate.at.astimezone(UTC).replace(microsecond=0),
                cause=candidate.cause,
                effect=effect,
                certainty=candidate.certainty,
                lineup_id=candidate.lineup_id,
                lineup_name=candidate.lineup_name,
                dashboard_id=candidate.page_id,
                dashboard_name=(
                    inputs.page_names.get(candidate.page_id)
                    if candidate.page_id is not None
                    else None
                ),
            )
        )
    return events
