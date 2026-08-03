"""Settings -> Cloud relay routes: register, add-panel, revoke.

The relay's network calls are stubbed (register_install / RelayClient), so these
exercise the Flask wiring, flashes, and settings persistence without a relay."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.app_factory import create_app
from app.main import REPO_ROOT
from app.ota._codec import b64u_encode
from app.relay_config import RELAY_SECTION


@pytest.fixture
def app_with_gate(tmp_path: Path) -> Flask:
    app = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    return app


def _setup(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_relay_page_renders_unlinked(app_with_gate: Flask) -> None:
    client = app_with_gate.test_client()
    _setup(client)
    resp = client.get("/settings/relay")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Cloud relay" in body
    assert "Register this install" in body


def test_register_persists_identity(app_with_gate: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.relay_pairing.register_install",
        lambda base, pub, label="", allow_local=False: ("inst_9", "ptok_9"),
    )
    client = app_with_gate.test_client()
    _setup(client)
    resp = client.post(
        "/settings/relay/register",
        data={"base_url": "https://relay.example", "allow_local": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    cfg = app_with_gate.config["SETTINGS_STORE"].get_section(RELAY_SECTION)
    assert cfg["install_id"] == "inst_9"
    assert cfg["publisher_token_secret"] == "ptok_9"
    assert cfg["install_privkey_secret"]  # keypair persisted
    assert cfg["enabled"] is True


def test_add_panel_mints_code_and_records_slot(
    app_with_gate: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Link the install first (stub the network).
    monkeypatch.setattr(
        "app.relay_pairing.register_install",
        lambda base, pub, label="", allow_local=False: ("inst_9", "ptok_9"),
    )
    client = app_with_gate.test_client()
    _setup(client)
    client.post("/settings/relay/register", data={"base_url": "https://relay.example"})

    # Stub the code mint at the client layer.
    minted: dict[str, Any] = {}

    class _FakeClient:
        def mint_pair_code(self, *, ttl_seconds: int | None = None) -> tuple[str, str]:
            minted["ttl_seconds"] = ttl_seconds
            return "ABC234", "2099-01-01T00:00:00Z"

    monkeypatch.setattr("app.relay_pairing.build_client", lambda _cfg: _FakeClient())

    resp = client.post(
        "/settings/relay/add-panel",
        data={
            "device_id": "parents_panel",
            "kind": "esp32_client",
            "panel_w": "800",
            "panel_h": "480",
            "ttl_seconds": "7200",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "ABC234" in body  # code shown on the page
    assert "2 hours" in body  # chosen lifetime echoed in the expiry copy
    assert minted["ttl_seconds"] == 7200
    cfg = app_with_gate.config["SETTINGS_STORE"].get_section(RELAY_SECTION)
    slot = cfg["pending_pairings"]["ABC234"]
    assert slot["device_id"] == "parents_panel"
    assert slot["kind"] == "esp32_client"
    assert slot["panel"] == {"w": 800, "h": 480}


def test_revoke_removes_device(app_with_gate: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    # Create a relay device directly, then revoke via the route.
    from app import device_service

    app = app_with_gate
    devices = app.config["DEVICE_REGISTRY"]
    renderers = app.config["RENDERER_REGISTRY"]
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="parents_panel",
        kind_id="esp32_client",
        transport="relay",
        relay_frame_key=b64u_encode(b"\x33" * 32),
    )
    assert devices.get("parents_panel") is not None

    # No relay link → build_client returns None; revoke still removes locally.
    client = app.test_client()
    _setup(client)
    resp = client.post(
        "/settings/relay/revoke", data={"device_id": "parents_panel"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert devices.get("parents_panel") is None


def test_devices_delete_revokes_relay_pairing(
    app_with_gate: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Deleting a relay panel from Settings → Devices (not the Cloud relay
    # page) must still revoke the relay pairing, or the mailbox + device
    # token linger on the relay forever.
    from app import device_service

    app = app_with_gate
    devices = app.config["DEVICE_REGISTRY"]
    device_service.create_instance(
        devices=devices,
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="parents_panel",
        kind_id="esp32_client",
        transport="relay",
        relay_frame_key=b64u_encode(b"\x33" * 32),
    )

    revoked: list[str] = []

    class _FakeClient:
        def revoke_device(self, device_id: str) -> None:
            revoked.append(device_id)

    monkeypatch.setattr("app.relay_config.build_client", lambda _cfg: _FakeClient())

    client = app.test_client()
    _setup(client)
    resp = client.post("/settings/devices/parents_panel/delete", follow_redirects=False)
    assert resp.status_code == 302
    assert devices.get("parents_panel") is None
    assert revoked == ["parents_panel"]
