"""Scheduler liveness and lineup staleness diagnostics: the failure paths
that used to leave a frozen panel with nothing in the log."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.renderer import CHROMIUM_ARGS, _chromium_launch_kwargs
from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.schedule_store import ScheduleStore

T0 = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)


def _deck(**kw) -> Deck:
    base = dict(
        id="d1",
        name="D",
        device_ids=["panel"],
        advance="timer",
        advance_interval_minutes=30,
        advance_anchor="00:00",
        refresh_interval_minutes=15,
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )
    base.update(kw)
    return Deck(**base)


@pytest.fixture
def wiring(tmp_path: Path):
    deck_store = DeckStore(tmp_path / "decks.json")
    push = MagicMock()
    push.push.return_value = MagicMock(status="sent", error=None, duration_s=0.01, event_id="e")
    push.promote_deck_page.return_value = True
    push.device_in_quiet_hours.return_value = False
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=MagicMock(),
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
    )
    return scheduler, push, deck_store


# -- stale pre-rendered frames -------------------------------------------


def test_fresh_warm_frame_is_promoted(wiring) -> None:
    scheduler, push, store = wiring
    store.upsert(_deck())
    push.deck_render_for.return_value = {"timestamp": T0.timestamp() - 10 * 60}
    scheduler._tick_once(T0)
    push.promote_deck_page.assert_called_once_with("panel", "a")
    push.push.assert_not_called()


def test_stale_warm_frame_renders_live_instead(wiring, caplog) -> None:
    scheduler, push, store = wiring
    store.upsert(_deck())  # warm cadence 15 min, so anything past 30 min is stale
    push.deck_render_for.return_value = {"timestamp": T0.timestamp() - 3 * 3600}
    with caplog.at_level(logging.WARNING, logger="app.scheduler"):
        scheduler._tick_once(T0)
    push.promote_deck_page.assert_not_called()
    assert push.push.call_args[0][0] == "a"
    assert "180 min old" in caplog.text and "rendering live" in caplog.text


def test_page_without_warm_cadence_is_always_promotable(wiring) -> None:
    scheduler, push, store = wiring
    store.upsert(_deck(refresh_interval_minutes=0))
    push.deck_render_for.return_value = {"timestamp": T0.timestamp() - 3 * 3600}
    scheduler._tick_once(T0)
    push.promote_deck_page.assert_called_once_with("panel", "a")
    push.push.assert_not_called()


def test_frame_without_timestamp_is_promoted(wiring) -> None:
    scheduler, push, store = wiring
    store.upsert(_deck())
    push.deck_render_for.return_value = {"composition_digest": "x"}
    scheduler._tick_once(T0)
    push.promote_deck_page.assert_called_once_with("panel", "a")


# -- skipped advances are visible -----------------------------------------


def test_manual_hold_skip_is_logged_and_card_reads_held(wiring, caplog) -> None:
    """A panel inside a button / touch hold is skipped by the timer. That
    used to be silent, so a held panel looked frozen: no advance line, no
    skip line, and the card still showing the last send."""
    scheduler, push, store = wiring
    store.upsert(_deck())
    held_state = MagicMock(
        device_id="panel", rotation_id="d1", override_until=datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    )
    state_store = MagicMock()
    state_store.all.return_value = {"panel": held_state}
    scheduler._rotation_state_store = state_store
    with caplog.at_level(logging.INFO, logger="app.scheduler"):
        scheduler._tick_once(T0)
    assert "deck timer advance skipped: device=panel deck=d1 -> a (manual hold)" in caplog.text
    push.push.assert_not_called()
    push.promote_deck_page.assert_not_called()
    assert scheduler._rotation_last_status["d1"] == "held"
    assert scheduler._rotation_last_reason["d1"] == "all devices manually held"


def test_quiet_hours_skip_is_logged(wiring, caplog) -> None:
    scheduler, push, store = wiring
    push.device_in_quiet_hours.return_value = True
    store.upsert(_deck())
    with caplog.at_level(logging.INFO, logger="app.scheduler"):
        scheduler._tick_once(T0)
    assert "deck timer advance skipped: device=panel deck=d1 -> a (quiet hours)" in caplog.text


# -- tick liveness ---------------------------------------------------------


def test_slow_tick_is_logged(wiring, caplog) -> None:
    scheduler, _push, _store = wiring
    with caplog.at_level(logging.WARNING, logger="app.scheduler"):
        scheduler._note_tick_duration(1.0)
        assert "scheduler tick took" not in caplog.text
        scheduler._note_tick_duration(scheduler._tick + 5.0)
    assert "scheduler tick took 35.0s" in caplog.text


def test_stuck_tick_warns_then_repeats_slowly(wiring, caplog) -> None:
    scheduler, _push, _store = wiring
    with caplog.at_level(logging.WARNING, logger="app.scheduler"):
        assert scheduler._check_stuck_tick(1000.0) is False  # nothing running
        scheduler._tick_started_at = 1000.0
        assert scheduler._check_stuck_tick(1000.0 + 60) is False  # under threshold
        assert scheduler._check_stuck_tick(1000.0 + 301) is True
        assert scheduler._check_stuck_tick(1000.0 + 400) is False  # repeat window
        assert scheduler._check_stuck_tick(1000.0 + 301 + 900) is True
    assert caplog.text.count("scheduler tick has been running for") == 2
    assert "301s" in caplog.text


# -- Chromium launch -------------------------------------------------------


def test_chromium_launches_docker_safe(monkeypatch) -> None:
    monkeypatch.delenv("TESSERAE_CHROMIUM_PATH", raising=False)
    kwargs = _chromium_launch_kwargs()
    assert "--disable-dev-shm-usage" in kwargs["args"]
    assert "--disable-dev-shm-usage" in CHROMIUM_ARGS
    monkeypatch.setenv("TESSERAE_CHROMIUM_PATH", "/opt/chrome")
    kwargs = _chromium_launch_kwargs()
    assert kwargs["executable_path"] == "/opt/chrome"
    assert "--disable-dev-shm-usage" in kwargs["args"]
