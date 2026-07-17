"""Touch region unit tests (issue #49): extraction normalisation,
sidecar round-trip, stroke classification, hit-testing, and the grid
composer's ``data-on-tap`` emission."""

from __future__ import annotations

from pathlib import Path

from flask import Flask

from app.touch_regions import (
    TAP_RADIUS_PX,
    classify_stroke,
    hit_test,
    load_regions,
    normalize_regions,
    regions_sidecar_path,
    resolve_gesture_action,
    save_regions,
)

# -- normalisation -------------------------------------------------------


def test_normalize_regions_parses_browser_payload() -> None:
    raw = [
        {
            "x": 10,
            "y": 20,
            "w": 100,
            "h": 50,
            "depth": 2,
            "order": 0,
            "tap": "page:forecast",
            "swipe": '{"up": "rotate_next", "down": "rotate_prev", "diagonal": "nope"}',
            "slide": None,
        },
        # Zero-area boxes are dropped.
        {"x": 0, "y": 0, "w": 0, "h": 50, "tap": "refresh", "swipe": None, "slide": None},
        # No actions at all: dropped.
        {"x": 0, "y": 0, "w": 10, "h": 10, "tap": None, "swipe": None, "slide": None},
        # Malformed box: dropped, not raised.
        {"x": "nan", "y": 0, "w": 10, "h": 10, "tap": "refresh"},
        "not-a-dict",
    ]
    regions = normalize_regions(raw)
    assert len(regions) == 1
    region = regions[0]
    assert region["tap"] == "page:forecast"
    # Unknown swipe directions are dropped; known ones survive.
    assert region["swipe"] == {"up": "rotate_next", "down": "rotate_prev"}


def test_normalize_regions_tolerates_garbage_root() -> None:
    assert normalize_regions(None) == []
    assert normalize_regions("boom") == []
    assert normalize_regions(42) == []


def test_normalize_regions_bad_swipe_json_reads_as_no_swipe() -> None:
    raw = [{"x": 0, "y": 0, "w": 10, "h": 10, "tap": "refresh", "swipe": "{not json"}]
    regions = normalize_regions(raw)
    assert regions[0]["swipe"] is None
    assert regions[0]["tap"] == "refresh"


def test_normalize_regions_origin_and_dangling_passthrough() -> None:
    raw = [
        {"x": 0, "y": 0, "w": 10, "h": 10, "tap": "refresh", "origin": "config"},
        {"x": 0, "y": 20, "w": 10, "h": 10, "tap": "refresh", "origin": "hax"},
        # A region whose only content is a dangling @ref survives so the
        # editor overlay / render_report can flag it.
        {"x": 0, "y": 40, "w": 10, "h": 10, "tap": None, "dangling": ["dim", 42]},
    ]
    regions = normalize_regions(raw)
    assert regions[0]["origin"] == "config"
    assert regions[1]["origin"] == "markup"  # anything non-config reads as markup
    assert regions[2]["dangling"] == ["dim"]
    assert regions[2]["tap"] is None


def test_is_side_effecting() -> None:
    from app.touch_regions import is_side_effecting

    assert is_side_effecting("webhook:https://x.example/y")
    assert is_side_effecting("ha:whatever")
    assert not is_side_effecting("page:morning")
    assert not is_side_effecting("refresh")
    assert not is_side_effecting("rotate_next")


# -- sidecar round-trip --------------------------------------------------


def test_save_and_load_regions_round_trip(tmp_path: Path) -> None:
    regions = [
        {
            "x": 1,
            "y": 2,
            "w": 3,
            "h": 4,
            "depth": 0,
            "order": 0,
            "tap": "refresh",
            "swipe": None,
            "slide": None,
        },
    ]
    save_regions(tmp_path, "abc123", regions)
    assert load_regions(tmp_path, "abc123") == regions


def test_load_regions_missing_or_malformed_reads_empty(tmp_path: Path) -> None:
    assert load_regions(tmp_path, "nope") == []
    regions_sidecar_path(tmp_path, "bad").write_text("{not json", encoding="utf-8")
    assert load_regions(tmp_path, "bad") == []


def test_save_regions_overwrites_previous_map(tmp_path: Path) -> None:
    """Same pixels can carry different annotations; the latest render's
    map must win."""
    old = [
        {
            "x": 0,
            "y": 0,
            "w": 5,
            "h": 5,
            "depth": 0,
            "order": 0,
            "tap": "refresh",
            "swipe": None,
            "slide": None,
        }
    ]
    save_regions(tmp_path, "d1", old)
    save_regions(tmp_path, "d1", [])
    assert load_regions(tmp_path, "d1") == []


# -- stroke classification -----------------------------------------------


def test_classify_stroke_tap_within_radius() -> None:
    assert classify_stroke(100, 100, 100, 100) == ("tap", 0)
    assert classify_stroke(100, 100, 100 + TAP_RADIUS_PX, 100) == ("tap", 0)


def test_classify_stroke_swipe_directions() -> None:
    assert classify_stroke(100, 300, 100, 100) == ("swipe_up", 200)
    assert classify_stroke(100, 100, 100, 350) == ("swipe_down", 250)
    assert classify_stroke(400, 100, 100, 100) == ("swipe_left", 300)
    assert classify_stroke(100, 100, 500, 130) == ("swipe_right", 400)


