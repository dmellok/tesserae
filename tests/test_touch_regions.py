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


def test_normalize_regions_flags_undispatchable_action() -> None:
    # A region whose tap action can't dispatch is flagged in "invalid",
    # so render_report / the overlay don't green-light a dead dashboard.
    raw = [
        {"x": 0, "y": 0, "w": 10, "h": 10, "tap": "page:home", "origin": "config"},
        {"x": 0, "y": 20, "w": 10, "h": 10, "tap": "warp:9", "origin": "config"},
    ]
    regions = normalize_regions(raw)
    assert regions[0]["invalid"] == []
    assert regions[1]["invalid"] and regions[1]["invalid"][0]["gesture"] == "tap"


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


def test_resolve_gesture_action_actionless_object_is_inert() -> None:
    """A JSON value with no ``action`` name must no-op, not error, so
    markup written for a future server degrades cleanly."""
    region = {"tap": '{"domain": "light"}', "swipe": None}
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


# -- structured actions + slide (phase 3) --------------------------------


def test_coerce_action_forms() -> None:
    from app.touch_regions import coerce_action

    assert coerce_action("page:x") == "page:x"
    assert coerce_action("  refresh  ") == "refresh"
    ha = '{"action": "ha", "domain": "light", "service": "toggle"}'
    parsed = coerce_action(ha)
    assert isinstance(parsed, dict) and parsed["domain"] == "light"
    # HA actions canonicalise to {action, domain, service, data}.
    assert coerce_action({"action": "ha", "domain": "light", "service": "toggle"}) == {
        "action": "ha",
        "domain": "light",
        "service": "toggle",
        "data": {},
    }
    # Malformed / actionless-and-serviceless values read as None, never raise.
    assert coerce_action("{not json") is None
    assert coerce_action('{"domain": "light"}') is None  # no service -> not an HA call
    assert coerce_action(None) is None
    assert coerce_action(42) is None


def test_coerce_action_tolerates_natural_ha_shapes() -> None:
    """The shapes an agent naturally writes all normalise to the canonical
    HA form: no ``action`` key (inferred from ``service``), ``entity_id`` at
    the top level, a dotted service, or the HA-native ``target``."""
    from app.touch_regions import coerce_action

    # No "action" key + top-level entity_id (the shape that silently no-op'd).
    got = coerce_action(
        {
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.desk",
            "data": {"brightness_pct": "{value}"},
        }
    )
    assert got == {
        "action": "ha",
        "domain": "light",
        "service": "turn_on",
        "data": {"brightness_pct": "{value}", "entity_id": "light.desk"},
    }
    # Dotted service + HA-native target.
    got = coerce_action({"service": "light.toggle", "target": {"entity_id": "light.x"}})
    assert got == {
        "action": "ha",
        "domain": "light",
        "service": "toggle",
        "data": {"entity_id": "light.x"},
    }


def test_action_invalid_reason() -> None:
    from app.touch_regions import action_invalid_reason

    # Dispatchable -> no reason.
    assert action_invalid_reason("page:home") is None
    assert action_invalid_reason("refresh") is None
    assert action_invalid_reason({"domain": "light", "service": "toggle"}) is None
    # Undispatchable -> a reason (the honest-verification signal).
    assert action_invalid_reason("frobnicate:9") is not None
    assert action_invalid_reason({"action": "ha", "domain": "light"}) is not None  # no service
    assert action_invalid_reason({"foo": "bar"}) is not None
    assert action_invalid_reason("{not json") is not None


def test_resolve_gesture_action_returns_structured_dict() -> None:
    region = {"tap": '{"action": "ha", "domain": "light", "service": "toggle"}'}
    spec = resolve_gesture_action(region, "tap")
    assert isinstance(spec, dict) and spec["action"] == "ha"


