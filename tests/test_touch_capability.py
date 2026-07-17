"""Touch capability flag (issue #49): the reTerminal E1003 hardware entry
declares a touch digitizer, that flag propagates into the kind manifest,
and the device APIs (MCP ``/devices`` and the panels device list) surface
it so agents and the editor know which panels are actually tappable."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_service
from app.main import REPO_ROOT, create_app


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


def test_e1003_kind_manifest_declares_touch(app: Flask) -> None:
    reg = app.config["DEVICE_REGISTRY"]
    e1003 = reg.get("seeed_reterminal_e1003")
    assert e1003 is not None
    assert e1003.manifest.get("touch") is True
    # Button-driven models don't claim a digitizer.
    e1001 = reg.get("seeed_reterminal_e1001")
    assert e1001 is not None
    assert "touch" not in e1001.manifest or e1001.manifest.get("touch") is not True


def _make_e1003(app: Flask, instance_id: str = "hall_e1003") -> None:
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id=instance_id,
            kind_id="seeed_reterminal_e1003",
            name="Hall E1003",
        )


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_mcp_devices_exposes_touch(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    _make_e1003(app)
    rows = app.test_client().get("/api/mcp/devices").get_json()["devices"]
    e = next(r for r in rows if r["id"] == "hall_e1003")
    assert e.get("touch") is True


def test_panels_devices_exposes_touch(app: Flask) -> None:
    _make_e1003(app)
    client = app.test_client()
    _sign_in(client)
    rows = client.get("/pages/canvas/devices.json").get_json()["devices"]
    e = next(r for r in rows if r["id"] == "hall_e1003")
    assert e.get("touch") is True


def test_non_touch_device_omits_flag(app: Flask) -> None:
    """A plain esp32 client (no digitizer) carries no touch flag, so the
    agent treats it as display-only."""
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})
    with app.app_context():
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DATA_ROOT"],
            instance_id="plain_esp32",
            kind_id="esp32_client",
        )
    rows = app.test_client().get("/api/mcp/devices").get_json()["devices"]
    e = next(r for r in rows if r["id"] == "plain_esp32")
    assert "touch" not in e
