"""Grayscale calibration: a measured grey ramp overriding the linear one.

The grey packers assumed evenly-spaced levels. A panel whose mid-levels
paint lighter than that renders washed out, and error diffusion
compounds it by diffusing against values the panel never produces.
"""

from __future__ import annotations

import io

from PIL import Image

from app.palette_profiles.bundled import bundled_profile, default_slug_for, list_bundled
from app.palette_profiles.schema import GrayRamp, profile_from_dict
from app.quantizer import pack_to_panel_bin_2bpp_gray, pack_to_panel_bin_4bpp_gray


def _flat(level: int, size: tuple[int, int] = (64, 32)) -> bytes:
    img = Image.new("RGB", size, (level, level, level))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _levels_2bpp(packed: bytes) -> set[int]:
    return {(byte >> shift) & 0b11 for byte in packed for shift in (0, 2, 4, 6)}


def test_ramp_interpolates_to_any_level_count() -> None:
    ramp = GrayRamp(levels=("#000000", "#808080", "#ffffff"))
    assert ramp.as_tuples(3) == ((0, 0, 0), (128, 128, 128), (255, 255, 255))
    # Endpoints are preserved and the middle is filled in.
    five = ramp.as_tuples(5)
    assert five is not None
    assert five[0] == (0, 0, 0) and five[-1] == (255, 255, 255)
    assert five[2] == (128, 128, 128)
    assert [v[0] for v in five] == sorted(v[0] for v in five), "must stay ascending"


def test_ramp_needs_two_anchors() -> None:
    """One anchor can't describe a ramp, so it's no override rather than a
    guess the packer would silently apply to every level."""
    assert GrayRamp(levels=()).as_tuples(4) is None
    assert GrayRamp(levels=("#123456",)).as_tuples(4) is None


_LIFTED = ((0x1E,) * 3, (0x6E,) * 3, (0xB4,) * 3, (0xE8,) * 3)


def test_lifted_ramp_darkens_a_midtone() -> None:
    """The ramp says what the panel actually paints. Declaring the middle
    levels lighter than nominal tells the quantizer they overshoot, so a
    mid grey drops to a darker level. That is the fix for a washed-out
    panel, and it is the whole point of the feature.

    0x8C sits nearer 170 than 85 on the linear ramp (level 2), but nearer
    110 than 180 on the lifted one (level 1)."""
    mid = _flat(0x8C)
    nominal = pack_to_panel_bin_2bpp_gray(
        Image.open(io.BytesIO(mid)), width=64, height=32, dither="none"
    )
    lifted = pack_to_panel_bin_2bpp_gray(
        Image.open(io.BytesIO(mid)), width=64, height=32, dither="none", palette_override=_LIFTED
    )
    assert _levels_2bpp(nominal) == {2}
    assert _levels_2bpp(lifted) == {1}
    assert len(nominal) == len(lifted), "calibration must not change the wire size"


def test_lifted_ramp_darkens_a_gradient_overall() -> None:
    """Not every value crosses a boundary, so the single-patch case above
    isn't enough on its own: what the user sees is the whole frame getting
    darker. Across a full sweep the mean level has to drop."""
    src = Image.linear_gradient("L").resize((256, 16)).convert("RGB")
    nominal = pack_to_panel_bin_2bpp_gray(src, width=256, height=16, dither="none")
    lifted = pack_to_panel_bin_2bpp_gray(
        src, width=256, height=16, dither="none", palette_override=_LIFTED
    )

    def mean_level(packed: bytes) -> float:
        vals = [(byte >> shift) & 0b11 for byte in packed for shift in (6, 4, 2, 0)]
        return sum(vals) / len(vals)

    assert mean_level(lifted) < mean_level(nominal)


def test_override_is_ignored_at_the_wrong_length() -> None:
    """A partial ramp would silently remap every level above the gap, so a
    wrong-length override falls back to nominal instead of being padded."""
    src = Image.open(io.BytesIO(_flat(0xAA)))
    baseline = pack_to_panel_bin_2bpp_gray(src, width=64, height=32, dither="none")
    short = pack_to_panel_bin_2bpp_gray(
        src,
        width=64,
        height=32,
        dither="none",
        palette_override=((0, 0, 0), (255, 255, 255)),  # 2 entries for a 4-level panel
    )
    assert short == baseline


def test_16_level_packer_takes_a_ramp() -> None:
    src = Image.open(io.BytesIO(_flat(0x80, (64, 8))))
    ramp = GrayRamp(levels=("#1E1E1E", "#6E6E6E", "#B4B4B4", "#E8E8E8")).as_tuples(16)
    assert ramp is not None and len(ramp) == 16
    packed = pack_to_panel_bin_4bpp_gray(
        src, width=64, height=8, dither="none", palette_override=ramp
    )
    assert len(packed) == 64 * 8 // 2, "calibration must not change the wire size"


def test_bundled_grey_profiles_resolve() -> None:
    for family, slug in (("gray_4", "nominal-gray4"), ("gray_16", "nominal-gray16")):
        assert default_slug_for(family) == slug
        profile = bundled_profile(slug)
        assert profile is not None and profile.family == family
        # The nominal ramps must resolve to the evenly-spaced palette the
        # packers already use, so selecting one changes nothing.
        count = 4 if family == "gray_4" else 16
        step = 255 // (count - 1)
        assert profile.gray.as_tuples(count) == tuple((i * step,) * 3 for i in range(count))
    assert next(p.slug for p in list_bundled("gray_4")) == "nominal-gray4"


def test_grey_profile_round_trips_through_json() -> None:
    profile = bundled_profile("soft-mid-gray4")
    assert profile is not None
    restored = profile_from_dict(profile.to_dict())
    assert restored.gray.levels == profile.gray.levels


def test_colour_profiles_carry_no_ramp() -> None:
    """An empty key on every colour profile is noise in the file and the
    diff, and a colour profile has no levels to describe."""
    colour = bundled_profile("paperlesspaper-spectra6")
    assert colour is not None
    assert colour.gray.levels == ()
    assert "gray" not in colour.to_dict()


def test_grey_panels_get_a_calibration_picker() -> None:
    """The picker was gated to colour gamuts, so a grey panel saw no
    Calibration tab at all and had no way to reach a ramp."""
    from app.settings.index_routes import _palette_family_for

    class _Dev:
        def __init__(self, gamut: str) -> None:
            self.panel = {"gamut": gamut}

    assert _palette_family_for(_Dev("gray_4")) == "gray_4"
    assert _palette_family_for(_Dev("gray_16")) == "gray_16"
    # Unchanged for the gamuts that never had a family.
    assert _palette_family_for(_Dev("mono")) == ""
    assert _palette_family_for(_Dev("spectra_6")) == "spectra6"
