"""Component settings preserve options and validate the actual target schemas."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from app.companion_component_settings import Schema, SchemaConflict
from app.companion_dashboard_settings import InvalidSettings
from app.state.page_store import Page

from .test_companion_dashboard_settings import app as _app_fixture


@pytest.fixture
def app(tmp_path):
    return _app_fixture.__wrapped__(tmp_path)


def registry() -> dict[str, Any]:
    return {
        "qa": SimpleNamespace(
            id="qa",
            manifest={
                "name": "QA",
                "version": "1",
                "cell_options": [
                    {"name": "title", "type": "string", "default": "Default"},
                    {
                        "name": "count",
                        "type": "number",
                        "default": 3,
                        "min": 0,
                        "max": 20,
                        "step": 1,
                    },
                    {"name": "list", "type": "select", "choices_from": "lists", "default": "a"},
                    {
                        "name": "players",
                        "type": "multiselect",
                        "choices_from": "lists",
                        "default": [],
                    },
                    {"name": "token", "type": "password", "default": "secret-default"},
                    {"name": "headers", "type": "textarea", "default": "{}"},
                    {"name": "url", "type": "string", "default": "fallback"},
                    {"name": "conditional", "type": "string", "show_if": {"title": "yes"}},
                ],
            },
            server_module=SimpleNamespace(
                choices=lambda _: [
                    {"value": "a", "label": "Alpha"},
                    {"value": "b", "label": "Beta"},
                ]
            ),
        )
    }


def page(*, grid: bool = False) -> Page:
    if grid:
        return Page.model_validate(
            {
                "id": "component-qa",
                "name": "QA",
                "cells": [
                    {
                        "id": "one",
                        "plugin": "qa",
                        "x": 0,
                        "y": 0,
                        "w": 100,
                        "h": 100,
                        "options": {"title": "First", "unknown": {"keep": 1}},
                    },
                    {
                        "id": "two",
                        "plugin": "qa",
                        "x": 100,
                        "y": 0,
                        "w": 100,
                        "h": 100,
                        "options": {"title": "Second"},
                    },
                ],
            }
        )
    return Page.model_validate(
        {
            "id": "component-qa",
            "name": "QA",
            "layout_kind": "canvas",
            "canvas": {
                "els": [
                    {
                        "id": "one",
                        "kind": "widget",
                        "widget": "qa",
                        "fragment": "body",
                        "options": {
                            "title": "First",
                            "players": ["stale", "a"],
                            "token": "credential",
                            "unknown": {"keep": 1},
                        },
                    },
                    {
                        "id": "two",
                        "kind": "code",
                        "source": "qa",
                        "options": {"title": "Legacy"},
                        "html": "unchanged",
                        "sources": [{"key": "qa", "name": "named", "options": {"title": "Source"}}],
                        "bind": [{"source": "qa", "options": {"title": "Bound"}}],
                    },
                ]
            },
        }
    )


def field(schema: Schema, name: str, component: int = 0):
    return next(f for f in schema.components[component]["fields"] if f.spec["name"] == name)


def patch(schema: Schema, values: dict[str, Any] | None = None, **kwargs: Any) -> Page:
    return schema.apply(
        {"schema_revision": schema.revision, "component_values": values or {}, **kwargs}
    )


@pytest.mark.parametrize("grid", [False, True])
def test_instances_defaults_clear_reset_and_unknown_preserved(grid: bool) -> None:
    p, reg = page(grid=grid), registry()
    before = p.model_dump()
    s = Schema(p, reg)
    assert p.model_dump() == before
    assert len(s.components) == (2 if grid else 4)
    title, count = field(s, "title"), field(s, "count")
    assert count.view["value"] == 3 and not count.view["has_override"]
    changed = patch(s, {title.id: "Updated", count.id: None})
    again = Schema(changed, reg)
    assert field(again, "count").view["value"] is None
    assert field(again, "count").view["has_override"]
    assert field(again, "title", 1).view["value"] == ("Second" if grid else "Legacy")
    reset = Schema(patch(again, reset_fields=[count.id, title.id]), reg)
    assert field(reset, "count").view["value"] == 3
    assert not field(reset, "count").view["has_override"]
    assert field(reset, "title").view["value"] == "Default"
    opts = reset.doc["cells"][0]["options"] if grid else reset.doc["canvas"]["els"][0]["options"]
    assert opts["unknown"] == {"keep": 1}


def test_dynamic_choices_order_stale_failure_and_schema_revision() -> None:
    reg = registry()
    s = Schema(page(), reg)
    players = field(s, "players")
    assert s.choices(players)[0][1]["value"] == "b"
    reordered = Schema(patch(s, {players.id: ["a", "stale", "b"]}), reg)
    assert field(reordered, "players").view["value"] == ["a", "stale", "b"]

    def fail(_):
        raise ValueError("PRIVATE-TOKEN")

    reg["qa"].server_module.choices = fail
    assert Schema(page(), reg).revision == s.revision
    s = Schema(page(), reg)
    assert s.choices(field(s, "players")) == ([], False)
    patch(s, {players.id: ["a", "stale"]})
    with pytest.raises(InvalidSettings, match="unavailable"):
        patch(Schema(page(), reg), {players.id: ["c"]})
    reg["qa"].manifest["cell_options"][1]["default"] = 4
    with pytest.raises(SchemaConflict):
        Schema(page(), reg).apply({"schema_revision": s.revision})


def test_shared_choice_provider_preserves_web_metadata_and_error_sentinels() -> None:
    from app.page_routes import _materialize_cell_options
    from app.widget_choices import resolve_choices

    plugin = registry()["qa"]
    spec = {"name": "list", "type": "select", "choices_from": "lists"}
    row = {"value": "a", "label": "Alpha", "image": "/widget/a.png"}
    plugin.manifest["cell_options"] = [spec]
    plugin.server_module.choices = lambda _: [row]
    assert _materialize_cell_options([plugin])["qa"][0]["choices"] == [row]
    assert resolve_choices(plugin, spec) == ([{"value": "a", "label": "Alpha"}], True)
    sentinel = {"value": "", "label": "Home Assistant unreachable: check connection"}
    plugin.server_module.choices = lambda _: [sentinel]
    assert _materialize_cell_options([plugin])["qa"][0]["choices"] == [sentinel]
    assert resolve_choices(plugin, spec) == ([], False)


def test_declared_shared_mixed_dedup_overlap_and_constraints() -> None:
    p = page()
    reg = registry()
    raw = p.model_dump(mode="json")
    raw["canvas"]["inputs"] = [
        {
            "name": "title",
            "label": "Shared",
            "targets": [{"el": "one", "key": "title"}, {"el": "two", "key": "title"}],
        }
    ]
    p = Page.model_validate(raw)
    s = Schema(p, reg)
    assert s.inputs[0].view["value_state"] == "mixed" and "value" not in s.inputs[0].view
    assert all(f.spec["name"] != "title" for f in s.components[0]["fields"])
    changed = patch(s, inputs={"title": "Shared"})
    assert (
        changed.canvas.els[0].options["title"] == changed.canvas.els[1].options["title"] == "Shared"
    )
    with pytest.raises(InvalidSettings, match="more than once"):
        patch(Schema(p, reg), inputs={"title": "x"}, reset_fields=[s.inputs[0].id])
    for invalid in (-1, 21, 2.5, True):
        with pytest.raises(InvalidSettings):
            patch(Schema(p, reg), {field(s, "count").id: invalid})
    reg["qa"].manifest["cell_options"][0]["required"] = True
    s = Schema(p, reg)
    assert s.inputs[0].view["required"] and not s.inputs[0].view["can_clear"]
    with pytest.raises(InvalidSettings):
        patch(s, inputs={"title": None})


def test_password_alias_propagates_and_option_headers_are_text() -> None:
    raw = page().model_dump(mode="json")
    reg = registry()
    raw["canvas"]["els"][0]["options"]["title"] = "credential"
    raw["canvas"]["inputs"] = [
        {
            "name": "secret",
            "label": "Secret",
            "type": "string",
            "targets": [{"el": "one", "key": "token"}, {"el": "one", "key": "title"}],
        },
        {"name": "alias", "label": "Alias", "targets": [{"el": "one", "key": "title"}]},
        {
            "name": "headers",
            "label": "Headers",
            "type": "textarea",
            "targets": [{"el": "two", "slot": "source_options", "index": 0, "key": "headers"}],
        },
        {"name": "url", "label": "URL", "targets": [{"el": "two", "key": "url"}]},
    ]
    s = Schema(Page.model_validate(raw), reg)
    assert "credential" not in str(s.response()) and "secret-default" not in str(s.response())
    assert all(f.view["secret"] for f in s.inputs[:2])
    updated = patch(s, inputs={"headers": "not json", "url": None})
    assert updated.canvas.els[1].sources[0].options["headers"] == "not json"
    assert updated.canvas.els[1].options["url"] is None


def test_schema_constraints_and_static_choices_change_conflict() -> None:
    reg = registry()
    p = page()
    s = Schema(p, reg)
    for key, value in (("min", 2), ("choices", [{"value": "x"}]), ("default", 7)):
        changed = copy.deepcopy(reg)
        changed["qa"].manifest["cell_options"][1][key] = value
        assert Schema(p, changed).revision != s.revision
    with pytest.raises(InvalidSettings):
        s.apply({"component_values": {field(s, "count").id: 4}})


def test_static_declared_choices_and_duplicate_multiselect() -> None:
    reg = registry()
    reg["qa"].manifest["cell_options"][2] = {
        "name": "list",
        "type": "select",
        "choices": [{"value": "a", "label": "Alpha"}, {"value": "b", "label": "Beta"}],
    }
    raw = page().model_dump(mode="json")
    raw["canvas"]["inputs"] = [
        {
            "name": "list",
            "label": "List",
            "type": "select",
            "targets": [{"el": "one", "key": "list"}],
        }
    ]
    s = Schema(Page.model_validate(raw), reg)
    assert s.inputs[0].view["choices"] == [
        {"value": "a", "label": "Alpha"},
        {"value": "b", "label": "Beta"},
    ]
    with pytest.raises(InvalidSettings, match="only once"):
        patch(s, {field(s, "players").id: ["a", "a"]})


def test_http_choices_permissions_pagination_atomic_schema(app) -> None:
    import jsonschema

    from ._schema import schema_for
    from .test_companion_dashboard_settings import _headers

    reg = registry()
    app.config["PLUGIN_REGISTRY"].plugins.update(reg)
    app.config["PAGE_STORE"].save(page())
    client, headers = app.test_client(), _headers(app)
    url = "/api/app/v1/dashboards/component-qa/settings"
    initial = client.get(url, headers=headers).get_json()
    jsonschema.validate(initial, schema_for("DashboardSettingsResponse"))
    selection = next(f for f in initial["components"][0]["fields"] if f["name"] == "list")
    choices_url = url + "/fields/" + selection["field_id"] + "/choices"
    assert client.get(choices_url).status_code == 401
    assert client.get(choices_url, headers=_headers(app, read=False)).status_code == 403
    result = client.get(choices_url + "?limit=1&offset=1&q=a", headers=headers).get_json()
    assert result["total"] == 2 and result["choices"] == [{"value": "b", "label": "Beta"}]
    jsonschema.validate(result, schema_for("DashboardSettingsChoicesResponse"))
    assert client.get(choices_url + "?schema_revision=old", headers=headers).status_code == 409
    token = next(f for f in initial["components"][0]["fields"] if f["name"] == "token")
    assert (
        client.get(url + "/fields/" + token["field_id"] + "/choices", headers=headers).status_code
        == 404
    )
    count = next(f for f in initial["components"][0]["fields"] if f["name"] == "count")
    body = {
        "revision": initial["revision"],
        "schema_revision": initial["schema_revision"],
        "settings": {"name": "Must not save"},
        "component_values": {count["field_id"]: 500},
    }
    assert client.patch(url, headers=headers, json=body).status_code == 400
    assert app.config["PAGE_STORE"].get("component-qa").name == "QA"
    reg["qa"].manifest["cell_options"][1]["max"] = 50
    body["component_values"][count["field_id"]] = 7
    response = client.patch(url, headers=headers, json=body)
    assert response.status_code == 409 and response.get_json()["error"]["code"] == "schema_conflict"

    def fail(_):
        raise RuntimeError("PRIVATE-TOKEN")

    reg["qa"].server_module.choices = fail
    result = client.get(choices_url, headers=headers)
    assert result.get_json()["status"] == "unavailable"
    assert "PRIVATE-TOKEN" not in result.get_data(as_text=True)
