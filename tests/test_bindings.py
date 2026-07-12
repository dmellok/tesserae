"""Live data bindings for canvas shapes: the six transforms as pure functions,
plus the composer applying them so a bound shape reflects data on each render."""

from __future__ import annotations

from typing import Any

from flask import Flask

from app import bindings, composer
from app.state.panel_store import Binding, Element


def _bind(field: str, transform: str, params: dict[str, Any]) -> Binding:
    return Binding(source="weather_now", field=field, transform=transform, params=params)


# -- path resolver ------------------------------------------------------


def test_resolve_path_dot_and_index() -> None:
    data = {"sun": {"nowMin": 720}, "arr": [{"v": 3}, {"v": 9}]}
    assert bindings._resolve_path(data, "sun.nowMin") == 720
    assert bindings._resolve_path(data, "arr.1.v") == 9
    assert bindings._resolve_path(data, "arr[0].v") == 3
    assert bindings._resolve_path(data, "sun.missing") is None
    assert bindings._resolve_path(data, "arr.9.v") is None


# -- transforms ---------------------------------------------------------


def test_position_maps_scalar_along_segment_with_field_bounds() -> None:
    data = {"sun": {"nowMin": 720, "riseMin": 420, "setMin": 1020}}
    b = _bind(
        "sun.nowMin",
        "position",
        {"axis": "x", "in": ["sun.riseMin", "sun.setMin"], "out": [210, 940], "center": 22},
    )
    # t = (720-420)/(1020-420) = 0.5 -> 210 + 0.5*730 - 11 = 564
    assert bindings.apply_binding(b, data) == {"x": 564}


def test_length_grows_and_can_anchor() -> None:
    data = {"pct": 50}
    grow = _bind("pct", "length", {"dim": "w", "in": [0, 100], "out": [10, 200]})
    assert bindings.apply_binding(grow, data) == {"w": 105}
    anchored = _bind(
        "pct", "length", {"dim": "w", "in": [0, 100], "out": [10, 200], "anchorMax": 300}
    )
    assert bindings.apply_binding(anchored, data) == {"w": 105, "x": 195}


def test_pick_indexes_arrays_with_center() -> None:
    data = {"bandIndex": 2}
    b = _bind("bandIndex", "pick", {"set": {"x": [109, 303, 497, 691, 885, 1078]}})
    assert bindings.apply_binding(b, data) == {"x": 497}
    centered = _bind("bandIndex", "pick", {"set": {"x": [109, 303, 497]}, "center": 14})
    assert bindings.apply_binding(centered, data) == {"x": 490}  # 497 - 7


def test_color_thresholds() -> None:
    stops = {"stops": [[5, "#4a5aa8"], [15, "#1f8378"], [25, "#c08a2c"]], "else": "#900000"}
    assert bindings.apply_binding(_bind("t", "color", stops), {"t": 3}) == {"color": "#4a5aa8"}
    assert bindings.apply_binding(_bind("t", "color", stops), {"t": 20}) == {"color": "#c08a2c"}
    assert bindings.apply_binding(_bind("t", "color", stops), {"t": 40}) == {"color": "#900000"}


def test_gradient_interpolates_between_stops() -> None:
    two = _bind("v", "gradient", {"stops": [[0, "#000000"], [100, "#ffffff"]]})
    assert bindings.apply_binding(two, {"v": 50}) == {"color": "#808080"}
    assert bindings.apply_binding(two, {"v": -10}) == {"color": "#000000"}  # clamped low
    assert bindings.apply_binding(two, {"v": 999}) == {"color": "#ffffff"}  # clamped high
    three = _bind("v", "gradient", {"stops": [[0, "#ff0000"], [50, "#00ff00"], [100, "#0000ff"]]})
    assert bindings.apply_binding(three, {"v": 25}) == {"color": "#808000"}  # halfway red->green


def test_icon_lookup() -> None:
    b = _bind("code", "icon", {"table": {"3": "ph-cloud"}, "default": "ph-question"})
    assert bindings.apply_binding(b, {"code": 3}) == {"icon": "ph-cloud"}
    assert bindings.apply_binding(b, {"code": 99}) == {"icon": "ph-question"}


def test_missing_field_yields_no_patch() -> None:
    b = _bind("nope.here", "color", {"stops": [[5, "#fff"]]})
    assert bindings.apply_binding(b, {"temp": 10}) == {}
    # A malformed binding never raises, it just no-ops.
    bad = _bind("temp", "position", {"axis": "z"})
    assert bindings.apply_binding(bad, {"temp": 10}) == {}


# -- composer applies bindings each render ------------------------------


def test_build_canvas_els_applies_bindings(app: Flask, monkeypatch: object) -> None:
    """A bound rect (colour) and a bound ellipse (position) reflect live data in
    the composed output, evaluated in the same pass as data elements."""
    payload = {"temp": 20, "sun": {"nowMin": 720, "riseMin": 420, "setMin": 1020}}
    monkeypatch.setattr(composer, "_fetch_plugin_data", lambda *a, **k: payload)  # type: ignore[attr-defined]

    rect = Element(
        id="badge",
        kind="rect",
        x=0,
        y=0,
        w=100,
        h=40,
        color="#000000",
        bind=[
            _bind("temp", "color", {"stops": [[5, "#4a5aa8"], [15, "#1f8378"], [25, "#c08a2c"]]})
        ],
    )
    dot = Element(
        id="sun_dot",
        kind="ellipse",
        x=0,
        y=100,
        w=22,
        h=22,
        bind=[
            _bind(
                "sun.nowMin",
                "position",
                {"axis": "x", "in": ["sun.riseMin", "sun.setMin"], "out": [210, 940], "center": 22},
            )
        ],
    )
    with app.app_context():
        out = composer._build_canvas_els([rect, dot], 1200, 800)
    by_id = {e["id"]: e for e in out}
    assert by_id["badge"]["color"] == "#c08a2c"  # temp 20 -> third threshold
    assert by_id["sun_dot"]["x"] == 564  # noon marker centred on the arc
