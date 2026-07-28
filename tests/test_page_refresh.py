"""Scheduler page content-refresh: pages carry their own update cadence
and re-render only for devices currently showing them (#140)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.page_store import Page, PageStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_store import ScheduleStore

T0 = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@dataclass
class FakePush:
    pushes: list[tuple[str, frozenset, bool, str]] = field(default_factory=list)
    # device_id -> latest render record (as the real PushManager stores it).
    latest: dict[str, dict] = field(default_factory=dict)

    def push(self, page_id: str, *, device_ids=None, respect_quiet_hours=False, source="page"):
        self.pushes.append((page_id, frozenset(device_ids or ()), respect_quiet_hours, source))

        class R:
            status = "sent"
            error = None
            duration_s = 0.0
            event_id = None

        return R()

    def latest_render_for(self, device_id: str):
        return self.latest.get(device_id)


def _pages(tmp_path: Path, *pages: Page) -> PageStore:
    store = PageStore(tmp_path / "pages.json")
    for p in pages:
        store.save(p)
    return store


def _sched(tmp_path: Path, page_store: PageStore, pusher: FakePush, **kw: Any) -> Scheduler:
    return Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        push_manager=lambda: pusher,  # type: ignore[arg-type,return-value]
        page_store=page_store,
        timezone_provider=lambda: UTC,
        device_ids_for_page=kw.pop("device_ids_for_page", None),
        **kw,
    )


def test_lone_single_bound_page_refreshes_on_cadence(tmp_path: Path) -> None:
    ps = _pages(tmp_path, Page(id="clock", name="Clock", device_ids=["panel"], refresh_minutes=1))
    pusher = FakePush()
    sched = _sched(tmp_path, ps, pusher)

    sched._maybe_refresh_pages(T0)
    assert pusher.pushes == [("clock", frozenset({"panel"}), True, "page_refresh")]

    # Within the cadence: nothing.
    sched._maybe_refresh_pages(T0 + timedelta(seconds=30))
    assert len(pusher.pushes) == 1

    # Past it: again.
    sched._maybe_refresh_pages(T0 + timedelta(seconds=61))
    assert len(pusher.pushes) == 2


def test_zero_cadence_and_ambiguous_devices_never_refresh(tmp_path: Path) -> None:
    ps = _pages(
        tmp_path,
        Page(id="static", name="S", device_ids=["panel"], refresh_minutes=0),
        # Two pages bound to the same device with no rotation/deck: the
        # server can't know which is showing, so it never guesses.
        Page(id="a", name="A", device_ids=["shared"], refresh_minutes=1),
        Page(id="b", name="B", device_ids=["shared"], refresh_minutes=1),
    )
    pusher = FakePush()
    sched = _sched(tmp_path, ps, pusher)
    sched._maybe_refresh_pages(T0)
    assert pusher.pushes == []


def test_deck_nav_record_decides_the_shown_page(tmp_path: Path) -> None:
    ps = _pages(
        tmp_path,
        Page(id="home", name="H", device_ids=["panel"], refresh_minutes=1),
        Page(id="away", name="A", device_ids=["panel"], refresh_minutes=1),
    )
    decks = DeckStore(tmp_path / "decks.json")
    decks.upsert(
        Deck(
            id="d",
            name="D",
            device_ids=["panel"],
            pages=[DeckPage(page_id="home"), DeckPage(page_id="away")],
        )
    )
    nav = DeckNavStore(tmp_path / "nav.json")
    nav.set("panel", "d", "away")
    pusher = FakePush()
    sched = _sched(tmp_path, ps, pusher, deck_store=decks, deck_nav_store=nav)
    sched._maybe_refresh_pages(T0)
    assert pusher.pushes == [("away", frozenset({"panel"}), True, "page_refresh")]


def test_rotation_position_decides_the_shown_page(tmp_path: Path) -> None:
    ps = _pages(
        tmp_path,
        Page(id="clock", name="C", device_ids=["panel"], refresh_minutes=1),
        Page(id="cal", name="K", device_ids=["panel"], refresh_minutes=1),
    )
    rots = RotationStore(tmp_path / "rot.json")
    rots.upsert(
        Rotation(
            id="r",
            name="r",
            anchor="00:00",
            days_of_week=[0, 1, 2, 3, 4, 5, 6],
            steps=[
                RotationStep(page_id="clock", dwell_minutes=15),
                RotationStep(page_id="cal", dwell_minutes=15),
            ],
        )
    )
    pusher = FakePush()
    sched = _sched(
        tmp_path,
        ps,
        pusher,
        rotation_store=rots,
        device_ids_for_page=lambda pid: ["panel"],
    )
    # 12:00 -> minute 720 -> cycle pos 0 -> step 0 ("clock") is showing.
    sched._maybe_refresh_pages(T0)
    assert pusher.pushes == [("clock", frozenset({"panel"}), True, "page_refresh")]


def test_multi_bound_device_refreshes_the_shown_page(tmp_path: Path) -> None:
    """A device bound to several dashboards auto-updates the one it is actually
    showing (from the last render), not none of them (regression: the binding
    count could not disambiguate, so multi-bound pages never refreshed)."""
    ps = _pages(
        tmp_path,
        Page(id="a", name="A", device_ids=["panel"], refresh_minutes=1),
        Page(id="b", name="B", device_ids=["panel"], refresh_minutes=1),
    )
    pusher = FakePush()
    pusher.latest = {"panel": {"page_id": "b"}}  # panel currently shows b
    sched = _sched(tmp_path, ps, pusher)
    sched._maybe_refresh_pages(T0)
    pushed = {p[0] for p in pusher.pushes}
    assert pushed == {"b"}
