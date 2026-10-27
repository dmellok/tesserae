"""What a display's lineup position (the deck nav record) must say: the
lineup and page actually on its glass.

* Daily / interval lineup fires record it, like cycle lineup advances, so
  Next / Previous step the lineup that last painted the display.
* Reading the position, delivering the next page and recording it happen
  under one per-display lock across button presses, Next / Previous and
  the scheduler, so they can't interleave into "glass shows c, record b".
* Only a frame that reached the panel (``sent`` / ``no_change``, or a
  promoted warm frame) moves the record; failed, superseded or missing
  pages leave it, and Next / Previous report why."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app import button_service as button_service_module
from app.button_service import ButtonService
from app.push import PushResult, RendererResult
from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.legacy_projections import RotationProjection
from app.state.page_store import Page, PageStore
from app.state.schedule_store import ScheduleStore
from app.state.settings_store import SettingsStore

DAY = datetime(2026, 6, 15, tzinfo=UTC)


def at(hh: int, mm: int, ss: int = 0) -> datetime:
    return DAY.replace(hour=hh, minute=mm, second=ss)


@dataclass
class FakePush:
    calls: list[str] = field(default_factory=list)
    status: dict[str, str] = field(default_factory=dict)  # page -> status
    block_on: str | None = None
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    promote_kwargs: list[dict[str, Any]] = field(default_factory=list)
    push_kwargs: list[dict[str, Any]] = field(default_factory=list)

    def push(self, page_id: str, **kw: Any) -> PushResult:
        self.calls.append(page_id)
        self.push_kwargs.append(kw)
        if page_id == self.block_on:
            self.started.set()
            assert self.release.wait(5), "test never released the push"
        status = self.status.get(page_id, "sent")
        rows = [
            RendererResult(renderer_id=f"r__{d}", topic="", digest="x", url="", bytes_written=1)
            for d in sorted(kw.get("device_ids") or [])
        ]
        return PushResult(
            status=status,  # type: ignore[arg-type]
            page_id=page_id,
            renderers=rows if status in ("sent", "no_change") else [],
            error=None if status in ("sent", "no_change") else f"{page_id} {status}",
        )

    def promote_deck_page(self, _device_id: str, _page_id: str, **kw: Any) -> bool:
        self.promote_kwargs.append(kw)
        return False

    def device_in_quiet_hours(self, _device_id: str) -> bool:
        return False

    def latest_render_for(self, _device_id: str) -> dict[str, Any] | None:
        return None


@dataclass
class Rig:
    sched: Scheduler
    svc: ButtonService
    push: FakePush
    decks: DeckStore
    nav: DeckNavStore


def _rig(tmp_path: Path) -> Rig:
    decks = DeckStore(tmp_path / "decks.json")
    nav = DeckNavStore(tmp_path / "nav.json")
    states = DeviceRotationStateStore(tmp_path / "state.json")
    pages = PageStore(tmp_path / "pages.json")
    for pid in ("a", "b", "c", "x", "y"):
        pages.save(Page(id=pid, name=pid.upper(), device_ids=["frame"]))
    push = FakePush()
    sched = Scheduler(
        store=ScheduleStore(tmp_path / "schedules.json"),
        rotation_store=RotationProjection(decks),
        rotation_state_store=states,
        deck_store=decks,
        deck_nav_store=nav,
        page_store=pages,
        push_manager=lambda: push,
        page_exists=lambda _pid: True,
        timezone_provider=lambda: UTC,
    )
    svc = ButtonService(
        rotation_store=RotationProjection(decks),
        state_store=states,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        page_store=pages,
        push_manager=push,  # type: ignore[arg-type]
        deck_store=decks,
        deck_nav_store=nav,
    )
    return Rig(sched, svc, push, decks, nav)


def _cycle(did: str, pages: list[str], **kw: Any) -> Deck:
    base: dict[str, Any] = dict(
        id=did,
        name=did.title(),
        device_ids=["frame"],
        advance="timer",
        advance_interval_minutes=30,
        advance_anchor="00:00",
        advance_min_hold_minutes=0,
        pages=[DeckPage(page_id=p) for p in pages],
    )
    base.update(kw)
    return Deck(**base)


def _daily(did: str, pages: list[str], fires_at: str) -> Deck:
    return Deck(
        id=did,
        name=did.title(),
        device_ids=["frame"],
        advance="timer",
        advance_trigger="daily",
        advance_fires_at=fires_at,
        advance_min_hold_minutes=0,
        pages=[DeckPage(page_id=p) for p in pages],
    )


# ---------- daily / interval fires record the position ----------


def test_next_after_a_daily_lineup_fire_steps_that_lineup(tmp_path: Path) -> None:
    # The reproduced bug: breakfast at a, the dinner timer paints x, then
    # Next painted breakfast's b instead of dinner's y.
    rig = _rig(tmp_path)
    rig.decks.upsert(
        _cycle("breakfast", ["a", "b"], advance_anchor="07:00", advance_end_at="09:00")
    )
    rig.decks.upsert(_daily("dinner", ["x", "y"], "18:00"))
    rig.sched._tick_once(at(7, 0, 5))
    assert rig.push.calls == ["a"]
    assert rig.nav.get("frame")["deck_id"] == "breakfast"
    rig.sched._tick_once(at(18, 0, 30))
    assert rig.push.calls == ["a", "x"]
    assert rig.nav.current_page("frame", "dinner") == "x"

    result = rig.svc.step(device_id="frame", forward=True)
    assert result.pushed_page_id == "y"
    assert rig.nav.current_page("frame", "dinner") == "y"


def test_an_undelivered_daily_fire_leaves_the_position(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(
        _cycle("breakfast", ["a", "b"], advance_anchor="07:00", advance_end_at="09:00")
    )
    rig.decks.upsert(_daily("dinner", ["x", "y"], "18:00"))
    rig.sched._tick_once(at(7, 0, 5))
    rig.push.status["x"] = "failed"
    rig.sched._tick_once(at(18, 0, 30))
    assert rig.nav.current_page("frame", "breakfast") == "a"


# ---------- one display's navigation is serialised ----------


def test_scheduler_waits_for_a_next_in_flight(tmp_path: Path) -> None:
    # The reproduced race: HA delivers b, the scheduler delivers and records
    # c, then HA records b, so the record says b while the glass shows c.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b", "c"]))
    rig.nav.set("frame", "cycle", "a")
    rig.push.block_on = "b"

    step = threading.Thread(target=lambda: rig.svc.step(device_id="frame", forward=True))
    step.start()
    assert rig.push.started.wait(5)
    tick = threading.Thread(target=lambda: rig.sched._tick_once(at(1, 0)))  # c is due
    tick.start()
    tick.join(0.3)
    assert tick.is_alive(), "the scheduler should be waiting for the step"
    assert rig.push.calls == ["b"]
    rig.push.release.set()
    step.join(5)
    tick.join(5)
    assert rig.push.calls == ["b", "c"]
    # Record and glass agree: c was delivered last and recorded last.
    assert rig.nav.current_page("frame", "cycle") == "c"


def test_next_reports_busy_when_the_display_stays_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b"]))
    monkeypatch.setattr(button_service_module, "NAV_LOCK_TIMEOUT_S", 0.05)
    locked, done = threading.Event(), threading.Event()

    def hold_lock() -> None:
        with rig.nav.device_lock("frame"):
            locked.set()
            done.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert locked.wait(5)
    try:
        result = rig.svc.step(device_id="frame", forward=True)
    finally:
        done.set()
        holder.join(5)
    assert result.error == "Display busy, try again."
    assert rig.push.calls == []


def test_navigation_lock_is_per_display_and_reentrant(tmp_path: Path) -> None:
    nav = DeckNavStore(tmp_path / "nav.json")
    assert nav.device_lock("a") is nav.device_lock("a")
    assert nav.device_lock("a") is not nav.device_lock("b")
    with nav.device_lock("a"), nav.device_lock("a"):
        pass


# ---------- only a delivered frame moves the position ----------


@pytest.mark.parametrize("status", ["superseded", "not_found", "failed", "busy", "unbound"])
def test_next_that_did_not_reach_the_panel_keeps_the_position(tmp_path: Path, status: str) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b", "c"]))
    rig.nav.set("frame", "cycle", "a")
    rig.push.status["b"] = status
    result = rig.svc.step(device_id="frame", forward=True)
    assert result.pushed_page_id is None
    assert result.error == f"b {status}"
    assert rig.nav.current_page("frame", "cycle") == "a"


def test_physical_press_that_was_superseded_keeps_the_position(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b", "c"], advance="manual"))
    rig.nav.set("frame", "cycle", "a")
    rig.push.status["b"] = "superseded"
    rig.svc.handle_button(device_id="frame", button="right", event_id=1)
    assert rig.nav.current_page("frame", "cycle") == "a"


def test_scheduler_advance_that_was_superseded_keeps_the_position(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b"]))
    rig.sched._tick_once(at(0, 0))
    rig.push.status["b"] = "superseded"
    rig.sched._tick_once(at(0, 30))
    assert rig.push.calls == ["a", "b"]
    assert rig.nav.current_page("frame", "cycle") == "a"


def test_rotation_step_that_did_not_land_is_not_committed(tmp_path: Path) -> None:
    # No lineup bound: the step runs on a rotation reaching the display
    # through its pages, and must not record a position the panel lacks.
    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b"], device_ids=[]))
    rig.push.status["b"] = "superseded"
    result = rig.svc.step(device_id="frame", forward=True)
    assert result.error == "b superseded"
    assert result.pushed_page_id is None
    states = DeviceRotationStateStore(tmp_path / "state.json")
    assert states.get("frame") is None


def test_hook_surfaces_the_reason_to_home_assistant(tmp_path: Path) -> None:
    from flask import Flask

    from app import ha_hooks

    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b"]))
    rig.nav.set("frame", "cycle", "a")
    rig.push.status["b"] = "superseded"
    app = Flask(__name__)
    device = type("D", (), {"id": "frame", "display_name": "Frame", "kind_of": "x"})()
    app.config["DEVICE_REGISTRY"] = type("R", (), {"get": staticmethod(lambda _id: device)})()
    app.config["BUTTON_SERVICE"] = rig.svc
    assert ha_hooks._step_device(app, "frame", 1) == "Frame: b superseded"
    rig.push.status.pop("b")
    assert ha_hooks._step_device(app, "frame", 1) is None


# ---------- a press never undoes a Hold set while it rendered ----------


def _ha(tmp_path: Path, states: DeviceRotationStateStore) -> Any:
    from unittest.mock import MagicMock

    from app.ha_discovery import HaHooks, HomeAssistantDiscovery

    device = MagicMock(id="frame", display_name="Frame", kind_of="x", panel={"w": 1, "h": 1})
    device.manifest, device.config_schema = {}, {}
    registry = MagicMock()
    registry.get.return_value = device
    registry.all.return_value = [device]
    return HomeAssistantDiscovery(
        transport=MagicMock(connected=False),
        push_manager=MagicMock(),
        page_store=PageStore(tmp_path / "ha-pages.json"),
        base_url_fn=lambda: "http://x",
        device_registry=registry,
        hooks=HaHooks(rotation_state_store=states),
        state_refresh_s=0,
    )


def test_a_press_finishing_after_a_new_hold_keeps_the_hold(tmp_path: Path) -> None:
    # The reproduced bug: block a press during delivery, set Hold=60, let
    # the press finish, and the hold was gone (the press wrote back the
    # whole record it had read before rendering).
    rig = _rig(tmp_path)
    states = rig.svc._state  # the one store production shares
    rig.decks.upsert(_cycle("cycle", ["a", "b"], advance="manual"))
    rig.nav.set("frame", "cycle", "a")
    rig.push.block_on = "b"
    press = threading.Thread(
        target=lambda: rig.svc.handle_button(device_id="frame", button="right", event_id=7)
    )
    press.start()
    assert rig.push.started.wait(5)
    _ha(tmp_path, states)._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", b"60")
    rig.push.release.set()
    press.join(5)
    state = states.get("frame")
    assert state.automation_hold_until is not None
    assert state.automation_hold_until > datetime.now(UTC)
    # And the press's own fields landed too.
    assert state.last_button_event_id == 7
    assert rig.nav.current_page("frame", "cycle") == "b"


def test_a_remote_step_committing_after_a_new_hold_keeps_the_hold(tmp_path: Path) -> None:
    # Rotation path: the step's position is committed after delivery.
    rig = _rig(tmp_path)
    states = rig.svc._state  # the one store production shares
    rig.decks.upsert(_cycle("cycle", ["a", "b"], device_ids=[]))
    rig.push.block_on = "b"
    step = threading.Thread(target=lambda: rig.svc.step(device_id="frame", forward=True))
    step.start()
    assert rig.push.started.wait(5)
    _ha(tmp_path, states)._on_device_cmd("tesserae/ha/dev/frame/cmd/hold", b"60")
    rig.push.release.set()
    step.join(5)
    state = states.get("frame")
    assert state.automation_hold_until is not None
    assert state.rotation_id == "cycle" and state.step_index == 1


def test_a_hold_release_never_clears_a_newer_hold(tmp_path: Path) -> None:
    from app.state.device_rotation_state_model import DeviceRotationState

    rig = _rig(tmp_path)
    states = DeviceRotationStateStore(tmp_path / "state.json")
    lapsed = DeviceRotationState(device_id="frame", automation_hold_until=at(1, 0))
    states.upsert(lapsed)
    snapshot = states.all()  # what the scheduler read
    newer = at(3, 0)
    states.patch("frame", {"automation_hold_until": newer})  # Hold set meanwhile

    class Stale:
        def __getattr__(self, name: str) -> Any:
            return getattr(states, name)

        def all(self) -> dict[str, DeviceRotationState]:
            return snapshot

    rig.sched._rotation_state_store = Stale()
    rig.sched._release_lapsed_device_holds(at(2, 0))
    assert states.get("frame").automation_hold_until == newer
    assert "frame" not in rig.sched._hold_catchups


def test_a_rejoin_keeps_a_hold_set_after_it_read_the_state(tmp_path: Path) -> None:
    from app.state.device_rotation_state_model import DeviceRotationState

    rig = _rig(tmp_path)
    states = DeviceRotationStateStore(tmp_path / "state.json")
    rig.decks.upsert(_cycle("cycle", ["a", "b"]))
    states.upsert(
        DeviceRotationState(
            device_id="frame", rotation_id="cycle", step_index=1, override_until=at(0, 5)
        )
    )
    snapshot = states.all()
    states.patch("frame", {"automation_hold_until": at(5, 0)})

    class Stale:
        def __getattr__(self, name: str) -> Any:
            return getattr(states, name)

        def all(self) -> dict[str, DeviceRotationState]:
            return snapshot

    rig.sched._rotation_state_store = Stale()
    rig.sched._maybe_rejoin_rotations(at(0, 10))
    state = states.get("frame")
    assert state.override_until is None  # the rejoin's own field
    assert state.automation_hold_until == at(5, 0)  # someone else's, kept


def test_state_patch_is_field_level_and_compare_and_set(tmp_path: Path) -> None:
    states = DeviceRotationStateStore(tmp_path / "state.json")
    states.patch("frame", {"last_button": "right", "step_index": 2})
    states.patch("frame", {"automation_hold_until": at(1, 0)})
    state = states.get("frame")
    assert (state.last_button, state.step_index) == ("right", 2)
    assert states.patch("frame", {"step_index": 3}, expect={"step_index": 9}) is None
    assert states.get("frame").step_index == 2
    assert states.patch("frame", {"step_index": 3}, expect={"step_index": 2}) is not None
    assert states.get("frame").step_index == 3


# ---------- Home Assistant's steps carry deadlines all the way down ----------


def test_a_home_assistant_step_bounds_promote_and_push(tmp_path: Path) -> None:
    from flask import Flask

    from app import ha_hooks

    rig = _rig(tmp_path)
    rig.decks.upsert(_cycle("cycle", ["a", "b"]))
    rig.nav.set("frame", "cycle", "a")
    app = Flask(__name__)
    device = type("D", (), {"id": "frame", "display_name": "Frame", "kind_of": "x"})()
    app.config["DEVICE_REGISTRY"] = type("R", (), {"get": staticmethod(lambda _id: device)})()
    app.config["BUTTON_SERVICE"] = rig.svc
    assert ha_hooks._step_device(app, "frame", 1) is None
    assert rig.push.promote_kwargs == [{"lock_timeout_s": 60.0}]
    assert rig.push.push_kwargs[-1]["lock_timeout_s"] == 60.0
    assert rig.push.push_kwargs[-1]["settle_max_s"] == 30.0
    # A physical press is unchanged: no deadlines.
    rig.svc.handle_button(device_id="frame", button="right", event_id=1)
    assert rig.push.promote_kwargs[-1] == {}
    assert "lock_timeout_s" not in rig.push.push_kwargs[-1]
