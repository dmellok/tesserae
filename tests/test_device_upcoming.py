"""The device timeline projection (#232).

These tests pin the part of the feature that is easy to get plausibly wrong:
the gates. A projection that walks a dwell grid is trivial; one that agrees
with the scheduler about minimum hold, backfill suppression, quiet hours, and
which panel a fire actually reaches is the whole point, and every case below
exists because the naive answer differs from the engine's.

Pure inputs, no app and no scheduler: the arithmetic module takes a snapshot
and returns events, so the cases read as "given this runtime state, this is
what the glass does next".
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from app.device_upcoming import (
    CycleRecord,
    HomeReturnRecord,
    ProjectionInputs,
    TimedRecord,
    project_upcoming,
)
from app.quiet_hours import QuietHoursWindow
from app.state.conditions import Condition
from app.state.rotation_model import Rotation, RotationStep
from app.state.schedule_model import Schedule

NOW = datetime(2026, 8, 17, 11, 10, tzinfo=UTC)
PAGE_NAMES = {
    "todo": "TO-DO list",
    "weather": "Hong Kong Observatory",
    "home": "Home",
    "news": "News",
}


def _inputs(**overrides: object) -> ProjectionInputs:
    fields: dict[str, object] = {
        "device_id": "black_picpak",
        "now": NOW,
        "through": NOW + timedelta(hours=24),
        "tz": UTC,
        "page_names": PAGE_NAMES,
    }
    fields.update(overrides)
    return ProjectionInputs(**fields)  # type: ignore[arg-type]


def _cycle(**overrides: object) -> Rotation:
    fields: dict[str, object] = {
        "id": "playroom_deck",
        "name": "Playroom Deck",
        "device_ids": ["black_picpak"],
        "steps": [
            RotationStep(page_id="todo", dwell_minutes=30),
            RotationStep(page_id="weather", dwell_minutes=30),
        ],
        "anchor": "00:00",
        "min_hold_minutes": 0,
    }
    fields.update(overrides)
    return Rotation(**fields)  # type: ignore[arg-type]


def _at(hour: int, minute: int, day: int = 17) -> float:
    return datetime(2026, 8, day, hour, minute, tzinfo=UTC).timestamp()


# -- cycle records -------------------------------------------------------


def test_a_cycle_reports_its_dwell_boundaries_soonest_first() -> None:
    events = project_upcoming(
        _inputs(
            cycles=[
                CycleRecord(
                    rotation=_cycle(),
                    last_step=0,
                    last_pushed_at=_at(11, 0),
                    last_window_start=_at(11, 0),
                )
            ],
            current_page_id="todo",
        ),
        limit=3,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T11:30:00+00:00",
        "2026-08-17T12:00:00+00:00",
        "2026-08-17T12:30:00+00:00",
    ]
    assert [e.dashboard_id for e in events] == ["weather", "todo", "weather"]
    assert [e.effect for e in events] == ["change_screen"] * 3
    assert {e.cause for e in events} == {"cycle"}
    assert {e.certainty for e in events} == {"scheduled"}
    assert events[0].dashboard_name == "Hong Kong Observatory"
    assert events[0].lineup_name == "Playroom Deck"


def test_the_event_id_names_the_display_record_and_instant() -> None:
    """Re-reading an unchanged schedule has to yield the same ids, or the
    client can't tell a re-read from a new update."""
    events = project_upcoming(
        _inputs(cycles=[CycleRecord(rotation=_cycle(), last_step=0, last_pushed_at=_at(11, 0))]),
        limit=1,
    )
    assert events[0].event_id("black_picpak") == "black_picpak:playroom_deck:20260817T113000Z"


def test_a_single_step_cycle_repaints_rather_than_changes() -> None:
    """The "keep one dashboard fresh" shape: same dashboard, new render.
    Reporting it as a change would promise the panel something else."""
    rotation = _cycle(steps=[RotationStep(page_id="weather", dwell_minutes=15)])
    events = project_upcoming(
        _inputs(
            cycles=[
                CycleRecord(
                    rotation=rotation,
                    last_step=0,
                    last_pushed_at=_at(11, 0),
                    last_window_start=_at(11, 0),
                )
            ],
            current_page_id="weather",
        ),
        limit=2,
    )
    assert [(e.effect, e.dashboard_id) for e in events] == [
        ("refresh_screen", "weather"),
        ("refresh_screen", "weather"),
    ]


