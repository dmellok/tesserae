"""Deck admin routes: create / update / toggle / delete + graph validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page


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
