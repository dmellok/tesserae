"""esp32_client smoke: parse normalises the well-known fields, validate
rejects out-of-bounds sleep intervals, manifest declares config_schema."""

from __future__ import annotations

import json

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def esp(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("esp32_client")
    assert d is not None
    return d


def test_manifest_fields(esp) -> None:
    assert esp.name == "ESP32 client"
    assert esp.renderer_ids == ["esp32_bin"]
    assert esp.status_topic == "tesserae/esp32/status"
    assert esp.config_topic == "tesserae/esp32/config"
    assert "sleep_interval_s" in esp.config_schema


def test_parse_status_normalises_known_fields(esp) -> None:
    payload = json.dumps(
        {"battery_mv": 3850, "battery_pct": 72, "rssi": -64, "ip": "10.0.0.42"}
    ).encode()
    parsed = esp.parse_status(payload)
    assert parsed["battery_mv"] == 3850
    assert parsed["battery_pct"] == 72
    assert parsed["rssi"] == -64
    assert parsed["ip"] == "10.0.0.42"


def test_parse_status_empty_payload_returns_none_fields(esp) -> None:
    parsed = esp.parse_status(b"")
    assert parsed == {"battery_mv": None, "battery_pct": None, "rssi": None, "ip": None}


def test_parse_status_passes_through_unknown_fields(esp) -> None:
    payload = json.dumps({"battery_mv": 3700, "firmware": "0.4.2"}).encode()
    parsed = esp.parse_status(payload)
    assert parsed["battery_mv"] == 3700
    assert parsed["firmware"] == "0.4.2"


def test_validate_config_accepts_in_bounds(esp) -> None:
    ok, err = esp.validate_config({"sleep_interval_s": 900})
    assert ok and err is None


def test_validate_config_rejects_too_short(esp) -> None:
    ok, err = esp.validate_config({"sleep_interval_s": 5})
    assert not ok and "must be >=" in err


def test_validate_config_rejects_too_long(esp) -> None:
    ok, err = esp.validate_config({"sleep_interval_s": 99 * 24 * 60 * 60})
    assert not ok and "must be <=" in err


def test_validate_config_rejects_missing_field(esp) -> None:
    ok, err = esp.validate_config({})
    assert not ok and "missing" in err


def test_validate_config_rejects_non_integer(esp) -> None:
    ok, err = esp.validate_config({"sleep_interval_s": "not a number"})
    assert not ok and "integer" in err
