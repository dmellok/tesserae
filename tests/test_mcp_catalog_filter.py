"""The catalog can be asked for less (#257).

`list_widgets` is the most expensive read an MCP agent makes, and an agent that
already knows it wants weather still paid for all of it. `?q=` narrows the
widgets to the ones that match, and `?fields=` trims each entry to what the
caller will use. Both are opt-in: without them the response is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flask import Flask
    from flask.testing import FlaskClient


def _enable(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})


def _catalog(client: FlaskClient, query: str = "") -> dict[str, Any]:
    resp = client.get(f"/api/mcp/catalog{query}")
    assert resp.status_code == 200, resp.get_json()
    body: dict[str, Any] = resp.get_json()
    return body


def test_without_a_filter_the_response_is_unchanged(app: Flask) -> None:
    _enable(app)
    body = _catalog(app.test_client())
    assert "filter" not in body
    assert {"key", "name", "desc", "fragments"} <= set(body["widgets"][0])


def test_q_keeps_only_matching_widgets_and_says_how_many_of_how_many(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    everything = _catalog(client)["widgets"]
    target = everything[0]

    body = _catalog(client, f"?q={target['key']}")

    keys = [w["key"] for w in body["widgets"]]
    assert target["key"] in keys
    assert len(keys) < len(everything), "a key should not match the whole catalog"
    assert body["filter"] == {
        "q": target["key"],
        "fields": None,
        "matched": len(keys),
        "total": len(everything),
    }


def test_q_is_case_insensitive_and_every_term_must_match(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    target = _catalog(client)["widgets"][0]
    name = str(target["name"])

    assert target["key"] in [w["key"] for w in _catalog(client, f"?q={name.upper()}")["widgets"]]
    both = _catalog(client, f"?q={name}+zzz-no-such-term")
    assert both["widgets"] == []


def test_q_searches_the_full_description_not_just_the_summary(app: Flask) -> None:
    """The catalog carries one sentence; a word from the rest still names the widget."""
    _enable(app)
    client = app.test_client()
    for widget in _catalog(client)["widgets"]:
        full = client.get(f"/api/mcp/widgets/{widget['key']}/options").get_json().get("desc") or ""
        visible = f"{widget['key']} {widget['name']} {widget['desc']}".lower()
        hidden = [
            w for w in full.lower().split() if w.isalpha() and len(w) > 6 and w not in visible
        ]
        if hidden:
            break
    else:
        raise AssertionError("no bundled widget has a description longer than its summary")

    keys = [w["key"] for w in _catalog(client, f"?q={hidden[0]}")["widgets"]]
    assert widget["key"] in keys


def test_nothing_matching_is_an_empty_list_that_says_so(app: Flask) -> None:
    _enable(app)
    body = _catalog(app.test_client(), "?q=zzz-no-such-widget")
    assert body["widgets"] == []
    assert body["filter"]["matched"] == 0
    assert body["filter"]["total"] > 0, "empty must read as 'nothing matched', not 'no widgets'"


def test_fields_trims_each_entry_and_always_keeps_the_key(app: Flask) -> None:
    _enable(app)
    body = _catalog(app.test_client(), "?fields=name,name")
    assert body["widgets"]
    assert all(set(w) == {"key", "name"} for w in body["widgets"])
    assert body["filter"]["fields"] == ["key", "name"]


def test_an_unknown_field_is_refused_with_the_valid_ones(app: Flask) -> None:
    _enable(app)
    resp = app.test_client().get("/api/mcp/catalog?fields=name,colour")
    assert resp.status_code == 400
    body = resp.get_json()
    assert "colour" in body["error"]
    assert "name" in body["fields"]
    assert "sample" not in body["fields"], "sample is never in the catalog, so it is not a field"


def test_the_filters_combine_and_leave_the_other_blocks_alone(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    plain = _catalog(client)
    target = plain["widgets"][0]

    body = _catalog(client, f"?q={target['key']}&fields=desc")

    assert all(set(w) == {"key", "desc"} for w in body["widgets"])
    assert target["key"] in [w["key"] for w in body["widgets"]]
    for block in ("appearance", "libraries", "icons"):
        assert body[block] == plain[block]
