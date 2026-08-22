"""Adding a REST device without pairing (discussion #240).

Pairing needs the device to run the /register handshake. A digital sign, a
browser tab, or a photo frame never will: they only fetch an image URL. They
still have to exist as a device, so a page can target them and
``/preview/<id>.png`` can follow their lineup.

Before this the REST branch of Add device offered only "Issue pairing code",
and the manual form lived in the MQTT branch, so the documented path (add a
REST device, set a custom panel size) did not exist.
"""

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


def _signed_in(app: Flask):
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


def _add(client, **over):
    data = {
        "id": "lobby_sign",
        "kind": "esp32_client",
        "name": "Lobby sign",
        "panel_preset": "custom",
        "panel_w": "1920",
        "panel_h": "1080",
        "panel_orientation": "landscape",
    }
    data.update(over)
    return client.post("/settings/devices/add", data=data, follow_redirects=True)


def test_a_rest_device_can_be_added_without_pairing(app: Flask) -> None:
    client = _signed_in(app)
    _add(client, transport="rest")

    device = app.config["DEVICE_REGISTRY"].get("lobby_sign")
    assert device is not None
    assert device.transport == "rest"
    assert device.manifest.get("access_token")
    assert device.manifest["panel"]["w"] == 1920
    assert device.manifest["panel"]["h"] == 1080


def test_the_rest_transport_and_token_are_persisted(app: Flask) -> None:
    """A restart must not drop the device back to MQTT, or the sign's URL
    stops following its lineup."""
    client = _signed_in(app)
    _add(client, transport="rest")

    device = app.config["DEVICE_REGISTRY"].get("lobby_sign")
    raw = json.loads(device.path.read_text(encoding="utf-8"))
    assert raw["transport"] == "rest"
    assert raw["access_token"] == device.manifest["access_token"]


def test_the_minted_token_authenticates_the_device_api(app: Flask) -> None:
    client = _signed_in(app)
    _add(client, transport="rest")
    token = app.config["DEVICE_REGISTRY"].get("lobby_sign").manifest["access_token"]

    resp = client.post(
        "/api/v1/device/lobby_sign/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data="{}",
    )
    assert resp.status_code == 200


def test_an_add_without_a_transport_field_is_unchanged(app: Flask) -> None:
    """The MQTT branch posts no transport; absence still reads as MQTT."""
    client = _signed_in(app)
    _add(client, id="kitchen_panel")

    device = app.config["DEVICE_REGISTRY"].get("kitchen_panel")
    assert device.transport == "mqtt"
    assert "transport" not in device.manifest


def test_the_devices_page_offers_the_manual_rest_form(app: Flask) -> None:
    """Both add forms are in the DOM at once; the REST one carries the
    hidden transport field that selects this path."""
    client = _signed_in(app)
    html = client.get("/settings/devices").get_data(as_text=True)

    assert "Add without pairing" in html
    assert 'name="transport" value="rest"' in html
    # Fields are resolved by name per form, so the duplicate ids that would
    # break the shared preset script must not appear.
    assert html.count('id="add-device-id"') <= 1
    assert html.count('id="rest-add-device-id"') == 1
