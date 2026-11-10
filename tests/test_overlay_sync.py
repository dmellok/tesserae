"""Unit tests for app.overlay_sync: capability parsing and the
composition-to-wire rect transform that mirrors the .bin renderer's
chain (rotate on orientation mismatch, 180 flip, scale to native,
underscan inset). REST endpoint coverage lives in
tests/test_rest_overlay.py."""

from __future__ import annotations

import pytest

from app import overlay_sync

# -- advertised_overlay ---------------------------------------------------


def test_capability_parses_and_is_validated() -> None:
    assert overlay_sync.advertised_overlay({"overlay": {"schema": 1}}) == {"schema": 1}
    assert overlay_sync.advertised_overlay(b'{"overlay": {"schema": 2}}') == {"schema": 2}
    for payload in (
        {},
        {"overlay": None},
        {"overlay": {}},
        {"overlay": {"schema": 0}},
        {"overlay": {"schema": True}},
        {"overlay": {"schema": "1"}},
        b"not json",
    ):
        assert overlay_sync.advertised_overlay(payload) is None


# -- rect_to_wire ----------------------------------------------------------


def test_identity_when_comp_matches_native() -> None:
    # E1003 common case: landscape composition at native dims, no flip,
    # no underscan -> coordinates pass through untouched.
    assert overlay_sync.rect_to_wire(
        (120, 640, 300, 90),
        comp_w=1872,
        comp_h=1404,
        native_w=1872,
        native_h=1404,
        flip=False,
        underscan=0,
    ) == (120, 640, 300, 90)


def test_portrait_composition_rotates_cw() -> None:
    # Portrait 1404x1872 composition on the landscape-native panel:
    # rotate(-90, expand) maps (x, y, w, h) -> (H - (y + h), x, h, w).
    out = overlay_sync.rect_to_wire(
        (100, 200, 50, 80),
        comp_w=1404,
        comp_h=1872,
        native_w=1872,
        native_h=1404,
        flip=False,
        underscan=0,
    )
    assert out == (1872 - (200 + 80), 100, 80, 50)


def test_flip_mirrors_both_axes() -> None:
    out = overlay_sync.rect_to_wire(
        (10, 20, 100, 50),
        comp_w=800,
        comp_h=480,
        native_w=800,
        native_h=480,
        flip=True,
        underscan=0,
    )
    assert out == (800 - 110, 480 - 70, 100, 50)


def test_scale_to_native_dims() -> None:
    # Composition at half the native dims scales up 2x.
    out = overlay_sync.rect_to_wire(
        (10, 20, 30, 40),
        comp_w=400,
        comp_h=240,
        native_w=800,
        native_h=480,
        flip=False,
        underscan=0,
    )
    assert out == (20, 40, 60, 80)


def test_underscan_insets_toward_centre() -> None:
    # A full-panel rect shrinks into the (u, u)..(W-u, H-u) box.
    out = overlay_sync.rect_to_wire(
        (0, 0, 800, 480),
        comp_w=800,
        comp_h=480,
        native_w=800,
        native_h=480,
        flip=False,
        underscan=10,
    )
    assert out == (10, 10, 780, 460)


# -- point_from_wire (touch_space=fb, firmware #21) -------------------------


@pytest.mark.parametrize(
    "geo",
    [
        # identity: landscape composition at native dims
        {
            "comp_w": 960,
            "comp_h": 540,
            "native_w": 960,
            "native_h": 540,
            "flip": False,
            "underscan": 0,
        },
        # PaperS3 portrait: 540x960 canvas turned CW onto the 960x540 buffer
        {
            "comp_w": 540,
            "comp_h": 960,
            "native_w": 960,
            "native_h": 540,
            "flip": False,
            "underscan": 0,
        },
        # either flipped orientation
        {
            "comp_w": 960,
            "comp_h": 540,
            "native_w": 960,
            "native_h": 540,
            "flip": True,
            "underscan": 0,
        },
        {
            "comp_w": 540,
            "comp_h": 960,
            "native_w": 960,
            "native_h": 540,
            "flip": True,
            "underscan": 0,
        },
        # scaled composition
        {
            "comp_w": 480,
            "comp_h": 270,
            "native_w": 960,
            "native_h": 540,
            "flip": False,
            "underscan": 0,
        },
        # underscan inset
        {
            "comp_w": 960,
            "comp_h": 540,
            "native_w": 960,
            "native_h": 540,
            "flip": False,
            "underscan": 12,
        },
    ],
)
def test_point_from_wire_inverts_rect_to_wire(geo: dict) -> None:
    # Every canvas pixel sent forward, then back, lands on itself.
    for cx, cy in [(0, 0), (geo["comp_w"] - 1, 0), (0, geo["comp_h"] - 1), (37, 211), (201, 119)]:
        wire = overlay_sync.rect_to_wire((cx, cy, 1, 1), **geo)
        assert wire is not None
        assert overlay_sync.point_from_wire(wire[0], wire[1], **geo) == (cx, cy)


