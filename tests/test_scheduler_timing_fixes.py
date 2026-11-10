"""Regression tests for lineup timing fixes in the scheduler (independent review)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.push import PushResult
from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.page_store import Page, PageStore
from app.state.schedule_store import ScheduleStore

T = datetime(2026, 6, 15, 6, 0, tzinfo=UTC)


class _Push:
    def __init__(self, fail_devices: set[str] | None = None, fail_all: bool = False) -> None:
        self.fail_devices = fail_devices or set()
        self.fail_all = fail_all
        self.calls: list[tuple[str, set[str]]] = []

    def push(self, page_id, *, device_ids=None, **_kw):
        devs = set(device_ids or [])
        self.calls.append((page_id, devs))
        failing = self.fail_all or bool(devs & self.fail_devices)
        return PushResult(
            status="failed" if failing else "sent",
            page_id=page_id,
            error="temporary failure" if failing else None,
        )

    def device_in_quiet_hours(self, _dev):
        return False


def _rig(tmp_path, devices=("panel",), dwells=(50, 63, 63), **deck_kw):
    ds = DeckStore(tmp_path / "decks.json")
    ps = PageStore(tmp_path / "pages.json")
    for pid in ("a", "b", "c"):
        ps.save(Page(id=pid, name=pid.upper(), device_ids=list(devices)))
    deck = Deck(
        id="lu",
        name="Lu",
        advance="timer",
        advance_trigger="cycle",
        advance_anchor="06:00",
        advance_end_at="18:00",
        device_ids=list(devices),
        pages=[DeckPage(page_id=p, dwell_minutes=m) for p, m in zip("abc", dwells, strict=False)],
        **deck_kw,
    )
    ds.upsert(deck)
    push = _Push()
    sched = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        push_manager=lambda: push,
        timezone_provider=lambda: UTC,
        deck_store=ds,
        deck_nav_store=DeckNavStore(tmp_path / "nav.json"),
        page_store=ps,
        device_ids_for_page=lambda _pid: list(devices),
    )
    return sched, push, deck, ds


def _tick(sched, now):
    due = sched.find_due_rotations(now)
    for rot, idx in due:
        sched._fire_cycle(rot, idx, now)
    return [(r.id, i) for r, i in due]


def test_failed_bound_cycle_send_is_retried_next_tick(tmp_path):
    sched, push, _deck, _ds = _rig(tmp_path)
    push.fail_all = True
    assert _tick(sched, T) == [("lu", 0)]
    assert sched.rotation_status()["lu"]["last_status"] == "failed"
    push.fail_all = False
    # Pipeline recovered: the next tick retries the same slot, once.
    assert _tick(sched, T + timedelta(seconds=30)) == [("lu", 0)]
    assert [c[0] for c in push.calls] == ["a", "a"]
    assert sched.rotation_status()["lu"]["last_status"] == "sent"
    # And it does not double-send afterwards.
    assert _tick(sched, T + timedelta(seconds=60)) == []
    assert len(push.calls) == 2


def test_failed_display_retries_without_resending_the_successful_one(tmp_path):
    sched, push, _deck, _ds = _rig(tmp_path, devices=("p1", "p2"))
    push.fail_devices = {"p2"}
    _tick(sched, T)
    push.fail_devices = set()
    assert _tick(sched, T + timedelta(seconds=30)) == [("lu", 0)]
    assert push.calls == [("a", {"p1"}), ("a", {"p2"}), ("a", {"p2"})]
    assert _tick(sched, T + timedelta(seconds=60)) == []


def test_failed_send_does_not_block_the_next_slot(tmp_path):
    sched, push, _deck, _ds = _rig(tmp_path, dwells=(5, 5, 5))
    push.fail_all = True
    _tick(sched, T)
    push.fail_all = False
    assert _tick(sched, T + timedelta(minutes=5)) == [("lu", 1)]
    assert push.calls[-1][0] == "b"


def _telemetry(sched, wake_at: float):
    entries = {
        "panel": SimpleNamespace(always_on=False, is_trusted=True, predicted_next_wake_at=wake_at),
        "other": SimpleNamespace(always_on=True, is_trusted=True, predicted_next_wake_at=None),
    }
    sched._device_telemetry = SimpleNamespace(get=entries.get)


def test_smart_sync_ignores_displays_outside_the_lineup(tmp_path):
    sched, push, _deck, ds = _rig(tmp_path, advance_smart_sync=True)
    # The dashboards are also bound to an always-on display the lineup
    # doesn't target.
    sched._device_ids_for_page = lambda _pid: ["panel", "other"]
    _telemetry(sched, (T + timedelta(minutes=10)).timestamp())
    assert _tick(sched, T) == []  # waiting for the panel's wake at 06:10
    assert push.calls == []
    # Targeting the always-on display too is what opens the gate.
    ds.upsert(ds.get("lu").model_copy(update={"device_ids": ["panel", "other"]}))
    assert _tick(sched, T) == [("lu", 0)]


def test_smart_sync_on_interval_lineup_uses_its_targets(tmp_path):
    sched, _push, deck, ds = _rig(tmp_path)
    ds.upsert(
        deck.model_copy(
            update={
                "advance_trigger": "interval",
                "advance_interval_minutes": 5,
                "advance_window_start": "05:00",
                "advance_window_end": "18:00",
                "advance_smart_sync": True,
            }
        )
    )
    sched._device_ids_for_page = lambda _pid: ["panel", "other"]
    _telemetry(sched, (T + timedelta(minutes=10)).timestamp())
    assert sched.find_due(T) == []
