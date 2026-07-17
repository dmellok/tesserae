"""Grid-editor touch tests (issue #49): the per-cell ``touch_json`` form
field parses into the cell's on_tap / on_swipe / on_slide, survives a
compose, and the non-composer-gated picker endpoints answer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import PageStore


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _store(tmp_path: Path) -> PageStore:
    return PageStore(tmp_path / "data" / "core" / "pages.json")


def _new(client: Any) -> str:
    resp = client.post("/pages/new", data={"name": "Home", "layout": "1_cell"})
    return str(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


def _save_cell(client: Any, pid: str, cell_id: str, touch: dict[str, Any]) -> Any:
    return client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "clock_word",
            "x": "0",
            "y": "0",
            "w": "400",
            "h": "300",
            "touch_json": json.dumps(touch),
        },
    )


def test_cell_touch_json_parses_into_fields(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client)
    cell_id = _store(tmp_path).get(pid).cells[0].id
    _save_cell(
        client,
        pid,
        cell_id,
        {
            "on_tap": "page:forecast",
            "on_swipe": {"up": "rotate_next", "bogus": "x"},
            "on_slide": {
                "axis": "y",
                "action": {
                    "action": "ha",
                    "domain": "light",
                    "service": "turn_on",
                    "data": {"entity_id": "light.x", "brightness_pct": "{value}"},
                },
            },
        },
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.on_tap == "page:forecast"
    assert cell.on_swipe == {"up": "rotate_next"}  # unknown direction dropped
    assert cell.on_slide["axis"] == "y"
    assert cell.on_slide["action"]["domain"] == "light"


def test_empty_touch_json_clears_fields(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client)
    cell_id = _store(tmp_path).get(pid).cells[0].id
    _save_cell(client, pid, cell_id, {"on_tap": "refresh"})
    assert _store(tmp_path).get(pid).cells[0].on_tap == "refresh"
    _save_cell(client, pid, cell_id, {})  # empty object clears
    assert _store(tmp_path).get(pid).cells[0].on_tap is None


def test_malformed_touch_json_does_not_500(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client)
    cell_id = _store(tmp_path).get(pid).cells[0].id
    resp = client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "clock_word",
            "x": "0",
            "y": "0",
            "w": "400",
            "h": "300",
            "touch_json": "{not json",
        },
    )
    assert resp.status_code in (200, 302)
    assert _store(tmp_path).get(pid).cells[0].on_tap is None


def test_cell_touch_emits_data_attrs_on_compose(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client)
    cell_id = _store(tmp_path).get(pid).cells[0].id
    _save_cell(client, pid, cell_id, {"on_tap": "page:forecast"})
    body = client.get(f"/compose/{pid}").get_data(as_text=True)
    assert 'data-on-tap="page:forecast"' in body
    assert 'data-touch-origin="config"' in body


# -- picker endpoints (ungated) ------------------------------------------


def test_dashboards_json_lists_pages(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client)
    rows = client.get("/pages/dashboards.json").get_json()["pages"]
    ids = {r["id"] for r in rows}
    assert pid in ids


def test_grid_ha_actions_unconfigured(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/pages/ha-actions.json").get_json()
    assert body["configured"] is False
    assert body["services"] == [] and body["entities"] == []
