"""pico_bin_client smoke: parse normalises the well-known fields,
validate rejects out-of-bounds sleep intervals, manifest declares the
config_schema. Mirrors devices/esp32_client/tests/test_smoke.py with
the device id swapped and the renderer link asserted as pico_bin.
"""

from __future__ import annotations

import json

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def pico(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("pico_bin_client")
    assert d is not None
    return d


def test_manifest_fields(pico) -> None:
    assert pico.name == "Pico BIN client"
    # The renderer link is the source-of-truth for the kind -> renderer
    # mapping that clone_for_instances reads when adding a device
    # instance. pico_bin (not pi_bin, not esp32_bin) so the right
    # retain + packing pair gets clones.
    assert pico.renderer_ids == ["pico_bin"]
    assert pico.status_topic == "tesserae/pico_bin/status"
    assert pico.config_topic == "tesserae/pico_bin/config"
    assert "sleep_interval_s" in pico.config_schema


def test_manifest_panel_is_landscape_inky_13_3(pico) -> None:
    """The default kind panel is the 13.3" Spectra 6 (1600x1200
    landscape-native). Instances can override but the kind ships with
    the Inky Impression 13.3" dims so a one-click Register from the
    Discovered strip has sensible defaults."""
    panel = pico.manifest["panel"]
    assert panel["w"] == 1600
    assert panel["h"] == 1200
    assert panel["orientation"] == "landscape"


def test_parse_status_normalises_known_fields(pico) -> None:
    payload = json.dumps(
        {"battery_mv": 3850, "battery_pct": 72, "rssi": -64, "ip": "10.0.0.42"}
    ).encode()
    parsed = pico.parse_status(payload)
    assert parsed["battery_mv"] == 3850
    assert parsed["battery_pct"] == 72
    assert parsed["rssi"] == -64
    assert parsed["ip"] == "10.0.0.42"


def test_parse_status_empty_payload_returns_none_fields(pico) -> None:
    parsed = pico.parse_status(b"")
    assert parsed == {
        "battery_mv": None,
        "battery_pct": None,
        "rssi": None,
        "ip": None,
        "sleep_until": None,
        "next_sleep_s": None,
    }


def test_parse_status_reads_smart_sync_fields(pico) -> None:
    payload = json.dumps(
        {
            "battery_pct": 80,
            "sleep_until": 1_700_000_300.5,
            "next_sleep_s": 600,
        }
    ).encode()
    parsed = pico.parse_status(payload)
    assert parsed["sleep_until"] == 1_700_000_300.5
    assert parsed["next_sleep_s"] == 600


def test_parse_status_passes_through_unknown_fields(pico) -> None:
    payload = json.dumps({"battery_mv": 3700, "firmware": "0.1.0"}).encode()
    parsed = pico.parse_status(payload)
    assert parsed["battery_mv"] == 3700
    assert parsed["firmware"] == "0.1.0"


def test_validate_config_accepts_in_bounds(pico) -> None:
    ok, err = pico.validate_config({"sleep_interval_s": 900})
    assert ok and err is None


def test_validate_config_rejects_too_short(pico) -> None:
    ok, err = pico.validate_config({"sleep_interval_s": 4})
    assert not ok and "must be >=" in err


def test_validate_config_rejects_too_long(pico) -> None:
    ok, err = pico.validate_config({"sleep_interval_s": 99 * 24 * 60 * 60})
    assert not ok and "must be <=" in err


def test_validate_config_rejects_missing_field(pico) -> None:
    ok, err = pico.validate_config({})
    assert not ok and "missing" in err


def test_validate_config_rejects_non_integer(pico) -> None:
    ok, err = pico.validate_config({"sleep_interval_s": "not a number"})
    assert not ok and "integer" in err
