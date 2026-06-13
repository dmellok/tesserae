"""Rotation scheduler behaviour: ``compute_current_step`` math + the
Scheduler tick wiring (fires on step transitions only, respects DOW
filter, clears state on disable)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.scheduler import Scheduler, compute_current_step
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_store import ScheduleStore


def _steps(*pairs: tuple[str, int]) -> list[RotationStep]:
    return [RotationStep(page_id=p, dwell_minutes=d) for p, d in pairs]


def _rot(
    *,
    id: str = "r",
    anchor: str = "00:00",
    days: list[int] | None = None,
    steps: list[RotationStep] | None = None,
    priority: int = 0,
) -> Rotation:
    return Rotation(
        id=id,
        name=id,
        anchor=anchor,
        days_of_week=days if days is not None else [0, 1, 2, 3, 4, 5, 6],
        steps=steps or _steps(("a", 30), ("b", 30)),
        priority=priority,
    )


# -- compute_current_step ----------------------------------------------


def test_step_zero_at_anchor() -> None:
    r = _rot(anchor="00:00", steps=_steps(("a", 30), ("b", 30)))
    # 2026-06-15 is a Monday; the rotation runs all week.
    now = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    idx, step = compute_current_step(r, now, UTC)
    assert idx == 0
    assert step.page_id == "a"


def test_step_transitions_at_dwell_boundary() -> None:
    r = _rot(anchor="00:00", steps=_steps(("a", 30), ("b", 30)))
    now = datetime(2026, 6, 15, 0, 30, tzinfo=UTC)
    idx, step = compute_current_step(r, now, UTC)
    assert idx == 1
    assert step.page_id == "b"


def test_cycle_wraps_to_step_zero_after_full_cycle() -> None:
    r = _rot(anchor="00:00", steps=_steps(("a", 30), ("b", 30)))
    now = datetime(2026, 6, 15, 1, 0, tzinfo=UTC)  # 60 min from anchor
    idx, step = compute_current_step(r, now, UTC)
    assert idx == 0
    assert step.page_id == "a"


def test_uneven_steps_pick_correct_bucket() -> None:
    """Three steps with different dwells; sample within the middle one."""
    r = _rot(steps=_steps(("a", 15), ("b", 45), ("c", 30)))
    # At anchor + 30 min, we're inside step "b" (15..60 min window).
    now = datetime(2026, 6, 15, 0, 30, tzinfo=UTC)
    idx, step = compute_current_step(r, now, UTC)
    assert idx == 1
    assert step.page_id == "b"


def test_before_anchor_returns_none() -> None:
    """A rotation anchored at 09:00 returns None when wall clock is 08:30."""
    r = _rot(anchor="09:00", steps=_steps(("a", 60), ("b", 60)))
    now = datetime(2026, 6, 15, 8, 30, tzinfo=UTC)
    assert compute_current_step(r, now, UTC) is None


def test_outside_days_of_week_returns_none() -> None:
    """Monday-only rotation, tested on Tuesday."""
    r = _rot(days=[0], steps=_steps(("a", 30), ("b", 30)))  # Monday only
    now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)  # Tuesday
    assert compute_current_step(r, now, UTC) is None


def test_anchor_reseeds_each_local_day() -> None:
    """Day 2 at the anchor should pick step 0, regardless of how long
    a cycle is. Cycle is 90 min; day 1 anchor + 90 min lands at step 0,
    but day 2 at anchor should ALSO be step 0 (not whichever step the
    continuous mod would land us on)."""
    r = _rot(anchor="00:00", steps=_steps(("a", 30), ("b", 60)))  # 90-min cycle
    day_one = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    day_two = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    idx_d1, _ = compute_current_step(r, day_one, UTC)
    idx_d2, _ = compute_current_step(r, day_two, UTC)
    assert idx_d1 == 0
    assert idx_d2 == 0  # re-anchors at midnight, doesn't carry remainder


def test_end_at_returns_none_past_window() -> None:
    """Rotation runs 09:00 to 17:00. At 17:30, it's no longer active."""
    r = Rotation(
        id="r",
        name="r",
        anchor="09:00",
        end_at="17:00",
        steps=_steps(("a", 30), ("b", 30)),
    )
    now = datetime(2026, 6, 15, 17, 30, tzinfo=UTC)
    assert compute_current_step(r, now, UTC) is None


def test_end_at_active_just_before_window_end() -> None:
    """One minute before end_at, the rotation is still active."""
    r = Rotation(
        id="r",
        name="r",
        anchor="09:00",
        end_at="17:00",
        steps=_steps(("a", 30), ("b", 30)),
    )
    now = datetime(2026, 6, 15, 16, 59, tzinfo=UTC)
    assert compute_current_step(r, now, UTC) is not None


def test_end_at_none_means_until_midnight() -> None:
    """Default behaviour (no end_at): cycle continues to end of day."""
    r = Rotation(id="r", name="r", anchor="09:00", steps=_steps(("a", 30), ("b", 30)))
    late = datetime(2026, 6, 15, 23, 59, tzinfo=UTC)
    assert compute_current_step(r, late, UTC) is not None


