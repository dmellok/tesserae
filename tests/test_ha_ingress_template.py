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


def test_catalog_data_urls_already_carry_the_ingress_prefix(app: Flask) -> None:
    """``url_for`` output includes SCRIPT_NAME, which the ingress middleware
    sets from ``X-Ingress-Path``. The catalog hands those URLs to its client in
    data-* attributes, so they arrive prefixed and must NOT be prefixed again:
    doing so requested /api/hassio_ingress/<token>/api/hassio_ingress/<token>/…
    and Home Assistant answered 404, which the page reported as the catalog
    being unreachable."""
    from unittest.mock import MagicMock

    from app.marketplace import CatalogEntry, Marketplace

    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].patch_section("app", {"online_features": True})
    app.config["SETTINGS_STORE"].patch_section("experiments", {"templates": True})
    entry = CatalogEntry(
        id="sample",
        name="Sample",
        description="",
        icon=None,
        author_name="a",
        author_github=None,
        tags=[],
        kind="widget",
        tesserae_compat="0.x",
        official=False,
        screenshot_sizes=[],
        extra_screenshot_count=0,
        folders=None,
        release_version="1.0.0",
        release_tarball_url="https://x.invalid/s.tgz",
        release_sha256="0" * 64,
        source=None,
    )
    mkt = MagicMock(spec=Marketplace)
    mkt.index_url.return_value = "https://catalog.invalid/widgets.json"
    mkt.fetch_index.return_value = [entry]
    mkt.cached_index.return_value = [entry]
    mkt.installed.return_value = {}
    mkt.screenshots_base.return_value = ""
    mkt.plugins_dir.return_value = None
    app.config["MARKETPLACE"] = mkt

    prefix = "/api/hassio_ingress/abc123"
    body = client.get("/plugins/browse", headers={"X-Ingress-Path": prefix}).get_data(as_text=True)
    for attr in ("data-templates-url", "data-install-url", "data-uninstall-url"):
        marker = attr + '="'
        start = body.index(marker) + len(marker)
        url = body[start : body.index('"', start)]
        assert url.startswith(prefix + "/"), f"{attr} = {url}"


def test_catalog_client_prefixes_urls_idempotently() -> None:
    """The catalog's request URLs come from two places: literals in the script,
    which need the prefix, and data-* attributes, which already have it. It has
    had this wrong in both directions, so every request goes through the
    idempotent ``withPrefix`` helper rather than bare concatenation."""
    source = (REPO_ROOT / "static" / "catalog_browse.js").read_text(encoding="utf-8")
    assert "function withPrefix(" in source
    offenders = []
    for lineno, line in enumerate(source.splitlines(), 1):
        code = line.strip()
        if code.startswith("//") or code.startswith("*"):
            continue
        if "fetch(" in code and "withPrefix(" not in code:
            offenders.append(f"catalog_browse.js:{lineno}: {code}")
    assert not offenders, "catalog request not routed through withPrefix:\n" + "\n".join(offenders)
