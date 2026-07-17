"""REST touch-dispatch tests (issue #49): the ``touch_*`` query params
on ``GET /frame`` and the standalone ``POST /tap`` endpoint.

Same full-app fixture shape as ``test_rest_api``. Dispatched cases use a
``webhook:`` action so no real page push (and no Playwright render) is
triggered; the webhook itself fires into a closed local port from a
daemon thread and is logged-and-dropped by design."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.touch_regions import save_regions


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _register(app: Flask, client, device_id: str = "hall_panel") -> str:
    """Register a device instance and return its bearer token."""
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.1.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["device_token"]


WEBHOOK_REGION = {
    "x": 0,
    "y": 0,
    "w": 800,
    "h": 600,
    "depth": 1,
    "order": 0,
    "tap": "webhook:http://127.0.0.1:9/tesserae-test",
    "swipe": None,
    "slide": None,
    # Config origin: the provenance gate only honours side-effecting
    # actions (webhook/ha) from editor/MCP-authored config.
    "origin": "config",
    "dangling": [],
}


def _seed_frame(app: Flask, device_id: str, *, regions: list[dict] | None = None) -> None:
    """Fake a rendered frame + its touch region sidecar."""
    push_mgr = app.config["PUSH_MANAGER"]
    push_mgr._latest_renders[device_id] = {
        "digest": "art123",
        "ext": "bin",
        "filename": "art123.bin",
        "composition_digest": "comp456",
    }
    if regions is not None:
        save_regions(push_mgr._renders_dir, "comp456", regions)


# -- GET /frame touch params ---------------------------------------------


def test_frame_touch_wake_dispatches_and_serves_frame(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame"
        "?touch_x0=100&touch_y0=100&touch_digest=art123&touch_event_id=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "art123"
    rows = list(app.config["EVENT_LOG"].list(type="push", source="touch", limit=10))
    assert len(rows) == 1
    assert rows[0].status == "webhook_dispatched"
    assert rows[0].extra["gesture"] == "tap"


def test_frame_touch_wake_accepts_quoted_etag_digest(app: Flask) -> None:
    """Firmware that echoes the ETag header verbatim (with quotes) must
    not be treated as stale."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        '/api/v1/device/hall_panel/frame?touch_x0=5&touch_y0=5&touch_digest="art123"',
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = list(app.config["EVENT_LOG"].list(type="push", source="touch", limit=10))
    assert rows and rows[0].status == "webhook_dispatched"


def test_frame_stale_touch_degrades_to_plain_poll(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame?touch_x0=100&touch_y0=100&touch_digest=oldframe",
        headers={"Authorization": f"Bearer {token}"},
    )
    # The wake still serves the current frame.
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "art123"
    rows = list(app.config["EVENT_LOG"].list(type="push", source="touch", limit=10))
    assert rows and rows[0].status == "stale"


def test_frame_touch_params_ignored_when_button_present(app: Flask) -> None:
    """A wake carrying both a button and touch params dispatches the
    button only (firmware shouldn't send both; button wins)."""
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = client.get(
        "/api/v1/device/hall_panel/frame"
        "?button=refresh&touch_x0=100&touch_y0=100&touch_digest=art123",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    touch_rows = list(app.config["EVENT_LOG"].list(type="push", source="touch", limit=10))
    assert touch_rows == []


# -- POST /tap -----------------------------------------------------------


def _tap(client, token: str, body: dict, device_id: str = "hall_panel"):
    return client.post(
        f"/api/v1/device/{device_id}/tap",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(body),
    )


def test_tap_requires_auth(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register(app, client)
    resp = client.post(
        "/api/v1/device/hall_panel/tap",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"x0": 1, "y0": 1, "digest": "art123"}),
    )
    assert resp.status_code == 401


def test_tap_validates_body(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel")

    assert _tap(app.test_client(), token, {"y0": 1, "digest": "d"}).status_code == 400
    assert _tap(app.test_client(), token, {"x0": 1, "y0": 1}).status_code == 400
    assert _tap(app.test_client(), token, {"x0": -5, "y0": 1, "digest": "d"}).status_code == 400


def test_tap_dispatches_webhook_action(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[WEBHOOK_REGION])

    resp = _tap(client, token, {"x": 50, "y": 60, "digest": "art123"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["outcome"] == "webhook_dispatched"
    assert body["gesture"] == "tap"
    assert body["action_spec"] == "webhook:http://127.0.0.1:9/tesserae-test"


def test_tap_swipe_stroke_classifies_server_side(app: Flask) -> None:
    region = dict(WEBHOOK_REGION, tap=None, swipe={"left": "webhook:http://127.0.0.1:9/left"})
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[region])

    resp = _tap(client, token, {"x0": 500, "y0": 100, "x1": 100, "y1": 120, "digest": "art123"})
    body = resp.get_json()
    assert body["gesture"] == "swipe_left"
    assert body["outcome"] == "webhook_dispatched"


def test_tap_stale_and_no_target_outcomes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)
    _seed_frame(app, "hall_panel", regions=[dict(WEBHOOK_REGION, w=10, h=10)])

    stale = _tap(client, token, {"x0": 5, "y0": 5, "digest": "not-current"})
    assert stale.status_code == 200
    assert stale.get_json()["outcome"] == "stale"

    miss = _tap(client, token, {"x0": 500, "y0": 500, "digest": "art123"})
    assert miss.status_code == 200
    assert miss.get_json()["outcome"] == "no_target"


def test_tap_no_frame_outcome(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    token = _register(app, client)

    resp = _tap(client, token, {"x0": 5, "y0": 5, "digest": "whatever"})
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "no_frame"
