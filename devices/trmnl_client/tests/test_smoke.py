"""trmnl_client smoke tests: parse_status handles both mV and decimal-V
``Battery-Voltage`` formats; the rest of the header coercion still
works the way the BYOS protocol expects."""

from __future__ import annotations

import json

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def trmnl(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("trmnl_client")
    assert d is not None
    return d


def _parse(trmnl, headers: dict) -> dict:
    return trmnl.parse_status(json.dumps(headers).encode("utf-8"))


def test_battery_voltage_as_millivolts_int(trmnl) -> None:
    """Older / DIY firmware sends millivolts as an integer string."""
    parsed = _parse(trmnl, {"Battery-Voltage": "3860"})
    assert parsed["battery_mv"] == 3860


def test_battery_voltage_as_decimal_volts(trmnl) -> None:
    """Newer native firmware sends volts as a decimal string (e.g.
    ``"3.86"``). Tesserae must accept it and normalise to mV so the
    topbar indicator + HA discovery pick the device up."""
    parsed = _parse(trmnl, {"Battery-Voltage": "3.86"})
    assert parsed["battery_mv"] == 3860


def test_battery_voltage_as_decimal_volts_extra_precision(trmnl) -> None:
    """A reading like ``"4.214"`` rounds to nearest mV."""
    parsed = _parse(trmnl, {"Battery-Voltage": "4.214"})
    assert parsed["battery_mv"] == 4214


def test_battery_voltage_at_full_charge_volts(trmnl) -> None:
    parsed = _parse(trmnl, {"Battery-Voltage": "4.2"})
    assert parsed["battery_mv"] == 4200


def test_battery_voltage_case_folded(trmnl) -> None:
    """Header lookup is case-insensitive; volts form must work under
    any of the documented case spellings."""
    for spelling in ("Battery-Voltage", "battery-voltage", "BATTERY-VOLTAGE"):
        parsed = _parse(trmnl, {spelling: "3.86"})
        assert parsed["battery_mv"] == 3860, spelling


def test_battery_voltage_missing(trmnl) -> None:
    parsed = _parse(trmnl, {"rssi": "-58"})
    assert parsed["battery_mv"] is None


def test_battery_voltage_malformed(trmnl) -> None:
    """Garbage values surface as None rather than crashing the request."""
    for bad in ("not a number", "", "  ", "1.2.3"):
        parsed = _parse(trmnl, {"Battery-Voltage": bad})
        assert parsed["battery_mv"] is None, bad


def test_battery_voltage_negative_or_zero_ignored(trmnl) -> None:
    """Sensors occasionally read 0 on a flat battery; don't surface that
    as a 0 mV reading (it's noise, not a real measurement)."""
    parsed = _parse(trmnl, {"Battery-Voltage": "0"})
    assert parsed["battery_mv"] is None
    parsed = _parse(trmnl, {"Battery-Voltage": "-1"})
    assert parsed["battery_mv"] is None


def test_battery_pct_still_parsed_alongside_voltage(trmnl) -> None:
    """When both pct (from percent-charged) and voltage are present,
    both surface in the parsed dict; merge_status_parsed downstream
    keeps the explicit pct."""
    parsed = _parse(trmnl, {"Percent-Charged": "67", "Battery-Voltage": "3.86"})
    assert parsed["battery_pct"] == 67
    assert parsed["battery_mv"] == 3860


def test_rssi_and_panel_dims_still_work(trmnl) -> None:
    """Sanity: changing the battery parser didn't break neighbouring
    field parsing."""
    parsed = _parse(trmnl, {"rssi": "-58", "Width": "800", "Height": "480"})
    assert parsed["rssi"] == -58
    assert parsed["panel_w"] == 800
    assert parsed["panel_h"] == 480
