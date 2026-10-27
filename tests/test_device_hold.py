"""Per-display automation hold (Home Assistant's Hold number).

``DeviceRotationState.automation_hold_until`` in the future keeps every
automated delivery off one display: cycle lineup advances, daily / interval
lineup fires, rotations, schedules, page refreshes, home returns and
rejoins. Manual sends, Play / Fire now and Next / Previous still go. When
the hold runs out (or is set to 0) the scheduler clears it and puts the
display back on its lineup's current step."""

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

    def latest_render_for(self, _device_id: str) -> dict[str, Any] | None:
        return None

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


def _hold(rig: Rig, device_id: str, until: datetime) -> None:
    state = rig.states.get(device_id) or DeviceRotationState(device_id=device_id)
    rig.states.upsert(state.model_copy(update={"automation_hold_until": until}))


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


def test_a_held_display_misses_lineup_advances_and_rejoins_when_it_ends(tmp_path: Path) -> None:
    # The reproduced bug: Hold=60 and the timer still pushed.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))
    assert rig.push.to() == [("a", frozenset({"frame"}))]
    _hold(rig, "frame", at(1, 20))
    rig.push.calls.clear()

    rig.sched._tick_once(at(0, 30))  # b is due
    rig.sched._tick_once(at(1, 0))  # a again
    assert rig.push.calls == []
    assert rig.nav.current_page("frame", "cycle") == "a"

    rig.sched._tick_once(at(1, 25))  # the hold ran out at 01:20
    assert rig.push.to() == [("a", frozenset({"frame"}))]
    assert rig.states.get("frame").automation_hold_until is None
    rig.push.calls.clear()
    rig.sched._tick_once(at(1, 30))  # and the lineup carries on
    assert rig.push.to() == [("b", frozenset({"frame"}))]


def test_only_the_held_display_sits_out(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame", "hall"]))
    rig.sched._tick_once(at(0, 0))
    _hold(rig, "frame", at(1, 20))
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 30))
    assert rig.push.to() == [("b", frozenset({"hall"}))]
    rig.sched._tick_once(at(1, 0))
    rig.push.calls.clear()
    # The release repaints the held display only; hall already shows a.
    rig.sched._tick_once(at(1, 25))
    assert rig.push.to() == [("a", frozenset({"frame"}))]


def test_release_repaints_over_a_page_sent_by_hand_during_the_hold(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))  # a, and a is still current at 00:20
    _hold(rig, "frame", at(0, 20))
    # (a manual send of some other page happened in between)
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 21))
    assert rig.push.to() == [("a", frozenset({"frame"}))]


