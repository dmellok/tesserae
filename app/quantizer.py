"""Image ops used by renderers.

Two responsibility groups:

* **Pure transforms**, ``rotate_png``, ``apply_underscan``, ``fit_to_panel``.
  Composable, no panel/palette assumptions, used by any renderer.
* **Colour / mono packing**, ``pack_to_panel_bin`` produces the panel-
  native buffer that ESP32 firmware streams to SPI and Pi-side binary
  listeners consume. Bit width depends on the gamut:
  4-bpp nibble-packed for 6/7-colour panels
  (``waveshare_e6``, ``inky_7colour``), and 2-bpp native for 4-colour
  BWRY panels (``bwry_4``, PicPak class). Dither dispatch (Pillow's
  Floyd-Steinberg / none + our own numpy-backed atkinson / jarvis /
  stucki / bayer-8x8 / halftone / crosshatch) lives here.

Lifted largely from inky-dash's ``app.quantizer``. The 4-bpp packing
layout (``width * height // 2`` bytes, high nibble = even column,
palette to firmware-nibble LUT) is byte-compatible with the existing
``dmellok/esp32-inky-dash-client`` firmware.
"""

from __future__ import annotations

import io
from typing import Any, Literal, TypedDict

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

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


# 4-colour BWRY palette (v0.69.3 for PicPak support; v0.69.4 flipped
# the wire format from 4-bpp nibble-packed to native 2-bpp and swapped
# the Y/R indices to match the PicPak controller's palette order).
# Physical panels of this class (400 x 300 PicPak, Waveshare 4.2"
# DEPG0420BR variants, other B/W/Red/Yellow SKUs) reproduce these four
# colours only; no green, no blue, no grey. Server dithers to this
# palette when the panel declares ``gamut = "bwry_4"``.
#
# Palette index equals the wire value. Order is (black, white, yellow,
# red) so index 0 -> 0x0, 1 -> 0x1, 2 -> 0x2, 3 -> 0x3, and the packer
# skips the palette-index-to-nibble translation entirely. Matches the
# PicPak's on-panel palette register (0x0 black, 0x1 white, 0x2 yellow,
# 0x3 red).
#
# Naming is chemistry-only (not ``waveshare_bwry`` etc.) because we
# define the on-wire layout ourselves for this gamut: no manufacturer-
# specific packing convention exists yet in the fleet, so there's
# nothing to alias. If a manufacturer-specific packing shows up later
# (a firmware that reserves specific values the way Waveshare's E6
# firmware reserves 0x4 and 0x7 in the 4-bpp layout), we'd add
# ``waveshare_bwry`` / etc. alongside and canonicalise into it.
BWRY_4_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # 0 -> 0x0 black
    (255, 255, 255),  # 1 -> 0x1 white
    (255, 255, 0),  # 2 -> 0x2 yellow
    (255, 0, 0),  # 3 -> 0x3 red
)

# Identity LUT. Kept as a constant so ``_GAMUT_TABLE``'s shape is
# uniform across gamuts (every entry is a ``(palette, nibble_lut)``
# pair). ``pack_to_panel_bin`` still runs the ``bytes.translate`` step
# for BWRY, but the identity mapping means it's a no-op on the payload;
# the 2-bpp packer that runs afterwards reads the palette indices out
# of the untranslated buffer directly.
_BWRY_4_NIBBLE_BY_PALETTE_INDEX: tuple[int, ...] = (0x0, 0x1, 0x2, 0x3)

# Black / White / Red tri-colour panels (the most common highlight
# e-ink: Waveshare 2.13"/2.9"/4.2" "B" variants, many Pimoroni Inky
# pHAT reds). Same 2-bit indexed family as BWRY, one highlight instead
# of two. Used by the PNG renderers (``circuitpython_png``) so a client
# can declare ``gamut = "bwr_3"`` and get a 3-colour indexed image
# instead of wasting a palette slot on the unused yellow of ``bwry_4``.
# Red matches the BWRY red so a mixed BWR + BWRY fleet dithers reds the
# same way.
BWR_3_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # black
    (255, 255, 255),  # white
    (255, 0, 0),  # red
)

# Four-level greyscale ramp (2-bit, no highlight): the grayscale e-ink
# class CircuitPython's display stack drives as a 2-bit indexed buffer
# (Waveshare 4.2" grayscale, some GDEW panels). Distinct from ``mono``
# (2 levels) and from BWR/BWRY (highlight colours, not greys). Nominal
# even ramp; a real panel's mid-greys drift, but the renderer emits the
# nominal palette so the wire format stays deterministic per gamut
# (calibrated ramps can follow the same path ``bwry_4`` will).
GRAY_4_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),  # black
    (85, 85, 85),  # dark grey
    (170, 170, 170),  # light grey
    (255, 255, 255),  # white
)

# 16-level (4-bit) grayscale ramp for the IT8951-class panels (Seeed
# reTerminal E1003, TRMNL X). Even 0..255 ramp in 17-step increments;
# these panels dither a full-range source to 16 greys, so the palette is
# metadata / dither guidance, not a packer target (the 4-bpp gray packer
# emits the level index directly). Same rationale as GRAY_4 for the drift.
GRAY_16_PALETTE: tuple[tuple[int, int, int], ...] = tuple((v, v, v) for v in range(0, 256, 17))


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
# 4-colour BWRY (PicPak class). Calibrated against a physical PicPak 4.2"
# panel by varanu5 <https://github.com/varanu5>, rather than ported from
# epdoptimize, which carries no BWRY profile. Panel batches drift, so the
# Calibration tab lets anyone fork these and adjust.
#
# The nominal palette these replace is the *ideal* sRGB primaries, and
# the gap is what makes uncalibrated BWRY output look muddy. Yellow is
# the worst case: dithering against (255, 255, 0) treats it as a bright
# highlight, but the ink lays down a dark mustard, so error diffusion
# picks yellow for highlights it cannot deliver. The reproducible range
# is also roughly [36, 236], not [0, 255], so an uncalibrated dither
# over-drives contrast against endpoints the panel never reaches.
#
# Order matches BWRY_4_PALETTE (black, white, yellow, red) so the same
# identity nibble LUT applies and the on-wire bytes are unchanged.
BWRY_4_CALIBRATED_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0x24, 0x25, 0x22),  # 0 black   warm near-black, never a true 0
    (0xEC, 0xE9, 0xDF),  # 1 white   slightly cream paper
    (0xDE, 0xB4, 0x28),  # 2 yellow  mustard, not lemon
    (0xBC, 0x42, 0x48),  # 3 red     dusty brick
)


# Panel colour gamuts the .bin packer can target, keyed by the value
# stored on a device's panel block. Each maps to (palette, nibble LUT).
# ``waveshare_e6`` is the default everywhere so existing devices and the
# always-E6 ESP32 path are unchanged.
#
# On the manufacturer names: same ink chemistry, different on-wire
# byte layouts. Waveshare's firmware reserves nibbles 0x4 and 0x7 for
# the Spectra 6 palette (blue remaps to 0x5, green to 0x6);
# Pimoroni's inky library targets the UC8159's native palette order
# verbatim for ACeP. "Spectra 6" alone doesn't tell the packer which
# byte layout to produce, so the canonical gamut string has to say
# "Spectra 6 done Waveshare-style" (``waveshare_e6``) or "ACeP done
# Inky-style" (``inky_7colour``). Chemistry-only aliases
# (``spectra_6``, ``acep_7colour``) exist in :data:`ACCEPTED_GAMUTS`
# so a client can declare "I'm a Spectra 6 panel" without knowing
# which packing scheme its driver expects; the alias resolves to the
# canonical form via :func:`canonicalise_gamut` at persistence time.
# Renaming the canonical values to something chemistry-neutral would
# require migrating every existing on-disk config, so the awkward
# names stay for backward compat and the aliases carry the semantic
# intent.
PanelGamut = Literal["waveshare_e6", "inky_7colour", "bwry_4", "bwr_3"]
PANEL_GAMUTS: tuple[str, ...] = ("waveshare_e6", "inky_7colour", "bwry_4", "bwr_3")