def test_classify_stroke_dominant_axis_wins() -> None:
    gesture, magnitude = classify_stroke(0, 0, 120, 80)
    assert gesture == "swipe_right"
    assert magnitude == 120


# -- hit-testing ---------------------------------------------------------


def _region(
    x: int, y: int, w: int, h: int, *, depth: int = 0, order: int = 0, tap: str | None = "refresh"
) -> dict:
    return {
        "x": x,
        "y": y,
        "w": w,
        "h": h,
        "depth": depth,
        "order": order,
        "tap": tap,
        "swipe": None,
        "slide": None,
    }


def test_hit_test_misses_outside_all_regions() -> None:
    assert hit_test([_region(0, 0, 100, 100)], 200, 50) is None
    assert hit_test([], 10, 10) is None


def test_hit_test_deepest_region_wins() -> None:
    outer = _region(0, 0, 400, 300, depth=1, order=0, tap="page:outer")
    inner = _region(100, 100, 50, 50, depth=4, order=1, tap="page:inner")
    hit = hit_test([outer, inner], 120, 120)
    assert hit is not None and hit["tap"] == "page:inner"
    # Outside the inner box, the outer region still catches.
    hit = hit_test([outer, inner], 10, 10)
    assert hit is not None and hit["tap"] == "page:outer"


def test_hit_test_document_order_breaks_depth_ties() -> None:
    first = _region(0, 0, 100, 100, depth=2, order=0, tap="page:first")
    second = _region(50, 0, 100, 100, depth=2, order=1, tap="page:second")
    hit = hit_test([first, second], 75, 50)
    assert hit is not None and hit["tap"] == "page:second"


def test_hit_test_edges_are_half_open() -> None:
    region = _region(10, 10, 100, 100)
    assert hit_test([region], 10, 10) is not None  # top-left inclusive
    assert hit_test([region], 110, 110) is None  # bottom-right exclusive


# -- gesture -> action resolution ----------------------------------------


def test_resolve_gesture_action_tap_and_swipe() -> None:
    region = {
        "tap": "page:forecast",
        "swipe": {"up": "rotate_next", "left": "webhook:https://hook.example/x"},
    }
    assert resolve_gesture_action(region, "tap") == "page:forecast"
    assert resolve_gesture_action(region, "swipe_up") == "rotate_next"
    assert resolve_gesture_action(region, "swipe_left") == "webhook:https://hook.example/x"
    assert resolve_gesture_action(region, "swipe_down") is None


def test_resolve_gesture_action_object_form_is_inert() -> None:
    """The structured (JSON) action form is reserved; it must no-op, not
    error, so markup written for a future server degrades cleanly."""
    region = {"tap": '{"action": "ha", "domain": "light"}', "swipe": None}
    assert resolve_gesture_action(region, "tap") is None


def test_resolve_gesture_action_missing_declarations() -> None:
    assert resolve_gesture_action({"tap": None, "swipe": None}, "tap") is None
    assert resolve_gesture_action({}, "swipe_up") is None


# -- grid composer emission ----------------------------------------------


def test_compose_emits_cell_touch_attributes(app: Flask) -> None:
    """A grid cell's ``on_tap`` / ``on_swipe`` land as ``data-on-tap`` /
    ``data-on-swipe`` on the cell container, ready for extraction."""
    from app.state.page_store import Cell, Page

    page = Page(
        id="touchy",
        name="Touchy",
        layout_kind="grid",
        cells=[
            Cell(
                id="c1",
                plugin="clock_word",
                x=0,
                y=0,
                w=300,
                h=200,
                on_tap="page:forecast",
                on_swipe={"up": "rotate_next"},
            ),
            Cell(id="c2", plugin="clock_word", x=300, y=0, w=300, h=200),
        ],
    )
    app.config["PAGE_STORE"].save(page)
    body = app.test_client().get("/compose/touchy").get_data(as_text=True)
    assert 'data-on-tap="page:forecast"' in body
    assert "data-on-swipe=" in body and "rotate_next" in body
    # The unannotated cell gets no touch attributes.
    assert body.count("data-on-tap=") == 1


def test_compose_widget_manifest_on_tap_is_cell_default(app: Flask) -> None:
    """A widget manifest ``on_tap`` becomes the cell's default action;
    the per-cell override wins over it."""
    from app.state.page_store import Cell, Page

    registry = app.config["PLUGIN_REGISTRY"]
    clock = registry.get("clock_word")
    assert clock is not None
    clock.manifest["on_tap"] = "refresh"
    try:
        page = Page(
            id="deft",
            name="Default",
            layout_kind="grid",
            cells=[
                Cell(id="c1", plugin="clock_word", x=0, y=0, w=300, h=200),
                Cell(
                    id="c2", plugin="clock_word", x=300, y=0, w=300, h=200, on_tap="page:override"
                ),
            ],
        )
        app.config["PAGE_STORE"].save(page)
        body = app.test_client().get("/compose/deft").get_data(as_text=True)
        assert 'data-on-tap="refresh"' in body
        assert 'data-on-tap="page:override"' in body
    finally:
        clock.manifest.pop("on_tap", None)
