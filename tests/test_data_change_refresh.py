"""Semantic data events, placement matching, and quiet-window debounce."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from app.data_change_refresh import (
    DataChangeEvent,
    DataChangeRefreshCoordinator,
    matching_page_ids,
    personal_data_delete_event,
    personal_data_update_event,
)
from app.plugin_loader import Plugin, PluginRegistry
from app.state.page_store import Cell, Page, PageStore
from app.state.panel_store import CanvasLayout, Element


def _snapshot(*, generated: str, expires: str, lists: list[dict]) -> dict:
    return {
        "version": "personal_data_bridge_v1",
        "source_id": "reminders",
        "generated_at": generated,
        "expires_at": expires,
        "data": {"lists": lists},
    }


def _list(list_id: str, title: str, item_title: str = "Milk") -> dict:
    return {
        "id": list_id,
        "title": title,
        "items": [
            {
                "id": f"item-{list_id}",
                "title": item_title,
                "due_date": None,
                "priority": "none",
                "completed": False,
            }
        ],
    }


def _plugin(tmp_path: Path, plugin_id: str, *, selector: bool = True) -> Plugin:
    spec = {"source": "personal_data.reminders"}
    if selector:
        spec["selector_option"] = "list_id"
    return Plugin(
        id=plugin_id,
        path=tmp_path / plugin_id,
        manifest={
            "name": plugin_id,
            "kind": "widget",
            "supports": {"sizes": ["md"]},
            "updates": {"on_change": [spec]},
        },
        data_dir=tmp_path / "data" / plugin_id,
    )


def test_reminders_semantic_diff_ignores_ttl_and_order_only_changes() -> None:
    before = _snapshot(
        generated="2026-08-05T00:00:00Z",
        expires="2026-08-06T23:00:00Z",
        lists=[_list("food", "Groceries"), _list("work", "Work")],
    )
    after = _snapshot(
        generated="2026-08-05T01:00:00Z",
        expires="2026-08-07T00:00:00Z",
        lists=[_list("work", "Work"), _list("food", "Groceries")],
    )
    assert personal_data_update_event("reminders", before, after) is None


def test_reminders_semantic_diff_returns_only_changed_list_ids() -> None:
    before = _snapshot(
        generated="2026-08-05T00:00:00Z",
        expires="2026-08-06T23:00:00Z",
        lists=[_list("food", "Groceries"), _list("work", "Work")],
    )
    after = _snapshot(
        generated="2026-08-05T01:00:00Z",
        expires="2026-08-07T00:00:00Z",
        lists=[_list("food", "Groceries", "Oat milk"), _list("home", "Home")],
    )
    event = personal_data_update_event("reminders", before, after)
    assert event == DataChangeEvent(
        source="personal_data.reminders",
        selectors=frozenset({"food", "work", "home"}),
    )


def test_legacy_fridge_semantic_diff_ignores_item_order() -> None:
    before = {
        "data": {
            "items": [
                _list("food", "Groceries")["items"][0],
                _list("work", "Work")["items"][0],
            ]
        }
    }
    after = {"data": {"items": list(reversed(before["data"]["items"]))}}

    assert personal_data_update_event("reminders.fridge", before, after) is None

    after["data"]["items"][0] = {
        **after["data"]["items"][0],
        "title": "Updated task",
    }
    assert personal_data_update_event("reminders.fridge", before, after) == DataChangeEvent(
        source="personal_data.reminders.fridge"
    )


def test_first_snapshot_is_source_wide_even_when_it_contains_no_lists() -> None:
    current = _snapshot(
        generated="2026-08-05T00:00:00Z",
        expires="2026-08-06T23:00:00Z",
        lists=[],
    )
    assert personal_data_update_event("reminders", None, current) == DataChangeEvent(
        source="personal_data.reminders"
    )


def test_first_nonempty_snapshot_and_delete_are_narrowed_to_known_lists() -> None:
    current = _snapshot(
        generated="2026-08-05T12:00:00Z",
        expires="2026-08-07T12:00:00Z",
        lists=[
            _list("food", "Groceries", "Milk"),
            _list("work", "Tasks", "Review PR"),
        ],
    )
    expected = DataChangeEvent(
        source="personal_data.reminders",
        selectors=frozenset({"food", "work"}),
    )

    assert personal_data_update_event("reminders", None, current) == expected
    assert personal_data_delete_event("reminders", current) == expected


def test_health_summary_diff_is_source_wide_and_ignores_envelope_renewal() -> None:
    before = {
        "version": "personal_data_bridge_v1",
        "source_id": "health.summary",
        "generated_at": "2026-08-14T12:00:00Z",
        "expires_at": "2026-08-16T12:00:00Z",
        "data": {"sleep": {"nights": []}},
    }
    renewed = {
        **before,
        "generated_at": "2026-08-14T12:30:00Z",
        "expires_at": "2026-08-16T12:30:00Z",
    }
    changed = {**renewed, "data": {"sleep": {"nights": [{"wake_date": "2026-08-14"}]}}}
    expected = DataChangeEvent(source="personal_data.health.summary")

    assert personal_data_update_event("health.summary", None, before) == expected
    assert personal_data_update_event("health.summary", before, renewed) is None
    assert personal_data_update_event("health.summary", renewed, changed) == expected
    assert personal_data_delete_event("health.summary", changed) == expected


def test_matching_pages_respects_placement_opt_in_and_selector(tmp_path: Path) -> None:
    registry = PluginRegistry(
        plugins={
            "reminders": _plugin(tmp_path, "reminders"),
            "source_wide": _plugin(tmp_path, "source_wide", selector=False),
        }
    )
    pages = [
        Page(
            id="grid_food",
            name="Food",
            cells=[
                Cell(
                    id="food",
                    plugin="reminders",
                    options={"list_id": "food"},
                    update_on_change=True,
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                ),
                Cell(
                    id="food-duplicate",
                    plugin="reminders",
                    options={"list_id": "food"},
                    update_on_change=True,
                    x=100,
                    y=0,
                    w=100,
                    h=100,
                ),
            ],
        ),
        Page(
            id="grid_disabled",
            name="Disabled",
            cells=[
                Cell(
                    id="off",
                    plugin="reminders",
                    options={"list_id": "food"},
                    update_on_change=False,
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                )
            ],
        ),
        Page(
            id="grid_work",
            name="Work",
            cells=[
                Cell(
                    id="work",
                    plugin="reminders",
                    options={"list_id": "work"},
                    update_on_change=True,
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                )
            ],
        ),
        Page(
            id="canvas_food",
            name="Canvas",
            layout_kind="canvas",
            canvas=CanvasLayout(
                els=[
                    Element(
                        id="food",
                        widget="reminders",
                        options={"list_id": "food"},
                        update_on_change=True,
                    )
                ]
            ),
        ),
        Page(
            id="source_wide",
            name="Source wide",
            cells=[
                Cell(
                    id="all",
                    plugin="source_wide",
                    update_on_change=True,
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                )
            ],
        ),
    ]
    events = {"personal_data.reminders": frozenset({"food"})}
    assert matching_page_ids(pages, registry, events) == {
        "grid_food",
        "canvas_food",
        "source_wide",
    }


def test_source_wide_event_matches_every_enabled_selector(tmp_path: Path) -> None:
    registry = PluginRegistry(plugins={"reminders": _plugin(tmp_path, "reminders")})
    pages = [
        Page(
            id="work",
            name="Work",
            cells=[
                Cell(
                    id="work",
                    plugin="reminders",
                    options={"list_id": "work"},
                    update_on_change=True,
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                )
            ],
        )
    ]
    assert matching_page_ids(pages, registry, {"personal_data.reminders": None}) == {"work"}


def test_coordinator_debounces_and_unions_selector_bursts(tmp_path: Path) -> None:
    registry = PluginRegistry(plugins={"reminders": _plugin(tmp_path, "reminders")})
    store = PageStore(tmp_path / "pages.json")
    for list_id in ("food", "work"):
        store.save(
            Page(
                id=list_id,
                name=list_id,
                cells=[
                    Cell(
                        id=list_id,
                        plugin="reminders",
                        options={"list_id": list_id},
                        update_on_change=True,
                        x=0,
                        y=0,
                        w=100,
                        h=100,
                    )
                ],
            )
        )
    fired = threading.Event()
    refreshes: list[set[str]] = []

    def refresh(page_ids: set[str]) -> None:
        refreshes.append(page_ids)
        fired.set()

    coordinator = DataChangeRefreshCoordinator(
        page_store=store,
        plugin_registry=lambda: registry,
        refresh_pages=refresh,
        debounce_seconds=0.15,
    )
    coordinator.notify(DataChangeEvent("personal_data.reminders", selectors=frozenset({"food"})))
    time.sleep(0.10)
    coordinator.notify(DataChangeEvent("personal_data.reminders", selectors=frozenset({"work"})))

    # A pure quiet-window debounce restarts on every event; it has no max cap.
    assert not fired.wait(0.08)
    assert fired.wait(1.0)
    time.sleep(0.08)
    coordinator.stop()
    assert refreshes == [{"food", "work"}]