def test_slide_declaration_axis_defaults_from_aspect() -> None:
    from app.touch_regions import slide_declaration

    wide = {"w": 300, "h": 40, "slide": {"action": "webhook:http://x/{value}"}}
    tall = {"w": 40, "h": 300, "slide": {"action": "webhook:http://x/{value}"}}
    assert slide_declaration(wide)["axis"] == "x"
    assert slide_declaration(tall)["axis"] == "y"
    explicit = {"w": 300, "h": 40, "slide": {"axis": "y", "action": "refresh"}}
    assert slide_declaration(explicit)["axis"] == "y"
    assert slide_declaration({"slide": None}) is None
    assert slide_declaration({"slide": {"axis": "y"}}) is None  # no action


def test_slide_value_axes_and_clamping() -> None:
    from app.touch_regions import slide_value

    region = {"x": 100, "y": 100, "w": 200, "h": 100}
    # Horizontal: left = 0, right = 100.
    assert slide_value(region, "x", 100, 150) == 0
    assert slide_value(region, "x", 300, 150) == 100
    assert slide_value(region, "x", 200, 150) == 50
    # Vertical: bottom = 0, top = 100 (fills upward).
    assert slide_value(region, "y", 150, 200) == 0
    assert slide_value(region, "y", 150, 100) == 100
    # Points past the edges pin to the end stops.
    assert slide_value(region, "x", 50, 150) == 0
    assert slide_value(region, "x", 999, 150) == 100


def test_substitute_value_string_and_dict() -> None:
    from app.touch_regions import substitute_value

    assert substitute_value("webhook:http://x/y?level={value}", 40) == "webhook:http://x/y?level=40"
    action = {
        "action": "ha",
        "domain": "light",
        "service": "turn_on",
        "data": {"entity_id": "light.x", "brightness_pct": "{value}", "note": "set to {value}%"},
    }
    out = substitute_value(action, 65)
    assert isinstance(out, dict)
    # Exact-match placeholder becomes a real number; embedded stays text.
    assert out["data"]["brightness_pct"] == 65
    assert out["data"]["note"] == "set to 65%"
    # The original action is untouched (deep copy).
    assert action["data"]["brightness_pct"] == "{value}"


def test_canonical_action_normalises_flat_ha_and_keeps_strings() -> None:
    from app.touch_regions import canonical_action, canonical_slide, canonical_swipe

    got = canonical_action(
        {"service": "light.turn_on", "entity_id": "light.x", "brightness_pct": 40}
    )
    assert got == {
        "action": "ha",
        "domain": "light",
        "service": "turn_on",
        "data": {"entity_id": "light.x", "brightness_pct": 40},
    }
    assert canonical_action("page:home") == "page:home"
    assert canonical_action("") is None and canonical_action(None) is None
    # Unrecognisable dict is kept (not silently dropped) so it can be flagged.
    assert canonical_action({"foo": "bar"}) == {"foo": "bar"}
    # Swipe values canonicalise per direction; empty map -> None.
    assert canonical_swipe({"left": {"service": "switch.toggle"}})["left"]["service"] == "toggle"
    assert canonical_swipe({}) is None
    # Slide canonicalises the inner action.
    slide = canonical_slide({"axis": "y", "action": {"service": "light.turn_on"}})
    assert slide["action"]["action"] == "ha" and slide["axis"] == "y"


def test_element_model_canonicalises_touch_on_write() -> None:
    """The Element model stores touch actions canonically so the canvas
    editor's Interaction panel can decode an agent-written flat HA action
    instead of showing it blank (issue #49)."""
    from app.state.panel_store import Element

    el = Element.model_validate(
        {
            "id": "e1",
            "kind": "box",
            "x": 0,
            "y": 0,
            "w": 10,
            "h": 10,
            "on_tap": {
                "service": "light.turn_on",
                "entity_id": ["light.hall"],
                "brightness_pct": 50,
            },
            "on_swipe": {"left": {"service": "light.toggle", "entity_id": "light.x"}},
        }
    )
    assert el.on_tap["action"] == "ha"
    assert el.on_tap["data"]["brightness_pct"] == 50
    assert el.on_swipe["left"]["action"] == "ha"
    # Idempotent: re-validating the canonical form doesn't double-wrap.
    assert Element.model_validate(el.model_dump()).on_tap == el.on_tap


