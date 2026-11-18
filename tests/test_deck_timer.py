"""Timer-advance decks (Phase 1 of the rotations merge): the model's cycle math
and the scheduler branch that walks a deck's pages on a wall-clock anchor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


def test_priority_minimum_hold_blocks_sensor_flaps(wiring) -> None:
    from app.scheduler_conditions import ConditionEvaluator
    from app.state.conditions import Condition

    scheduler, push, store, _nav = wiring
    states = [{"entity_id": "sensor.activity", "state": "idle"}]
    scheduler._condition_evaluator = ConditionEvaluator(ha_get_states=lambda: states)
    store.upsert(
        _deck(
            advance_mode="priority",
            advance_min_hold_minutes=30,
            pages=[
                DeckPage(
                    page_id="active",
                    conditions=[
                        Condition(
                            source_kind="ha_entity",
                            source_id="sensor.activity",
                            operator="==",
                            value="running",
                        )
                    ],
                ),
                DeckPage(page_id="photos", dwell_minutes=60),
            ],
        )
    )
    now = datetime(2026, 11, 16, 11, 0, 47, tzinfo=UTC)
    scheduler._tick_once(now)
    push.push.reset_mock()
    for minutes, value in [(1, "running"), (2, "idle"), (3, "running")]:
        states[0]["state"] = value
        scheduler._condition_evaluator.refresh_ha_states()
        scheduler._reevaluate_priority_lineups(now + timedelta(minutes=minutes))
    push.push.assert_not_called()
    assert scheduler._priority_pass(now + timedelta(minutes=29, seconds=59)) == 1
    scheduler._priority_pass(now + timedelta(minutes=30))
    assert push.push.call_args.args[0] == "active"


@pytest.mark.parametrize("status", ["failed", "busy"])
def test_priority_worker_respects_send_attempt_cap(wiring, status):
    from app.scheduler import _CYCLE_SEND_ATTEMPTS

    scheduler, push, store, _nav = wiring
    store.upsert(_deck(advance_mode="priority", advance_min_hold_minutes=0))
    push.push.return_value = MagicMock(status=status, error="unavailable")
    now = datetime(2026, 6, 15, 0, 0, tzinfo=UTC)
    for tick in range(10):
        scheduler._reevaluate_priority_lineups(now + timedelta(seconds=30 * tick))
    assert push.push.call_count == _CYCLE_SEND_ATTEMPTS
    scheduler._reevaluate_priority_lineups(now + timedelta(minutes=30))
    assert push.push.call_count == _CYCLE_SEND_ATTEMPTS + 1


@pytest.fixture
def priority_frame(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.push import PushResult
    from app.scheduler_conditions import ConditionEvaluator
    from app.state.conditions import Condition
    from app.state.deck_nav_store import DeckNavStore
    from app.state.page_store import Page, PageStore

    class RetainedTransport:
        def __init__(self):
            self.sent = []
            self.latest = {}
            self.quiet = False

        def push(self, page_id, *, device_ids=None, source="page", **kwargs):
            for device_id in device_ids or {"panel"}:
                self.sent.append((device_id, page_id, source))
                self.latest[device_id] = {"page_id": page_id}
            return PushResult(status="sent", page_id=page_id)

        def latest_render_for(self, device_id):
            return self.latest.get(device_id)

        def device_in_quiet_hours(self, device_id):
            return self.quiet

    pages = PageStore(tmp_path / "pages.json")
    for pid in ("print", "photos"):
        pages.save(Page(id=pid, name=pid, device_ids=["panel"], refresh_minutes=15))
    decks = DeckStore(tmp_path / "decks.json")
    decks.upsert(
        _deck(
            advance_mode="priority",
            advance_min_hold_minutes=0,
            advance_smart_sync=True,
            advance_smart_sync_lead_s=30,
            pages=[
                DeckPage(
                    page_id="print",
                    conditions=[
                        Condition(
                            source_kind="ha_entity",
                            source_id="sensor.printer",
                            operator="==",
                            value="running",
                        )
                    ],
                ),
                DeckPage(page_id="photos", dwell_minutes=60),
            ],
        )
    )
    states = [{"entity_id": "sensor.printer", "state": "idle"}]
    evaluator = ConditionEvaluator(ha_get_states=lambda: states)
    evaluator.refresh_ha_states()
    pusher = RetainedTransport()
    nav = DeckNavStore(tmp_path / "nav.json")
    telemetry = SimpleNamespace(always_on=False, is_trusted=True, predicted_next_wake_at=None)
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=decks,
        deck_nav_store=nav,
        page_store=pages,
        condition_evaluator=evaluator,
        push_manager=lambda: pusher,
        device_telemetry=SimpleNamespace(get=lambda did: telemetry),
        timezone_provider=lambda: UTC,
    )
    monkeypatch.setattr(scheduler, "_maybe_warm_decks", lambda now: None)
    monkeypatch.setattr(scheduler, "_maybe_warm_ahead", lambda now: None)
    return scheduler, pusher, nav, states, telemetry


def test_unpredicted_wake_queues_selection_without_rendering_in_request(priority_frame):
    scheduler, transport, nav, states, telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    telemetry.predicted_next_wake_at = (now + timedelta(hours=1)).timestamp()
    states[0] = {"entity_id": "sensor.printer", "state": "running"}
    scheduler.prepare_device_wake("panel", now=now + timedelta(minutes=21))
    assert transport.latest_render_for("panel")["page_id"] == "photos"
    assert nav.current_page("panel", "d1") == "photos"
    scheduler._priority_pass(now + timedelta(minutes=21))
    assert transport.latest_render_for("panel")["page_id"] == "print"
    assert nav.current_page("panel", "d1") == "print"


@pytest.mark.parametrize("gate", ["quiet", "global_pause", "device_pause", "manual_hold"])
def test_event_and_wake_checks_preserve_automation_holds(priority_frame, tmp_path, gate):
    from app.state.device_rotation_state_model import DeviceRotationState
    from app.state.device_rotation_state_store import DeviceRotationStateStore

    scheduler, transport, nav, states, _telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    states[0] = {"entity_id": "sensor.printer", "state": "running"}
    if gate == "quiet":
        transport.quiet = True
    elif gate == "global_pause":
        scheduler._paused_provider = lambda: True
    else:
        store = DeviceRotationStateStore(tmp_path / "holds.json")
        field = "automation_pause_until" if gate == "device_pause" else "override_until"
        store.upsert(
            DeviceRotationState(
                device_id="panel",
                rotation_id="d1",
                step_index=1,
                **{field: now + timedelta(hours=1)},
            )
        )
        scheduler._rotation_state_store = store
    transport.sent.clear()
    scheduler.prepare_device_wake("panel", now=now + timedelta(minutes=1))
    scheduler._priority_pass(now + timedelta(minutes=1))
    assert transport.sent == []
    assert nav.current_page("panel", "d1") == "photos"


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


def test_a_five_minute_cycle_alternates_under_the_default_min_hold(wiring) -> None:
    """Two dashboards, five-minute dwell, defaults otherwise (#167).

    ``advance_min_hold_minutes`` defaults to 5, so this is the dwell and the
    hold set to the same value, which is what anyone who leaves the defaults
    alone gets. The gate used to measure from the moment of the last fire,
    and a fire lands a few seconds into its window (the tick that noticed the
    boundary), so the next boundary fell just inside the hold. Every other
    window was swallowed, and with two pages the swallowed window is always
    the same one: the panel sat on a single dashboard while still firing on
    the grid, at twice the interval.

    Ticks here deliberately land a few seconds late, because ticking exactly
    on the boundary is what hid this.
    """
    scheduler, push, store, _nav = wiring
    store.upsert(
        _deck(
            advance_interval_minutes=5,
            pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
        )
    )
    painted: list[str] = []
    for window in range(6):
        # +7s: a real tick notices the boundary shortly after it passes.
        moment = datetime(2026, 6, 15, 0, 0, tzinfo=UTC).replace(minute=(window * 5) % 60, second=7)
        scheduler._tick_once(moment)
        if push.push.call_args is not None:
            painted.append(push.push.call_args[0][0])
        push.push.reset_mock()
    assert painted == ["a", "b", "a", "b", "a", "b"]


def test_a_hold_longer_than_the_dwell_still_slows_the_cycle(wiring) -> None:
    """The other half of the same gate: a hold above the dwell is someone
    deliberately keeping each dashboard up longer than the cycle says, and
    that has to keep working."""
    scheduler, push, store, _nav = wiring
    store.upsert(_deck(advance_interval_minutes=30, advance_min_hold_minutes=60))
    fired_at: list[str] = []
    for window in range(5):
        moment = datetime(2026, 6, 15, 0, 0, tzinfo=UTC).replace(
            hour=(window * 30) // 60, minute=(window * 30) % 60, second=7
        )
        scheduler._tick_once(moment)
        if push.push.call_args is not None:
            fired_at.append(moment.strftime("%H:%M"))
        push.push.reset_mock()
    # Every other 30-minute window is held, so the panel repaints hourly,
    # which is what asking for a 60-minute hold means.
    assert fired_at == ["00:00", "01:00", "02:00"]


def test_promoted_advance_leaves_the_history_row_to_the_promote(tmp_path: Path) -> None:
    """A cache-hit promote changes what the panel shows without a push; the
    promote writes the History (``type="push"``) row itself
    (``PushManager.promote_deck_page``, #266), tagged with the lineup, so the
    scheduler must not write a second one."""

    from app.state.event_log import EventLog

    deck_store = DeckStore(tmp_path / "decks.json")
    deck_store.upsert(_deck())
    push = MagicMock()
    push.promote_deck_page.return_value = True
    push.deck_render_for.return_value = {"composition_digest": "compdigest"}
    push.device_in_quiet_hours.return_value = False
    log = EventLog(tmp_path / "events.db")
    scheduler = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        deck_store=deck_store,
        deck_nav_store=MagicMock(),
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
        event_log=log,
    )
    scheduler._tick_once(datetime(2026, 6, 15, 0, 0, tzinfo=UTC))

    push.push.assert_not_called()
    push.promote_deck_page.assert_called_once()
    assert push.promote_deck_page.call_args.kwargs["extra"] == {"deck_id": "d1"}
    assert log.list(type="push", source="deck", limit=5) == []
    # The internal advance event is still recorded.
    assert log.list(type="deck", limit=5)


def test_a_reordered_rotation_sends_the_dashboard_now_in_its_slot(wiring) -> None:
    """Steps reordered mid-window: the slot keeps its number but names another
    dashboard. After ``resync_cycle`` the next tick sends that one; a display
    already showing the right dashboard is left alone."""
    scheduler, push, store, _nav = wiring
    store.upsert(_deck(advance_min_hold_minutes=0))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 5, tzinfo=UTC))
    assert push.push.call_args[0][0] == "a"
    push.push.reset_mock()
    # b moved to the front: 00:10 is still step 0, which is now b.
    deck = store.get("d1")
    store.upsert(deck.model_copy(update={"pages": list(reversed(deck.pages))}))
    scheduler._tick_once(datetime(2026, 6, 15, 0, 10, tzinfo=UTC))
    push.push.assert_not_called()  # the old behaviour: wait for 00:30
    scheduler.resync_cycle("d1")
    scheduler._tick_once(datetime(2026, 6, 15, 0, 11, tzinfo=UTC))
    assert push.push.call_args[0][0] == "b"
    # A reorder that leaves the slot's dashboard where it was repaints nothing.
    push.push.reset_mock()
    scheduler.resync_cycle("d1")
    scheduler._tick_once(datetime(2026, 6, 15, 0, 12, tzinfo=UTC))
    push.push.assert_not_called()


def test_printer_event_selects_within_debounce_and_next_wake_gets_print(priority_frame):
    scheduler, transport, nav, states, telemetry = priority_frame
    wake = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(wake)
    assert nav.current_page("panel", "d1") == "photos"
    telemetry.predicted_next_wake_at = (wake + timedelta(hours=1)).timestamp()
    started = wake + timedelta(minutes=21)
    states[0] = {"entity_id": "sensor.printer", "state": "running"}
    scheduler.on_ha_state_changed("sensor.printer", states[0], now=started)
    scheduler._priority_pass(started + timedelta(seconds=1))
    assert scheduler.live_step_index(scheduler._cycle_records()[0], started) == 0
    # Smart sync avoids paid work while the frame is asleep. The old nav
    # record must not let a photos refresh replace the selected print.
    transport.sent.clear()
    scheduler._maybe_refresh_pages(started + timedelta(minutes=1))
    assert transport.sent == []
    assert nav.current_page("panel", "d1") == "photos"
    scheduler._priority_pass(wake + timedelta(minutes=59, seconds=30))
    assert transport.latest_render_for("panel")["page_id"] == "print"
    assert nav.current_page("panel", "d1") == "print"
    scheduler.prepare_device_wake("panel", now=wake + timedelta(hours=1))
    scheduler._priority_pass(wake + timedelta(hours=1))
    assert transport.latest_render_for("panel")["page_id"] == "print"
    transport.sent.clear()
    scheduler._maybe_refresh_pages(wake + timedelta(hours=1, minutes=1))
    assert all(page != "photos" for _, page, _ in transport.sent)
    transport.push("photos", device_ids={"panel"}, source="page")
    assert transport.latest_render_for("panel")["page_id"] == "photos"
    # An unchanged selection must not undo an explicit push at wake.
    scheduler.prepare_device_wake("panel", now=wake + timedelta(hours=1, minutes=2))
    scheduler._priority_pass(wake + timedelta(hours=1, minutes=2))
    assert transport.latest_render_for("panel")["page_id"] == "photos"


def test_printer_event_is_debounced_without_waiting_for_a_timer_tick(priority_frame):
    scheduler, transport, nav, states, telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    telemetry.always_on = True
    started = now + timedelta(minutes=1)
    states[0] = {"entity_id": "sensor.printer", "state": "running"}
    scheduler.on_ha_state_changed("sensor.printer", states[0], now=started)
    transport.sent.clear()
    assert scheduler._priority_pass(started + timedelta(seconds=0.5)) == 0.5
    assert transport.sent == []
    scheduler._priority_pass(started + timedelta(seconds=1))
    assert nav.current_page("panel", "d1") == "print"


def test_event_sensor_flaps_wait_for_minimum_hold_expiry(priority_frame):
    scheduler, transport, nav, states, telemetry = priority_frame
    deck = scheduler._deck_store.get("d1")
    scheduler._deck_store.upsert(deck.model_copy(update={"advance_min_hold_minutes": 30}))
    telemetry.always_on = True
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    scheduler._condition_evaluator.receive_ha_snapshot(states)
    transport.sent.clear()
    for minute, value in [(1, "running"), (2, "idle"), (3, "running")]:
        changed = now + timedelta(minutes=minute)
        scheduler.on_ha_state_changed(
            "sensor.printer", {"entity_id": "sensor.printer", "state": value}, now=changed
        )
        scheduler._priority_pass(changed + timedelta(seconds=1))
    assert transport.sent == []
    assert scheduler._priority_pass(now + timedelta(minutes=29, seconds=59)) == 1
    scheduler._priority_pass(now + timedelta(minutes=30))
    assert transport.sent == [("panel", "print", "deck")]
    assert nav.current_page("panel", "d1") == "print"


@pytest.mark.parametrize("source", ["scheduler", "scheduler_fallback", "webhook", "home_assistant"])
def test_gate_leaves_a_named_send_of_an_unselected_priority_step_alone(priority_frame, source):
    """ "print" is in the priority lineup and not its pick. A lineup step or a
    refresh of it is stale, but a schedule, a webhook or a Home Assistant
    command names that dashboard on purpose and must still go out."""
    scheduler, _transport, _nav, _states, _telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    assert scheduler.automatic_delivery_eligible_devices("print", {"panel"}, source, now=now) == {
        "panel"
    }
    for stale in ("deck", "rotation", "page_refresh"):
        assert scheduler.automatic_delivery_eligible_devices(
            "print", {"panel"}, stale, now=now
        ) == (set())


def test_schedule_for_an_unselected_priority_step_fires_once(priority_frame):
    """With the gate wired the way the app wires it, such a schedule came
    back ``held`` before ``_last_fired`` was set: it re-fired on every 30 s
    tick and its pill said the displays were paused."""
    from app.push import PushResult
    from app.state.schedule_model import Schedule

    scheduler, transport, _nav, _states, _telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    sent = transport.push

    def gated_push(page_id, *, device_ids=None, source="page", **kwargs):
        targets = set(device_ids or {"panel"})
        if not scheduler.automatic_delivery_eligible_devices(page_id, targets, source, now=now):
            return PushResult(status="held", page_id=page_id)
        return sent(page_id, device_ids=device_ids, source=source, **kwargs)

    transport.push = gated_push
    transport.sent.clear()
    record = Schedule(id="s", name="S", page_id="print", type="interval", interval_minutes=15)
    result = scheduler._fire(record, now, respect_quiet_hours=True)
    assert result.status == "sent"
    assert ("panel", "print", "scheduler") in transport.sent
    assert scheduler._last_fired["s"] == now.timestamp()
    assert scheduler._last_status["s"] != "held"


def test_gate_does_not_veto_a_follow_up(priority_frame):
    """A follow-up finishes the frame already on the display. A schedule can
    put a dashboard on a display whose lineup shows another one, so the
    "showing" map is the wrong question: the push manager already drops a
    follow-up once a newer frame has landed."""
    scheduler, _transport, nav, _states, _telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    assert nav.current_page("panel", "d1") == "photos"
    showing = scheduler._devices_showing_pages(now)
    assert "panel" not in showing.get("print", set())
    assert scheduler.automatic_delivery_eligible_devices("print", {"panel"}, "follow-up", now=now)
    assert scheduler.automatic_delivery_eligible("print", "panel", "follow-up", now=now)


def test_gate_reads_the_showing_map_once_for_any_number_of_displays(priority_frame, monkeypatch):
    scheduler, _transport, _nav, _states, _telemetry = priority_frame
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    scheduler._tick_once(now)
    reads = []
    real = scheduler._devices_showing_pages

    def counted(when):
        reads.append(when)
        return real(when)

    monkeypatch.setattr(scheduler, "_devices_showing_pages", counted)
    kept = scheduler.automatic_delivery_eligible_devices(
        "photos", {"panel", "hall", "study", "porch"}, "page_refresh", now=now
    )
    assert kept == {"panel"}
    assert len(reads) == 1
    # A lineup step is not a refresh: the map is not read at all.
    reads.clear()
    assert scheduler.automatic_delivery_eligible_devices("photos", {"panel"}, "deck", now=now)
    assert reads == []


def test_gate_is_inert_without_a_priority_lineup(tmp_path):
    """No priority lineup, no Home Assistant: every source gets every display
    back, apart from the refresh sources' own "is it showing" check."""
    scheduler = Scheduler(store=ScheduleStore(tmp_path / "s.json"), push_manager=lambda: None)
    now = datetime(2026, 11, 16, 11, 0, tzinfo=UTC)
    for source in ("scheduler", "webhook", "rotation", "deck", "follow-up", "home_assistant"):
        assert scheduler.automatic_delivery_eligible_devices(
            "home", {"kitchen", "hall"}, source, now=now
        ) == {"kitchen", "hall"}
