"""Image ops used by renderers.

Two responsibility groups:

* **Pure transforms** — ``rotate_png``, ``apply_underscan``, ``fit_to_panel``.
  Composable, no panel/palette assumptions, used by any renderer.
* **Spectra 6 / Waveshare E6 packing** — ``pack_to_panel_bin`` produces the
  4-bpp panel-native buffer that ESP32 firmware streams to SPI and Pi-side
  binary listeners consume. Dither dispatch (Pillow's Floyd-Steinberg /
  none + our own numpy-backed atkinson / jarvis / stucki / bayer-8x8 /
  halftone / crosshatch) lives here.

Lifted largely from inky-dash's ``app.quantizer``. The packing layout
(``width * height // 2`` bytes, high nibble = even column, palette →
firmware-nibble LUT) is byte-compatible with the existing
``dmellok/esp32-inky-dash-client`` firmware.
"""

from __future__ import annotations

import io
from typing import Literal

import numpy as np
from PIL import Image, ImageEnhance

# DitherMode covers both the Pillow-only callers and the numpy-backed ones.
# pack_to_panel_bin dispatches on the full set; callers that don't own a
# numpy implementation (Pillow's quantize) should validate against the
# narrower _PIL_DITHER_MAP themselves.
DitherMode = Literal[
    "floyd-steinberg",
    "none",
    "atkinson",
    "jarvis",
    "stucki",
    "bayer-8x8",
    "halftone",
    "crosshatch",
]


# Spectra 6 7-colour palette. Nominal sRGB approximations of the panel's
# ink primaries — the panel firmware does the actual gamut projection.
# Used by Pi-side PNG listeners that quantise their own buffer.
SPECTRA_6_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # black
    (255, 255, 255),  # white
    (255, 255, 0),  # yellow
    (255, 0, 0),  # red
    (0, 0, 255),  # blue
    (0, 255, 0),  # green
    (255, 140, 0),  # orange
)


# Waveshare E6 6-colour palette — the .bin packers target this. No orange:
# the firmware reserves nibble 0x4 (we map blue to 0x5, green to 0x6 via
# the LUT below) so the gamut is one colour smaller than Spectra 6.
#
# Palette order matters: Pillow's quantize emits indices 0…N-1 in the
# order we declare. The LUT translates those to firmware nibbles.
WAVESHARE_E6_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # 0 -> 0x0 black
    (255, 255, 255),  # 1 -> 0x1 white
    (255, 255, 0),  # 2 -> 0x2 yellow
    (255, 0, 0),  # 3 -> 0x3 red
    (0, 0, 255),  # 4 -> 0x5 blue (firmware reserves 0x4)
    (0, 255, 0),  # 5 -> 0x6 green (firmware reserves 0x7)
)

_E6_NIBBLE_BY_PALETTE_INDEX: tuple[int, ...] = (0x0, 0x1, 0x2, 0x3, 0x5, 0x6)


# Pimoroni Inky Impression 7-colour (ACeP / UC8159) palette. Same ink
# primaries as Spectra 6 but a 7th colour (orange) and a *different* index
# order: the .bin nibble IS the inky library's native palette index, so a
# Pi client can write it straight into the UC8159 buffer. Declaring the
# palette in that index order makes the LUT an identity map (0…6).
INKY_7COLOUR_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # 0 black
    (255, 255, 255),  # 1 white
    (0, 255, 0),  # 2 green
    (0, 0, 255),  # 3 blue
    (255, 0, 0),  # 4 red
    (255, 255, 0),  # 5 yellow
    (255, 140, 0),  # 6 orange
)

_INKY_7COLOUR_NIBBLE_BY_PALETTE_INDEX: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)


# Panel colour gamuts the .bin packer can target, keyed by the value
# stored on a device's panel block. Each maps to (palette, nibble LUT).
# ``waveshare_e6`` is the default everywhere so existing devices and the
# always-E6 ESP32 path are unchanged.
PanelGamut = Literal["waveshare_e6", "inky_7colour"]
PANEL_GAMUTS: tuple[str, ...] = ("waveshare_e6", "inky_7colour")
_GAMUT_TABLE: dict[str, tuple[tuple[tuple[int, int, int], ...], tuple[int, ...]]] = {
    "waveshare_e6": (WAVESHARE_E6_PALETTE, _E6_NIBBLE_BY_PALETTE_INDEX),
    "inky_7colour": (INKY_7COLOUR_PALETTE, _INKY_7COLOUR_NIBBLE_BY_PALETTE_INDEX),
}