def test_point_from_wire_papers3_portrait_corners() -> None:
    # The buffer's top-right is the portrait canvas's top-left: the
    # renderer turns the canvas clockwise onto the buffer.
    geo = {
        "comp_w": 540,
        "comp_h": 960,
        "native_w": 960,
        "native_h": 540,
        "flip": False,
        "underscan": 0,
    }
    assert overlay_sync.point_from_wire(959, 0, **geo) == (0, 0)
    assert overlay_sync.point_from_wire(0, 0, **geo) == (0, 959)
    assert overlay_sync.point_from_wire(959, 539, **geo) == (539, 0)
    # Off-buffer input clamps onto the canvas.
    assert overlay_sync.point_from_wire(5000, -40, **geo) == (0, 0)


def test_offscreen_rect_degenerates_to_none() -> None:
    assert (
        overlay_sync.rect_to_wire(
            (900, 500, 40, 40),
            comp_w=800,
            comp_h=480,
            native_w=800,
            native_h=480,
            flip=False,
            underscan=0,
        )
        is None
    )


# -- max_targets (v1.9 firmware) ---------------------------------------------


def test_capability_parses_max_targets_with_clamp() -> None:
    assert overlay_sync.advertised_overlay({"overlay": {"schema": 1, "max_targets": 32}}) == {
        "schema": 1,
        "max_targets": 32,
    }
    # Clamped to a sane band; bools and junk ignored.
    assert overlay_sync.advertised_overlay({"overlay": {"schema": 1, "max_targets": 500}}) == {
        "schema": 1,
        "max_targets": 64,
    }
    assert overlay_sync.advertised_overlay({"overlay": {"schema": 1, "max_targets": 0}}) == {
        "schema": 1,
        "max_targets": 1,
    }
    assert overlay_sync.advertised_overlay({"overlay": {"schema": 1, "max_targets": True}}) == {
        "schema": 1
    }


# -- slot value resolution (attribute paths + per-slot map) ---------------


_STATES = {
    "light.desk": {
        "state": "on",
        "attributes": {"brightness": 128, "friendly_name": "Desk"},
    },
    "sensor.temp": {"state": "21.4", "attributes": {}},
    "sensor.gone": {"state": "unavailable", "attributes": {}},
}


def _get_state(entity_id: str):
    return _STATES.get(entity_id)


def test_resolve_plain_state_key() -> None:
    assert overlay_sync.resolve_slot_value("ha:sensor.temp", _get_state) == "21.4"


def test_resolve_attribute_path_key() -> None:
    assert (
        overlay_sync.resolve_slot_value("ha:light.desk:attributes.brightness", _get_state) == "128"
    )
    assert overlay_sync.resolve_slot_value("ha:light.desk:state", _get_state) == "on"


def test_resolve_missing_attribute_or_entity_is_none() -> None:
    assert overlay_sync.resolve_slot_value("ha:light.desk:attributes.nope", _get_state) is None
    assert overlay_sync.resolve_slot_value("ha:switch.unknown", _get_state) is None
    assert overlay_sync.resolve_slot_value("ha:sensor.gone", _get_state) is None
    assert overlay_sync.resolve_slot_value("weather:sydney", _get_state) is None


def test_values_document_applies_slot_map_then_suffix() -> None:
    slots = [
        {"key": "ha:light.desk", "map": {"on": "1", "off": "0"}, "suffix": ""},
        {"key": "ha:sensor.temp", "suffix": "°"},
        {"key": "ha:light.desk:attributes.brightness", "suffix": ""},
    ]
    doc = overlay_sync.values_document(slots, ha_get_state=_get_state, now=1000.0)
    assert doc["seq"] == 1_000_000  # milliseconds
    assert doc["values"] == {
        "ha:light.desk": "1",
        "ha:sensor.temp": "21.4°",
        "ha:light.desk:attributes.brightness": "128",
    }


def test_values_document_unmapped_value_falls_back_to_raw() -> None:
    slots = [{"key": "ha:light.desk", "map": {"off": "0"}, "suffix": ""}]
    doc = overlay_sync.values_document(slots, ha_get_state=_get_state, now=1.0)
    assert doc["values"] == {"ha:light.desk": "on"}
