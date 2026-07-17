"""Canvas-side touch tests (issue #49 phase 2): Element on_tap/on_swipe
emission, the hotspot kind, code-element actions maps, and the panels
pages.json endpoint. The headless extraction endpoint itself needs
Chromium and is exercised out-of-band; these cover everything up to the
markup the extractor reads."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _new_canvas(client: Any) -> str:
    return str(client.get("/pages/canvas/").location.rsplit("/", 1)[1])


def test_canvas_element_on_tap_emits_attrs_with_config_origin(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "e1",
                    "kind": "rect",
                    "x": 0,
                    "y": 0,
                    "w": 200,
                    "h": 100,
                    "on_tap": "page:forecast",
                    "on_swipe": {"up": "rotate_next"},
                },
                {"id": "e2", "kind": "rect", "x": 0, "y": 120, "w": 200, "h": 100},
            ]
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'data-on-tap="page:forecast"' in body
    assert 'data-touch-origin="config"' in body
    assert "data-on-swipe=" in body and "rotate_next" in body
    # The plain rect carries no touch attributes.
    assert body.count("data-on-tap=") == 1


def test_canvas_widget_element_on_tap_emits_on_cell(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "w1",
                    "widget": "weather_now",
                    "x": 0,
                    "y": 0,
                    "w": 300,
                    "h": 200,
                    "on_tap": "refresh",
                }
            ]
        },
    )
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'data-plugin="weather_now"' in body
    assert 'data-on-tap="refresh"' in body
    assert 'data-touch-origin="config"' in body


def test_hotspot_kind_round_trips_and_renders_empty(app: Flask) -> None:
    """A hotspot persists through save/doc and emits only its touch
    attributes; PanelsDecorate paints nothing for it (verified by the
    compose page carrying the deco payload with kind=hotspot)."""
    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "h1",
                    "kind": "hotspot",
                    "x": 40,
                    "y": 40,
                    "w": 160,
                    "h": 120,
                    "on_tap": "rotate_next",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    doc = client.get(f"/pages/canvas/c/{cid}/doc.json").get_json()
    el = doc["els"][0]
    assert el["kind"] == "hotspot"
    assert el["on_tap"] == "rotate_next"
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert 'data-on-tap="rotate_next"' in body
    assert '"kind": "hotspot"' in body


def test_code_element_actions_map_emits_touch_actions_attr(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "c1",
                    "kind": "code",
                    "x": 0,
                    "y": 0,
                    "w": 400,
                    "h": 300,
                    "html": '<div data-on-tap="@go">Tap me</div>',
                    "actions": {"go": "page:morning"},
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert "data-touch-actions=" in body
    assert "page:morning" in body


def test_pages_json_lists_grid_and_canvas_dashboards(app: Flask) -> None:
    from app.state.page_store import Page

    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    app.config["PAGE_STORE"].save(Page(id="grid1", name="Morning Grid", layout_kind="grid"))
    rows = client.get("/pages/canvas/pages.json").get_json()["pages"]
    ids = {r["id"]: r for r in rows}
    assert "grid1" in ids and ids["grid1"]["kind"] == "grid"
    assert cid in ids and ids[cid]["kind"] == "canvas"


def test_canvas_on_slide_emits_attr(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "s1",
                    "kind": "hotspot",
                    "x": 0,
                    "y": 0,
                    "w": 40,
                    "h": 300,
                    "on_slide": {
                        "axis": "y",
                        "action": {
                            "action": "ha",
                            "domain": "light",
                            "service": "turn_on",
                            "data": {"entity_id": "light.x", "brightness_pct": "{value}"},
                        },
                    },
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    assert "data-on-slide=" in body
    assert "brightness_pct" in body
    assert 'data-touch-origin="config"' in body


def test_ha_actions_json_unconfigured(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/pages/canvas/ha-actions.json")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured"] is False
    assert body["services"] == [] and body["entities"] == []
