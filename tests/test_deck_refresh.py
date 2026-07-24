"""Scheduler deck refresh: each deck page warms on its own effective cadence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.schedule_store import ScheduleStore

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class FakePush:
    warmed: list[tuple[str, str]] = field(default_factory=list)

    def warm_deck_page(self, page_id: str, device_id: str) -> bool:
        self.warmed.append((page_id, device_id))
        return True


def _scheduler(tmp_path: Path, decks: DeckStore, pusher: FakePush) -> Scheduler:
    return Scheduler(
        store=ScheduleStore(tmp_path / "schedules.json"),
        push_manager=lambda: pusher,  # type: ignore[arg-type,return-value]
        deck_store=decks,
    )


def _deck(tmp_path: Path) -> DeckStore:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(
        Deck(
            id="d",
            name="D",
            device_ids=["panel"],
            refresh_interval_minutes=15,  # default
            pages=[
                DeckPage(page_id="fast", refresh_interval_minutes=5),
                DeckPage(page_id="slow", refresh_interval_minutes=60),
                DeckPage(page_id="default"),  # inherits the deck's 15
            ],
        )
    )
    return store


def test_pages_warm_on_their_own_cadence(tmp_path: Path) -> None:
    pusher = FakePush()
    s = _scheduler(tmp_path, _deck(tmp_path), pusher)

    # First pass: every page is warmed once.
    s._warm_decks(T0)
    assert set(pusher.warmed) == {("fast", "panel"), ("slow", "panel"), ("default", "panel")}

    # +5 min: only the 5-minute page is due.
    pusher.warmed.clear()
    s._warm_decks(T0 + timedelta(minutes=5))
    assert pusher.warmed == [("fast", "panel")]

    # +15 min: the 5-min page again, plus the 15-min (inherited) page.
    pusher.warmed.clear()
    s._warm_decks(T0 + timedelta(minutes=15))
    assert set(pusher.warmed) == {("fast", "panel"), ("default", "panel")}

    # +60 min: all three are due (incl. the 60-min page).
    pusher.warmed.clear()
    s._warm_decks(T0 + timedelta(minutes=60))
    assert set(pusher.warmed) == {("fast", "panel"), ("slow", "panel"), ("default", "panel")}


def test_zero_interval_page_is_not_scheduler_warmed(tmp_path: Path) -> None:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(
        Deck(
            id="d",
            name="D",
            device_ids=["panel"],
            refresh_interval_minutes=15,
            pages=[
                DeckPage(page_id="static", refresh_interval_minutes=0),
                DeckPage(page_id="live"),
            ],
        )
    )
    pusher = FakePush()
    s = _scheduler(tmp_path, store, pusher)
    s._warm_decks(T0)
    # The 0-interval page is skipped; the inheriting one warms.
    assert pusher.warmed == [("live", "panel")]


def test_disabled_deck_not_warmed(tmp_path: Path) -> None:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(
        Deck(id="d", name="D", enabled=False, device_ids=["panel"], pages=[DeckPage(page_id="a")])
    )
    pusher = FakePush()
    _scheduler(tmp_path, store, pusher)._warm_decks(T0)
    assert pusher.warmed == []


# -- home card return (deck editor: "returns to home after N min") ----------


@dataclass
class FakeNavPush(FakePush):
    promoted: list[tuple[str, str]] = field(default_factory=list)
    promote_ok: bool = True

    def promote_deck_page(self, device_id: str, page_id: str) -> bool:
        if self.promote_ok:
            self.promoted.append((device_id, page_id))
        return self.promote_ok


def _home_deck(tmp_path: Path, *, timeout: int = 15) -> DeckStore:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(
        Deck(
            id="d",
            name="D",
            device_ids=["panel"],
            pages=[DeckPage(page_id="home"), DeckPage(page_id="away")],
            home_page_id="home",
            home_timeout_minutes=timeout,
        )
    )
    return store


def _nav_store(tmp_path: Path):
    from app.state.deck_nav_store import DeckNavStore

    return DeckNavStore(tmp_path / "nav.json")


def test_idle_device_returns_home_after_timeout(tmp_path: Path) -> None:
    decks, pusher, nav = _home_deck(tmp_path), FakeNavPush(), _nav_store(tmp_path)
    sched = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        push_manager=lambda: pusher,  # type: ignore[arg-type,return-value]
        deck_store=decks,
        deck_nav_store=nav,
    )
    nav.set("panel", "d", "away")
    # Backdate the interaction past the 15-minute timeout.
    raw = nav._load_raw()
    raw["panel"]["updated_at"] = (T0 - timedelta(minutes=20)).timestamp()
    nav._save_raw(raw)

    sched._maybe_return_decks_home(T0)
    assert pusher.promoted == [("panel", "home")]
    assert nav.get("panel")["page_id"] == "home"


def test_recent_interaction_holds_the_page(tmp_path: Path) -> None:
    decks, pusher, nav = _home_deck(tmp_path), FakeNavPush(), _nav_store(tmp_path)
    sched = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        push_manager=lambda: pusher,  # type: ignore[arg-type,return-value]
        deck_store=decks,
        deck_nav_store=nav,
    )
    nav.set("panel", "d", "away")
    raw = nav._load_raw()
    raw["panel"]["updated_at"] = (T0 - timedelta(minutes=5)).timestamp()
    nav._save_raw(raw)

    sched._maybe_return_decks_home(T0)
    assert pusher.promoted == []
    assert nav.get("panel")["page_id"] == "away"


def test_zero_timeout_never_returns(tmp_path: Path) -> None:
    decks, pusher, nav = _home_deck(tmp_path, timeout=0), FakeNavPush(), _nav_store(tmp_path)
    sched = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        push_manager=lambda: pusher,  # type: ignore[arg-type,return-value]
        deck_store=decks,
        deck_nav_store=nav,
    )
    nav.set("panel", "d", "away")
    raw = nav._load_raw()
    raw["panel"]["updated_at"] = (T0 - timedelta(hours=5)).timestamp()
    nav._save_raw(raw)
    sched._maybe_return_decks_home(T0)
    assert pusher.promoted == []


def test_device_already_home_untouched(tmp_path: Path) -> None:
    decks, pusher, nav = _home_deck(tmp_path), FakeNavPush(), _nav_store(tmp_path)
    sched = Scheduler(
        store=ScheduleStore(tmp_path / "s.json"),
        push_manager=lambda: pusher,  # type: ignore[arg-type,return-value]
        deck_store=decks,
        deck_nav_store=nav,
    )
    nav.set("panel", "d", "home")
    raw = nav._load_raw()
    raw["panel"]["updated_at"] = (T0 - timedelta(hours=5)).timestamp()
    nav._save_raw(raw)
    sched._maybe_return_decks_home(T0)
    assert pusher.promoted == []
