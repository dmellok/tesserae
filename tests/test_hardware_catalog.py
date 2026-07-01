"""Hardware catalog: discover, schema validation, derived-kind registration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import device_loader, hardware_catalog
from app.main import REPO_ROOT


@pytest.fixture
def hardware_schema_path() -> Path:
    return REPO_ROOT / "schema" / "hardware.schema.json"


@pytest.fixture
def device_schema_path() -> Path:
    return REPO_ROOT / "schema" / "device.schema.json"


def _write_protocol(devices_dir: Path, name: str, *, panel: dict[str, Any] | None = None) -> Path:
    """Drop a minimal protocol-level device folder under ``devices_dir``."""
    d = devices_dir / name
    d.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": f"{name} protocol",
        "version": "0.0.1",
        "renderers": ["pi_png"],
        "status_topic": f"tesserae/{name}/status",
        "panel": panel or {"w": 800, "h": 480, "orientation": "landscape"},
    }
    (d / "device.json").write_text(json.dumps(manifest))
    (d / "device.py").write_text("def parse_status(payload):\n    return {}\n")
    return d


def _write_hardware(
    hardware_dir: Path,
    *,
    vendor: str,
    file_name: str,
    manifest: dict[str, Any],
) -> Path:
    vendor_dir = hardware_dir / vendor
    vendor_dir.mkdir(parents=True, exist_ok=True)
    path = vendor_dir / file_name
    path.write_text(json.dumps(manifest))
    return path


def _hardware_manifest(
    *,
    sku_id: str = "test_sku",
    protocol: str = "test_protocol",
    panel_w: int = 1200,
    panel_h: int = 1600,
    **extra: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tesserae_compat": "1.x",
        "id": sku_id,
        "name": "Test SKU",
        "vendor": "Test Vendor",
        "protocol": protocol,
        "panel": {"w": panel_w, "h": panel_h, "orientation": "landscape"},
    }
    base.update(extra)
    return base


def test_bundled_hardware_loads_against_real_protocols(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """The repo's bundled hardware/ entries load cleanly against the
    bundled protocol-level devices/, with no schema or wiring errors."""
    registry = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=device_schema_path,
        data_root=tmp_path,
        hardware_dir=REPO_ROOT / "hardware",
        hardware_schema_path=hardware_schema_path,
    )
    assert registry.errors == []
    assert "seeed_reterminal_e1003" in registry.devices
    sku = registry.devices["seeed_reterminal_e1003"]
    assert sku.panel is not None
    # Landscape-native as of v0.64.57 (the unified firmware's IT8951 driver
    # writes in landscape order); the manifest used to declare portrait dims
    # while it routed through TRMNL BYOS + trmnl_png.
    assert sku.panel["w"] == 1872
    assert sku.panel["h"] == 1404
    assert sku.manifest["vendor"] == "Seeed Studio"


def test_discover_validates_schema(tmp_path: Path, hardware_schema_path: Path) -> None:
    """A hardware entry missing a required field surfaces as a LoaderError
    with the field path in the message, not a crash."""
    _write_hardware(
        tmp_path,
        vendor="acme",
        file_name="bad.json",
        manifest={"tesserae_compat": "1.x", "id": "bad_sku"},  # missing name, vendor, etc.
    )
    entries, errors = hardware_catalog.discover_hardware(tmp_path, schema_path=hardware_schema_path)
    assert entries == []
    assert len(errors) == 1
    assert "hardware schema" in errors[0].message


def test_unknown_protocol_surfaces_as_error(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """A hardware entry referencing a protocol that doesn't exist in the
    registry surfaces as 'unknown protocol' rather than silently
    skipping."""
    devs = tmp_path / "devices"
    devs.mkdir()
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="acme",
        file_name="orphan.json",
        manifest=_hardware_manifest(sku_id="orphan_sku", protocol="does_not_exist"),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    assert "orphan_sku" not in registry.devices
    assert any("unknown protocol" in err.message for err in registry.errors)


def test_hardware_entry_derives_from_protocol(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """The derived kind borrows the protocol's parse_status module + status
    topic but carries its own panel, vendor, and protocol_config."""
    devs = tmp_path / "devices"
    _write_protocol(devs, "test_protocol")
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="acme",
        file_name="sku.json",
        manifest=_hardware_manifest(
            sku_id="acme_super",
            protocol="test_protocol",
            panel_w=1200,
            panel_h=1600,
            description="Test SKU description",
            protocol_config={"model_header": "ACME Super", "extra_key": 42},
        ),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    assert "acme_super" in registry.devices
    sku = registry.devices["acme_super"]
    proto = registry.devices["test_protocol"]
    assert sku.module is proto.module  # shared parse_status
    assert sku.status_topic == proto.status_topic
    assert sku.panel is not None
    assert (sku.panel["w"], sku.panel["h"]) == (1200, 1600)
    assert sku.manifest["vendor"] == "Test Vendor"
    assert sku.manifest["protocol_config"] == {
        "model_header": "ACME Super",
        "extra_key": 42,
    }


def test_folder_wins_on_id_conflict(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """When a hardware entry's id collides with an existing protocol folder,
    the folder wins and the hardware entry surfaces as 'id already in use'
    rather than silently shadowing the folder kind."""
    devs = tmp_path / "devices"
    _write_protocol(devs, "trmnl_client")
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="vendor",
        file_name="collision.json",
        manifest=_hardware_manifest(sku_id="trmnl_client", protocol="trmnl_client"),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    # Folder-defined kind is still the one in the registry.
    folder_kind = registry.devices["trmnl_client"]
    assert folder_kind.manifest["name"] == "trmnl_client protocol"
    assert any("already in use" in err.message for err in registry.errors)


def test_deprecated_aliases_register_as_lookup_targets(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """A deprecated alias on a hardware entry registers the derived Device
    under both the canonical id and each alias, so device-instance files
    keyed on the old id keep resolving after a rename."""
    devs = tmp_path / "devices"
    _write_protocol(devs, "test_protocol")
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="acme",
        file_name="renamed.json",
        manifest=_hardware_manifest(
            sku_id="new_name",
            protocol="test_protocol",
            deprecated_aliases=["old_name", "older_name"],
        ),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    assert "new_name" in registry.devices
    assert "old_name" in registry.devices
    assert "older_name" in registry.devices
    # All three resolve to the same Device, so a parse_status call routes
    # through the same protocol module regardless of which id the caller
    # used to look it up.
    assert registry.devices["new_name"] is registry.devices["old_name"]
    assert registry.devices["new_name"] is registry.devices["older_name"]


def test_config_schema_extends_merges_additively(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """``config_schema_extends`` adds fields to the protocol's
    ``config_schema`` without replacing the existing form."""
    devs = tmp_path / "devices"
    proto_dir = _write_protocol(devs, "test_protocol")
    # Re-write the manifest with a config_schema so we can verify the
    # merge keeps the protocol's original fields.
    base_manifest = {
        "tesserae_compat": "1.x",
        "name": "test_protocol",
        "version": "0.0.1",
        "renderers": ["pi_png"],
        "status_topic": "tesserae/test_protocol/status",
        "panel": {"w": 800, "h": 480, "orientation": "landscape"},
        "config_schema": {"refresh_rate_s": {"type": "int", "default": 60, "label": "Refresh"}},
    }
    (proto_dir / "device.json").write_text(json.dumps(base_manifest))

    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="acme",
        file_name="extended.json",
        manifest=_hardware_manifest(
            sku_id="extended_sku",
            protocol="test_protocol",
            config_schema_extends={
                "brightness": {"type": "int", "default": 50, "label": "Brightness"}
            },
        ),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    sku = registry.devices["extended_sku"]
    schema = sku.config_schema
    assert "refresh_rate_s" in schema  # protocol's field preserved
    assert "brightness" in schema  # extension applied
    assert schema["brightness"]["default"] == 50


def test_hardware_renderers_override_replaces_protocol_default(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """When a hardware entry declares its own ``renderers`` array, the
    derived kind picks up that list instead of inheriting the
    protocol's default. Used when the same wire protocol serves panels
    with meaningfully different output formats: TRMNL BYOS drives both
    mono panels (1-bit trmnl_png) and colour panels (indexed-palette
    trmnl_png_color) over the exact same /api/display flow, and only
    the renderer differs per-SKU."""
    devs = tmp_path / "devices"
    # Protocol declares one renderer as its default; the hardware entry
    # will override with something different.
    _write_protocol(devs, "test_protocol")
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="vendor",
        file_name="colour_sku.json",
        manifest=_hardware_manifest(
            sku_id="colour_sku",
            protocol="test_protocol",
            renderers=["esp32_bin"],  # different from the protocol's ["pi_png"]
        ),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    sku = registry.devices["colour_sku"]
    assert sku.renderer_ids == ["esp32_bin"], (
        "renderers list on the hardware manifest must replace the "
        f"protocol's default; got {sku.renderer_ids!r}"
    )


def test_hardware_without_renderers_inherits_from_protocol(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """A hardware entry that DOESN'T declare renderers still inherits
    the protocol's defaults, so the common case (99% of SKUs) doesn't
    need to duplicate the renderer list."""
    devs = tmp_path / "devices"
    _write_protocol(devs, "test_protocol")
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="vendor",
        file_name="inherit_sku.json",
        manifest=_hardware_manifest(sku_id="inherit_sku", protocol="test_protocol"),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    sku = registry.devices["inherit_sku"]
    assert sku.renderer_ids == ["pi_png"]  # protocol's default


def test_underscore_prefixed_files_ignored(
    tmp_path: Path, device_schema_path: Path, hardware_schema_path: Path
) -> None:
    """Files starting with ``_`` are treated as notes / drafts and skipped
    by the discovery walk."""
    devs = tmp_path / "devices"
    _write_protocol(devs, "test_protocol")
    hw = tmp_path / "hardware"
    _write_hardware(
        hw,
        vendor="acme",
        file_name="_draft.json",
        manifest=_hardware_manifest(sku_id="should_skip", protocol="test_protocol"),
    )
    registry = device_loader.discover(
        devs,
        schema_path=device_schema_path,
        data_root=tmp_path / "data",
        hardware_dir=hw,
        hardware_schema_path=hardware_schema_path,
    )
    assert "should_skip" not in registry.devices
    assert registry.errors == []