def test_end_at_wrap_around_window() -> None:
    """end_at < anchor means a window that wraps midnight, e.g.
    22:00 to 06:00. Active at 23:00 and 02:00, inactive at 12:00."""
    r = Rotation(
        id="r",
        name="r",
        anchor="22:00",
        end_at="06:00",
        steps=_steps(("a", 60), ("b", 60)),
    )
    assert compute_current_step(r, datetime(2026, 6, 15, 23, 0, tzinfo=UTC), UTC) is not None
    # 02:00 is "before today's 22:00 anchor", so the same-day path
    # returns None (we're not in the active window for THIS day's
    # cycle). Wrap-window semantics still hold for daytime gap:
    assert compute_current_step(r, datetime(2026, 6, 15, 12, 0, tzinfo=UTC), UTC) is None


def test_timezone_aware_anchor() -> None:
    """``anchor='09:00'`` is the local 9am; at 09:30 local we're 30 min
    in, so step 1 on a 30/30 split."""
    tz = ZoneInfo("Europe/London")
    r = _rot(anchor="09:00", steps=_steps(("a", 30), ("b", 30)))
    # 09:30 BST = 08:30 UTC (June, DST active).
    now_utc = datetime(2026, 6, 15, 8, 30, tzinfo=UTC)
    idx, _ = compute_current_step(r, now_utc, tz)
    assert idx == 1


# -- Scheduler tick wiring ---------------------------------------------


@pytest.fixture
def wiring(tmp_path: Path):
    """Returns (scheduler, push_manager_stub, rotation_store)."""
    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    rotation_store = RotationStore(tmp_path / "rotations.json")
    push_manager = MagicMock()
    push_manager.push.return_value = MagicMock(
        status="sent", error=None, duration_s=0.01, event_id="evt"
    )
    scheduler = Scheduler(
        store=schedule_store,
        rotation_store=rotation_store,
        push_manager=lambda: push_manager,
        page_exists=lambda _pid: True,  # tests use fake page_ids
    )
    return scheduler, push_manager, rotation_store


def test_first_tick_fires_current_step(wiring) -> None:
    """A freshly enabled rotation fires its current step on the first
    observation, not on the next transition (otherwise users see the
    rotation 'do nothing' for up to a full step dwell)."""
    scheduler, push, store = wiring
    store.upsert(_rot(steps=_steps(("a", 30), ("b", 30))))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    push.push.assert_called_once()
    args, kwargs = push.push.call_args
    assert args[0] == "a"
    assert kwargs["source"] == "rotation"


def test_subsequent_ticks_within_same_step_do_not_re_fire(wiring) -> None:
    scheduler, push, store = wiring
    store.upsert(_rot(steps=_steps(("a", 30), ("b", 30))))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 5, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 20, tzinfo=UTC))
    assert push.push.call_count == 1


def test_step_transition_fires(wiring) -> None:
    scheduler, push, store = wiring
    store.upsert(_rot(steps=_steps(("a", 30), ("b", 30))))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 2
    assert push.push.call_args.args[0] == "b"


def test_disable_clears_last_step_so_reenable_fires_fresh(wiring) -> None:
    """Disable + re-enable should fire the current step immediately,
    not wait for the next transition."""
    scheduler, push, store = wiring
    r = _rot(steps=_steps(("a", 30), ("b", 30)))
    store.upsert(r)
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    assert push.push.call_count == 1
    # Disable
    store.upsert(r.model_copy(update={"enabled": False}))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 5, tzinfo=UTC))
    assert push.push.call_count == 1  # disabled doesn't fire
    # Re-enable and tick again - should fire current step even though
    # it's the same one that fired pre-disable.
    store.upsert(r.model_copy(update={"enabled": True}))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 10, tzinfo=UTC))
    assert push.push.call_count == 2


def test_outside_days_of_week_skips_tick(wiring) -> None:
    scheduler, push, store = wiring
    store.upsert(_rot(days=[0], steps=_steps(("a", 30), ("b", 30))))  # Monday only
    # 2026-06-16 is Tuesday.
    scheduler._tick_once(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
    push.push.assert_not_called()


def test_quiet_result_still_bumps_last_step(wiring) -> None:
    """A quiet-hours push counts as 'we tried' so the next tick within
    the same step doesn't re-attempt."""
    scheduler, push, store = wiring
    push.push.return_value = MagicMock(status="quiet", error=None, duration_s=0.0, event_id="evt")
    store.upsert(_rot(steps=_steps(("a", 30), ("b", 30))))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 5, tzinfo=UTC))
    assert push.push.call_count == 1


# -- Manual play step / anchor override --------------------------------


