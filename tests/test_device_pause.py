"""Per-display automation pause (Home Assistant's Pause number).

``DeviceRotationState.automation_pause_until`` in the future keeps every
automated delivery off one display: cycle lineup advances, daily / interval
lineup fires, rotations, schedules, page refreshes, home returns and
rejoins. Manual sends and Play / Fire now still go. When the pause runs out
(or is set to 0) the scheduler clears it and catches the display up on what
it missed. It is separate from ``override_until`` (a button's hold, and the
Hold number), which the pause never touches."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.push import PushResult, RendererResult
from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_model import DeviceRotationState
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.legacy_projections import RotationProjection
from app.state.page_store import Page, PageStore
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore

DAY = datetime(2026, 6, 15, tzinfo=UTC)


def at(hh: int, mm: int, ss: int = 0) -> datetime:
    return DAY.replace(hour=hh, minute=mm, second=ss)


@dataclass
class RecPush:
    """Records every push; per-display renderer rows like the real one."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    bound: dict[str, list[str]] = field(default_factory=dict)
    quiet: set[str] = field(default_factory=set)
    fail: set[str] = field(default_factory=set)
    showing: dict[str, str] = field(default_factory=dict)  # device -> page on its glass

    def push(self, page_id: str, **kw: Any) -> PushResult:
        self.calls.append({"page_id": page_id, **kw})
        targets = set(kw.get("device_ids") or self.bound.get(page_id, []))
        targets -= set(kw.get("exclude_device_ids") or ())
        if not targets:
            return PushResult(status="held", page_id=page_id)
        if kw.get("respect_quiet_hours"):
            targets -= self.quiet
            if not targets:
                return PushResult(status="quiet", page_id=page_id)
        if page_id in self.fail:
            return PushResult(status="failed", page_id=page_id, error="render failed")
        rows = [
            RendererResult(renderer_id=f"r__{d}", topic="", digest="x", url="", bytes_written=1)
            for d in sorted(targets)
        ]
        return PushResult(status="sent", page_id=page_id, renderers=rows)

    def promote_deck_page(self, _device_id: str, _page_id: str) -> bool:
        return False

    def device_in_quiet_hours(self, device_id: str) -> bool:
        return device_id in self.quiet

    def latest_render_for(self, device_id: str) -> dict[str, Any] | None:
        page = self.showing.get(device_id)
        return {"page_id": page} if page is not None else None

    def to(self) -> list[tuple[str, frozenset[str] | None]]:
        """(page, explicit device set) per call, for assertions."""
        out = []
        for c in self.calls:
            ids = c.get("device_ids")
            out.append((c["page_id"], frozenset(ids) if ids is not None else None))
        return out


@dataclass
class Rig:
    sched: Scheduler
    push: RecPush
    decks: DeckStore
    nav: DeckNavStore
    states: DeviceRotationStateStore
    schedules: ScheduleStore
    pages: PageStore


def _rig(tmp_path: Path, bound: dict[str, list[str]] | None = None) -> Rig:
    decks = DeckStore(tmp_path / "decks.json")
    nav = DeckNavStore(tmp_path / "nav.json")
    states = DeviceRotationStateStore(tmp_path / "state.json")
    schedules = ScheduleStore(tmp_path / "schedules.json")
    pages = PageStore(tmp_path / "pages.json")
    push = RecPush(bound=dict(bound or {}))
    sched = Scheduler(
        store=schedules,
        rotation_store=RotationProjection(decks),
        rotation_state_store=states,
        deck_store=decks,
        deck_nav_store=nav,
        page_store=pages,
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
        device_ids_for_page=lambda pid: list(push.bound.get(pid, [])),
    )
    return Rig(sched, push, decks, nav, states, schedules, pages)


def _pause(rig: Rig, device_id: str, until: datetime) -> None:
    state = rig.states.get(device_id) or DeviceRotationState(device_id=device_id)
    rig.states.upsert(state.model_copy(update={"automation_pause_until": until}))


