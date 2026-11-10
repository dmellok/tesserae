"""fraimic_bin renderer.

Composition PNG packed into the raw ``.bin`` a Fraimic canvas accepts on
``POST /api/image``. The colour work is the shared Spectra 6 path
(``pack_to_panel_bin``: dither, saturation, calibration profile), whose
codes are the ones Fraimic's spec names (black 0x0, white 0x1, yellow 0x2,
red 0x3, blue 0x5, green 0x6). What is Fraimic-specific is the byte order,
which follows the panel's controller chips rather than raster order, from
Fraimic's converter specs (github.com/Fraimic/fraimic_bin_converter):

* 31.5" Large Canvas (EL315, 1440x2560 portrait), 2,304,000 bytes: eight
  288,000-byte controller blocks. IC1-IC4 cover the bottom half of the
  picture and IC5-IC8 the top half. Each block row is a two-pixel-wide
  vertical strip read bottom to top, and IC4 / IC8 carry 160 real pixels
  per row, padded out to 400 bytes with 0x11 (white).
* 13.3" (EL133UF1, 1200x1600 portrait), 960,000 bytes: raster rows of
  columns 0-599 for the whole picture, then rows of columns 600-1199.

Both are authored in the portrait frame. A frame hung on its side is set
up as a landscape panel; the composition is turned to portrait here, and
the panel's flip setting turns it the other way round.
"""

from __future__ import annotations

import io
from typing import Any

import numpy as np
from PIL import Image

from app.quantizer import fit_to_panel, pack_to_panel_bin, underscan_image
from app.state.page_store import Panel

DEFAULTS: dict[str, Any] = {
    "dither": "floyd-steinberg",
    "saturation": 1.4,
    "contrast": 1.0,
    "calibrated": False,
}

# Portrait frame (width, height) -> exact .bin size.
EL315 = (1440, 2560)
EL133 = (1200, 1600)
BIN_SIZES = {EL315: 2_304_000, EL133: 960_000}

_EL315_BLOCK_ROWS = 720
_EL315_BLOCK_BYTES = 400
_EL315_PAD = 0x11


def _setting(settings: dict[str, Any], key: str) -> Any:
    return settings.get(key, DEFAULTS[key])


def portrait_dims(panel: Panel) -> tuple[int, int]:
    """The panel's portrait frame, whichever way it is mounted."""
    return min(panel.w, panel.h), max(panel.w, panel.h)


def _pairs(codes: np.ndarray) -> np.ndarray:
    """Two 4-bit codes per byte along the last axis, first one high."""
    return ((codes[..., 0::2] << 4) | codes[..., 1::2]).astype(np.uint8)


def pack_el315(codes: np.ndarray) -> bytes:
    """Reorder a 2560x1440 portrait array of panel codes into the 31.5"
    panel's eight controller blocks (Fraimic EL315 spec, section 5)."""
    if codes.shape != (EL315[1], EL315[0]):
        raise ValueError(f"EL315 frame must be 2560x1440 codes, got {codes.shape}")
    flipped = np.flipud(codes)
    out = bytearray()
    for group in (0, 1):  # IC1-IC4 the bottom half, IC5-IC8 the top half
        strip = flipped[group * 1280 : (group + 1) * 1280]
        band = strip.reshape(1280, _EL315_BLOCK_ROWS, 2).transpose(1, 0, 2).reshape(720, 2560)
        for start, stop in ((0, 800), (800, 1600), (1600, 2400), (2400, 2560)):
            block = _pairs(band[:, start:stop])
            if block.shape[1] < _EL315_BLOCK_BYTES:
                pad = np.full(
                    (_EL315_BLOCK_ROWS, _EL315_BLOCK_BYTES - block.shape[1]),
                    _EL315_PAD,
                    dtype=np.uint8,
                )
                block = np.hstack([block, pad])
            out += block.tobytes()
    return bytes(out)


def pack_el133(codes: np.ndarray) -> bytes:
    """Reorder a 1600x1200 portrait array of panel codes into the 13.3"
    panel's two controller halves (Fraimic EL133UF1 spec, section 4)."""
    if codes.shape != (EL133[1], EL133[0]):
        raise ValueError(f"EL133 frame must be 1600x1200 codes, got {codes.shape}")
    return _pairs(codes[:, :600]).tobytes() + _pairs(codes[:, 600:]).tobytes()


def _unpack_raster(raster: bytes, width: int, height: int) -> np.ndarray:
    """Raster 4-bpp bytes (high nibble = even column) back to codes."""
    packed = np.frombuffer(raster, dtype=np.uint8).reshape(height, width // 2)
    codes = np.empty((height, width), dtype=np.uint8)
    codes[:, 0::2] = packed >> 4
    codes[:, 1::2] = packed & 0x0F
    return codes


def transform(png_bytes: bytes, *, panel: Panel, settings: dict[str, Any]) -> bytes:
    """Composition PNG to the frame's controller-ordered ``.bin``."""
    width, height = portrait_dims(panel)
    if (width, height) not in BIN_SIZES:
        # Any other panel (an unbound page's own size, a mistyped panel) is
        # fitted to the 31.5" frame: the frame refuses a file of the wrong
        # size, so there is nothing else worth sending.
        width, height = EL315
    img = Image.open(io.BytesIO(png_bytes))
    if panel.w > panel.h:
        # Hung on its side (turned a quarter anticlockwise): the landscape
        # composition's top goes to the portrait frame's right edge. A frame
        # turned the other way sets the panel's flip.
        img = img.rotate(-90, expand=True)
    if panel.flip:
        img = img.rotate(180, expand=True)
    if img.size != (width, height):
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=width, target_h=height, scale=fit, bg="white")
    if panel.underscan:
        img = underscan_image(img, underscan=panel.underscan)
    tone = settings.get("_profile_tone") or {}
    dither = settings.get("_profile_dither") or {}
    edges = settings.get("_profile_edges") or {}
    raster = pack_to_panel_bin(
        img,
        width=width,
        height=height,
        dither=_setting(settings, "dither"),
        saturation=float(_setting(settings, "saturation")),
        contrast=float(_setting(settings, "contrast")),
        gamut=panel.gamut,
        calibrated=bool(_setting(settings, "calibrated")),
        palette_override=settings.get("_palette_override"),
        exposure=int(tone.get("exposure", 0)),
        s_curve=int(tone.get("s_curve", 0)),
        serpentine=bool(dither.get("serpentine", False)),
        diffusion_strength=int(dither.get("diffusion_strength", 100)),
        smoothing_radius=int(edges.get("smoothing_radius", 0)),
        preserve_line_art=bool(edges.get("preserve_line_art", False)),
        protect_native_colours=int(edges.get("protect_native_colours", 0)),
        lab_compress_min=int(tone.get("lab_compress_min", 0)),
        lab_compress_max=int(tone.get("lab_compress_max", 100)),
        color_match=str(dither.get("color_match", "rgb")),
    )
    codes = _unpack_raster(raster, width, height)
    packed = pack_el315(codes) if (width, height) == EL315 else pack_el133(codes)
    if len(packed) != BIN_SIZES[(width, height)]:  # pragma: no cover - geometry guard
        raise ValueError(f"packed {len(packed)} bytes, expected {BIN_SIZES[(width, height)]}")
    return packed


def payload(digest: str, base_url: str, *, settings: dict[str, Any]) -> dict[str, Any]:
    del settings  # app.fraimic uploads the file itself; this is the artifact URL
    return {"url": f"{base_url.rstrip('/')}/renders/{digest}.bin"}