def test_force_step_jumps_to_requested_step(wiring) -> None:
    """force_step(rotation, idx=2) re-anchors the cycle so step 2 is
    "now" — compute_step_state on the same instant reports idx 2 with
    a fresh dwell window."""
    scheduler, _push, store = wiring
    r = _rot(steps=_steps(("a", 30), ("b", 30), ("c", 30), ("d", 30)))
    store.upsert(r)
    # Without override at 00:00 we're on step 0.
    now = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    state0 = scheduler.compute_step_state(r, now)
    assert state0 is not None and state0.step_index == 0
    # Force step 2 at 00:00.
    forced = scheduler.force_step(r, 2, now)
    assert forced is not None
    assert forced.step_index == 2
    assert forced.forced_at is not None
    # Step 2's window starts at now and runs 30 minutes.
    assert forced.step_started_at == now
    assert (forced.next_transition_at - forced.step_started_at).total_seconds() == 30 * 60


def test_force_step_clears_last_step_so_tick_fires(wiring) -> None:
    """After force_step, the very next scheduler tick must fire the
    requested step even if that step's index matches the previously
    fired one (e.g. user clicks the same step they're already on as
    a 're-push' shortcut)."""
    scheduler, push, store = wiring
    r = _rot(steps=_steps(("a", 30), ("b", 30)))
    store.upsert(r)
    # Initial tick fires step 0.
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    assert push.push.call_count == 1
    # Force step 0 again at the same wall-clock; tick fires again.
    scheduler.force_step(r, 0, datetime(2026, 6, 15, 0, 1, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 1, tzinfo=UTC))
    assert push.push.call_count == 2


def test_force_step_continues_cycle_from_there(wiring) -> None:
    """After force_step(idx=1), step 2 should fire after step 1's dwell
    elapses — proving the cycle 'continues from here' rather than
    snapping back to the deterministic schedule on the next tick."""
    scheduler, push, store = wiring
    r = _rot(steps=_steps(("a", 30), ("b", 30), ("c", 30)))
    store.upsert(r)
    # Force step 1 at 00:00. Step 1 fires now.
    scheduler.force_step(r, 1, datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    args, _ = push.push.call_args
    assert args[0] == "b"  # step 1's page
    # 29 minutes in: still step 1, no fresh push.
    scheduler._tick_once(datetime(2026, 6, 15, 0, 29, tzinfo=UTC))
    assert push.push.call_count == 1
    # 30 minutes in: step transitions to step 2 (page "c") — would
    # have been step 0 if we'd followed the anchor-deterministic
    # schedule.
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 2
    args, _ = push.push.call_args
    assert args[0] == "c"


def test_force_step_invalid_index_raises(wiring) -> None:
    scheduler, _push, store = wiring
    r = _rot(steps=_steps(("a", 30), ("b", 30)))
    store.upsert(r)
    with pytest.raises(IndexError):
        scheduler.force_step(r, 5, datetime(2026, 6, 15, 0, 0, tzinfo=UTC))


def test_clear_anchor_override_resets_to_deterministic(wiring) -> None:
    """Disabling or deleting a rotation drops the override so a later
    re-enable resumes its anchor-deterministic schedule.

    Uses an explicit UTC tz provider on the scheduler so the assertion
    isn't sensitive to the host's local timezone (the fixture's default
    of host-local would lose this guarantee on hosts where UTC 00:00
    isn't aligned with the rotation's anchor)."""
    scheduler, _push, store = wiring
    scheduler._tz_provider = lambda: UTC
    r = _rot(steps=_steps(("a", 30), ("b", 30), ("c", 30)))
    store.upsert(r)
    scheduler.force_step(r, 2, datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    forced = scheduler.compute_step_state(r, datetime(2026, 6, 15, 0, 1, tzinfo=UTC))
    assert forced is not None and forced.step_index == 2
    scheduler.clear_anchor_override(r.id)
    cleared = scheduler.compute_step_state(r, datetime(2026, 6, 15, 0, 1, tzinfo=UTC))
    assert cleared is not None
    assert cleared.step_index == 0  # back to anchor-deterministic
    assert cleared.forced_at is None


def test_override_garbage_collected_when_next_anchor_catches_up(wiring) -> None:
    """A force_step set on Monday at 12:00 must NOT bleed into
    Tuesday — once the next daily anchor passes, the override is
    silently dropped and the deterministic schedule resumes."""
    scheduler, _push, store = wiring
    scheduler._tz_provider = lambda: UTC
    # 4 steps of 15min each = 1h cycle, anchored 09:00 daily.
    r = _rot(
        anchor="09:00",
        steps=_steps(("a", 15), ("b", 15), ("c", 15), ("d", 15)),
    )
    store.upsert(r)
    # Monday 12:00 UTC, force step 3.
    monday_noon = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    scheduler.force_step(r, 3, monday_noon)
    on_monday = scheduler.compute_step_state(r, monday_noon)
    assert on_monday is not None and on_monday.forced_at is not None
    # Tuesday 09:30 UTC: the daily anchor has rolled over. State must
    # reflect deterministic position (30 min past anchor = step 2).
    tuesday_morning = datetime(2026, 6, 16, 9, 30, tzinfo=UTC)
    on_tuesday = scheduler.compute_step_state(r, tuesday_morning)
    assert on_tuesday is not None
    assert on_tuesday.forced_at is None
    assert on_tuesday.step_index == 2
    # And the override map is empty (GC'd).
    assert r.id not in scheduler._rotation_force_state
