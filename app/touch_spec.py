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


def build_frame_spec(els: Iterable[Element], *, wire: WireFn | None = None) -> dict[str, Any]:
    """Build the frame spec from a layout's elements.

    Returns a doc conforming to ``schema/frame-spec.schema.json``. Pass ``wire``
    (from :func:`wire_transform`) to emit rects in device-framebuffer coordinates;
    without it, rects stay in canvas space (preview / tests). The ``layout_digest``
    is derived from the primitive structure (stable across data-only redraws).
    ``atlases`` is omitted here; the atlas pipeline attaches descriptors for the
    roles the primitives reference. Invalid primitives are skipped."""
    primitives = [p for p in (_primitive_for(el, wire) for el in els) if p is not None]
    return {"layout_digest": touch_layout_digest(primitives), "primitives": primitives}
