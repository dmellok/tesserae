"""Device-facing overlay specs (hybrid render mode, schema 1).

Touch boards with partial refresh (E1003 first) advertise
``overlay: {"schema": 1}`` and fetch a per-frame overlay spec: a small
draw list the firmware applies over the served frame for sub-second
feedback (tap-echo inverts today; value slots + glyph atlases are the
next schema slice). The firmware applies coordinates verbatim in the
wire-framebuffer pixel space it paints, so ALL transforms happen here.

Slice 1 ships rect-only specs: the tap-echo targets are derived from
the touch-region sidecar the render already produced (issue #49), moved
from composition space into wire space by mirroring the .bin renderer's
transform chain exactly: rotate on orientation mismatch, 180-degree
flip, scale to firmware-native dims, underscan inset. The contract is
documented in docs/dev/client-protocol.md; firmware behaviour and caps
in the v1.8 firmware report (8 targets / 8 slots / 2 atlases).

mypy --strict does not apply here; shapes mirror app.deck_sync.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.panel import is_flipped_orientation

logger = logging.getLogger(__name__)

# Firmware-side caps from the v1.8 implementation report. The server
# must stay within them; anything past the cap is dropped with a log
# line (no silent truncation).
MAX_TARGETS = 8


def advertised_overlay(payload: bytes | str | dict[str, Any]) -> dict[str, int] | None:
    """The overlay capability a device advertises in its register /
    heartbeat body (``{"overlay": {"schema": 1}}``), validated, or None.

    Sticky like the OTA schema (a firmware capability, not removable
    hardware): the caller carries it forward across beats that omit it."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    cap = body.get("overlay")
    if not isinstance(cap, dict):
        return None
    schema = cap.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        return None
    return {"schema": schema}


_Rect = tuple[float, float, float, float]


def _rotate_cw(rect: _Rect, comp_w: float, comp_h: float) -> _Rect:
    """Rect through a 90-degree clockwise rotation of a comp_w x comp_h
    canvas (PIL ``rotate(-90, expand=True)``): (x, y, w, h) in the
    original maps to (comp_h - (y + h), x, h, w) in the rotated
    comp_h x comp_w canvas."""
    x, y, w, h = rect
    return (comp_h - (y + h), x, h, w)


def _flip_180(rect: _Rect, cur_w: float, cur_h: float) -> _Rect:
    x, y, w, h = rect
    return (cur_w - (x + w), cur_h - (y + h), w, h)


def _scale(rect: _Rect, sx: float, sy: float) -> _Rect:
    x, y, w, h = rect
    return (x * sx, y * sy, w * sx, h * sy)


def _underscan(rect: _Rect, w: float, h: float, u: float) -> _Rect:
    """Mirror ``quantizer.underscan_image``: content shrinks to
    (W-2u, H-2u) and sits at (u, u)."""
    x, y, rw, rh = rect
    sx = (w - 2 * u) / w
    sy = (h - 2 * u) / h
    return (u + x * sx, u + y * sy, rw * sx, rh * sy)


def rect_to_wire(
    rect: tuple[float, float, float, float],
    *,
    comp_w: int,
    comp_h: int,
    native_w: int,
    native_h: int,
    flip: bool,
    underscan: int,
) -> tuple[int, int, int, int] | None:
    """A composition-space rect through the .bin renderer's transform
    chain, landing in wire-framebuffer pixels. Mirrors the renderer
    order exactly: rotate on orientation mismatch, 180 flip, scale to
    native dims, underscan inset. Returns ints clamped to the panel, or
    None when the rect degenerates to nothing."""
    cur_w, cur_h = float(comp_w), float(comp_h)
    out = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))

    firmware_landscape = native_w > native_h
    comp_landscape = comp_w > comp_h
    if firmware_landscape != comp_landscape:
        out = _rotate_cw(out, cur_w, cur_h)
        cur_w, cur_h = cur_h, cur_w
    if flip:
        out = _flip_180(out, cur_w, cur_h)
    if (cur_w, cur_h) != (float(native_w), float(native_h)):
        out = _scale(out, native_w / cur_w, native_h / cur_h)
        cur_w, cur_h = float(native_w), float(native_h)
    if underscan > 0 and native_w - 2 * underscan > 0 and native_h - 2 * underscan > 0:
        out = _underscan(out, cur_w, cur_h, float(underscan))

    x = max(0, min(native_w, round(out[0])))
    y = max(0, min(native_h, round(out[1])))
    x2 = max(0, min(native_w, round(out[0] + out[2])))
    y2 = max(0, min(native_h, round(out[1] + out[3])))
    if x2 - x <= 0 or y2 - y <= 0:
        return None
    return (x, y, x2 - x, y2 - y)


def _panel_geometry(panel: dict[str, Any]) -> dict[str, Any] | None:
    """The transform inputs from a device's panel block: composition
    dims, firmware-native dims (falling back to composition, same
    policy as the renderers), flip, underscan."""
    try:
        comp_w, comp_h = int(panel.get("w") or 0), int(panel.get("h") or 0)
    except (TypeError, ValueError):
        return None
    if comp_w <= 0 or comp_h <= 0:
        return None
    native_w = panel.get("native_w") or comp_w
    native_h = panel.get("native_h") or comp_h
    try:
        native_w, native_h = int(native_w), int(native_h)
    except (TypeError, ValueError):
        return None
    underscan = panel.get("underscan") or 0
    try:
        underscan = int(underscan)
    except (TypeError, ValueError):
        underscan = 0
    return {
        "comp_w": comp_w,
        "comp_h": comp_h,
        "native_w": native_w,
        "native_h": native_h,
        "flip": is_flipped_orientation(panel.get("orientation")),
        "underscan": max(0, underscan),
    }


def build_spec(
    *,
    frame_digest: str,
    regions: list[dict[str, Any]],
    panel: dict[str, Any],
) -> dict[str, Any] | None:
    """The schema-1 overlay spec for one frame: rect-only (tap-echo
    targets from the touch-region sidecar), wire-space coordinates.
    None when the panel block can't provide the transform inputs."""
    geo = _panel_geometry(panel)
    if geo is None:
        return None
    targets: list[dict[str, Any]] = []
    for i, region in enumerate(regions):
        try:
            rect = (
                float(region["x"]),
                float(region["y"]),
                float(region["w"]),
                float(region["h"]),
            )
        except (KeyError, TypeError, ValueError):
            continue
        wire = rect_to_wire(
            rect,
            comp_w=geo["comp_w"],
            comp_h=geo["comp_h"],
            native_w=geo["native_w"],
            native_h=geo["native_h"],
            flip=geo["flip"],
            underscan=geo["underscan"],
        )
        if wire is None:
            continue
        if len(targets) >= MAX_TARGETS:
            logger.info(
                "overlay spec: dropping target %d+ for frame %s (firmware cap %d)",
                i,
                frame_digest,
                MAX_TARGETS,
            )
            break
        targets.append(
            {
                "id": f"t{len(targets) + 1}",
                "x": wire[0],
                "y": wire[1],
                "w": wire[2],
                "h": wire[3],
                "echo": "invert",
            }
        )
    return {"schema": 1, "frame_digest": frame_digest, "targets": targets}
