"""Palette extractor: k-means determinism, token assignment heuristics.

These don't pin the exact suggested colours — k-means on a real photo
is too noisy a signal to assert pixel-perfect — but they pin the
structural guarantees the UI depends on: every token key lands in the
output, hex codes are well-formed, light vs dark detection swings on
the dominant cluster's luminance, and re-extracting the same input
yields the same palette so the builder's "Apply" UX is stable.
"""

from __future__ import annotations

import io
import re

import pytest
from PIL import Image

from app.palette_extract import (
    RGB,
    PaletteExtractError,
    assign_to_tokens,
    extract_dominant,
)

_HEX_RE = re.compile(r"^#[0-9A-F]{6}$")


# -- helpers -----------------------------------------------------------


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid(rgb: tuple[int, int, int], size: tuple[int, int] = (40, 40)) -> bytes:
    return _png_bytes(Image.new("RGB", size, rgb))


def _bands(*colors: tuple[int, int, int]) -> bytes:
    """A horizontal stack of colour bands, used to give k-means a
    deterministic multi-cluster input."""
    h = 60
    w = 60 * len(colors)
    img = Image.new("RGB", (w, h), colors[0])
    px = img.load()
    band_w = 60
    for i, c in enumerate(colors):
        for x in range(i * band_w, (i + 1) * band_w):
            for y in range(h):
                px[x, y] = c
    return _png_bytes(img)


# -- extract_dominant -------------------------------------------------


def test_extract_dominant_returns_input_colour_for_solid_image() -> None:
    """A solid red panel collapses to a single cluster ≈ pure red."""
    out = extract_dominant(_solid((255, 0, 0)))
    assert out, "extract should never return an empty list for a real image"
    # k-means rounding can drift the centre by a unit; allow ±2.
    head = out[0]
    assert abs(head.r - 255) <= 2 and head.g <= 2 and head.b <= 2


def test_extract_dominant_orders_by_cluster_size() -> None:
    """k=10 against a 6-band image gives 6 real clusters (the rest fold
    into the dominant ones). The first returned colour should match
    the band with the most pixels — they're equal-sized here, so we
    just check that each input band appears somewhere near the top."""
    bands = [
        (255, 0, 0),
        (0, 200, 0),
        (0, 0, 255),
        (240, 240, 60),
        (200, 60, 200),
        (240, 240, 240),
    ]
    out = extract_dominant(_bands(*bands), k=10)
    # Every input band has a close match in the top 8 outputs (k-means
    # may split one cluster across two centres). Tolerance is generous
    # because the algorithm picks centres, not source bands.
    for band in bands:
        assert any(
            abs(c.r - band[0]) < 30 and abs(c.g - band[1]) < 30 and abs(c.b - band[2]) < 30
            for c in out[:8]
        ), f"band {band!r} missing from top-8 extracted colours"


def test_extract_dominant_is_deterministic_for_same_input() -> None:
    """k-means++ seeds from a fixed RNG, so re-extracting the same
    image must yield identical output. The builder's "Apply" UX
    depends on this — re-uploading shouldn't shuffle the form."""
    sample = _bands((30, 30, 60), (210, 70, 40), (240, 230, 200))
    a = extract_dominant(sample)
    b = extract_dominant(sample)
    assert a == b


def test_extract_dominant_rejects_empty_upload() -> None:
    with pytest.raises(PaletteExtractError, match="empty"):
        extract_dominant(b"")


def test_extract_dominant_rejects_oversized_upload() -> None:
    """The route layer already enforces a content-length cap; defence
    in depth catches anything that snuck past."""
    big = b"\x00" * (8 * 1024 * 1024 + 1)
    with pytest.raises(PaletteExtractError, match="exceeds"):
        extract_dominant(big)


def test_extract_dominant_rejects_non_image_bytes() -> None:
    with pytest.raises(PaletteExtractError, match="decode"):
        extract_dominant(b"this is plain text, not an image")


# -- assign_to_tokens --------------------------------------------------


_REQUIRED_TOKENS = (
    "bg",
    "surface",
    "surface_sunken",
    "text_primary",
    "text_secondary",
    "text_muted",
    "edge",
    "on_accent",
    "accent_1",
    "accent_1_soft",
    "accent_2",
    "accent_2_soft",
    "accent_3",
    "accent_3_soft",
    "accent_4",
    "accent_4_soft",
    "accent_5",
    "accent_5_soft",
    "accent_6",
    "accent_6_soft",
)


