"""Interval / daily trigger decks (#167 Phase 2a): the migrated-schedule
shapes. Cadence parity with Schedule(type="interval"/"daily"): drifting
cooldown inside a wrap-around window, once-per-local-day with the backfill
guard, whole-deck fallback when conditions fail, and the unbound
fire-to-page-devices path. Plus the pure Rotation/Schedule -> Deck mappings."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.scheduler import Scheduler, _deck_to_rotation
from app.state.deck_migration import rotation_to_deck, schedule_to_deck
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore


def _interval_deck(**kw) -> Deck:
    base = dict(
        id="i1",
        name="Interval",
        device_ids=[],
        pages=[DeckPage(page_id="a")],
        advance="timer",
        advance_trigger="interval",
        advance_interval_minutes=30,
        advance_min_hold_minutes=0,
    )
    base.update(kw)
    return Deck(**base)


def _daily_deck(**kw) -> Deck:
    base = dict(
        id="d1",
        name="Daily",
        device_ids=[],
        pages=[DeckPage(page_id="a")],
        advance="timer",
        advance_trigger="daily",
        advance_fires_at="07:00",
        advance_min_hold_minutes=0,
    )
    base.update(kw)
    return Deck(**base)


@pytest.fixture
def wiring(tmp_path: Path):
    deck_store = DeckStore(tmp_path / "decks.json")
    nav = MagicMock()
    push = MagicMock()
    push.push.return_value = MagicMock(status="sent", error=None, duration_s=0.01, event_id="e")
    push.promote_deck_page.return_value = False
    push.device_in_quiet_hours.return_value = False
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=nav,
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
    )
    return scheduler, push, deck_store, nav


# -- model ---------------------------------------------------------------


def test_daily_trigger_requires_fires_at() -> None:
    with pytest.raises(ValidationError):
        _daily_deck(advance_fires_at=None)


def test_trigger_times_validated_as_hhmm() -> None:
    with pytest.raises(ValidationError):
        _daily_deck(advance_fires_at="7:00")
    with pytest.raises(ValidationError):
        _interval_deck(advance_window_start="24:00")


def test_cycle_default_keeps_existing_decks_unchanged() -> None:
    d = Deck(id="d", name="D", pages=[DeckPage(page_id="a")])
    assert d.advance_trigger == "cycle"
    assert d.legacy_kind is None
    assert d.advance_fallback_page_id is None


# -- interval trigger -----------------------------------------------------


def test_interval_deck_fires_on_drifting_cooldown(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_interval_deck())
    # First due tick fires, unbound -> one schedule-style push to the page.
    scheduler._tick_once(datetime(2026, 6, 15, 10, 7, tzinfo=UTC))
    assert push.push.call_count == 1
    assert push.push.call_args[0][0] == "a"
    assert "device_ids" not in push.push.call_args.kwargs
    # Inside the cooldown: nothing.
    scheduler._tick_once(datetime(2026, 6, 15, 10, 20, tzinfo=UTC))
    assert push.push.call_count == 1
    # Cooldown counts from the LAST FIRE (10:07), not an anchor boundary.
    scheduler._tick_once(datetime(2026, 6, 15, 10, 37, tzinfo=UTC))
    assert push.push.call_count == 2


def test_interval_deck_respects_wraparound_window(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_interval_deck(advance_window_start="22:00", advance_window_end="06:00"))
    scheduler._tick_once(datetime(2026, 6, 15, 12, 0, tzinfo=UTC))  # outside
    assert push.push.call_count == 0
    scheduler._tick_once(datetime(2026, 6, 15, 23, 0, tzinfo=UTC))  # inside, pre-midnight
    assert push.push.call_count == 1
    scheduler._tick_once(datetime(2026, 6, 16, 5, 0, tzinfo=UTC))  # inside, post-midnight
    assert push.push.call_count == 2


def test_interval_deck_failed_push_retries_next_tick(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_interval_deck())
    push.push.return_value = MagicMock(status="failed", error="boom", duration_s=0.0, event_id="e")
    scheduler._tick_once(datetime(2026, 6, 15, 10, 0, tzinfo=UTC))
    push.push.return_value = MagicMock(status="sent", error=None, duration_s=0.01, event_id="e")
    # Failure did not arm the cooldown; the very next tick retries.
    scheduler._tick_once(datetime(2026, 6, 15, 10, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 2


def test_bound_interval_deck_targets_its_devices_and_arms_cooldown(wiring) -> None:
    # #167 decommission: bound trigger decks fire through the schedule
    # engine with a device filter (one targeted push, no nav record; nav is
    # a cycle/navigation concept).
    scheduler, push, store, nav = wiring
    store.upsert(_interval_deck(device_ids=["panel"]))
    scheduler._tick_once(datetime(2026, 6, 15, 10, 0, tzinfo=UTC))
    assert push.push.call_args.kwargs.get("device_ids") == {"panel"}
    nav.set.assert_not_called()
    scheduler._tick_once(datetime(2026, 6, 15, 10, 10, tzinfo=UTC))
    assert push.push.call_count == 1  # cooldown armed by the fire


# -- daily trigger --------------------------------------------------------


def test_daily_deck_fires_once_per_day_after_target(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_daily_deck())
    # Observed before today's target: eligible when the clock passes it.
    scheduler._tick_once(datetime(2026, 6, 15, 6, 0, tzinfo=UTC))
    assert push.push.call_count == 0
    scheduler._tick_once(datetime(2026, 6, 15, 7, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 1
    scheduler._tick_once(datetime(2026, 6, 15, 9, 0, tzinfo=UTC))
    assert push.push.call_count == 1  # once per day
    scheduler._tick_once(datetime(2026, 6, 16, 7, 1, tzinfo=UTC))
    assert push.push.call_count == 2  # next day fires again


def test_daily_deck_backfill_guard_skips_missed_target(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_daily_deck())
    # First observation is AFTER today's 07:00: don't fire the stale slot.
    scheduler._tick_once(datetime(2026, 6, 15, 11, 0, tzinfo=UTC))
    assert push.push.call_count == 0
    scheduler._tick_once(datetime(2026, 6, 16, 7, 0, 30, tzinfo=UTC))
    assert push.push.call_count == 1  # tomorrow's slot fires normally


def test_daily_deck_respects_days_of_week(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_daily_deck(advance_days_of_week=[0]))  # Mondays only
    scheduler._tick_once(datetime(2026, 6, 16, 7, 30, tzinfo=UTC))  # a Tuesday
    assert push.push.call_count == 0
    scheduler._tick_once(datetime(2026, 6, 22, 6, 0, tzinfo=UTC))  # Monday, pre-target
    scheduler._tick_once(datetime(2026, 6, 22, 7, 30, tzinfo=UTC))  # Monday, post-target
    assert push.push.call_count == 1


# -- conditions + fallback ------------------------------------------------


def test_failing_conditions_route_to_fallback_page(tmp_path: Path) -> None:
    deck_store = DeckStore(tmp_path / "decks.json")
    push = MagicMock()
    push.push.return_value = MagicMock(status="sent", error=None, duration_s=0.01, event_id="e")
    evaluator = MagicMock()
    evaluator.all_pass.return_value = False  # every page's conditions fail
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=MagicMock(),
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
        condition_evaluator=evaluator,
    )
    deck_store.upsert(
        _interval_deck(
            pages=[
                DeckPage(
                    page_id="a",
                    conditions=[
                        {
                            "source_kind": "ha_entity",
                            "source_id": "binary_sensor.home",
                            "operator": "==",
                            "value": "on",
                        }
                    ],
                )
            ],
            advance_fallback_page_id="fallback",
        )
    )
    scheduler._tick_once(datetime(2026, 6, 15, 10, 0, tzinfo=UTC))
    assert push.push.call_args[0][0] == "fallback"


def test_failing_conditions_without_fallback_hold(tmp_path: Path) -> None:
    deck_store = DeckStore(tmp_path / "decks.json")
    push = MagicMock()
    evaluator = MagicMock()
    evaluator.all_pass.return_value = False
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=MagicMock(),
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
        condition_evaluator=evaluator,
    )
    deck_store.upsert(
        _interval_deck(
            pages=[
                DeckPage(
                    page_id="a",
                    conditions=[
                        {
                            "source_kind": "ha_entity",
                            "source_id": "binary_sensor.home",
                            "operator": "==",
                            "value": "on",
                        }
                    ],
                )
            ]
        )
    )
    scheduler._tick_once(datetime(2026, 6, 15, 10, 0, tzinfo=UTC))
    push.push.assert_not_called()


# -- legacy mappings ------------------------------------------------------


def test_rotation_round_trips_through_deck() -> None:
    rotation = Rotation(
        id="morning",
        name="Morning",
        device_ids=["panel"],
        steps=[
            RotationStep(page_id="a", dwell_minutes=15),
            RotationStep(page_id="b", dwell_minutes=45),
        ],
        anchor="06:30",
        end_at="22:00",
        days_of_week=[0, 1, 2, 3, 4],
        priority=3,
        smart_sync=True,
        smart_sync_lead_s=20,
        mode="priority",
        min_hold_minutes=10,
    )
    deck = rotation_to_deck(rotation)
    assert deck is not None
    assert deck.legacy_kind == "rotation"
    assert deck.advance == "timer" and deck.advance_trigger == "cycle"
    # The engine adapter reproduces the original rotation exactly.
    assert _deck_to_rotation(deck) == rotation


def test_rotation_with_repeated_page_maps_losslessly() -> None:
    rotation = Rotation(
        id="r",
        name="R",
        steps=[
            RotationStep(page_id="a", dwell_minutes=15),
            RotationStep(page_id="b", dwell_minutes=15),
            RotationStep(page_id="a", dwell_minutes=15),
        ],
    )
    deck = rotation_to_deck(rotation)
    assert [p.page_id for p in deck.pages] == ["a", "b", "a"]
    assert _deck_to_rotation(deck) == rotation


def test_interval_schedule_maps_to_interval_deck() -> None:
    schedule = Schedule(
        id="hourly",
        name="Hourly",
        page_id="dash",
        type="interval",
        interval_minutes=60,
        time_of_day_start="08:00",
        time_of_day_end="20:00",
        days_of_week=[5, 6],
        priority=2,
        smart_sync=True,
        fallback_page_id="fb",
    )
    deck = schedule_to_deck(schedule)
    assert deck.legacy_kind == "schedule"
    assert deck.advance_trigger == "interval"
    assert deck.advance_interval_minutes == 60
    assert deck.advance_window_start == "08:00" and deck.advance_window_end == "20:00"
    assert deck.advance_days_of_week == [5, 6]
    assert deck.advance_fallback_page_id == "fb"
    assert deck.device_ids == [] and deck.page_ids == ["dash"]
    assert deck.advance_min_hold_minutes == 0
    assert deck.refresh_interval_minutes == 0


def test_daily_schedule_maps_to_daily_deck() -> None:
    schedule = Schedule(
        id="morning",
        name="Morning",
        page_id="dash",
        type="daily",
        fires_at=datetime(2026, 1, 1, 7, 30),
        conditions=[
            {
                "source_kind": "ha_entity",
                "source_id": "person.kayden",
                "operator": "==",
                "value": "home",
            }
        ],
    )
    deck = schedule_to_deck(schedule)
    assert deck.advance_trigger == "daily"
    assert deck.advance_fires_at == "07:30"
    assert deck.pages[0].conditions[0].source_id == "person.kayden"
