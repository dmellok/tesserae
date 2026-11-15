"""Schedules and rotating lineups share the dashboard editor's list."""

from __future__ import annotations

import pytest

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page


@pytest.fixture
def app(tmp_path):
    app = create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="home", name="Home"))
    pages.save(Page(id="other", name="Other"))
    decks = app.config["DECK_STORE"]
    decks.upsert(
        Deck(
            id="rotation",
            name="Rotation",
            pages=[DeckPage(page_id="home"), DeckPage(page_id="other")],
        )
    )
    decks.upsert(
        Deck(
            id="morning",
            name="Morning",
            pages=[DeckPage(page_id="home")],
            advance="timer",
            advance_trigger="daily",
            advance_fires_at="07:00",
            advance_days_of_week=[0, 1, 2, 3, 4],
        )
    )
    return app


@pytest.mark.parametrize("is_new", [False, True])
def test_unified_section_lists_schedule_and_lineup_with_edit_remove_and_return(app, is_new):
    body = app.test_client().get("/pages/home", query_string={"new": "1"} if is_new else {}).text
    assert 'data-ev2-lrow="rotation"' in body
    assert 'data-ev2-lrow="morning"' in body
    assert "Schedule · Weekdays at " in body
    assert "Lineup · by hand" in body
    assert "In 2 lineups · 1 schedule" in body
    assert "Own schedules" not in body
    assert "Schedules and lineups" not in body
    assert "New scheduled lineup" in body
    assert 'href="/decks/rotation/edit"' in body
    assert 'data-open-schedule-dialog="schedule-dialog-morning"' in body
    assert 'id="schedule-dialog-new"' in body
    assert f'value="/pages/home{"?new=1#lineup" if is_new else "#schedules"}"' in body


@pytest.mark.parametrize("is_new", [False, True])
def test_quick_schedule_returns_and_leaving_removes_only_that_schedule(app, is_new):
    client = app.test_client()
    target = "/pages/home?new=1#lineup" if is_new else "/pages/home#schedules"
    response = client.post(
        "/schedules/new",
        data={
            "name": "Evening",
            "page_id": "home",
            "type": "daily",
            "fires_at": "19:00",
            "enabled": "on",
            "priority": "0",
            "next": target,
        },
    )
    assert response.location == target
    created = next(d for d in app.config["DECK_STORE"].all() if d.name == "Evening")
    body = client.get(target).text
    assert f'data-ev2-lrow="{created.id}"' in body
    response = client.post(f"/pages/home/lineups/{created.id}/leave")
    assert response.get_json()["ok"] is True
    assert app.config["DECK_STORE"].get(created.id) is None
    assert app.config["SCHEDULE_STORE"].get(created.id) is None
    assert app.config["DECK_STORE"].get("morning") is not None
    assert app.config["DECK_STORE"].get("rotation") is not None
    assert app.config["PAGE_STORE"].get("home") is not None


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example/",
        "/%2fevil.example/",
        "/%5cevil.example/",
        "/%0devil",
    ],
)
def test_quick_schedule_ignores_unsafe_return_paths(app, target):
    response = app.test_client().post(
        "/schedules/new",
        data={
            "name": "Safe",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "30",
            "priority": "0",
            "next": target,
        },
    )
    assert response.location.startswith("/decks?")


def test_multi_dashboard_timer_uses_lineup_editor_and_removes_only_this_page(app):
    decks = app.config["DECK_STORE"]
    deck = decks.get("rotation")
    decks.upsert(
        deck.model_copy(
            update={"advance": "timer", "advance_trigger": "daily", "advance_fires_at": "08:00"}
        )
    )
    body = app.test_client().get("/pages/home").text
    assert 'href="/decks/rotation/edit"' in body
    assert 'data-open-schedule-dialog="schedule-dialog-rotation"' not in body
    response = app.test_client().post("/pages/home/lineups/rotation/leave")
    assert response.status_code == 200
    assert decks.get("rotation").page_ids == ["other"]


def test_dashboard_without_lineups_has_empty_summary_and_creation_actions(app):
    app.config["PAGE_STORE"].save(Page(id="empty", name="Empty"))
    body = app.test_client().get("/pages/empty").text
    assert "Not in any lineup" in body
    assert "New scheduled lineup" in body
    assert "Add to a lineup" in body
    assert 'data-ev2-ladd="morning"' not in body
