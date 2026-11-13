"""Regression tests for lineup timing fixes in the scheduler (independent review)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.deck_routes import _lineup_input
from app.lineups_view import (
    DashboardInfo,
    DisplayInput,
    OnScreenInput,
    PublishedInput,
    UpcomingInput,
    build_view,
)
from app.next_update import MODE_CONNECTED, MODE_SLEEPS, Reach
from app.push import PushResult
from app.scheduler import Scheduler
from app.scheduler_conditions import ConditionEvaluator
from app.state.conditions import ha_condition
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


def _rig(tmp_path, devices=("panel",), dwells=(50, 63, 63), refresh=0, **deck_kw):
    ds = DeckStore(tmp_path / "decks.json")
    ps = PageStore(tmp_path / "pages.json")
    for pid in ("a", "b", "c"):
        ps.save(
            Page(
                id=pid,
                name=pid.upper(),
                device_ids=list(devices),
                refresh_minutes=refresh if pid == "a" else 0,
            )
        )
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


def test_a_send_that_keeps_failing_is_given_up_after_a_few_ticks(tmp_path):
    """A display that is off fails every tick; the window is tried three
    times, then recorded as spent, and the next window starts afresh."""
    sched, push, _deck, _ds = _rig(tmp_path, dwells=(5, 5, 5))
    push.fail_all = True
    for tick in range(3):
        assert _tick(sched, T + timedelta(seconds=30 * tick)) == [("lu", 0)]
    assert len(push.calls) == 3
    for tick in range(3, 9):
        assert _tick(sched, T + timedelta(seconds=30 * tick)) == []
    assert len(push.calls) == 3
    assert sched.rotation_status()["lu"]["last_status"] == "failed"
    # The next window gets its own three tries.
    assert _tick(sched, T + timedelta(minutes=5)) == [("lu", 1)]
    assert push.calls[-1][0] == "b"
    push.fail_all = False
    assert _tick(sched, T + timedelta(minutes=5, seconds=30)) == [("lu", 1)]
    assert sched.rotation_status()["lu"]["last_status"] == "sent"


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


def _view(sched, deck, now, reach=None, on="a"):
    """The Lineups display view fed by the scheduler's own projection."""
    events = sched.upcoming_for_device("panel", now=now, hours=3, limit=12)
    display = DisplayInput(
        "panel",
        "Panel",
        100,
        100,
        reach or Reach(MODE_CONNECTED, live=True),
        (_lineup_input(deck, runtime=None, with_urls=False),),
        on_screen=OnScreenInput(on, T.timestamp()),
        published=PublishedInput(on, T.timestamp()),
        published_known=True,
        upcoming=tuple(
            UpcomingInput(e.scheduled_at.timestamp(), e.lineup_id, e.dashboard_id) for e in events
        ),
    )
    dashboards = {x: DashboardInfo(x, x.upper()) for x in "abc"}
    return build_view([display], dashboards, now=now).displays[0]


def _next_pill(view):
    return next(x.pill for x in view.lineups[0].steps if x.next)


def _next_ticks(view):
    return [t for seg in view.day.segments for t in seg.ticks if t.next]


def test_next_marks_follow_min_hold_like_the_engine(tmp_path):
    sched, _push, deck, _ds = _rig(tmp_path, dwells=(5, 5, 5), advance_min_hold_minutes=10)
    _tick(sched, T)
    now = T + timedelta(minutes=1)
    view = _view(sched, deck, now)
    assert view.next_update.at_label == "06:10"
    # Nothing is sent at 06:05, so the pill and the countdown say 06:10 / C.
    assert _next_pill(view).label == "Next 06:10"
    assert [x.dashboard_id for x in view.lineups[0].steps if x.next] == ["c"]
    assert [t.minute for t in _next_ticks(view)] == [370]
    assert _next_ticks(view)[0].in_min == 9
    assert _tick(sched, T + timedelta(minutes=5)) == []
    assert _tick(sched, T + timedelta(minutes=10)) == [("lu", 2)]


