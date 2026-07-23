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
from pathlib import Path
from typing import Any

from app.panel import is_flipped_orientation

logger = logging.getLogger(__name__)

# Firmware-side caps from the v1.8 implementation report (v1.9 raised
# the target buffer and advertises its own limit via
# ``overlay.max_targets``; 8 stays the floor for firmware that doesn't
# send the field). The server must stay within them; anything past a
# cap is dropped with a log line (no silent truncation).
MAX_TARGETS = 8
# Firmware parses spec documents into a fixed 8 KB buffer; the v1.9
# worst-case host test (32 targets + 8 slots + 2 full atlases) measures
# ~6.5 KB, so exceeding this indicates a server-side regression.
MAX_SPEC_BYTES = 8192


def _is_nav_region(region: dict[str, Any]) -> bool:
    """True when a touch region's action navigates (page jump, rotation
    step): the targets that most deserve instant echo when a frame has
    more regions than the device's target budget."""
    specs: list[str] = []
    tap = region.get("tap")
    if isinstance(tap, str):
        specs.append(tap)
    swipe = region.get("swipe")
    if isinstance(swipe, dict):
        specs.extend(str(s) for s in swipe.values())
    return any(
        s.startswith(("page:", "step:")) or s in ("rotate_next", "rotate_prev") for s in specs
    )


MAX_SLOTS = 8
MAX_ATLASES = 2
MAX_GLYPHS = 32
MAX_VALUE_CHARS = 47

# Every glyph a slice-2 atlas carries. Sized for numeric sensor values:
# digits, separators, sign, percent, degree, and the two temperature
# letters. 19 glyphs, comfortably under the 32-glyph firmware cap. A
# value character outside this set renders as a mean-width blank on the
# device (firmware fallback), so values should stay numeric-ish.
ATLAS_CHARSET = "0123456789.,:-+%°CF "


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


def _rect_from(entry: dict[str, Any], geo: dict[str, Any]) -> tuple[int, int, int, int] | None:
    try:
        rect = (float(entry["x"]), float(entry["y"]), float(entry["w"]), float(entry["h"]))
    except (KeyError, TypeError, ValueError):
        return None
    return rect_to_wire(
        rect,
        comp_w=geo["comp_w"],
        comp_h=geo["comp_h"],
        native_w=geo["native_w"],
        native_h=geo["native_h"],
        flip=geo["flip"],
        underscan=geo["underscan"],
    )


