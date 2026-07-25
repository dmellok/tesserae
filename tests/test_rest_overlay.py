"""Overlay capability ingestion on /status (sticky, firmware-property
semantics). The schema-1 spec endpoint these tests once covered was
removed with protocol v2 (docs/protocol-v2-touch.md); value/patch
delivery coverage lives in tests/test_overlay_phase2.py and
tests/test_rest_frame_patch.py."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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


def _register_esp32(app: Flask, client, device_id: str) -> str:
    _sign_in(client)
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "esp32_client",
                "panel_w": 1872,
                "panel_h": 1404,
            }
        ),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_removed_spec_endpoint_404s(app: Flask) -> None:
    """Old firmware probing the retired schema-1 spec endpoint must get
    a plain 404, which its contract defines as feature-off."""
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")
    resp = client.get(f"/api/v1/device/e1003/frame/overlay/{'a' * 16}", headers=_auth(token))
    assert resp.status_code == 404


def test_overlay_capability_is_sticky_on_status(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")

    def post_status(body: dict[str, Any]):
        return client.post(
            "/api/v1/device/e1003/status", headers=_auth(token), data=json.dumps(body)
        )

    post_status({"overlay": {"schema": 1}, "battery_mv": 4000})
    assert app.config["DEVICE_STATUS"]["e1003"]["overlay"] == {"schema": 1}
    # A beat that omits it keeps the capability (firmware property,
    # unlike the removable-card deck_cache).
    post_status({"battery_mv": 3990})
    assert app.config["DEVICE_STATUS"]["e1003"]["overlay"] == {"schema": 1}


def test_proto_capability_is_sticky_and_persisted(app: Flask) -> None:
    client = app.test_client()
    token = _register_esp32(app, client, "e1003")

    def post_status(body: dict[str, Any]):
        return client.post(
            "/api/v1/device/e1003/status", headers=_auth(token), data=json.dumps(body)
        )

    post_status({"proto": {"v": 2}, "overlay": {"schema": 2}})
    assert app.config["DEVICE_STATUS"]["e1003"]["proto"] == {"v": 2}
    post_status({"battery_mv": 3990})  # omitting keeps it
    assert app.config["DEVICE_STATUS"]["e1003"]["proto"] == {"v": 2}
    assert app.config["DEVICE_FACTS"].get("e1003")["proto"] == {"v": 2}


def test_advertised_proto_validation() -> None:
    from app.overlay_sync import advertised_proto

    assert advertised_proto({"proto": {"v": 2}}) == {"v": 2}
    assert advertised_proto({"proto": {"v": 0}}) is None
    assert advertised_proto({"proto": {"v": True}}) is None
    assert advertised_proto({"proto": "2"}) is None
    assert advertised_proto(b'{"proto": {"v": 3}}') == {"v": 3}
    assert advertised_proto({}) is None
