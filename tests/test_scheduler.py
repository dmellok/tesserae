"""Scheduler unit tests: due-detection rules, window filters, backfill
suppression, factory pickup of replaced PushManager."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.push import PushResult
from app.scheduler import Scheduler
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore


@pytest.fixture
def store(tmp_path: Path) -> ScheduleStore:
    return ScheduleStore(tmp_path / "schedules.json")


@pytest.fixture
def push_manager():
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home", duration_s=0.0)
    return pm


@pytest.fixture
def scheduler(store: ScheduleStore, push_manager) -> Scheduler:
    return Scheduler(store=store, push_manager=lambda: push_manager)


def test_interval_fires_when_cooldown_elapsed(scheduler: Scheduler, store: ScheduleStore) -> None:
    s = Schedule(id="a", name="A", page_id="home", type="interval", interval_minutes=15)
    store.upsert(s)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    due = scheduler.find_due(now)
    assert [d.id for d in due] == ["a"]


def test_interval_not_due_within_cooldown(
    scheduler: Scheduler, store: ScheduleStore, push_manager
) -> None:
    s = Schedule(id="a", name="A", page_id="home", type="interval", interval_minutes=15)
    store.upsert(s)
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    scheduler.run_due_once(now)
    later = now + timedelta(minutes=5)
    assert scheduler.find_due(later) == []
    assert push_manager.push.call_count == 1


def test_interval_respects_window(scheduler: Scheduler, store: ScheduleStore) -> None:
    s = Schedule(
        id="a",
        name="A",
        page_id="home",
        type="interval",
        interval_minutes=15,
        time_of_day_start="09:00",
        time_of_day_end="17:00",
    )
    store.upsert(s)
    # 03:00 local — outside the window.
    early = datetime.now().astimezone().replace(hour=3, minute=0).astimezone(UTC)
    assert scheduler.find_due(early) == []
    # 10:00 local — inside.
    midmorning = datetime.now().astimezone().replace(hour=10, minute=0).astimezone(UTC)
    assert [s.id for s in scheduler.find_due(midmorning)] == ["a"]


def test_disabled_schedule_skipped(scheduler: Scheduler, store: ScheduleStore) -> None:
    s = Schedule(
        id="a", name="A", page_id="home", type="interval", interval_minutes=15, enabled=False
    )
    store.upsert(s)
    assert scheduler.find_due(datetime(2026, 6, 1, 12, tzinfo=UTC)) == []


def test_skips_schedule_with_deleted_target_page(
    store: ScheduleStore, push_manager, caplog: pytest.LogCaptureFixture
) -> None:
    """A schedule whose ``page_id`` no longer resolves is filtered out of
    find_due so the History view doesn't fill with "page not found" rows.
    The first miss logs a warning; subsequent ticks stay quiet."""
    sched = Scheduler(
        store=store,
        push_manager=lambda: push_manager,
        page_exists=lambda pid: pid == "home",
    )
    store.upsert(
        Schedule(id="ghost", name="Ghost", page_id="vanished", type="interval", interval_minutes=15)
    )
    store.upsert(
        Schedule(id="live", name="Live", page_id="home", type="interval", interval_minutes=15)
    )
    now = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    with caplog.at_level("WARNING", logger="app.scheduler"):
        due = sched.find_due(now)
        assert [d.id for d in due] == ["live"]
        assert sum("vanished" in r.message for r in caplog.records) == 1
        # Second pass: still skips, but no second warning.
        sched.find_due(now)
        assert sum("vanished" in r.message for r in caplog.records) == 1


def test_priority_ordering(scheduler: Scheduler, store: ScheduleStore) -> None:
    store.upsert(
        Schedule(
            id="low", name="L", page_id="home", type="interval", interval_minutes=15, priority=1
        )
    )
    store.upsert(
        Schedule(
            id="hi", name="H", page_id="home", type="interval", interval_minutes=15, priority=10
        )
    )
    due = scheduler.find_due(datetime(2026, 6, 1, 12, tzinfo=UTC))
    assert [d.id for d in due] == ["hi", "low"]


def test_daily_backfill_suppressed_when_enabled_after_target(
    scheduler: Scheduler, store: ScheduleStore
) -> None:
    # Target is 07:00 today; the scheduler "starts watching" at 11:00.
    # find_due at 11:00 must NOT fire the morning's missed 07:00.
    today = datetime.now().astimezone()
    fires_at = today.replace(hour=7, minute=0, second=0, microsecond=0)
    s = Schedule(id="morning", name="Morning", page_id="home", type="daily", fires_at=fires_at)
    store.upsert(s)
    eleven_am = today.replace(hour=11, minute=0, second=0, microsecond=0).astimezone(UTC)
    assert scheduler.find_due(eleven_am) == []


def test_daily_fires_once_per_local_day(scheduler: Scheduler, store: ScheduleStore) -> None:
    today = datetime.now().astimezone()
    fires_at = today.replace(hour=7, minute=0)
    s = Schedule(id="morning", name="Morning", page_id="home", type="daily", fires_at=fires_at)
    store.upsert(s)
    # Pretend we've been watching since 06:00 so the 07:00 target is in
    # range. Fire once.
    six_am_today = today.replace(hour=6, minute=0).astimezone(UTC)
    scheduler.find_due(six_am_today)  # populates first_seen
    seven_am = today.replace(hour=7, minute=1).astimezone(UTC)
    fired = scheduler.run_due_once(seven_am)
    assert [s.id for s, _ in fired] == ["morning"]
    # Subsequent ticks today do not refire.
    nine_am = today.replace(hour=9, minute=0).astimezone(UTC)
    assert scheduler.find_due(nine_am) == []


def test_factory_returns_current_push_manager(store: ScheduleStore, push_manager) -> None:
    holder = {"pm": push_manager}
    sched = Scheduler(store=store, push_manager=lambda: holder["pm"])
    store.upsert(Schedule(id="a", name="A", page_id="home", type="interval", interval_minutes=15))
    sched.run_due_once(datetime(2026, 6, 1, 10, tzinfo=UTC))
    assert holder["pm"].push.call_count == 1
    # Swap the push manager — the scheduler's next fire targets the new one.
    new_pm = MagicMock()
    new_pm.push.return_value = PushResult(status="sent", page_id="home")
    holder["pm"] = new_pm
    sched.fire_now("a")
    assert new_pm.push.call_count == 1
    # The old one didn't get a second call.
    assert push_manager.push.call_count == 1


def test_failed_push_does_not_record_last_fired(
    scheduler: Scheduler, store: ScheduleStore, push_manager
) -> None:
    push_manager.push.return_value = PushResult(status="failed", page_id="home", error="boom")
    store.upsert(Schedule(id="a", name="A", page_id="home", type="interval", interval_minutes=15))
    scheduler.run_due_once(datetime(2026, 6, 1, 10, tzinfo=UTC))
    # Failure -> still due on the next tick (no cooldown recorded).
    later = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)
    assert [s.id for s in scheduler.find_due(later)] == ["a"]


def test_status_snapshot_contains_first_seen(scheduler: Scheduler, store: ScheduleStore) -> None:
    store.upsert(Schedule(id="a", name="A", page_id="home", type="interval", interval_minutes=15))
    scheduler.find_due(datetime(2026, 6, 1, 10, tzinfo=UTC))
    snapshot = scheduler.status()
    assert "a" in snapshot
    assert snapshot["a"]["first_seen"] is not None
