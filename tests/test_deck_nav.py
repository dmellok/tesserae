"""Deck navigation in ButtonService: a graph-link button promotes/pushes the
target page, records nav position, dedups, and falls through for non-link
buttons."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.button_service import ButtonService, TouchStroke
from app.push import PushResult
from app.state.deck_model import Deck, DeckLink, DeckPage, DeckZone
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.page_store import Page, PageStore
from app.state.rotation_store import RotationStore
from app.state.settings_store import SettingsStore


@dataclass
class FakePush:
    pushes: list[tuple[str, str]] = field(default_factory=list)
    promotes: list[tuple[str, str]] = field(default_factory=list)
    warmed: set[tuple[str, str]] = field(default_factory=set)

    def push(
        self,
        page_id: str,
        *,
        device_ids: set[str] | None = None,
        respect_quiet_hours: bool = False,
        source: str = "page",
    ) -> PushResult:
        self.pushes.append((page_id, source))
        return PushResult(status="pushed", page_id=page_id)

    def promote_deck_page(self, device_id: str, page_id: str) -> bool:
        if (device_id, page_id) in self.warmed:
            self.promotes.append((device_id, page_id))
            return True
        return False

    def has_warm_deck_page(self, device_id: str, page_id: str) -> bool:
        return (device_id, page_id) in self.warmed

    def latest_render_for(self, device_id: str) -> dict | None:
        return None  # no current frame -> a non-deck touch resolves to no_frame


@dataclass
class _FakeDevice:
    panel: dict[str, int]


@dataclass
class _FakeDevices:
    """Minimal DeviceRegistry duck-type: one 800x480 device named 'panel'."""

    def get(self, device_id: str) -> _FakeDevice | None:
        return _FakeDevice(panel={"w": 800, "h": 480}) if device_id == "panel" else None


def _pages(tmp_path: Path) -> PageStore:
    ps = PageStore(tmp_path / "pages.json")
    for pid in ("overview", "calendar", "weather"):
        ps.save(Page(id=pid, name=pid.title(), device_ids=["panel"]))
    return ps


def _deck_store(tmp_path: Path) -> DeckStore:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(
        Deck(
            id="d1",
            name="D",
            device_ids=["panel"],
            pages=[
                DeckPage(
                    page_id="overview",
                    links=[
                        DeckLink(target_page_id="calendar", button="right"),
                        DeckLink(target_page_id="weather", button="left"),
                        # Tap the bottom-right quadrant -> calendar.
                        DeckLink(
                            target_page_id="calendar",
                            zone=DeckZone(x=0.5, y=0.5, w=0.5, h=0.5),
                        ),
                    ],
                ),
                DeckPage(
                    page_id="calendar", links=[DeckLink(target_page_id="overview", button="left")]
                ),
                DeckPage(
                    page_id="weather", links=[DeckLink(target_page_id="overview", button="right")]
                ),
            ],
        )
    )
    return store


def _wire(
    tmp_path: Path,
    pusher: FakePush,
    decks: DeckStore,
    nav: DeckNavStore,
    *,
    with_devices: bool = False,
) -> ButtonService:
    return ButtonService(
        rotation_store=RotationStore(tmp_path / "rot.json"),
        state_store=DeviceRotationStateStore(tmp_path / "drs.json"),
        settings_store=SettingsStore(tmp_path / "s.json"),
        page_store=_pages(tmp_path),
        push_manager=pusher,  # type: ignore[arg-type]
        deck_store=decks,
        deck_nav_store=nav,
        devices=_FakeDevices() if with_devices else None,  # type: ignore[arg-type]
    )


def test_deck_button_navigates_and_records_position(tmp_path: Path) -> None:
    decks, nav, pusher = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav)

    # From the entry page (overview), "right" -> calendar. Not warmed -> push.
    res = svc.handle_button(device_id="panel", button="right", event_id=1)
    assert res.pushed_page_id == "calendar"
    assert res.action_spec == "deck:d1:calendar"
    assert pusher.pushes == [("calendar", "deck")]
    assert nav.current_page("panel", "d1") == "calendar"

    # From calendar, "left" -> overview (graph, not a linear list).
    res2 = svc.handle_button(device_id="panel", button="left", event_id=2)
    assert res2.pushed_page_id == "overview"
    assert nav.current_page("panel", "d1") == "overview"


def test_deck_promotes_warmed_frame_without_rendering(tmp_path: Path) -> None:
    decks, nav = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json")
    pusher = FakePush()
    pusher.warmed.add(("panel", "calendar"))
    svc = _wire(tmp_path, pusher, decks, nav)

    res = svc.handle_button(device_id="panel", button="right", event_id=1)
    assert res.pushed_page_id == "calendar"
    assert pusher.promotes == [("panel", "calendar")]
    assert pusher.pushes == []  # promoted a warmed frame, no on-the-fly render


def test_deck_button_dedups_repeat_event(tmp_path: Path) -> None:
    decks, nav, pusher = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav)

    svc.handle_button(device_id="panel", button="right", event_id=5)
    res = svc.handle_button(device_id="panel", button="right", event_id=5)  # retry
    assert res.dedup is True
    assert len(pusher.pushes) == 1  # navigated once


def test_non_link_button_falls_through_to_rotation_path(tmp_path: Path) -> None:
    decks, nav, pusher = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav)

    # "refresh" isn't a graph link on overview, so it falls through to the
    # normal button_map (default "refresh" action), NOT a deck nav, and the
    # deck nav position is untouched.
    res = svc.handle_button(device_id="panel", button="refresh", event_id=1)
    assert res.action_spec == "refresh"
    assert not (res.action_spec or "").startswith("deck:")
    assert nav.get("panel") is None


def test_no_deck_bound_uses_rotation_path(tmp_path: Path) -> None:
    # No deck store wired at all -> plain ButtonService behaviour: "right" hits
    # the default rotate_next map, not any deck path.
    svc = ButtonService(
        rotation_store=RotationStore(tmp_path / "rot.json"),
        state_store=DeviceRotationStateStore(tmp_path / "drs.json"),
        settings_store=SettingsStore(tmp_path / "s.json"),
        page_store=_pages(tmp_path),
        push_manager=FakePush(),  # type: ignore[arg-type]
    )
    res = svc.handle_button(device_id="panel", button="right", event_id=1)
    assert res.action_spec == "rotate_next"


def test_deck_touch_zone_navigates(tmp_path: Path) -> None:
    decks, nav, pusher = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav, with_devices=True)

    # Tap the bottom-right quadrant of the 800x480 panel -> calendar zone.
    res = svc.handle_touch(
        device_id="panel", stroke=TouchStroke(x0=700, y0=400, x1=700, y1=400), frame_digest="d"
    )
    assert res.outcome == "dispatched"
    assert res.base.action_spec == "deck:d1:calendar"
    assert res.base.pushed_page_id == "calendar"
    assert nav.current_page("panel", "d1") == "calendar"


def test_deck_touch_outside_zone_falls_through(tmp_path: Path) -> None:
    decks, nav, pusher = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav, with_devices=True)

    # Tap the top-left quadrant: no deck zone there -> falls through to the
    # normal touch path (no current frame -> no_frame), deck nav untouched.
    res = svc.handle_touch(
        device_id="panel", stroke=TouchStroke(x0=50, y0=50, x1=50, y1=50), frame_digest="d"
    )
    assert res.outcome == "no_frame"
    assert not (res.base.action_spec or "").startswith("deck:")
    assert nav.get("panel") is None


def test_deck_touch_dedups_repeat_event(tmp_path: Path) -> None:
    decks, nav, pusher = _deck_store(tmp_path), DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav, with_devices=True)
    stroke = TouchStroke(x0=700, y0=400, x1=700, y1=400)

    svc.handle_touch(device_id="panel", stroke=stroke, frame_digest="d", event_id=9)
    res = svc.handle_touch(device_id="panel", stroke=stroke, frame_digest="d", event_id=9)
    assert res.outcome == "deduped"
    assert len(pusher.pushes) == 1


def test_graphless_deck_defaults_left_right_to_prev_next(tmp_path: Path) -> None:
    """Firmware bench finding: a deck authored without a graph (the
    management-page flow) must still navigate. left/right default to
    prev/next in deck order, wrapping, and win over the rotation map
    for deck-bound devices."""
    decks = DeckStore(tmp_path / "decks.json")
    decks.upsert(
        Deck(
            id="plain",
            name="Plain",
            device_ids=["panel"],
            pages=[DeckPage(page_id="p0"), DeckPage(page_id="p1"), DeckPage(page_id="p2")],
        )
    )
    nav, pusher = DeckNavStore(tmp_path / "nav.json"), FakePush()
    svc = _wire(tmp_path, pusher, decks, nav)

    res = svc.handle_button(device_id="panel", button="right", event_id=1)
    assert res.action_spec == "deck:plain:p1"
    assert nav.current_page("panel", "plain") == "p1"

    # left from p1 -> p0; left again wraps to p2.
    svc.handle_button(device_id="panel", button="left", event_id=2)
    assert nav.current_page("panel", "plain") == "p0"
    res = svc.handle_button(device_id="panel", button="left", event_id=3)
    assert res.action_spec == "deck:plain:p2"

    # Non-nav buttons still fall through to the button map.
    res = svc.handle_button(device_id="panel", button="refresh", event_id=4)
    assert res.action_spec == "refresh"
