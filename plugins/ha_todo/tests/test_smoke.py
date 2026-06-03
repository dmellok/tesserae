"""ha_todo smoke: composer renders for all 4 variants with ha_core mocked
— no HA connection, no network."""

from __future__ import annotations

import pytest
from flask import Flask
from flask.testing import FlaskClient

# Sample HA service response shape: items live under
# ``service_response[<entity_id>].items``. Each item has a uid + summary
# + status + optional due (ISO date or datetime).
_ITEMS = [
    {"uid": "001", "summary": "Milk", "status": "needs_action", "due": None},
    {"uid": "002", "summary": "Eggs", "status": "needs_action", "due": "2026-06-10"},
    {"uid": "003", "summary": "Bread", "status": "completed", "due": None},
]

_STATE = {
    "entity_id": "todo.shopping_list",
    "state": "2",
    "attributes": {"friendly_name": "Shopping List"},
}

_SERVICE_RESPONSE = {
    "changed_states": [],
    "service_response": {"todo.shopping_list": {"items": _ITEMS}},
}


def _core(app: Flask):
    return app.config["PLUGIN_REGISTRY"].get("ha_core").server_module


def _patch_core(monkeypatch, core):
    """Pin ha_core to a happy-path config + canned HA responses."""
    monkeypatch.setattr(core, "is_configured", lambda: True)
    monkeypatch.setattr(core, "get_state", lambda _eid: _STATE)
    monkeypatch.setattr(
        core,
        "call_service_with_response",
        lambda _d, _s, *, data: _SERVICE_RESPONSE,
    )
    monkeypatch.setattr(core, "friendly_name", lambda st: st["attributes"]["friendly_name"])


@pytest.mark.parametrize("variant", ["r1", "g2", "s3", "d4"])
@pytest.mark.parametrize("size", ["sm", "md", "lg"])
def test_todo_renders_items_per_variant(client: FlaskClient, variant: str, size: str) -> None:
    """Use ``?sample=1`` to short-circuit the widget's fetch() and let
    the gallery's widget_samples fixture provide the data. That's the
    same path /_test/widgets uses — exercises the full render shell
    without needing to mock HA service-call responses.

    The cell payload JSON (data-data attribute) carries each item's
    summary, so a substring grep proves the data reached the cell."""
    resp = client.get(
        f"/_test/render?plugin=ha_todo&size={size}&variant={variant}&theme=default&sample=1"
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="ha_todo"' in body
    # Two sample items that aren't substrings of anything else in the
    # page chrome; if the data didn't reach the cell these would be
    # absent.
    assert "Pay the electric bill" in body
    assert "Refill prescription" in body


def test_todo_error_when_not_configured(app: Flask, client: FlaskClient, monkeypatch) -> None:
    monkeypatch.setattr(_core(app), "is_configured", lambda: False)
    resp = client.get("/_test/render?plugin=ha_todo&size=md")
    assert resp.status_code == 200
    assert "not configured" in resp.get_data(as_text=True)


def test_todo_max_items_truncation(monkeypatch) -> None:
    """``max_items`` is enforced server-side. Direct unit test on the
    server module so we don't need the full Flask app."""
    from plugins.ha_todo import server as widget_server

    class _FakeCore:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def get_state(_eid):
            return _STATE

        @staticmethod
        def call_service_with_response(_d, _s, *, data):
            return _SERVICE_RESPONSE

        @staticmethod
        def friendly_name(st):
            return st["attributes"]["friendly_name"]

        @staticmethod
        def coerce_error(err):
            return str(err)

    monkeypatch.setattr(widget_server, "_core", lambda: _FakeCore)
    result = widget_server.fetch(
        {
            "entity_id": "todo.shopping_list",
            "max_items": 1,
            "include_completed": False,
        },
        settings={},
        ctx={},
    )
    # 1 needs_action item kept, completed item dropped.
    assert len(result["items"]) == 1
    assert result["items"][0]["summary"] == "Milk"
    assert result["needs_action_count"] == 2


def test_todo_include_completed(monkeypatch) -> None:
    from plugins.ha_todo import server as widget_server

    class _FakeCore:
        @staticmethod
        def is_configured():
            return True

        @staticmethod
        def get_state(_eid):
            return _STATE

        @staticmethod
        def call_service_with_response(_d, _s, *, data):
            return _SERVICE_RESPONSE

        @staticmethod
        def friendly_name(st):
            return st["attributes"]["friendly_name"]

        @staticmethod
        def coerce_error(err):
            return str(err)

    monkeypatch.setattr(widget_server, "_core", lambda: _FakeCore)
    result = widget_server.fetch(
        {
            "entity_id": "todo.shopping_list",
            "max_items": 8,
            "include_completed": True,
        },
        settings={},
        ctx={},
    )
    # Both needs_action items + the completed one all land.
    assert len(result["items"]) == 3
    assert result["completed_count"] == 1
