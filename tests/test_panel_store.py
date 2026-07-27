"""Canvas Element model, including the touch-v3 typed primitives.

The store had no dedicated tests; this covers the element round-trip plus the
button/switch/slider/stepper fields added for device-owned touch.
"""

from __future__ import annotations

from app.state.panel_store import CanvasPage, Element


def _roundtrip(el: Element) -> Element:
    """Element -> JSON dict -> Element, the store's persist/load path."""
    return Element.model_validate(el.model_dump(mode="json"))


def test_button_primitive_roundtrips() -> None:
    el = Element(
        id="btn_scene",
        kind="button",
        x=40,
        y=900,
        w=180,
        h=80,
        label="Movie",
        icon="film-slate",
        weight="duotone",
        on_tap="page:scenes",
    )
    out = _roundtrip(el)
    assert out.kind == "button"
    assert out.label == "Movie"
    assert out.icon == "film-slate"
    assert out.weight == "duotone"
    # on_tap is canonicalised to its dispatchable form but stays the nav spec.
    assert out.on_tap == "page:scenes"


def test_switch_primitive_carries_binding_and_state() -> None:
    el = Element(id="sw_desk", kind="switch", w=160, h=80, value_key="ha:light.desk", state="on")
    out = _roundtrip(el)
    assert out.kind == "switch"
    assert out.value_key == "ha:light.desk"
    assert out.state == "on"


def test_slider_primitive_range_roundtrips() -> None:
    el = Element(
        id="sl_bri",
        kind="slider",
        w=360,
        h=70,
        axis="x",
        value_key="ha:light.desk:attributes.brightness_pct",
        value_min=0,
        value_max=100,
        value_step=5,
        value_now=60,
    )
    out = _roundtrip(el)
    assert out.kind == "slider"
    assert out.axis == "x"
    assert (out.value_min, out.value_max, out.value_step, out.value_now) == (0, 100, 5, 60)


def test_stepper_primitive_roundtrips() -> None:
    el = Element(
        id="st_vol",
        kind="stepper",
        w=160,
        h=70,
        value_key="ha:media.vol",
        value_min=0,
        value_max=30,
        value_step=1,
        value_now=12,
    )
    out = _roundtrip(el)
    assert out.kind == "stepper"
    assert (out.value_min, out.value_max, out.value_now) == (0, 30, 12)


def test_primitive_defaults_are_lenient() -> None:
    # An in-progress primitive (dropped but not yet configured) must not be
    # rejected by the model; required fields are enforced at spec-build time.
    el = Element(id="sw_new", kind="switch")
    assert el.value_key == ""
    assert el.value_min == 0.0 and el.value_max == 100.0 and el.value_step == 1.0


def test_non_primitive_elements_unaffected() -> None:
    # A plain widget carries the new fields at their defaults, unchanged.
    el = _roundtrip(Element(id="w1", kind="widget", widget="weather_now"))
    assert el.kind == "widget"
    assert el.value_key == "" and el.axis == "" and el.state == ""


def test_new_fields_survive_canvas_document_roundtrip() -> None:
    page = CanvasPage(
        id="c1",
        els=[Element(id="sw", kind="switch", value_key="ha:light.x", state="off")],
    )
    out = CanvasPage.model_validate(page.model_dump(mode="json"))
    assert out.els[0].kind == "switch"
    assert out.els[0].value_key == "ha:light.x"
    assert out.els[0].state == "off"