# Wider allow-list for values a client can *declare* over
# /api/v1/device/{discover, register} (added v0.69.1 for issue #41
# and the follow-up comment asking for rgb24/rgb16 coverage).
# Semantic labels (``spectra_6``,
# ``acep_7colour``) alias into the canonical PANEL_GAMUTS values at
# persistence time so the .bin packer's lookup keeps working; the
# rest (``mono``, ``rgb24``, ``rgb16``, ``bwr_3``, ``gray_4``) sit as
# metadata for renderers / clients that key off panel type
# (CircuitPython generic driver, TRMNL mono path, future full-colour
# LCD hybrids) without going through the .bin packer. ``bwr_3`` (tri-
# colour B/W/Red) and ``gray_4`` (2-bit greyscale ramp) are the
# CircuitPython "grayscale" 2-bit family the ``circuitpython_png``
# renderer quantises to an indexed PNG.
ACCEPTED_GAMUTS: frozenset[str] = frozenset(
    {
        "waveshare_e6",
        "inky_7colour",
        "spectra_6",
        "acep_7colour",
        "mono",
        "rgb24",
        "rgb16",
        "bwry_4",
        "bwr_3",
        "gray_4",
        "gray_16",
    }
)

# Aliases from declared-name to canonical PANEL_GAMUTS entry. Applied at
# persistence time so the on-disk panel block always carries a value
# the .bin packer understands (or a passthrough label for renderers
# that don't care).
_GAMUT_ALIASES: dict[str, str] = {
    "spectra_6": "waveshare_e6",
    "acep_7colour": "inky_7colour",
}


def canonicalise_gamut(declared: str) -> str:
    """Map a declared gamut label to its canonical panel-block value.

    Preserves ``waveshare_e6`` and ``inky_7colour`` verbatim (the .bin
    packer's targets); collapses semantic labels (``spectra_6``,
    ``acep_7colour``) onto their canonical equivalents; passes through
    ``mono``, ``rgb24``, ``rgb16`` for renderers that key off panel
    type without going through the packer. Unknown values fall back
    to ``waveshare_e6`` so a corrupt payload can't strand the device
    with a nonsense panel."""
    if declared in _GAMUT_ALIASES:
        return _GAMUT_ALIASES[declared]
    if declared in ACCEPTED_GAMUTS:
        return declared
    return "waveshare_e6"


# Look up the calibrated palette for a gamut, or None when no calibration
# profile exists (custom panels, future gamuts). Both calibrated palettes
# use the same nibble LUT as their nominal counterparts so the on-wire
# bytes are unchanged, only the dither targets and the source tone
# mapping change.
_CALIBRATED_PALETTES: dict[str, tuple[tuple[int, int, int], ...]] = {
    "waveshare_e6": WAVESHARE_E6_CALIBRATED_PALETTE,
    "inky_7colour": INKY_7COLOUR_CALIBRATED_PALETTE,
    "bwry_4": BWRY_4_CALIBRATED_PALETTE,
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


# Identity LUT, same rationale as BWRY's: palette index IS the wire
# value (0b00 black, 0b01 white, 0b10 red). The palette has only three
# entries, so the reserved 0b11 field can never be emitted; firmware
# that encounters it (a corrupt frame) renders it as white per the
# XIAO BWR wire contract.
_BWR_3_WIRE_BY_PALETTE_INDEX: tuple[int, ...] = (0x0, 0x1, 0x2)

_GAMUT_TABLE: dict[str, tuple[tuple[tuple[int, int, int], ...], tuple[int, ...]]] = {
    "waveshare_e6": (WAVESHARE_E6_PALETTE, _E6_NIBBLE_BY_PALETTE_INDEX),
    "inky_7colour": (INKY_7COLOUR_PALETTE, _INKY_7COLOUR_NIBBLE_BY_PALETTE_INDEX),
    "bwry_4": (BWRY_4_PALETTE, _BWRY_4_NIBBLE_BY_PALETTE_INDEX),
    "bwr_3": (BWR_3_PALETTE, _BWR_3_WIRE_BY_PALETTE_INDEX),
}

# Gamuts whose native wire format is 2-bpp packed (four pixels per
# byte, MSB first) rather than 4-bpp nibbles: the PicPak-class BWRY
# panels and the XIAO 7.5" BWR class.
_NATIVE_2BPP_GAMUTS: frozenset[str] = frozenset({"bwry_4", "bwr_3"})

_MONO_PALETTE: tuple[tuple[int, int, int], ...] = ((0, 0, 0), (255, 255, 255))


def palette_for_gamut(gamut: str) -> tuple[tuple[int, int, int], ...]:
    """The viewable RGB palette a panel of ``gamut`` reproduces, for quantise
    previews (Panel view). Colour gamuts use the .bin packer's palette; the
    grayscale ramps and mono map to their grey levels; anything unrecognised
    canonicalises to the 6-colour Spectra palette so a preview never fails."""
    g = canonicalise_gamut(gamut)
    table_entry = _GAMUT_TABLE.get(g)
    if table_entry is not None:
        return table_entry[0]
    if g == "gray_16":
        return GRAY_16_PALETTE
    if g == "gray_4":
        return GRAY_4_PALETTE
    if g == "mono":
        return _MONO_PALETTE
    return WAVESHARE_E6_PALETTE


def _apply_exposure(img: Image.Image, exposure: int) -> Image.Image:
    """Linear brightness shift. ``exposure`` in -100..+100 maps to a
    multiplier ``1 + exposure/200``, so +100 is +50% brighter and -100
    is 50% darker. Clips at 0..255. Skips the numpy round-trip on the
    no-op fast path so profiles with ``exposure=0`` cost nothing."""
    if exposure == 0:
        return img
    factor = 1.0 + max(-100, min(100, int(exposure))) / 200.0
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) * factor
    return Image.fromarray(np.clip(arr, 0.0, 255.0).astype(np.uint8))


def _srgb_to_lab(rgb_arr: np.ndarray) -> np.ndarray:
    """Vectorised sRGB (0-255 uint8) -> CIE L*a*b* (float32). Uses the
    standard D65 illuminant, sRGB gamma (piecewise 2.4-with-linear-toe),
    then the CIELAB nonlinearity. Cost is a couple of small numpy ops
    per pixel; only called when the caller opts into a LAB-based mode.

    Returns an array shaped ``(N, 3)`` for an ``(N, 3)`` input and
    ``(H, W, 3)`` for ``(H, W, 3)``."""
    x = rgb_arr.astype(np.float32) / 255.0
    # sRGB -> linear RGB.
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    # Linear RGB -> XYZ (D65). Column matrix from the sRGB reference.
    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ],
        dtype=np.float32,
    )
    xyz = lin @ m.T
    # D65 reference white in the same scale.
    ref = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    xn = xyz / ref
    delta = 6.0 / 29.0
    f = np.where(xn > delta**3, np.cbrt(np.clip(xn, 1e-8, None)), xn / (3 * delta**2) + 4.0 / 29.0)
    ls = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([ls, a, b], axis=-1)


def _lab_to_srgb(lab_arr: np.ndarray) -> np.ndarray:
    """LAB -> sRGB (0-255 uint8), inverse of :func:`_srgb_to_lab`. Clips
    out-of-gamut values so the output is displayable even for LAB tuples
    the sRGB primaries can't represent."""
    ls = lab_arr[..., 0]
    a = lab_arr[..., 1]
    b = lab_arr[..., 2]
    fy = (ls + 16.0) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0
    delta = 6.0 / 29.0

    def _finv(t: np.ndarray) -> np.ndarray:
        return np.where(t > delta, t**3, 3 * delta**2 * (t - 4.0 / 29.0))

    ref = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    xyz = np.stack([_finv(fx) * ref[0], _finv(fy) * ref[1], _finv(fz) * ref[2]], axis=-1)
    m_inv = np.array(
        [
            [3.2404542, -1.5371385, -0.4985314],
            [-0.9692660, 1.8760108, 0.0415560],
            [0.0556434, -0.2040259, 1.0572252],
        ],
        dtype=np.float32,
    )
    lin = xyz @ m_inv.T
    # Linear RGB -> sRGB gamma.
    srgb = np.where(
        lin <= 0.0031308, lin * 12.92, 1.055 * np.clip(lin, 1e-8, None) ** (1.0 / 2.4) - 0.055
    )
    return np.clip(srgb * 255.0, 0.0, 255.0).astype(np.uint8)