def test_next_countdown_runs_to_the_check_in_of_a_sleeping_display(tmp_path):
    sched, _push, deck, _ds = _rig(tmp_path, dwells=(30, 30, 30))
    _tick(sched, T)
    now = T + timedelta(minutes=1)
    reach = Reach(
        MODE_SLEEPS,
        next_checkin_at=(T + timedelta(minutes=40)).timestamp(),
        gap_s=1200,
        cadence_stable=True,
    )
    view = _view(sched, deck, now, reach=reach)
    assert view.next_update.at_label == "~06:40"
    (tick,) = _next_ticks(view)
    assert tick.in_min == 39  # to the 06:40 check-in, not the 06:30 send


def _failing_rule_evaluator():
    ev = ConditionEvaluator(
        ha_get_states=lambda: [{"entity_id": "sensor.x", "state": "off"}],
        timezone_provider=lambda: UTC,
        location_provider=lambda: (None, None),
    )
    ev.refresh_ha_states()
    return ev


def test_next_names_the_dashboard_the_conditions_will_send(tmp_path):
    sched, _push, deck, ds = _rig(tmp_path, dwells=(5, 5, 5))
    sched._condition_evaluator = _failing_rule_evaluator()
    pages = list(deck.pages)
    pages[1] = pages[1].model_copy(update={"conditions": [ha_condition("sensor.x", "==", "on")]})
    deck = deck.model_copy(update={"pages": pages})
    ds.upsert(deck)
    _tick(sched, T)
    view = _view(sched, deck, T + timedelta(minutes=1))
    assert view.next_update.name == "C"
    assert [x.dashboard_id for x in view.lineups[0].steps if x.next] == ["c"]
    assert _tick(sched, T + timedelta(minutes=5)) == [("lu", 2)]


def test_priority_mode_does_not_promise_the_next_dashboard(tmp_path):
    sched, push, deck, ds = _rig(tmp_path, dwells=(5, 5, 5))
    deck = deck.model_copy(update={"advance_mode": "priority"})
    ds.upsert(deck)
    _tick(sched, T)
    view = _view(sched, deck, T + timedelta(minutes=1))
    assert view.next_update.name == "A"
    assert [x.dashboard_id for x in view.lineups[0].steps if x.next] == ["a"]
    _tick(sched, T + timedelta(minutes=5))
    assert [c[0] for c in push.calls] == ["a", "a"]


def test_next_update_includes_the_dashboards_own_updates_refresh(tmp_path):
    sched, push, deck, _ds = _rig(tmp_path, dwells=(50, 63, 63), refresh=5)
    _tick(sched, T)
    sched._page_last_refresh["a"] = T.timestamp()
    now = T + timedelta(minutes=1)
    events = sched.upcoming_for_device("panel", now=now, hours=3, limit=6)
    assert (events[0].cause, events[0].dashboard_id) == ("dashboard_refresh", "a")
    assert events[0].effect == "refresh_screen"
    assert events[0].scheduled_at == T + timedelta(minutes=5)
    assert events[1].cause == "cycle" and events[1].scheduled_at == T + timedelta(minutes=50)
    view = _view(sched, deck, now)
    assert view.next_update.at_label == "06:05"
    # The scheduler really does repaint A at 06:05.
    sched._maybe_refresh_pages(T + timedelta(minutes=5))
    assert push.calls[-1][0] == "a"


def test_updates_refresh_is_not_projected_for_a_page_with_updates_off(tmp_path):
    sched, _push, _deck, _ds = _rig(tmp_path, dwells=(50, 63, 63), refresh=0)
    _tick(sched, T)
    events = sched.upcoming_for_device("panel", now=T + timedelta(minutes=1), hours=3, limit=6)
    assert [e.cause for e in events][:1] == ["cycle"]
    assert all(e.cause != "dashboard_refresh" for e in events)


def test_updates_refresh_is_projected_for_every_stretch_the_dashboard_is_shown(tmp_path):
    sched, _push, _deck, _ds = _rig(tmp_path, dwells=(20, 20, 20), refresh=15)
    _tick(sched, T)
    sched._page_last_refresh["a"] = T.timestamp()
    events = sched.upcoming_for_device("panel", now=T + timedelta(minutes=1), hours=3, limit=20)
    refreshes = [e.scheduled_at for e in events if e.cause == "dashboard_refresh"]
    assert refreshes == [T + timedelta(minutes=m) for m in (15, 75, 135)]
