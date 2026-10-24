"""Native settings API permission, concurrency and lossless editing boundary."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import jsonschema
import pytest
from flask import Flask

from app.composer import page_preview_token
from app.state.companion_token_store import COMPANION_SCOPES
from app.state.page_store import Page, PageRevisionConflict, PageStore, page_revision
from app.state.panel_store import CanvasLayout

from ._schema import schema_for
from .test_companion_api import _seed_device
from .test_companion_api import app as _app_fixture


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return _app_fixture.__wrapped__(tmp_path)


URL = "/api/app/v1/dashboards/pantry/settings"


def _headers(app: Flask, *, write: bool = True, read: bool = True) -> dict[str, str]:
    scopes = (["dashboards:read"] if read else []) + (["dashboards:write"] if write else [])
    token, _ = app.config["COMPANION_TOKENS"].issue(client={"name": "Test"}, scopes=scopes)
    return {"Authorization": f"Bearer {token}"}


def _seed(app: Flask, *, canvas: bool = True) -> Page:
    layout = CanvasLayout.model_validate(
        {
            "theme": "dark",
            "style": "editorial",
            "font": "old-font",
            "bg": "#123456",
            "els": [
                {
                    "id": "tile",
                    "kind": "code",
                    "js": "ctx.keepMe()",
                    "html": "<b>Keep</b>",
                    "options": {
                        "location": "London",
                        "count": 3,
                        "enabled": False,
                        "choice": "a",
                        "many": ["a"],
                        "unknown_future": {"x": 42},
                    },
                    "sources": [
                        {
                            "name": "api",
                            "url": "https://example.org",
                            "headers": {"Authorization": "super-secret", "X-Keep": "keep"},
                        }
                    ],
                }
            ],
            "inputs": [
                {
                    "name": "location",
                    "label": "Location",
                    "type": "string",
                    "required": True,
                    "targets": [{"el": "tile", "key": "location"}],
                },
                {
                    "name": "count",
                    "label": "Count",
                    "type": "integer",
                    "targets": [{"el": "tile", "key": "count"}],
                },
                {
                    "name": "enabled",
                    "label": "Enabled",
                    "type": "boolean",
                    "targets": [{"el": "tile", "key": "enabled"}],
                },
                {
                    "name": "choice",
                    "label": "Choice",
                    "type": "select",
                    "choices": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
                    "targets": [{"el": "tile", "key": "choice"}],
                },
                {
                    "name": "many",
                    "label": "Many",
                    "type": "multiselect",
                    "choices": [{"value": "a", "label": "A"}],
                    "targets": [{"el": "tile", "key": "many"}],
                },
                {
                    "name": "token",
                    "label": "Token",
                    "type": "string",
                    "secret": True,
                    "required": True,
                    "default": "secret-default",
                    "targets": [
                        {"el": "tile", "slot": "source_header", "index": 0, "key": "Authorization"}
                    ],
                },
                {
                    "name": "headers",
                    "label": "Headers",
                    "type": "textarea",
                    "targets": [{"el": "tile", "slot": "source_header", "index": 0}],
                },
                {
                    "name": "future",
                    "label": "Future field",
                    "type": "custom",
                    "targets": [{"el": "tile", "key": "future"}],
                },
            ],
        }
    )
    page = Page(
        id="pantry",
        name="Pantry",
        layout_kind="canvas" if canvas else "grid",
        canvas=layout if canvas else None,
        custom_css=".keep {color:red}",
        gap=17,
        icon="ph-house",
        refresh_minutes=10,
    )
    app.config["PAGE_STORE"].save(page)
    return page


def _get(client: Any, headers: dict[str, str]) -> dict[str, Any]:
    result = client.get(URL, headers=headers)
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def test_permissions_and_redaction(app: Flask) -> None:
    _seed(app)
    c, h = app.test_client(), _headers(app, write=False)
    assert "dashboards:write" not in COMPANION_SCOPES
    assert "dashboard_settings" in c.get("/api/app/v1").get_json()["features"]
    assert c.get(URL).status_code == 401
    assert c.get(URL, headers=_headers(app, read=False)).status_code == 403
    detail = _get(c, h)
    assert not detail["can_edit"]
    assert detail["settings"]["theme"] == "dark"
    assert detail["settings"]["font"] == "old-font"
    assert "super-secret" not in json.dumps(detail)
    assert "secret-default" not in json.dumps(detail)
    inputs = {i["name"]: i for i in detail["inputs"]}
    assert inputs["token"]["configured"] and "value" not in inputs["token"]
    assert inputs["headers"]["secret"] and "value" not in inputs["headers"]
    assert inputs["enabled"]["value"] is False
    assert not inputs["future"]["editable"]
    assert (
        c.patch(
            URL, headers=h, json={"revision": detail["revision"], "settings": {"name": "New"}}
        ).status_code
        == 403
    )
    token = app.config["COMPANION_TOKENS"].lookup(h["Authorization"][7:])
    app.config["COMPANION_TOKENS"].set_optional_scope(
        token.token_id, "dashboards:write", granted=True
    )
    assert _get(c, h)["can_edit"]
    jsonschema.validate(_get(c, h), schema_for("DashboardSettingsResponse"))


@pytest.mark.parametrize("canvas", [True, False])
def test_partial_save_preserves_layout_and_changes_preview(app: Flask, canvas: bool) -> None:
    original = _seed(app, canvas=canvas)
    before = original.model_dump(mode="json")
    c, h = app.test_client(), _headers(app)
    detail = _get(c, h)
    if not canvas:
        assert detail["inputs"] == []
    _seed_device(app)
    cached_preview = object()
    app.config["PREVIEW_CACHE"] = {"pantry": cached_preview, "other": cached_preview}
    rejected = c.patch(URL, headers=h, json={"revision": "stale", "settings": {"name": "No"}})
    assert rejected.status_code == 409
    assert app.config["PREVIEW_CACHE"]["pantry"] is cached_preview
    result = c.patch(
        URL,
        headers=h,
        json={
            "revision": detail["revision"],
            "settings": {
                "name": "  Updated  ",
                "device_ids": ["kitchen"],
                "sleep_interval_s": 300,
                "refresh_minutes": 15,
            },
        },
    )
    assert result.status_code == 200, result.get_json()
    assert "pantry" not in app.config["PREVIEW_CACHE"]
    assert app.config["PREVIEW_CACHE"]["other"] is cached_preview
    updated = app.config["PAGE_STORE"].get("pantry")
    after = updated.model_dump(mode="json")
    for field in ("canvas", "cells", "custom_css", "gap", "icon", "theme", "style", "font"):
        assert after[field] == before[field]
    assert after["name"] == "Updated"
    assert after["updated_by"] == "companion"
    assert page_preview_token(original, (600, 400)) != page_preview_token(updated, (600, 400))
    assert result.get_json()["revision"] != detail["revision"]
    jsonschema.validate(result.get_json(), schema_for("DashboardSettingsResponse"))
    # Fresh store validates the persisted payload, including source header types.
    assert PageStore(app.config["PAGE_STORE"]._path).get("pantry") == updated


def test_typed_inputs_secret_preservation_replacement_clear_and_appearance(app: Flask) -> None:
    _seed(app)
    c, h = app.test_client(), _headers(app)
    detail = _get(c, h)
    result = c.patch(
        URL,
        headers=h,
        json={
            "revision": detail["revision"],
            "settings": {"theme": "light", "style": "standard", "font": None},
            "inputs": {
                "location": "Shanghai",
                "count": 7,
                "enabled": True,
                "choice": "b",
                "many": [],
            },
        },
    )
    assert result.status_code == 200, result.get_json()
    page = app.config["PAGE_STORE"].get("pantry")
    assert page.canvas.els[0].sources[0].headers["Authorization"] == "super-secret"
    assert page.theme == page.canvas.theme == "light"
    assert page.canvas.font == "" and page.font is None
    result = c.patch(
        URL,
        headers=h,
        json={"revision": result.get_json()["revision"], "inputs": {"token": "new-secret"}},
    )
    assert result.status_code == 200
    assert "new-secret" not in result.get_data(as_text=True)
    assert (
        app.config["PAGE_STORE"].get("pantry").canvas.els[0].sources[0].headers["Authorization"]
        == "new-secret"
    )
    result = c.patch(
        URL,
        headers=h,
        json={
            "revision": result.get_json()["revision"],
            "inputs": {"headers": '{"Authorization":"replacement", "X-New":"yes"}'},
        },
    )
    assert result.status_code == 200
    assert app.config["PAGE_STORE"].get("pantry").canvas.els[0].sources[0].headers == {
        "Authorization": "replacement",
        "X-New": "yes",
    }
    page = app.config["PAGE_STORE"].get("pantry").model_copy(deep=True)
    next(spec for spec in page.canvas.inputs if spec.name == "token").required = False
    app.config["PAGE_STORE"].save(page)
    result = c.patch(
        URL,
        headers=h,
        json={"revision": _get(c, h)["revision"], "inputs": {"headers": None, "count": None}},
    )
    assert result.status_code == 200
    page = app.config["PAGE_STORE"].get("pantry")
    assert page.canvas.els[0].sources[0].headers == {}
    assert page.canvas.els[0].options["count"] is None


@pytest.mark.parametrize(
    "patch",
    [
        {"settings": {"name": " "}},
        {"settings": {"device_ids": ["missing"]}},
        {"settings": {"refresh_minutes": True}},
        {"settings": {"refresh_minutes": -1}},
        {"settings": {"sleep_interval_s": 0}},
        {"settings": {"theme": "missing"}},
        {"settings": {"canvas": {}}},
        {"inputs": {"missing": "hi"}},
        {"inputs": {"future": "hi"}},
        {"inputs": {"count": "3"}},
        {"inputs": {"count": True}},
        {"inputs": {"enabled": 1}},
        {"inputs": {"choice": "bad"}},
        {"inputs": {"many": ["bad"]}},
        {"inputs": {"token": None}},
        {"inputs": {"headers": None}},
        {"inputs": {"headers": '{"X-New":"yes"}'}},
        {"inputs": {"location": ""}},
        {"inputs": {"headers": "not json"}},
    ],
)
def test_invalid_patch_is_atomic(app: Flask, patch: dict[str, Any]) -> None:
    page = _seed(app)
    c, h = app.test_client(), _headers(app)
    revision = _get(c, h)["revision"]
    result = c.patch(URL, headers=h, json={"revision": revision, **patch})
    assert result.status_code == 400, result.get_json()
    assert result.get_json()["error"]["fields"]
    assert app.config["PAGE_STORE"].get("pantry") == page
    jsonschema.validate(result.get_json(), schema_for("ErrorResponse"))


@pytest.mark.parametrize("actor", ["ui", "mcp"])
def test_web_or_mcp_change_causes_conflict(app: Flask, actor: str) -> None:
    page = _seed(app)
    c, h = app.test_client(), _headers(app)
    revision = _get(c, h)["revision"]
    # Change only an advanced field (not part of native settings).
    app.config["PAGE_STORE"].save(
        page.model_copy(update={"custom_css": "b{}", "updated_by": actor})
    )
    result = c.patch(URL, headers=h, json={"revision": revision, "settings": {"name": "Stale"}})
    assert result.status_code == 409
    assert result.get_json()["error"]["code"] == "revision_conflict"
    assert app.config["PAGE_STORE"].get("pantry").name == "Pantry"
    jsonschema.validate(result.get_json(), schema_for("ErrorResponse"))


def test_two_concurrent_writers_only_one_succeeds(tmp_path: Path) -> None:
    store = PageStore(tmp_path / "pages.json")
    page = Page(id="test", name="Test")
    store.save(page)
    start = Barrier(2)

    def update(name: str) -> str:
        start.wait()
        try:
            store.update_if_revision(
                page.id,
                page_revision(page),
                lambda current: current.model_copy(update={"name": name}),
            )
            return "saved"
        except PageRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(update, ("One", "Two")))
    assert sorted(outcomes) == ["conflict", "saved"]


def test_revision_transform_failure_and_response_cannot_mutate_saved_page(tmp_path: Path) -> None:
    store = PageStore(tmp_path / "pages.json")
    original = Page(id="a", name="A", device_ids=["screen"])
    store.save(original)
    revision = page_revision(original)

    def reject(page: Page) -> Page:
        page.device_ids.append("not-saved")
        page.name = "Not saved"
        raise ValueError("Rejected")

    with pytest.raises(ValueError, match="Rejected"):
        store.update_if_revision("a", revision, reject)
    assert store.get("a").name == "A"
    assert store.get("a").device_ids == ["screen"]
    result = store.update_if_revision("a", revision, lambda p: p.model_copy(update={"name": "B"}))
    result.name = "Mutated response"
    assert store.get("a").name == "B"


def test_secret_choices_and_indirect_aliases_are_not_returned(app: Flask) -> None:
    from app.state.panel_store import ConfigInput

    page = _seed(app)
    page.canvas.inputs += [
        ConfigInput(name="alias_far", label="Far alias", targets=[{"el": "tile", "key": "other"}]),
        ConfigInput(
            name="alias_bridge",
            label="Bridge",
            targets=[{"el": "tile", "key": "choice"}, {"el": "tile", "key": "other"}],
        ),
        ConfigInput(
            name="secret_choice",
            label="Secret choices",
            type="select",
            secret=True,
            choices=[{"value": "secret-choice", "label": "Private choice"}],
            targets=[{"el": "tile", "key": "choice"}],
        ),
    ]
    app.config["PAGE_STORE"].save(page)
    detail = _get(app.test_client(), _headers(app))
    assert "secret-choice" not in json.dumps(detail)
    assert "Private choice" not in json.dumps(detail)
    inputs = {spec["name"]: spec for spec in detail["inputs"]}
    assert inputs["secret_choice"]["choices"] == []
    assert not inputs["secret_choice"]["editable"]
    assert inputs["alias_far"]["secret"] and "value" not in inputs["alias_far"]


def test_missing_dashboard_malformed_request_and_no_implicit_push(app: Flask) -> None:
    from unittest.mock import Mock

    c, h = app.test_client(), _headers(app)
    assert c.get(URL, headers=h).status_code == 404
    assert c.patch(URL, headers=h, json={"revision": "old"}).status_code == 404
    _seed(app)
    for body in (None, [], {}, {"revision": "x", "layout": {}}, {"revision": 3}):
        assert c.patch(URL, headers=h, json=body).status_code == 400
    push = Mock()
    app.config["PUSH_MANAGER"] = push
    assert (
        c.patch(
            URL, headers=h, json={"revision": _get(c, h)["revision"], "settings": {"name": "New"}}
        ).status_code
        == 200
    )
    push.push.assert_not_called()


@pytest.mark.parametrize("secret_first", [False, True])
def test_duplicate_names_are_read_only_redacted_and_never_rewrite_declarations(
    app: Flask, secret_first: bool
) -> None:
    from app.state.panel_store import ConfigInput

    page = _seed(app)
    duplicate = ConfigInput(
        name="location",
        label="Secret alias",
        secret=True,
        default="private-default",
        targets=[{"el": "tile", "slot": "source_header", "index": 0, "key": "Authorization"}],
    )
    if secret_first:
        page.canvas.inputs.insert(0, duplicate)
    else:
        page.canvas.inputs.append(duplicate)
    app.config["PAGE_STORE"].save(page)
    original_canvas = page.canvas.model_dump(mode="json")
    c, h = app.test_client(), _headers(app)
    detail = _get(c, h)
    rows = [row for row in detail["inputs"] if row["name"] == "location"]
    assert len(rows) == 1
    assert rows[0]["secret"] and rows[0]["configured"]
    assert not rows[0]["editable"] and "value" not in rows[0]
    assert "super-secret" not in json.dumps(detail)
    assert "private-default" not in json.dumps(detail)
    rejected = c.patch(
        URL, headers=h, json={"revision": detail["revision"], "inputs": {"location": "New"}}
    )
    assert rejected.status_code == 400
    assert rejected.get_json()["error"]["fields"].get("inputs.location")
    saved = c.patch(
        URL, headers=h, json={"revision": detail["revision"], "settings": {"name": "New name"}}
    )
    assert saved.status_code == 200, saved.get_json()
    assert app.config["PAGE_STORE"].get("pantry").canvas.model_dump(mode="json") == original_canvas
    jsonschema.validate(saved.get_json(), schema_for("DashboardSettingsResponse"))


def test_duplicate_required_input_with_unset_alias_does_not_block_metadata_save(app: Flask) -> None:
    from app.state.panel_store import ConfigInput

    page = _seed(app)
    page.canvas.inputs.append(
        ConfigInput(
            name="location",
            label="Missing alias",
            required=True,
            targets=[{"el": "tile", "key": "missing"}],
        )
    )
    app.config["PAGE_STORE"].save(page)
    c, h = app.test_client(), _headers(app)
    detail = _get(c, h)
    row = next(row for row in detail["inputs"] if row["name"] == "location")
    assert not row["editable"] and "value" not in row
    saved = c.patch(
        URL, headers=h, json={"revision": detail["revision"], "settings": {"name": "New name"}}
    )
    assert saved.status_code == 200, saved.get_json()
    assert app.config["PAGE_STORE"].get("pantry").canvas == page.canvas