def _compress_lab_range(img: Image.Image, min_pct: int, max_pct: int) -> Image.Image:
    """LAB lightness dynamic-range compression. Rescales the L*
    channel so the source's [0, 100] range lands in
    ``[min_pct, max_pct]``; a + b stay untouched so hue is preserved.
    ``min_pct=0`` and ``max_pct=100`` is the identity (no-op fast path).

    This replaces the linear per-channel `_compress_to_calibrated_range`
    when the profile opts into LAB compression; the two are alternatives
    (only one runs per render)."""
    if min_pct <= 0 and max_pct >= 100:
        return img
    lo = max(0, min(100, int(min_pct)))
    hi = max(0, min(100, int(max_pct)))
    if hi <= lo:
        return img
    arr = np.asarray(img.convert("RGB"))
    lab = _srgb_to_lab(arr)
    ls = lab[..., 0]
    ls = ls / 100.0 * (hi - lo) + lo
    lab[..., 0] = np.clip(ls, 0.0, 100.0)
    rgb = _lab_to_srgb(lab)
    return Image.fromarray(rgb)


def _apply_smoothing(img: Image.Image, radius: int) -> Image.Image:
    """Pre-dither Gaussian blur. ``radius`` in 0..3 px; 0 is the
    no-op fast path (returns the input untouched). Applied before
    tone / dither so hard antialiased edges soften a hair before
    error-diffusion has a chance to build a noisy tail along them."""
    if radius <= 0:
        return img
    return img.filter(ImageFilter.GaussianBlur(radius=max(0, min(3, int(radius)))))


def _line_art_mask(img: Image.Image, threshold: int = 40) -> np.ndarray:
    """1D boolean mask (length H*W) marking pixels that fall inside a
    line-art region. Detected via Pillow's FIND_EDGES on the luminance
    channel, thresholded, then 3x3 dilated so the edge halo is covered
    (dither error otherwise leaks across the fresh nearest-neighbour
    strip we're going to paint on top of these pixels).

    ``threshold`` is empirical: 40/255 catches typical dashboard text
    and rules without lighting up photographic mid-tones.

    Pillow's FIND_EDGES lights up the image border because the kernel
    reads pixels outside the frame as black. That's a spurious edge
    that would surface as a stray nearest-neighbour ring on every
    preserve-line-art render, so we zero the outer 2 px of the mask."""
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_arr = np.asarray(edges, dtype=np.uint8)
    mask = edge_arr > threshold
    if mask.shape[0] > 4 and mask.shape[1] > 4:
        mask[:2, :] = False
        mask[-2:, :] = False
        mask[:, :2] = False
        mask[:, -2:] = False
    if not mask.any():
        return mask.ravel()
    # 3x3 max-filter dilation via Pillow (avoids a scipy dependency).
    mask_img = Image.fromarray(mask.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))
    dilated = np.asarray(mask_img, dtype=np.uint8) > 0
    return dilated.ravel()


def _apply_s_curve(img: Image.Image, amount: int) -> Image.Image:
    """Mid-tone contrast shift via a sigmoid S-curve. ``amount`` in
    -100..+100; positive values steepen the S (more mid-tone punch)
    and negative values flatten it (softer, milkier). Applies to R,
    G, B independently rather than luminance so the palette-space
    dither still sees per-channel error. Skips no-op fast path."""
    amount = max(-100, min(100, int(amount)))
    if amount == 0:
        return img
    # k controls the sigmoid steepness. amount=100 -> k=6 (strong S);
    # amount=-100 -> k=-6 (inverted / flattened).
    k = amount / 100.0 * 6.0
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    centred = arr - 0.5
    # sigmoid(k*x); rescale so 0 and 1 stay pinned regardless of k.
    if k > 0:
        raw = 1.0 / (1.0 + np.exp(-k * centred * 2.0))
        lo = 1.0 / (1.0 + np.exp(k))
        hi = 1.0 / (1.0 + np.exp(-k))
        out = (raw - lo) / (hi - lo)
    else:
        # Inverse sigmoid: flatten mid-tones. Blend towards linear.
        blend = 1.0 + k / 6.0 * 0.6  # -100 -> 0.4
        out = 0.5 + centred * blend
    return Image.fromarray(np.clip(out * 255.0, 0.0, 255.0).astype(np.uint8))


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


class SourceCrop(TypedDict, total=False):
    """A source-image crop, in the image's own normalized coordinate space.

    ``x``/``y``/``w``/``h`` are fractions in [0, 1] of the source dimensions, so
    the rect stays valid if the image is later re-uploaded at a different
    resolution. ``rotate`` is a clockwise quarter-turn (0/90/180/270). This is
    the shared crop shape: the Send / gallery path resolves an editor rectangle
    into it, and the Companion image push resolves focus + zoom into it per
    target panel, then both feed it through :func:`apply_source_crop`.
    """

    x: float
    y: float
    w: float
    h: float
    rotate: int


