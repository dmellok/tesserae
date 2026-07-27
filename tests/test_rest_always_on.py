"""always_on delivery: a per-device Tesserae setting carried in the /status
config envelope (touch-v3), defaulting false so firmware always sees the field."""

from __future__ import annotations

import json
from pathlib import Path

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
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
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


def _status(client, token: str) -> dict:
    resp = client.post(
        "/api/v1/device/e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert resp.status_code == 200
    return resp.get_json()


def test_always_on_defaults_false(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    assert _status(client, token)["config"]["always_on"] is False


def test_always_on_setting_is_delivered(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    app.config["SETTINGS_STORE"].update_section("devices", {"e1003": {"always_on": True}})
    assert _status(client, token)["config"]["always_on"] is True
