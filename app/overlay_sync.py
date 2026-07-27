"""Device-facing overlay machinery (hybrid render mode).

Touch boards with partial refresh (E1003 first) advertise
``overlay: {"schema": N}`` and receive live values (``values_document``,
drawn on-device through glyph atlases) and post-action frame patches
(:mod:`app.frame_patch`). The firmware applies coordinates verbatim in
the wire-framebuffer pixel space it paints, so ALL transforms happen
here: :func:`rect_to_wire` mirrors the .bin renderer's chain exactly
(rotate on orientation mismatch, 180-degree flip, scale to
firmware-native dims, underscan inset). Contract in
docs/dev/client-protocol.md.

The schema-1 overlay-spec builder (tap-echo target lists) was removed
with protocol v2 (docs/protocol-v2-touch.md); its successor is the
interaction manifest.

mypy --strict does not apply here; shapes mirror app.deck_sync.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.panel import is_flipped_orientation

logger = logging.getLogger(__name__)

MAX_VALUE_CHARS = 47

# Every glyph a slice-2 atlas carries. Sized for numeric sensor values:
# digits, separators, sign, percent, degree, and the two temperature
# letters. 19 glyphs, comfortably under the 32-glyph firmware cap. A
# value character outside this set renders as a mean-width blank on the
# device (firmware fallback), so values should stay numeric-ish.
ATLAS_CHARSET = "0123456789.,:-+%°CF "


def advertised_proto(payload: bytes | str | dict[str, Any]) -> dict[str, int] | None:
    """The protocol capability a device advertises in its register /
    heartbeat body (``{"proto": {"v": 2}}``), validated, or None.
    Protocol v2 devices own hit testing and consume interaction
    manifests / bundles / the SSE stream (docs/protocol-v2-touch.md).
    Sticky like ``overlay``: a firmware property, carried forward across
    beats that omit it and persisted in the device-facts store."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    cap = body.get("proto")
    if not isinstance(cap, dict):
        return None
    v = cap.get("v")
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        return None
    return {"v": min(64, v)}


def advertised_can_stay_awake(payload: bytes | str | dict[str, Any]) -> bool | None:
    """The ``can_stay_awake`` hardware capability a device advertises: it *can*
    run mains-powered without deep sleep (touch-v3). Sticky like ``proto`` /
    ``overlay``, a firmware property carried forward across beats that omit it.
    Whether the device *does* stay awake is the per-device ``always_on`` setting,
    not this; the server offers that setting only to can_stay_awake devices."""
    if isinstance(payload, (bytes, bytearray, str)):
        try:
            body = json.loads(payload)
        except (ValueError, TypeError):
            return None
    else:
        body = payload
    if not isinstance(body, dict):
        return None
    cap = body.get("can_stay_awake")
    return cap if isinstance(cap, bool) else None


def advertised_overlay(payload: bytes | str | dict[str, Any]) -> dict[str, int] | None:
    """The overlay capability a device advertises in its register /
    heartbeat body (``{"overlay": {"schema": 1, "max_targets": 32}}``),
    validated, or None. ``max_targets`` is additive (firmware >= v1.9);
    absent means the v1.8 baseline of 8, and the value clamps to a sane
    band so a corrupt beat can't zero out or explode the spec.

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
    out: dict[str, int] = {"schema": schema}
    raw_max = cap.get("max_targets")
    if isinstance(raw_max, int) and not isinstance(raw_max, bool):
        out["max_targets"] = max(1, min(64, raw_max))
    return out


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


# -- glyph atlases ---------------------------------------------------------


def pack_atlas_strip(
    glyphs: list[tuple[str, Any]],
) -> tuple[bytes, dict[str, dict[str, int]], int, int]:
    """Pack per-glyph grayscale PIL images (equal heights) into one
    horizontal 4bpp-gray strip: ``(bin_bytes, glyph_table, strip_w,
    height)``. Firmware contract: rows packed at exactly
    max(glyph.x + glyph.w) pixels, 2 px/byte, high nibble = left pixel,
    0x0 black / 0xF white, so the strip width is forced even by
    widening the final glyph's declared width when needed."""
    import numpy as np

    if not glyphs:
        raise ValueError("no glyphs to pack")
    height = glyphs[0][1].height
    widths = [img.width for _ch, img in glyphs]
    strip_w = sum(widths)
    pad = strip_w % 2
    strip_w += pad

    arr = np.full((height, strip_w), 255, dtype=np.uint8)
    table: dict[str, dict[str, int]] = {}
    x = 0
    for i, (ch, img) in enumerate(glyphs):
        if img.height != height:
            raise ValueError("glyph heights differ")
        w = img.width
        arr[:, x : x + w] = np.asarray(img.convert("L"), dtype=np.uint8)
        declared_w = w + (pad if i == len(glyphs) - 1 else 0)
        table[ch] = {"x": x, "w": declared_w}
        x += w

    nibbles = np.clip(np.rint(arr.astype(np.float32) / 17.0), 0, 15).astype(np.uint8)
    packed = ((nibbles[:, 0::2] << 4) | nibbles[:, 1::2]).astype(np.uint8).tobytes()
    return packed, table, strip_w, height