def _clamp01(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


# PIL's ``Transpose.ROTATE_*`` turns counter-clockwise; ``rotate`` is clockwise.
_CW_ROTATE: dict[int, Image.Transpose] = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def apply_source_crop(img: Image.Image, crop: SourceCrop | None) -> Image.Image:
    """Crop and rotate a source image by a normalized rect before it's fit.

    Clamps the rect into the image and enforces a minimum 1px extent, so a
    zero/negative/out-of-range box can never resolve to an empty crop (the
    server owns this clamp; a caller's rect is advisory). An absent crop, or one
    that resolves to the whole image with no rotation, returns ``img`` unchanged.
    """
    if not crop:
        return img
    w0, h0 = img.width, img.height
    x = _clamp01(crop.get("x", 0.0))
    y = _clamp01(crop.get("y", 0.0))
    cw = min(_clamp01(crop.get("w", 1.0)), 1.0 - x)
    ch = min(_clamp01(crop.get("h", 1.0)), 1.0 - y)
    left = round(x * w0)
    top = round(y * h0)
    right = min(w0, max(left + 1, round((x + cw) * w0)))
    bottom = min(h0, max(top + 1, round((y + ch) * h0)))
    if (left, top, right, bottom) != (0, 0, w0, h0):
        img = img.crop((left, top, right, bottom))
    transpose = _CW_ROTATE.get(int(crop.get("rotate", 0) or 0) % 360)
    if transpose is not None:
        img = img.transpose(transpose)
    return img


def resolve_framing_crop(
    *,
    source_w: int,
    source_h: int,
    target_w: int,
    target_h: int,
    focus_x: float,
    focus_y: float,
    zoom: float,
) -> SourceCrop:
    """Resolve a Companion 0.6 framing intent into a :class:`SourceCrop`.

    ``source_w``/``source_h`` must be the orientation-normalized source
    dimensions (after ``exif_transpose``): the client picks ``focus_x``/
    ``focus_y`` against the image as displayed, so resolving against the
    stored pixel buffer of a rotated photo would land the crop in the wrong
    region. Contract steps: derive the ordinary Fill crop from the aspect
    ratios, divide both dimensions by ``zoom``, centre on the focus, clamp
    inside the source. The result always has the target's aspect ratio, so
    a ``fill`` fit of the cropped image is a pure resize.
    """
    source_aspect = source_w / source_h
    target_aspect = target_w / target_h
    if source_aspect >= target_aspect:
        base_w = target_aspect / source_aspect
        base_h = 1.0
    else:
        base_w = 1.0
        base_h = source_aspect / target_aspect
    w = base_w / zoom
    h = base_h / zoom
    x = min(max(focus_x - w / 2.0, 0.0), 1.0 - w)
    y = min(max(focus_y - h / 2.0, 0.0), 1.0 - h)
    return {"x": x, "y": y, "w": w, "h": h}


def fit_to_panel(
    img: Image.Image,
    *,
    target_w: int,
    target_h: int,
    scale: str = "fit",
    bg: str = "white",
    crop: SourceCrop | None = None,
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
    sized. Dashboard renders skip this, the composer emits panel-exact PNGs.

    ``crop`` (optional) is a normalized source crop applied *before* the fit, so
    a chosen subject survives the panel fit rather than being centre-cropped by
    ``fill``. See :func:`apply_source_crop`.

    EXIF orientation is normalized first. Phone cameras commonly store a
    landscape pixel buffer plus an orientation tag rather than rotating the
    pixels, and Pillow does not apply that tag on ``open()``, so without this a
    portrait phone photo lands on the panel sideways. It also has to happen
    *before* the crop: normalized crop coordinates are meaningless unless both
    the client picking them and the server applying them agree on which way up
    the image is. Rendered dashboard compositions carry no EXIF, so this is a
    no-op for them."""
    src = apply_source_crop(ImageOps.exif_transpose(img).convert("RGB"), crop)
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
    """Project an image to ``palette`` and return mode-RGB Pillow output.

    Fast path (``floyd-steinberg``, ``none``): straight through Pillow's
    built-in ``rgb.quantize(palette, dither=...)``, since those are the
    two dither modes Pillow ships. Every other mode declared in
    ``DitherMode`` (``atkinson``, ``jarvis``, ``stucki``, ``bayer-8x8``,
    ``halftone``, ``crosshatch``) runs through the same numpy-backed
    dither pipeline the ``.bin`` packers use, and the resulting palette-
    index buffer is materialised back into an RGB image via a
    palette lookup.

    Before v0.65.2 this function only supported the two Pillow modes;
    picking any of the other five on the ``trmnl_png`` / ``trmnl_png_color``
    device dither setting crashed the render (see #47).
    """
    img = src if isinstance(src, Image.Image) else Image.open(io.BytesIO(src))
    rgb = img.convert("RGB")
    if dither in _PIL_DITHER_MAP:
        pal_img = _palette_image(palette)
        return rgb.quantize(palette=pal_img, dither=_PIL_DITHER_MAP[dither]).convert("RGB")
    # Numpy-backed dithers return palette indices; project through the
    # palette to recover an RGB image. Same implementations
    # ``pack_to_panel_bin`` uses below.
    pal_arr = np.array(palette, dtype=np.float32)
    if dither == "atkinson":
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
    idx_arr = np.frombuffer(raw, dtype=np.uint8)
    pal_u8 = np.array(palette, dtype=np.uint8)
    rgb_flat = pal_u8[idx_arr]  # (H*W, 3)
    width, height = rgb.size
    # Pillow infers "RGB" from the (H, W, 3) uint8 shape. Passing
    # ``mode=`` explicitly is deprecated as of Pillow 13.
    return Image.fromarray(rgb_flat.reshape((height, width, 3)))


def quantize_to_png(
    src: bytes | Image.Image,
    *,
    dither: DitherMode = "floyd-steinberg",
    palette: tuple[tuple[int, int, int], ...] = SPECTRA_6_PALETTE,
) -> bytes:
    out = io.BytesIO()
    quantize(src, dither=dither, palette=palette).save(out, format="PNG", optimize=True)
    return out.getvalue()


# --- CircuitPython client image pipeline ------------------------------
#
# Shared by the ``circuitpython_png`` and ``circuitpython_bmp``
# renderers. Both fit the composition to the panel, contrast-adjust, and
# quantise to the panel's exact indexed palette so ``adafruit_imageload``
# mounts the result with no on-device quantise or dither. The only
# difference between the two renderers is the container the result is
# saved into (indexed PNG vs uncompressed indexed BMP), so the pixel
# pipeline lives here and each renderer just picks the ``save`` format.

_CIRCUITPYTHON_MONO_PALETTE: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (255, 255, 255),
)

_CIRCUITPYTHON_DEFAULTS: dict[str, object] = {
    "dither": "floyd-steinberg",
    "contrast": 1.0,
}


def palette_for_circuitpython_gamut(
    gamut: str | None,
) -> tuple[tuple[int, int, int], ...] | None:
    """Map a panel's declared gamut to the indexed palette a CircuitPython
    client wants, or ``None`` when the gamut asks for a full-colour
    (unquantised) output.

    Unknown or empty gamuts fall through to the 6-colour Spectra 6 palette so
    a panel that just hasn't declared its gamut yet still produces a sensible
    indexed image rather than 8-bit RGB. Aliases handled:

      * ``mono`` -> black + white
      * ``bwr_3`` -> 3-colour black/white/red tri-colour e-ink
      * ``gray_4`` -> 4-level greyscale ramp (2-bit, no highlight)
      * ``bwry_4`` -> 4-colour black/white/red/yellow
      * ``acep_7colour`` / ``acep_7color`` / ``inky_7colour`` -> 7-colour
      * ``spectra_6`` / ``waveshare_e6`` (and the fallback) -> 6-colour, no orange
      * ``rgb24`` / ``rgb16`` -> ``None`` (full-colour passthrough)
    """
    g = (gamut or "").lower()
    if g == "mono":
        return _CIRCUITPYTHON_MONO_PALETTE
    if g == "bwry_4":
        return BWRY_4_PALETTE
    if g == "bwr_3":
        return BWR_3_PALETTE
    if g == "gray_4":
        return GRAY_4_PALETTE
    if g in ("acep_7colour", "acep_7color", "inky_7colour"):
        return INKY_7COLOUR_PALETTE
    if g in ("rgb24", "rgb16"):
        return None
    # Spectra 6 (E6) and everything unrecognised: the 6-colour palette
    # (black/white/red/yellow/blue/green, NO orange). A CircuitPython client
    # paints exactly what arrives, so the 7-colour SPECTRA_6_PALETTE (which
    # carries orange for the Pi-side inky path that reprojects to its own
    # gamut) would put a 7th colour on a 6-colour panel (#118).
    return WAVESHARE_E6_PALETTE


def circuitpython_indexed_image(
    png_bytes: bytes,
    *,
    width: int,
    height: int,
    gamut: str | None,
    flip: bool = False,
    underscan: int = 0,
    settings: dict[str, object] | None = None,
    native_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Fit, contrast-adjust, and quantise a composition PNG for a
    CircuitPython client, returning the ready-to-save Pillow image.

    The result is palette-mode (``"P"``) for every quantised gamut, so a
    caller that saves it as PNG or uncompressed BMP produces an indexed
    file ``adafruit_imageload`` mounts natively. ``rgb24`` / ``rgb16``
    gamuts return an ``"RGB"`` image instead (full-colour passthrough).

    ``width`` / ``height`` are the composition dims. ``native_size`` is
    the client's framebuffer when the device declared one (issue #200):
    a composition in the other aspect is turned 90° CW onto it, the way
    the .bin renderers land a portrait dashboard on a landscape-native
    panel, so the file always arrives shaped like the buffer the client
    reported. Without it the output keeps the composition's shape, which
    is what every client that predates the ``rotation`` field expects.

    The device paints what arrives: no on-device quantise, no dither, no
    nibble unpack. This is the shared pixel pipeline behind both the
    ``circuitpython_png`` and ``circuitpython_bmp`` renderers.
    """
    settings = settings or {}
    img: Image.Image = Image.open(io.BytesIO(png_bytes))

    if flip:
        # Upside-down physical mount: turn the whole image 180° so it
        # reads upright on the wall.
        img = img.rotate(180, expand=True)

    if img.size != (width, height):
        # Composer pre-sizes pages to ``panel.w x panel.h`` so this is
        # usually a no-op. It only does real work on Send-page image
        # pushes where the input PNG isn't panel-sized. Fitting to the
        # composition dims before any rotation keeps an arbitrary
        # uploaded photo's aspect handling identical to the .bin path.
        fit = str(settings.get("image_fit") or "fit")
        img = fit_to_panel(img, target_w=width, target_h=height, scale=fit, bg="white")

    if native_size is not None:
        native_w, native_h = native_size
        if (native_w > native_h) != (width > height):
            # Composition aspect differs from the framebuffer: rotate so
            # the canvas's left edge lands on the panel's top edge. PIL
            # ``rotate`` is counter-clockwise, so -90 gives CW.
            img = img.rotate(-90, expand=True)
        if img.size != (native_w, native_h):
            # Bounded fallback for a client whose declared buffer isn't
            # the exact composition pair (mismatched dims, not just a
            # swapped aspect).
            fit = str(settings.get("image_fit") or "fit")
            img = fit_to_panel(img, target_w=native_w, target_h=native_h, scale=fit, bg="white")

    if underscan:
        # Per-device underscan: inset rendered content so it clears a
        # physical bezel or mat covering the panel edge.
        img = underscan_image(img, underscan=underscan)

    contrast = float(settings.get("contrast", _CIRCUITPYTHON_DEFAULTS["contrast"]))  # type: ignore[arg-type]
    if abs(contrast - 1.0) > 1e-6:
        # Pre-dither contrast push: bumping contrast forces more pixels to
        # definite black or definite white before the dither pass, which
        # tends to read better on text-heavy dashboards.
        img = ImageEnhance.Contrast(img.convert("L")).enhance(contrast).convert("RGB")

    palette = palette_for_circuitpython_gamut(gamut)
    if palette is None:
        # rgb24 / rgb16: full-colour passthrough, no quantise or dither.
        return img.convert("RGB")

    pal_img = _palette_image(palette)
    dither_mode = _PIL_DITHER_MAP.get(
        str(settings.get("dither", _CIRCUITPYTHON_DEFAULTS["dither"])),
        Image.Dither.FLOYDSTEINBERG,
    )
    # ``Image.quantize`` keeps palette mode ("P"), which is what we want:
    # the saved file carries an indexed pixel format adafruit_imageload
    # reads natively. Deliberately no ``.convert("RGB")`` afterwards.
    return img.convert("RGB").quantize(palette=pal_img, dither=dither_mode)


# --- numpy-backed dither for the panel-bin packer ---------------------


def _as_pixel_mask(mask: Image.Image, width: int, height: int) -> np.ndarray:
    """Flatten a region mask ("L" image) to a 1D boolean array (length
    H*W, scanline order) matching the packer's index buffer. True where the
    mask asks for nearest-colour. Resizes nearest-neighbour if the mask
    arrives at the wrong size (defensive; the .bin renderer already aligns
    it), thresholds at mid-grey so only the painted extremes count."""
    m = mask.convert("L")
    if m.size != (width, height):
        m = m.resize((width, height), Image.Resampling.NEAREST)
    arr = np.asarray(m, dtype=np.uint8) >= 128
    flat: np.ndarray = arr.ravel()
    return flat


def _apply_nearest_override(
    raw: bytes,
    rgb: Image.Image,
    palette: tuple[tuple[int, int, int], ...],
    *,
    width: int,
    height: int,
    region_nearest_mask: Image.Image | None,
    extra_mask: np.ndarray | None = None,
) -> bytes:
    """Overlay a plain nearest-colour quantise onto the dithered index
    buffer ``raw`` wherever a mask asks for it, and return the merged buffer.

    Two mask sources, unioned: ``region_nearest_mask`` (issue #86, the per-
    cell dither map, an "L" image) and ``extra_mask`` (a flat bool array,
    used by the colour packer's preserve-line-art pass). The nearest quantise
    is taken from the SAME tone-mapped ``rgb`` the dither ran on, computed
    once and only when some mask is actually non-empty, so an all-diffuse
    frame with no masks returns ``raw`` untouched, byte-identical to before.

    Shared by all three .bin packers (colour / 1-bpp mono / 4-bpp gray) so
    the composite semantics stay identical across panel types."""
    override = np.zeros(len(raw), dtype=bool)
    if extra_mask is not None:
        override |= extra_mask
    if region_nearest_mask is not None:
        override |= _as_pixel_mask(region_nearest_mask, width, height)
    if not override.any():
        return raw
    pal_img = _palette_image(palette)
    nearest = rgb.quantize(palette=pal_img, dither=Image.Dither.NONE).tobytes()
    nearest_arr = np.frombuffer(nearest, dtype=np.uint8)
    merged_arr = np.frombuffer(raw, dtype=np.uint8).copy()
    merged_arr[override] = nearest_arr[override]
    return merged_arr.tobytes()


def _nearest_palette_indices(pixels: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """Vectorised nearest-palette lookup. Squared euclidean in sRGB -
    cheap and good enough for a 6-colour gamut."""
    flat = pixels.reshape(-1, 3)
    diff = flat[:, None, :] - palette[None, :, :]
    d2 = np.einsum("pnk,pnk->pn", diff, diff)
    idx = np.argmin(d2, axis=1).astype(np.uint8)
    reshaped: np.ndarray = idx.reshape(pixels.shape[:-1])
    return reshaped


def _native_colour_mask(
    rgb: Image.Image, palette: tuple[tuple[int, int, int], ...], tolerance: int
) -> np.ndarray:
    """1D boolean mask (length H*W) marking pixels that already sit on one
    of the panel's own colours, within ``tolerance`` (Euclidean distance in
    sRGB, so 0 means an exact match and ~16 covers a JPEG-softened white).

    Error diffusion doesn't leave those pixels alone. A pixel that lands
    exactly on a palette entry generates no error of its own, but it still
    receives its neighbours', so a white background beside anything
    saturated gets pushed off white and quantises to something else. That's
    the speckle on flat backgrounds, and it's worst at the ends of the
    range where there's no headroom to absorb the error (discussion #227).

    Computed on the tone-mapped image the dither actually sees, not the
    original: exposure, the S-curve, the LAB compression and the calibrated
    pre-pass all move pixels, and the calibrated path deliberately pulls
    pure white down to the panel's real paper colour. A mask built before
    any of that would miss the pixels it means to protect.

    One squared-distance pass per palette entry rather than a single
    broadcast against all of them: at panel size the broadcast allocates a
    (H*W, N, 3) intermediate, and the gamuts here have at most seven
    entries to walk."""
    arr = np.asarray(rgb.convert("RGB"), dtype=np.float32)
    limit = float(max(0, tolerance)) ** 2
    best: np.ndarray | None = None
    for entry in palette:
        delta = arr - np.array(entry, dtype=np.float32)
        d2 = np.einsum("hwc,hwc->hw", delta, delta)
        best = d2 if best is None else np.minimum(best, d2)
    if best is None:  # empty palette; nothing to protect
        return np.zeros(arr.shape[0] * arr.shape[1], dtype=bool)
    flat: np.ndarray = (best <= limit).ravel()
    return flat


def _error_diffusion(
    rgb: Image.Image,
    palette: np.ndarray,
    weights: list[tuple[int, int, float]],
    *,
    serpentine: bool = False,
    strength: float = 1.0,
    color_match: str = "rgb",
    hold: np.ndarray | None = None,
) -> bytes:
    """Generic error-diffusion dither.

    Each pixel reads its predecessors' errors, so the per-pixel work has a
    fundamental sequential dependency. We feed numpy arrays into typed
    ``array.array`` flat buffers, ~3x faster than indexing numpy in the
    hot loop. Still slow at panel size (several seconds) but opt-in;
    FS / bayer remain the fast paths.

    ``serpentine`` reverses every other row's scan direction, which
    hides the diagonal "worming" banding pattern that plain left-to-
    right scans can produce on gradient regions. Slightly slower
    (branch overhead) but reads noticeably cleaner on photos.

    ``strength`` scales the propagated error (1.0 = normal, 0.5 =
    softer / flatter dither). Values under 1.0 discard error rather
    than spread it, so the output stays palette-locked with less
    speckle at the cost of tone accuracy. Values above 1.0 amplify
    the dither for exaggerated texture and are almost never useful.

    ``color_match`` picks the distance metric used to find each pixel's
    nearest palette entry. ``rgb`` (default) is the historical simple
    Euclidean-in-sRGB match. ``lab`` computes distance in CIE L*a*b*
    (perceptual) and ``chroma-aware`` biases towards preserving hue
    over lightness by weighting the a*/b* difference 2x. The source
    LAB values are pre-computed once (source-with-accumulated-error is
    approximated by the initial LAB coordinates; small errors don't
    shift LAB space enough to matter for practical dashboards).

    ``hold`` (flat bool array, length H*W) marks pixels that propagate no
    error onward. Their own output still goes through the normal match
    here and is corrected afterwards by :func:`_apply_nearest_override`,
    which owns the same mask; what this does is stop a protected region
    acting as a reservoir that collects error from one side and dumps it
    out the other, which would leave a dirty seam just past the boundary."""
    import array as _array

    strength = max(0.0, float(strength))
    arr = np.asarray(rgb, dtype=np.float32)
    H, W, _ = arr.shape
    buf_r = _array.array("f", arr[:, :, 0].ravel().tolist())
    buf_g = _array.array("f", arr[:, :, 1].ravel().tolist())
    buf_b = _array.array("f", arr[:, :, 2].ravel().tolist())
    out = bytearray(H * W)

    # RGB targets are always used for error computation (the error
    # is the un-quantised residual in the same space the neighbours'
    # buffer lives in). LAB targets, when active, only steer the
    # nearest-palette lookup.
    pr = [float(palette[i, 0]) for i in range(palette.shape[0])]
    pg = [float(palette[i, 1]) for i in range(palette.shape[0])]
    pb = [float(palette[i, 2]) for i in range(palette.shape[0])]
    n_pal = len(pr)

    use_lab = color_match in ("lab", "chroma-aware")
    chroma_weight = 2.0 if color_match == "chroma-aware" else 1.0
    # Declare LAB buffers up-front so mypy sees one type per name;
    # the LAB branch guards indexing behind ``use_lab`` so the empty
    # placeholders are never touched at runtime.
    buf_l = _array.array("f")
    buf_a = _array.array("f")
    buf_b_lab = _array.array("f")
    pal_L: list[float] = []
    pal_a: list[float] = []
    pal_b_lab: list[float] = []
    if use_lab:
        pal_lab = _srgb_to_lab(np.asarray(palette, dtype=np.uint8))
        src_lab = _srgb_to_lab(arr.astype(np.uint8))
        buf_l = _array.array("f", src_lab[:, :, 0].ravel().tolist())
        buf_a = _array.array("f", src_lab[:, :, 1].ravel().tolist())
        buf_b_lab = _array.array("f", src_lab[:, :, 2].ravel().tolist())
        pal_L = [float(pal_lab[i, 0]) for i in range(pal_lab.shape[0])]
        pal_a = [float(pal_lab[i, 1]) for i in range(pal_lab.shape[0])]
        pal_b_lab = [float(pal_lab[i, 2]) for i in range(pal_lab.shape[0])]
    # Flipped weights list is precomputed for serpentine rows so the hot
    # loop doesn't re-mirror per pixel.
    w_dy_lr = [w[0] for w in weights]
    w_dx_lr = [w[1] for w in weights]
    w_frac = [w[2] for w in weights]
    w_dx_rl = [-dx for dx in w_dx_lr]
    n_weights = len(weights)
    # Same array.array treatment as the colour buffers: a numpy bool
    # indexed per pixel in the hot loop costs more than the branch saves.
    hold_flags = _array.array("B", hold.astype(np.uint8).tobytes()) if hold is not None else None

    for y in range(H):
        row_base = y * W
        reverse = serpentine and (y & 1)
        if reverse:
            x_range = range(W - 1, -1, -1)
            w_dx_row = w_dx_rl
        else:
            x_range = range(W)
            w_dx_row = w_dx_lr
        for x in x_range:
            idx = row_base + x
            r = buf_r[idx]
            g = buf_g[idx]
            b = buf_b[idx]
            if use_lab:
                # LAB nearest-palette. chroma_weight (2.0 for chroma-
                # aware, 1.0 for plain LAB) biases towards preserving
                # hue over lightness, so blues stay blue even at the
                # cost of a lightness match.
                L0 = buf_l[idx]
                A0 = buf_a[idx]
                B0 = buf_b_lab[idx]
                best_i = 0
                dL = L0 - pal_L[0]
                dA = A0 - pal_a[0]
                dB = B0 - pal_b_lab[0]
                best_d = dL * dL + chroma_weight * (dA * dA + dB * dB)
                for i in range(1, n_pal):
                    dL = L0 - pal_L[i]
                    dA = A0 - pal_a[i]
                    dB = B0 - pal_b_lab[i]
                    d = dL * dL + chroma_weight * (dA * dA + dB * dB)
                    if d < best_d:
                        best_d = d
                        best_i = i
            else:
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
            if hold_flags is not None and hold_flags[idx]:
                continue
            er = (r - pr[best_i]) * strength
            eg = (g - pg[best_i]) * strength
            eb = (b - pb[best_i]) * strength
            for k in range(n_weights):
                ny = y + w_dy_lr[k]
                nx = x + w_dx_row[k]
                if 0 <= ny < H and 0 <= nx < W:
                    frac = w_frac[k]
                    nidx = ny * W + nx
                    buf_r[nidx] += er * frac
                    buf_g[nidx] += eg * frac
                    buf_b[nidx] += eb * frac
    return bytes(out)


# Floyd-Steinberg: the canonical four-neighbour error-diffusion. Used
# when the profile asks for a LAB colour match on FS, since Pillow's
# built-in FS is RGB-only.
_FS_WEIGHTS: list[tuple[int, int, float]] = [
    (0, 1, 7 / 16),
    (1, -1, 3 / 16),
    (1, 0, 5 / 16),
    (1, 1, 1 / 16),
]


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
    palette_override: tuple[tuple[int, int, int], ...] | None = None,
    exposure: int = 0,
    s_curve: int = 0,
    serpentine: bool = False,
    diffusion_strength: int = 100,
    smoothing_radius: int = 0,
    preserve_line_art: bool = False,
    protect_native_colours: int = 0,
    lab_compress_min: int = 0,
    lab_compress_max: int = 100,
    color_match: str = "rgb",
    region_nearest_mask: Image.Image | None = None,
) -> bytes:
    """Quantise against the selected ``gamut`` palette and pack to the
    panel's native buffer.

    Wire format depends on the gamut:

    * ``waveshare_e6`` / ``inky_7colour`` (6 and 7 colour panels): 4-bpp
      nibble-packed. Layout: ``height`` rows x ``width/2`` bytes each,
      scanline order, high nibble = even column (col 0, 2, …), low
      nibble = odd column. Matches the firmware's ``epd_display`` SPI
      stream.
    * ``bwry_4`` (native 2-bpp panels, PicPak class): 2-bpp packed.
      Layout: ``height`` rows x ``width/4`` bytes each, scanline order,
      MSB = leftmost pixel (bits 7:6 = col 0, 5:4 = col 1, 3:2 = col 2,
      1:0 = col 3). Values are the four palette indices directly (0x0
      black, 0x1 white, 0x2 yellow, 0x3 red), goes straight to the SPI
      stream on the C3-class controllers these panels ship with.
    * ``bwr_3`` (XIAO 7.5" BWR class): same 2-bpp layout with the
      tri-colour values 0x0 black, 0x1 white, 0x2 red. 0x3 is reserved
      on the wire and never emitted (the palette has three entries).

    Unknown gamuts fall back to ``waveshare_e6`` (6 colour, 4-bpp).

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
    * ``region_nearest_mask`` (issue #86), optional ``"L"`` image at
      ``(width, height)``. Pixels >= 128 are forced onto a plain nearest-
      colour quantise instead of the frame dither, so flat-colour UI cells
      stay clean while photo cells keep diffusing. ``None`` reproduces the
      pre-#86 single-strategy behaviour byte-for-byte.
    * ``protect_native_colours`` (discussion #227), tolerance in sRGB
      distance. Above 0, pixels already sitting on one of the panel's own
      colours keep that colour instead of collecting their neighbours'
      diffused error, which is what speckles flat white and black
      backgrounds. 0 (the default) is byte-for-byte the previous
      behaviour. Only offered on the sparse gamuts: on the 16-level
      greyscale ramp nearly every pixel is close to some entry, so the
      same guard would flatten photographs rather than clean up
      backgrounds.
    """
    if gamut in _NATIVE_2BPP_GAMUTS:
        if width % 4:
            raise ValueError(
                f"2-bpp panel width must be a multiple of 4 (four pixels per byte), got {width}"
            )
    elif width % 2:
        raise ValueError(f"panel width must be even (two pixels per byte), got {width}")
    if img.size != (width, height):
        raise ValueError(f"image must be {width}x{height}, got {img.size}")

    palette, nibble_by_index = _GAMUT_TABLE.get(gamut, _GAMUT_TABLE["waveshare_e6"])
    # When the caller opts into calibration, swap the dither target to the
    # calibrated palette for this gamut (if we have one) and compress the
    # source range into the calibrated [black, white] band so the dither
    # has somewhere to spread error to. Nibble LUT stays the rendered
    # gamut's, on-wire bytes are unchanged.
    #
    # ``palette_override`` (populated by the app-side palette-profile
    # resolver in :mod:`app.push`) trumps the built-in ``_CALIBRATED_PALETTES``
    # lookup when the device has a Calibration-tab profile applied. The
    # nibble LUT and gamut selection are unchanged; only the RGB target
    # values dither snaps to shift.
    # v0.68 removed the ``calibrated`` toggle from the device-card UI;
    # the Calibration-tab palette profile is now the single source of
    # truth. When a profile is applied, ``palette_override`` carries
    # its palette in and wins unconditionally. Legacy configs that
    # still have ``calibrated=true`` set but no profile applied
    # continue to hit the built-in ``_CALIBRATED_PALETTES`` map for
    # their gamut (backward-compat path); everything else falls
    # through to the nominal palette.
    calibrated_active = palette_override is not None or (
        calibrated and gamut in _CALIBRATED_PALETTES
    )
    if calibrated_active:
        if palette_override is not None and len(palette_override) >= len(palette):
            palette = palette_override[: len(palette)]
        else:
            palette = _CALIBRATED_PALETTES[gamut]
    pal_arr = np.array(palette, dtype=np.float32)

    rgb = img.convert("RGB")
    if calibrated_active:
        rgb = _compress_to_calibrated_range(rgb, palette)
    # LAB dynamic-range compression (v0.67.4). Overrides the linear
    # calibrated range squeeze above when active; the two compressors
    # are alternatives (only one squeezes the source per render).
    # No-op fast path at min=0, max=100.
    if lab_compress_min > 0 or lab_compress_max < 100:
        rgb = _compress_lab_range(rgb, lab_compress_min, lab_compress_max)
    # Pre-dither smoothing (v0.67.2 experimental). Softens the source
    # a hair before tone-mapping runs; useful on antialiased text where
    # error-diffusion would otherwise build a noisy tail along each
    # letterform edge.
    if smoothing_radius:
        rgb = _apply_smoothing(rgb, smoothing_radius)
    # Tone pipeline order: exposure (linear brightness) -> S-curve
    # (mid-tone contrast) -> saturation / contrast (existing per-clone
    # multipliers). Each helper short-circuits on the no-op value so
    # profiles that leave the knobs at defaults stay free of numpy
    # round-trips.
    if exposure:
        rgb = _apply_exposure(rgb, exposure)
    if s_curve:
        rgb = _apply_s_curve(rgb, s_curve)
    # ImageEnhance is C-speed and idempotent at factor=1.0 (no-op fast path).
    if saturation != 1.0:
        rgb = ImageEnhance.Color(rgb).enhance(saturation)
    if contrast != 1.0:
        rgb = ImageEnhance.Contrast(rgb).enhance(contrast)

    strength_scale = max(0.0, min(200, int(diffusion_strength))) / 100.0
    # Built once, before the dither runs, from the fully tone-mapped source:
    # the error-diffusion paths take it as ``hold`` so a protected region
    # doesn't pass error through itself, and every path (Pillow's FS
    # included) takes it again below as a nearest-colour override.
    native_mask = (
        _native_colour_mask(rgb, palette, protect_native_colours)
        if protect_native_colours > 0
        else None
    )
    # Pillow's built-in Floyd-Steinberg only knows RGB nearest. When
    # the profile asks for LAB / chroma-aware match on FS, we detour
    # through the numpy error-diffusion path with the FS weights so
    # the LAB metric is honoured. Slower than Pillow but keeps the
    # colour-match semantics consistent across dithers.
    use_numpy_fs = dither == "floyd-steinberg" and color_match in ("lab", "chroma-aware")
    if dither in _PIL_DITHER_MAP and not use_numpy_fs:
        pal_img = _palette_image(palette)
        indexed = rgb.quantize(palette=pal_img, dither=_PIL_DITHER_MAP[dither])
        raw = indexed.tobytes()
    elif dither == "atkinson":
        raw = _error_diffusion(
            rgb,
            pal_arr,
            _ATKINSON_WEIGHTS,
            serpentine=serpentine,
            strength=strength_scale,
            color_match=color_match,
            hold=native_mask,
        )
    elif dither == "jarvis":
        raw = _error_diffusion(
            rgb,
            pal_arr,
            _JJN_WEIGHTS,
            serpentine=serpentine,
            strength=strength_scale,
            color_match=color_match,
            hold=native_mask,
        )
    elif dither == "stucki":
        raw = _error_diffusion(
            rgb,
            pal_arr,
            _STUCKI_WEIGHTS,
            serpentine=serpentine,
            strength=strength_scale,
            color_match=color_match,
            hold=native_mask,
        )
    elif dither == "floyd-steinberg":  # numpy-FS LAB detour
        raw = _error_diffusion(
            rgb,
            pal_arr,
            _FS_WEIGHTS,
            serpentine=serpentine,
            strength=strength_scale,
            color_match=color_match,
            hold=native_mask,
        )
    elif dither == "bayer-8x8":
        raw = _dither_ordered(rgb, pal_arr, _BAYER_8X8)
    elif dither == "halftone":
        raw = _dither_ordered(rgb, pal_arr, _HALFTONE_16, strength=128.0)
    elif dither == "crosshatch":
        raw = _dither_ordered(rgb, pal_arr, _CROSSHATCH_8, strength=96.0)
    else:
        raise ValueError(f"unknown dither mode: {dither!r}")

    # Nearest-colour overrides. Two independent masks steer pixels away
    # from the frame's dither and onto a plain nearest-neighbour quantise:
    #
    #  * ``preserve_line_art`` (v0.67.2): sharp edges detected in the tone-
    #    mapped source, so text and hairline rules stay crisp without
    #    losing the error-diffusion win on surrounding photographic regions.
    #  * ``region_nearest_mask`` (issue #86): per-widget ``render.dither``
    #    hints rasterised into a region map by the composer, so flat-colour
    #    UI cells map straight to the palette while photo cells still
    #    diffuse. Composition-mode agnostic (grid today, canvas later): the
    #    packer only sees a mask, never a cell.
    #
    #  * ``protect_native_colours`` (discussion #227): pixels already on one
    #    of the panel's own colours, so a flat white or black background
    #    keeps its colour instead of quantising to whatever its neighbours'
    #    diffused error pushed it towards.
    #
    # All three select from the SAME nearest quantise of the SAME tone-mapped
    # ``rgb`` the dither ran on, unioned and applied in one pass (see
    # :func:`_apply_nearest_override`), so an all-photo dashboard with no
    # hints pays nothing.
    line_mask = _line_art_mask(rgb) if preserve_line_art else None
    extra_mask = line_mask
    if native_mask is not None:
        extra_mask = native_mask if extra_mask is None else (extra_mask | native_mask)
    raw = _apply_nearest_override(
        raw,
        rgb,
        palette,
        width=width,
        height=height,
        region_nearest_mask=region_nearest_mask,
        extra_mask=extra_mask,
    )

    # palette index -> firmware/library nibble via bytes.translate (C-speed).
    # 256-byte LUT; anything past the gamut's entries falls through to 0x0
    # (safe black). For ``bwry_4`` the LUT is identity (palette index IS
    # the wire value), so translate is a no-op here and the 2-bpp packer
    # below reads the palette indices straight from ``nibbles``.
    lut = bytearray(256)
    for i, nibble in enumerate(nibble_by_index):
        lut[i] = nibble
    nibbles = raw.translate(bytes(lut))

    if gamut in _NATIVE_2BPP_GAMUTS:
        # 2-bpp native pack, 4 pixels per byte. MSB = leftmost pixel:
        # bits 7:6 = col 0, 5:4 = col 1, 3:2 = col 2, 1:0 = col 3.
        # Goes straight to the SPI stream on PicPak-class controllers
        # (BWRY) and the XIAO 7.5" BWR firmware, no decode / repack.
        idx = np.frombuffer(nibbles, dtype=np.uint8).reshape(height, width)
        packed_arr = (
            (idx[:, 0::4] << 6) | (idx[:, 1::4] << 4) | (idx[:, 2::4] << 2) | idx[:, 3::4]
        ).astype(np.uint8)
        packed = packed_arr.tobytes()
        expected = width * height // 4
        assert len(packed) == expected, f"packed buffer is {len(packed)}, expected {expected}"
        return packed

    # 4-bpp nibble pack (the 6 and 7 colour panels). Two per byte, the
    # firmware refuses any other layout.
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
    protect_native_colours: int = 0,
    region_nearest_mask: Image.Image | None = None,
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

    ``protect_native_colours`` behaves as it does on the colour packer
    (discussion #227). This is the panel class it matters most on: with
    only black and white to spend, a background that is already one of
    them has nothing to gain from diffusion and everything to lose.
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

    native_mask = (
        _native_colour_mask(rgb, palette, protect_native_colours)
        if protect_native_colours > 0
        else None
    )

    # Same dither branch as pack_to_panel_bin, just against the 2-colour
    # mono palette. Every mode produces palette indices (0=black, 1=white)
    # at the end of this block.
    if dither in _PIL_DITHER_MAP:
        pal_img = _palette_image(palette)
        indexed = rgb.quantize(palette=pal_img, dither=_PIL_DITHER_MAP[dither])
        raw = indexed.tobytes()
    elif dither == "atkinson":
        raw = _error_diffusion(rgb, pal_arr, _ATKINSON_WEIGHTS, hold=native_mask)
    elif dither == "jarvis":
        raw = _error_diffusion(rgb, pal_arr, _JJN_WEIGHTS, hold=native_mask)
    elif dither == "stucki":
        raw = _error_diffusion(rgb, pal_arr, _STUCKI_WEIGHTS, hold=native_mask)
    elif dither == "bayer-8x8":
        raw = _dither_ordered(rgb, pal_arr, _BAYER_8X8)
    elif dither == "halftone":
        raw = _dither_ordered(rgb, pal_arr, _HALFTONE_16, strength=128.0)
    elif dither == "crosshatch":
        raw = _dither_ordered(rgb, pal_arr, _CROSSHATCH_8, strength=96.0)
    else:
        raise ValueError(f"unknown dither mode: {dither!r}")

    # Per-cell dither map (issue #86): snap flat-UI regions to nearest mono
    # instead of dithering them. On a 2-colour panel this is the difference
    # between crisp black text and a stippled grey approximation. Unioned
    # with the native-colour guard (#227) the same way the colour packer
    # does it, so the two features compose instead of racing.
    raw = _apply_nearest_override(
        raw,
        rgb,
        palette,
        width=width,
        height=height,
        region_nearest_mask=region_nearest_mask,
        extra_mask=native_mask,
    )

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
    region_nearest_mask: Image.Image | None = None,
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

    # Per-cell dither map (issue #86): snap flat-UI regions to the nearest
    # gray level instead of dithering them, so a solid panel background or
    # crisp text region doesn't pick up error-diffusion speckle.
    raw = _apply_nearest_override(
        raw,
        rgb,
        palette,
        width=width,
        height=height,
        region_nearest_mask=region_nearest_mask,
    )

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


# 4-level linear grayscale palette. Index i -> RGB (i * 85, i * 85, i * 85),
# so 0 -> black and 3 -> white. Used by ``pack_to_panel_bin_2bpp_gray``
# (Seeed reTerminal E1001 in its 4-gray waveform mode). As with the
# 16-level palette, index equals on-wire value, so no LUT step.
_GRAY_4_PALETTE: tuple[tuple[int, int, int], ...] = tuple(
    (i * 85, i * 85, i * 85) for i in range(4)
)


def pack_to_panel_bin_2bpp_gray(
    img: Image.Image,
    *,
    width: int,
    height: int,
    dither: DitherMode = "floyd-steinberg",
    contrast: float = 1.0,
    region_nearest_mask: Image.Image | None = None,
) -> bytes:
    """Quantise to 4-level grayscale and pack to a 2-bpp wire format.

    Mirror of :func:`pack_to_panel_bin_4bpp_gray` for UC8179-class mono
    panels driven in their 4-gray waveform mode (the Seeed reTerminal
    E1001's 7.5" 800x480 EP75 in particular).

    Wire layout:

    * Exactly ``width * height / 4`` bytes (96000 for the E1001's
      800x480 panel). No header, no padding, no checksum.
    * Row-major, top-left origin, no mirror. The firmware handles any
      physical panel-side mirror itself.
    * 2-bpp packed, scanline order, no row padding: ``width / 4`` bytes
      per row.
    * **MSB first**: bits 7-6 = leftmost pixel of each 4-pixel group
      (column 0, 4, 8, ...), bits 1-0 = rightmost (column 3, 7, ...).
    * Gray value per 2-bit field: **0b00 = black, 0b11 = white**,
      linear 4-level gray.

    ``width`` must be a multiple of 4 (four pixels per byte). Image
    must already be at ``(width, height)``, caller sizes / orients
    first. Dither modes match the other packers, against the 4-entry
    linear grayscale palette.
    """
    if width % 4:
        raise ValueError(f"panel width must be a multiple of 4 (2bpp pack), got {width}")
    if img.size != (width, height):
        raise ValueError(f"image must be {width}x{height}, got {img.size}")

    palette = _GRAY_4_PALETTE
    pal_arr = np.array(palette, dtype=np.float32)

    # Grayscale-first, then back to RGB, same as the 4-bpp packer: the
    # dither routines expect an RGB source and the contrast bump should
    # act on luminance, not on channel imbalance that no longer exists.
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

    raw = _apply_nearest_override(
        raw,
        rgb,
        palette,
        width=width,
        height=height,
        region_nearest_mask=region_nearest_mask,
    )

    # Palette indices are 0..3 already; pack four per byte, MSB first.
    indices = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
    # Clamp strays > 3 so a buggy dither output can't corrupt
    # neighbouring fields when shifted.
    indices = np.clip(indices, 0, 3).astype(np.uint8)
    p0 = indices[:, 0::4]
    p1 = indices[:, 1::4]
    p2 = indices[:, 2::4]
    p3 = indices[:, 3::4]
    packed = (p0 << 6) | (p1 << 4) | (p2 << 2) | p3

    out = packed.tobytes()
    expected = width * height // 4
    assert len(out) == expected, f"packed buffer is {len(out)}, expected {expected}"
    return out
