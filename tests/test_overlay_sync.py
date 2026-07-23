"""Unit tests for app.overlay_sync: capability parsing and the
composition-to-wire rect transform that mirrors the .bin renderer's
chain (rotate on orientation mismatch, 180 flip, scale to native,
underscan inset). REST endpoint coverage lives in
tests/test_rest_overlay.py."""

from __future__ import annotations

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


# -- build_spec --------------------------------------------------------------


PANEL = {
    "w": 1872,
    "h": 1404,
    "orientation": "landscape",
    "native_w": 1872,
    "native_h": 1404,
}


def _region(x: int, y: int, w: int = 100, h: int = 50) -> dict:
    return {"x": x, "y": y, "w": w, "h": h, "on_tap": "refresh"}


def test_build_spec_shapes_targets() -> None:
    spec = overlay_sync.build_spec(
        frame_digest="a" * 16,
        regions=[_region(120, 640, 300, 90)],
        panel=PANEL,
    )
    assert spec == {
        "schema": 1,
        "frame_digest": "a" * 16,
        "targets": [{"id": "t1", "x": 120, "y": 640, "w": 300, "h": 90, "echo": "invert"}],
    }


def test_build_spec_caps_targets_at_firmware_limit() -> None:
    regions = [_region(10 * i, 10) for i in range(12)]
    spec = overlay_sync.build_spec(frame_digest="b" * 16, regions=regions, panel=PANEL)
    assert spec is not None
    assert len(spec["targets"]) == overlay_sync.MAX_TARGETS
    assert [t["id"] for t in spec["targets"]] == [f"t{i}" for i in range(1, 9)]


def test_build_spec_drops_malformed_and_offscreen_regions() -> None:
    regions = [
        {"x": "nan-ish"},  # malformed
        _region(5000, 5000),  # off-panel
        _region(100, 100),  # good
    ]
    spec = overlay_sync.build_spec(frame_digest="c" * 16, regions=regions, panel=PANEL)
    assert spec is not None
    assert len(spec["targets"]) == 1
    assert spec["targets"][0]["x"] == 100


def test_build_spec_none_on_bad_panel() -> None:
    assert overlay_sync.build_spec(frame_digest="d" * 16, regions=[], panel={}) is None


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


def test_build_spec_honours_device_target_budget() -> None:
    regions = [_region(10 * i, 10) for i in range(20)]
    spec = overlay_sync.build_spec(
        frame_digest="e" * 16, regions=regions, panel=PANEL, max_targets=32
    )
    assert spec is not None and len(spec["targets"]) == 20  # under budget: all emit
    spec8 = overlay_sync.build_spec(
        frame_digest="e" * 16, regions=regions, panel=PANEL, max_targets=8
    )
    assert spec8 is not None and len(spec8["targets"]) == 8


def test_trim_prioritizes_nav_targets_in_document_order() -> None:
    # 10 regions, budget 8: two nav regions sit LAST in document order and
    # must survive the trim; the two dropped are the last non-nav ones.
    regions = [dict(_region(10 * i, 10), tap="refresh") for i in range(8)]
    regions.append(dict(_region(200, 10), tap="page:kitchen"))
    regions.append(dict(_region(220, 10), swipe={"left": "rotate_next"}))
    spec = overlay_sync.build_spec(
        frame_digest="f" * 16, regions=regions, panel=PANEL, max_targets=8
    )
    assert spec is not None
    xs = [t["x"] for t in spec["targets"]]
    # Nav rects (x=200, 220) survived; the last two non-nav (x=60, 70) dropped.
    assert 200 in xs and 220 in xs
    assert 60 not in xs and 70 not in xs
    # Survivors are emitted in document order (x ascending here).
    assert xs == sorted(xs)
