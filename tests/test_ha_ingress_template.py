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
    skip those, otherwise every panel payload pings :8123, where HA
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


def test_static_js_prefixes_root_relative_requests() -> None:
    """A root-relative ``fetch("/...")`` in shipped JS leaves the app entirely
    under HA Ingress (the request lands on the HA host root, which answers with
    its own page, so the caller's ``resp.json()`` throws and the UI reports the
    server as unreachable). Every request URL must carry
    ``window.TESSERAE_URL_PREFIX``. Vendored libraries are exempt."""
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "static").rglob("*.js")):
        if "vendor" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for call in ("fetch('/", 'fetch("/', "fetch(`/", "EventSource('/", 'EventSource("/'):
                if call in line:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "root-relative request URLs break HA Ingress:\n" + "\n".join(offenders)


def test_broker_settings_card_hides_embedded_fields_under_ha(app: Flask) -> None:
    """Under HA the bundled Mosquitto add-on already owns 1883; offering a
    second broker on the same host is a footgun. The Settings → MQTT
    broker card drops every ``embedded_*`` field including the toggle."""
    app.config["HA_INGRESS_MODE"] = True
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    for name in (
        "embedded_enabled",
        "embedded_port",
        "embedded_bind",
        "embedded_username",
        "embedded_password",
    ):
        # Fields are rendered with id="broker-<name>"; their absence means
        # the field was stripped from the section's field list.
        assert f'id="broker-{name}"' not in body, name


def test_broker_settings_card_hides_ha_managed_connection_fields(app: Flask) -> None:
    """host/port/username/password come from the HA add-on Configuration
    tab under HA. The Settings card hides them so there's one source of
    truth, but keeps fields that don't have an HA option (keepalive,
    client_id)."""
    app.config["HA_INGRESS_MODE"] = True
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    for name in ("host", "port", "username", "password"):
        assert f'id="broker-{name}"' not in body, name
    assert 'id="broker-keepalive"' in body
    assert 'id="broker-client_id"' in body
    # Blurb explains where these moved.
    assert "Configuration tab" in body


def test_transport_rebuild_logs_when_ignoring_embedded_under_ha(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Belt-and-suspenders: a legacy install with ``embedded_enabled=true``
    saved must not bring up amqtt under HA, it would clash with the
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
