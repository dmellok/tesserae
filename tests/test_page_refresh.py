"""Scheduler page content-refresh: pages carry their own update cadence
and re-render only for devices currently showing them (#140)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.plugin_loader import Plugin, PluginRegistry
from app.scheduled_refresh import scheduled_placements
from app.scheduler import Scheduler
from app.state.deck_model import Deck, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.page_store import Cell, Page, PageStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.schedule_store import ScheduleStore
from app.state.widget_update_schedule import WidgetUpdateSchedule

T0 = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


@dataclass
class FakePush:
    pushes: list[tuple[str, frozenset, bool, str]] = field(default_factory=list)
    warms: list[tuple[str, str]] = field(default_factory=list)
    # device_id -> latest render record (as the real PushManager stores it).
    latest: dict[str, dict] = field(default_factory=dict)
    status: str = "sent"
    quiet_devices: set[str] = field(default_factory=set)

    def push(self, page_id: str, *, device_ids=None, respect_quiet_hours=False, source="page"):
        self.pushes.append((page_id, frozenset(device_ids or ()), respect_quiet_hours, source))

        class R:
            status = self.status
            error = None
            duration_s = 0.0
            event_id = None

        return R()

    def latest_render_for(self, device_id: str):
        return self.latest.get(device_id)

    def device_in_quiet_hours(self, device_id: str) -> bool:
        return device_id in self.quiet_devices

    def warm_deck_page(self, page_id: str, device_id: str) -> bool:
        self.warms.append((page_id, device_id))
        return True


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
        plugin_registry=kw.pop("plugin_registry", None),
        timezone_provider=lambda: UTC,
        device_ids_for_page=kw.pop("device_ids_for_page", None),
        **kw,
    )


def _scheduled_registry(tmp_path: Path) -> PluginRegistry:
    plugin = Plugin(
        id="daily",
        path=tmp_path / "daily",
        manifest={
            "name": "Daily",
            "kind": "widget",
            "supports": {"sizes": ["md"]},
            "updates": {"on_schedule": [{"kind": "daily", "suggested_at": "07:00"}]},
        },
        data_dir=tmp_path / "data" / "daily",
    )
    return PluginRegistry(plugins={"daily": plugin})


def _scheduled_cell(cell_id: str, *, at: str | None = None) -> Cell:
    return Cell(
        id=cell_id,
        plugin="daily",
        update_schedule=WidgetUpdateSchedule(kind="daily", at=at),
        x=0,
        y=0,
        w=100,
        h=100,
    )


def test_scheduled_resolver_ignores_stale_unsupported_placement(tmp_path: Path) -> None:
    page = Page(
        id="reminders",
        name="Reminders",
        cells=[_scheduled_cell("a")],
    )
    registry = _scheduled_registry(tmp_path)
    assert [record.page_id for record in scheduled_placements([page], registry)] == ["reminders"]

    plugin = registry.get("daily")
    assert plugin is not None
    plugin.manifest["updates"] = {"on_change": [{"source": "test.daily"}]}
    assert scheduled_placements([page], registry) == []


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


def test_data_change_refreshes_active_page_and_only_warms_inactive_deck_page(
    tmp_path: Path,
) -> None:
    ps = _pages(
        tmp_path,
        Page(id="active", name="Active", device_ids=["panel"]),
        Page(id="inactive", name="Inactive", device_ids=["panel"]),
        Page(id="unrelated", name="Unrelated", device_ids=["panel"]),
    )
    decks = DeckStore(tmp_path / "decks.json")
    decks.upsert(
        Deck(
            id="kitchen",
            name="Kitchen",
            device_ids=["panel"],
            pages=[DeckPage(page_id="active"), DeckPage(page_id="inactive")],
        )
    )
    nav = DeckNavStore(tmp_path / "nav.json")
    nav.set("panel", "kitchen", "active")
    pusher = FakePush()
    sched = _sched(tmp_path, ps, pusher, deck_store=decks, deck_nav_store=nav)

    sched.refresh_pages_for_update({"active", "inactive"}, source="data_change", now=T0)

    assert pusher.pushes == [("active", frozenset({"panel"}), True, "data_change")]
    assert pusher.warms == [("inactive", "panel")]


def test_data_change_warms_shared_deck_page_once_per_device(tmp_path: Path) -> None:
    ps = _pages(
        tmp_path,
        Page(id="shared", name="Shared", device_ids=["panel"]),
        Page(id="current", name="Current", device_ids=["panel"]),
    )
    decks = DeckStore(tmp_path / "decks.json")
    for deck_id in ("morning", "evening"):
        decks.upsert(
            Deck(
                id=deck_id,
                name=deck_id.title(),
                device_ids=["panel"],
                pages=[DeckPage(page_id="shared")],
            )
        )
    pusher = FakePush()
    sched = _sched(tmp_path, ps, pusher, deck_store=decks)

    sched.refresh_pages_for_update({"shared"}, source="data_change", now=T0)

    assert pusher.pushes == []
    assert pusher.warms == [("shared", "panel")]
    assert sched._deck_last_warm == {
        f"{deck_id}\x00panel\x00shared": T0.timestamp() for deck_id in ("morning", "evening")
    }


def test_data_change_attempt_preserves_page_cadence_timestamp(tmp_path: Path) -> None:
    ps = _pages(tmp_path, Page(id="active", name="Active", device_ids=["panel"]))
    pusher = FakePush(status="failed")
    sched = _sched(tmp_path, ps, pusher)

    sched.refresh_pages_for_update({"active"}, source="data_change", now=T0)
    assert sched._page_last_refresh["active"] == T0.timestamp()

    sched.refresh_pages_for_update({"active"}, source="data_change", now=T0 + timedelta(seconds=30))

    assert len(pusher.pushes) == 2


def test_widget_daily_boundary_refreshes_once_and_coalesces_placements(
    tmp_path: Path,
) -> None:
    page = Page(
        id="reminders",
        name="Reminders",
        device_ids=["panel"],
        cells=[_scheduled_cell("a"), _scheduled_cell("b")],
    )
    ps = _pages(tmp_path, page)
    pusher = FakePush()
    registry = _scheduled_registry(tmp_path)
    sched = _sched(tmp_path, ps, pusher, plugin_registry=lambda: registry)

    before = datetime(2026, 6, 15, 23, 59, tzinfo=UTC)
    sched._maybe_refresh_scheduled_widgets(before)
    assert pusher.pushes == []

    boundary = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    sched._maybe_refresh_scheduled_widgets(boundary)
    sched._maybe_refresh_scheduled_widgets(boundary + timedelta(minutes=1))

    assert pusher.pushes == [("reminders", frozenset({"panel"}), True, "widget_schedule")]


def test_widget_custom_time_does_not_backfill_when_enabled_after_target(
    tmp_path: Path,
) -> None:
    page = Page(
        id="reminders",
        name="Reminders",
        device_ids=["panel"],
        cells=[_scheduled_cell("a", at="07:00")],
    )
    ps = _pages(tmp_path, page)
    pusher = FakePush()
    registry = _scheduled_registry(tmp_path)
    sched = _sched(tmp_path, ps, pusher, plugin_registry=lambda: registry)

    enabled_late = datetime(2026, 6, 15, 11, 0, tzinfo=UTC)
    sched._maybe_refresh_scheduled_widgets(enabled_late)
    assert pusher.pushes == []

    sched._maybe_refresh_scheduled_widgets(datetime(2026, 6, 16, 7, 0, tzinfo=UTC))
    assert len(pusher.pushes) == 1


def test_widget_schedule_retries_after_quiet_result(tmp_path: Path) -> None:
    page = Page(
        id="reminders",
        name="Reminders",
        device_ids=["panel"],
        cells=[_scheduled_cell("a")],
    )
    ps = _pages(tmp_path, page)
    pusher = FakePush(status="quiet")
    registry = _scheduled_registry(tmp_path)
    sched = _sched(tmp_path, ps, pusher, plugin_registry=lambda: registry)

    sched._maybe_refresh_scheduled_widgets(datetime(2026, 6, 15, 23, 59, tzinfo=UTC))
    boundary = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    sched._maybe_refresh_scheduled_widgets(boundary)
    pusher.status = "sent"
    sched._maybe_refresh_scheduled_widgets(boundary + timedelta(hours=7))

    assert [push[3] for push in pusher.pushes] == ["widget_schedule", "widget_schedule"]


def test_widget_schedule_waits_through_quiet_without_push_events(tmp_path: Path) -> None:
    page = Page(
        id="reminders",
        name="Reminders",
        device_ids=["panel"],
        cells=[_scheduled_cell("a")],
    )
    ps = _pages(tmp_path, page)
    pusher = FakePush(quiet_devices={"panel"})
    registry = _scheduled_registry(tmp_path)
    sched = _sched(tmp_path, ps, pusher, plugin_registry=lambda: registry)

    sched._maybe_refresh_scheduled_widgets(datetime(2026, 6, 15, 23, 59, tzinfo=UTC))
    boundary = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    sched._maybe_refresh_scheduled_widgets(boundary)
    sched._maybe_refresh_scheduled_widgets(boundary + timedelta(seconds=30))
    assert pusher.pushes == []

    pusher.quiet_devices.clear()
    sched._maybe_refresh_scheduled_widgets(boundary + timedelta(seconds=60))
    assert len(pusher.pushes) == 1


def test_widget_schedule_backs_off_after_delivery_failure(tmp_path: Path) -> None:
    page = Page(
        id="reminders",
        name="Reminders",
        device_ids=["panel"],
        cells=[_scheduled_cell("a")],
    )
    ps = _pages(tmp_path, page)
    pusher = FakePush(status="failed")
    registry = _scheduled_registry(tmp_path)
    sched = _sched(tmp_path, ps, pusher, plugin_registry=lambda: registry)

    sched._maybe_refresh_scheduled_widgets(datetime(2026, 6, 15, 23, 59, tzinfo=UTC))
    boundary = datetime(2026, 6, 16, 0, 0, tzinfo=UTC)
    sched._maybe_refresh_scheduled_widgets(boundary)
    sched._maybe_refresh_scheduled_widgets(boundary + timedelta(seconds=30))
    assert len(pusher.pushes) == 1

    pusher.status = "sent"
    sched._maybe_refresh_scheduled_widgets(boundary + timedelta(minutes=5, seconds=1))
    assert len(pusher.pushes) == 2


def test_widget_schedule_warms_inactive_lineup_page_without_promoting(
    tmp_path: Path,
) -> None:
    ps = _pages(
        tmp_path,
        Page(id="active", name="Active", device_ids=["panel"]),
        Page(
            id="reminders",
            name="Reminders",
            device_ids=["panel"],
            cells=[_scheduled_cell("a")],
        ),
    )
    decks = DeckStore(tmp_path / "decks.json")
    decks.upsert(
        Deck(
            id="kitchen",
            name="Kitchen",
            device_ids=["panel"],
            pages=[DeckPage(page_id="active"), DeckPage(page_id="reminders")],
        )
    )
    nav = DeckNavStore(tmp_path / "nav.json")
    nav.set("panel", "kitchen", "active")
    pusher = FakePush()
    registry = _scheduled_registry(tmp_path)
    sched = _sched(
        tmp_path,
        ps,
        pusher,
        plugin_registry=lambda: registry,
        deck_store=decks,
        deck_nav_store=nav,
    )

    sched._maybe_refresh_scheduled_widgets(datetime(2026, 6, 15, 23, 59, tzinfo=UTC))
    sched._maybe_refresh_scheduled_widgets(datetime(2026, 6, 16, 0, 0, tzinfo=UTC))

    assert pusher.pushes == []
    assert pusher.warms == [("reminders", "panel")]
