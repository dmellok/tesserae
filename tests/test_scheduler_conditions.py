"""Tests for v0.48 conditional schedules + rotation routing.

Covers four orthogonal axes:

* ``Schedule.conditions`` + ``fallback_page_id`` routing in ``_fire``
* ``RotationStep.conditions`` skip in scheduled-mode rotations
* ``Rotation.mode == "priority"`` route-by-first-matching-step
* ``Rotation.min_hold_minutes`` flap-prevention gate

The evaluator itself is exercised through the scheduler rather than
in isolation; ``test_scheduler_conditions_evaluator.py`` covers the
HA / time / sun resolvers directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.push import PushResult
from app.scheduler import Scheduler
from app.scheduler_conditions import ConditionEvaluator
from app.state.conditions import ha_condition
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore

# -- test plumbing -----------------------------------------------------


class _StubPushManager:
    """Minimal PushManager stand-in: records every push call + returns
    whatever PushResult the test stuffed into ``_result``."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []  # (page_id, source)
        self._result = PushResult(status="sent", page_id="ignored")

    def push(
        self,
        page_id: str,
        *,
        respect_quiet_hours: bool = False,
        source: str = "page",
    ) -> PushResult:
        del respect_quiet_hours
        self.calls.append((page_id, source))
        return PushResult(status="sent", page_id=page_id)


def _scheduler(
    tmp_path: Path,
    *,
    schedules: list[Schedule] | None = None,
    rotations: list[Rotation] | None = None,
    ha_states: list[dict[str, Any]] | None = None,
) -> tuple[Scheduler, _StubPushManager]:
    """Build a scheduler with file-backed stores in tmp_path + a stub
    PushManager + a configured evaluator."""
    push = _StubPushManager()
    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    for s in schedules or []:
        schedule_store.upsert(s)
    rotation_store = RotationStore(tmp_path / "rotations.json")
    for r in rotations or []:
        rotation_store.upsert(r)
    evaluator = ConditionEvaluator(
        ha_get_states=lambda: list(ha_states or []),
        timezone_provider=lambda: None,
        location_provider=lambda: (None, None),
    )
    evaluator.refresh_ha_states()
    scheduler = Scheduler(
        store=schedule_store,
        rotation_store=rotation_store,
        push_manager=lambda: push,  # type: ignore[arg-type]
        condition_evaluator=evaluator,
    )
    return scheduler, push


def _hourly_schedule(
    schedule_id: str,
    page_id: str,
    **kwargs: Any,
) -> Schedule:
    """Test-data convenience: a daily 09:00 schedule on every day."""
    return Schedule(
        id=schedule_id,
        name=schedule_id,
        page_id=page_id,
        type="daily",
        fires_at=datetime(2026, 6, 16, 9, 0, tzinfo=UTC),
        **kwargs,
    )


# -- schedule conditions -----------------------------------------------


def test_schedule_with_passing_conditions_fires(tmp_path: Path) -> None:
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
    )
    scheduler, push = _scheduler(
        tmp_path,
        schedules=[s],
        ha_states=[{"entity_id": "binary_sensor.home", "state": "on"}],
    )
    result = scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    assert result.status == "sent"
    assert push.calls == [("dashboard_morning", "scheduler")]


def test_schedule_with_failing_conditions_and_no_fallback_is_held(tmp_path: Path) -> None:
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
    )
    scheduler, push = _scheduler(
        tmp_path,
        schedules=[s],
        ha_states=[{"entity_id": "binary_sensor.home", "state": "off"}],
    )
    result = scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    assert result.status == "held"
    assert push.calls == []  # nothing pushed


def test_schedule_with_failing_conditions_routes_to_fallback(tmp_path: Path) -> None:
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
        fallback_page_id="away_message",
    )
    scheduler, push = _scheduler(
        tmp_path,
        schedules=[s],
        ha_states=[{"entity_id": "binary_sensor.home", "state": "off"}],
    )
    result = scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    assert result.status == "sent"
    # Routed to fallback, source tagged as scheduler_fallback so the
    # history view can distinguish primary from fallback fires.
    assert push.calls == [("away_message", "scheduler_fallback")]


