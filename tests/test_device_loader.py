"""Device loader contract: discovery, compat rejection, export validation,
config_topic-conditional validate_config requirement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import device_loader
from app.main import REPO_ROOT


@pytest.fixture
def schema_path() -> Path:
    return REPO_ROOT / "schema" / "device.schema.json"


def _write(root: Path, name: str, manifest_overrides: dict, body: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": name,
        "version": "0.0.1",
        "renderers": ["pi_png"],
        "status_topic": f"tesserae/{name}/status",
    }
    manifest.update(manifest_overrides)
    (d / "device.json").write_text(json.dumps(manifest))
    (d / "device.py").write_text(body)
    return d


_PARSE_ONLY = "def parse_status(payload):\n    return {}\n"

_PARSE_AND_VALIDATE = """
def parse_status(payload):
    return {}

def validate_config(payload):
    return True, None
"""


def test_discovers_bundled_devices(tmp_path: Path, schema_path: Path) -> None:
    registry = device_loader.discover(
        REPO_ROOT / "devices", schema_path=schema_path, data_root=tmp_path
    )
    assert registry.errors == []
    assert "pi_bin_client" in registry.devices
    assert "pi_png_client" in registry.devices
    assert "esp32_client" in registry.devices


def test_display_name_collapses_auto_default_to_id(tmp_path: Path) -> None:
    """An instance whose name is the auto-generated "Kind (id)" default
    shows just the id in pickers; a custom name is shown verbatim.
    display_name only reads name + id, so a stub module is fine."""
    from types import ModuleType

    from app.device_loader import Device

    stub = ModuleType("stub")

    def _dev(name: str) -> Device:
        return Device(
            id="lounge_frame",
            path=tmp_path,
            manifest={"name": name, "status_topic": "t"},
            module=stub,
            data_dir=tmp_path,
            kind_of="pi_bin_client",
        )

    assert _dev("Pi BIN client (lounge_frame)").display_name == "lounge_frame"
    assert _dev("Lounge Display").display_name == "Lounge Display"


def test_compat_mismatch_rejected(tmp_path: Path, schema_path: Path) -> None:
    devs = tmp_path / "devices"
    devs.mkdir()
    _write(devs, "future", {"tesserae_compat": "9.x"}, _PARSE_ONLY)
    registry = device_loader.discover(devs, schema_path=schema_path, data_root=tmp_path / "data")
    assert "future" not in registry.devices
    assert any("tesserae_compat" in err.message for err in registry.errors)


def test_missing_parse_status_rejected(tmp_path: Path, schema_path: Path) -> None:
    devs = tmp_path / "devices"
    devs.mkdir()
    _write(devs, "empty", {}, "# nothing\n")
    registry = device_loader.discover(devs, schema_path=schema_path, data_root=tmp_path / "data")
    assert "empty" not in registry.devices
    assert any("parse_status" in err.message for err in registry.errors)


def test_config_topic_requires_validate_config(tmp_path: Path, schema_path: Path) -> None:
    devs = tmp_path / "devices"
    devs.mkdir()
    _write(
        devs,
        "no_validate",
        {
            "config_topic": "tesserae/no_validate/config",
            "config_schema": {"x": {"type": "int", "default": 1}},
        },
        _PARSE_ONLY,  # parse_status only, no validate_config
    )
    registry = device_loader.discover(devs, schema_path=schema_path, data_root=tmp_path / "data")
    assert "no_validate" not in registry.devices
    assert any("validate_config" in err.message for err in registry.errors)


def test_config_topic_with_validate_config_passes(tmp_path: Path, schema_path: Path) -> None:
    devs = tmp_path / "devices"
    devs.mkdir()
    _write(
        devs,
        "ok",
        {
            "config_topic": "tesserae/ok/config",
            "config_schema": {"x": {"type": "int", "default": 1}},
        },
        _PARSE_AND_VALIDATE,
    )
    registry = device_loader.discover(devs, schema_path=schema_path, data_root=tmp_path / "data")
    assert "ok" in registry.devices


def test_validate_config_returns_tuple_check(tmp_path: Path, schema_path: Path) -> None:
    devs = tmp_path / "devices"
    devs.mkdir()
    # validate_config returns the wrong shape.
    body = """
def parse_status(payload):
    return {}

def validate_config(payload):
    return "not a tuple"
"""
    _write(
        devs,
        "bad_return",
        {
            "config_topic": "tesserae/bad/config",
            "config_schema": {"x": {"type": "int", "default": 1}},
        },
        body,
    )
    registry = device_loader.discover(devs, schema_path=schema_path, data_root=tmp_path / "data")
    device = registry.get("bad_return")
    assert device is not None
    ok, err = device.validate_config({"x": 1})
    assert ok is False
    assert "malformed" in err


def test_parse_status_falls_back_on_exception(tmp_path: Path, schema_path: Path) -> None:
    devs = tmp_path / "devices"
    devs.mkdir()
    body = """
def parse_status(payload):
    raise RuntimeError("nope")
"""
    _write(devs, "boom", {}, body)
    registry = device_loader.discover(devs, schema_path=schema_path, data_root=tmp_path / "data")
    device = registry.get("boom")
    assert device is not None
    parsed = device.parse_status(b"x")
    assert "error" in parsed
    assert "RuntimeError" in parsed["error"]


# ---------------------------------------------------------------------
# per-device locale
# ---------------------------------------------------------------------


def test_locale_absent_by_default() -> None:
    """A device (kind or instance) that never mentions locale defers to
    the app-wide default -- None here means "no override", not
    "render in English"."""
    from types import ModuleType

    from app.device_loader import Device

    device = Device(
        id="lounge_frame",
        path=Path("/tmp/lounge_frame"),
        manifest={"name": "Lounge", "status_topic": "t"},
        module=ModuleType("stub"),
        data_dir=Path("/tmp/lounge_frame_data"),
    )
    assert device.locale is None


def test_locale_property_reads_manifest() -> None:
    from types import ModuleType

    from app.device_loader import Device

    device = Device(
        id="kitchen",
        path=Path("/tmp/kitchen"),
        manifest={"name": "Kitchen", "status_topic": "t", "locale": "de"},
        module=ModuleType("stub"),
        data_dir=Path("/tmp/kitchen_data"),
    )
    assert device.locale == "de"


def test_instance_locale_overrides_kind(tmp_path: Path, schema_path: Path) -> None:
    """An instance's own locale wins over its kind's -- the German
    kitchen panel / English office panel off one server scenario."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "kitchen.json").write_text(
        json.dumps(
            {
                "id": "kitchen",
                "kind": "pi_bin_client",
                "name": "Kitchen",
                "status_topic": "tesserae/pi_bin/kitchen/status",
                "locale": "de",
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "office.json").write_text(
        json.dumps(
            {
                "id": "office",
                "kind": "pi_bin_client",
                "name": "Office",
                "status_topic": "tesserae/pi_bin/office/status",
            }
        ),
        encoding="utf-8",
    )
    registry = device_loader.discover(
        REPO_ROOT / "devices", schema_path=schema_path, data_root=data_dir
    )
    assert registry.errors == []
    assert registry.devices["kitchen"].locale == "de"
    assert registry.devices["office"].locale is None
