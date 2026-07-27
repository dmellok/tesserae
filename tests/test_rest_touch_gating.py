"""touch_v3 experiment gate: the device-facing touch endpoints are inert until
the experiment is enabled (opt-in while firmware support lands)."""

from __future__ import annotations

import json
from pathlib import Path

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
    a.config["TESTING"] = True  # touch_v3 left at its default (off)
    return a


def _register(app: Flask, client) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {"device_id": "e1003", "kind": "esp32_client", "panel_w": 1872, "panel_h": 1404}
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _seed(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    app.config["PAGE_STORE"].save(
        Page(
            id="p1",
            name="T",
            layout_kind="canvas",
            canvas=CanvasLayout(
                els=[
                    Element(
                        id="sw",
                        kind="switch",
                        x=10,
                        y=10,
                        w=160,
                        h=80,
                        label="Desk",
                        value_key="ha:light.desk",
                    ),
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


def test_endpoints_inert_when_disabled(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, monkeypatch)
    # frame/spec: valid but empty, so a v3 device latches off gracefully.
    spec = client.get("/api/v1/device/e1003/frame/spec", headers=_h(token)).get_json()
    assert spec["primitives"] == []
    # interact: nothing to dispatch.
    r = client.post(
        "/api/v1/device/e1003/interact", headers=_h(token), data=json.dumps({"primitive_id": "sw"})
    )
    assert r.get_json()["outcome"] == "no_target"
    # atlas: 404.
    assert (
        client.get(f"/api/v1/device/e1003/atlas/{'a' * 16}", headers=_h(token)).status_code == 404
    )


def test_frame_spec_serves_primitives_when_enabled(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    token = _register(app, client)
    _seed(app, monkeypatch)
    app.config["SETTINGS_STORE"].update_section("experiments", {"touch_v3": True})
    spec = client.get("/api/v1/device/e1003/frame/spec", headers=_h(token)).get_json()
    assert [p["id"] for p in spec["primitives"]] == ["sw"]
