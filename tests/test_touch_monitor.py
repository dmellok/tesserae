"""Per-device touch monitor (issue #49).

The touch-capable reTerminal E1003 gets a diagnostic page under
``/devices/touch/<id>`` that plots recent touch events on a virtual panel
and overlays the last render's touch regions. Non-touch devices have no
monitor (404), and the device card only links to it for touch panels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_service
from app.main import REPO_ROOT, create_app
from app.touch_regions import save_regions


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


def _make_e1003(app: Flask, instance_id: str = "hall_e1003") -> None:
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=instance_id,
            kind_id="seeed_reterminal_e1003",
            name="Hall E1003",
        )


def _make_esp32(app: Flask, instance_id: str = "plain_esp32") -> None:
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=instance_id,
            kind_id="esp32_client",
        )


def _record_touch(app: Flask, *, target: str, status: str, extra: dict[str, Any]) -> None:
    app.config["EVENT_LOG"].record(
        type="touch", source="device", target=target, status=status, extra=extra
    )


def test_monitor_page_renders_for_touch_device(app: Flask) -> None:
    _make_e1003(app)
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/devices/touch/hall_e1003")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Touch monitor" in body
    assert "touch_monitor.js" in body
    # The stage carries the device id and the SSE stream URL scoped to touch.
    assert 'data-device-id="hall_e1003"' in body
    assert "type=touch" in body


def test_monitor_404s_for_non_touch_device(app: Flask) -> None:
    _make_esp32(app)
    client = app.test_client()
    _sign_in(client)
    assert client.get("/devices/touch/plain_esp32").status_code == 404


def test_monitor_404s_for_unknown_device(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/devices/touch/nope").status_code == 404


def test_data_json_returns_panel_dims_and_events(app: Flask) -> None:
    _make_e1003(app)
    _record_touch(
        app,
        target="hall_e1003",
        status="ha_dispatched",
        extra={
            "gesture": "tap",
            "touch": {"x0": 120, "y0": 80},
            "action_spec": {"action": "ha", "domain": "light", "service": "toggle"},
        },
    )
    # A touch on a different device must not leak into this device's view.
    _record_touch(app, target="other_dev", status="ha_dispatched", extra={"gesture": "tap"})
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/devices/touch/hall_e1003/data.json").get_json()
    assert payload["panel"]["w"] > 0 and payload["panel"]["h"] > 0
    events = payload["events"]
    assert len(events) == 1
    assert events[0]["gesture"] == "tap"
    assert events[0]["status"] == "ha_dispatched"
    assert events[0]["touch"] == {"x0": 120, "y0": 80}


def test_data_json_returns_last_render_regions(app: Flask) -> None:
    _make_e1003(app)
    push_mgr = app.config["PUSH_MANAGER"]
    renders_dir = app.config["RENDERS_DIR"]
    digest = "deadbeefcafe"
    save_regions(
        renders_dir,
        digest,
        [{"x": 10, "y": 20, "w": 100, "h": 60, "on_tap": {"action": "ha"}}],
    )
    # Point the device's latest render at that composition digest so the
    # endpoint knows which sidecar to load.
    push_mgr._latest_renders["hall_e1003"] = {"composition_digest": digest}
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/devices/touch/hall_e1003/data.json").get_json()
    regions = payload["regions"]
    assert len(regions) == 1
    assert regions[0]["x"] == 10 and regions[0]["w"] == 100


def test_data_json_404s_for_non_touch_device(app: Flask) -> None:
    _make_esp32(app)
    client = app.test_client()
    _sign_in(client)
    assert client.get("/devices/touch/plain_esp32/data.json").status_code == 404


def test_clear_deletes_touch_events_and_survives_refresh(app: Flask) -> None:
    """Clear must remove the rows, not just the client view: the monitor
    re-seeds from touch history on every load, so a view-only clear would
    reappear on refresh (the reported bug)."""
    _make_e1003(app)
    for _ in range(3):
        _record_touch(app, target="hall_e1003", status="ha_dispatched", extra={"gesture": "tap"})
    # A push event for the same device and a touch on another device must
    # both survive: Clear is scoped to this device's touch events only.
    app.config["EVENT_LOG"].record(type="push", source="page", target="hall_e1003", status="sent")
    _record_touch(app, target="other_dev", status="ha_dispatched", extra={"gesture": "tap"})

    client = app.test_client()
    _sign_in(client)
    assert len(client.get("/devices/touch/hall_e1003/data.json").get_json()["events"]) == 3

    resp = client.post("/devices/touch/hall_e1003/clear")
    assert resp.status_code == 200
    assert resp.get_json()["cleared"] == 3

    # Refresh: the reported bug was that they came back. They must not.
    assert client.get("/devices/touch/hall_e1003/data.json").get_json()["events"] == []
    # Scope check: the push row and the other device's touch are untouched.
    log = app.config["EVENT_LOG"]
    assert log.count(type="push") == 1
    assert len(log.list(type="touch", limit=50)) == 1


def test_clear_404s_for_non_touch_device(app: Flask) -> None:
    _make_esp32(app)
    client = app.test_client()
    _sign_in(client)
    assert client.post("/devices/touch/plain_esp32/clear").status_code == 404


def test_regions_preview_renders_selected_dashboard(app: Flask, monkeypatch: Any) -> None:
    """The monitor's dashboard picker previews a dashboard's regions at the
    device panel size without pushing it (agent feedback: let me see regions
    before I send)."""
    _make_e1003(app)
    # A saved canvas page to preview.
    ps = app.config["PAGE_STORE"]
    from app.state.page_store import Page

    ps.save(Page(id="board1", name="Board One", layout_kind="canvas"))
    # Stub the headless render/extract so no Chromium is needed.
    monkeypatch.setattr(
        "app.renderer.inspect_composed",
        lambda req, pool=None: [{"x": 10, "y": 20, "w": 100, "h": 60, "tap": "refresh"}],
    )
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/devices/touch/hall_e1003/regions.json?page=board1").get_json()
    assert payload["panel"]["w"] > 0
    assert payload["page_id"] == "board1"
    assert len(payload["regions"]) == 1
    assert payload["regions"][0]["tap"] == "refresh"


def test_regions_preview_no_page_returns_empty(app: Flask) -> None:
    _make_e1003(app)
    client = app.test_client()
    _sign_in(client)
    payload = client.get("/devices/touch/hall_e1003/regions.json").get_json()
    assert payload["regions"] == [] and payload["page_id"] == ""


def test_regions_preview_404s_for_unknown_page_or_device(app: Flask) -> None:
    _make_e1003(app)
    _make_esp32(app)
    client = app.test_client()
    _sign_in(client)
    assert client.get("/devices/touch/hall_e1003/regions.json?page=nope").status_code == 404
    assert client.get("/devices/touch/plain_esp32/regions.json?page=x").status_code == 404


def test_device_card_links_monitor_only_for_touch(app: Flask) -> None:
    _make_e1003(app)
    _make_esp32(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/devices").get_data(as_text=True)
    assert "/devices/touch/hall_e1003" in body
    assert "/devices/touch/plain_esp32" not in body
