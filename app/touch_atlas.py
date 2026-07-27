"""Glyph atlases for touch-v3 primitive text (labels + value readouts).

Reuses the overlay atlas pipeline (:func:`app.overlay_sync.build_atlas`) with a
wider charset (full printable ASCII + the degree sign) and its own cache
namespace, then converts the result to the firmware atlas descriptor
(``schema/atlas.schema.json``). Each packed glyph cell is advance-wide (the
rasterizer measures the span's layout box), so the advance equals the cell width
and text lays out by walking cells; no separate advance metric is needed.

The atlas is device-served and content-addressed, so identical (role, charset,
font) atlases dedupe across devices and are fetched once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Printable ASCII (0x20-0x7E) plus the degree sign. Fixed so atlases are static
# and content-addressed; values (digits, signs, units) are always present.
TOUCH_CHARSET = "".join(chr(c) for c in range(0x20, 0x7F)) + "°"

# (px, weight) per atlas role id, matching primitives.json atlas_roles.
TOUCH_ATLAS_ROLES: dict[str, tuple[int, int]] = {"l20": (20, 400), "v28": (28, 700)}


def _descriptor(role_id: str, px: int, weight: int, atlas: dict[str, Any]) -> dict[str, Any]:
    """Convert a build_atlas result to the firmware atlas descriptor."""
    height = int(atlas["height"])
    ascent = round(height * 0.8)
    table: dict[str, dict[str, int]] = atlas["glyphs"]
    # The packed cell width is the advance (the span's measured layout box), so
    # adv == w and text lays out by walking cells.
    glyphs = {ch: {"x": g["x"], "w": g["w"], "adv": g["w"]} for ch, g in table.items()}
    space = table.get(" ", {})
    return {
        "id": role_id,
        "digest": atlas["digest"],
        "format": "gray4",
        "font": "Inter",
        "px": px,
        "weight": weight,
        "strip_h": height,
        "ascent": ascent,
        "descent": height - ascent,
        "space_adv": int(space.get("w") or max(2, px // 3)),
        "glyphs": glyphs,
    }


def build_touch_atlas(role_id: str, *, renders_dir: Path, rasterize: Any) -> dict[str, Any] | None:
    """Build (or reuse) the atlas for one role id, returning its firmware
    descriptor, or None if the role is unknown or the build fails. The strip
    bytes are persisted as ``touch-atlas-<digest>.bin`` in the renders dir."""
    spec = TOUCH_ATLAS_ROLES.get(role_id)
    if spec is None:
        return None
    px, weight = spec
    from app.overlay_sync import build_atlas

    atlas = build_atlas(
        px,
        weight,
        renders_dir=renders_dir,
        rasterize=rasterize,
        charset=TOUCH_CHARSET,
        prefix="touch",
    )
    if atlas is None:
        return None
    return _descriptor(role_id, px, weight, atlas)
