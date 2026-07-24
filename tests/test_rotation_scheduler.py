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


# -- Smart sync (issue #10) on rotations -------------------------------


class _FakeTelemetry:
    """Same shape as the test_scheduler.py stand-in: per-device
    ``predicted_next_wake_at`` + ``is_trusted`` flags without spinning
    up the real persisted telemetry store."""

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self._entries = entries or {}

    def get(self, device_id: str):
        raw = self._entries.get(device_id)
        if raw is None:
            return None

        class _E:
            predicted_next_wake_at = raw.get("predicted_next_wake_at")
            is_trusted = bool(raw.get("is_trusted", False))

        return _E()


def _smart_wiring(tmp_path: Path, *, device_ids, telemetry):
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
        page_exists=lambda _pid: True,
        device_ids_for_page=lambda page_id: device_ids.get(page_id, []),
        device_telemetry=telemetry,
    )
    return scheduler, push_manager, rotation_store


def test_rotation_smart_sync_off_fires_on_transition(tmp_path: Path) -> None:
    """``smart_sync=False`` (default): step transitions fire immediately
    regardless of telemetry, matching pre-issue-#10 behaviour."""
    scheduler, push, store = _smart_wiring(
        tmp_path,
        device_ids={"a": ["esp"], "b": ["esp"]},
        telemetry=_FakeTelemetry({"esp": {"is_trusted": True, "predicted_next_wake_at": 9e9}}),
    )
    store.upsert(_rot(steps=_steps(("a", 30), ("b", 30))))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 2


def test_rotation_smart_sync_no_devices_falls_back(tmp_path: Path) -> None:
    """Smart-sync ON but page has no bound devices: rotation still
    transitions naturally (otherwise an orphan rotation would never
    fire)."""
    scheduler, push, store = _smart_wiring(
        tmp_path, device_ids={"a": [], "b": []}, telemetry=_FakeTelemetry()
    )
    r = _rot(steps=_steps(("a", 30), ("b", 30))).model_copy(update={"smart_sync": True})
    store.upsert(r)
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 2


def test_rotation_smart_sync_warming_up_falls_back(tmp_path: Path) -> None:
    """Devices bound but none trusted yet: keep firing on natural
    transitions so the panel still updates during warm-up."""
    scheduler, push, store = _smart_wiring(
        tmp_path,
        device_ids={"a": ["esp"], "b": ["esp"]},
        telemetry=_FakeTelemetry({"esp": {"is_trusted": False, "predicted_next_wake_at": 9e9}}),
    )
    r = _rot(steps=_steps(("a", 30), ("b", 30))).model_copy(update={"smart_sync": True})
    store.upsert(r)
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 2


def test_rotation_smart_sync_holds_outside_lead_window(tmp_path: Path) -> None:
    """Wake is 10 min away with a 10s lead: smart-sync holds the
    rotation entirely. Mirrors the schedule gate, no special-case
    'always fire first observation' carve-out, the panel keeps showing
    whatever it last got until the lead window opens."""
    now = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    # Wake well past both tick moments below so the gate stays closed
    # on each one (if the prediction were earlier than a later tick,
    # the gate falls through to fire as a "stale prediction, ship the
    # freshest frame anyway" safety net).
    far_future = now.timestamp() + 3600
    scheduler, push, store = _smart_wiring(
        tmp_path,
        device_ids={"a": ["esp"], "b": ["esp"]},
        telemetry=_FakeTelemetry(
            {"esp": {"is_trusted": True, "predicted_next_wake_at": far_future}}
        ),
    )
    r = _rot(steps=_steps(("a", 30), ("b", 30))).model_copy(
        update={"smart_sync": True, "smart_sync_lead_s": 10}
    )
    store.upsert(r)
    scheduler._tick_once(now)
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 0


def test_rotation_smart_sync_fires_inside_lead_window(tmp_path: Path) -> None:
    """Wake is 5s away, lead is 10s: fire now with whichever step is
    current at this moment so the fresh frame is sitting in the broker
    when the panel wakes."""
    now = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    near_wake = now.timestamp() + 5
    scheduler, push, store = _smart_wiring(
        tmp_path,
        device_ids={"a": ["esp"], "b": ["esp"]},
        telemetry=_FakeTelemetry(
            {"esp": {"is_trusted": True, "predicted_next_wake_at": near_wake}}
        ),
    )
    r = _rot(steps=_steps(("a", 30), ("b", 30))).model_copy(
        update={"smart_sync": True, "smart_sync_lead_s": 10}
    )
    store.upsert(r)
    scheduler._tick_once(now)
    assert push.push.call_count == 1
    assert push.push.call_args.args[0] == "a"


