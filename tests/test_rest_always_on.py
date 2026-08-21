"""always_on delivery: a per-device Tesserae setting carried in the /status
config envelope (touch-v3), defaulting false so firmware always sees the field.

Also covers the cadence that pairs with it: an always-on panel polls on
``awake_poll_s``, which is what ``next_poll_s`` has to be derived from,
because such a device is not on the deep-sleep grid at all."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.device_service import AWAKE_POLL_MAX_S, AWAKE_POLL_MIN_S, DEFAULT_AWAKE_POLL_S
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


def test_awake_poll_s_defaults_and_is_delivered(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    assert _status(client, token)["config"]["awake_poll_s"] == DEFAULT_AWAKE_POLL_S
    app.config["SETTINGS_STORE"].update_section("devices", {"e1003": {"awake_poll_s": 10}})
    assert _status(client, token)["config"]["awake_poll_s"] == 10


def test_next_poll_follows_awake_cadence_not_sleep_interval(app: Flask) -> None:
    """An always-on panel isn't on the sleep grid, so the sleep interval
    says nothing about when it comes back."""
    client = app.test_client()
    token = _register(app, client, "e1003")
    store = app.config["SETTINGS_STORE"]
    store.update_section("devices", {"e1003": {"sleep_interval_s": 3600}})
    assert _status(client, token)["next_poll_s"] == 3600
    store.update_section(
        "devices",
        {"e1003": {"sleep_interval_s": 3600, "always_on": True, "awake_poll_s": 10}},
    )
    assert _status(client, token)["next_poll_s"] == 10


def test_awake_poll_s_is_clamped_to_bounds(app: Flask) -> None:
    """The value can reach the server from a hand-edited settings file, and
    every caller turns it straight into a poll interval."""
    client = app.test_client()
    token = _register(app, client, "e1003")
    store = app.config["SETTINGS_STORE"]
    store.update_section("devices", {"e1003": {"always_on": True, "awake_poll_s": 1}})
    assert _status(client, token)["next_poll_s"] == AWAKE_POLL_MIN_S
    store.update_section("devices", {"e1003": {"always_on": True, "awake_poll_s": 99999}})
    assert _status(client, token)["next_poll_s"] == AWAKE_POLL_MAX_S


def test_awake_cadence_ignored_while_always_on_is_off(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003")
    app.config["SETTINGS_STORE"].update_section(
        "devices", {"e1003": {"sleep_interval_s": 300, "awake_poll_s": 5}}
    )
    assert _status(client, token)["next_poll_s"] == 300


def test_awake_cadence_respects_a_declared_refresh_floor(app: Flask) -> None:
    """The always-on path is the only one that can ask for under 30s, so
    it's the one that has to respect glass that can't be driven faster."""
    client = app.test_client()
    token = _register(app, client, "e1003")
    app.config["DEVICE_REGISTRY"].get("e1003").manifest["refresh_floor_s"] = 60
    app.config["SETTINGS_STORE"].update_section(
        "devices", {"e1003": {"always_on": True, "awake_poll_s": 5}}
    )
    assert _status(client, token)["next_poll_s"] == 60


def test_always_on_field_hidden_until_capability_advertised(app: Flask) -> None:
    """The switch follows the firmware's own statement about power, not the
    model: offering it on a battery panel invites a flat battery."""
    from app.settings.index_routes import _visible_config_fields

    client = app.test_client()
    _register(app, client, "e1003")
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    with app.test_request_context("/"):
        names = {f["name"] for f in _visible_config_fields(device)}
        assert "always_on" not in names
        assert "awake_poll_s" not in names
        app.config["DEVICE_STATUS"]["e1003"] = {"can_stay_awake": True}
        names = {f["name"] for f in _visible_config_fields(device)}
        assert {"always_on", "awake_poll_s"} <= names
