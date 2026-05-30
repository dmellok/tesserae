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
def registries(tmp_path: Path):
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


def test_create_instance_portrait_swaps_and_stores_orientation(registries) -> None:
    devices, renderers, data_root = registries
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
        panel_overrides={"w": 800, "h": 480},
        orientation="portrait_flipped",
    )
    assert result.ok and result.device is not None
    panel = result.device.panel
    assert panel["orientation"] == "portrait_flipped"
    # Portrait variant → canvas is tall (w/h swapped from the landscape override).
    assert (panel["w"], panel["h"]) == (480, 800)
    # The Panel model derives flip from the orientation for the renderers.
    from app.panel import is_flipped_orientation
    from app.state.page_store import Panel

    assert is_flipped_orientation("portrait_flipped") is True
    assert is_flipped_orientation("portrait") is False
    assert Panel(w=480, h=800, flip=True).flip is True


def test_update_panel_sets_flipped_orientation(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    r1 = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        w=800,
        h=480,
        orientation="landscape_flipped",
    )
    assert r1.ok and r1.device is not None
    assert r1.device.panel["orientation"] == "landscape_flipped"


def test_update_quiet_hours_persists_and_reloads(registries) -> None:
    """An enabled quiet-hours override writes ``quiet_hours`` to the
    instance manifest and the reloaded Device exposes it."""
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        kind_id="esp32_client",
    )
    result = device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        enabled=True,
        start="22:30",
        end="07:00",
    )
    assert result.ok and result.device is not None
    qh = result.device.manifest.get("quiet_hours")
    assert qh == {"enabled": True, "start": "22:30", "end": "07:00"}


def test_update_quiet_hours_clearing_drops_block(registries) -> None:
    """Saving with everything blank + disabled removes the block from
    the manifest so the device falls back to the app-level setting."""
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        kind_id="esp32_client",
    )
    # First set it.
    device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        enabled=True,
        start="22:30",
        end="07:00",
    )
    # Then clear it.
    result = device_service.update_instance_quiet_hours(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="hallway",
        enabled=False,
        start="",
        end="",
    )
    assert result.ok and result.device is not None
    assert "quiet_hours" not in result.device.manifest


def test_create_instance_rejects_bad_id_and_unknown_kind(registries) -> None:
    devices, renderers, data_root = registries
    # Mixed case is forgivingly lowercased, but a leading digit / spaces
    # / too-short are genuinely rejected.
    for bad in ("1leading_digit", "a", "has space"):
        res = device_service.create_instance(
            devices=devices,
            renderers=renderers,
            data_root=data_root,
            instance_id=bad,
            kind_id="esp32_client",
        )
        assert not res.ok and res.device is None, bad

    bad_kind = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="ok_id",
        kind_id="nope",
    )
    assert not bad_kind.ok

    # Registering against another instance (not a kind) is refused.
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    dup = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    assert not dup.ok  # id already in use


def test_update_panel_stores_dims_verbatim(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    result = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        w=600,
        h=448,
        orientation="portrait",
    )
    assert result.ok and result.device is not None
    # update stores exactly what's given — no swap (the form's JS already
    # swapped the displayed inputs).
    assert (result.device.panel["w"], result.device.panel["h"]) == (600, 448)
    assert result.device.panel["orientation"] == "portrait"
    saved = json.loads((data_root / "esp32_lab.json").read_text())
    assert saved["panel"]["w"] == 600


def test_update_panel_persists_gamut_and_clamps_unknown(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        kind_id="pi_bin_client",
    )
    ok = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=600,
        h=448,
        orientation="landscape",
        gamut="inky_7colour",
    )
    assert ok.ok and ok.device is not None
    assert ok.device.panel["gamut"] == "inky_7colour"
    # Persisted to disk.
    saved = json.loads((data_root / "bin_57.json").read_text())
    assert saved["panel"]["gamut"] == "inky_7colour"
    # An unknown gamut clamps back to the safe E6 default.
    clamped = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=600,
        h=448,
        orientation="landscape",
        gamut="bogus",
    )
    assert clamped.ok and clamped.device is not None
    assert clamped.device.panel["gamut"] == "waveshare_e6"
    # Omitting gamut leaves the stored value untouched.
    device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=600,
        h=448,
        orientation="landscape",
        gamut="inky_7colour",
    )
    kept = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_57",
        w=640,
        h=400,
        orientation="landscape",
    )
    assert kept.ok and kept.device is not None
    assert kept.device.panel["gamut"] == "inky_7colour"


def test_update_panel_persists_and_clamps_underscan(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        kind_id="pi_bin_client",
    )
    ok = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        w=600,
        h=448,
        orientation="landscape",
        underscan=24,
    )
    assert ok.ok and ok.device is not None
    assert ok.device.panel["underscan"] == 24
    # Oversize is clamped so the inset can't swallow the panel.
    big = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        w=600,
        h=448,
        orientation="landscape",
        underscan=99999,
    )
    assert big.ok and big.device is not None
    assert big.device.panel["underscan"] == 448 // 2 - 1  # min(w,h)//2 - 1
    # Negative clamps to 0; omitting it leaves the stored value.
    neg = device_service.update_instance_panel(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="bin_u",
        w=600,
        h=448,
        orientation="landscape",
        underscan=-5,
    )
    assert neg.ok and neg.device is not None
    # 0 is the default, so the surfaced panel dict omits it.
    assert neg.device.panel.get("underscan", 0) == 0


def test_delete_instance_refuses_kind(registries) -> None:
    devices, renderers, _data_root = registries
    result = device_service.delete_instance(
        devices=devices, renderers=renderers, instance_id="esp32_client"
    )
    assert not result.ok
    assert devices.get("esp32_client") is not None


def test_delete_instance_removes_record_file_and_clones(registries) -> None:
    devices, renderers, data_root = registries
    device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=data_root,
        instance_id="esp32_lab",
        kind_id="esp32_client",
    )
    assert renderers.get("esp32_bin__esp32_lab") is not None
    result = device_service.delete_instance(
        devices=devices, renderers=renderers, instance_id="esp32_lab"
    )
    assert result.ok
    assert devices.get("esp32_lab") is None
    assert renderers.get("esp32_bin__esp32_lab") is None
    assert not (data_root / "esp32_lab.json").exists()
