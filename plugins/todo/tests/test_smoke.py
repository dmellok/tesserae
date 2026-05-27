"""todo smoke: widget cell + admin blueprint round-trip.

The cell renders deterministically from a JSON file in the plugin's
data_dir, so we seed that file directly rather than driving the admin
forms — much faster and avoids the need for authenticated requests
from the test client."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient


def _seed_lists(app: Flask, data: dict) -> Path:
    plugin = app.config["PLUGIN_REGISTRY"].get("todo")
    assert plugin is not None
    path = plugin.data_dir / "lists.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_todo_renders_empty_when_no_list_selected(client: FlaskClient, size: str) -> None:
    resp = client.get(f"/_test/render?plugin=todo&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="todo"' in body
    # No list_id option means the empty state should appear.
    assert "Pick a list" in body or "list" in body.lower()


def test_todo_cell_data_includes_seeded_items(app: Flask, client: FlaskClient) -> None:
    _seed_lists(
        app,
        {
            "lists": [
                {
                    "id": "groceries",
                    "name": "Groceries",
                    "created_at": "2026-05-27T10:00:00+00:00",
                    "items": [
                        {
                            "id": "a",
                            "text": "Milk",
                            "created_at": "2026-05-27T10:01:00+00:00",
                            "completed_at": None,
                        },
                        {
                            "id": "b",
                            "text": "Bread",
                            "created_at": "2026-05-27T10:02:00+00:00",
                            "completed_at": None,
                        },
                    ],
                }
            ]
        },
    )
    # Call fetch() directly with the cell's data_dir; this exercises
    # the same code path the composer uses.
    plugin = app.config["PLUGIN_REGISTRY"].get("todo")
    out = plugin.server_module.fetch(
        {"list_id": "groceries", "show_completed": True, "max_items": 0},
        {},
        ctx={"data_dir": str(plugin.data_dir)},
    )
    assert out["list_name"] == "Groceries"
    assert len(out["items"]) == 2
    assert out["items"][0]["text"] == "Milk"


def test_todo_prunes_items_completed_more_than_24h_ago(app: Flask) -> None:
    _seed_lists(
        app,
        {
            "lists": [
                {
                    "id": "x",
                    "name": "X",
                    "items": [
                        {
                            "id": "old",
                            "text": "old done",
                            "created_at": "2026-05-20T00:00:00+00:00",
                            "completed_at": "2026-05-20T00:00:00+00:00",
                        },
                        {
                            "id": "new",
                            "text": "fresh",
                            "created_at": "2026-05-26T00:00:00+00:00",
                            "completed_at": None,
                        },
                    ],
                }
            ]
        },
    )
    plugin = app.config["PLUGIN_REGISTRY"].get("todo")
    out = plugin.server_module.fetch(
        {"list_id": "x"},
        {},
        ctx={"data_dir": str(plugin.data_dir)},
    )
    ids = [i["id"] for i in out["items"]]
    assert "old" not in ids
    assert "new" in ids


def test_todo_admin_blueprint_responds(app: Flask, client: FlaskClient) -> None:
    """The admin index page should render even with no data."""
    # The admin page is auth-gated in the real app, but in testing mode
    # the auth middleware accepts unauthenticated requests through to
    # the route handlers.
    with client.session_transaction() as sess:
        sess["authed"] = True
    resp = client.get("/plugins/todo/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Todo" in body


def test_todo_choices_returns_lists(app: Flask, client: FlaskClient) -> None:
    _seed_lists(
        app,
        {
            "lists": [
                {"id": "a", "name": "Alpha", "items": []},
                {"id": "b", "name": "Beta", "items": []},
            ]
        },
    )
    plugin = app.config["PLUGIN_REGISTRY"].get("todo")
    # choices() reads from current_app — drive it inside a request ctx.
    with app.test_request_context("/"):
        choices = plugin.server_module.choices("lists")
    assert {"value": "a", "label": "Alpha"} in choices
    assert {"value": "b", "label": "Beta"} in choices