_PIL_DITHER_MAP: dict[str, Image.Dither] = {
    "floyd-steinberg": Image.Dither.FLOYDSTEINBERG,
    "none": Image.Dither.NONE,
}


# --- pure transforms --------------------------------------------------


def rotate_png(png_bytes: bytes, *, quarters: int) -> bytes:
    """Rotate a PNG by N 90deg clockwise turns. ``quarters=0`` is a no-op."""
    n = quarters % 4
    if n == 0:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes))
    # PIL rotates counter-clockwise; negate to get clockwise.
    rotated = img.rotate(-90 * n, expand=True)
    out = io.BytesIO()
    rotated.save(out, format="PNG", optimize=True)
    return out.getvalue()


def apply_underscan(png_bytes: bytes, *, underscan: int, fill: str = "#ffffff") -> bytes:
    """Inset the image by ``underscan`` pixels on every edge, padding the
    border with ``fill``. The original WxH is preserved; the content is
    downscaled to (W-2u, H-2u) and pasted at (u, u) so the published frame
    still matches the panel grid. Compensates for a physical mat / bezel."""
    if underscan <= 0:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = img.size
    inner_w = w - 2 * underscan
    inner_h = h - 2 * underscan
    if inner_w <= 0 or inner_h <= 0:
        return png_bytes
    inner = img.resize((inner_w, inner_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), fill)
    canvas.paste(inner, (underscan, underscan))
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    return out.getvalue()


