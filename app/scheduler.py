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
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any

from app.push import PushManager, PushResult
from app.scheduler_conditions import ConditionEvaluator
from app.state.deck_store import DeckStore
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
    ``now``. Thin wrapper kept for backwards compatibility; new code
    should use ``Scheduler.compute_current_step`` so that in-memory
    "play step N now" overrides are honoured. The override map lives
    on the Scheduler instance, so this free function only sees the
    deterministic anchor-based schedule."""
    state = _compute_step_state(rotation, now, tz, forced=None)
    if state is None:
        return None
    return state.step_index, rotation.steps[state.step_index]


@dataclass(frozen=True)
class StepState:
    """Resolved current-step state used by the scheduler tick and the
    rotations UI. ``forced_at`` is the wall-clock moment the user
    manually played a step; ``None`` means the regular daily anchor
    is in force."""

    step_index: int
    step_started_at: datetime
    next_transition_at: datetime
    cycle_position_minutes: float
    forced_at: datetime | None


def _compute_step_state(
    rotation: Rotation,
    now: datetime,
    tz: tzinfo | None,
    *,
    forced: tuple[datetime, int] | None,
) -> StepState | None:
    """Locate the active step + dwell-window edges, with optional
    manual override.

    Without override, this matches the original behaviour:
      * Wall-clock must satisfy day-of-week + anchor + end_at gates.
      * Position = (now - today_anchor) % cycle.

    With override (``forced = (clicked_at, step_index)``), we still
    apply the day-of-week + end_at gates, but the cycle anchor used
    for position math becomes ``clicked_at - prefix(step_index)`` so
    that the requested step starts at the moment of the click. The
    override stays in effect as long as the click landed inside
    today's anchor window; the next day's anchor moment silently GCs
    it back to deterministic.
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
    if rotation.end_at is not None:
        end_t = _parse_hhmm(rotation.end_at)
        end_today = local_now.replace(hour=end_t.hour, minute=end_t.minute, second=0, microsecond=0)
        if end_today > anchor_today:
            if local_now >= end_today:
                return None
        else:
            if end_today <= local_now < anchor_today:
                return None
    cycle = rotation.cycle_minutes
    if cycle <= 0:
        return None

    forced_at_local: datetime | None = None
    if forced is not None:
        forced_at, forced_idx = forced
        if 0 <= forced_idx < len(rotation.steps):
            forced_local = forced_at.astimezone(tz) if tz else forced_at.astimezone()
            # Override is valid while the click landed in today's window
            # (>= today's anchor) and isn't in the future.
            if anchor_today <= forced_local <= local_now:
                prefix = sum(s.dwell_minutes for s in rotation.steps[:forced_idx])
                effective_anchor = forced_local - timedelta(minutes=prefix)
                forced_at_local = forced_local
            else:
                effective_anchor = anchor_today
        else:
            effective_anchor = anchor_today
    else:
        effective_anchor = anchor_today

    minutes_since_anchor = (local_now - effective_anchor).total_seconds() / 60.0
    position = minutes_since_anchor % cycle
    cumulative = 0.0
    for idx, step in enumerate(rotation.steps):
        prev_cumulative = cumulative
        cumulative += step.dwell_minutes
        if position < cumulative:
            cycles_done = int(minutes_since_anchor // cycle)
            step_started_minutes = cycles_done * cycle + prev_cumulative
            step_started_at = effective_anchor + timedelta(minutes=step_started_minutes)
            next_transition_at = step_started_at + timedelta(minutes=step.dwell_minutes)
            return StepState(
                step_index=idx,
                step_started_at=step_started_at,
                next_transition_at=next_transition_at,
                cycle_position_minutes=position,
                forced_at=forced_at_local,
            )
    # Edge case: float math left position exactly at cycle. Roll
    # forward to the last step of the previous cycle.
    last_idx = len(rotation.steps) - 1
    last_step = rotation.steps[last_idx]
    cycles_done = int(minutes_since_anchor // cycle)
    step_started_minutes = cycles_done * cycle + (cycle - last_step.dwell_minutes)
    step_started_at = effective_anchor + timedelta(minutes=step_started_minutes)
    return StepState(
        step_index=last_idx,
        step_started_at=step_started_at,
        next_transition_at=step_started_at + timedelta(minutes=last_step.dwell_minutes),
        cycle_position_minutes=position,
        forced_at=forced_at_local,
    )


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
        deck_store: DeckStore | None = None,
        # Conditional schedules + rotation steps (v0.48). Optional; None
        # means every condition resolves to True (legacy behaviour) so
        # existing tests don't need updating. Production wires a real
        # evaluator backed by ha_core's state list + the app's tz +
        # settings.app lat/lon.
        condition_evaluator: ConditionEvaluator | None = None,
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
        self._deck_store = deck_store
        # deck_id -> last warm POSIX timestamp, so a deck's pages are re-warmed
        # (in the background, silently) at its refresh cadence. The lock keeps
        # warm passes from stacking when one runs longer than a tick.
        self._deck_last_warm: dict[str, float] = {}
        self._deck_warm_lock = threading.Lock()
        self._push_factory = push_manager
        self._event_log = event_log
        self._tz_provider = timezone_provider or (lambda: None)
        self._page_exists = page_exists
        self._device_ids_for_page = device_ids_for_page
        self._device_telemetry = device_telemetry
        self._condition_evaluator = condition_evaluator
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
        # rotation_id -> POSIX timestamp of the last push fired for it,
        # used by the v0.48 minimum-hold-time gate so a flapping HA
        # sensor near a numeric threshold doesn't thrash a priority
        # rotation. Updated on every successful (sent / quiet / held)
        # fire.
        self._rotation_last_pushed_at: dict[str, float] = {}
        # v0.48 running-state pills. Tracks the most recent PushStatus
        # the scheduler observed for each schedule / rotation, plus a
        # human-readable reason string (e.g. "conditions not met") so
        # the Schedules + Rotations index pages can show what the
        # scheduler is currently doing without tailing the event log.
        self._last_status: dict[str, str] = {}
        self._last_reason: dict[str, str | None] = {}
        self._rotation_last_status: dict[str, str] = {}
        self._rotation_last_reason: dict[str, str | None] = {}
        # rotation_id we've warned about for a missing step page; same
        # one-shot semantics as ``_warned_missing_page``.
        self._warned_missing_rotation_page: set[str] = set()
        # Debounce key for condition-decision events so we don't write
        # a row on every 30s tick; only when the per-step pass/fail
        # outcome or picked step changes. Maps rotation_id/schedule_id
        # -> stable hash of the previous decision.
        self._rotation_last_condition_key: dict[str, Any] = {}
        self._schedule_last_condition_key: dict[str, Any] = {}
        # Manual "play step N now and continue from there" overrides.
        # Map rotation_id -> (clicked_at_utc, step_index). The cycle
        # math treats clicked_at as the start of step_index's dwell
        # window, replacing the deterministic anchor-based position
        # until the next daily anchor crosses (or the user pokes
        # again). In-memory only, by design: a restart wipes overrides
        # and the rotation goes back to its anchor-deterministic
        # schedule.
        self._rotation_force_state: dict[str, tuple[datetime, int]] = {}
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
                if s.smart_sync and self._smart_sync_should_wait(
                    s.page_id, s.smart_sync_lead_s, now
                ):
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
        # v0.48: refresh the HA state cache once per tick so each
        # condition evaluation across schedules + rotation steps reads
        # consistent state. Best-effort; HA unreachable returns False
        # and the evaluator falls open (every condition passes), so
        # dashboards keep refreshing on their existing cadence even
        # when HA is offline.
        if self._condition_evaluator is not None:
            self._condition_evaluator.refresh_ha_states()
        # Rotations fire FIRST so any same-tick schedule pushes land
        # last. Reason: eink panels show the most recent frame. If a
        # user has set up a daily schedule to override the rotation at
        # 09:00 (priority knob), the schedule's frame ends up on the
        # panel because rotation already fired before it.
        for rotation, step_index in self.find_due_rotations(now):
            self._fire_rotation(rotation, step_index, now, respect_quiet_hours=True)
        for schedule in self.find_due(now):
            self._fire(schedule, now, respect_quiet_hours=True)
        # Deck pre-render refresh (silent, off the tick thread).
        self._maybe_warm_decks(now)

    def _maybe_warm_decks(self, now: datetime) -> None:
        """Kick a background deck-warm pass unless one is already running, so a
        slow warm never blocks the tick or stacks up."""
        if self._deck_store is None:
            return
        if not self._deck_warm_lock.acquire(blocking=False):
            return

        def _run() -> None:
            try:
                self._warm_decks(now)
            finally:
                self._deck_warm_lock.release()

        threading.Thread(target=_run, name="tesserae-deck-warm", daemon=True).start()

    def _warm_decks(self, now: datetime) -> None:
        """Re-warm each enabled deck's pages for its bound devices when the
        deck's refresh cadence is due (or it hasn't been warmed yet). Silent:
        warming stamps a side cache and never repaints a device's live frame."""
        if self._deck_store is None:
            return
        pusher = self._push_factory()
        warm = getattr(pusher, "warm_deck_page", None)
        if not callable(warm):
            return
        now_ts = now.timestamp()
        for deck in self._deck_store.all():
            if not deck.enabled or not deck.device_ids or deck.refresh_interval_minutes <= 0:
                continue
            last = self._deck_last_warm.get(deck.id)
            if last is not None and now_ts - last < deck.refresh_interval_minutes * 60:
                continue
            for device_id in deck.device_ids:
                for page in deck.pages:
                    try:
                        warm(page.page_id, device_id)
                    except Exception:
                        logger.exception(
                            "deck warm failed deck=%s page=%s device=%s",
                            deck.id,
                            page.page_id,
                            device_id,
                        )
            self._deck_last_warm[deck.id] = now_ts

    def compute_step_state(
        self, rotation: Rotation, now: datetime | None = None
    ) -> StepState | None:
        """Resolve the current step + dwell-window edges, with any
        in-memory force-step override in effect. The deterministic
        free ``compute_current_step`` is the no-override version;
        callers that want override-aware state (the scheduler tick,
        the rotations UI) should use this method."""
        now = now or datetime.now(UTC)
        tz = self._tz_provider()
        with self._lock:
            forced = self._rotation_force_state.get(rotation.id)
        state = _compute_step_state(rotation, now, tz, forced=forced)
        # If the override is no longer in effect (the deterministic
        # anchor caught up or rolled past it) we GC it so memory
        # doesn't accumulate stale overrides across days.
        if forced is not None and (state is None or state.forced_at is None):
            with self._lock:
                if self._rotation_force_state.get(rotation.id) == forced:
                    self._rotation_force_state.pop(rotation.id, None)
        return state

    def force_step(
        self,
        rotation: Rotation,
        step_index: int,
        now: datetime | None = None,
    ) -> StepState | None:
        """Record a manual "play step ``step_index`` now" override and
        return the resulting StepState (or ``None`` if the rotation
        isn't active right now). The override re-bases the cycle so
        ``step_index`` starts at ``now``; subsequent steps follow at
        their normal dwell intervals until the next daily anchor.

        Caller is responsible for actually pushing the step (the
        scheduler doesn't auto-fire from this method) and for clearing
        ``_rotation_last_step`` so the next tick treats the new step
        as a transition. ``rotation_routes.play`` wires both."""
        if not 0 <= step_index < len(rotation.steps):
            raise IndexError(f"step_index {step_index} out of range")
        now = now or datetime.now(UTC)
        with self._lock:
            self._rotation_force_state[rotation.id] = (now, step_index)
            # Clear last_step so the next tick treats this as a fresh
            # transition. Without this, the new step's index might
            # equal the previously-fired step's index (e.g. you click
            # the same step you're already on) and the scheduler would
            # skip the push.
            self._rotation_last_step.pop(rotation.id, None)
            # Manual force also bypasses the v0.48 min-hold gate -
            # user intent always reaches the panel, same convention
            # as quiet hours and conditional schedules' ``bypass``.
            self._rotation_last_pushed_at.pop(rotation.id, None)
        return self.compute_step_state(rotation, now)

    def clear_anchor_override(self, rotation_id: str) -> None:
        """Drop any manual override for ``rotation_id``. Used by tests
        and by the rotation routes when the user disables a rotation
        or deletes it."""
        with self._lock:
            self._rotation_force_state.pop(rotation_id, None)

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
        out: list[tuple[Rotation, int]] = []
        for rotation in self._rotation_store.all():
            if not rotation.enabled:
                # Clear last-step so re-enable fires the current step
                # rather than waiting for the next transition.
                with self._lock:
                    self._rotation_last_step.pop(rotation.id, None)
                continue
            state = self.compute_step_state(rotation, now)
            if state is None:
                continue
            # v0.48: route through the conditional / priority picker so
            # an unmet condition on the current step advances to the
            # next eligible one (scheduled mode) or so the highest-
            # priority matching step wins (priority mode). Returns
            # None when no step is eligible right now, which we treat
            # as "hold on whatever was last shown".
            time_step_index = state.step_index
            picked = self._pick_eligible_step(rotation, time_step_index, now)
            # Observability: write one ``conditions`` event row per
            # decision so the Events page shows what the scheduler
            # actually saw (HA state, pass/fail per condition, time
            # vs picked step). Debounced inside the helper.
            self._record_rotation_condition_decision(rotation, time_step_index, picked, now)
            if picked is None:
                # All steps failed their conditions. Surface that as a
                # held pill on the Rotations page so the user can see why
                # the rotation isn't advancing without tailing the log.
                with self._lock:
                    self._rotation_last_status[rotation.id] = "held"
                    self._rotation_last_reason[rotation.id] = "no step's conditions are met"
                continue
            step_index = picked
            step = rotation.steps[step_index]
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
            # Minimum hold gate (v0.48): prevent flap when a condition
            # input oscillates near a threshold. Applies in both modes;
            # ``last_pushed_at`` is updated by ``_fire_rotation`` on
            # every successful fire. Manual "play step now" already
            # writes to the override and bypasses the find/fire path.
            min_hold_s = rotation.min_hold_minutes * 60
            if min_hold_s > 0:
                with self._lock:
                    last_pushed_at = self._rotation_last_pushed_at.get(rotation.id)
                if last_pushed_at is not None:
                    elapsed = now.timestamp() - last_pushed_at
                    if elapsed < min_hold_s:
                        continue
            # Smart-sync: same wake-aware gate that schedules use.
            # Hold the transition until a bound device is close to
            # waking, so the frame the panel grabs is fresh rather
            # than minutes-stale. ``compute_step_state`` already runs
            # at every tick, so when the gate later opens we'll pick
            # up whichever step is current at fire-time, which is
            # what the panel would see anyway. Skipped intermediate
            # steps are silent on purpose (the user opted in to "only
            # render around wake").
            if rotation.smart_sync and self._smart_sync_should_wait(
                step.page_id, rotation.smart_sync_lead_s, now
            ):
                continue
            out.append((rotation, step_index))
        out.sort(key=lambda pair: (-pair[0].priority, pair[0].id))
        return out

    def _record_rotation_condition_decision(
        self,
        rotation: Rotation,
        time_step_index: int,
        picked_step_index: int | None,
        now: datetime,
    ) -> None:
        """Write one ``conditions`` event row describing this rotation's
        condition evaluation. No-op when no step has conditions, no
        event log is wired, or no evaluator is configured. Debounced
        per-rotation: a quiet rotation that ticks every 30 seconds with
        identical state writes one row, not 2880 a day."""
        if self._event_log is None or self._condition_evaluator is None:
            return
        if all(not step.conditions for step in rotation.steps):
            return  # no conditions in play; nothing to surface
        step_results: list[dict[str, Any]] = []
        for idx, step in enumerate(rotation.steps):
            if not step.conditions:
                step_results.append(
                    {
                        "step_index": idx,
                        "page_id": step.page_id,
                        "passes": True,
                        "no_conditions": True,
                        "conditions": [],
                    }
                )
                continue
            results = self._condition_evaluator.evaluate(step.conditions, when=now)
            step_results.append(
                {
                    "step_index": idx,
                    "page_id": step.page_id,
                    "passes": all(r.passed for r in results),
                    "conditions": [
                        {
                            "source_kind": r.condition.source_kind,
                            "source_id": r.condition.source_id,
                            "operator": r.condition.operator,
                            "value": r.condition.value,
                            "observed": r.observed,
                            "passed": r.passed,
                            "reason": r.reason,
                        }
                        for r in results
                    ],
                }
            )
        key = (
            time_step_index,
            picked_step_index,
            tuple(
                (
                    sr["step_index"],
                    sr["passes"],
                    tuple((c["passed"], c["observed"]) for c in sr["conditions"]),
                )
                for sr in step_results
            ),
        )
        with self._lock:
            prev = self._rotation_last_condition_key.get(rotation.id)
            if prev == key:
                return
            self._rotation_last_condition_key[rotation.id] = key
        if picked_step_index is None:
            status = "held"
        elif picked_step_index == time_step_index:
            status = "passed"
        else:
            status = "shifted"
        self._event_log.record(
            type="conditions",
            source="rotation",
            target=rotation.id,
            status=status,
            extra={
                "rotation_name": rotation.name,
                "mode": rotation.mode,
                "time_step_index": time_step_index,
                "time_step_page": rotation.steps[time_step_index].page_id,
                "picked_step_index": picked_step_index,
                "picked_step_page": (
                    rotation.steps[picked_step_index].page_id
                    if picked_step_index is not None
                    else None
                ),
                "steps": step_results,
            },
        )

    def _record_schedule_condition_decision(
        self,
        schedule: Schedule,
        passed: bool,
        now: datetime,
    ) -> None:
        """One row per condition evaluation on a Schedule. Same debounce
        + no-op semantics as the rotation variant."""
        if self._event_log is None or self._condition_evaluator is None:
            return
        if not schedule.conditions:
            return
        results = self._condition_evaluator.evaluate(schedule.conditions, when=now)
        conditions = [
            {
                "source_kind": r.condition.source_kind,
                "source_id": r.condition.source_id,
                "operator": r.condition.operator,
                "value": r.condition.value,
                "observed": r.observed,
                "passed": r.passed,
                "reason": r.reason,
            }
            for r in results
        ]
        key = (passed, tuple((c["passed"], c["observed"]) for c in conditions))
        with self._lock:
            prev = self._schedule_last_condition_key.get(schedule.id)
            if prev == key:
                return
            self._schedule_last_condition_key[schedule.id] = key
        status = "passed" if passed else "fallback" if schedule.fallback_page_id else "held"
        self._event_log.record(
            type="conditions",
            source="schedule",
            target=schedule.id,
            status=status,
            extra={
                "schedule_name": schedule.name,
                "page_id": schedule.page_id,
                "fallback_page_id": schedule.fallback_page_id,
                "conditions": conditions,
            },
        )

    def _pick_eligible_step(
        self,
        rotation: Rotation,
        time_step_index: int,
        now: datetime,
    ) -> int | None:
        """Return the index of the rotation step that should actually
        fire, honouring conditions and the rotation's mode.

        * **scheduled** (default): start at the time-based step, walk
          forward through the cycle, return the first step whose
          conditions pass. ``None`` if none pass; the caller treats
          that as "hold on whatever was last shown".
        * **priority**: walk steps in declared order and return the
          first whose conditions pass. Step durations are ignored.

        Rotations without an evaluator fall back to the time-based
        index for scheduled mode and step 0 for priority mode."""
        if not rotation.steps:
            return None
        if self._condition_evaluator is None:
            return 0 if rotation.mode == "priority" else time_step_index
        n = len(rotation.steps)
        if rotation.mode == "priority":
            order: list[int] = list(range(n))
        else:
            order = [(time_step_index + i) % n for i in range(n)]
        for idx in order:
            step = rotation.steps[idx]
            if self._condition_evaluator.all_pass(step.conditions, when=now):
                return idx
        return None

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
                # v0.48: arm the minimum-hold gate so a flapping
                # condition input can't immediately re-trigger a
                # step transition.
                self._rotation_last_pushed_at[rotation.id] = now.timestamp()
        with self._lock:
            self._rotation_last_status[rotation.id] = result.status
            if result.status == "failed":
                self._rotation_last_reason[rotation.id] = result.error or "push failed"
            elif result.status == "quiet":
                self._rotation_last_reason[rotation.id] = "all devices in quiet hours"
            else:
                self._rotation_last_reason[rotation.id] = None
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

    def _smart_sync_should_wait(self, page_id: str, lead_s: int, now: datetime) -> bool:
        """Return True when smart-sync wants to *hold* the fire (don't
        push yet), False when it should fire now. Shared between
        schedules and rotations: both consult the same predicted-wake
        telemetry, just with their own per-record lead windows.

        Hold conditions:
          - At least one bound device is trusted AND none of the
            trusted devices are within the lead window of their next
            predicted wake. The page has a fresh frame ready but no
            panel is about to wake; waiting saves a stale frame from
            sitting in the broker until the next actual wake.

        Fire conditions (return False, let the natural cadence win):
          - No telemetry dependencies wired (test path / bare run).
          - The page has no bound devices.
          - No bound device is trusted yet (warm-up).
          - At least one trusted device is inside the lead window.
        """
        if self._device_telemetry is None or self._device_ids_for_page is None:
            return False
        device_ids = self._device_ids_for_page(page_id)
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
            # through to the natural cadence so the page still pushes
            # on the user's configured timing.
            return False
        # Fire when the soonest predicted wake is within the lead
        # window of right now (we want the frame waiting before the
        # panel asks for it). Devices that have already woken (offset
        # past prediction) won't satisfy this and the fire waits for
        # the next prediction.
        now_ts = now.timestamp()
        soonest = min(trusted_predictions)
        lead_window_starts_at = soonest - lead_s
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
        bypass_conditions: bool = False,
    ) -> PushResult:
        # Conditional-schedule gate (v0.48). Evaluated before the push
        # so a held schedule incurs zero rendering cost. ``fire_now``
        # passes ``bypass_conditions=True`` because manual intent
        # should always reach the panel (same convention quiet hours
        # uses). When all conditions pass we route to ``schedule.page_id``;
        # when any fail we route to ``schedule.fallback_page_id`` if
        # set, or skip silently.
        target_page = schedule.page_id
        held = False
        if not bypass_conditions and schedule.conditions and self._condition_evaluator is not None:
            passed = self._condition_evaluator.all_pass(schedule.conditions, when=now)
            # Observability: surface the per-condition evaluation on
            # the Events page, same shape as rotation decisions.
            self._record_schedule_condition_decision(schedule, passed, now)
            if not passed:
                held = True
        if held:
            if schedule.fallback_page_id:
                target_page = schedule.fallback_page_id
                logger.info(
                    "Schedule %s: conditions failed; routing to fallback page %s",
                    schedule.id,
                    target_page,
                )
            else:
                logger.info(
                    "Schedule %s held: conditions not met (skipping silently)",
                    schedule.id,
                )
                # Treat held like a successful fire for last_fired so
                # the interval gate doesn't re-evaluate every tick
                # through the held window.
                with self._lock:
                    self._last_fired[schedule.id] = now.timestamp()
                    self._last_status[schedule.id] = "held"
                    self._last_reason[schedule.id] = "conditions not met"
                return PushResult(status="held", page_id=schedule.page_id)
        logger.info("Firing schedule %s -> page %s", schedule.id, target_page)
        # Tick-driven firings (the background loop) are automation -
        # they should respect quiet hours so a 22:30 schedule doesn't
        # wake the room. fire_now() is the manual "Fire" button on the
        # Schedules page; user intent always goes through, so it leaves
        # respect_quiet_hours off.
        result = self._push_factory().push(
            target_page,
            respect_quiet_hours=respect_quiet_hours,
            source="scheduler_fallback" if held else "scheduler",
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
        with self._lock:
            self._last_status[schedule.id] = result.status
            if held:
                # Conditions failed but a fallback page was configured;
                # surface that on the pill so the user knows why a
                # different page is showing.
                self._last_reason[schedule.id] = "fallback page (conditions failed)"
            elif result.status == "failed":
                self._last_reason[schedule.id] = result.error or "push failed"
            elif result.status == "quiet":
                self._last_reason[schedule.id] = "all devices in quiet hours"
            else:
                self._last_reason[schedule.id] = None
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
        """Manual trigger: skip every gate (quiet hours, conditions),
        fire the schedule immediately."""
        s = self._store.get(schedule_id)
        if s is None:
            return None
        return self._fire(s, datetime.now(UTC), bypass_conditions=True)

    def status(self) -> dict[str, dict[str, Any]]:
        """Snapshot of the scheduler's in-memory state. Used by the
        /schedules page to show 'last fired' + the v0.48 running-state
        pill next to each row. Values:

        * ``last_fired`` — POSIX timestamp of the last successful fire.
        * ``first_seen`` — POSIX timestamp the scheduler first observed
          this enabled schedule (suppresses daily backfill).
        * ``last_status`` — most recent ``PushStatus`` string (sent /
          held / quiet / failed). ``None`` if the schedule has never
          been ticked since process start.
        * ``last_reason`` — human-readable detail (e.g. ``"conditions
          not met"``, ``"fallback page (conditions failed)"``). ``None``
          on plain success.
        """
        with self._lock:
            ids = self._last_fired.keys() | self._first_seen.keys() | self._last_status.keys()
            return {
                sid: {
                    "last_fired": self._last_fired.get(sid),
                    "first_seen": self._first_seen.get(sid),
                    "last_status": self._last_status.get(sid),
                    "last_reason": self._last_reason.get(sid),
                }
                for sid in ids
            }

    def rotation_status(self) -> dict[str, dict[str, Any]]:
        """Snapshot of per-rotation runtime state, mirroring ``status``.
        Used by the Rotations index to surface ``held`` (no step's
        conditions met), ``quiet`` (devices asleep), and ``failed``
        states without tailing the event log."""
        with self._lock:
            ids = (
                self._rotation_last_status.keys()
                | self._rotation_last_step.keys()
                | self._rotation_last_pushed_at.keys()
            )
            return {
                rid: {
                    "last_status": self._rotation_last_status.get(rid),
                    "last_reason": self._rotation_last_reason.get(rid),
                    "last_step": self._rotation_last_step.get(rid),
                    "last_pushed_at": self._rotation_last_pushed_at.get(rid),
                }
                for rid in ids
            }
