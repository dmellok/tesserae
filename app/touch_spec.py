"""Build the touch-v3 wire spec from a canvas layout's placed primitives.

Server side of the device-owned touch contract (see
``notes/design-handoffs/touch-v3/``). The renderer leaves each primitive's rect
blank in the image; this module emits the declarative spec the firmware draws
from and hit-tests against. Action *payloads* never appear here: a primitive
carries only a ``{tier, type}`` classification, resolved back to a real action
server-side when the device reports an interaction.

Primitives that can't form a valid spec entry (a switch with no binding, a
button with no action, an off-panel rect) are skipped, not emitted malformed;
this is where the model's leniency (`app.state.panel_store`) is enforced.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any

from app.overlay_sync import _panel_geometry, rect_to_wire
from app.state.panel_store import Element

# A canvas-space -> device-framebuffer rect transform, or None if the rect
# degenerates (off-panel / zero-area).
WireFn = Callable[[float, float, float, float], "tuple[int, int, int, int] | None"]


def wire_transform(panel: dict[str, Any], canvas_w: int, canvas_h: int) -> WireFn | None:
    """A canvas-space -> device-framebuffer rect transform for one device.

    Scales an authored canvas rect to the device's composition dims (the panel
    renders the artboard scaled to fill), then runs it through the .bin
    renderer's composition->wire chain (rotate / flip / scale / underscan), so the
    firmware draws at final framebuffer coordinates and never has to know the
    artboard size or panel orientation. Returns None if the panel geometry is
    unusable (the caller then serves canvas-space rects unchanged)."""
    geo = _panel_geometry(panel)
    if geo is None or canvas_w <= 0 or canvas_h <= 0:
        return None
    sx = geo["comp_w"] / canvas_w
    sy = geo["comp_h"] / canvas_h

    def _to_wire(x: float, y: float, w: float, h: float) -> tuple[int, int, int, int] | None:
        return rect_to_wire(
            (x * sx, y * sy, w * sx, h * sy),
            comp_w=geo["comp_w"],
            comp_h=geo["comp_h"],
            native_w=geo["native_w"],
            native_h=geo["native_h"],
            flip=geo["flip"],
            underscan=geo["underscan"],
        )

    return _to_wire


def wire_is_upright(panel: dict[str, Any]) -> bool:
    """Whether the device framebuffer shows the composition upright.

    False when the .bin renderer turns the frame on its way to the panel: a
    quarter turn because the composition's orientation differs from the
    firmware-native one (a portrait-native Sticky set to landscape), or a half
    turn for a flipped mount. The rects survive that transform (see
    :func:`wire_transform`), but the firmware draws a primitive's label and
    icon in its own framebuffer orientation, so on a turned frame they come out
    sideways or upside down (#343). Unusable geometry answers True: rects stay
    in canvas space, as before."""
    geo = _panel_geometry(panel)
    if geo is None:
        return True
    comp_landscape = geo["comp_w"] > geo["comp_h"]
    native_landscape = geo["native_w"] > geo["native_h"]
    return comp_landscape == native_landscape and not geo["flip"]


def wire_turn(panel: dict[str, Any]) -> int:
    """How far the composition is turned, clockwise, on its way to the device
    framebuffer: 0, 90, 180 or 270.

    The same two steps as :func:`app.overlay_sync.rect_to_wire`: a quarter turn
    clockwise when the composition's orientation differs from the
    firmware-native one, then a half turn for a flipped mount. A firmware that
    reads the spec's ``turn`` draws each control's label and value turned to
    match, so they read upright on the wall (firmware #73). Unusable geometry
    answers 0, like :func:`wire_is_upright`."""
    geo = _panel_geometry(panel)
    if geo is None:
        return 0
    turn = 0
    if (geo["comp_w"] > geo["comp_h"]) != (geo["native_w"] > geo["native_h"]):
        turn += 90
    if geo["flip"]:
        turn += 180
    return turn


def is_spec_primitive(el: Element, *, upright: bool = True) -> bool:
    """Whether ``el`` makes it into a frame spec (ignoring the wire transform's
    off-panel check). The firmware draws only what the spec carries, so the
    renderer must paint everything else rather than leave its rect blank, or
    the control vanishes from the panel (#343).

    ``upright=False`` (see :func:`wire_is_upright`) leaves buttons out: the
    firmware would draw their label and icon sideways, and a server-painted
    button still works through its ``on_tap`` region on the v1/v2 path.
    Switches, sliders and steppers stay device-drawn there, since their only
    action is the device-side ``value_key`` path and a server-painted one would
    not respond to a tap."""
    if not upright and el.kind == "button":
        return False
    return _primitive_for(el, None) is not None


# Atlas role ids referenced by primitive text. The atlas descriptors themselves
# are attached downstream by the atlas pipeline; here we only reference them.
ATLAS_LABEL = "l20"
ATLAS_VALUE = "v28"

# The four typed touch controls. Public: the REST spec endpoint and the render
# report both ask "is this element a primitive", and a third private copy of the
# set is one more place for the answer to drift.
PRIMITIVE_KINDS = frozenset({"button", "switch", "slider", "stepper"})


def classify_action(spec: str | dict[str, Any] | None) -> tuple[int, str]:
    """Map an action spec to ``(tier, type)`` for the wire spec.

    tier: 0 local-only, 1 optimistic + confirm, 2 must round-trip.
    type: nav | ha | webhook | refresh | fetch. Unknown specs fall back to a
    conservative round-trip nav so the device shows a pending affordance."""
    if isinstance(spec, dict):
        return (1, "ha") if spec.get("action") == "ha" else (2, "nav")
    if not spec:
        return (2, "nav")
    text = str(spec)
    head = text.split(":", 1)[0]
    if head in ("page", "step", "rotate_next", "rotate_prev", "rotate"):
        return (0, "nav")
    if head == "ha":
        return (1, "ha")
    if head in ("webhook", "webhook_refresh", "room_book"):
        return (2, "webhook")
    if text == "refresh":
        return (2, "refresh")
    if text in ("fetch_latest", "fetch"):
        return (2, "fetch")
    return (2, "nav")


def _text_ref(
    atlas: str, *, text: str | None = None, max_chars: int | None = None
) -> dict[str, Any]:
    ref: dict[str, Any] = {"atlas": atlas, "align": "center"}
    if text is not None:
        ref["text"] = text
    if max_chars is not None:
        ref["max_chars"] = max_chars
    return ref


def _primitive_for(el: Element, wire: WireFn | None) -> dict[str, Any] | None:
    if el.kind not in PRIMITIVE_KINDS:
        return None
    if el.x < 0 or el.y < 0:
        return None  # a touch control must sit fully on-panel
    if wire is not None:
        wired = wire(el.x, el.y, el.w, el.h)
        if wired is None:
            return None  # degenerates in wire space (off-panel / zero-area)
        rect = {"x": wired[0], "y": wired[1], "w": wired[2], "h": wired[3]}
    else:
        rect = {"x": el.x, "y": el.y, "w": el.w, "h": el.h}
    base: dict[str, Any] = {"id": el.id, "type": el.kind, "rect": rect}

    if el.kind == "button":
        if not el.on_tap:
            return None
        tier, kind = classify_action(el.on_tap)
        base["action"] = {"tier": tier, "type": kind}
        if el.label:
            base["label"] = _text_ref(ATLAS_LABEL, text=el.label)
        if el.icon:
            base["icon"] = {"name": el.icon, "weight": el.weight or "bold", "px": 40}
        if el.value_key:
            # A state-bound button reflects its entity (drawn filled while
            # on) and still fires on_tap. Same seeding + live-values contract
            # as a switch; the tap is momentary, so no optimistic flip.
            base["value_key"] = el.value_key
            if el.state in ("on", "off"):
                base["state"] = el.state
        return base

    if el.kind == "switch":
        if not el.value_key:
            return None
        base["value_key"] = el.value_key
        if el.state in ("on", "off"):
            base["state"] = el.state
        if el.label:
            base["label"] = _text_ref(ATLAS_LABEL, text=el.label)
        base["action"] = {"tier": 1, "type": "ha"}
        return base

    if el.kind == "slider":
        if el.axis not in ("x", "y"):
            return None
        base["axis"] = el.axis
        base["min"] = el.value_min
        base["max"] = el.value_max
        base["step"] = el.value_step
        base["value"] = el.value_now
        if el.value_key:
            base["value_key"] = el.value_key
        base["value_text"] = _text_ref(ATLAS_VALUE, max_chars=4)
        base["action"] = {"tier": 1, "type": "ha"}
        return base

    # stepper
    base["min"] = el.value_min
    base["max"] = el.value_max
    base["step"] = el.value_step
    base["value"] = el.value_now
    if el.value_key:
        base["value_key"] = el.value_key
    base["value_text"] = _text_ref(ATLAS_VALUE, max_chars=4)
    base["action"] = {"tier": 1, "type": "ha"}
    return base


def touch_layout_digest(primitives: list[dict[str, Any]]) -> str:
    """Stable 16-hex hash of the primitives' STRUCTURE (id, kind, rect, action,
    binding, geometry), excluding seeded ``value``/``state`` so a switch flip or
    slider move is a data change, not a layout change. The device holds this and
    re-fetches the spec only when it changes."""
    structural = [{k: v for k, v in p.items() if k not in ("value", "state")} for p in primitives]
    blob = json.dumps(structural, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def fold_atlas_digests(layout_digest: str, atlas_digests: Iterable[str]) -> str:
    """The layout digest a spec carries once its glyph atlases are attached.

    The firmware keeps its held spec, and the atlas strips it loaded, for as
    long as ``layout_digest`` stays the same, and restores both from SD after a
    reboot. So a strip that changes under an unchanged control layout never
    reaches the panel: a rebuilt atlas (the #360 polarity fix) kept drawing the
    old inverted labels, and a spec first served without its atlases (cold
    cache) never gained text. Folding the attached atlas digests in moves the
    digest exactly when the strips change. Atlas digests hash the strip bytes,
    so a data-only redraw leaves this stable. With no atlases the primitive
    digest is returned unchanged."""
    digests = sorted(d for d in atlas_digests if d)
    if not digests:
        return layout_digest
    blob = json.dumps([layout_digest, digests], separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_frame_spec(
    els: Iterable[Element],
    *,
    wire: WireFn | None = None,
    upright: bool = True,
    turn: int = 0,
) -> dict[str, Any]:
    """Build the frame spec from a layout's elements.

    Returns a doc conforming to ``schema/frame-spec.schema.json``. Pass ``wire``
    (from :func:`wire_transform`) to emit rects in device-framebuffer coordinates;
    without it, rects stay in canvas space (preview / tests). The ``layout_digest``
    is derived from the primitive structure (stable across data-only redraws).
    ``atlases`` is omitted here; the atlas pipeline attaches descriptors for the
    roles the primitives reference. Invalid primitives are skipped, and so are
    buttons when ``upright`` is False (see :func:`is_spec_primitive`).

    ``turn`` (from :func:`wire_turn`) is written into the doc and folded into
    the digest only when it is not 0, so an upright panel's spec and digest
    are unchanged. A device keeps its held spec while the digest stays the
    same, and the rects of a square or symmetric layout can survive a turn
    unchanged, hence the fold."""
    primitives = [
        p
        for p in (_primitive_for(el, wire) for el in els if upright or el.kind != "button")
        if p is not None
    ]
    digest = touch_layout_digest(primitives)
    if turn not in (90, 180, 270):
        return {"layout_digest": digest, "primitives": primitives}
    blob = json.dumps([digest, {"turn": turn}], separators=(",", ":"))
    return {
        "layout_digest": hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16],
        "turn": turn,
        "primitives": primitives,
    }