def test_fire_now_bypasses_conditions(tmp_path: Path) -> None:
    """Manual fire bypasses the condition gate the same way it
    bypasses quiet hours - user intent always reaches the panel."""
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
    )
    scheduler, push = _scheduler(
        tmp_path,
        schedules=[s],
        ha_states=[{"entity_id": "binary_sensor.home", "state": "off"}],
    )
    result = scheduler.fire_now(s.id)
    assert result is not None
    assert result.status == "sent"
    assert push.calls == [("dashboard_morning", "scheduler")]


def test_legacy_schedule_without_evaluator_fires_normally(tmp_path: Path) -> None:
    """Schedulers built before the evaluator existed (or in tests
    that don't wire one) treat every condition as passing."""
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
    )
    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    schedule_store.upsert(s)
    push = _StubPushManager()
    scheduler = Scheduler(
        store=schedule_store,
        push_manager=lambda: push,  # type: ignore[arg-type]
        # no condition_evaluator
    )
    result = scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    assert result.status == "sent"
    assert push.calls == [("dashboard_morning", "scheduler")]


# -- rotation scheduled-mode routing -----------------------------------


def _rotation(
    rotation_id: str,
    steps: list[RotationStep],
    *,
    mode: str = "scheduled",
    min_hold_minutes: int = 5,
) -> Rotation:
    return Rotation(
        id=rotation_id,
        name=rotation_id,
        steps=steps,
        mode=mode,  # type: ignore[arg-type]
        min_hold_minutes=min_hold_minutes,
    )


def test_rotation_scheduled_mode_skips_failing_step(tmp_path: Path) -> None:
    """When the time-based slot lands on a step whose conditions
    fail, the rotation advances to the next eligible step rather
    than holding on a dark frame."""
    rotation = _rotation(
        "kitchen",
        steps=[
            RotationStep(
                page_id="solar_dashboard",
                dwell_minutes=30,
                conditions=[ha_condition("sensor.solar_w", ">", 1000)],
            ),
            RotationStep(page_id="calendar", dwell_minutes=30),
        ],
    )
    scheduler, _push = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[{"entity_id": "sensor.solar_w", "state": "200"}],  # below threshold
    )
    # Time slot 0 (step 0) but conditions fail; picker should advance.
    idx = scheduler._pick_eligible_step(rotation, time_step_index=0, now=datetime.now(UTC))
    assert idx == 1  # advanced past the unmet condition


def test_refresh_ha_states_does_not_wipe_cache_on_empty_result(tmp_path: Path) -> None:
    """Regression for the prod bug: ``_ha_get_states`` ran in a
    background thread without a Flask app context, raised, was caught
    by the closure, returned []. ``refresh_ha_states`` then replaced
    the cache with empty, every condition fail-opened, and the gated
    step fired. The fix keeps the previous cache when the new result
    is empty AND we had data before."""
    del tmp_path
    calls: dict[str, int] = {"n": 0}
    states_by_call = [
        [{"entity_id": "binary_sensor.octoprint_printing", "state": "off"}],
        [],  # second call returns nothing (HA blip / context error)
    ]

    def ha_get_states() -> list[dict[str, Any]]:
        i = calls["n"]
        calls["n"] += 1
        return states_by_call[i] if i < len(states_by_call) else []

    evaluator = ConditionEvaluator(ha_get_states=ha_get_states)

    # First refresh: cache populated.
    assert evaluator.refresh_ha_states() is True
    assert evaluator.ha_state("binary_sensor.octoprint_printing") is not None

    # Second refresh: empty result. Must NOT wipe the previous cache.
    result = evaluator.refresh_ha_states()
    assert result is False, "empty result should not be adopted"
    assert evaluator.ha_state("binary_sensor.octoprint_printing") is not None, (
        "previous cache should survive an empty refresh"
    )