def test_hold_zero_ends_it_on_the_next_tick(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.sched._tick_once(at(0, 0))
    _hold(rig, "frame", at(2, 0))
    rig.sched._tick_once(at(0, 30))
    rig.push.calls.clear()
    _hold(rig, "frame", at(0, 40))  # Home Assistant writes "now" for 0
    rig.sched._tick_once(at(0, 40, 30))
    assert rig.push.to() == [("b", frozenset({"frame"}))]


# ---------- daily / interval lineups, schedules, rotations ----------


def test_daily_lineup_fire_waits_for_the_hold_then_lands(tmp_path: Path) -> None:
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
    _hold(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    rig.sched._tick_once(at(18, 10))
    assert rig.push.calls == []
    rig.sched._tick_once(at(18, 31))
    assert rig.push.to() == [("x", frozenset({"frame"}))]
    rig.sched._tick_once(at(19, 0))
    assert len(rig.push.calls) == 1  # once that day


def test_interval_schedule_leaves_a_held_display_out(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame", "hall"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    _hold(rig, "frame", at(12, 0))
    rig.sched._tick_once(at(10, 0))
    # Delivered by binding as usual, with the held display left out.
    assert len(rig.push.calls) == 1
    call = rig.push.calls[0]
    assert call["page_id"] == "news" and "device_ids" not in call
    assert call["exclude_device_ids"] == {"frame"}


def test_schedule_with_every_display_held_lands_when_the_hold_ends(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    _hold(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))
    rig.sched._tick_once(at(10, 10))
    assert rig.push.calls == []
    rig.sched._tick_once(at(10, 21))
    assert rig.push.to() == [("news", frozenset({"frame"}))]
    rig.sched._tick_once(at(10, 25))
    assert len(rig.push.calls) == 1  # caught up once, not again


def test_unbound_rotation_leaves_a_held_display_out(tmp_path: Path) -> None:
    # A cycle bound to no display reaches panels through its pages.
    rig = _rig(tmp_path, bound={"a": ["frame", "hall"], "b": ["frame", "hall"]})
    rig.decks.upsert(_cycle([]))
    _hold(rig, "frame", at(2, 0))
    rig.sched._tick_once(at(0, 0))
    assert rig.push.to() == [("a", frozenset({"hall"}))]


def test_page_refresh_skips_a_held_display(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.pages.save(Page(id="clock", name="Clock", device_ids=["frame"], refresh_minutes=5))
    rig.sched._tick_once(at(9, 0))
    assert rig.push.to() == [("clock", frozenset({"frame"}))]
    rig.push.calls.clear()
    _hold(rig, "frame", at(9, 20))
    rig.sched._tick_once(at(9, 5))
    rig.sched._tick_once(at(9, 10))
    assert rig.push.calls == []
    rig.sched._tick_once(at(9, 21))
    assert rig.push.to() == [("clock", frozenset({"frame"}))]


def test_home_return_waits_for_the_hold(tmp_path: Path) -> None:
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
    _hold(rig, "frame", datetime.now(UTC) + timedelta(hours=1))
    later = datetime.now(UTC) + timedelta(minutes=10)
    rig.sched._maybe_return_decks_home(later)
    assert rig.push.calls == []
    assert rig.nav.current_page("frame", "nav") == "detail"
    _hold(rig, "frame", datetime.now(UTC) - timedelta(seconds=1))
    rig.sched._maybe_return_decks_home(later)
    assert rig.push.to() == [("home", frozenset({"frame"}))]


def test_rejoin_after_a_button_hold_waits_for_the_display_hold(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.states.upsert(
        DeviceRotationState(
            device_id="frame",
            rotation_id="cycle",
            step_index=1,
            override_until=at(0, 5),  # the press's hold ran out
            automation_hold_until=at(1, 0),  # but the display is held
        )
    )
    rig.sched._maybe_rejoin_rotations(at(0, 10))
    assert rig.push.calls == []
    assert rig.states.get("frame").override_until == at(0, 5)


# ---------- manual paths still go ----------


def test_manual_fire_and_play_reach_a_held_display(tmp_path: Path) -> None:
    rig = _rig(tmp_path, bound={"news": ["frame"], "a": ["frame"], "b": ["frame"]})
    rig.schedules.upsert(
        Schedule(id="news", name="News", page_id="news", type="interval", interval_minutes=30)
    )
    rig.decks.upsert(_cycle([]))
    _hold(rig, "frame", datetime.now(UTC) + timedelta(hours=1))
    assert rig.sched.fire_now("news").status == "sent"
    rotation = next(r for r in rig.sched._cycle_records() if r.id == "cycle")
    result = rig.sched._fire_rotation(rotation, 1, datetime.now(UTC), bypass_holds=True)
    assert result.status == "sent"
    assert [c["page_id"] for c in rig.push.calls] == ["news", "b"]
    assert all("exclude_device_ids" not in c for c in rig.push.calls)


def test_next_still_steps_a_held_display(tmp_path: Path) -> None:
    from app.button_service import ButtonService
    from app.state.settings_store import SettingsStore

    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    rig.nav.set("frame", "cycle", "a")
    _hold(rig, "frame", datetime.now(UTC) + timedelta(hours=1))
    svc = ButtonService(
        rotation_store=RotationProjection(rig.decks),
        state_store=rig.states,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        page_store=rig.pages,
        push_manager=rig.push,  # type: ignore[arg-type]
        deck_store=rig.decks,
        deck_nav_store=rig.nav,
    )
    assert svc.step(device_id="frame", forward=True).pushed_page_id == "b"


# ---------- Home Assistant's Hold number drives it ----------


def test_home_assistant_hold_pauses_and_resumes_the_display(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from app.ha_discovery import HaHooks, HomeAssistantDiscovery

    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"]))
    device = MagicMock(id="frame", display_name="Frame", kind_of="x", panel={"w": 1, "h": 1})
    device.manifest = {}
    device.config_schema = {}
    registry = MagicMock()
    registry.get.return_value = device
    registry.all.return_value = [device]
    transport = MagicMock(connected=False)
    ha = HomeAssistantDiscovery(
        transport=transport,
        push_manager=MagicMock(),
        page_store=rig.pages,
        base_url_fn=lambda: "http://x",
        device_registry=registry,
        hooks=HaHooks(rotation_state_store=rig.states),
        state_refresh_s=0,
    )
    now = datetime.now(UTC)
    rig.sched._tick_once(now)
    rig.push.calls.clear()
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", b"60")
    assert rig.sched._device_hold_ids(now + timedelta(minutes=1)) == {"frame"}
    rig.sched._tick_once(now + timedelta(minutes=31))
    rig.sched._tick_once(now + timedelta(minutes=59))
    assert rig.push.calls == []
    ha._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", b"0")
    rig.sched._tick_once(datetime.now(UTC) + timedelta(seconds=1))
    assert [c["device_ids"] for c in rig.push.calls] == [{"frame"}]
    assert rig.states.get("frame").automation_hold_until is None
    # Junk never sets a hold.
    for junk in (b"", b"soon", b"nan", b"inf", b"1e999"):
        ha._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", junk)
    assert rig.states.get("frame").automation_hold_until is None


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


def test_push_manager_excludes_held_displays(tmp_path: Path) -> None:
    import io
    from unittest.mock import patch

    from PIL import Image

    manager, events = _real_push_manager(tmp_path)
    png = io.BytesIO()
    Image.new("RGB", (10, 10), (0, 0, 0)).save(png, format="PNG")
    with patch("app.push.render_to_png") as rtp:
        rtp.side_effect = AssertionError("nothing to render for a held display")
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


def test_a_shared_daily_fire_reaches_the_held_display_when_its_hold_ends(
    tmp_path: Path,
) -> None:
    # The reproduced bug: dinner targets frame and hall, frame is held, hall
    # gets dinner, and releasing frame never delivered it.
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _hold(rig, "frame", at(18, 30))
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
    _hold(rig, "frame", DAY + timedelta(days=1, hours=8))
    rig.sched._tick_once(at(18, 0, 30))
    rig.push.calls.clear()
    # The hold ends the next morning: yesterday's dinner isn't delivered.
    rig.sched._tick_once(DAY + timedelta(days=1, hours=8, minutes=1))
    assert [c["page_id"] for c in rig.push.calls] == []


def test_a_newer_lineup_step_wins_over_an_older_missed_fire(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.decks.upsert(_cycle(["frame"], advance_anchor="19:00", advance_end_at="23:00"))
    rig.sched._tick_once(at(17, 0))
    _hold(rig, "frame", at(19, 20))
    rig.sched._tick_once(at(18, 0, 30))  # dinner, frame held
    rig.sched._tick_once(at(19, 0))  # the evening lineup starts, frame held
    rig.push.calls.clear()
    rig.sched._tick_once(at(19, 21))
    # What frame would show had it never been held: the 19:00 step.
    assert rig.push.to() == [("a", frozenset({"frame"}))]


def test_a_hold_ending_in_quiet_hours_catches_up_when_they_end(tmp_path: Path) -> None:
    # The reproduced bug: the catch-up was spent in quiet hours, so nothing
    # reached the display until the next boundary.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"], advance_interval_minutes=120))
    rig.sched._tick_once(at(0, 0))
    _hold(rig, "frame", at(0, 20))
    rig.push.quiet.add("frame")
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 21))
    rig.sched._tick_once(at(0, 30))
    assert rig.push.calls == []  # quiet hours are respected
    rig.push.quiet.discard("frame")
    rig.sched._tick_once(at(0, 40))  # quiet hours over, same step
    assert rig.push.to() == [("a", frozenset({"frame"}))]
    rig.sched._tick_once(at(0, 50))
    assert len(rig.push.calls) == 1


def test_a_missed_fire_waits_out_quiet_hours_too(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _hold(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    rig.push.quiet.add("frame")
    rig.push.calls.clear()
    rig.sched._tick_once(at(18, 31))
    assert rig.push.calls == []
    rig.push.quiet.discard("frame")
    rig.sched._tick_once(at(19, 0))
    assert rig.push.to() == [("x", frozenset({"frame"}))]


def test_a_failed_catch_up_retries_after_a_pause(tmp_path: Path) -> None:
    from app.scheduler import HOLD_CATCHUP_RETRY_S

    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle(["frame"], advance_interval_minutes=120))
    rig.sched._tick_once(at(0, 0))
    _hold(rig, "frame", at(0, 20))
    rig.push.fail.add("a")
    rig.push.calls.clear()
    rig.sched._tick_once(at(0, 21))
    rig.sched._tick_once(at(0, 21, 30))  # inside the retry pause
    assert len(rig.push.calls) == 1
    rig.push.fail.clear()
    rig.sched._tick_once(at(0, 21) + timedelta(seconds=HOLD_CATCHUP_RETRY_S + 1))
    assert len(rig.push.calls) == 2
    assert rig.push.calls[-1]["page_id"] == "a"
    assert "frame" not in rig.sched._hold_catchups


def _dinner_missed(rig: Rig) -> None:
    rig.decks.upsert(_shared_dinner())
    rig.sched._tick_once(at(17, 0))
    _hold(rig, "frame", at(18, 30))
    rig.sched._tick_once(at(18, 0, 30))
    rig.push.calls.clear()


def test_a_deleted_lineup_is_not_replayed(tmp_path: Path) -> None:
    # The reproduced bug: dinner deleted while frame was held, and dinner
    # still painted when the hold ended.
    rig = _rig(tmp_path)
    _dinner_missed(rig)
    rig.decks.delete("dinner")
    rig.sched._tick_once(at(18, 31))
    assert rig.push.calls == []
    assert "frame" not in rig.sched._hold_catchups


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
    _hold(rig, "frame", at(10, 20))
    rig.sched._tick_once(at(10, 0))
    rig.push.calls.clear()
    rig.schedules.delete("news")
    rig.sched._tick_once(at(10, 21))
    assert rig.push.calls == []


def test_push_lock_wait_is_bounded_and_reports_busy(tmp_path: Path) -> None:
    import threading
    import time

    manager, _events = _real_push_manager(tmp_path)
    held, release = threading.Event(), threading.Event()

    def hog() -> None:  # another push holding the render lock
        with manager._lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=hog)
    thread.start()
    assert held.wait(5)
    try:
        started = time.monotonic()
        result = manager.push("home", lock_timeout_s=0.2, settle=False)
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        thread.join(5)
    assert result.status == "busy"
    assert "busy" in (result.error or "")
    # The withdrawn ticket doesn't supersede anyone later.
    assert (
        manager._acquire_or_supersede(
            device_id=None, source="t", target="home", bypass_coalesce=False, lock_timeout_s=1.0
        )
        is None
    )
    manager._lock.release()


# ---------- coalescing tickets that time out ----------


def _hog(manager: Any):
    import threading

    held, release = threading.Event(), threading.Event()

    def run() -> None:
        with manager._lock:
            held.set()
            release.wait(5)

    thread = threading.Thread(target=run)
    thread.start()
    assert held.wait(5)
    return thread, release


def test_sends_that_gave_up_never_supersede_one_still_waiting(tmp_path: Path) -> None:
    # Sol's case: A waits normally, B and C queue behind it, B then C time
    # out; the ticket fell back to B's abandoned one and A came back
    # "superseded" although nothing newer was delivered.
    import threading

    manager, _events = _real_push_manager(tmp_path)
    hog, release = _hog(manager)
    outcome: dict[str, Any] = {}

    def a() -> None:
        outcome["a"] = manager._acquire_or_supersede(
            device_id="hallway", source="t", target="home", bypass_coalesce=False
        )

    waiter = threading.Thread(target=a)
    waiter.start()
    threading.Event().wait(0.1)  # A's ticket is issued and waiting
    results: dict[str, Any] = {}

    def give_up(name: str, wait: float) -> None:
        results[name] = manager._acquire_or_supersede(
            device_id="hallway",
            source="t",
            target="home",
            bypass_coalesce=False,
            lock_timeout_s=wait,
        )

    # B and C queue together behind A; B gives up first, then C.
    b = threading.Thread(target=give_up, args=("b", 0.3))
    b.start()
    threading.Event().wait(0.05)
    c = threading.Thread(target=give_up, args=("c", 0.5))
    c.start()
    b.join(5)
    c.join(5)
    assert results == {"b": "busy", "c": "busy"}
    release.set()
    hog.join(5)
    waiter.join(5)
    assert outcome["a"] is None  # A got the lock and proceeds
    manager._lock.release()


def test_a_newer_waiting_send_still_supersedes(tmp_path: Path) -> None:
    import threading

    manager, _events = _real_push_manager(tmp_path)
    hog, release = _hog(manager)
    outcome: dict[str, Any] = {}

    def send(name: str) -> None:
        got = manager._acquire_or_supersede(
            device_id="hallway", source="t", target="home", bypass_coalesce=False
        )
        outcome[name] = got
        if got is None:
            manager._lock.release()

    first = threading.Thread(target=send, args=("first",))
    first.start()
    threading.Event().wait(0.1)
    second = threading.Thread(target=send, args=("second",))
    second.start()
    threading.Event().wait(0.1)
    release.set()
    for thread in (hog, first, second):
        thread.join(5)
    assert outcome == {"first": "superseded", "second": None}


def test_deck_cache_operations_give_up_when_the_renderer_stays_busy(tmp_path: Path) -> None:
    import time

    manager, _events = _real_push_manager(tmp_path)
    hog, release = _hog(manager)
    try:
        started = time.monotonic()
        assert manager.promote_deck_page("hallway", "home", lock_timeout_s=0.1) is False
        assert manager.warm_deck_page("home", "hallway", lock_timeout_s=0.1) is False
        assert manager.clear_deck_cache("hallway", lock_timeout_s=0.1) is False
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        hog.join(5)
    assert manager.clear_deck_cache("hallway", lock_timeout_s=1.0) is True
