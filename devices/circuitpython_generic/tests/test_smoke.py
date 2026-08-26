"""circuitpython_generic smoke tests: the kind loads via the device
loader, parse_status normalises the v1 REST status body shape, and
validate_config bounds the sleep cadence."""

from __future__ import annotations

import json

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def cp(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("circuitpython_generic")
    assert d is not None, "circuitpython_generic kind failed to load"
    return d


def _parse(cp, body: dict) -> dict:
    return cp.parse_status(json.dumps(body).encode("utf-8"))


def test_kind_renders_via_circuitpython_png(cp) -> None:
    """Wire format defaults to circuitpython_png; adafruit_imageload
    can stream-decode that without a quantize pass on the device."""
    assert "circuitpython_png" in cp.renderer_ids


def test_parse_status_extracts_well_known_fields(cp) -> None:
    parsed = _parse(
        cp,
        {
            "battery_mv": 3850,
            "battery_pct": 45,
            "rssi": -72,
            "ip": "192.168.1.100",
            "next_sleep_s": 3600,
        },
    )
    assert parsed["battery_mv"] == 3850
    assert parsed["battery_pct"] == 45
    assert parsed["rssi"] == -72
    assert parsed["ip"] == "192.168.1.100"
    assert parsed["next_sleep_s"] == 3600


def test_parse_status_coerces_stringy_numbers(cp) -> None:
    """A firmware that sends battery as a quoted string ("3850") still
    lands on the device card as an integer rather than a string blob."""
    parsed = _parse(cp, {"battery_mv": "3850", "rssi": "-72"})
    assert parsed["battery_mv"] == 3850
    assert parsed["rssi"] == -72


def test_parse_status_preserves_unknown_fields(cp) -> None:
    """Unknown keys ride along so a board can surface its own
    diagnostics (cpu_temp_c, free_mem_b, etc.) without each one needing
    a device-kind code change first."""
    parsed = _parse(cp, {"battery_mv": 3850, "cpu_temp_c": 42, "free_mem_b": 102400})
    assert parsed["battery_mv"] == 3850
    assert parsed["cpu_temp_c"] == 42
    assert parsed["free_mem_b"] == 102400


def test_parse_status_empty_body_returns_empty_dict(cp) -> None:
    """A pure 'I painted, going to sleep' poll posts no body; the
    last-seen timestamp should still tick from that."""
    assert cp.parse_status(b"") == {}


def test_parse_status_garbage_body_surfaces_as_raw(cp) -> None:
    """A firmware bug sending malformed JSON should not lose the
    payload silently; the raw text lands under "raw" so the Settings
    card can flag it."""
    parsed = cp.parse_status(b"this is not json")
    assert parsed == {"raw": "this is not json"}


def test_validate_config_accepts_in_range_sleep_interval(cp) -> None:
    ok, err = cp.validate_config({"sleep_interval_s": 900})
    assert ok is True
    assert err is None


def test_validate_config_rejects_missing_sleep_interval(cp) -> None:
    ok, err = cp.validate_config({})
    assert ok is False
    assert err is not None and "sleep_interval_s" in err


def test_validate_config_rejects_too_short(cp) -> None:
    ok, err = cp.validate_config({"sleep_interval_s": 4})
    assert ok is False
    assert err is not None and ">=" in err


def test_validate_config_rejects_too_long(cp) -> None:
    ok, err = cp.validate_config({"sleep_interval_s": 10 * 7 * 24 * 60 * 60})
    assert ok is False
    assert err is not None and "<=" in err


def test_validate_config_rejects_non_integer(cp) -> None:
    ok, err = cp.validate_config({"sleep_interval_s": "not an int"})
    assert ok is False
    assert err is not None and "integer" in err
