"""pi_png renderer.

Takes the composition-orientation PNG produced by the composer, rotates it to
the Pi client's landscape-native pixel grid, and publishes the v3-frozen
``{url, rotate, scale, bg, saturation}`` payload.

The payload shape is byte-compatible with the long-standing
``inky/update`` contract — a Pi listener built against inky-dash v3/v4 works
against Tesserae unchanged, just by subscribing to a different topic.
"""

from __future__ import annotations

from typing import Any

from app.quantizer import rotate_png
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "rotate": 0,
    "scale": "fit",
    "bg": "white",
    "saturation": 0.5,
    # 1 CW quarter-turn for ribbon-cable-up portrait mount. NOTE: this
    # is the opposite direction from pi_bin's renderer-side rotation —
    # pi_bin writes raw landscape pixels into the panel buffer, while
    # pi_png hands the image to inky.set_image() which applies its own
    # mount-aware rotation. The two paths compound differently, so the
    # server-side rotations end up being opposites.
    "transform_rotate_quarters": 1,
}


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Rotate the composition PNG to landscape orientation.

    ``transform_rotate_quarters`` defaults to 1 (a single 90deg CW turn) —
    the Pi panel's mounted orientation is portrait but the client expects
    landscape. Override per install if the panel is mounted landscape.
    """
    del panel  # rotation count is fixed at the renderer level; panel dims unused
    quarters = int(_setting(settings, "transform_rotate_quarters"))
    return rotate_png(png_bytes, quarters=quarters)


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
