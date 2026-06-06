"""pi_png renderer.

Takes the composition-orientation PNG produced by the composer, rotates it to
the Pi client's landscape-native pixel grid, and publishes the v3-frozen
``{url, rotate, scale, bg, saturation}`` payload.

The payload shape is byte-compatible with the long-standing
``inky/update`` contract, a Pi listener built against inky-dash v3/v4 works
against Tesserae unchanged, just by subscribing to a different topic.
"""

from __future__ import annotations

from typing import Any

from app.quantizer import apply_underscan, rotate_png
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "rotate": 0,
    "scale": "fit",
    "bg": "white",
    "saturation": 0.5,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Rotate the composition PNG to match the panel's orientation.

    Derives the turn from the panel the same way ``pi_bin`` does, so the
    per-device Rotation control drives both renderers identically: a
    portrait panel (taller than wide) gets a 90° CCW turn, ``3``
    clockwise quarter-turns, to map its top edge onto the client's
    landscape buffer; landscape gets none. ``panel.flip`` adds 180° for
    an upside-down mount.
    """
    del settings  # rotation comes from the panel now, not a fixed setting
    quarters = 3 if panel.w < panel.h else 0
    if panel.flip:
        quarters = (quarters + 2) % 4
    out = rotate_png(png_bytes, quarters=quarters)
    # Per-device underscan: inset content so it clears a physical mat/bezel.
    if panel.underscan:
        out = apply_underscan(out, underscan=panel.underscan)
    return out


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    """Build the v3-frozen Pi client payload. ``url`` points at the
    artifact this renderer just wrote."""
    return {
        "url": f"{base_url.rstrip('/')}/renders/{digest}.png",
        "rotate": int(_setting(settings, "rotate")),
        "scale": str(_setting(settings, "scale")),
        "bg": str(_setting(settings, "bg")),
        "saturation": float(_setting(settings, "saturation")),
    }
