"""POST /interact: device-owned touch report -> action dispatch.

Verifies the touch-v3 dispatch fires the side-effect (HA / nav) but, unlike the
coordinate path, does NOT schedule the post-action reconcile/frame-patch: the
device draws its own feedback and data arrives via the values channel.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

import app.rest_api as rest_api
from app.main import REPO_ROOT, create_app
from app.state.page_store import Page
from app.state.panel_store import CanvasLayout, Element


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
    a.config["SETTINGS_STORE"].update_section("experiments", {"touch_v3": True})
    return a


def _register(app: Flask, client, device_id: str) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": device_id, "kind": "esp32_client", "panel_w": 1872, "panel_h": 1404}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _seed(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="Touch",
            layout_kind="canvas",
            canvas=CanvasLayout(
                els=[
                    Element(
                        id="sw", kind="switch", x=10, y=10, w=160, h=80, value_key="ha:light.desk"
                    ),
                    Element(id="btn", kind="button", x=200, y=10, w=160, h=80, on_tap="refresh"),
                ]
            ),
        )
    )
    monkeypatch.setattr(
        app.config["PUSH_MANAGER"], "latest_render_for", lambda _d: {"digest": "a" * 16}
    )
    monkeypatch.setattr(
        rest_api,
        "_frame_info_for_digest",
        lambda _dev, _dig: {"page_id": "p1", "composition_digest": "c"},
    )


def _post(client, token: str, body: dict[str, Any]):
    return client.post("/api/v1/device/e1003/interact", headers=_auth(token), data=json.dumps(body))


def test_switch_fires_ha_toggle_without_reconcile(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed(app, monkeypatch)
    svc = app.config["BUTTON_SERVICE"]
    ha_calls: list[tuple[str, str, dict[str, Any]]] = []
    reconciles: list[str] = []
    monkeypatch.setattr(svc, "_call_ha", lambda d, s, data: ha_calls.append((d, s, data)))
    monkeypatch.setattr(svc, "_schedule_ha_reconcile", lambda did: reconciles.append(did))

    resp = _post(client, token, {"primitive_id": "sw", "interaction": "tap", "event_id": 1})
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "ha_dispatched"
    assert ha_calls == [("light", "toggle", {"entity_id": "light.desk"})]
    assert reconciles == []  # touch-v3 skips the post-action reconcile/patch


def test_button_action_dispatched(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed(app, monkeypatch)
    resp = _post(client, token, {"primitive_id": "btn", "interaction": "tap", "event_id": 2})
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] in ("fetched", "dispatched", "noop")


def test_unknown_primitive_is_no_target(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed(app, monkeypatch)
    resp = _post(client, token, {"primitive_id": "nope", "interaction": "tap"})
    assert resp.status_code == 200
    assert resp.get_json()["outcome"] == "no_target"


def test_event_id_dedup(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    _seed(app, monkeypatch)
    svc = app.config["BUTTON_SERVICE"]
    monkeypatch.setattr(svc, "_call_ha", lambda d, s, data: None)
    monkeypatch.setattr(svc, "_schedule_ha_reconcile", lambda did: None)
    assert _post(client, token, {"primitive_id": "sw", "event_id": 7}).get_json()["outcome"] == (
        "ha_dispatched"
    )
    assert _post(client, token, {"primitive_id": "sw", "event_id": 7}).get_json()["outcome"] == (
        "deduped"
    )


def test_interact_requires_auth(app: Flask) -> None:
    client = app.test_client()
    _register(app, client, "e1003")
    resp = client.post("/api/v1/device/e1003/interact", data=json.dumps({"primitive_id": "sw"}))
    assert resp.status_code in (401, 403)
