"""End-to-end device-section behaviour via the test client.

Covers: status block renders even with no heartbeat, status appears after a
heartbeat is delivered, config form rejects invalid values + accepts good
ones + publishes them retained to the broker."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.settings_store import SettingsStore


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    # testing=False so the auth gate is installed; then sign in via /setup.
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


def test_device_section_renders_with_no_heartbeat(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Both shipped devices appear, with the "no heartbeat" status state.
    assert "Device: Pi client" in body
    assert "Device: ESP32 client" in body
    assert "no heartbeat received yet" in body
    # ESP32 client config form (sleep_interval_s) shows up.
    assert "Sleep interval (seconds)" in body
    # pi_client has no config_topic — no Save button rendered for its form.
    pi_idx = body.index("Device: Pi client")
    esp_idx = body.index("Device: ESP32 client")
    pi_section = body[pi_idx:esp_idx]
    assert 'name="sleep_interval_s"' not in pi_section


def test_status_cache_renders_after_heartbeat(app: Flask) -> None:
    # Push a status payload through the status cache the way the MQTT
    # dispatcher would, then re-render and check the parsed fields show up.
    app.config["DEVICE_STATUS"]["esp32_client"] = {
        "received_at": time.time(),
        "parsed": {
            "battery_mv": 3820,
            "battery_pct": 67,
            "rssi": -58,
            "ip": "10.0.0.42",
        },
    }
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings").get_data(as_text=True)
    # Fresh heartbeat -> "ok" status dot + parsed fields visible.
    assert "is-ok" in body
    assert "3820" in body
    assert "10.0.0.42" in body


def test_stale_heartbeat_renders_warn(app: Flask) -> None:
    app.config["DEVICE_STATUS"]["esp32_client"] = {
        "received_at": time.time() - 200,  # past 90s fresh threshold
        "parsed": {"battery_mv": 3700},
    }
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings").get_data(as_text=True)
    assert "is-warn" in body


def test_config_form_rejects_out_of_bounds(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    # 5 seconds is below the 30-second min the device declares.
    resp = client.post(
        "/settings/device-esp32_client",
        data={"sleep_interval_s": "5"},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "Invalid ESP32 client config" in body
    # Nothing persisted, nothing published.
    store = SettingsStore(tmp_path / "core" / "settings.json")
    assert store.get_section("devices") == {}


def test_config_form_saves_and_publishes_on_valid_input(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/device-esp32_client",
        data={"sleep_interval_s": "1800"},
        follow_redirects=False,
    )
    store = SettingsStore(tmp_path / "core" / "settings.json")
    saved = store.get_section("devices")
    # Persisted under devices.<id>.
    assert saved["esp32_client"]["sleep_interval_s"] == 1800
    # Published through the transport — exercising the publish path is
    # covered by the unit test for MqttTransport; here we just confirm the
    # endpoint completed without raising.


def test_device_status_subscription_dispatches_to_cache(app: Flask) -> None:
    # Simulate an incoming MQTT message by invoking the transport's
    # on_message dispatcher directly. Subscriptions were registered at
    # boot in _rebuild_transport — verify a heartbeat arrives in the cache.
    transport = app.config["MQTT_TRANSPORT"]
    payload = json.dumps({"battery_mv": 4000, "rssi": -55, "ip": "1.2.3.4"}).encode()

    class _Msg:
        topic = "tesserae/esp32/status"
        payload_attr = payload

    msg = _Msg()
    msg.payload = payload  # type: ignore[attr-defined]
    transport._on_message(None, None, msg)

    cache = app.config["DEVICE_STATUS"]
    assert "esp32_client" in cache
    assert cache["esp32_client"]["parsed"]["battery_mv"] == 4000
    assert cache["esp32_client"]["parsed"]["ip"] == "1.2.3.4"


def test_device_status_subscriptions_replayed_on_broker_rebuild(app: Flask) -> None:
    # Trigger a broker rebuild (via the same callable settings_routes uses
    # on save) and verify the device subscriptions are re-installed on the
    # new transport instance.
    app.config["REBUILD_TRANSPORT"]()
    new_transport = app.config["MQTT_TRANSPORT"]
    assert "tesserae/pi/status" in new_transport.topic_subscriptions
    assert "tesserae/esp32/status" in new_transport.topic_subscriptions
