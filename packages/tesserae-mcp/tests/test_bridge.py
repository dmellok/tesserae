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
        "list_icons",
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


def test_probe_payload_truncation_keeps_shape():
    """A 24h Home Assistant history overflowed the MCP result limit with no
    truncation, so the whole result was unusable and the only recourse was
    grepping a saved tool-result file. Long lists are shortened; the shape and
    the scalars stay, and each trimmed list says how long it really was."""
    from tesserae_mcp import _truncate_payload

    payload = {
        "history": [{"t": i, "v": i * 1.5} for i in range(400)],
        "unit": "C",
        "nested": {"rows": list(range(100))},
    }
    out, dropped = _truncate_payload(payload, 20)

    assert len(out["history"]) == 20
    assert out["history__truncated"] == {"total": 400, "shown": 20}
    assert len(out["nested"]["rows"]) == 20
    assert out["nested"]["rows__truncated"] == {"total": 100, "shown": 20}
    # Scalars and short lists are untouched, and the shape is preserved.
    assert out["unit"] == "C"
    assert out["history"][0] == {"t": 0, "v": 0.0}
    assert dropped == ["data.history (400 items)", "data.nested.rows (100 items)"]

    small = {"a": [1, 2, 3]}
    assert _truncate_payload(small, 20) == (small, [])


def test_catalog_summary_keeps_identity_and_drops_option_schemas():
    """The full catalog is ~95k characters, past the tool-result cap, so it
    spools to a file the caller has to grep. Each widget keeps what answers
    "which widget do I want"; get_widget_options(key) answers the rest, for one
    widget instead of all of them."""
    from tesserae_mcp import _summarise_catalog

    catalog = {
        "widgets": [
            {
                "key": f"w{i}",
                "name": f"W{i}",
                "desc": "Does a thing.",
                "fragments": ["sm", "lg"],
                "options": {"a": {"blob": "y" * 400}},
            }
            for i in range(60)
        ],
        "appearance": {"themes": ["a", "b"]},
        "libraries": ["chartjs"],
    }
    out = _summarise_catalog(catalog)

    assert sorted(out["widgets"][0]) == ["desc", "fragments", "key", "name"]
    assert "options" not in out["widgets"][0]
    # "desc" is what the catalog actually calls it (panels_schema.catalog_entry);
    # it was missing from the summary keys, so the default list_widgets() view
    # carried no description at all and agents picked widgets from key+name alone.
    assert out["widgets"][0]["desc"] == "Does a thing."
    assert len(out["widgets"]) == 60, "every widget still listed, just trimmed"
    # The small blocks are exactly what a summary can't stand in for.
    assert out["appearance"] == catalog["appearance"]
    assert out["libraries"] == catalog["libraries"]
    assert out["summarised"]["widgets"] == 60

    # A catalog with nothing to trim gains no note.
    lean = {"widgets": [{"key": "a", "name": "A"}], "appearance": {}}
    assert "summarised" not in _summarise_catalog(lean)


def test_served_tool_docs_replace_the_embedded_docstring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool description fix on the server reaches an installed bridge, the same
    way an instructions fix already does. This is what stops a wrong contract
    (create_schedule's fires_at, say) needing a PyPI release to correct."""
    import asyncio
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "doc_shape": "SERVER DOC SHAPE",
            "tool_docs": {"create_schedule": "SERVED create_schedule CONTRACT"},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    tools = asyncio.run(bridge.build_server().list_tools())
    by_name = {t.name: t for t in tools}

    assert by_name["create_schedule"].description == "SERVED create_schedule CONTRACT"
    # Tools the server didn't override keep their embedded docstring.
    assert "Delete a schedule by id" in (by_name["delete_schedule"].description or "")


def test_doc_shape_is_appended_to_served_tool_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canvas shape rides on the two document-writing tools whether their
    prose came from the server or the embedded copy, so the served text never has
    to repeat it (and can't drift from DOC_SHAPE)."""
    import asyncio
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "doc_shape": "SERVER DOC SHAPE abc",
            "tool_docs": {"set_canvas": "SERVED set_canvas PROSE"},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    tools = asyncio.run(bridge.build_server().list_tools())
    description = next(t for t in tools if t.name == "set_canvas").description or ""

    assert "SERVED set_canvas PROSE" in description
    assert "SERVER DOC SHAPE abc" in description
    # add_element gets it too, and it is stated exactly once.
    add_element = next(t for t in tools if t.name == "add_element").description or ""
    assert add_element.count("SERVER DOC SHAPE abc") == 1


def test_malformed_tool_docs_fall_back_to_the_docstring(monkeypatch: pytest.MonkeyPatch) -> None:
    """A junk entry must not register a tool with an empty description."""
    import asyncio
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "tool_docs": {"list_devices": "   ", "list_pages": 42},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    tools = asyncio.run(bridge.build_server().list_tools())
    by_name = {t.name: t for t in tools}

    assert "colour capability" in (by_name["list_devices"].description or "")
    assert "canvas (freeform) dashboards" in (by_name["list_pages"].description or "")


def test_every_tool_has_a_description() -> None:
    """Registration reads each function's docstring, so a new tool without one
    would silently register blank."""
    import asyncio

    tools = asyncio.run(bridge.build_server().list_tools())

    blank = [t.name for t in tools if not (t.description or "").strip()]
    assert blank == []


def test_an_older_bridge_is_told_it_is_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    """The handshake carries the upgrade note, so the agent can tell the operator
    rather than silently running against stale tool descriptions."""
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "bridge": {"latest": "99.0.0", "upgrade": "pipx upgrade tesserae-mcp"},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    text = bridge.build_server().instructions or ""

    assert "BRIDGE OUT OF DATE" in text
    assert "99.0.0" in text and bridge.__version__ in text
    assert "pipx upgrade tesserae-mcp" in text


def test_a_current_bridge_gets_no_upgrade_note(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "bridge": {"latest": bridge.__version__},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    assert "BRIDGE OUT OF DATE" not in (bridge.build_server().instructions or "")


def test_a_bridge_ahead_of_the_server_gets_no_upgrade_note(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running from a clone: telling the operator to "upgrade" to an older
    release would be wrong."""
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "bridge": {"latest": "0.0.1"},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    assert "BRIDGE OUT OF DATE" not in (bridge.build_server().instructions or "")


def test_an_unparseable_version_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    payload = json.dumps(
        {
            "schema": bridge._DOCS_SCHEMA,
            "instructions": "SERVER INSTRUCTIONS",
            "bridge": {"latest": "not-a-version"},
        }
    ).encode()
    monkeypatch.setattr(bridge, "_request", _fake_request(200, payload))

    assert "BRIDGE OUT OF DATE" not in (bridge.build_server().instructions or "")