def test_assign_returns_every_required_token() -> None:
    out = assign_to_tokens(extract_dominant(_bands((220, 40, 40), (240, 240, 240), (40, 40, 40))))
    for key in _REQUIRED_TOKENS:
        assert key in out, f"missing token {key!r}"


def test_assign_emits_uppercase_hex_six_digits() -> None:
    """The builder colour inputs accept any case but Spectra CSS uses
    uppercase. Keep extraction output consistent so a save → reload
    diff doesn't show case noise."""
    out = assign_to_tokens(extract_dominant(_bands((220, 40, 40), (240, 240, 240), (40, 40, 40))))
    for key in _REQUIRED_TOKENS:
        assert _HEX_RE.match(out[key]), f"{key} = {out[key]!r} doesn't look like #RRGGBB"


def test_assign_empty_palette_returns_full_default_set_in_light_mode() -> None:
    """No extracted colours → fall back to the bundled ``light``
    defaults so the form still has every key filled in."""
    out = assign_to_tokens([], mode="light")
    for key in _REQUIRED_TOKENS:
        assert key in out
    assert out["bg"] == "#E7E4DC"  # matches the bundled light theme


def test_assign_empty_palette_returns_dark_defaults_when_asked() -> None:
    out = assign_to_tokens([], mode="dark")
    assert out["bg"] == "#141310"


def test_assign_auto_detects_light_mode_from_bright_dominant() -> None:
    """When the modal pixel is bright, the assigner should pick the
    light treatment (light bg, dark text). Build an image whose
    population is dominated by paper-white."""
    img = Image.new("RGB", (90, 30), (245, 244, 240))
    px = img.load()
    # Carve out a small dark accent strip — not enough to flip the mode.
    for x in range(10):
        for y in range(30):
            px[x, y] = (40, 40, 40)
    out = assign_to_tokens(extract_dominant(_png_bytes(img)))
    assert RGB(*_hex_to_rgb(out["bg"])).luminance > RGB(*_hex_to_rgb(out["text_primary"])).luminance


def test_assign_auto_detects_dark_mode_from_dark_dominant() -> None:
    """Modal pixel is near-black → dark treatment."""
    img = Image.new("RGB", (90, 30), (20, 20, 25))
    px = img.load()
    for x in range(10):
        for y in range(30):
            px[x, y] = (240, 240, 240)
    out = assign_to_tokens(extract_dominant(_png_bytes(img)))
    assert RGB(*_hex_to_rgb(out["bg"])).luminance < RGB(*_hex_to_rgb(out["text_primary"])).luminance


def test_assign_synthesises_accents_for_monochrome_image() -> None:
    """A pure-grey input has no saturated clusters; the assigner
    should still produce six distinct accent slots (synthesised hue
    rotation) so the builder isn't fed six identical neutrals."""
    out = assign_to_tokens(extract_dominant(_solid((128, 128, 128))))
    accents = [out[f"accent_{i}"] for i in range(1, 7)]
    # At least four distinct values — the synth rotation generates six
    # from a slate-blue anchor, all near-unique.
    assert len(set(accents)) >= 4


def test_assign_soft_accents_share_their_base_accent_hue() -> None:
    """Each ``accent_N_soft`` is the matching ``accent_N`` mixed with
    bg. They should land close to bg with a tint of the accent — not
    fully neutral, not fully saturated."""
    out = assign_to_tokens(extract_dominant(_bands((220, 40, 40), (40, 220, 60), (245, 245, 240))))
    for i in range(1, 7):
        accent = RGB(*_hex_to_rgb(out[f"accent_{i}"]))
        soft = RGB(*_hex_to_rgb(out[f"accent_{i}_soft"]))
        bg = RGB(*_hex_to_rgb(out["bg"]))
        # ``soft`` should sit between accent and bg in each channel
        # within a small slack (mix is linear at t=0.78).
        for ch in ("r", "g", "b"):
            lo = min(getattr(accent, ch), getattr(bg, ch))
            hi = max(getattr(accent, ch), getattr(bg, ch))
            v = getattr(soft, ch)
            assert lo - 4 <= v <= hi + 4, (
                f"accent_{i}_soft channel {ch} = {v} not between {lo} and {hi}"
            )


# -- helpers -----------------------------------------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
