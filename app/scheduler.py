"""Background scheduler, fires schedules whose time has come.

Runs as a daemon thread (default tick 30 s). On each tick:

1. Reads enabled schedules from the store
2. Filters by type-specific rules:
   * **interval**, respects the day-of-week mask AND the time-of-day window
     AND the per-schedule cooldown (last_fired + interval_minutes)
   * **daily**  , respects the day-of-week mask, fires once per local day
     once the wall-clock time passes ``fires_at``. The first_seen guard
     suppresses backfill: enabling a 07:00 daily schedule at 11:00 doesn't
     fire today's missed 07:00, the next fire is tomorrow at 07:00.
3. Sorts by ``priority`` (high first) and hands each due schedule to the
   PushManager.

last_fired + first_seen live in memory only. A restart resets both, which
means a freshly-restarted Tesserae may fire an interval schedule "early"
once and may skip a daily schedule whose target already passed today.
Persisting them is the obvious upgrade if either becomes a problem.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime, time, tzinfo
from typing import Any

from app.push import PushManager, PushResult
from app.state.event_log import EventLog
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore

logger = logging.getLogger(__name__)


# Returns the active tz or None for host-local. Resolved on every tick so
# a settings change applies without restarting the scheduler thread.
TimezoneProvider = Callable[[], tzinfo | None]


def _local(now: datetime, tz: tzinfo | None) -> datetime:
    """Convert a UTC-aware datetime to the configured tz, falling back to
    the host's local clock when no tz is set."""
    if tz is None:
        return now.astimezone()
    return now.astimezone(tz)


def _matches_dow(schedule: Schedule, now: datetime, tz: tzinfo | None) -> bool:
    return _local(now, tz).weekday() in schedule.days_of_week


def _matches_window(schedule: Schedule, now: datetime, tz: tzinfo | None) -> bool:
    if schedule.time_of_day_start is None and schedule.time_of_day_end is None:
        return True
    current = _local(now, tz).time()
    start = _parse_hhmm(schedule.time_of_day_start) if schedule.time_of_day_start else time(0, 0)
    end = _parse_hhmm(schedule.time_of_day_end) if schedule.time_of_day_end else time(23, 59, 59)
    if start <= end:
        return start <= current <= end
    # Wrap-around window (e.g. 22:00 -> 06:00).
    return current >= start or current <= end


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def compute_current_step(
    rotation: Rotation, now: datetime, tz: tzinfo | None
) -> tuple[int, RotationStep] | None:
    """Return the ``(step_index, step)`` whose dwell window contains
    ``now`` (in the rotation's anchor timezone), or ``None`` if the
    rotation isn't active right now (day-of-week mask excludes today,
    or wall-clock is before today's anchor).

    The cycle re-anchors at the configured ``anchor`` HH:MM each local
    day, so DST flips don't desync long rotations: at 00:00 (or
    whatever the anchor is), step 0 starts fresh.

    Math: minutes since today's anchor, modulo total cycle length, then
    walk the steps in order summing dwells until we find the bucket
    that contains the modulo result.
    """
    local_now = _local(now, tz)
    if local_now.weekday() not in rotation.days_of_week:
        return None
    anchor_t = _parse_hhmm(rotation.anchor)
    anchor_today = local_now.replace(
        hour=anchor_t.hour, minute=anchor_t.minute, second=0, microsecond=0
    )
    if local_now < anchor_today:
        return None
    minutes_since_anchor = (local_now - anchor_today).total_seconds() / 60.0
    cycle = rotation.cycle_minutes
    if cycle <= 0:
        return None
    position = minutes_since_anchor % cycle
    cumulative = 0.0
    for idx, step in enumerate(rotation.steps):
        cumulative += step.dwell_minutes
        if position < cumulative:
            return idx, step
    # Defensive fallback: float arithmetic edge case can leave position
    # equal to cycle; treat that as the last step (we just rolled over).
    return len(rotation.steps) - 1, rotation.steps[-1]


