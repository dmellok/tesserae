"""A palette profile's colours land on the panel slot of the same name,
whatever order the gamut's wire palette happens to use (pull request #298).

Profiles emit black, white, yellow, red, blue, green, orange. That is the
Spectra 6 order, so applying it positionally was invisible on E6 panels and
swapped two pairs of primaries on ``inky_7colour``, whose palette runs black,
white, green, blue, red, yellow, orange.
"""

from __future__ import annotations

from PIL import Image

from app.quantizer import (
    INKY_7COLOUR_PALETTE,
    WAVESHARE_E6_PALETTE,
    align_palette_override,
    pack_to_panel_bin,
)

# A profile whose colours are recognisably "the same hue, calibrated".
PROFILE = (
    (10, 10, 10),  # black
    (245, 245, 245),  # white
    (240, 220, 20),  # yellow
    (210, 20, 20),  # red
    (20, 20, 200),  # blue
    (30, 200, 30),  # green
    (200, 110, 20),  # orange
)


def test_e6_order_is_the_profile_order() -> None:
    assert align_palette_override(PROFILE, WAVESHARE_E6_PALETTE) == PROFILE[:6]


def test_inky_7colour_gets_its_own_slot_order() -> None:
    aligned = align_palette_override(PROFILE, INKY_7COLOUR_PALETTE)
    # nominal: black, white, green, blue, red, yellow, orange
    assert aligned == (
        PROFILE[0],
        PROFILE[1],
        PROFILE[5],  # green
        PROFILE[4],  # blue
        PROFILE[3],  # red
        PROFILE[2],  # yellow
        PROFILE[6],  # orange
    )


def test_unknown_nominal_colour_falls_back_to_position() -> None:
    custom = ((0, 0, 0), (255, 255, 255), (123, 45, 67))
    assert align_palette_override(PROFILE, custom) == (PROFILE[0], PROFILE[1], PROFILE[2])


def _nibbles(buf: bytes) -> list[int]:
    out: list[int] = []
    for b in buf:
        out.extend((b >> 4, b & 0x0F))
    return out


def test_pure_green_on_inky_packs_the_green_nibble_under_a_profile() -> None:
    img = Image.new("RGB", (4, 1), (0, 255, 0))
    plain = pack_to_panel_bin(img, width=4, height=1, gamut="inky_7colour", dither="none")
    profiled = pack_to_panel_bin(
        img,
        width=4,
        height=1,
        gamut="inky_7colour",
        dither="none",
        palette_override=PROFILE,
    )
    # Green is nibble 2 on this panel; with the old positional override the
    # profile's yellow sat in that slot and green pixels came out as nibble 5.
    assert set(_nibbles(plain)[:4]) == {2}
    assert set(_nibbles(profiled)[:4]) == {2}


def test_pure_red_on_inky_packs_the_red_nibble_under_a_profile() -> None:
    img = Image.new("RGB", (4, 1), (255, 0, 0))
    profiled = pack_to_panel_bin(
        img,
        width=4,
        height=1,
        gamut="inky_7colour",
        dither="none",
        palette_override=PROFILE,
    )
    assert set(_nibbles(profiled)[:4]) == {4}
