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

from collections.abc import Iterable
from typing import Any

from app.state.panel_store import Element

# Atlas role ids referenced by primitive text. The atlas descriptors themselves
# are attached downstream by the atlas pipeline; here we only reference them.
ATLAS_LABEL = "l20"
ATLAS_VALUE = "v28"

_PRIMITIVE_KINDS = frozenset({"button", "switch", "slider", "stepper"})


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
    if head == "webhook":
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


def _primitive_for(el: Element) -> dict[str, Any] | None:
    if el.kind not in _PRIMITIVE_KINDS:
        return None
    if el.x < 0 or el.y < 0:
        return None  # a touch control must sit fully on-panel
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


def build_frame_spec(layout_digest: str, els: Iterable[Element]) -> dict[str, Any]:
    """Build the frame spec from a layout's elements.

    Returns a doc conforming to ``schema/frame-spec.schema.json``. ``atlases`` is
    omitted here; the atlas pipeline attaches descriptors for the roles the
    primitives reference. Invalid primitives are skipped."""
    primitives = [p for p in (_primitive_for(el) for el in els) if p is not None]
    return {"layout_digest": layout_digest, "primitives": primitives}