def _cycle(devices: list[str], **kw: Any) -> Deck:
    base: dict[str, Any] = dict(
        id="cycle",
        name="Cycle",
        device_ids=devices,
        advance="timer",
        advance_interval_minutes=30,
        advance_anchor="00:00",
        advance_min_hold_minutes=0,
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )
    base.update(kw)
    return Deck(**base)


# ---------- cycle lineups ----------


def test_a_paused_display_misses_lineup_advances_and_catches_up_when_it_ends(
    tmp_path: Path,
) -> None:
    # The reproduced bug: Pause=60 and the timer still pushed.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))
    assert rig.push.to() == [("a", frozenset({"frame"}))]
    _pause(rig, "frame", at(0, 50))
    rig.push.calls.clear()

    rig.sched._tick_once(at(0, 30))  # b is due
    assert rig.push.calls == []
    assert rig.nav.current_page("frame", "cycle") == "a"

    rig.sched._tick_once(at(0, 55))  # the pause ran out at 00:50
    assert rig.push.to() == [("b", frozenset({"frame"}))]
    assert rig.states.get("frame").automation_pause_until is None
    assert rig.nav.current_page("frame", "cycle") == "b"
    rig.push.calls.clear()
    rig.sched._tick_once(at(1, 0))  # and the lineup carries on
    assert rig.push.to() == [("a", frozenset({"frame"}))]


def test_only_the_paused_display_sits_out(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame", "hall"]))
    rig.sched._tick_once(at(0, 0))
    _pause(rig, "frame", at(0, 50))
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 30))
    assert rig.push.to() == [("b", frozenset({"hall"}))]
    rig.push.calls.clear()
    # The release repaints the paused display only; hall already shows b.
    rig.sched._tick_once(at(0, 55))
    assert rig.push.to() == [("b", frozenset({"frame"}))]


def test_ending_a_pause_with_nothing_missed_does_not_repaint(tmp_path: Path) -> None:
    # The lineup never moved during the pause, so there is nothing to catch
    # up on, whatever else was sent to the display by hand in between.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))  # a, and a is still current at 00:21
    _pause(rig, "frame", at(0, 20))
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 21))
    assert rig.push.calls == []
    assert rig.states.get("frame").automation_pause_until is None
    rig.sched._tick_once(at(0, 30))  # the lineup carries on as usual
    assert rig.push.to() == [("b", frozenset({"frame"}))]


def test_pause_zero_on_an_idle_display_does_not_repaint(tmp_path: Path) -> None:
    # Home Assistant writes "now" for 0 on a display that sat idle.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))
    rig.push.calls.clear()
    _pause(rig, "frame", at(0, 10))
    rig.sched._tick_once(at(0, 10, 30))
    assert rig.push.calls == []


def test_pause_zero_ends_it_on_the_next_tick(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))
    _pause(rig, "frame", at(2, 0))
    rig.sched._tick_once(at(0, 30))
    rig.push.calls.clear()
    _pause(rig, "frame", at(0, 40))  # Home Assistant writes "now" for 0
    rig.sched._tick_once(at(0, 40, 30))
    assert rig.push.to() == [("b", frozenset({"frame"}))]


def test_ending_a_pause_does_not_repaint_an_unbound_lineup_already_on_its_step(
    tmp_path: Path,
) -> None:
    # A lineup bound to no display reaches panels through its pages and has no
    # nav record: what the display shows is the last frame published to it.
    rig = _rig(tmp_path, bound={"a": ["frame"], "b": ["frame"]})
    rig.decks.upsert(_cycle([]))
    rig.sched._tick_once(at(0, 0))
    assert rig.push.to() == [("a", None)]  # by the pages' bindings
    rig.push.showing["frame"] = "a"
    rig.push.calls.clear()
    _pause(rig, "frame", at(0, 10))  # Pause 0, or a pause that ran out
    rig.sched._tick_once(at(0, 10, 30))
    assert rig.push.calls == []
    assert rig.states.get("frame").automation_pause_until is None
    assert "frame" not in rig.sched._pause_catchups
    rig.sched._tick_once(at(0, 30))  # the lineup carries on as usual
    assert rig.push.to() == [("b", None)]