def test_the_window_already_running_is_reported_when_it_has_not_fired() -> None:
    """After a restart or a re-enable the next tick fires the current step,
    so "about to change" is the true answer, not the boundary after it."""
    events = project_upcoming(
        _inputs(cycles=[CycleRecord(rotation=_cycle(), last_step=None)], current_page_id="weather"),
        limit=1,
    )
    assert events[0].scheduled_at == NOW
    assert events[0].dashboard_id == "todo"
    assert events[0].effect == "change_screen"


def test_minimum_hold_swallows_a_boundary_the_scheduler_would_swallow() -> None:
    """The hold is measured window-to-window, so a 45-minute hold over
    30-minute dwells advances every other window, exactly as the engine
    does. A naive projection reports all of them."""
    events = project_upcoming(
        _inputs(
            cycles=[
                CycleRecord(
                    rotation=_cycle(min_hold_minutes=45),
                    last_step=0,
                    last_pushed_at=_at(11, 0) + 7,  # the tick landed just inside its window
                    last_window_start=_at(11, 0),
                )
            ]
        ),
        limit=3,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T12:00:00+00:00",
        "2026-08-17T13:00:00+00:00",
        "2026-08-17T14:00:00+00:00",
    ]


def test_a_conditional_cycle_is_flagged_and_a_priority_one_hides_its_target() -> None:
    """Conditions only skip a step in scheduled mode, so the time-based one
    is still the honest answer. In priority mode they choose it outright,
    and naming a dashboard would be a guess the card renders as fact."""
    condition = Condition(
        source_kind="ha_entity", source_id="binary_sensor.home", operator="==", value="on"
    )
    scheduled = _cycle(
        steps=[
            RotationStep(page_id="todo", dwell_minutes=30, conditions=[condition]),
            RotationStep(page_id="weather", dwell_minutes=30),
        ]
    )
    events = project_upcoming(
        _inputs(cycles=[CycleRecord(rotation=scheduled, last_step=0, last_pushed_at=_at(11, 0))]),
        limit=1,
    )
    assert (events[0].certainty, events[0].dashboard_id) == ("conditional", "weather")

    priority = scheduled.model_copy(update={"mode": "priority"})
    events = project_upcoming(
        _inputs(cycles=[CycleRecord(rotation=priority, last_step=0, last_pushed_at=_at(11, 0))]),
        limit=1,
    )
    assert (events[0].certainty, events[0].dashboard_id, events[0].dashboard_name) == (
        "conditional",
        None,
        None,
    )


def test_smart_sync_makes_the_timing_an_estimate() -> None:
    events = project_upcoming(
        _inputs(
            cycles=[
                CycleRecord(
                    rotation=_cycle(smart_sync=True), last_step=0, last_pushed_at=_at(11, 0)
                )
            ]
        ),
        limit=1,
    )
    assert events[0].certainty == "estimated"


def test_a_step_bound_to_another_panel_advances_without_repainting_this_one() -> None:
    """An unbound cycle falls through to dashboard bindings, so a panel can
    be on the receiving end of some steps and not others."""
    rotation = _cycle(device_ids=[])
    events = project_upcoming(
        _inputs(
            cycles=[
                CycleRecord(
                    rotation=rotation,
                    last_step=0,
                    last_pushed_at=_at(11, 0),
                    last_window_start=_at(11, 0),
                    device_pages=frozenset({"todo"}),
                )
            ]
        ),
        limit=2,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T12:00:00+00:00",
        "2026-08-17T13:00:00+00:00",
    ]
    assert {e.dashboard_id for e in events} == {"todo"}


def test_a_cycle_outside_its_days_resumes_at_the_next_anchor() -> None:
    """17 Aug 2026 is a Monday; a weekend-only cycle wakes on Saturday."""
    rotation = _cycle(days_of_week=[5, 6], anchor="08:00")
    events = project_upcoming(
        _inputs(
            through=NOW + timedelta(days=7),
            cycles=[CycleRecord(rotation=rotation)],
        ),
        limit=1,
    )
    assert events[0].scheduled_at.isoformat() == "2026-08-22T08:00:00+00:00"


# -- timed records -------------------------------------------------------


def _interval(**overrides: object) -> Schedule:
    fields: dict[str, object] = {
        "id": "fridge",
        "name": "Fridge",
        "page_id": "todo",
        "type": "interval",
        "interval_minutes": 15,
    }
    fields.update(overrides)
    return Schedule(**fields)  # type: ignore[arg-type]


def test_an_interval_record_counts_from_its_last_fire() -> None:
    events = project_upcoming(
        _inputs(
            timed=[TimedRecord(schedule=_interval(), last_fired=_at(11, 5))],
            current_page_id="todo",
        ),
        limit=2,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T11:20:00+00:00",
        "2026-08-17T11:35:00+00:00",
    ]
    assert [e.cause for e in events] == ["interval", "interval"]
    assert [e.effect for e in events] == ["refresh_screen", "refresh_screen"]


