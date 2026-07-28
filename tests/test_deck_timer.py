"""Timer-advance decks (Phase 1 of the rotations merge): the model's cycle math
and the scheduler branch that walks a deck's pages on a wall-clock anchor."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.schedule_store import ScheduleStore

# -- model ---------------------------------------------------------------


def _deck(**kw) -> Deck:
    base = dict(
        id="d1",
        name="D",
        device_ids=["panel"],
        advance="timer",
        advance_interval_minutes=30,
        advance_anchor="00:00",
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )
    base.update(kw)
    return Deck(**base)


def test_advance_defaults_manual_and_backcompat() -> None:
    d = Deck(id="d", name="D", pages=[DeckPage(page_id="a")])
    assert d.advance == "manual"
    assert d.advance_interval_minutes == 30 and d.advance_anchor == "00:00"


def test_advance_cycle_uses_interval_then_dwell() -> None:
    d = _deck()  # 2 pages, 30 min each
    assert d.advance_cycle_minutes == 60
    d2 = _deck(pages=[DeckPage(page_id="a", dwell_minutes=10), DeckPage(page_id="b")])
    assert d2.advance_cycle_minutes == 40  # 10 + 30


def test_advance_page_at_buckets_and_wraps() -> None:
    d = _deck()  # a: [0,30), b: [30,60)
    assert d.advance_page_at(0) == "a"
    assert d.advance_page_at(29) == "a"
    assert d.advance_page_at(30) == "b"
    assert d.advance_page_at(59) == "b"
    assert d.advance_page_at(60) == "a"  # wraps
    assert d.advance_page_at(90) == "b"


def test_bad_anchor_rejected() -> None:
    with pytest.raises(ValidationError):
        _deck(advance_anchor="9:5")


# -- scheduler branch ----------------------------------------------------


@pytest.fixture
def wiring(tmp_path: Path):
    deck_store = DeckStore(tmp_path / "decks.json")
    nav = MagicMock()
    push = MagicMock()
    push.push.return_value = MagicMock(status="sent", error=None, duration_s=0.01, event_id="e")
    push.promote_deck_page.return_value = False  # force the push path for assertions
    push.device_in_quiet_hours.return_value = False
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=nav,
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,  # deterministic across host TZ
    )
    return scheduler, push, deck_store, nav


def test_timer_deck_fires_current_step_then_holds_then_advances(wiring) -> None:
    scheduler, push, store, nav = wiring
    store.upsert(_deck())
    # first tick at the anchor -> step a
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    assert push.push.call_args[0][0] == "a"
    nav.set.assert_called_with("panel", "d1", "a")
    # within the dwell -> no re-push
    push.push.reset_mock()
    scheduler._tick_once(datetime(2026, 6, 15, 0, 10, tzinfo=UTC))
    push.push.assert_not_called()
    # boundary at 00:30 -> advance to b
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))
    assert push.push.call_args[0][0] == "b"


def test_manual_deck_is_not_advanced(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_deck(advance="manual"))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    push.push.assert_not_called()


def test_quiet_hours_skips_advance(wiring) -> None:
    scheduler, push, store, _nav = wiring
    push.device_in_quiet_hours.return_value = True
    store.upsert(_deck())
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    push.push.assert_not_called()


# -- parity: windows, min-hold (Tier A + B via the rotation engine) -------


def test_timer_deck_off_day_does_not_advance(wiring) -> None:
    scheduler, push, store, _nav = wiring
    dow = datetime(2026, 6, 15).weekday()
    store.upsert(_deck(advance_days_of_week=[d for d in range(7) if d != dow]))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    push.push.assert_not_called()


def test_timer_deck_past_end_at_window_does_not_advance(wiring) -> None:
    scheduler, push, store, _nav = wiring
    store.upsert(_deck(advance_end_at="00:15"))  # cycle stops after 00:15
    scheduler._tick_once(datetime(2026, 6, 15, 0, 20, tzinfo=UTC))
    push.push.assert_not_called()


def test_timer_deck_min_hold_blocks_early_transition(wiring) -> None:
    scheduler, push, store, _nav = wiring
    # 30-min steps but a 60-min min hold: the boundary transition to b is held.
    store.upsert(_deck(advance_min_hold_minutes=60))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))
    assert push.push.call_args[0][0] == "a"
    push.push.reset_mock()
    scheduler._tick_once(datetime(2026, 6, 15, 0, 30, tzinfo=UTC))  # within min-hold
    push.push.assert_not_called()


def test_deck_to_rotation_rejects_overlong_cycle() -> None:
    from app.scheduler import _deck_to_rotation

    big = _deck(
        pages=[
            DeckPage(page_id="a", dwell_minutes=10_000),
            DeckPage(page_id="b", dwell_minutes=10_000),
        ]
    )
    assert _deck_to_rotation(big) is None
