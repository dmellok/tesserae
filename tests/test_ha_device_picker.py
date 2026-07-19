"""HA device picker for config forms (OpenDisplay support).

Pure-logic tests for parsing HA's template output, plus the settings
endpoint that lists an integration's devices (degrading to an empty list
with a message when HA isn't configured).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.ha_device_picker import (
    build_template,
    list_integration_devices,
    parse_devices,
    parse_resolution,
)
from app.main import REPO_ROOT, create_app


def test_parse_resolution_from_model() -> None:
    assert parse_resolution("296x128 E Ink") == (296, 128)
    assert parse_resolution("800 x 480") == (800, 480)
    assert parse_resolution("480×800 colour") == (480, 800)  # unicode times
    # A diagonal-only model carries no pixels.
    assert parse_resolution('2.9" E Ink') is None
    assert parse_resolution(None) is None
    assert parse_resolution("") is None


def test_parse_devices_adds_resolution_and_sorts() -> None:
    rendered = (
        '[{"device_id": "b2", "name": "Zebra", "model": "296x128 E Ink"},'
        ' {"device_id": "a1", "name": "apple", "model": "2.9\\" E Ink"}]'
    )
    out = parse_devices(rendered)
    # Sorted case-insensitively by name: apple before Zebra.
    assert [d["device_id"] for d in out] == ["a1", "b2"]
    # Zebra's model carries pixels -> w/h present; apple's diagonal -> absent.
    zebra = next(d for d in out if d["device_id"] == "b2")
    assert zebra["w"] == 296 and zebra["h"] == 128
    apple = next(d for d in out if d["device_id"] == "a1")
    assert "w" not in apple


def test_parse_devices_tolerates_garbage() -> None:
    assert parse_devices("not json") == []
    assert parse_devices('{"not": "a list"}') == []
    # Rows without a device_id are dropped.
    assert parse_devices('[{"name": "x"}]') == []


def test_build_template_validates_and_embeds_integration() -> None:
    assert "integration_entities('opendisplay')" in build_template("opendisplay")


def test_list_integration_devices_rejects_bad_domain() -> None:
    found, err = list_integration_devices(object(), "not a domain!")
    assert found == [] and err == "invalid integration"


def test_list_integration_devices_soft_without_ha() -> None:
    # A module with no render_template (ha_core absent / old) -> empty + msg.
    found, err = list_integration_devices(object(), "opendisplay")
    assert found == [] and err is not None


def test_list_integration_devices_parses_from_mod() -> None:
    class FakeMod:
        @staticmethod
        def render_template(_template: str, *, timeout: int = 10) -> str:
            return '[{"device_id": "abc", "name": "Kitchen", "model": "296x128 E Ink"}]'

    found, err = list_integration_devices(FakeMod(), "opendisplay")
    assert err is None
    assert found == [
        {"device_id": "abc", "name": "Kitchen", "model": "296x128 E Ink", "w": 296, "h": 128}
    ]


def test_list_integration_devices_surfaces_render_error() -> None:
    class BoomMod:
        @staticmethod
        def render_template(_template: str, *, timeout: int = 10) -> str:
            raise RuntimeError("Home Assistant is not configured")

    found, err = list_integration_devices(BoomMod(), "opendisplay")
    assert found == [] and err == "Home Assistant is not configured"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_endpoint_soft_when_ha_unconfigured(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices/ha-devices.json?integration=opendisplay")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["devices"] == []
    assert body["error"]  # a message so the UI can fall back to manual entry


def test_endpoint_returns_devices_when_ha_answers(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    core = app.config["PLUGIN_REGISTRY"].get("ha_core").server_module
    monkeypatch.setattr(
        core,
        "render_template",
        lambda _t, timeout=10: '[{"device_id":"abc","name":"Kitchen","model":"296x128 E Ink"}]',
        raising=False,
    )
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/devices/ha-devices.json?integration=opendisplay")
    body = resp.get_json()
    assert body["error"] is None
    assert body["devices"][0]["device_id"] == "abc"
    assert body["devices"][0]["w"] == 296 and body["devices"][0]["h"] == 128
