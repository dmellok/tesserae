"""Reshape HA device telemetry into Tesserae's heartbeat shape (OpenDisplay).

Pure-logic tests for the mapping. The template runs on Home Assistant, so
these exercise the parse side against the JSON it would render.
"""

from __future__ import annotations

from app.ha_telemetry import build_template, parse_telemetry


def test_build_template_embeds_device_and_uses_functions() -> None:
    t = build_template("4ab9c7f0e2")
    assert '"4ab9c7f0e2"' in t
    # Function forms, not filters (some HA builds don't register the filters).
    assert "device_entities(d)" in t
    assert 'device_attr(d, "sw_version")' in t


def test_parse_maps_and_coerces() -> None:
    rendered = '{"fw_version": "1.4.2", "battery_pct": "85", "rssi": "-61", "temperature": "21.5"}'
    hb = parse_telemetry(rendered)
    assert hb == {"fw_version": "1.4.2", "battery_pct": 85, "rssi": -61, "temperature": 21.5}
    # battery_pct / rssi are ints, temperature keeps its fraction.
    assert isinstance(hb["battery_pct"], int)
    assert isinstance(hb["temperature"], float)


def test_parse_drops_absent_and_blank_fields() -> None:
    # Blank strings (no such sensor) and empty fw are dropped, so tiles stay
    # blank rather than showing zero.
    rendered = '{"fw_version": "", "battery_pct": "", "rssi": "", "temperature": "18"}'
    assert parse_telemetry(rendered) == {"temperature": 18}


def test_parse_drops_non_numeric() -> None:
    rendered = '{"fw_version": "2.0", "battery_pct": "unknown", "rssi": "n/a", "temperature": ""}'
    assert parse_telemetry(rendered) == {"fw_version": "2.0"}


def test_parse_tolerates_garbage() -> None:
    assert parse_telemetry("not json") == {}
    assert parse_telemetry("[1, 2, 3]") == {}
    assert parse_telemetry("") == {}