def fit_to_panel(
    img: Image.Image,
    *,
    target_w: int,
    target_h: int,
    scale: str = "fit",
    bg: str = "white",
) -> Image.Image:
    """Resize ``img`` to (target_w, target_h) using the requested scale mode.

    * ``fit``     — preserve aspect, letterbox with ``bg``.
    * ``fill``    — preserve aspect, crop overflow.
    * ``stretch`` — squash to exact dims.
    * ``center``  — paste original at panel centre; clip overflow, ``bg``
                    elsewhere.

    Used by the Send page (M7) when the uploaded image isn't already panel
    sized. Dashboard renders skip this — the composer emits panel-exact PNGs."""
    src = img.convert("RGB")
    if src.size == (target_w, target_h):
        return src
    if scale == "stretch":
        return src.resize((target_w, target_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), bg)
    if scale == "center":
        x = (target_w - src.width) // 2
        y = (target_h - src.height) // 2
        canvas.paste(src, (x, y))
        return canvas
    # fit + fill share the aspect-preserving logic; sign of the scaling
    # difference decides which dimension drives.
    src_ratio = src.width / src.height
    tgt_ratio = target_w / target_h
    if (scale == "fit" and src_ratio > tgt_ratio) or (scale == "fill" and src_ratio < tgt_ratio):
        new_w = target_w
        new_h = max(1, round(target_w / src_ratio))
    else:
        new_h = target_h
        new_w = max(1, round(target_h * src_ratio))
    resized = src.resize((new_w, new_h), Image.Resampling.LANCZOS)
    if scale == "fit":
        canvas.paste(resized, ((target_w - new_w) // 2, (target_h - new_h) // 2))
        return canvas
    # fill: crop centred.
    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


# --- Pillow-fronted SPECTRA 6 quantise (for PNG-side listeners) -------


def _palette_image(palette: tuple[tuple[int, int, int], ...]) -> Image.Image:
    pal = Image.new("P", (1, 1))
    flat: list[int] = []
    for r, g, b in palette:
        flat.extend([r, g, b])
    flat.extend([0] * (256 * 3 - len(flat)))
    pal.putpalette(flat)
    return pal


def quantize(
    src: bytes | Image.Image,
    *,
    dither: DitherMode = "floyd-steinberg",
    palette: tuple[tuple[int, int, int], ...] = SPECTRA_6_PALETTE,
) -> Image.Image:
    """Project an image to ``palette`` and return mode-RGB Pillow output."""
    if dither not in _PIL_DITHER_MAP:
        raise ValueError(f"unsupported Pillow dither mode: {dither!r}")
    img = src if isinstance(src, Image.Image) else Image.open(io.BytesIO(src))
    rgb = img.convert("RGB")
    pal_img = _palette_image(palette)
    return rgb.quantize(palette=pal_img, dither=_PIL_DITHER_MAP[dither]).convert("RGB")


def quantize_to_png(
    src: bytes | Image.Image,
    *,
    dither: DitherMode = "floyd-steinberg",
    palette: tuple[tuple[int, int, int], ...] = SPECTRA_6_PALETTE,
) -> bytes:
    out = io.BytesIO()
    quantize(src, dither=dither, palette=palette).save(out, format="PNG", optimize=True)
    return out.getvalue()


# --- numpy-backed dither for the panel-bin packer ---------------------


def _nearest_palette_indices(pixels: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """Vectorised nearest-palette lookup. Squared euclidean in sRGB —
    cheap and good enough for a 6-colour gamut."""
    flat = pixels.reshape(-1, 3)
    diff = flat[:, None, :] - palette[None, :, :]
    d2 = np.einsum("pnk,pnk->pn", diff, diff)
    idx = np.argmin(d2, axis=1).astype(np.uint8)
    reshaped: np.ndarray = idx.reshape(pixels.shape[:-1])
    return reshaped


def _error_diffusion(
    rgb: Image.Image, palette: np.ndarray, weights: list[tuple[int, int, float]]
) -> bytes:
    """Generic error-diffusion dither.

    Each pixel reads its predecessors' errors, so the per-pixel work has a
    fundamental sequential dependency. We feed numpy arrays into typed
    ``array.array`` flat buffers — ~3x faster than indexing numpy in the
    hot loop. Still slow at panel size (several seconds) but opt-in;
    FS / bayer remain the fast paths."""
    import array as _array

    arr = np.asarray(rgb, dtype=np.float32)
    H, W, _ = arr.shape
    buf_r = _array.array("f", arr[:, :, 0].ravel().tolist())
    buf_g = _array.array("f", arr[:, :, 1].ravel().tolist())
    buf_b = _array.array("f", arr[:, :, 2].ravel().tolist())
    out = bytearray(H * W)

    pr = [float(palette[i, 0]) for i in range(palette.shape[0])]
    pg = [float(palette[i, 1]) for i in range(palette.shape[0])]
    pb = [float(palette[i, 2]) for i in range(palette.shape[0])]
    n_pal = len(pr)
    w_dy = [w[0] for w in weights]
    w_dx = [w[1] for w in weights]
    w_frac = [w[2] for w in weights]
    n_weights = len(weights)

    for y in range(H):
        row_base = y * W
        for x in range(W):
            idx = row_base + x
            r = buf_r[idx]
            g = buf_g[idx]
            b = buf_b[idx]
            best_i = 0
            dr = r - pr[0]
            dg = g - pg[0]
            db = b - pb[0]
            best_d = dr * dr + dg * dg + db * db
            for i in range(1, n_pal):
                dr = r - pr[i]
                dg = g - pg[i]
                db = b - pb[i]
                d = dr * dr + dg * dg + db * db
                if d < best_d:
                    best_d = d
                    best_i = i
            out[idx] = best_i
            er = r - pr[best_i]
            eg = g - pg[best_i]
            eb = b - pb[best_i]
            for k in range(n_weights):
                ny = y + w_dy[k]
                nx = x + w_dx[k]
                if 0 <= ny < H and 0 <= nx < W:
                    frac = w_frac[k]
                    nidx = ny * W + nx
                    buf_r[nidx] += er * frac
                    buf_g[nidx] += eg * frac
                    buf_b[nidx] += eb * frac
    return bytes(out)


# Atkinson: 6 neighbours each at 1/8 — only 6/8 of the error propagates,
# the rest is intentionally discarded for the classic Mac contrasty look.
_ATKINSON_WEIGHTS: list[tuple[int, int, float]] = [
    (0, 1, 1 / 8),
    (0, 2, 1 / 8),
    (1, -1, 1 / 8),
    (1, 0, 1 / 8),
    (1, 1, 1 / 8),
    (2, 0, 1 / 8),
]

# Jarvis-Judice-Ninke: 12 taps, weights /48. Smoother gradients than FS.
_JJN_WEIGHTS: list[tuple[int, int, float]] = [
    (0, 1, 7 / 48), (0, 2, 5 / 48),
    (1, -2, 3 / 48), (1, -1, 5 / 48), (1, 0, 7 / 48), (1, 1, 5 / 48), (1, 2, 3 / 48),
    (2, -2, 1 / 48), (2, -1, 3 / 48), (2, 0, 5 / 48), (2, 1, 3 / 48), (2, 2, 1 / 48),
]  # fmt: skip

# Stucki: 12 taps, weights /42. Sharper than JJN, smoother than FS.
_STUCKI_WEIGHTS: list[tuple[int, int, float]] = [
    (0, 1, 8 / 42), (0, 2, 4 / 42),
    (1, -2, 2 / 42), (1, -1, 4 / 42), (1, 0, 8 / 42), (1, 1, 4 / 42), (1, 2, 2 / 42),
    (2, -2, 1 / 42), (2, -1, 2 / 42), (2, 0, 4 / 42), (2, 1, 2 / 42), (2, 2, 1 / 42),
]  # fmt: skip


# 8x8 Bayer threshold (recursive 2x expansion of [[0,2],[3,1]]) / 64.
# Classic ordered-dither tile — deterministic crosshatch, no error
# propagation, vectorises trivially.
_BAYER_8X8: np.ndarray = (
    np.array(
        [
            [0, 32, 8, 40, 2, 34, 10, 42],
            [48, 16, 56, 24, 50, 18, 58, 26],
            [12, 44, 4, 36, 14, 46, 6, 38],
            [60, 28, 52, 20, 62, 30, 54, 22],
            [3, 35, 11, 43, 1, 33, 9, 41],
            [51, 19, 59, 27, 49, 17, 57, 25],
            [15, 47, 7, 39, 13, 45, 5, 37],
            [63, 31, 55, 23, 61, 29, 53, 21],
        ],
        dtype=np.float32,
    )
    / 64.0
)


def _dither_ordered(
    rgb: Image.Image,
    palette: np.ndarray,
    matrix: np.ndarray,
    *,
    strength: float = 64.0,
) -> bytes:
    """Generic ordered (threshold-matrix) dither. Vectorised across the
    whole image — ~10x faster than the error-diffusion paths."""
    arr = np.asarray(rgb, dtype=np.float32)
    H, W, _ = arr.shape
    mh, mw = matrix.shape
    threshold = np.tile(matrix, ((H + mh - 1) // mh, (W + mw - 1) // mw))[:H, :W]
    offset = (threshold - 0.5) * strength
    biased = arr + offset[..., None]
    indices = _nearest_palette_indices(biased, palette)
    return bytes(indices.astype(np.uint8).tobytes())


def _make_halftone_matrix(size: int = 16) -> np.ndarray:
    """Clustered-dot halftone — threshold grows radially from each cell
    centre, so darker pixels fill outward as circular dots."""
    cx = (size - 1) / 2
    cy = (size - 1) / 2
    mat = np.zeros((size, size), dtype=np.float32)
    for i in range(size):
        for j in range(size):
            mat[i, j] = float(((i - cx) ** 2 + (j - cy) ** 2) ** 0.5)
    out: np.ndarray = mat / mat.max()
    return out


def _make_crosshatch_matrix(size: int = 8) -> np.ndarray:
    """Pen-and-ink crosshatch — diagonals darken first as the image darkens."""
    mat = np.zeros((size, size), dtype=np.float32)
    for i in range(size):
        for j in range(size):
            d_main = abs(i - j)
            d_anti = abs(i + j - (size - 1))
            mat[i, j] = float(min(d_main, d_anti))
    out: np.ndarray = mat / mat.max()
    return out


_HALFTONE_16: np.ndarray = _make_halftone_matrix(16)
_CROSSHATCH_8: np.ndarray = _make_crosshatch_matrix(8)


def pack_to_panel_bin(
    img: Image.Image,
    *,
    width: int,
    height: int,
    dither: DitherMode = "floyd-steinberg",
    saturation: float = 1.0,
    contrast: float = 1.0,
    gamut: str = "waveshare_e6",
) -> bytes:
    """Quantise against the selected ``gamut`` palette and pack to the
    panel's native 4-bpp buffer. Layout: ``height`` rows x ``width/2`` bytes
    each, scanline order, high nibble = even column (col 0, 2, …), low nibble
    = odd column. Matches the firmware's ``epd_display`` SPI stream.

    ``gamut`` selects the target palette + nibble LUT (see ``PANEL_GAMUTS``):
    ``waveshare_e6`` (6 colours, the default — ESP32 firmware + Waveshare E6
    Pi clients) or ``inky_7colour`` (7 colours incl. orange, indices matching
    the Pimoroni inky library so a Pi client writes them straight to the
    UC8159 buffer). An unknown value falls back to ``waveshare_e6``.

    ``width`` must be even. The image must already be at
    ``(width, height)`` — callers resize / orient first (the dashboard
    composer produces panel-sized output; the Send page uploads use
    ``fit_to_panel`` before reaching this point).

    Quantisation knobs (only the .bin path runs them — the Pi PNG listener
    owns gamut projection on its end):

    * ``dither`` — ``floyd-steinberg`` (default), ``none``, ``atkinson``,
      ``jarvis``, ``stucki``, ``bayer-8x8``, ``halftone``, ``crosshatch``.
      FS is the fast smooth-gradient default. Bayer-8x8 is the fastest
      non-FS option. The error-diffusion alternatives run on a Python
      hot loop — noticeably slower (several seconds at 1200x1600) but
      opt-in.
    * ``saturation`` — pre-quantise multiplier (1.0 = no change). ~1.3-1.5
      pushes near-saturated colours onto the palette before dither kicks
      in; kills the speck artefact on near-native solids.
    * ``contrast`` — pre-quantise multiplier (1.0 = no change). Useful
      when near-black / near-white regions are dithering noisily into
      grey equivalents on the panel.
    """
    if width % 2:
        raise ValueError(f"panel width must be even (two pixels per byte), got {width}")
    if img.size != (width, height):
        raise ValueError(f"image must be {width}x{height}, got {img.size}")

    palette, nibble_by_index = _GAMUT_TABLE.get(gamut, _GAMUT_TABLE["waveshare_e6"])
    pal_arr = np.array(palette, dtype=np.float32)

    rgb = img.convert("RGB")
    # ImageEnhance is C-speed and idempotent at factor=1.0 (no-op fast path).
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)

    if dither in _PIL_DITHER_MAP:
        pal_img = _palette_image(palette)
        indexed = rgb.quantize(palette=pal_img, dither=_PIL_DITHER_MAP[dither])
        raw = indexed.tobytes()
    elif dither == "atkinson":
        raw = _error_diffusion(rgb, pal_arr, _ATKINSON_WEIGHTS)
    elif dither == "jarvis":
        raw = _error_diffusion(rgb, pal_arr, _JJN_WEIGHTS)
    elif dither == "stucki":
        raw = _error_diffusion(rgb, pal_arr, _STUCKI_WEIGHTS)
    elif dither == "bayer-8x8":
        raw = _dither_ordered(rgb, pal_arr, _BAYER_8X8)
    elif dither == "halftone":
        raw = _dither_ordered(rgb, pal_arr, _HALFTONE_16, strength=128.0)
    elif dither == "crosshatch":
        raw = _dither_ordered(rgb, pal_arr, _CROSSHATCH_8, strength=96.0)
    else:
        raise ValueError(f"unknown dither mode: {dither!r}")

    # palette index -> firmware/library nibble via bytes.translate (C-speed).
    # 256-byte LUT; anything past the gamut's entries falls through to 0x0
    # (safe black).
    lut = bytearray(256)
    for i, nibble in enumerate(nibble_by_index):
        lut[i] = nibble
    nibbles = raw.translate(bytes(lut))

    # Pack two-per-byte. The firmware refuses any other layout.
    evens = nibbles[0::2]
    odds = nibbles[1::2]
    packed = bytes((e << 4) | o for e, o in zip(evens, odds, strict=True))
    expected = width * height // 2
    assert len(packed) == expected, f"packed buffer is {len(packed)}, expected {expected}"
    return packed