def test_an_interval_record_outside_its_window_resumes_at_the_edge() -> None:
    """The cooldown has long lapsed by the time the window reopens, so the
    first tick inside it fires. Stepping blindly by the interval would put
    the next update in the middle of the closed window."""
    schedule = _interval(interval_minutes=60, time_of_day_start="18:00", time_of_day_end="22:00")
    events = project_upcoming(
        _inputs(timed=[TimedRecord(schedule=schedule, last_fired=_at(10, 0))]),
        limit=2,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T18:00:00+00:00",
        "2026-08-17T19:00:00+00:00",
    ]


def test_a_daily_record_fires_once_a_day() -> None:
    schedule = Schedule(
        id="morning",
        name="Morning",
        page_id="news",
        type="daily",
        fires_at=datetime(2026, 1, 1, 18, 0, tzinfo=UTC),
    )
    events = project_upcoming(
        _inputs(through=NOW + timedelta(days=3), timed=[TimedRecord(schedule=schedule)]),
        limit=3,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T18:00:00+00:00",
        "2026-08-18T18:00:00+00:00",
        "2026-08-19T18:00:00+00:00",
    ]
    assert [e.cause for e in events] == ["daily"] * 3


def test_a_daily_record_first_seen_after_todays_target_waits_for_tomorrow() -> None:
    """The engine suppresses backfill: enabling an 07:00 daily at 11:00
    doesn't fire today. A projection that ignores first_seen would put an
    update on the card that will never happen."""
    schedule = Schedule(
        id="morning",
        name="Morning",
        page_id="news",
        type="daily",
        fires_at=datetime(2026, 1, 1, 7, 0, tzinfo=UTC),
    )
    events = project_upcoming(
        _inputs(
            through=NOW + timedelta(days=2),
            timed=[TimedRecord(schedule=schedule, first_seen=_at(9, 0))],
        ),
        limit=2,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-18T07:00:00+00:00",
        "2026-08-19T07:00:00+00:00",
    ]


def test_a_daily_record_due_now_is_reported_as_due_now() -> None:
    schedule = Schedule(
        id="morning",
        name="Morning",
        page_id="news",
        type="daily",
        fires_at=datetime(2026, 1, 1, 7, 0, tzinfo=UTC),
    )
    events = project_upcoming(
        _inputs(timed=[TimedRecord(schedule=schedule, first_seen=_at(6, 0))]),
        limit=1,
    )
    assert events[0].scheduled_at == NOW


def test_a_fallback_dashboard_makes_the_target_unknowable() -> None:
    condition = Condition(
        source_kind="ha_entity", source_id="binary_sensor.home", operator="==", value="on"
    )
    schedule = _interval(conditions=[condition], fallback_page_id="weather")
    events = project_upcoming(
        _inputs(timed=[TimedRecord(schedule=schedule, last_fired=_at(11, 5))]),
        limit=1,
    )
    assert (events[0].certainty, events[0].dashboard_id) == ("conditional", None)
    assert events[0].effect == "change_screen"


# -- quiet hours ---------------------------------------------------------


def test_updates_inside_quiet_hours_are_left_out() -> None:
    """A fire that reaches a quiet panel repaints nothing, and the endpoint
    only describes what the glass does."""
    night = datetime(2026, 8, 17, 21, 30, tzinfo=UTC)
    events = project_upcoming(
        ProjectionInputs(
            device_id="black_picpak",
            now=night,
            through=night + timedelta(hours=12),
            tz=UTC,
            page_names=PAGE_NAMES,
            timed=[
                TimedRecord(
                    schedule=_interval(interval_minutes=60),
                    last_fired=night.timestamp() - 3540,
                )
            ],
            quiet_window=QuietHoursWindow(time(22, 0), time(7, 0)),
        ),
        limit=5,
    )
    assert [e.scheduled_at.isoformat() for e in events] == [
        "2026-08-17T21:31:00+00:00",
        "2026-08-18T07:31:00+00:00",
        "2026-08-18T08:31:00+00:00",
    ]


# -- home return ---------------------------------------------------------


def test_home_return_lands_one_timeout_after_the_last_interaction() -> None:
    events = project_upcoming(
        _inputs(
            home_returns=[
                HomeReturnRecord(
                    deck_id="kitchen",
                    deck_name="Kitchen Deck",
                    home_page_id="home",
                    idle_since=_at(11, 0),
                    timeout_minutes=20,
                )
            ],
            current_page_id="weather",
        )
    )
    assert [e.scheduled_at.isoformat() for e in events] == ["2026-08-17T11:20:00+00:00"]
    assert (events[0].cause, events[0].effect, events[0].dashboard_name) == (
        "home_return",
        "change_screen",
        "Home",
    )


