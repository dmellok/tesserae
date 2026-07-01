"""Image ops used by renderers.

Two responsibility groups:

* **Pure transforms**, ``rotate_png``, ``apply_underscan``, ``fit_to_panel``.
  Composable, no panel/palette assumptions, used by any renderer.
* **Spectra 6 / Waveshare E6 packing**, ``pack_to_panel_bin`` produces the
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
from PIL import Image, ImageEnhance, ImageFilter

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
# ink primaries, the panel firmware does the actual gamut projection.
# Used by Pi-side PNG listeners that quantise their own buffer (the
# inky library projects back to its own palette anyway, so calibrated
# targets here would just feed two rounds of misprojection).
SPECTRA_6_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # black
    (255, 255, 255),  # white
    (255, 255, 0),  # yellow
    (255, 0, 0),  # red
    (0, 0, 255),  # blue
    (0, 255, 0),  # green
    (255, 140, 0),  # orange
)


# Waveshare E6 6-colour palette, the .bin packers' default target. No
# orange: the firmware reserves nibble 0x4 (we map blue to 0x5, green
# to 0x6 via the LUT below) so the gamut is one colour smaller than
# Spectra 6.
#
# Palette order matters: Pillow's quantize emits indices 0…N-1 in the
# order we declare. The LUT translates those to firmware nibbles.
#
# Calibrated alternative below
# (``WAVESHARE_E6_CALIBRATED_PALETTE``) is opt-in via the per-device
# ``calibrated`` toggle and pairs with ``_compress_to_calibrated_range``;
# enabling it without the tone-mapping pre-pass collapses mid-tones to
# solid paper-grey on the panel, which is why the swap stays gated.
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


# Calibrated targets, what the panels actually reproduce under normal
# viewing light. Used **only** when the per-device ``calibrated`` toggle
# is on, and always paired with the ``_compress_to_calibrated_range``
# tone-mapping pass below: the palette alone makes mid-tones collapse
# to solid calibrated-white (washed out); the tone-mapping rescales the
# source range into [calibrated_black, calibrated_white] so dither has
# room to work again.
#
# Order matches the nominal palettes above so the same nibble LUT
# applies to both.
#
# Ported from paperlesspaper/epdoptimize (Apache 2.0), specifically the
# ``spectra6`` (E6) and ``acep`` (Inky 7-colour) profiles in
# ``src/dither/data/default-palettes.json``. See NOTICES.md.
WAVESHARE_E6_CALIBRATED_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0x1F, 0x22, 0x26),  # 0 black   (panel dark slate)
    (0xB9, 0xC7, 0xC9),  # 1 white   (panel paper)
    (0xC1, 0xBB, 0x1E),  # 2 yellow  (mustard)
    (0x62, 0x20, 0x1E),  # 3 red     (dusty red)
    (0x23, 0x3F, 0x8E),  # 4 blue    (navy)
    (0x35, 0x56, 0x3A),  # 5 green   (forest)
)
INKY_7COLOUR_CALIBRATED_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0x19, 0x1E, 0x21),  # 0 black
    (0xF1, 0xF1, 0xF1),  # 1 white
    (0x53, 0xA4, 0x28),  # 2 green
    (0x31, 0x31, 0x8F),  # 3 blue
    (0xD2, 0x0E, 0x13),  # 4 red
    (0xF3, 0xCF, 0x11),  # 5 yellow
    (0xB8, 0x5E, 0x1C),  # 6 orange  (brick)
)


# Panel colour gamuts the .bin packer can target, keyed by the value
# stored on a device's panel block. Each maps to (palette, nibble LUT).
# ``waveshare_e6`` is the default everywhere so existing devices and the
# always-E6 ESP32 path are unchanged.
PanelGamut = Literal["waveshare_e6", "inky_7colour"]
PANEL_GAMUTS: tuple[str, ...] = ("waveshare_e6", "inky_7colour")
# Look up the calibrated palette for a gamut, or None when no calibration
# profile exists (custom panels, future gamuts). Both calibrated palettes
# use the same nibble LUT as their nominal counterparts so the on-wire
# bytes are unchanged, only the dither targets and the source tone
# mapping change.
_CALIBRATED_PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "waveshare_e6": WAVESHARE_E6_CALIBRATED_PALETTE,
    "inky_7colour": INKY_7COLOUR_CALIBRATED_PALETTE,
}


def _compress_to_calibrated_range(
    img: Image.Image, palette: tuple[tuple[int, int, int], ...]
) -> Image.Image:
    """Linearly remap a source's [0, 255] RGB range into the palette's
    [calibrated_black, calibrated_white] band so the calibrated palette
    actually has data to dither against.

    Without this step the palette alone makes mid-tones collapse: a
    near-white source pixel is already inside the calibrated white's
    neighbourhood (the white target is ~#B9C7C9, not pure white), so
    Floyd-Steinberg sees zero error to diffuse and the panel paints a
    flat block of paper-grey. After compression, the same source pixel
    sits well into the upper end of the palette's range with real error
    to spread, the dither field reappears and the panel reads as
    properly bright.

    Linear per-channel rescale is the dumber-but-honest version of
    epdoptimize's LAB dynamic-range compression; the panel itself is so
    nonlinear that any tone curve is an approximation, and the perceptual
    delta between this and a proper LAB pass isn't worth the numpy/colour
    complexity at the gamut sizes we target. The first and second palette
    entries are always (black, white) in both the E6 and 7-colour decks.
    """
    if len(palette) < 2:
        return img
    pmin = np.array(palette[0], dtype=np.float32)
    pmax = np.array(palette[1], dtype=np.float32)
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    scaled = arr * (pmax - pmin) / 255.0 + pmin
    return Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8))


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


def underscan_image(img: Image.Image, *, underscan: int, fill: str = "#ffffff") -> Image.Image:
    """Inset ``img`` by ``underscan`` pixels on every edge, padding the
    border with ``fill``. Size is preserved: content is downscaled to
    (W-2u, H-2u) and pasted at (u, u). Compensates for a physical mat /
    bezel covering the screen edge, the border sits under the mat, so its
    colour is invisible in practice. No-op for ``underscan <= 0`` or when
    the inset would consume the whole image."""
    if underscan <= 0:
        return img
    rgb = img.convert("RGB")
    w, h = rgb.size
    inner_w = w - 2 * underscan
    inner_h = h - 2 * underscan
    if inner_w <= 0 or inner_h <= 0:
        return rgb
    inner = rgb.resize((inner_w, inner_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), fill)
    canvas.paste(inner, (underscan, underscan))
    return canvas


def apply_underscan(png_bytes: bytes, *, underscan: int, fill: str = "#ffffff") -> bytes:
    """``underscan_image`` for the PNG-bytes renderers (pi_png). Re-encodes
    only when an inset is actually applied."""
    if underscan <= 0:
        return png_bytes
    img = Image.open(io.BytesIO(png_bytes))
    out_img = underscan_image(img, underscan=underscan, fill=fill)
    out = io.BytesIO()
    out_img.save(out, format="PNG", optimize=True)
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

    * ``fit``    , preserve aspect, letterbox with ``bg``.
    * ``fill``   , preserve aspect, crop overflow.
    * ``stretch``, squash to exact dims.
    * ``center`` , paste original at panel centre; clip overflow, ``bg``
                    elsewhere.
    * ``blur``   , preserve aspect (as ``fit``) over a blurred, cover-cropped
                    copy of the image so the letterbox area is filled.

    Used by the Send page when the uploaded image isn't already panel
    sized. Dashboard renders skip this, the composer emits panel-exact PNGs."""
    src = img.convert("RGB")
    if src.size == (target_w, target_h) and scale != "blur":
        return src
    if scale == "stretch":
        return src.resize((target_w, target_h), Image.Resampling.LANCZOS)
    if scale == "blur":
        # Cover-crop a copy to fill the panel, blur it for the backdrop,
        # then paste the aspect-fit image centred on top.
        background = fit_to_panel(src, target_w=target_w, target_h=target_h, scale="fill", bg=bg)
        radius = max(8, min(target_w, target_h) // 16)
        background = background.filter(ImageFilter.GaussianBlur(radius))
        ratio = src.width / src.height
        if ratio > target_w / target_h:
            fg_w, fg_h = target_w, max(1, round(target_w / ratio))
        else:
            fg_w, fg_h = max(1, round(target_h * ratio)), target_h
        fg = src.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
        background.paste(fg, ((target_w - fg_w) // 2, (target_h - fg_h) // 2))
        return background
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
    """Vectorised nearest-palette lookup. Squared euclidean in sRGB -
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
    ``array.array`` flat buffers, ~3x faster than indexing numpy in the
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


# Atkinson: 6 neighbours each at 1/8, only 6/8 of the error propagates,
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
# Classic ordered-dither tile, deterministic crosshatch, no error
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
    whole image, ~10x faster than the error-diffusion paths."""
    arr = np.asarray(rgb, dtype=np.float32)
    H, W, _ = arr.shape
    mh, mw = matrix.shape
    threshold = np.tile(matrix, ((H + mh - 1) // mh, (W + mw - 1) // mw))[:H, :W]
    offset = (threshold - 0.5) * strength
    biased = arr + offset[..., None]
    indices = _nearest_palette_indices(biased, palette)
    return bytes(indices.astype(np.uint8).tobytes())


def _make_halftone_matrix(size: int = 16) -> np.ndarray:
    """Clustered-dot halftone, threshold grows radially from each cell
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
    """Pen-and-ink crosshatch, diagonals darken first as the image darkens."""
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
    calibrated: bool = False,
) -> bytes:
    """Quantise against the selected ``gamut`` palette and pack to the
    panel's native 4-bpp buffer. Layout: ``height`` rows x ``width/2`` bytes
    each, scanline order, high nibble = even column (col 0, 2, …), low nibble
    = odd column. Matches the firmware's ``epd_display`` SPI stream.

    ``gamut`` selects the target palette + nibble LUT (see ``PANEL_GAMUTS``):
    ``waveshare_e6`` (6 colours, the default, ESP32 firmware + Waveshare E6
    Pi clients) or ``inky_7colour`` (7 colours incl. orange, indices matching
    the Pimoroni inky library so a Pi client writes them straight to the
    UC8159 buffer). An unknown value falls back to ``waveshare_e6``.

    ``calibrated`` swaps the dither target from nominal sRGB primaries to
    measured panel colours and runs a tone-mapping pre-pass that squeezes
    the source's [0, 255] range into the panel's reproducible band. Pair
    works as one: the palette alone makes mid-tones collapse without the
    tone mapping; the tone mapping alone darkens nothing because it
    quantizes against the unchanged nominal targets. Off by default, turn
    it on per device once you've A/B'd a frame and decided the calibrated
    look beats the nominal one for that panel and that content.

    ``width`` must be even. The image must already be at
    ``(width, height)``, callers resize / orient first (the dashboard
    composer produces panel-sized output; the Send page uploads use
    ``fit_to_panel`` before reaching this point).

    Quantisation knobs (only the .bin path runs them, the Pi PNG listener
    owns gamut projection on its end):

    * ``dither``, ``floyd-steinberg`` (default), ``none``, ``atkinson``,
      ``jarvis``, ``stucki``, ``bayer-8x8``, ``halftone``, ``crosshatch``.
      FS is the fast smooth-gradient default. Bayer-8x8 is the fastest
      non-FS option. The error-diffusion alternatives run on a Python
      hot loop, noticeably slower (several seconds at 1200x1600) but
      opt-in.
    * ``saturation``, pre-quantise multiplier (1.0 = no change). ~1.3-1.5
      pushes near-saturated colours onto the palette before dither kicks
      in; kills the speck artefact on near-native solids.
    * ``contrast``, pre-quantise multiplier (1.0 = no change). Useful
      when near-black / near-white regions are dithering noisily into
      grey equivalents on the panel.
    """
    if width % 2:
        raise ValueError(f"panel width must be even (two pixels per byte), got {width}")
    if img.size != (width, height):
        raise ValueError(f"image must be {width}x{height}, got {img.size}")

    palette, nibble_by_index = _GAMUT_TABLE.get(gamut, _GAMUT_TABLE["waveshare_e6"])
    # When the caller opts into calibration, swap the dither target to the
    # calibrated palette for this gamut (if we have one) and compress the
    # source range into the calibrated [black, white] band so the dither
    # has somewhere to spread error to. Nibble LUT stays the rendered
    # gamut's, on-wire bytes are unchanged.
    calibrated_active = calibrated and gamut in _CALIBRATED_PALETTES
    if calibrated_active:
        palette = _CALIBRATED_PALETTES[gamut]
    pal_arr = np.array(palette, dtype=np.float32)

    rgb = img.convert("RGB")
    if calibrated_active:
        rgb = _compress_to_calibrated_range(rgb, palette)
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


# 2-colour mono palette used by the 1-bpp packer below. Index 0 = black,
# index 1 = white, which matches the firmware wire convention
# (bit-set = white, bit-clear = black). Defined at module scope so callers
# (and tests) can reference it without re-typing the tuple.
_MONO_PALETTE_2: tuple[tuple[int, int, int], tuple[int, int, int]] = (
    (0, 0, 0),
    (255, 255, 255),
)


def pack_to_panel_bin_1bpp(
    img: Image.Image,
    *,
    width: int,
    height: int,
    dither: DitherMode = "floyd-steinberg",
    contrast: float = 1.0,
) -> bytes:
    """Quantise to mono B/W and pack to the firmware's 1-bpp wire format.

    Mirror of :func:`pack_to_panel_bin` for 1-bpp panels (Waveshare 4.2"
    B/W + the `esp32_bw_bin` renderer). Same dither pipeline (every mode
    `pack_to_panel_bin` supports works here too), 2-colour palette, and
    a different pack stride.

    Wire layout:

    * Exactly ``width * height / 8`` bytes (15000 for 400x300). No
      header, no padding, no checksum.
    * Scanline order, 8 pixels per byte, **MSB = leftmost pixel**
      (``np.packbits`` default).
    * **bit-set (1) = white, bit-clear (0) = black** (palette index 1
      maps to white). A fully white scanline is 0xFF bytes; a fully
      black scanline is 0x00.

    ``width`` must be a multiple of 8 (no row padding, so partial
    bytes at the right edge would silently corrupt the packing). The
    image must already be at ``(width, height)``; callers
    ``fit_to_panel`` first.

    The dither pre-pass takes the same ``contrast`` knob as the colour
    packer because that's the single most useful tuning lever on a
    photo / text mixed dashboard: bump > 1 to deepen black-or-white
    decisions on photos, drop < 1 to soften text-heavy frames.
    """
    if width % 8:
        raise ValueError(f"panel width must be a multiple of 8 (8 pixels per byte), got {width}")
    if img.size != (width, height):
        raise ValueError(f"image must be {width}x{height}, got {img.size}")

    palette = _MONO_PALETTE_2
    pal_arr = np.array(palette, dtype=np.float32)

    rgb = img.convert("RGB")
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)

    # Same dither branch as pack_to_panel_bin, just against the 2-colour
    # mono palette. Every mode produces palette indices (0=black, 1=white)
    # at the end of this block.
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

    # Palette indices: 0 = black, 1 = white. Bit-set must be white,
    # so the mask straight from the indices already matches the wire
    # convention. np.packbits defaults to bitorder='big', i.e. MSB
    # is the first (leftmost) pixel, which is also what the firmware
    # expects (matches tools/gen_splash.py's pack_1bpp in the firmware
    # repo: np.packbits(white_mask, axis=1)).
    indices = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
    # Tolerate strays past index 1 (a buggy dither implementation):
    # clamp anything above 0 down to 1 so we still get a 1-bit mask.
    white_mask = (indices >= 1).astype(np.uint8)
    packed_arr = np.packbits(white_mask, axis=1)
    packed = packed_arr.tobytes()

    expected = width * height // 8
    assert len(packed) == expected, f"packed buffer is {len(packed)}, expected {expected}"
    return packed


# 16-level linear grayscale palette. Index i -> RGB (i * 17, i * 17, i * 17),
# so 0 -> (0,0,0) black and 15 -> (255,255,255) white. Used by
# ``pack_to_panel_bin_4bpp_gray`` (Seeed reTerminal E1003 + any other
# IT8951-driven grayscale panel). Palette indices are byte-identical to
# the target nibble values on the wire, so the packer skips the LUT step
# the colour packer needs.
_GRAY_16_PALETTE: tuple[tuple[int, int, int], ...] = tuple(
    (i * 17, i * 17, i * 17) for i in range(16)
)


def pack_to_panel_bin_4bpp_gray(
    img: Image.Image,
    *,
    width: int,
    height: int,
    dither: DitherMode = "floyd-steinberg",
    contrast: float = 1.0,
) -> bytes:
    """Quantise to 16-level grayscale and pack to the panel's native 4-bpp
    grayscale wire format.

    Mirror of :func:`pack_to_panel_bin` for grayscale IT8951 panels (the
    Seeed reTerminal E1003 in particular, 1872x1404, 10.3 inch). Uses a
    16-entry linear grayscale palette instead of the Spectra 6 gamut,
    and skips the palette-index-to-firmware-nibble LUT because index
    equals nibble here.

    Wire layout:

    * Exactly ``width * height / 2`` bytes (1314144 for the E1003's
      1872x1404 panel). No header, no padding, no checksum.
    * Row-major, top-left origin, no mirror. The firmware handles any
      physical panel-side mirror itself; the renderer keeps a normal
      orientation so a photo painted on the panel reads the same way it
      does on a browser preview.
    * 4-bpp packed, scanline order, no row padding: ``width / 2`` bytes
      per row.
    * **HIGH nibble = LEFT pixel** of each byte-pair (even column: 0,
      2, 4, ...), **LOW nibble = RIGHT pixel** (odd column: 1, 3, ...).
    * Gray value per nibble: **0x0 = black, 0xF = white**, linear 4-bit
      gray. Nothing else to translate.

    ``width`` must be even (two pixels per byte). Image must already be
    at ``(width, height)``, caller sizes / orients first.

    The dither pre-pass uses the same modes as ``pack_to_panel_bin``,
    against a 16-entry linear grayscale palette. Floyd-Steinberg on
    grayscale is the smoothest for photos; ordered dither modes are
    the fastest opt-ins for text-heavy dashboards.
    """
    if width % 2:
        raise ValueError(f"panel width must be even (two pixels per byte), got {width}")
    if img.size != (width, height):
        raise ValueError(f"image must be {width}x{height}, got {img.size}")

    palette = _GRAY_16_PALETTE
    pal_arr = np.array(palette, dtype=np.float32)

    # Grayscale-first, then back to RGB so the dither routines that
    # expect an RGB source (all of them) see uniform-channel data.
    # Contrast bump applies after the grayscale conversion so it
    # deepens gray extremes rather than modifying channel imbalance
    # that no longer exists.
    gray = img.convert("L")
    if contrast != 1.0:
        gray = ImageEnhance.Contrast(gray).enhance(contrast)
    rgb = gray.convert("RGB")

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

    # Palette indices are 0..15 already; each maps directly to a nibble
    # value (0 = black, 15 = white). Reshape, split even/odd columns
    # (columns 0, 2, ... land in the high nibble; 1, 3, ... in the low
    # nibble), pack, done. All-numpy so it stays fast on 2.6M-pixel
    # panels.
    indices = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
    # Clamp any stray > 15 down to 15 so a buggy dither output can't
    # corrupt neighbouring nibbles when we shift it.
    indices = np.clip(indices, 0, 15).astype(np.uint8)
    left = indices[:, 0::2]  # even columns -> high nibble
    right = indices[:, 1::2]  # odd columns  -> low nibble
    packed = (left << 4) | right

    out = packed.tobytes()
    expected = width * height // 2
    assert len(out) == expected, f"packed buffer is {len(out)}, expected {expected}"
    return out
