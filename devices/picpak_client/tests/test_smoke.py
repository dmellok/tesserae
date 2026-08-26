"""picpak_client smoke: parse normalises the well-known fields, the
manifest declares the right panel gamut + scan direction, and validate
rejects out-of-bounds sleep intervals."""

from __future__ import annotations

import json

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def pic(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("picpak_client")
    assert d is not None
    return d


def test_manifest_fields(pic) -> None:
    assert pic.name == "PicPak client"
    # Binds to stock esp32_bin; the 2 bpp BWRY packer + the vflip step
    # both live upstream in the shared renderer, no picpak-specific
    # renderer needed.
    assert pic.renderer_ids == ["esp32_bin"]
    assert pic.status_topic == "tesserae/picpak/status"
    assert pic.config_topic == "tesserae/picpak/config"
    assert "sleep_interval_s" in pic.config_schema


def test_manifest_declares_bwry_panel_with_vflip(pic) -> None:
    """The PicPak panel is 400x300 4-colour BWRY and scans bottom-to-top
    at the hardware level. The manifest carries both signals so
    ``esp32_bin`` picks the right pack format + row order without the
    user having to touch settings."""
    panel = pic.manifest.get("panel", {})
    assert panel.get("w") == 400
    assert panel.get("h") == 300
    assert panel.get("gamut") == "bwry_4"
    assert panel.get("vflip") is True


def test_parse_status_normalises_known_fields(pic) -> None:
    payload = json.dumps(
        {"battery_mv": 4164, "battery_pct": 96, "rssi": -63, "ip": "10.0.20.40"}
    ).encode()
    parsed = pic.parse_status(payload)
    assert parsed["battery_mv"] == 4164
    assert parsed["battery_pct"] == 96
    assert parsed["rssi"] == -63
    assert parsed["ip"] == "10.0.20.40"


def test_parse_status_empty_payload_returns_none_fields(pic) -> None:
    parsed = pic.parse_status(b"")
    assert parsed == {
        "battery_mv": None,
        "battery_pct": None,
        "rssi": None,
        "ip": None,
        "sleep_until": None,
        "next_sleep_s": None,
    }


def test_parse_status_passes_through_picpak_specific_fields(pic) -> None:
    """PicPak's heartbeat carries kind / panel dims / firmware version /
    wake_reason. The parser passes them through so the admin UI can
    surface them without this module having to know about each one."""
    payload = json.dumps(
        {
            "battery_mv": 3900,
            "kind": "picpak_client",
            "panel_w": 400,
            "panel_h": 300,
            "fw_version": "0.1.0-dev",
            "wake_reason": "timer",
        }
    ).encode()
    parsed = pic.parse_status(payload)
    assert parsed["kind"] == "picpak_client"
    assert parsed["panel_w"] == 400
    assert parsed["panel_h"] == 300
    assert parsed["fw_version"] == "0.1.0-dev"
    assert parsed["wake_reason"] == "timer"


def test_parse_status_reads_smart_sync_fields(pic) -> None:
    payload = json.dumps(
        {"battery_pct": 80, "sleep_until": 1_700_000_300.5, "next_sleep_s": 900}
    ).encode()
    parsed = pic.parse_status(payload)
    assert parsed["sleep_until"] == 1_700_000_300.5
    assert parsed["next_sleep_s"] == 900


def test_validate_config_accepts_in_bounds(pic) -> None:
    ok, err = pic.validate_config({"sleep_interval_s": 900})
    assert ok and err is None


def test_validate_config_rejects_too_short(pic) -> None:
    ok, err = pic.validate_config({"sleep_interval_s": 4})
    assert not ok and "must be >=" in err


def test_validate_config_rejects_too_long(pic) -> None:
    ok, err = pic.validate_config({"sleep_interval_s": 99 * 24 * 60 * 60})
    assert not ok and "must be <=" in err


def test_validate_config_rejects_missing_field(pic) -> None:
    ok, err = pic.validate_config({})
    assert not ok and "missing" in err


def test_validate_config_rejects_non_integer(pic) -> None:
    ok, err = pic.validate_config({"sleep_interval_s": "not a number"})
    assert not ok and "integer" in err