def test_condition_decision_event_emitted_on_rotation_tick(tmp_path: Path) -> None:
    """Each rotation tick that involves condition evaluation should write
    one `type="conditions"` event row to the EventLog, debounced so a
    quiet 30-second tick loop doesn't flood the log."""
    from app.state.event_log import EventLog

    rotation = _rotation(
        "trmnl",
        steps=[
            RotationStep(page_id="todo", dwell_minutes=5),
            RotationStep(
                page_id="3d_print",
                dwell_minutes=5,
                conditions=[ha_condition("binary_sensor.octoprint_printing", "==", "on")],
            ),
        ],
    )
    push = _StubPushManager()
    schedule_store = ScheduleStore(tmp_path / "schedules.json")
    rotation_store = RotationStore(tmp_path / "rotations.json")
    rotation_store.upsert(rotation)
    evaluator = ConditionEvaluator(
        ha_get_states=lambda: [{"entity_id": "binary_sensor.octoprint_printing", "state": "off"}],
    )
    evaluator.refresh_ha_states()
    event_log = EventLog(tmp_path / "events.db", cap=200)
    scheduler = Scheduler(
        store=schedule_store,
        rotation_store=rotation_store,
        push_manager=lambda: push,  # type: ignore[arg-type]
        condition_evaluator=evaluator,
        event_log=event_log,
    )

    # Run the tick. The picker walks past step 1 (3D Print) and picks
    # step 0; a "conditions" event with status="shifted" should land.
    now = datetime(2026, 6, 18, 0, 5, tzinfo=UTC)  # in step 1's time slot
    scheduler._tick_once(now)
    rows = event_log.list(type="conditions")
    assert len(rows) == 1, f"expected one conditions event, got {[r.target for r in rows]}"
    row = rows[0]
    assert row.target == "trmnl"
    assert row.source == "rotation"
    assert row.status == "shifted"
    extra = row.extra
    assert extra["picked_step_index"] != extra["time_step_index"], (
        "picked step should differ from time-slot step when conditions failed"
    )
    # The per-condition detail block should carry observed values so the
    # user can see the printer was 'off' when the decision was made.
    step1 = next(s for s in extra["steps"] if s["step_index"] == 1)
    assert step1["passes"] is False
    cond = step1["conditions"][0]
    assert cond["source_id"] == "binary_sensor.octoprint_printing"
    assert cond["passed"] is False
    assert "off" in cond["observed"]

    # A second identical tick should be debounced (no new row).
    scheduler._tick_once(now + timedelta(seconds=30))
    rows_after = event_log.list(type="conditions")
    assert len(rows_after) == 1, (
        f"identical decision should be debounced; got {len(rows_after)} rows"
    )


def test_autonomous_tick_skips_conditioned_step_in_full_cycle(tmp_path: Path) -> None:
    """End-to-end of the user's reported bug shape: a 4-step rotation
    where the LAST step is gated on an HA entity. Walk the cycle from
    minute 0 through one full lap and assert the gated step never
    fires while the entity says no, but the other three do."""
    rotation = _rotation(
        "trmnl",
        # 5-minute dwells, so step 3 (3D Print) owns the 15-20 min
        # slot of every 20-min cycle.
        steps=[
            RotationStep(page_id="trmnl_todo", dwell_minutes=5),
            RotationStep(page_id="trmnl_gen_image", dwell_minutes=5),
            RotationStep(page_id="trmnl_home_assistant", dwell_minutes=5),
            RotationStep(
                page_id="trmnl_3d_print",
                dwell_minutes=5,
                conditions=[
                    ha_condition("binary_sensor.octoprint_printing", "==", "on"),
                ],
            ),
        ],
        min_hold_minutes=0,  # so transitions land each tick boundary
    )
    scheduler, push = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[
            # Printer is OFF, exactly like the user's screenshot.
            {"entity_id": "binary_sensor.octoprint_printing", "state": "off"},
        ],
    )

    # Walk one full 20-minute cycle plus a bit, ticking every 5 minutes.
    # Step 3's slot is 15-20 min; the picker should walk past it and
    # fire step 0 (TRMNL - Todo) instead each time the slot comes round.
    ticks = [
        datetime(2026, 6, 18, 0, 0, tzinfo=UTC),
        datetime(2026, 6, 18, 0, 5, tzinfo=UTC),
        datetime(2026, 6, 18, 0, 10, tzinfo=UTC),
        datetime(2026, 6, 18, 0, 15, tzinfo=UTC),
        datetime(2026, 6, 18, 0, 20, tzinfo=UTC),
    ]
    for t in ticks:
        scheduler._tick_once(t)

    fired_pages = [page_id for page_id, _src in push.calls]
    # No fire of the gated step.
    assert "trmnl_3d_print" not in fired_pages, (
        f"3D Print step fired despite printer being OFF; calls were {push.calls}"
    )
    # The other three should have all fired at their natural slots, in
    # order: Todo, Gen Image, Home Assistant.
    assert "trmnl_todo" in fired_pages
    assert "trmnl_gen_image" in fired_pages
    assert "trmnl_home_assistant" in fired_pages
    # And when the picker hits step 3's slot but walks to step 0, it
    # may or may not re-fire Todo depending on last_step; either way,
    # 3d_print stays off the list.