def build_spec(
    *,
    frame_digest: str,
    regions: list[dict[str, Any]],
    panel: dict[str, Any],
    slots: list[dict[str, Any]] | None = None,
    atlas_provider: Any | None = None,
    max_targets: int = MAX_TARGETS,
) -> dict[str, Any] | None:
    """The schema-1 overlay spec for one frame, wire-space coordinates:
    tap-echo targets from the touch-region sidecar, plus (when the
    sidecar carries value slots and an ``atlas_provider`` is supplied)
    ``slots`` + ``atlases`` for live value text.

    ``atlas_provider(px, weight)`` returns an atlas dict
    ``{id, digest, url, format, height, glyphs}`` or None on failure; a
    failed atlas drops its slots (the spec degrades to rect-only rather
    than erroring). Slots group by (px, weight); the firmware carries at
    most MAX_ATLASES groups, so the largest groups win and the rest are
    dropped with a log line. None when the panel block can't provide the
    transform inputs."""
    geo = _panel_geometry(panel)
    if geo is None:
        return None
    # Resolve every region to a wire rect first; when the frame carries
    # more regions than the device's advertised target budget, trim with
    # navigation-priority (page/rotation targets echo first, document
    # order within each class) instead of blind document order, so the
    # targets most likely to be pressed repeatedly keep their echo.
    resolved = [(region, wire) for region in regions if (wire := _rect_from(region, geo))]
    if len(resolved) > max_targets:
        logger.info(
            "overlay spec: %d regions > device budget %d for frame %s; nav targets win",
            len(resolved),
            max_targets,
            frame_digest,
        )
        by_priority = sorted(enumerate(resolved), key=lambda p: (not _is_nav_region(p[1][0]), p[0]))
        # Survivors go back to document position: the firmware's own
        # overflow rule is first-in-document-order, so emitting in
        # document order keeps behaviour identical even if a stale
        # device ignores its advertised budget.
        resolved = [rw for _i, rw in sorted(by_priority[:max_targets], key=lambda p: p[0])]
    targets = [
        {
            "id": f"t{n + 1}",
            "x": wire[0],
            "y": wire[1],
            "w": wire[2],
            "h": wire[3],
            "echo": "invert",
        }
        for n, (_region, wire) in enumerate(resolved)
    ]

    spec: dict[str, Any] = {"schema": 1, "frame_digest": frame_digest, "targets": targets}

    usable = [s for s in (slots or []) if isinstance(s, dict)]
    if usable and atlas_provider is not None:
        # Group by the (px, weight) pair that defines an atlas; largest
        # groups win the MAX_ATLASES budget.
        groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for s in usable:
            try:
                groups.setdefault((int(s["px"]), int(s["weight"])), []).append(s)
            except (KeyError, TypeError, ValueError):
                continue
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if len(ranked) > MAX_ATLASES:
            dropped = sum(len(v) for _k, v in ranked[MAX_ATLASES:])
            logger.info(
                "overlay spec: dropping %d slot(s) beyond %d atlas groups for frame %s",
                dropped,
                MAX_ATLASES,
                frame_digest,
            )
        out_slots: list[dict[str, Any]] = []
        out_atlases: list[dict[str, Any]] = []
        for idx, ((px, weight), members) in enumerate(ranked[:MAX_ATLASES]):
            atlas = atlas_provider(px, weight)
            if atlas is None:
                logger.warning(
                    "overlay spec: atlas build failed px=%d weight=%d; dropping %d slot(s)",
                    px,
                    weight,
                    len(members),
                )
                continue
            atlas_id = f"a{idx + 1}"
            out_atlases.append({**atlas, "id": atlas_id})
            for s in members:
                if len(out_slots) >= MAX_SLOTS:
                    logger.info(
                        "overlay spec: dropping slots past firmware cap %d for frame %s",
                        MAX_SLOTS,
                        frame_digest,
                    )
                    break
                wire = _rect_from(s, geo)
                if wire is None:
                    continue
                out_slots.append(
                    {
                        "id": f"s{len(out_slots) + 1}",
                        "x": wire[0],
                        "y": wire[1],
                        "w": wire[2],
                        "h": wire[3],
                        "key": str(s.get("key") or ""),
                        "align": str(s.get("align") or "left"),
                        "atlas": atlas_id,
                    }
                )
        if out_slots:
            spec["slots"] = out_slots
            spec["atlases"] = out_atlases
    size = len(json.dumps(spec, separators=(",", ":")))
    if size > MAX_SPEC_BYTES:
        # Shouldn't be reachable within the caps (firmware's worst-case
        # host test measures ~6.5 KB); a breach means a regression here,
        # so log loudly rather than ship a document the parser rejects.
        logger.error(
            "overlay spec: %d bytes exceeds firmware buffer %d for frame %s",
            size,
            MAX_SPEC_BYTES,
            frame_digest,
        )
    return spec


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

    meta_path = renders_dir / f"overlay-atlas-{px}-{weight}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (
            isinstance(meta, dict)
            and meta.get("charset") == charset
            and (renders_dir / f"overlay-atlas-{meta.get('digest')}.bin").is_file()
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
        (renders_dir / f"overlay-atlas-{digest}.bin").write_bytes(packed)
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


def values_document(
    slots: list[dict[str, Any]],
    *,
    ha_get_state: Any,
    now: float,
) -> dict[str, Any]:
    """The values document for a frame's slots: pre-formatted display
    strings keyed by slot key, plus a seq the firmware uses for
    newest-wins dedup. Slice-2 grammar: ``ha:<entity_id>`` resolves to
    the entity's state string plus the slot's declared suffix. A failed
    or unknown entity yields no key (the firmware keeps showing the
    baked-in render). Strings clip to the firmware's 47-char cap."""
    values: dict[str, str] = {}
    for slot in slots:
        key = slot.get("key")
        if not isinstance(key, str) or not key.startswith("ha:") or key in values:
            continue
        entity_id = key[3:]
        try:
            state = ha_get_state(entity_id)
        except Exception:
            logger.debug("overlay values: state fetch failed for %s", entity_id, exc_info=True)
            continue
        raw = state.get("state") if isinstance(state, dict) else None
        if not isinstance(raw, str) or raw in ("unknown", "unavailable"):
            continue
        suffix = str(slot.get("suffix") or "")
        values[key] = (raw + suffix)[:MAX_VALUE_CHARS]
    return {"seq": int(now), "values": values}
