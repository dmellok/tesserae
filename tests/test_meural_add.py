"""Meural registration through the shared Add device flow."""

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    app = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["SETTINGS_STORE"].patch_section("auth", {"disabled": True})
    return app


def test_add_device_card_offers_meural(app: Flask) -> None:
    html = app.test_client().get("/settings/devices").get_data(as_text=True)
    assert 'data-segmented-btn="meural"' in html
    assert 'data-transport-branch="meural"' in html
    assert 'name="kind" value="meural"' in html


@pytest.mark.parametrize("transport", ["", "rest"])
def test_add_meural_opens_its_page_without_pairing(app: Flask, transport: str) -> None:
    client = app.test_client()
    response = client.post(
        "/settings/devices/add",
        data={
            "kind": "meural",
            "delivery_method": "local",
            "id": "Meural Test",
            "name": "Example frame",
            "transport": transport,
            "host": "192.0.2.10",
        },
    )
    assert response.status_code == 302
    assert response.location == "/settings/devices/meural_test"
    device = app.config["DEVICE_REGISTRY"].get("meural_test")
    assert (
        app.config["SETTINGS_STORE"].get_section("devices")["meural_test"]["host"] == "192.0.2.10"
    )
    assert device is not None
    assert device.name == "Example frame"
    assert device.kind_of == "meural"
    assert device.transport == "push"
    assert device.panel["w"] == 1080
    assert device.panel["h"] == 1920
    assert device.panel["orientation"] == "portrait"
    assert device.status_topic is None
    assert device.config_topic is None
    assert "access_token" not in device.manifest
    with client.session_transaction() as session:
        assert "_trmnl_token_reveal" not in session
    page = client.get(response.location)
    assert page.status_code == 200
    assert b"Example frame" in page.data


def test_add_meural_rejects_invalid_id_and_duplicate(app: Flask) -> None:
    client = app.test_client()
    invalid = client.post(
        "/settings/devices/add",
        data={"kind": "meural", "delivery_method": "local", "id": "../bad"},
        follow_redirects=True,
    )
    assert b"Device id must be" in invalid.data
    assert app.config["DEVICE_REGISTRY"].get("../bad") is None
    client.post(
        "/settings/devices/add",
        data={"kind": "meural", "delivery_method": "local", "id": "meural_test", "name": "First"},
    )
    duplicate = client.post(
        "/settings/devices/add",
        data={"kind": "meural", "delivery_method": "local", "id": "meural_test", "name": "Second"},
        follow_redirects=True,
    )
    assert b"already in use" in duplicate.data
    assert app.config["DEVICE_REGISTRY"].get("meural_test").name == "First"