def build_atlas(
    px: int,
    weight: int,
    *,
    renders_dir: Path,
    rasterize: Any,
    charset: str = ATLAS_CHARSET,
    prefix: str = "overlay",
) -> dict[str, Any] | None:
    """Build (or reuse) the glyph atlas for one (px, weight) pair:
    ``{digest, format, height, glyphs}``, with the strip bytes persisted
    as ``overlay-atlas-<digest>.bin`` in the renders dir.

    ``rasterize(px, weight, charset)`` returns ``(png_bytes, boxes)``
    where boxes are ``[{ch, x, y, w, h}]`` page-space glyph boxes; the
    default implementation captures the ``/compose/_overlay_atlas``
    strip through the same browser + fonts as compositions. Cached per
    (px, weight, charset) in a meta sidecar; None on any failure (the
    spec then degrades to rect-only)."""
    import io

    from PIL import Image

    meta_path = renders_dir / f"{prefix}-atlas-{px}-{weight}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (
            isinstance(meta, dict)
            and meta.get("charset") == charset
            and (renders_dir / f"{prefix}-atlas-{meta.get('digest')}.bin").is_file()
        ):
            return {k: meta[k] for k in ("digest", "format", "height", "glyphs")}
    except (OSError, json.JSONDecodeError, KeyError):
        pass

    try:
        png_bytes, boxes = rasterize(px, weight, charset)
        img = Image.open(io.BytesIO(png_bytes)).convert("L")
        by_ch = {b["ch"]: b for b in boxes if isinstance(b, dict) and b.get("w", 0) > 0}
        drawn = [by_ch[ch] for ch in charset if ch in by_ch]
        if not drawn:
            return None
        top = max(0, min(int(b["y"]) for b in drawn))
        bottom = min(img.height, max(int(b["y"]) + int(b["h"]) for b in drawn))
        if bottom - top <= 0:
            return None
        glyph_imgs: list[tuple[str, Any]] = []
        for ch in charset:
            box = by_ch.get(ch)
            if box is None:
                # Undrawn glyph (a bare space collapses in some engines):
                # synthesize a blank at a third of the font size.
                blank = Image.new("L", (max(2, px // 3), bottom - top), 255)
                glyph_imgs.append((ch, blank))
                continue
            x0 = max(0, int(box["x"]))
            glyph_imgs.append((ch, img.crop((x0, top, x0 + int(box["w"]), bottom))))
        packed, table, _strip_w, height = pack_atlas_strip(glyph_imgs)
        import hashlib as _hashlib

        digest = _hashlib.sha256(packed).hexdigest()[:16]
        (renders_dir / f"{prefix}-atlas-{digest}.bin").write_bytes(packed)
        atlas = {"digest": digest, "format": "4bpp-gray", "height": height, "glyphs": table}
        meta_path.write_text(json.dumps({**atlas, "charset": charset}), encoding="utf-8")
        return atlas
    except Exception:
        logger.exception("overlay atlas build failed px=%d weight=%d", px, weight)
        return None


def browser_rasterizer(base_url: str, timezone_id: str | None = None) -> Any:
    """The default atlas rasterizer: captures the loopback
    ``/compose/_overlay_atlas`` strip. Returned as a callable so tests
    (and the REST endpoint) can inject alternatives."""

    def rasterize(px: int, weight: int, charset: str) -> tuple[bytes, list[dict[str, Any]]]:
        from urllib.parse import quote

        from app.renderer import CaptureRequest, RenderRequest, capture_composed, to_loopback_url

        url = to_loopback_url(
            f"{base_url.rstrip('/')}/compose/_overlay_atlas"
            f"?px={px}&weight={weight}&chars={quote(charset)}"
        )
        png, boxes = capture_composed(
            CaptureRequest(
                render=RenderRequest(
                    url=url,
                    viewport_w=min(4000, px * len(charset) * 2 + 64),
                    viewport_h=px * 2 + 32,
                    timezone_id=timezone_id,
                ),
                script=(
                    "() => Array.from(document.querySelectorAll('#strip span')).map(el => {"
                    "  const r = el.getBoundingClientRect();"
                    "  return {ch: el.getAttribute('data-ch'), x: Math.floor(r.x),"
                    "          y: Math.floor(r.y), w: Math.ceil(r.width), h: Math.ceil(r.height)};"
                    "})"
                ),
            ),
        )
        return png, boxes if isinstance(boxes, list) else []

    return rasterize


def resolve_slot_value(key: str, ha_get_state: Any) -> str | None:
    """The raw display string for one slot key, or None (unknown key
    grammar, fetch failure, missing entity / attribute).

    Grammar: ``ha:<entity_id>`` resolves to the entity's state string;
    ``ha:<entity_id>:<dotted.path>`` resolves the path against the full
    state object (``ha:light.desk:attributes.brightness``,
    ``ha:climate.den:attributes.hvac_action``). ``unknown`` /
    ``unavailable`` states yield None so the firmware keeps the baked-in
    render."""
    if not key.startswith("ha:") or len(key) <= 3:
        return None
    _prefix, entity_id, *rest = key.split(":", 2)
    path = rest[0] if rest else ""
    try:
        state = ha_get_state(entity_id)
    except Exception:
        logger.debug("overlay values: state fetch failed for %s", entity_id, exc_info=True)
        return None
    if not isinstance(state, dict):
        return None
    if not path:
        raw = state.get("state")
        if not isinstance(raw, str) or raw in ("unknown", "unavailable"):
            return None
        return raw
    node: Any = state
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None or isinstance(node, (dict, list)):
        return None
    return str(node)


def values_document(
    slots: list[dict[str, Any]],
    *,
    ha_get_state: Any,
    now: float,
) -> dict[str, Any]:
    """The values document for a frame's slots: pre-formatted display
    strings keyed by slot key, plus a seq the firmware uses for
    newest-wins dedup. Keys resolve per :func:`resolve_slot_value`; a
    slot may then carry a ``map`` (value -> display string, e.g.
    ``{"on": "1", "off": "0"}``) so non-numeric states land inside the
    numeric glyph charset. A failed or unknown key is simply absent
    (the firmware keeps showing the baked-in render). Strings clip to
    the firmware's 47-char cap. ``seq`` is wall time in MILLISECONDS
    (int64 on the wire): second-granularity seqs made two value changes
    inside one second dedup to a single repaint on the device."""
    values: dict[str, str] = {}
    for slot in slots:
        key = slot.get("key")
        if not isinstance(key, str) or key in values:
            continue
        raw = resolve_slot_value(key, ha_get_state)
        if raw is None:
            continue
        mapping = slot.get("map")
        if isinstance(mapping, dict):
            mapped = mapping.get(raw)
            if isinstance(mapped, str):
                raw = mapped
        suffix = str(slot.get("suffix") or "")
        values[key] = (raw + suffix)[:MAX_VALUE_CHARS]
    return {"seq": int(now * 1000), "values": values}