def test_home_return_due_inside_quiet_hours_waits_for_the_window_to_end() -> None:
    """The return pass checks quiet hours before touching the live slot and
    retries every tick, so it lands at the edge rather than being dropped."""
    night = datetime(2026, 8, 17, 22, 30, tzinfo=UTC)
    events = project_upcoming(
        ProjectionInputs(
            device_id="black_picpak",
            now=night,
            through=night + timedelta(hours=12),
            tz=UTC,
            page_names=PAGE_NAMES,
            home_returns=[
                HomeReturnRecord(
                    deck_id="kitchen",
                    deck_name="Kitchen Deck",
                    home_page_id="home",
                    idle_since=night.timestamp() - 600,
                    timeout_minutes=20,
                )
            ],
            quiet_window=QuietHoursWindow(time(22, 0), time(7, 0)),
        )
    )
    assert [e.scheduled_at.isoformat() for e in events] == ["2026-08-18T07:01:00+00:00"]


def test_an_overdue_home_return_is_reported_as_due_now() -> None:
    events = project_upcoming(
        _inputs(
            home_returns=[
                HomeReturnRecord(
                    deck_id="kitchen",
                    deck_name="Kitchen Deck",
                    home_page_id="home",
                    idle_since=_at(9, 0),
                    timeout_minutes=20,
                )
            ]
        )
    )
    assert events[0].scheduled_at == NOW


# -- coalescing ----------------------------------------------------------


def test_two_records_due_together_report_the_one_that_wins_the_panel() -> None:
    """The tick fires its whole due list and e-ink keeps the last frame to
    land. Listing both would describe the scheduler, not the display."""
    schedule = Schedule(
        id="morning",
        name="Morning",
        page_id="news",
        type="daily",
        fires_at=datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
        priority=5,
    )
    events = project_upcoming(
        _inputs(
            cycles=[CycleRecord(rotation=_cycle(), last_step=0, last_pushed_at=_at(11, 0))],
            timed=[TimedRecord(schedule=schedule, first_seen=_at(0, 0))],
        ),
        limit=2,
    )
    assert [(e.scheduled_at.isoformat(), e.cause, e.dashboard_id) for e in events] == [
        ("2026-08-17T11:30:00+00:00", "daily", "news"),
        ("2026-08-17T12:00:00+00:00", "cycle", "todo"),
    ]


def test_a_lower_priority_record_does_not_win_a_shared_tick() -> None:
    schedule = Schedule(
        id="morning",
        name="Morning",
        page_id="news",
        type="daily",
        fires_at=datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
        priority=-1,
    )
    events = project_upcoming(
        _inputs(
            cycles=[CycleRecord(rotation=_cycle(), last_step=0, last_pushed_at=_at(11, 0))],
            timed=[TimedRecord(schedule=schedule, first_seen=_at(0, 0))],
        ),
        limit=1,
    )
    assert (events[0].cause, events[0].dashboard_id) == ("cycle", "weather")


def test_a_disabled_record_projects_nothing() -> None:
    assert (
        project_upcoming(
            _inputs(
                cycles=[CycleRecord(rotation=_cycle(enabled=False))],
                timed=[TimedRecord(schedule=_interval(enabled=False))],
            )
        )
        == []
    )


def test_the_horizon_bounds_the_answer() -> None:
    events = project_upcoming(
        _inputs(
            through=NOW + timedelta(minutes=40),
            cycles=[CycleRecord(rotation=_cycle(), last_step=0, last_pushed_at=_at(11, 0))],
        ),
        limit=20,
    )
    assert [e.scheduled_at.isoformat() for e in events] == ["2026-08-17T11:30:00+00:00"]


def test_a_display_quiet_all_day_gets_no_home_return_at_all() -> None:
    """A minute past the end of a 00:00-23:59 window is still inside it.
    Reporting the next midnight would put an update on the card that the
    return pass will refuse every tick for as long as the setting stands."""
    events = project_upcoming(
        _inputs(
            home_returns=[
                HomeReturnRecord(
                    deck_id="kitchen",
                    deck_name="Kitchen Deck",
                    home_page_id="home",
                    idle_since=_at(11, 0),
                    timeout_minutes=20,
                )
            ],
            quiet_window=QuietHoursWindow(time(0, 0), time(23, 59)),
        )
    )
    assert events == []
