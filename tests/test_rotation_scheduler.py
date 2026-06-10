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
