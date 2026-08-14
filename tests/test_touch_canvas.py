"""Canvas-side touch tests (issue #49 phase 2): Element on_tap/on_swipe
emission, the hotspot kind, code-element actions maps, and the panels
pages.json endpoint. The headless extraction endpoint itself needs
Chromium and is exercised out-of-band; these cover everything up to the
markup the extractor reads."""

from __future__ import annotations

import json
import re
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


def _bind_device(app: Flask, cid: str, device_id: str, *, proto_v: int | None) -> None:
    """Create a device, bind it to the canvas page, and give it a status entry
    with (or without) the protocol-v2 handshake."""
    from app.device_service import create_instance

    result = create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id=device_id,
        kind_id="esp32_client",
    )
    assert result.ok
    store = app.config["PAGE_STORE"]
    page = store.get(cid)
    assert page is not None
    page.device_ids = [device_id]
    store.save(page)
    app.config["DEVICE_STATUS"][device_id] = {"proto": {"v": proto_v}} if proto_v else {}


def _canvas_with_button(client: Any) -> str:
    cid = _new_canvas(client)
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "b1",
                    "kind": "button",
                    "x": 40,
                    "y": 40,
                    "w": 200,
                    "h": 90,
                    "label": "Movie",
                    "on_tap": "page:scenes",
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return cid


def test_compose_paints_touch_primitives_when_no_device_draws_them(app: Flask) -> None:
    """The default: nothing on the other end owns those pixels, so the server
    paints the control rather than shipping an invisible hole (#228)."""
    client = app.test_client()
    _sign_in(client)
    cid = _canvas_with_button(client)
    body = client.get(f"/compose/{cid}?for_push=1").get_data(as_text=True)
    assert "window.__TESSERAE_DEVICE_DRAWS_TOUCH = false;" in body


def test_compose_reserves_touch_primitives_for_a_protocol_v2_panel(app: Flask) -> None:
    """A panel that draws its own controls still gets the blank reserve: the
    firmware contract is unchanged for the devices it was written for."""
    client = app.test_client()
    _sign_in(client)
    cid = _canvas_with_button(client)
    _bind_device(app, cid, "e1003", proto_v=2)
    body = client.get(f"/compose/{cid}?for_push=1&device_id=e1003").get_data(as_text=True)
    assert "window.__TESSERAE_DEVICE_DRAWS_TOUCH = true;" in body


def test_compose_paints_touch_primitives_for_a_display_only_panel(app: Flask) -> None:
    """Bound, but never advertised protocol v2: nothing will draw the control
    on-device, so the composition carries it."""
    client = app.test_client()
    _sign_in(client)
    cid = _canvas_with_button(client)
    _bind_device(app, cid, "plain", proto_v=None)
    body = client.get(f"/compose/{cid}?for_push=1&device_id=plain").get_data(as_text=True)
    assert "window.__TESSERAE_DEVICE_DRAWS_TOUCH = false;" in body


def test_preview_paints_touch_primitives_even_for_a_protocol_v2_panel(app: Flask) -> None:
    """A preview is a look at the design, not the bytes a panel receives, so it
    shows the control whatever the target does with it."""
    client = app.test_client()
    _sign_in(client)
    cid = _canvas_with_button(client)
    _bind_device(app, cid, "e1003", proto_v=2)
    body = client.get(f"/compose/{cid}?device_id=e1003").get_data(as_text=True)
    assert "window.__TESSERAE_DEVICE_DRAWS_TOUCH = false;" in body


def test_device_draws_touch_primitives_needs_a_real_v2_handshake(app: Flask) -> None:
    """The capability check itself: no device, an unknown device, a missing or
    junk ``proto`` all answer False, since painting a control nobody asked for
    is visible and a missing one is not."""
    from app.composer import device_draws_touch_primitives

    with app.test_request_context("/"):
        app.config["DEVICE_STATUS"].update(
            {
                "no_proto": {},
                "v1": {"proto": {"v": 1}},
                "junk": {"proto": {"v": True}},
                "v3": {"proto": {"v": 3}},
            }
        )
        assert device_draws_touch_primitives("") is False
        assert device_draws_touch_primitives("never_seen") is False
        assert device_draws_touch_primitives("no_proto") is False
        assert device_draws_touch_primitives("v1") is False
        assert device_draws_touch_primitives("junk") is False
        # Forward-compatible: a later protocol still owns its own pixels.
        assert device_draws_touch_primitives("v3") is True


def test_compose_carries_primitive_content_for_the_painter(app: Flask) -> None:
    """A primitive's caption, switch position and slider / stepper value reach
    the compose payload. The renderer draws the control from these now, so a
    prop the decoration shaping doesn't forward renders as an empty control
    (#228): the reserve rect never needed them, the painted one does."""
    client = app.test_client()
    _sign_in(client)
    cid = _new_canvas(client)
    resp = client.post(
        f"/pages/canvas/c/{cid}/save",
        json={
            "els": [
                {
                    "id": "sw1",
                    "kind": "switch",
                    "x": 0,
                    "y": 0,
                    "w": 220,
                    "h": 80,
                    "label": "Desk",
                    "state": "on",
                    "value_key": "ha:light.desk",
                },
                {
                    "id": "sl1",
                    "kind": "slider",
                    "x": 0,
                    "y": 100,
                    "w": 300,
                    "h": 64,
                    "axis": "x",
                    "value_min": 0,
                    "value_max": 100,
                    "value_now": 65,
                },
            ]
        },
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = client.get(f"/compose/{cid}").get_data(as_text=True)
    payloads = [json.loads(m) for m in re.findall(r"data-el='([^']+)'", body)]
    by_id = {p["id"]: p for p in payloads}
    assert by_id["sw1"]["label"] == "Desk" and by_id["sw1"]["state"] == "on"
    assert by_id["sl1"]["value_now"] == 65 and by_id["sl1"]["axis"] == "x"
    assert by_id["sl1"]["value_max"] == 100