def test_cell_model_canonicalises_touch_on_write() -> None:
    from app.state.page_store import Cell

    cell = Cell.model_validate(
        {
            "id": "c1",
            "plugin": "clock",
            "x": 0,
            "y": 0,
            "w": 1,
            "h": 1,
            "on_tap": {"service": "switch.toggle", "entity_id": "switch.fan"},
        }
    )
    assert cell.on_tap == {
        "action": "ha",
        "domain": "switch",
        "service": "toggle",
        "data": {"entity_id": "switch.fan"},
    }


def test_coerce_action_hoists_flat_service_data() -> None:
    """The flat HA shape puts service data (brightness_pct, …) at the top
    level beside entity_id. It must be folded into ``data`` so the call
    dispatches with the data, not just the entity (issue #49 agent feedback:
    a combined {service, entity_id, brightness_pct} validated OK but the
    brightness silently vanished)."""
    from app.touch_regions import coerce_action

    got = coerce_action(
        {"service": "light.turn_on", "entity_id": ["light.hall"], "brightness_pct": 50}
    )
    assert got == {
        "action": "ha",
        "domain": "light",
        "service": "turn_on",
        "data": {"entity_id": ["light.hall"], "brightness_pct": 50},
    }


def test_coerce_action_explicit_data_wins_over_hoist() -> None:
    from app.touch_regions import coerce_action

    got = coerce_action(
        {"service": "light.turn_on", "brightness_pct": 10, "data": {"brightness_pct": 99}}
    )
    assert got["data"]["brightness_pct"] == 99  # explicit data block wins


def test_coerce_action_splits_csv_entity_id_into_list() -> None:
    """HA wants a list of entity ids; a comma-joined string is a common
    hand-written shape and must normalise to the array HA accepts."""
    from app.touch_regions import coerce_action

    got = coerce_action({"service": "light.toggle", "entity_id": "light.a, light.b"})
    assert got["data"]["entity_id"] == ["light.a", "light.b"]


def test_parse_swipe_attr_accepts_structured_ha_per_direction() -> None:
    """A swipe direction can carry a structured HA object, the same form
    on_tap accepts (issue #49 agent feedback: structured swipe was dropped
    to null with no diagnostic)."""
    from app.touch_regions import _parse_swipe_attr

    parsed = _parse_swipe_attr('{"left": {"action": "ha", "domain": "light", "service": "toggle"}}')
    assert parsed == {"left": {"action": "ha", "domain": "light", "service": "toggle"}}
    # String specs still work.
    assert _parse_swipe_attr('{"right": "rotate_next"}') == {"right": "rotate_next"}


def test_normalize_regions_flags_directionless_swipe() -> None:
    """An inline swipe object with no up/down/left/right key can't fire; it
    must surface a diagnostic in ``invalid`` instead of being silently
    dropped (issue #49 agent feedback)."""
    from app.touch_regions import normalize_regions

    regions = normalize_regions(
        [
            {
                "x": 0,
                "y": 0,
                "w": 100,
                "h": 100,
                "swipe": '{"service": "light.toggle", "entity_id": "light.x"}',
            }
        ]
    )
    assert len(regions) == 1
    assert regions[0]["swipe"] is None
    reasons = [i for i in regions[0]["invalid"] if i["gesture"] == "swipe"]
    assert reasons and "per-direction" in reasons[0]["reason"]


def test_normalize_regions_structured_swipe_roundtrips() -> None:
    from app.touch_regions import normalize_regions

    regions = normalize_regions(
        [
            {
                "x": 0,
                "y": 0,
                "w": 100,
                "h": 100,
                "swipe": '{"left": {"action": "ha", "domain": "light", "service": "toggle"}}',
            }
        ]
    )
    assert regions[0]["swipe"]["left"]["service"] == "toggle"
    assert regions[0]["invalid"] == []  # structured swipe is dispatchable