def test_rotation_scheduled_mode_returns_none_when_all_fail(tmp_path: Path) -> None:
    rotation = _rotation(
        "kitchen",
        steps=[
            RotationStep(
                page_id="a",
                dwell_minutes=30,
                conditions=[ha_condition("binary_sensor.x", "==", "on")],
            ),
            RotationStep(
                page_id="b",
                dwell_minutes=30,
                conditions=[ha_condition("binary_sensor.y", "==", "on")],
            ),
        ],
    )
    scheduler, _push = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[
            {"entity_id": "binary_sensor.x", "state": "off"},
            {"entity_id": "binary_sensor.y", "state": "off"},
        ],
    )
    assert scheduler._pick_eligible_step(rotation, 0, datetime.now(UTC)) is None


# -- rotation priority-mode routing ------------------------------------


def test_rotation_priority_mode_picks_first_matching(tmp_path: Path) -> None:
    rotation = _rotation(
        "lounge",
        mode="priority",
        steps=[
            RotationStep(
                page_id="storm_warning",
                dwell_minutes=10,
                conditions=[ha_condition("binary_sensor.bom_warning", "==", "on")],
            ),
            RotationStep(
                page_id="solar_dashboard",
                dwell_minutes=10,
                conditions=[ha_condition("sensor.solar_w", ">", 1000)],
            ),
            # No conditions = always-on fallback step
            RotationStep(page_id="calendar", dwell_minutes=10),
        ],
    )
    # First two fail, fallback step wins.
    scheduler, _push = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[
            {"entity_id": "binary_sensor.bom_warning", "state": "off"},
            {"entity_id": "sensor.solar_w", "state": "500"},
        ],
    )
    idx = scheduler._pick_eligible_step(rotation, time_step_index=0, now=datetime.now(UTC))
    assert idx == 2  # calendar fallback
    # Now make the storm warning fire and ensure it preempts the others.
    scheduler2, _ = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[
            {"entity_id": "binary_sensor.bom_warning", "state": "on"},
            {"entity_id": "sensor.solar_w", "state": "5000"},
        ],
    )
    idx2 = scheduler2._pick_eligible_step(rotation, time_step_index=99, now=datetime.now(UTC))
    assert idx2 == 0  # highest-priority match


def test_rotation_priority_mode_holds_when_nothing_matches(tmp_path: Path) -> None:
    rotation = _rotation(
        "lounge",
        mode="priority",
        steps=[
            RotationStep(
                page_id="a",
                dwell_minutes=10,
                conditions=[ha_condition("binary_sensor.x", "==", "on")],
            ),
        ],
    )
    scheduler, _push = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[{"entity_id": "binary_sensor.x", "state": "off"}],
    )
    assert scheduler._pick_eligible_step(rotation, 0, datetime.now(UTC)) is None


# -- min hold gate -----------------------------------------------------


def test_min_hold_gate_blocks_repeat_within_window(tmp_path: Path) -> None:
    """After a successful fire, the gate prevents another transition
    until ``min_hold_minutes`` have elapsed - even if conditions now
    point at a different step."""
    rotation = _rotation(
        "lounge",
        mode="priority",
        min_hold_minutes=5,
        steps=[
            RotationStep(
                page_id="urgent",
                dwell_minutes=10,
                conditions=[ha_condition("binary_sensor.urgent", "==", "on")],
            ),
            RotationStep(page_id="default", dwell_minutes=10),
        ],
    )
    scheduler, push = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[{"entity_id": "binary_sensor.urgent", "state": "off"}],
    )
    now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    # First tick: default step fires.
    due = scheduler.find_due_rotations(now)
    assert len(due) == 1
    scheduler._fire_rotation(due[0][0], due[0][1], now)
    assert push.calls == [("default", "rotation")]
    # Immediately afterwards, urgent flips on. The min-hold gate
    # should suppress the transition.
    scheduler._condition_evaluator._ha_cache["binary_sensor.urgent"] = {  # type: ignore[union-attr]
        "entity_id": "binary_sensor.urgent",
        "state": "on",
    }
    soon = now + timedelta(minutes=2)
    due_soon = scheduler.find_due_rotations(soon)
    assert due_soon == []  # held
    # Past the hold window, the transition fires.
    later = now + timedelta(minutes=6)
    due_later = scheduler.find_due_rotations(later)
    assert len(due_later) == 1
    assert due_later[0][1] == 0  # picked the urgent step


