"""Smoke tests for the tesserae-mcp bridge.

These don't need a running Tesserae; they exercise the local plumbing (JSON
handling) and, when the mcp SDK is present, that all tools register.
"""

from __future__ import annotations

import pytest

import tesserae_mcp as bridge


def test_tools_register() -> None:
    """build_server() wires up every tool with the set_canvas doc spec."""
    import asyncio

    server = bridge.build_server()
    tools = asyncio.run(server.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "list_widgets",
        "list_services",
        "get_widget_options",
        "get_widget_choices",
        "probe_widget_data",
        "list_devices",
        "list_pages",
        "create_canvas_page",
        "delete_canvas_page",
        "get_canvas",
        "set_canvas",
        "set_canvas_background",
        "add_element",
        "add_elements_bulk",
        "describe_actions",
        "update_element",
        "append_code",
        "delete_element",
        "patch_canvas",
        "arrange",
        "measure_text",
        "render_report",
        "render_preview",
        "push_to_device",
        "bind_devices",
        "list_rotations",
        "create_rotation",
        "delete_rotation",
        "list_schedules",
        "create_schedule",
        "delete_schedule",
        "list_decks",
        "suggest_decks",
        "create_deck",
        "delete_deck",
    }
    set_canvas = next(t for t in tools if t.name == "set_canvas")
    assert "canvas document is JSON" in (set_canvas.description or "")


def test_server_ships_compose_instructions() -> None:
    """The compose-agent operator loop is sent at handshake via FastMCP
    instructions, so it lives with the tools instead of being pasted in."""
    server = bridge.build_server()
    text = server.instructions or ""
    assert "LOOP:" in text and "render_report" in text and "bind" in text
    # Bind-early + incremental-build guidance (so the agent targets hardware up
    # front and builds visibly instead of one big post at the end).
    assert "START HERE" in text
    assert "bind_devices" in text and "RIGHT AWAY" in text
    assert "append_code" in text
    # Touch actions are surfaced in the handshake instructions, not just buried
    # in the set_canvas doc-shape, so the agent knows they exist (issue #49).
    assert "INTERACTIVITY" in text
    assert "on_tap" in text and "on_slide" in text and "hotspot" in text


def _fake_request(status: int, body: bytes):
    return lambda *a, **k: (status, body, "application/json")


def test_fetch_docs_prefers_server_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """When Tesserae serves /instructions, the bridge uses that text verbatim
    (so a server-side copy change needs no bridge republish)."""
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS xyz",
            "doc_shape": "SERVER DOC SHAPE canvas document is JSON abc",
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))
    import asyncio

    server = bridge.build_server()
    assert "SERVER INSTRUCTIONS xyz" in (server.instructions or "")
    tools = asyncio.run(server.list_tools())
    set_canvas = next(t for t in tools if t.name == "set_canvas")
    assert "SERVER DOC SHAPE" in (set_canvas.description or "")


def test_fetch_docs_falls_back_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreachable / experiment-off server leaves the embedded copy in place."""

    def _boom(*a, **k):
        raise RuntimeError("cannot reach Tesserae")

    monkeypatch.setattr(bridge, "_request", _boom)
    server = bridge.build_server()
    assert server.instructions == bridge._INSTRUCTIONS


def test_fetch_docs_rejects_schema_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A payload shape this bridge doesn't understand is ignored (embedded copy wins)."""
    import json

    payload = json.dumps({"schema": 999, "instructions": "NEW SHAPE"}).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))
    assert bridge._fetch_docs() == {}
    server = bridge.build_server()
    assert server.instructions == bridge._INSTRUCTIONS


def test_json_wraps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx response is returned as data (with _status), not raised, so the
    agent can read 422 validation details."""
    monkeypatch.setattr(
        bridge,
        "_request",
        lambda *a, **k: (422, b'{"error":"bad","details":[]}', "application/json"),
    )
    out = bridge._json("PUT", "/pages/x/canvas", {"w": 0})
    assert out["error"] == "bad" and out["_status"] == 422
