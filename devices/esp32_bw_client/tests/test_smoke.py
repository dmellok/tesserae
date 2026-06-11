"""esp32_bw_client smoke: parse normalises the well-known fields,
validate rejects out-of-bounds sleep intervals, manifest declares
config_schema + binds to the new ``esp32_bw_bin`` renderer."""

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
    d = registry.get("esp32_bw_client")
    assert d is not None
    return d


def test_manifest_fields(esp) -> None:
    assert esp.name == "ESP32 BW client"
    assert esp.renderer_ids == ["esp32_bw_bin"]
    assert esp.status_topic == "tesserae/esp32/status"
    assert esp.config_topic == "tesserae/esp32/config"
    assert "sleep_interval_s" in esp.config_schema


def test_manifest_panel_is_400x300_landscape(esp) -> None:
    """The 4.2" Waveshare BW is the canonical first target; if a future
    revision needs a different default, the test exists to flag it."""
    assert esp.panel == {
        "w": 400,
        "h": 300,
        "orientation": "landscape",
        "name": 'Waveshare 4.2" BW',
    }


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
    assert parsed == {
        "battery_mv": None,
        "battery_pct": None,
        "rssi": None,
        "ip": None,
        "panel_w": None,
        "panel_h": None,
        "sleep_until": None,
        "next_sleep_s": None,
    }


def test_parse_status_extracts_panel_dims_from_heartbeat(esp) -> None:
    """BW panels come in many sizes; the firmware reports its actual
    resolution on the heartbeat so app.discovery can pre-fill the
    Discovered card with the right (w, h) instead of the manifest's
    400x300 default. Without this, a 296x128 panel would have to be
    hand-edited after registration."""
    payload = json.dumps({"battery_pct": 80, "panel_w": 296, "panel_h": 128}).encode()
    parsed = esp.parse_status(payload)
    assert parsed["panel_w"] == 296
    assert parsed["panel_h"] == 128


def test_parse_status_accepts_width_height_aliases(esp) -> None:
    """``width`` / ``height`` mirror the TRMNL + KOReader naming.
    Firmware variants that follow that convention drop in without
    a translator."""
    payload = json.dumps({"battery_pct": 80, "width": 800, "height": 480}).encode()
    parsed = esp.parse_status(payload)
    assert parsed["panel_w"] == 800
    assert parsed["panel_h"] == 480


def test_parse_status_canonical_key_wins_over_alias(esp) -> None:
    """If both are sent, ``panel_w`` is authoritative over ``width``
    (canonical name beats alias). Defensive; firmware shouldn't send
    both, but if it does we have a deterministic answer."""
    payload = json.dumps({"panel_w": 296, "width": 999, "panel_h": 128, "height": 999}).encode()
    parsed = esp.parse_status(payload)
    assert parsed["panel_w"] == 296
    assert parsed["panel_h"] == 128


def test_parse_status_reads_smart_sync_fields(esp) -> None:
    payload = json.dumps(
        {
            "battery_pct": 80,
            "sleep_until": 1_700_000_300.5,
            "next_sleep_s": 600,
        }
    ).encode()
    parsed = esp.parse_status(payload)
    assert parsed["sleep_until"] == 1_700_000_300.5
    assert parsed["next_sleep_s"] == 600


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
