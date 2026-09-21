"""A panel's vocabulary is its inks plus the mixes it dithers cleanly from two of them.

These pin the properties an agent relies on: that a colour panel reports more than its inks,
that every blend is computed from this panel's own palette rather than written down, and that a
panel which cannot show a mix does not offer it.
"""

from __future__ import annotations

from app import quantizer as q
from app.blends import blends_for
from app.mcp_api import _gamut_info


def _rgb(h: str) -> tuple[int, int, int]:
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def test_a_colour_panel_has_more_colours_than_inks() -> None:
    blends = blends_for(q.WAVESHARE_E6_PALETTE, False)
    # Six inks, and enough mixes that the panel is worth designing for in full colour.
    assert len(blends) > len(q.WAVESHARE_E6_PALETTE)
    names = {b["name"] for b in blends}
    assert {"orange", "purple", "teal", "navy", "grey"} <= names


_INK_HEX = {
    "black": "#000000",
    "white": "#FFFFFF",
    "yellow": "#FFFF00",
    "red": "#FF0000",
    "blue": "#0000FF",
    "green": "#00FF00",
    "orange": "#FF8C00",
}


def test_every_blend_is_a_mix_of_two_inks_this_panel_has() -> None:
    blends = blends_for(q.BWRY_4_PALETTE, False)
    for b in blends:
        assert set(b["from"]) <= {"black", "white", "yellow", "red"}, (
            f"{b['name']} mixes an ink this panel lacks"
        )
        # The hex is the stated mix of the two, not a value someone typed in.
        a, c = (_rgb(_INK_HEX[x]) for x in b["from"])
        want = tuple(round(a[i] * b["mix"] + c[i] * (1 - b["mix"])) for i in range(3))
        assert _rgb(b["hex"]) == want
        assert b["safe_on"] == "large areas"
    # A blue mix is impossible on a black/white/red/yellow panel and is not offered.
    assert not any("blue" in b["from"] for b in blends)


def test_a_mix_that_lands_on_an_ink_is_not_offered_as_a_separate_colour() -> None:
    # The ACeP panel prints orange itself, so red+yellow must not be sold as a new colour.
    assert "orange" not in {b["name"] for b in blends_for(q.INKY_7COLOUR_PALETTE, False)}
    assert "orange" in {b["name"] for b in blends_for(q.WAVESHARE_E6_PALETTE, False)}


def test_grayscale_and_one_bit_panels_report_none() -> None:
    # Their levels are already the whole vocabulary, and on one bit every mix is just texture.
    assert blends_for(q.GRAY_4_PALETTE, True) == []
    assert blends_for(q.GRAY_16_PALETTE, True) == []
    assert blends_for((), False) == []


def test_list_devices_reports_them_beside_the_inks() -> None:
    info = _gamut_info("waveshare_e6")
    assert len(info["colors"]) == 6
    assert len(info["blends"]) == 18
    assert info["mono"] is False
    assert _gamut_info("mono")["blends"] == []
    assert _gamut_info("gray_4")["blends"] == []
    # A full-colour transport has no fixed inks, so it has no blends to name either, but it
    # still reports the key: every device answers with the same shape.
    assert _gamut_info("rgb24") | {"blends": [], "colors": []} == _gamut_info("rgb24")