def test_ending_a_pause_still_catches_an_unbound_lineup_up_when_it_moved(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path, bound={"a": ["frame"], "b": ["frame"]})
    rig.decks.upsert(_cycle([]))
    rig.sched._tick_once(at(0, 0))
    rig.push.showing["frame"] = "a"
    _pause(rig, "frame", at(0, 50))
    rig.sched._tick_once(at(0, 30))  # b is due, frame paused
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 55))
    assert rig.push.to() == [("b", frozenset({"frame"}))]


# ---------- daily / interval lineups, schedules, rotations ----------


def test_daily_lineup_fire_waits_for_the_pause_then_lands(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(
        Deck(
            id="dinner",
            name="Dinner",
            device_ids=["frame"],
            advance="timer",
            advance_trigger="daily",
            advance_fires_at="18:00",
            advance_min_hold_minutes=0,
            pages=[DeckPage(page_id="x"), DeckPage(page_id="y")],
        )
    )
    rig.sched._tick_once(at(17, 0))  # observed before the slot
    _pause(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    rig.sched._tick_once(at(18, 10))
    assert rig.push.calls == []
    rig.sched._tick_once(at(18, 31))
    assert rig.push.to() == [("x", frozenset({"frame"}))]
    rig.sched._tick_once(at(19, 0))
    assert len(rig.push.calls) == 1  # once that day


def test_interval_schedule_leaves_a_paused_display_out(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame", "hall"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    _pause(rig, "frame", at(12, 0))
    rig.sched._tick_once(at(10, 0))
    # Delivered by binding as usual, with the paused display left out.
    assert len(rig.push.calls) == 1
    call = rig.push.calls[0]
    assert call["page_id"] == "news" and "device_ids" not in call
    assert call["exclude_device_ids"] == {"frame"}


def test_schedule_with_every_display_paused_lands_when_the_pause_ends(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    _pause(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))
    rig.sched._tick_once(at(10, 10))
    assert rig.push.calls == []
    rig.sched._tick_once(at(10, 21))
    assert rig.push.to() == [("news", frozenset({"frame"}))]
    rig.sched._tick_once(at(10, 25))
    assert len(rig.push.calls) == 1  # caught up once, not again


def test_unbound_rotation_leaves_a_paused_display_out(tmp_path: Path) -> None:
    # A cycle bound to no display reaches panels through its pages.
    rig = _rig(tmp_path, bound={"a": ["frame", "hall"], "b": ["frame", "hall"]})
    rig.decks.upsert(_cycle([]))
    _pause(rig, "frame", at(2, 0))
    rig.sched._tick_once(at(0, 0))
    assert rig.push.to() == [("a", frozenset({"hall"}))]


def test_page_refresh_skips_a_paused_display(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.pages.save(Page(id="clock", name="Clock", device_ids=["frame"], refresh_minutes=5))
    rig.sched._tick_once(at(9, 0))
    assert rig.push.to() == [("clock", frozenset({"frame"}))]
    rig.push.calls.clear()
    _pause(rig, "frame", at(9, 20))
    rig.sched._tick_once(at(9, 5))
    rig.sched._tick_once(at(9, 10))
    assert rig.push.calls == []
    rig.sched._tick_once(at(9, 21))
    assert rig.push.to() == [("clock", frozenset({"frame"}))]


def test_home_return_waits_for_the_pause(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(
        Deck(
            id="nav",
            name="Nav",
            device_ids=["frame"],
            home_timeout_minutes=5,
            pages=[DeckPage(page_id="home"), DeckPage(page_id="detail")],
        )
    )
    rig.nav.set("frame", "nav", "detail")
    _pause(rig, "frame", datetime.now(UTC) + timedelta(hours=1))
    later = datetime.now(UTC) + timedelta(minutes=10)
    rig.sched._maybe_return_decks_home(later)
    assert rig.push.calls == []
    assert rig.nav.current_page("frame", "nav") == "detail"
    _pause(rig, "frame", datetime.now(UTC) - timedelta(seconds=1))
    rig.sched._maybe_return_decks_home(later)
    assert rig.push.to() == [("home", frozenset({"frame"}))]


def test_rejoin_after_a_button_hold_waits_for_the_display_pause(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.states.upsert(
        DeviceRotationState(
            device_id="frame",
            rotation_id="cycle",
            step_index=1,
            override_until=at(0, 5),  # the press's hold ran out
            automation_pause_until=at(1, 0),  # but the display is paused
        )
    )
    rig.sched._maybe_rejoin_rotations(at(0, 10))
    assert rig.push.calls == []
    assert rig.states.get("frame").override_until == at(0, 5)


# ---------- manual paths still go ----------


def test_manual_fire_and_play_reach_a_paused_display(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame"], "a": ["frame"], "b": ["frame"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    rig.decks.upsert(_cycle([]))
    _pause(rig, "frame", datetime.now(UTC) + timedelta(hours=1))
    assert rig.sched.fire_now("news").status == "sent"
    rotation = next(r for r in rig.sched._cycle_records() if r.id == "cycle")
    result = rig.sched._fire_rotation(rotation, 1, datetime.now(UTC), bypass_holds=True)
    assert result.status == "sent"
    assert [c["page_id"] for c in rig.push.calls] == ["news", "b"]
    assert all("exclude_device_ids" not in c for c in rig.push.calls)


# ---------- Home Assistant's Pause number drives it ----------


def _ha_for(rig: Rig) -> Any:
    from unittest.mock import MagicMock

    from app.ha_discovery import HaHooks, HomeAssistantDiscovery

    device = MagicMock(id="frame", display_name="Frame", kind_of="x", panel={"w": 1, "h": 1})
    device.manifest = {}
    device.config_schema = {}
    registry = MagicMock()
    registry.get.return_value = device
    registry.all.return_value = [device]
    return HomeAssistantDiscovery(
        transport=MagicMock(connected=False),
        push_manager=MagicMock(),
        page_store=rig.pages,
        base_url_fn=lambda: "http://x",
        device_registry=registry,
        hooks=HaHooks(rotation_state_store=rig.states),
        state_refresh_s=0,
    )


def test_home_assistant_pause_stops_and_resumes_the_display(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    ha = _ha_for(rig)
    # Half-hour windows alternate a / b; start in the one the clock is in.
    now = datetime.now(UTC)
    start = now.replace(minute=now.minute // 30 * 30, second=0, microsecond=0)
    rig.sched._tick_once(start)
    first = rig.push.calls[0]["page_id"]
    rig.push.calls.clear()
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/pause", b"60")
    states = rig.sched._pause_states()
    assert rig.sched._pause_ids(now + timedelta(minutes=1), states) == {"frame"}
    rig.sched._tick_once(start + timedelta(minutes=31))  # the next window, paused
    assert rig.push.calls == []
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/pause", b"0")
    rig.sched._tick_once(start + timedelta(minutes=35))
    other = "b" if first == "a" else "a"
    assert [(c["page_id"], c["device_ids"]) for c in rig.push.calls] == [(other, {"frame"})]
    assert rig.states.get("frame").automation_pause_until is None
    # Junk never sets a pause.
    for junk in (b"", b"soon", b"nan", b"inf", b"1e999"):
        ha._on_device_cmd("tesserae/ha/dev/frame/cmd/pause", junk)
    assert rig.states.get("frame").automation_pause_until is None


def test_pause_never_touches_a_button_hold_nor_the_rotation_position(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    ha = _ha_for(rig)
    hold = datetime.now(UTC) + timedelta(hours=2)
    rig.states.upsert(
        DeviceRotationState(
            device_id="frame", rotation_id="cycle", step_index=1, override_until=hold
        )
    )
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/pause", b"30")
    after = rig.states.get("frame")
    assert after is not None and after.automation_pause_until is not None
    assert (after.override_until, after.rotation_id, after.step_index) == (hold, "cycle", 1)
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/pause", b"0")
    after = rig.states.get("frame")
    assert after is not None and after.override_until == hold


def test_hold_leaves_the_pause_alone_and_zero_on_an_idle_display_pushes_nothing(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    ha = _ha_for(rig)
    rig.sched._tick_once(at(0, 0))
    rig.push.calls.clear()
    pause_until = datetime.now(UTC) + timedelta(hours=1)
    _pause(rig, "frame", pause_until)
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", b"20")
    state = rig.states.get("frame")
    assert state is not None and state.override_until is not None
    assert state.automation_pause_until == pause_until
    # Hold 0 on an idle display, no pause involved: nothing is repainted
    # and no pause appears.
    (tmp_path / "idle").mkdir()
    idle = _rig(tmp_path / "idle")
    idle.decks.upsert(_cycle(["frame"]))
    ha_idle = _ha_for(idle)
    idle.sched._tick_once(at(0, 0))
    idle.push.calls.clear()
    ha_idle._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", b"0")
    idle.sched._tick_once(at(0, 5))
    assert idle.push.calls == []
    stored = idle.states.get("frame")
    assert stored is not None and stored.automation_pause_until is None


def test_a_press_rendering_cannot_undo_a_pause_set_meanwhile(tmp_path: Path) -> None:
    from app.button_service import ButtonService
    from app.state.rotation_model import Rotation, RotationStep
    from app.state.rotation_store import RotationStore
    from app.state.settings_store import SettingsStore

    states = DeviceRotationStateStore(tmp_path / "state.json")
    rotations = RotationStore(tmp_path / "rotations.json")
    rotations.upsert(
        Rotation(
            id="kitchen_rot",
            name="Kitchen",
            device_ids=["kitchen"],
            steps=[
                RotationStep(page_id="morning", dwell_minutes=30),
                RotationStep(page_id="afternoon", dwell_minutes=30),
            ],
        )
    )
    pages = PageStore(tmp_path / "pages.json")
    for pid in ("morning", "afternoon"):
        pages.save(Page(id=pid, name=pid, device_id="kitchen"))
    pause_until = datetime.now(UTC) + timedelta(hours=1)

    class PauseDuringRender:
        def push(self, page_id: str, **_kw: Any) -> PushResult:
            # Home Assistant sets the Pause while the press renders.
            states.patch("kitchen", {"automation_pause_until": pause_until})
            return PushResult(status="sent", page_id=page_id)

        def latest_render_for(self, _device_id: str) -> None:
            return None

    svc = ButtonService(
        rotation_store=rotations,
        state_store=states,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        page_store=pages,
        push_manager=PauseDuringRender(),  # type: ignore[arg-type]
    )
    result = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    assert result.pushed_page_id == "afternoon"
    after = states.get("kitchen")
    assert after is not None
    assert after.automation_pause_until == pause_until  # the press kept it
    assert after.override_until is not None and after.step_index == 1  # and its own hold


# ---------- the push manager's own exclusion ----------


def _real_push_manager(tmp_path: Path) -> tuple[Any, Any]:
    """A real PushManager over the bundled devices, one esp32 instance
    ``hallway`` and a page ``home`` bound to it."""

    from app import device_loader, device_service, renderer_loader
    from app.main import REPO_ROOT
    from app.push import PushManager
    from app.state.event_log import EventLog
    from app.state.settings_store import SettingsStore
    from app.transport import BrokerConfig, MqttTransport

    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path / "devices",
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path / "rdata",
    )
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=tmp_path / "devices",
        instance_id="hallway",
        kind_id="esp32_client",
    )
    pages = PageStore(tmp_path / "pages.json")
    pages.save(Page(id="home", name="Home", device_ids=["hallway"], cells=[]))

    class _Fake:
        def __init__(self, *a: Any, **kw: Any) -> None:
            self.on_connect = self.on_disconnect = self.on_message = None

        def username_pw_set(self, *a: Any, **kw: Any) -> None:
            pass

        def connect(self, *a: Any, **kw: Any) -> int:
            return 0

        def disconnect(self) -> int:
            return 0

        def loop_start(self) -> int:
            return 0

        def loop_stop(self) -> int:
            return 0

        def publish(self, *a: Any, **kw: Any) -> Any:
            return type("R", (), {"rc": 0})()

        def subscribe(self, *a: Any, **kw: Any) -> tuple[int, int]:
            return (0, 1)

    transport = MqttTransport(BrokerConfig(host="x"), client_factory=_Fake)
    transport.connect()
    events = EventLog(tmp_path / "events.db")
    manager = PushManager(
        registry=renderers,
        page_store=pages,
        transport=transport,
        settings=SettingsStore(tmp_path / "settings.json"),
        event_log=events,
        renders_dir=tmp_path / "renders",
        base_url_fn=lambda: "http://broker.local:8000",
        devices=devices,
    )
    return manager, events


def test_push_manager_excludes_paused_displays(tmp_path: Path) -> None:
    import io
    from unittest.mock import patch

    from PIL import Image

    manager, events = _real_push_manager(tmp_path)
    png = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(png, format="PNG")
    with patch("app.push.render_to_png") as rtp:
        rtp.side_effect = AssertionError("nothing to render for a paused display")
        result = manager.push("home", exclude_device_ids={"hallway"}, settle=False)
    assert result.status == "held"
    assert events.list(limit=5) == []  # no History row: callers retry


# ---------- shared fires and quiet hours ----------


def _shared_dinner() -> Deck:
    return Deck(
        id="dinner",
        name="Dinner",
        device_ids=["frame", "hall"],
        advance="timer",
        advance_trigger="daily",
        advance_fires_at="18:00",
        advance_min_hold_minutes=0,
        pages=[DeckPage(page_id="x"), DeckPage(page_id="y")],
    )


def test_a_shared_daily_fire_reaches_the_paused_display_when_its_pause_ends(
    tmp_path: Path,
) -> None:
    # The reproduced bug: dinner targets frame and hall, frame is paused, hall
    # gets dinner, and ending frame's pause never delivered it.
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _pause(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    assert rig.push.to() == [("x", frozenset({"hall"}))]
    rig.push.calls.clear()
    rig.sched._tick_once(at(18, 31))
    # Only the display that missed it, and only once.
    assert rig.push.to() == [("x", frozenset({"frame"}))]
    assert rig.nav.current_page("frame", "dinner") == "x"
    rig.sched._tick_once(at(18, 40))
    assert len(rig.push.calls) == 1


def test_a_missed_fire_expires_with_its_day(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _pause(rig, "frame", DAY + timedelta(days=1, hours=8))
    rig.sched._tick_once(at(18, 0, 30))
    rig.push.calls.clear()
    # The pause ends the next morning: yesterday's dinner isn't delivered.
    rig.sched._tick_once(DAY + timedelta(days=1, hours=8, minutes=1))
    assert [c["page_id"] for c in rig.push.calls] == []


def test_a_newer_lineup_step_wins_over_an_older_missed_fire(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.decks.upsert(_cycle(["frame"], advance_anchor="19:00", advance_end_at="23:00"))
    rig.sched._tick_once(at(17, 0))
    _pause(rig, "frame", at(19, 20))
    rig.sched._tick_once(at(18, 0, 30))  # dinner, frame paused
    rig.sched._tick_once(at(19, 0))  # the evening lineup starts, frame paused
    rig.push.calls.clear()
    rig.sched._tick_once(at(19, 21))
    # What frame would show had it never been paused: the 19:00 step.
    assert rig.push.to() == [("a", frozenset({"frame"}))]


def test_a_pause_ending_in_quiet_hours_catches_up_when_they_end(tmp_path: Path) -> None:
    # The reproduced bug: the catch-up was spent in quiet hours, so nothing
    # reached the display until the next boundary.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"], advance_interval_minutes=60))
    rig.sched._tick_once(at(0, 0))
    _pause(rig, "frame", at(1, 20))
    rig.sched._tick_once(at(1, 0))  # b is due while paused
    rig.push.quiet.add("frame")
    rig.push.calls.clear()
    rig.sched._tick_once(at(1, 21))
    rig.sched._tick_once(at(1, 30))
    assert rig.push.calls == []  # quiet hours are respected
    rig.push.quiet.discard("frame")
    rig.sched._tick_once(at(1, 40))  # quiet hours over, same step
    assert rig.push.to() == [("b", frozenset({"frame"}))]
    rig.sched._tick_once(at(1, 50))
    assert len(rig.push.calls) == 1


def test_a_missed_fire_waits_out_quiet_hours_too(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _pause(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    rig.push.quiet.add("frame")
    rig.push.calls.clear()
    rig.sched._tick_once(at(18, 31))
    assert rig.push.calls == []
    rig.push.quiet.discard("frame")
    rig.sched._tick_once(at(19, 0))
    assert rig.push.to() == [("x", frozenset({"frame"}))]


def test_a_failed_catch_up_retries_after_a_pause(tmp_path: Path) -> None:
    from app.scheduler import PAUSE_CATCHUP_RETRY_S

    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"], advance_interval_minutes=60))
    rig.sched._tick_once(at(0, 0))
    _pause(rig, "frame", at(1, 20))
    rig.sched._tick_once(at(1, 0))  # b is due while paused
    rig.push.fail.add("b")
    rig.push.calls.clear()
    rig.sched._tick_once(at(1, 21))
    rig.sched._tick_once(at(1, 21, 30))  # inside the retry wait
    assert len(rig.push.calls) == 1
    rig.push.fail.clear()
    rig.sched._tick_once(at(1, 21) + timedelta(seconds=PAUSE_CATCHUP_RETRY_S + 1))
    assert len(rig.push.calls) == 2
    assert rig.push.calls[-1]["page_id"] == "b"
    assert "frame" not in rig.sched._pause_catchups


def _dinner_missed(rig: Rig) -> None:
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _pause(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    rig.push.calls.clear()


def test_a_deleted_lineup_is_not_replayed(tmp_path: Path) -> None:
    # The reproduced bug: dinner deleted while frame was paused, and dinner
    # still painted when the pause ended.
    rig = _rig(tmp_path)
    _dinner_missed(rig)
    rig.decks.delete("dinner")
    rig.sched._tick_once(at(18, 31))
    assert rig.push.calls == []
    assert "frame" not in rig.sched._pause_catchups


def test_a_disabled_lineup_is_not_replayed(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _dinner_missed(rig)
    rig.decks.upsert(_shared_dinner().model_copy(update={"enabled": False}))
    rig.sched._tick_once(at(18, 31))
    assert rig.push.calls == []


def test_a_lineup_that_dropped_the_display_is_not_replayed(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _dinner_missed(rig)
    rig.decks.upsert(_shared_dinner().model_copy(update={"device_ids": ["hall"]}))
    rig.sched._tick_once(at(18, 31))
    assert rig.push.calls == []


def test_a_deleted_schedule_is_not_replayed(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame", "hall"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=60)
    )
    _pause(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))
    rig.push.calls.clear()
    rig.schedules.delete("news")
    rig.sched._tick_once(at(10, 21))
    assert rig.push.calls == []


# ---------- a missed fire re-checks its conditions at replay ----------


def _conditional_news(rig: Rig, states: list[dict[str, Any]], **kw: Any) -> None:
    from app.scheduler_conditions import ConditionEvaluator
    from app.state.conditions import ha_condition

    rig.sched._condition_evaluator = ConditionEvaluator(
        ha_get_states=lambda: states, timezone_provider=lambda: None
    )
    rig.sched._condition_evaluator.refresh_ha_states()
    rig.push.bound["news"] = ["frame"]
    rig.schedules.upsert(
        Schedule(
            id="news",
            name="News",
            page_id="news",
            type="interval",
            interval_minutes=60,
            conditions=[ha_condition("binary_sensor.home", "==", "on")],
            **kw,
        )
    )


def _home(state: str) -> list[dict[str, Any]]:
    return [{"entity_id": "binary_sensor.home", "state": state, "last_changed": ""}]


def test_a_missed_fire_is_dropped_when_its_conditions_turned_false(tmp_path: Path) -> None:
    # The reproduced bug: the fire was recorded while home was on, and it
    # replayed after home went off.
    rig = _rig(tmp_path)
    states = _home("on")
    _conditional_news(rig, states)
    _pause(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))  # conditions hold, frame is paused
    assert rig.push.calls == []
    states[:] = _home("off")
    rig.sched._tick_once(at(10, 21))
    assert rig.push.calls == []
    assert "frame" not in rig.sched._pause_catchups


def test_a_missed_fire_goes_to_the_fallback_when_its_conditions_turned_false(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    states = _home("on")
    _conditional_news(rig, states, fallback_page_id="calm")
    _pause(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))
    states[:] = _home("off")
    rig.sched._tick_once(at(10, 21))
    assert rig.push.to() == [("calm", frozenset({"frame"}))]
    assert rig.push.calls[0]["source"] == "scheduler_fallback"


def test_a_missed_fire_whose_conditions_still_hold_replays_its_page(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _conditional_news(rig, _home("on"), fallback_page_id="calm")
    _pause(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))
    rig.sched._tick_once(at(10, 21))
    assert rig.push.to() == [("news", frozenset({"frame"}))]
    assert rig.push.calls[0]["source"] == "scheduler"


def test_a_missed_fallback_fire_replays_the_page_once_its_conditions_hold(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    states = _home("off")
    _conditional_news(rig, states, fallback_page_id="calm")
    _pause(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))  # conditions false: the fallback was due
    states[:] = _home("on")
    rig.sched._tick_once(at(10, 21))
    assert rig.push.to() == [("news", frozenset({"frame"}))]


# ---------- the pause lookup is read once per tick ----------


def test_a_tick_reads_the_pause_state_once(tmp_path: Path) -> None:
    from unittest.mock import patch

    rig = _rig(tmp_path, bound={"news": ["frame", "hall"], "clock": ["frame"]})
    rig.decks.upsert(_cycle(["frame", "hall"]))
    rig.decks.upsert(_cycle(["frame"], id="second", name="Second"))
    rig.decks.upsert(
        Deck(
            id="nav",
            name="Nav",
            device_ids=["hall"],
            home_timeout_minutes=5,
            pages=[DeckPage(page_id="home"), DeckPage(page_id="detail")],
        )
    )
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    rig.pages.save(Page(id="clock", name="Clock", device_ids=["frame"], refresh_minutes=5))
    _pause(rig, "frame", at(5, 0))
    rig.sched._tick_once(at(0, 0))  # observe
    reads = 0
    real_pause_states = rig.sched._pause_states

    def counting() -> Any:
        nonlocal reads
        reads += 1
        return real_pause_states()

    with patch.object(rig.sched, "_pause_states", counting):
        rig.sched._tick_once(at(0, 30))
    assert reads == 1
    assert rig.push.calls  # and the tick did fire things for the unpaused display
