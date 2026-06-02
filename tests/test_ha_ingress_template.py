"""HA Ingress URL-prefix injection.

Pages rendered through the ingress middleware must surface SCRIPT_NAME to
the browser so root-relative paths in JS (EventSource, fetch) prepend it
and reach the add-on rather than the HA host root.
"""

from __future__ import annotations

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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_url_prefix_empty_without_ingress_header(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/events").get_data(as_text=True)
    assert 'window.TESSERAE_URL_PREFIX = "";' in body


def test_ingress_request_does_not_capture_ha_frontend_port(app: Flask) -> None:
    """Under HA Ingress the request comes through HA's frontend
    (homeassistant.local:8123). The before-request port capture must
    skip those — otherwise every panel payload pings :8123, where HA
    serves its own UI and 404s on /renders/."""
    client = app.test_client()
    _sign_in(client)
    client.get(
        "/events",
        base_url="http://homeassistant.local:8123",
        headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"},
    )
    assert app.config.get("DETECTED_HTTP_PORT") != 8123


def test_url_prefix_reflects_ingress_header(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get(
        "/events",
        headers={"X-Ingress-Path": "/api/hassio_ingress/abc123"},
    ).get_data(as_text=True)
    assert 'window.TESSERAE_URL_PREFIX = "/api/hassio_ingress/abc123";' in body


def test_broker_settings_card_hides_embedded_fields_under_ha(app: Flask) -> None:
    """Under HA the bundled Mosquitto add-on already owns 1883; offering a
    second broker on the same host is a footgun. The Settings → MQTT
    broker card drops every ``embedded_*`` field including the toggle."""
    app.config["HA_INGRESS_MODE"] = True
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings").get_data(as_text=True)
    for name in (
        "embedded_enabled",
        "embedded_port",
        "embedded_bind",
        "embedded_username",
        "embedded_password",
    ):
        assert f'name="broker[{name}]"' not in body, name


def test_transport_rebuild_logs_when_ignoring_embedded_under_ha(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Belt-and-suspenders: a legacy install with ``embedded_enabled=true``
    saved must not bring up amqtt under HA — it would clash with the
    host's Mosquitto. The runtime guard logs an info line and falls
    through to the external-broker code path."""
    a = create_app(testing=True, data_root=tmp_path, devices_dir=REPO_ROOT / "devices")
    a.config["HA_INGRESS_MODE"] = True
    a.config["SETTINGS_STORE"].patch_section(
        "broker",
        {"embedded_enabled": True, "host": "core-mosquitto"},
    )
    caplog.set_level("INFO", logger="app.transport_wiring")
    a.config["REBUILD_TRANSPORT"]()
    assert any("HA add-on detected" in r.message for r in caplog.records)
    assert a.config.get("EMBEDDED_BROKER") is None
