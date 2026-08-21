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
    # 03:00 local, outside the window.
    early = datetime.now().astimezone().replace(hour=3, minute=0).astimezone(UTC)
    assert scheduler.find_due(early) == []
    # 10:00 local, inside.
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
    # Swap the push manager, the scheduler's next fire targets the new one.
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


# ----- Smart sync (issue #10) ---------------------------------------------


class _FakeTelemetry:
    """Tiny stand-in for app.state.device_telemetry.TelemetryStore in
    scheduler tests. Lets the test set per-device predictions + trust
    without standing up the real persisted store."""

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self._entries = entries or {}

    def get(self, device_id: str):
        raw = self._entries.get(device_id)
        if raw is None:
            return None

        class _E:
            predicted_next_wake_at = raw.get("predicted_next_wake_at")
            is_trusted = bool(raw.get("is_trusted", False))
            always_on = bool(raw.get("always_on", False))

        return _E()


def _make_smart_scheduler(store, push_manager, *, device_ids, telemetry) -> Scheduler:
    return Scheduler(
        store=store,
        push_manager=lambda: push_manager,
        device_ids_for_page=lambda page_id: device_ids.get(page_id, []),
        device_telemetry=telemetry,
    )


def test_smart_sync_off_uses_interval_unchanged(store: ScheduleStore, push_manager) -> None:
    """``smart_sync=False`` (the default) ignores telemetry entirely;
    behaviour matches the pre-#10 interval schedule."""
    sched = _make_smart_scheduler(
        store,
        push_manager,
        device_ids={"home": ["esp"]},
        telemetry=_FakeTelemetry({"esp": {"is_trusted": True, "predicted_next_wake_at": 9e9}}),
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=False,
        )
    )
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    assert [s.id for s in sched.find_due(now)] == ["a"]


def test_smart_sync_on_with_no_bound_devices_falls_back_to_interval(
    store: ScheduleStore, push_manager
) -> None:
    """A page with no device_ids: smart_sync can't act, schedule still
    fires on its interval cadence."""
    sched = _make_smart_scheduler(
        store, push_manager, device_ids={"home": []}, telemetry=_FakeTelemetry()
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=True,
        )
    )
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    assert [s.id for s in sched.find_due(now)] == ["a"]


def test_smart_sync_on_warming_up_falls_back_to_interval(
    store: ScheduleStore, push_manager
) -> None:
    """Devices bound but none trusted yet, scheduler keeps firing on
    the existing cadence so the panel still gets fresh frames."""
    sched = _make_smart_scheduler(
        store,
        push_manager,
        device_ids={"home": ["esp"]},
        telemetry=_FakeTelemetry({"esp": {"is_trusted": False, "predicted_next_wake_at": 9e9}}),
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=True,
        )
    )
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    assert [s.id for s in sched.find_due(now)] == ["a"]


def test_smart_sync_holds_when_trusted_device_not_in_lead_window(
    store: ScheduleStore, push_manager
) -> None:
    """Device is trusted but its predicted wake is 10 min away with a
    10s lead, smart_sync says wait (return no candidates)."""
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    far_future = now.timestamp() + 600  # 10 min from now
    sched = _make_smart_scheduler(
        store,
        push_manager,
        device_ids={"home": ["esp"]},
        telemetry=_FakeTelemetry(
            {"esp": {"is_trusted": True, "predicted_next_wake_at": far_future}}
        ),
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=True,
            smart_sync_lead_s=10,
        )
    )
    assert sched.find_due(now) == []


def test_smart_sync_never_holds_for_an_always_on_device(store: ScheduleStore, push_manager) -> None:
    """The panel is reachable right now, so there is no wake to aim at.
    Holding would only delay a frame it could already have collected."""
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    far_future = now.timestamp() + 600
    sched = _make_smart_scheduler(
        store,
        push_manager,
        device_ids={"home": ["esp"]},
        telemetry=_FakeTelemetry(
            {
                "esp": {
                    "is_trusted": True,
                    "predicted_next_wake_at": far_future,
                    "always_on": True,
                }
            }
        ),
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=True,
            smart_sync_lead_s=10,
        )
    )
    assert [s.id for s in sched.find_due(now)] == ["a"]


def test_smart_sync_fires_inside_lead_window(store: ScheduleStore, push_manager) -> None:
    """Trusted device's prediction is 5 seconds away; lead window is
    10s so we should fire RIGHT NOW so the frame is waiting for the
    panel."""
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    near_wake = now.timestamp() + 5
    sched = _make_smart_scheduler(
        store,
        push_manager,
        device_ids={"home": ["esp"]},
        telemetry=_FakeTelemetry(
            {"esp": {"is_trusted": True, "predicted_next_wake_at": near_wake}}
        ),
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=True,
            smart_sync_lead_s=10,
        )
    )
    assert [s.id for s in sched.find_due(now)] == ["a"]


def test_smart_sync_respects_interval_floor(store: ScheduleStore, push_manager) -> None:
    """Even with a trusted prediction RIGHT NOW, the interval cadence
    is a floor, never push more often than the user configured. Fired
    a minute ago + 15-min floor means we hold."""
    now = datetime(2026, 6, 1, 10, tzinfo=UTC)
    sched = _make_smart_scheduler(
        store,
        push_manager,
        device_ids={"home": ["esp"]},
        telemetry=_FakeTelemetry(
            {"esp": {"is_trusted": True, "predicted_next_wake_at": now.timestamp()}}
        ),
    )
    store.upsert(
        Schedule(
            id="a",
            name="A",
            page_id="home",
            type="interval",
            interval_minutes=15,
            smart_sync=True,
        )
    )
    sched.run_due_once(now)  # initial fire
    one_minute_later = now + timedelta(minutes=1)
    assert sched.find_due(one_minute_later) == []
