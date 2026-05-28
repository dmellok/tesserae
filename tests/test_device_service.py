"""Unit tests for the device-instance lifecycle service.

The Settings routes are thin wrappers over these; testing the service
directly locks down the behaviour that used to be duplicated (and had
drifted) across the Add-device and Discovered-register routes — most
importantly that both create paths derive topics the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import device_loader, device_service, renderer_loader
from app.main import REPO_ROOT


@pytest.fixture
def registries(tmp_path: Path):  # noqa: ANN201 — test fixture
    """Bundled kinds + renderers loaded into fresh registries, with a
    tmp instance data root."""
    data_root = tmp_path / "devices"
    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=data_root,
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=tmp_path / "rdata",
    )
    assert devices.errors == []
    assert renderers.errors == []
    return devices, renderers, data_root


def test_derive_topic_swaps_prefix() -> None:
    assert (
        device_service.derive_topic("tesserae/esp32/status", "esp32_lab", suffix="status")
        == "tesserae/esp32_lab/status"
    )
    assert (
        device_service.derive_topic("tesserae/esp32/config", "esp32_lab", suffix="config")
        == "tesserae/esp32_lab/config"
    )


def test_derive_topic_falls_back_for_odd_shape() -> None:
    # A kind topic that doesn't fit tesserae/<prefix>/... still yields a
    # unique per-instance topic rather than copying the kind's verbatim.
    assert (
        device_service.derive_topic("weird/topic", "esp32_lab", suffix="status")
        == "tesserae/esp32_lab/status"
    )


def test_create_instance_derives_topics_and_clones(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    assert result.ok
    dev = result.device
    assert dev is not None
    assert dev.status_topic == "tesserae/esp32_lab/status"
    assert dev.config_topic == "tesserae/esp32_lab/config"  # kind has a config topic
    # Renderer clone exists for the instance.
    assert renderers.get("esp32_bin__esp32_lab") is not None
    # Persisted.
    assert (data_root / "esp32_lab.json").exists()


def test_create_instance_portrait_swaps_dims(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
        panel_overrides={"w": 800, "h": 480},
        orientation="portrait",
    )
    assert result.ok and result.device is not None
    assert result.device.panel == {
        "w": 480,
        "h": 800,
        "orientation": "portrait",
        "name": "ESP32 e-paper",
    }


def test_create_instance_stores_rotation(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", kind_id="esp32_client", rotation=270,
    )
    assert result.ok and result.device is not None
    assert result.device.panel["rotation"] == 270
    # Panel model exposes it as CW quarter-turns for the renderers.
    from app.state.page_store import Panel

    assert Panel(w=800, h=480, rotation=270).rotation_quarters == 3
    assert Panel(w=800, h=480).rotation_quarters is None  # auto


def test_update_panel_can_set_and_clear_rotation(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", kind_id="esp32_client", rotation=90,
    )
    # Set an explicit rotation.
    r1 = device_service.update_instance_panel(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", w=800, h=480, orientation="landscape", rotation=180,
    )
    assert r1.ok and r1.device is not None
    assert r1.device.panel["rotation"] == 180
    # None clears it back to auto (key dropped).
    r2 = device_service.update_instance_panel(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", w=800, h=480, orientation="landscape", rotation=None,
    )
    assert r2.ok and r2.device is not None
    assert "rotation" not in r2.device.panel


def test_create_instance_rejects_bad_id_and_unknown_kind(registries) -> None:
    devices, renderers, data_root = registries
    # Mixed case is forgivingly lowercased, but a leading digit / spaces
    # / too-short are genuinely rejected.
    for bad in ("1leading_digit", "a", "has space"):
        res = device_service.create_instance(
            devices=devices, renderers=renderers, data_root=data_root,
            instance_id=bad, kind_id="esp32_client",
        )
        assert not res.ok and res.device is None, bad

    bad_kind = device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="ok_id", kind_id="nope",
    )
    assert not bad_kind.ok

    # Registering against another instance (not a kind) is refused.
    device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", kind_id="esp32_client",
    )
    dup = device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", kind_id="esp32_client",
    )
    assert not dup.ok  # id already in use


def test_update_panel_stores_dims_verbatim(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", kind_id="esp32_client",
    )
    result = device_service.update_instance_panel(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", w=600, h=448, orientation="portrait",
    )
    assert result.ok and result.device is not None
    # update stores exactly what's given — no swap (the form's JS already
    # swapped the displayed inputs).
    assert (result.device.panel["w"], result.device.panel["h"]) == (600, 448)
    assert result.device.panel["orientation"] == "portrait"
    saved = json.loads((data_root / "esp32_lab.json").read_text())
    assert saved["panel"]["w"] == 600


def test_delete_instance_refuses_kind(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.delete_instance(
        devices=devices, renderers=renderers, instance_id="esp32_client"
    )
    assert not result.ok
    assert devices.get("esp32_client") is not None


def test_delete_instance_removes_record_file_and_clones(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices, renderers=renderers, data_root=data_root,
        instance_id="esp32_lab", kind_id="esp32_client",
    )
    assert renderers.get("esp32_bin__esp32_lab") is not None
    result = device_service.delete_instance(
        devices=devices, renderers=renderers, instance_id="esp32_lab"
    )
    assert result.ok
    assert devices.get("esp32_lab") is None
    assert renderers.get("esp32_bin__esp32_lab") is None
    assert not (data_root / "esp32_lab.json").exists()