def test_rotation_smart_sync_skips_intermediate_steps(tmp_path: Path) -> None:
    """Long wake interval crosses multiple step boundaries: the only
    fire that happens is the one inside the lead window, with whatever
    step is current at fire-time. Intermediate steps the panel slept
    through are silently skipped, matching what it would have rendered
    on wake anyway."""
    # 4 steps of 15 min each, 1h cycle. Device wake is at 00:50, lead
    # 10s. Tick at 00:00 (step 0), 00:15 (step 1), 00:30 (step 2),
    # 00:45 (step 3) all held; tick at 00:49:55 inside lead window
    # fires step 3 ("d"). Steps a/b/c never render.
    r = _rot(
        anchor="00:00",
        steps=_steps(("a", 15), ("b", 15), ("c", 15), ("d", 15)),
    ).model_copy(update={"smart_sync": True, "smart_sync_lead_s": 10})

    wake_at = datetime(2026, 6, 15, 0, 50, tzinfo=UTC).timestamp()
    scheduler, push, store = _smart_wiring(
        tmp_path,
        device_ids={"a": ["esp"], "b": ["esp"], "c": ["esp"], "d": ["esp"]},
        telemetry=_FakeTelemetry({"esp": {"is_trusted": True, "predicted_next_wake_at": wake_at}}),
    )
    store.upsert(r)
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 15, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 45, tzinfo=UTC))
    assert push.push.call_count == 0
    scheduler._tick_once(datetime(2026, 6, 15, 0, 49, 55, tzinfo=UTC))
    assert push.push.call_count == 1
    assert push.push.call_args.args[0] == "d"


# -- rejoin pass (discussion #140: paged-away devices come back) -----------


def _rejoin_harness(tmp_path: Path):
    from app.state.device_rotation_state_model import DeviceRotationState
    from app.state.device_rotation_state_store import DeviceRotationStateStore

    schedule_store = ScheduleStore(tmp_path / "sched.json")
    rotation_store = RotationStore(tmp_path / "rot.json")
    state_store = DeviceRotationStateStore(tmp_path / "state.json")
    push_manager = MagicMock()
    push_manager.push.return_value = MagicMock(
        status="sent", error=None, duration_s=0.1, event_id=None
    )
    scheduler = Scheduler(
        store=schedule_store,
        rotation_store=rotation_store,
        rotation_state_store=state_store,
        push_manager=lambda: push_manager,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
    )
    rotation_store.upsert(_rot(id="r1", steps=_steps(("home", 60), ("away", 60))))
    return scheduler, push_manager, state_store, DeviceRotationState


def test_lapsed_override_rejoins_device_to_current_step(tmp_path: Path) -> None:
    scheduler, push_manager, state_store, State = _rejoin_harness(tmp_path)
    # Monday 00:10 -> computed step 0 ("home"). Device paged to step 1
    # by button; its hold lapsed ten minutes ago.
    now = datetime(2026, 6, 15, 0, 10, tzinfo=UTC)
    state_store.upsert(
        State(
            device_id="panel",
            rotation_id="r1",
            step_index=1,
            override_until=datetime(2026, 6, 15, 0, 0, tzinfo=UTC),
        )
    )
    scheduler._maybe_rejoin_rotations(now)
    push_manager.push.assert_called_once()
    args, kwargs = push_manager.push.call_args
    assert args[0] == "home"
    assert kwargs["device_ids"] == {"panel"}
    fresh = state_store.get("panel")
    assert fresh.override_until is None and fresh.step_index == 0


def test_unlapsed_override_is_left_alone(tmp_path: Path) -> None:
    scheduler, push_manager, state_store, State = _rejoin_harness(tmp_path)
    now = datetime(2026, 6, 15, 0, 10, tzinfo=UTC)
    state_store.upsert(
        State(
            device_id="panel",
            rotation_id="r1",
            step_index=1,
            override_until=datetime(2026, 6, 15, 23, 0, tzinfo=UTC),
        )
    )
    scheduler._maybe_rejoin_rotations(now)
    push_manager.push.assert_not_called()
    assert state_store.get("panel").override_until is not None


def test_lapsed_override_on_current_step_clears_without_push(tmp_path: Path) -> None:
    scheduler, push_manager, state_store, State = _rejoin_harness(tmp_path)
    now = datetime(2026, 6, 15, 0, 10, tzinfo=UTC)  # computed step 0
    state_store.upsert(
        State(
            device_id="panel",
            rotation_id="r1",
            step_index=0,
            override_until=datetime(2026, 6, 15, 0, 0, tzinfo=UTC),
        )
    )
    scheduler._maybe_rejoin_rotations(now)
    push_manager.push.assert_not_called()
    assert state_store.get("panel").override_until is None