# -- ensure the legacy path stays clean -------------------------------


def test_rotation_without_conditions_or_mode_behaves_like_before(tmp_path: Path) -> None:
    """A rotation saved before 0.48 (no conditions, mode defaults to
    'scheduled') walks through the time-based step index untouched."""
    rotation = _rotation(
        "legacy",
        steps=[
            RotationStep(page_id="a", dwell_minutes=10),
            RotationStep(page_id="b", dwell_minutes=10),
        ],
    )
    scheduler, _ = _scheduler(tmp_path, rotations=[rotation])
    # time_step_index=1 should round-trip through the picker since
    # both steps have empty conditions.
    assert scheduler._pick_eligible_step(rotation, 1, datetime.now(UTC)) == 1
    assert scheduler._pick_eligible_step(rotation, 0, datetime.now(UTC)) == 0


# -- v0.48 running-state pill tracking ---------------------------------


def test_status_records_sent_after_successful_fire(tmp_path: Path) -> None:
    """A normal fire records ``last_status="sent"`` and clears the
    reason so the index pill renders as ``active`` (green check)."""
    s = _hourly_schedule("morning", "dashboard_morning")
    scheduler, _ = _scheduler(tmp_path, schedules=[s])
    scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    snap = scheduler.status()[s.id]
    assert snap["last_status"] == "sent"
    assert snap["last_reason"] is None


def test_status_records_held_when_conditions_fail(tmp_path: Path) -> None:
    """Silent-skip path: status reflects the hold so the Schedules
    page can show ``held`` instead of a stale ``enabled`` pill."""
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
    )
    scheduler, _ = _scheduler(
        tmp_path,
        schedules=[s],
        ha_states=[{"entity_id": "binary_sensor.home", "state": "off"}],
    )
    scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    snap = scheduler.status()[s.id]
    assert snap["last_status"] == "held"
    assert snap["last_reason"] == "conditions not met"


def test_status_records_fallback_when_conditions_route_to_fallback(tmp_path: Path) -> None:
    """When a fallback page absorbs the held fire, the pill says
    ``fallback`` rather than ``active`` so the user knows a different
    page is on the panel than the schedule's primary."""
    s = _hourly_schedule(
        "morning",
        "dashboard_morning",
        conditions=[ha_condition("binary_sensor.home", "==", "on")],
        fallback_page_id="away_message",
    )
    scheduler, _ = _scheduler(
        tmp_path,
        schedules=[s],
        ha_states=[{"entity_id": "binary_sensor.home", "state": "off"}],
    )
    scheduler._fire(s, datetime(2026, 6, 16, 9, 0, tzinfo=UTC))
    snap = scheduler.status()[s.id]
    assert snap["last_status"] == "sent"
    assert snap["last_reason"] is not None
    assert "fallback" in snap["last_reason"]


def test_rotation_status_records_held_when_no_step_eligible(tmp_path: Path) -> None:
    """Every step's conditions fail, so the rotation tick is held.
    ``rotation_status`` should surface that as ``held`` with a
    descriptive reason; the Rotations page renders an extra warm
    held pill alongside the existing "Now / Not active" pill."""
    rotation = _rotation(
        "guarded",
        steps=[
            RotationStep(
                page_id="urgent",
                dwell_minutes=10,
                conditions=[ha_condition("binary_sensor.urgent", "==", "on")],
            ),
            RotationStep(
                page_id="quiet",
                dwell_minutes=10,
                conditions=[ha_condition("binary_sensor.quiet", "==", "on")],
            ),
        ],
    )
    scheduler, _ = _scheduler(
        tmp_path,
        rotations=[rotation],
        ha_states=[
            {"entity_id": "binary_sensor.urgent", "state": "off"},
            {"entity_id": "binary_sensor.quiet", "state": "off"},
        ],
    )
    # find_due_rotations walks every rotation; when no step is eligible
    # it records the held state and skips the fire.
    due = scheduler.find_due_rotations(datetime(2026, 6, 16, 12, 0, tzinfo=UTC))
    assert due == []
    snap = scheduler.rotation_status()[rotation.id]
    assert snap["last_status"] == "held"
    assert snap["last_reason"] is not None
    assert "conditions" in snap["last_reason"]
