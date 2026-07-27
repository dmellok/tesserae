"""Touch-v3 wire spec builder: elements -> spec, validated against the schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.state.panel_store import Element
from app.touch_spec import build_frame_spec, classify_action, touch_layout_digest, wire_transform

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


def _validator() -> Draft202012Validator:
    spec = json.loads((_SCHEMA_DIR / "frame-spec.schema.json").read_text())
    atlas = json.loads((_SCHEMA_DIR / "atlas.schema.json").read_text())
    reg = Registry().with_resources([("atlas.schema.json", Resource.from_contents(atlas))])
    return Draft202012Validator(spec, registry=reg)


def _sample_els() -> list[Element]:
    return [
        Element(id="w1", kind="widget", widget="weather_now"),  # not a primitive
        Element(
            id="btn",
            kind="button",
            x=40,
            y=900,
            w=180,
            h=80,
            label="Movie",
            icon="film-slate",
            weight="duotone",
            on_tap="page:scenes",
        ),
        Element(
            id="sw",
            kind="switch",
            x=240,
            y=900,
            w=160,
            h=80,
            label="Desk",
            value_key="ha:light.desk",
            state="on",
        ),
        Element(
            id="sl",
            kind="slider",
            x=40,
            y=1000,
            w=360,
            h=70,
            axis="x",
            value_key="ha:light.desk:attributes.brightness_pct",
            value_min=0,
            value_max=100,
            value_step=5,
            value_now=60,
        ),
        Element(
            id="st",
            kind="stepper",
            x=420,
            y=1000,
            w=160,
            h=70,
            value_key="ha:media.vol",
            value_min=0,
            value_max=30,
            value_now=12,
        ),
    ]


def test_full_spec_validates_against_schema() -> None:
    doc = build_frame_spec(_sample_els())
    errors = sorted(_validator().iter_errors(doc), key=lambda e: list(e.path))
    assert not errors, errors[0].message if errors else ""
    # The plain widget is dropped; the four primitives remain, in order.
    assert [p["id"] for p in doc["primitives"]] == ["btn", "sw", "sl", "st"]
    assert isinstance(doc["layout_digest"], str) and len(doc["layout_digest"]) == 16


def test_layout_digest_stable_across_data_changes() -> None:
    # Flipping a switch and moving a slider are DATA changes, not layout changes,
    # so the layout digest must not move (a clock tick can't invalidate touch).
    base = build_frame_spec(_sample_els())
    els = _sample_els()
    for el in els:
        if el.id == "sw":
            el.state = "off"
        if el.id == "sl":
            el.value_now = 15
    changed = build_frame_spec(els)
    assert changed["layout_digest"] == base["layout_digest"]


def test_layout_digest_moves_on_structural_change() -> None:
    base = build_frame_spec(_sample_els())
    els = _sample_els()
    els.append(Element(id="btn2", kind="button", x=0, y=0, w=50, h=50, on_tap="refresh"))
    assert build_frame_spec(els)["layout_digest"] != base["layout_digest"]


def test_button_action_classified() -> None:
    doc = build_frame_spec([Element(id="b", kind="button", w=10, h=10, on_tap="ha:light.toggle")])
    assert doc["primitives"][0]["action"] == {"tier": 1, "type": "ha"}


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("page:home", (0, "nav")),
        ("rotate_next", (0, "nav")),
        ("step:2", (0, "nav")),
        ("ha:light.toggle", (1, "ha")),
        ({"action": "ha", "domain": "light", "service": "toggle"}, (1, "ha")),
        ("webhook:https://x", (2, "webhook")),
        ("refresh", (2, "refresh")),
        ("fetch_latest", (2, "fetch")),
        (None, (2, "nav")),
    ],
)
def test_classify_action(spec: object, expected: tuple[int, str]) -> None:
    assert classify_action(spec) == expected  # type: ignore[arg-type]


def test_invalid_primitives_are_skipped() -> None:
    els = [
        Element(id="sw_no_bind", kind="switch", w=10, h=10),  # no value_key
        Element(id="btn_no_action", kind="button", w=10, h=10),  # no on_tap
        Element(id="sl_no_axis", kind="slider", w=10, h=10),  # axis not x/y
        Element(id="off", kind="button", x=-5, y=0, w=10, h=10, on_tap="refresh"),  # off-panel
        Element(id="ok", kind="button", w=10, h=10, on_tap="refresh"),  # valid
    ]
    doc = build_frame_spec(els)
    assert [p["id"] for p in doc["primitives"]] == ["ok"]


def test_empty_layout_yields_no_primitives() -> None:
    doc = build_frame_spec([])
    assert doc["primitives"] == []
    assert doc["layout_digest"] == touch_layout_digest([])


def test_wire_transform_identity_when_canvas_matches_panel() -> None:
    # Canvas dims == composition dims, native == comp, no flip/underscan: the
    # rect passes through unchanged.
    wire = wire_transform({"w": 600, "h": 400}, 600, 400)
    assert wire is not None
    assert wire(10, 20, 100, 50) == (10, 20, 100, 50)


def test_wire_transform_scales_canvas_to_panel() -> None:
    # Canvas 300x200 authored, panel composition 600x400: a 2x scale each axis.
    wire = wire_transform({"w": 600, "h": 400}, 300, 200)
    assert wire is not None
    assert wire(10, 20, 100, 50) == (20, 40, 200, 100)


def test_build_frame_spec_emits_wired_rects() -> None:
    wire = wire_transform({"w": 1200, "h": 800}, 600, 400)  # 2x
    els = [Element(id="b", kind="button", x=10, y=10, w=100, h=50, on_tap="refresh")]
    doc = build_frame_spec(els, wire=wire)
    assert doc["primitives"][0]["rect"] == {"x": 20, "y": 20, "w": 200, "h": 100}


def test_wire_transform_none_for_bad_panel() -> None:
    assert wire_transform({}, 600, 400) is None
    assert wire_transform({"w": 600, "h": 400}, 0, 0) is None