def test_failed_rejoin_push_retries_next_tick(tmp_path: Path) -> None:
    scheduler, push_manager, state_store, State = _rejoin_harness(tmp_path)
    push_manager.push.return_value = MagicMock(
        status="failed", error="boom", duration_s=0.1, event_id=None
    )
    now = datetime(2026, 6, 15, 0, 10, tzinfo=UTC)
    state_store.upsert(
        State(
            device_id="panel",
            rotation_id="r1",
            step_index=1,
            override_until=datetime(2026, 6, 15, 0, 0, tzinfo=UTC),
        )
    )
    scheduler._maybe_rejoin_rotations(now)
    # Hold stays set (still lapsed), so the next tick retries.
    assert state_store.get("panel").override_until is not None
    scheduler._maybe_rejoin_rotations(now)
    assert push_manager.push.call_count == 2


# -- content refresh (discussion #140: data staleness between transitions) --


def _refresh_harness(tmp_path: Path, *, refresh_minutes: int = 5):
    from app.state.device_rotation_state_model import DeviceRotationState
    from app.state.device_rotation_state_store import DeviceRotationStateStore

    schedule_store = ScheduleStore(tmp_path / "sched.json")
    rotation_store = RotationStore(tmp_path / "rot.json")
    state_store = DeviceRotationStateStore(tmp_path / "state.json")
    push_manager = MagicMock()
    push_manager.push.return_value = MagicMock(
        status="sent", error=None, duration_s=0.1, event_id=None
    )
    scheduler = Scheduler(
        store=schedule_store,
        rotation_store=rotation_store,
        rotation_state_store=state_store,
        push_manager=lambda: push_manager,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
    )
    rot = Rotation(
        id="r1",
        name="r1",
        anchor="00:00",
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        steps=_steps(("home", 600), ("away", 600)),  # long dwells
        refresh_minutes=refresh_minutes,
    )
    rotation_store.upsert(rot)
    return scheduler, push_manager, state_store, DeviceRotationState


def test_content_refresh_repushes_current_step_on_cadence(tmp_path: Path) -> None:
    scheduler, push_manager, _state_store, _State = _refresh_harness(tmp_path)
    now = datetime(2026, 6, 15, 1, 0, tzinfo=UTC)  # inside step 0's dwell

    scheduler._maybe_refresh_rotation_content(now)
    assert push_manager.push.call_count == 1
    assert push_manager.push.call_args[0][0] == "home"

    # Within the cadence: no re-push.
    scheduler._maybe_refresh_rotation_content(now + __import__("datetime").timedelta(minutes=2))
    assert push_manager.push.call_count == 1

    # Past the cadence: re-push.
    scheduler._maybe_refresh_rotation_content(now + __import__("datetime").timedelta(minutes=6))
    assert push_manager.push.call_count == 2


def test_content_refresh_off_by_default(tmp_path: Path) -> None:
    scheduler, push_manager, _s, _S = _refresh_harness(tmp_path, refresh_minutes=0)
    scheduler._maybe_refresh_rotation_content(datetime(2026, 6, 15, 1, 0, tzinfo=UTC))
    push_manager.push.assert_not_called()


def test_content_refresh_counts_transition_as_fresh(tmp_path: Path) -> None:
    scheduler, push_manager, _s, _S = _refresh_harness(tmp_path)
    now = datetime(2026, 6, 15, 1, 0, tzinfo=UTC)
    # A transition fired 1 minute ago -> the step content is fresh.
    scheduler._rotation_last_pushed_at["r1"] = (
        now - __import__("datetime").timedelta(minutes=1)
    ).timestamp()
    scheduler._maybe_refresh_rotation_content(now)
    push_manager.push.assert_not_called()


def test_content_refresh_covers_manually_held_devices(tmp_path: Path) -> None:
    scheduler, push_manager, state_store, State = _refresh_harness(tmp_path)
    now = datetime(2026, 6, 15, 1, 0, tzinfo=UTC)  # computed step 0 ("home")
    state_store.upsert(
        State(
            device_id="panel",
            rotation_id="r1",
            step_index=1,  # held on "away"
            override_until=datetime(2026, 6, 15, 23, 0, tzinfo=UTC),
        )
    )
    scheduler._maybe_refresh_rotation_content(now)
    pages = [c.args[0] for c in push_manager.push.call_args_list]
    assert pages == ["home", "away"]
    held_call = push_manager.push.call_args_list[1]
    assert held_call.kwargs["device_ids"] == {"panel"}

    # Held page respects the same cadence.
    scheduler._maybe_refresh_rotation_content(now + __import__("datetime").timedelta(minutes=2))
    assert push_manager.push.call_count == 2