class Scheduler:
    def __init__(
        self,
        *,
        store: ScheduleStore,
        push_manager: Callable[[], PushManager],
        event_log: EventLog | None = None,
        timezone_provider: TimezoneProvider | None = None,
        page_exists: Callable[[str], bool] | None = None,
        # Smart sync (issue #10) dependencies. Optional so existing
        # tests + bare construction keep working; production wires both.
        device_ids_for_page: Callable[[str], list[str]] | None = None,
        device_telemetry: Any = None,
        # Rotations (issue: dashboard rotation). Optional; None means
        # no rotation evaluation runs each tick. Production wires it.
        rotation_store: RotationStore | None = None,
        tick_seconds: int = 30,
    ) -> None:
        """``push_manager`` is a zero-arg factory that resolves the
        currently-installed PushManager. We can't hold the instance because
        broker setting changes rebuild it, see app.main._rebuild_transport.
        Tests pass ``lambda: my_pm`` for a fixed instance.

        ``event_log`` is optional so unit tests can construct a Scheduler
        without a SQLite file. In production it's always wired.

        ``timezone_provider`` is a zero-arg callable resolving the active
        timezone (or None for host-local). Called on every tick so a
        settings change applies without restarting the scheduler thread.

        ``page_exists`` is a zero-arg-but-takes-page_id callable used to
        skip schedules whose target dashboard was deleted, so the History
        view doesn't fill up with 0.00s "page not found" rows once a
        minute. When ``None`` the scheduler is permissive (every schedule
        is dispatched and PushManager logs the miss itself), that matches
        existing tests, which don't care about staleness."""
        self._store = store
        self._rotation_store = rotation_store
        self._push_factory = push_manager
        self._event_log = event_log
        self._tz_provider = timezone_provider or (lambda: None)
        self._page_exists = page_exists
        self._device_ids_for_page = device_ids_for_page
        self._device_telemetry = device_telemetry
        self._tick = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # schedule_id -> last fired POSIX timestamp
        self._last_fired: dict[str, float] = {}
        # schedule_id -> first observed POSIX timestamp. Cleared when a
        # schedule gets disabled or removed, so re-enable starts a fresh
        # window. Used to suppress daily backfill (see find_due).
        self._first_seen: dict[str, float] = {}
        # schedule_id we've already warned about for a missing target page,
        # so a stale schedule doesn't spam the log on every tick. Cleared
        # implicitly: re-enabling or re-binding the schedule re-warns
        # because the new page check will pass (so we never re-add).
        self._warned_missing_page: set[str] = set()
        # rotation_id -> last fired step index. We push only when the
        # current step's index differs from this one (avoids re-pushing
        # the same page every tick). Cleared on disable so re-enable
        # fires the current step fresh.
        self._rotation_last_step: dict[str, int] = {}
        # rotation_id we've warned about for a missing step page; same
        # one-shot semantics as ``_warned_missing_page``.
        self._warned_missing_rotation_page: set[str] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        thread = threading.Thread(target=self._run, name="tesserae-scheduler", daemon=True)
        thread.start()
        self._thread = thread

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_once(datetime.now(UTC))
            except Exception:
                logger.exception("scheduler tick crashed")
            self._stop.wait(self._tick)

    def find_due(self, now: datetime | None = None) -> list[Schedule]:
        """Return schedules that should fire at ``now``, sorted by priority
        descending then id. Records each enabled schedule's first-observed
        timestamp so daily backfills are suppressed (see ``_observe``)."""
        now = now or datetime.now(UTC)
        tz = self._tz_provider()
        self._observe(now)
        candidates: list[Schedule] = []
        for s in self._store.all():
            if not s.enabled:
                continue
            # Skip schedules pointing at deleted pages. The PushManager
            # would still log a "page not found" event if we let it run,
            # which clogs the History view (and surprises the user days
            # later). Warn once per session per schedule so the operator
            # sees something actionable in the log.
            if self._page_exists is not None and not self._page_exists(s.page_id):
                with self._lock:
                    if s.id not in self._warned_missing_page:
                        self._warned_missing_page.add(s.id)
                        logger.warning(
                            "schedule %r (%s) targets missing page %r, skipping "
                            "until it's rebound or deleted",
                            s.name,
                            s.id,
                            s.page_id,
                        )
                continue
            if s.type == "interval":
                if not _matches_dow(s, now, tz):
                    continue
                if not _matches_window(s, now, tz):
                    continue
                if s.interval_minutes is None:
                    continue
                with self._lock:
                    last = self._last_fired.get(s.id)
                # Interval acts as a floor regardless of smart-sync; it's
                # the user-set "don't push the panel more often than this"
                # ceiling on the render rate.
                interval_passed = (
                    last is None or (now.timestamp() - last) >= s.interval_minutes * 60
                )
                if not interval_passed:
                    continue
                # Default path: fire on the interval cadence.
                # Smart-sync (issue #10): if the schedule opts in AND at
                # least one bound device is trusted, only fire when that
                # device is within ``smart_sync_lead_s`` of its predicted
                # wake. When no bound device is trusted (warm-up window)
                # or the schedule has no device bindings, fall back to
                # interval firing so the page still pushes on time.
                if s.smart_sync and self._smart_sync_should_wait(s, now):
                    continue
                candidates.append(s)
            elif s.type == "daily":
                if s.fires_at is None:
                    continue
                if not _matches_dow(s, now, tz):
                    continue
                # Target time is today (wall-clock in the configured tz) at
                # fires_at's HH:MM.
                local_now = _local(now, tz)
                target_local = local_now.replace(
                    hour=s.fires_at.hour,
                    minute=s.fires_at.minute,
                    second=0,
                    microsecond=0,
                )
                target = target_local.astimezone(UTC)
                if now < target:
                    continue
                with self._lock:
                    first_seen = self._first_seen.get(s.id)
                    last = self._last_fired.get(s.id)
                # Backfill guard: if we weren't watching the schedule at
                # the moment today's target would have fired, skip, the
                # user enabled it after the time had passed.
                if first_seen is None or first_seen > target.timestamp():
                    continue
                if last is not None:
                    last_dt = datetime.fromtimestamp(last, tz=UTC)
                    if _local(last_dt, tz).date() == local_now.date():
                        continue  # already fired today (local terms)
                candidates.append(s)
        candidates.sort(key=lambda s: (-s.priority, s.id))
        return candidates

    def _tick_once(self, now: datetime) -> None:
        self._observe(now)
        # Rotations fire FIRST so any same-tick schedule pushes land
        # last. Reason: eink panels show the most recent frame. If a
        # user has set up a daily schedule to override the rotation at
        # 09:00 (priority knob), the schedule's frame ends up on the
        # panel because rotation already fired before it.
        for rotation, step_index in self.find_due_rotations(now):
            self._fire_rotation(rotation, step_index, now, respect_quiet_hours=True)
        for schedule in self.find_due(now):
            self._fire(schedule, now, respect_quiet_hours=True)

    def find_due_rotations(self, now: datetime | None = None) -> list[tuple[Rotation, int]]:
        """Return ``(rotation, step_index)`` pairs whose current step
        DIFFERS from the last fired step (or has never fired). Each
        such pair is a step-transition that should push the new page.

        Sorted by ``priority`` descending then id, mirroring
        ``find_due``. Stale rotations (target page deleted) are skipped
        with a one-shot warning so the log doesn't fill up."""
        if self._rotation_store is None:
            return []
        now = now or datetime.now(UTC)
        tz = self._tz_provider()
        out: list[tuple[Rotation, int]] = []
        for rotation in self._rotation_store.all():
            if not rotation.enabled:
                # Clear last-step so re-enable fires the current step
                # rather than waiting for the next transition.
                with self._lock:
                    self._rotation_last_step.pop(rotation.id, None)
                continue
            picked = compute_current_step(rotation, now, tz)
            if picked is None:
                continue
            step_index, step = picked
            if self._page_exists is not None and not self._page_exists(step.page_id):
                with self._lock:
                    if rotation.id not in self._warned_missing_rotation_page:
                        self._warned_missing_rotation_page.add(rotation.id)
                        logger.warning(
                            "rotation %r (%s) step %d targets missing page %r, skipping",
                            rotation.name,
                            rotation.id,
                            step_index,
                            step.page_id,
                        )
                continue
            with self._lock:
                last_step = self._rotation_last_step.get(rotation.id)
            if last_step == step_index:
                continue
            out.append((rotation, step_index))
        out.sort(key=lambda pair: (-pair[0].priority, pair[0].id))
        return out

    def _fire_rotation(
        self,
        rotation: Rotation,
        step_index: int,
        now: datetime,
        *,
        respect_quiet_hours: bool = False,
    ) -> PushResult:
        step = rotation.steps[step_index]
        logger.info(
            "Firing rotation %s step %d -> page %s",
            rotation.id,
            step_index,
            step.page_id,
        )
        result = self._push_factory().push(
            step.page_id,
            respect_quiet_hours=respect_quiet_hours,
            source="rotation",
        )
        # Same status semantics as _fire: a quiet result still counts
        # as "we tried" so the next tick doesn't re-attempt within the
        # quiet window. Failures don't bump so the next tick retries.
        if result.status in ("sent", "quiet"):
            with self._lock:
                self._rotation_last_step[rotation.id] = step_index
        if self._event_log is not None:
            self._event_log.record(
                type="rotation",
                source="rotation",
                target=rotation.id,
                status=result.status,
                error=result.error,
                duration_s=result.duration_s,
                extra={
                    "rotation_name": rotation.name,
                    "step_index": step_index,
                    "page_id": step.page_id,
                    "push_event_id": result.event_id,
                },
            )
        return result

    def _smart_sync_should_wait(self, schedule: Schedule, now: datetime) -> bool:
        """Return True when smart-sync wants to *hold* this schedule
        (don't fire yet), False when it should fire now.

        Hold conditions:
          - Schedule has device bindings AND at least one is trusted
            AND none of the trusted devices are within the lead window
            of their next predicted wake. The page has a fresh frame
            ready but the panel isn't about to wake; waiting saves a
            stale frame from sitting in the broker until the next
            actual wake.

        Fire conditions (return False, let the interval cadence win):
          - No telemetry dependencies wired (test path / bare run).
          - The schedule's page has no bound devices.
          - No bound device is trusted yet (warm-up).
          - At least one trusted device is inside the lead window.
        """
        if self._device_telemetry is None or self._device_ids_for_page is None:
            return False
        device_ids = self._device_ids_for_page(schedule.page_id)
        if not device_ids:
            return False
        trusted_predictions: list[float] = []
        for device_id in device_ids:
            entry = self._device_telemetry.get(device_id)
            if entry is None or not entry.is_trusted:
                continue
            if entry.predicted_next_wake_at is None:
                continue
            trusted_predictions.append(entry.predicted_next_wake_at)
        if not trusted_predictions:
            # No trusted bindings: smart-sync hasn't warmed up. Fall
            # through to interval firing so the page still pushes on
            # the user's configured cadence.
            return False
        # Fire when the soonest predicted wake is within the lead
        # window of right now (we want the frame waiting before the
        # panel asks for it). Devices that have already woken (offset
        # past prediction) won't satisfy this and the schedule waits
        # for the next prediction.
        now_ts = now.timestamp()
        soonest = min(trusted_predictions)
        lead_window_starts_at = soonest - schedule.smart_sync_lead_s
        return now_ts < lead_window_starts_at

    def _observe(self, now: datetime) -> None:
        """Maintain ``_first_seen``. Drop entries for ids no longer enabled
        so a disable+re-enable resets the window, exactly what the user
        expects when they fix a typo and re-toggle a schedule."""
        enabled_ids = {s.id for s in self._store.all() if s.enabled}
        with self._lock:
            for sid in list(self._first_seen):
                if sid not in enabled_ids:
                    del self._first_seen[sid]
            for sid in enabled_ids:
                self._first_seen.setdefault(sid, now.timestamp())

    def _fire(
        self,
        schedule: Schedule,
        now: datetime,
        *,
        respect_quiet_hours: bool = False,
    ) -> PushResult:
        logger.info("Firing schedule %s -> page %s", schedule.id, schedule.page_id)
        # Tick-driven firings (the background loop) are automation -
        # they should respect quiet hours so a 22:30 schedule doesn't
        # wake the room. fire_now() is the manual "Fire" button on the
        # Schedules page; user intent always goes through, so it leaves
        # respect_quiet_hours off.
        result = self._push_factory().push(
            schedule.page_id,
            respect_quiet_hours=respect_quiet_hours,
            source="scheduler",
        )
        # Successful fires bump last_fired so the daily / interval gates
        # work; a failed push doesn't update it (the next tick can retry).
        # A ``quiet`` result also bumps it, every device was in quiet
        # hours, so the user's "no pushes overnight" intent is being
        # honoured. Treating it like a sent fire stops us from re-
        # attempting (and re-logging) on every tick through the quiet
        # window. The interval / daily gate then naturally re-arms for
        # the next slot.
        if result.status in ("sent", "quiet"):
            with self._lock:
                self._last_fired[schedule.id] = now.timestamp()
        if self._event_log is not None:
            # The scheduler row links to the push event it caused, so /events
            # can click through "this schedule fired -> this push happened".
            self._event_log.record(
                type="scheduler",
                source="scheduler",
                target=schedule.id,
                status=result.status,
                error=result.error,
                duration_s=result.duration_s,
                extra={
                    "schedule_name": schedule.name,
                    "page_id": schedule.page_id,
                    "push_event_id": result.event_id,
                },
            )
        return result

    # -- helpers for tests / manual fire ---------------------------------

    def run_due_once(self, now: datetime | None = None) -> list[tuple[Schedule, PushResult]]:
        """Synchronous one-pass fire-due, used by tests and manual triggers."""
        out: list[tuple[Schedule, PushResult]] = []
        when = now or datetime.now(UTC)
        self._observe(when)
        for s in self.find_due(when):
            out.append((s, self._fire(s, when)))
        return out

    def fire_now(self, schedule_id: str) -> PushResult | None:
        """Manual trigger: skip every gate, fire the schedule immediately."""
        s = self._store.get(schedule_id)
        if s is None:
            return None
        return self._fire(s, datetime.now(UTC))

    def status(self) -> dict[str, dict[str, float | None]]:
        """Snapshot of the scheduler's in-memory state. Used by the
        /schedules page to show 'last fired' next to each row."""
        with self._lock:
            return {
                sid: {
                    "last_fired": self._last_fired.get(sid),
                    "first_seen": self._first_seen.get(sid),
                }
                for sid in {*self._last_fired.keys(), *self._first_seen.keys()}
            }
