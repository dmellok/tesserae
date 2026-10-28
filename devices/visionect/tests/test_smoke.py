"""visionect smoke tests: the kind loads, heartbeats decode leniently and
the UUID config is validated."""

from __future__ import annotations

import json

import pytest

from app.device_loader import discover
from app.main import REPO_ROOT


@pytest.fixture
def visionect(tmp_path):
    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    assert registry.errors == [], registry.errors
    d = registry.get("visionect")
    assert d is not None
    return d


def test_kind_is_a_push_device_on_the_grey_renderer(visionect) -> None:
    assert visionect.manifest["transport"] == "push"
    assert visionect.renderer_ids == ["trmnl_png_gray16"]
    assert visionect.manifest["panel"]["native_w"] == 1440
    assert visionect.manifest["panel"]["native_h"] == 2560


def test_parse_status_keeps_numeric_readings(visionect) -> None:
    beat = {
        "battery_pct": 61,
        "battery_mv": 3804,
        "rssi": -58,
        "temperature_c": 23,
        "uptime_s": 9840,
        "fw_version": "6.17.4168",
        "uuid": "36002000-1247-3830-3933-303600000000",
        "battery_pct_bogus": "x",
    }
    parsed = visionect.parse_status(json.dumps(beat).encode("utf-8"))
    assert parsed["battery_pct"] == 61
    assert parsed["rssi"] == -58
    assert parsed["fw_version"] == "6.17.4168"
    assert "battery_pct_bogus" not in parsed


def test_parse_status_tolerates_garbage(visionect) -> None:
    assert visionect.parse_status(b"") == {}
    assert "error" in visionect.parse_status(b"not json")
    assert "error" in visionect.parse_status(b"[1, 2]")


@pytest.mark.parametrize(
    ("uuid", "ok"),
    [
        ("", True),
        ("36002000-1247-3830-3933-303600000000", True),
        ("36002000124738303933303600000000", True),
        ("36002000-1247", False),
        ("zz002000-1247-3830-3933-303600000000", False),
    ],
)
def test_validate_config_checks_the_uuid(visionect, uuid: str, ok: bool) -> None:
    assert visionect.validate_config({"uuid": uuid})[0] is ok


def test_catalog_models_load_on_the_visionect_protocol(tmp_path) -> None:
    from app import visionect as visionect_app
    from app.hardware_catalog import apply_to_registry, discover_hardware

    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    entries, errors = discover_hardware(
        REPO_ROOT / "hardware", schema_path=REPO_ROOT / "schema" / "hardware.schema.json"
    )
    assert errors == []
    apply_to_registry(registry, entries, data_root=tmp_path)
    sizes = {
        "visionect_place_and_play_32": (1440, 2560),
        "visionect_place_and_play_13": (1600, 1200),
        "visionect_joan_6": (1024, 758),
        "visionect_joan_13": (1600, 1200),
        "visionect_v_tablet_9_7": (1200, 825),
    }
    for kind_id, (w, h) in sizes.items():
        kind = registry.get(kind_id)
        assert kind is not None, kind_id
        assert visionect_app.is_visionect_device(kind), kind_id
        assert kind.manifest["transport"] == "push", kind_id
        assert (kind.panel["native_w"], kind.panel["native_h"]) == (w, h), kind_id


def test_only_the_place_and_play_32_declares_no_buttons(tmp_path) -> None:
    """The Place & Play 32" has no buttons or touch, so it gets no button map
    or Home Assistant Input entity. The other models keep the default."""
    from app.device_loader import has_buttons, has_touch, takes_input
    from app.hardware_catalog import apply_to_registry, discover_hardware

    registry = discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=tmp_path,
    )
    entries, errors = discover_hardware(
        REPO_ROOT / "hardware", schema_path=REPO_ROOT / "schema" / "hardware.schema.json"
    )
    assert errors == []
    apply_to_registry(registry, entries, data_root=tmp_path)
    kinds = ["visionect"] + [e.id for e in entries if e.protocol == "visionect"]
    assert len(kinds) == 6
    p32 = registry.get("visionect_place_and_play_32")
    assert not has_buttons(p32) and not has_touch(p32) and not takes_input(p32)
    for kind_id in kinds:
        if kind_id != "visionect_place_and_play_32":
            assert has_buttons(registry.get(kind_id)), kind_id
