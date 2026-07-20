"""Deck admin routes: create / update / toggle / delete + graph validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    ps = a.config["PAGE_STORE"]
    ps.save(Page(id="overview", name="Overview"))
    ps.save(Page(id="calendar", name="Calendar"))
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


GRAPH = json.dumps(
    [
        {"page_id": "overview", "links": [{"target_page_id": "calendar", "button": "right"}]},
        {"page_id": "calendar", "links": [{"target_page_id": "overview", "button": "left"}]},
    ]
)


def _decks(app: Flask) -> list:
    return app.config["DECK_STORE"].all()


def test_create_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/new",
        data={"name": "Kitchen", "refresh_interval_minutes": "10", "graph_json": GRAPH},
    )
    assert resp.status_code in (302, 200)
    decks = _decks(app)
    assert len(decks) == 1
    assert decks[0].name == "Kitchen"
    assert decks[0].refresh_interval_minutes == 10
    assert decks[0].page_ids == ["overview", "calendar"]


def test_index_renders_and_lists_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "Kdeck", "graph_json": GRAPH})
    resp = client.get("/decks")
    assert resp.status_code == 200
    assert "Kdeck" in resp.get_data(as_text=True)


def test_invalid_graph_json_flashes_without_crash(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/new", data={"name": "Bad", "graph_json": "{not json"}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert _decks(app) == []


def test_link_to_page_not_in_deck_rejected(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    bad = json.dumps(
        [{"page_id": "overview", "links": [{"target_page_id": "ghost", "button": "right"}]}]
    )
    client.post("/decks/new", data={"name": "Bad", "graph_json": bad}, follow_redirects=True)
    assert _decks(app) == []


def test_update_preserves_enabled_and_changes_name(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(f"/decks/{did}/toggle")  # disable
    assert app.config["DECK_STORE"].get(did).enabled is False
    client.post(f"/decks/{did}/update", data={"name": "Renamed", "graph_json": GRAPH})
    updated = app.config["DECK_STORE"].get(did)
    assert updated.name == "Renamed"
    assert updated.enabled is False  # toggle state preserved across an edit


def test_delete_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(f"/decks/{did}/delete")
    assert app.config["DECK_STORE"].get(did) is None


def test_suggestions_render_from_page_links(app: Flask) -> None:
    ps = app.config["PAGE_STORE"]
    ps.save(
        Page(
            id="overview",
            name="Overview",
            cells=[Cell(id="c1", x=0, y=0, w=12, h=6, on_tap="page:calendar")],
        )
    )
    ps.save(
        Page(
            id="calendar",
            name="Calendar",
            cells=[Cell(id="c2", x=0, y=0, w=12, h=6, on_tap="page:overview")],
        )
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "Suggested from your page links" in body
    # A pre-filled create form is rendered for the suggestion.
    assert "graph_json" in body and "Create deck" in body


def test_update_sets_per_page_refresh(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(
        f"/decks/{did}/update",
        data={
            "name": "K",
            "refresh_interval_minutes": "20",
            "page_refresh_overview": "5",
            "page_refresh_calendar": "",
        },
    )
    deck = app.config["DECK_STORE"].get(did)
    assert deck.refresh_interval_minutes == 20
    assert deck.page("overview").refresh_interval_minutes == 5
    assert deck.page("calendar").refresh_interval_minutes is None  # blank -> inherit


def test_update_preserves_graph(app: Flask) -> None:
    # The management update doesn't submit the graph; links must survive.
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(f"/decks/{did}/update", data={"name": "Renamed"})
    deck = app.config["DECK_STORE"].get(did)
    assert deck.name == "Renamed"
    assert any(lnk.target_page_id == "calendar" for lnk in deck.page("overview").links)


def test_sync_rederives_graph_from_page_links(app: Flask) -> None:
    ps = app.config["PAGE_STORE"]
    ps.save(Page(id="ov", name="Ov", cells=[Cell(id="c", x=0, y=0, w=12, h=6, on_tap="page:cal")]))
    ps.save(Page(id="cal", name="Cal", cells=[Cell(id="c", x=0, y=0, w=12, h=6, on_tap="page:ov")]))
    client = app.test_client()
    _sign_in(client)
    graph = json.dumps([{"page_id": "ov", "links": []}, {"page_id": "cal", "links": []}])
    client.post("/decks/new", data={"name": "D", "graph_json": graph})
    did = _decks(app)[0].id
    assert app.config["DECK_STORE"].get(did).page("ov").links == []
    client.post(f"/decks/{did}/sync")
    deck = app.config["DECK_STORE"].get(did)
    assert any(lnk.target_page_id == "cal" for lnk in deck.page("ov").links)


def test_edit_graph_advanced_replaces_graph(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    new_graph = json.dumps(
        [
            {"page_id": "overview", "links": []},
            {"page_id": "weather", "links": [{"target_page_id": "overview", "button": "left"}]},
        ]
    )
    client.post(f"/decks/{did}/graph", data={"graph_json": new_graph})
    assert set(app.config["DECK_STORE"].get(did).page_ids) == {"overview", "weather"}
