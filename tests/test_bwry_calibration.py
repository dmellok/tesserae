"""BWRY (PicPak) palette calibration.

The panel cannot reach the ideal sRGB primaries the nominal palette
targets, so ``bwry_4`` gains a measured calibrated palette and a bundled
profile family — the same machinery Spectra 6 and Inky 7-colour already
had. Every assertion about another gamut here exists to prove the change
is inert outside ``bwry_4``.
"""

import pytest
from PIL import Image

from app.palette_profiles.bundled import BUNDLED_PROFILES, default_slug_for, list_bundled
from app.quantizer import (
    _CALIBRATED_PALETTES,
    BWRY_4_CALIBRATED_PALETTE,
    BWRY_4_PALETTE,
    pack_to_panel_bin,
)


def _photo(w: int = 40, h: int = 30) -> Image.Image:
    """A deterministic gradient with colour, so quantiser changes show up."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 255) // w, (y * 255) // h, ((x + y) * 255) // (w + h))
    return img


# -- the calibrated palette itself --------------------------------------


def test_bwry_calibrated_palette_registered():
    assert _CALIBRATED_PALETTES["bwry_4"] is BWRY_4_CALIBRATED_PALETTE


def test_calibrated_palette_has_one_entry_per_ink():
    assert len(BWRY_4_CALIBRATED_PALETTE) == len(BWRY_4_PALETTE) == 4


def test_calibrated_values_are_inside_the_reproducible_range():
    """The whole point: no channel reaches the ideal 0 / 255 endpoints."""
    black, white, yellow, red = BWRY_4_CALIBRATED_PALETTE
    assert all(c > 0 for c in black), "measured black is not pure black"
    assert all(c < 255 for c in white), "measured white is not pure white"
    # Yellow is a mustard, not a lemon: its blue channel is well off zero
    # and its green channel well below full.
    assert yellow[1] < 220 and yellow[2] > 20
    # Red is dusty: darker than ideal, and not fully desaturated.
    assert red[0] < 255 and red[1] > 20


def test_calibrated_palette_changes_bwry_output():
    """A calibrated render must actually differ from the nominal one."""
    img = _photo()
    nominal = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=False)
    calibrated = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=True)
    assert len(nominal) == len(calibrated) == 40 * 30 // 4
    assert nominal != calibrated


# -- isolation: no other gamut may move ---------------------------------


@pytest.mark.parametrize("gamut", ["waveshare_e6", "inky_7colour", "bwr_3", "mono", "gray_4"])
@pytest.mark.parametrize("calibrated", [False, True])
def test_other_gamuts_render_byte_identical(gamut, calibrated, monkeypatch):
    """Render each non-BWRY gamut, then re-render with the bwry_4 entry
    removed from the calibrated map. Identical bytes prove nothing about
    these panels reads the new entry."""
    img = _photo()
    before = pack_to_panel_bin(img, width=40, height=30, gamut=gamut, calibrated=calibrated)

    patched = dict(_CALIBRATED_PALETTES)
    patched.pop("bwry_4")
    monkeypatch.setattr("app.quantizer._CALIBRATED_PALETTES", patched)
    after = pack_to_panel_bin(img, width=40, height=30, gamut=gamut, calibrated=calibrated)

    assert before == after, f"{gamut} (calibrated={calibrated}) changed"


def test_uncalibrated_bwry_is_unchanged(monkeypatch):
    """A PicPak with no profile applied and ``calibrated=False`` must
    render exactly as it did before this change. Same removal trick as
    the other-gamut test: with the new map entry gone, the code takes
    the literal pre-change path."""
    img = _photo()
    after = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=False)

    patched = dict(_CALIBRATED_PALETTES)
    patched.pop("bwry_4")
    monkeypatch.setattr("app.quantizer._CALIBRATED_PALETTES", patched)
    before = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=False)

    assert before == after


def test_calibrated_toggle_without_a_profile_now_reaches_the_measured_palette(monkeypatch):
    """The one case where registering ``bwry_4`` in ``_CALIBRATED_PALETTES``
    deliberately CHANGES an existing panel.

    ``pack_to_panel_bin`` treats ``calibrated=True`` plus a gamut present in the
    map as reason enough to swap the palette and compress the source range, with
    no profile involved. That path was unreachable for BWRY, so a PicPak owner
    who switched the per-device "Calibrated palette + tone mapping" toggle on got
    nothing: it silently fell through to the nominal primaries. Now it does what
    the toggle's own help text has always promised.

    Opt-in (the toggle defaults off) and intended, but pinned here because it is
    the one behaviour this family's arrival does not leave untouched, and because
    the profile-default reasoning (nominal first, so nothing restyles itself)
    covers the profile path only, not this one."""
    img = _photo()
    with_map = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=True)

    patched = dict(_CALIBRATED_PALETTES)
    patched.pop("bwry_4")
    monkeypatch.setattr("app.quantizer._CALIBRATED_PALETTES", patched)
    without_map = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=True)

    assert with_map != without_map, "the toggle should now reach the measured palette"
    # And the pre-change path is still exactly the nominal one, i.e. what a
    # PicPak with the toggle on has been rendering until now.
    nominal = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4", calibrated=False)
    assert without_map == nominal


# -- bundled profile family ---------------------------------------------


def test_bwry_family_has_profiles():
    profiles = list_bundled("bwry_4")
    assert [p.slug for p in profiles] == [
        "nominal-bwry",
        "picpak-bwry-calibrated",
    ]


def test_bwry_default_slug_is_nominal():
    """The default must stay uncalibrated so unlocking the Calibration
    tab doesn't silently change how existing panels render."""
    assert default_slug_for("bwry_4") == "nominal-bwry"


def test_default_profile_renders_identically_to_no_profile():
    """Unlocking the Calibration tab auto-applies the family default via
    the slug self-heal, so that default must be a true no-op: same bytes
    as a PicPak with no profile at all. This is why the nominal profile
    pins serpentine=False / color_match="rgb" (the renderer's no-profile
    defaults) instead of inheriting DitherSettings' own, which differ."""
    from app.palette_profiles.bundled import bundled_profile

    img = _photo(60, 40)
    no_profile = pack_to_panel_bin(img, width=60, height=40, gamut="bwry_4")

    nom = bundled_profile("nominal-bwry")
    with_default = pack_to_panel_bin(
        img,
        width=60,
        height=40,
        gamut="bwry_4",
        palette_override=nom.palette.as_tuples(),
        serpentine=nom.dither.serpentine,
        color_match=nom.dither.color_match,
        diffusion_strength=nom.dither.diffusion_strength,
    )
    assert no_profile == with_default


def test_nominal_profile_mirrors_the_long_standing_palette():
    """``nominal-bwry`` is a new object wrapping old values: it must stay
    an exact copy of BWRY_4_PALETTE, the palette every PicPak frame has
    been dithered against since bwry_4 support landed. Without this,
    editing either one could let them drift apart silently."""
    from app.palette_profiles.bundled import bundled_profile

    nom = bundled_profile("nominal-bwry")
    assert nom.palette.as_tuples()[:4] == BWRY_4_PALETTE


def test_measured_differs_from_nominal_only_by_palette():
    """The two non-LAB presets must share a dither block so switching
    between them is a clean single-variable A/B."""
    from app.palette_profiles.bundled import bundled_profile

    nom = bundled_profile("nominal-bwry")
    meas = bundled_profile("picpak-bwry-calibrated")
    assert nom.dither == meas.dither
    assert nom.palette.as_tuples() != meas.palette.as_tuples()


def test_other_families_keep_their_defaults():
    assert default_slug_for("spectra6") == "paperlesspaper-spectra6"
    assert default_slug_for("inky_7colour") == "paperlesspaper-inky7"


def test_existing_profiles_untouched_by_the_dither_kwarg():
    """``_profile`` gained an optional ``dither`` argument; every
    pre-existing preset must still carry the neutral defaults."""
    for p in BUNDLED_PROFILES:
        if p.family == "bwry_4":
            continue
        assert p.dither.color_match == "rgb"
        assert p.dither.algorithm == "floyd-steinberg"
        assert p.dither.diffusion_strength == 100


def test_no_bundled_profile_selects_lab_matching():
    """``color_match="lab"`` silently disables dithering (see the next
    test), so no shipped preset may steer users onto it."""
    for p in BUNDLED_PROFILES:
        assert p.dither.color_match == "rgb", f"{p.slug} selects {p.dither.color_match}"


def test_lab_matching_still_loses_dither_feedback():
    """Regression guard documenting a PRE-EXISTING upstream bug, not a
    BWRY one: ``_error_diffusion`` builds its LAB buffers once from the
    original image and never writes diffused error back into them, while
    the LAB branch reads those stale buffers to pick the nearest ink.
    Every pixel is judged against its pristine value, so error diffusion
    is ignored and the result is plain nearest-colour quantisation.

    ``_error_diffusion`` never sees a gamut and is shared by atkinson /
    jarvis / stucki (floyd-steinberg detours into it when LAB is
    selected), so this affects every gamut and every error-diffusion
    dither, under both ``lab`` and ``chroma-aware``.

    Signature: a flat patch must dither into a MIX of inks. Under LAB it
    collapses to a single ink. If this test starts failing, the upstream
    bug was fixed and a LAB preset becomes worth reconsidering."""
    from collections import Counter

    palette = ((36, 37, 34), (236, 233, 223), (222, 180, 40), (188, 66, 72))
    flat = Image.new("RGB", (40, 40), (128, 128, 128))

    def ink_count(match: str, dither: str) -> int:
        payload = pack_to_panel_bin(
            flat,
            width=40,
            height=40,
            gamut="bwry_4",
            dither=dither,
            color_match=match,
            palette_override=palette,
        )
        idx = []
        for byte in payload:
            idx += [(byte >> 6) & 3, (byte >> 4) & 3, (byte >> 2) & 3, byte & 3]
        return len(Counter(idx))

    # Every error-diffusion dither routes through the same function, so
    # every one of them loses the feedback.
    for dither in ("floyd-steinberg", "atkinson", "jarvis", "stucki"):
        assert ink_count("rgb", dither) > 1, f"{dither}+rgb should mix inks"
        for match in ("lab", "chroma-aware"):
            assert ink_count(match, dither) == 1, (
                f"{dither}+{match} no longer collapses — upstream bug may be fixed, "
                "in which case a LAB preset is worth reconsidering"
            )


def test_bwry_profile_palette_slices_to_the_four_inks():
    """The shared six-slot schema must line up with BWRY's ink order so
    the quantizer's ``palette_override[:4]`` slice is correct."""
    measured = next(p for p in BUNDLED_PROFILES if p.slug == "picpak-bwry-calibrated")
    tuples = measured.palette.as_tuples()
    assert tuples[:4] == BWRY_4_CALIBRATED_PALETTE


def test_profile_override_beats_nominal_for_bwry():
    img = _photo()
    measured = next(p for p in BUNDLED_PROFILES if p.slug == "picpak-bwry-calibrated")
    plain = pack_to_panel_bin(img, width=40, height=30, gamut="bwry_4")
    with_profile = pack_to_panel_bin(
        img,
        width=40,
        height=30,
        gamut="bwry_4",
        palette_override=measured.palette.as_tuples(),
    )
    assert plain != with_profile


# -- text speckle --------------------------------------------------------


def _text_scene(bg, fg):
    """Antialiased text on a flat background: the case that exposes the
    measured red sitting next to neutral grey."""
    from PIL import ImageDraw

    img = Image.new("RGB", (200, 60), bg)
    draw = ImageDraw.Draw(img)
    draw.text((8, 8), "Sharp Text 123", fill=fg)
    draw.text((8, 30), "Hello Panel", fill=fg)
    return img


def _red_fraction(img, palette):
    """Share of pixels painted with the red ink (palette index 3)."""
    payload = pack_to_panel_bin(img, width=200, height=60, gamut="bwry_4", palette_override=palette)
    idx = []
    for byte in payload:
        idx += [(byte >> 6) & 3, (byte >> 4) & 3, (byte >> 2) & 3, byte & 3]
    return sum(1 for i in idx if i == 3) / len(idx)


@pytest.mark.parametrize(("bg", "fg"), [((0, 0, 0), (255, 255, 255)), ((255, 255, 255), (0, 0, 0))])
def test_nominal_leaves_neutral_text_free_of_red(bg, fg):
    """Documents the trade between the two shipped profiles rather than
    asserting one is correct. Nominal keeps neutral text clean; the
    measured palette speckles it lightly, because the real red ink sits
    next to neutral mid-grey and antialiased edges land in that band.
    That is a known characteristic, not a regression."""
    from app.palette_profiles.bundled import bundled_profile

    img = _text_scene(bg, fg)
    nominal = bundled_profile("nominal-bwry").palette.as_tuples()[:4]
    measured = bundled_profile("picpak-bwry-calibrated").palette.as_tuples()[:4]

    assert _red_fraction(img, nominal) == 0.0
    assert _red_fraction(img, measured) > 0.0, "measured is expected to speckle"


# -- test patterns -------------------------------------------------------


def test_test_pattern_palette_is_the_bwry_deck():
    """``_palette_for`` fell through to the six-colour E6 deck for
    bwry_4. Three things broke at once: the swatch pattern painted blue
    and green blocks the panel cannot reproduce, a 4-entry profile
    palette was silently discarded by build_pattern's
    ``len(override) >= len(palette)`` guard, and error diffusion carried
    the out-of-gamut columns sideways into their neighbours."""
    from app.test_patterns import _palette_for

    pal, labels = _palette_for("bwry_4", False)
    assert pal == BWRY_4_PALETTE
    assert labels == ("black", "white", "yellow", "red")
    assert "blue" not in labels and "green" not in labels

    cal, _ = _palette_for("bwry_4", True)
    assert cal == BWRY_4_CALIBRATED_PALETTE


def test_other_gamuts_keep_their_test_pattern_decks():
    from app.quantizer import INKY_7COLOUR_PALETTE, WAVESHARE_E6_PALETTE
    from app.test_patterns import _palette_for

    assert _palette_for("waveshare_e6", False)[0] == WAVESHARE_E6_PALETTE
    assert _palette_for("inky_7colour", False)[0] == INKY_7COLOUR_PALETTE
    # Unknown gamuts still fall back to E6 rather than raising.
    assert _palette_for("something_new", False)[0] == WAVESHARE_E6_PALETTE


def test_bwry_profile_reaches_the_swatch_pattern():
    """The override guard needs the deck length to match, otherwise the
    user's chosen profile never affects the pattern they send."""
    import io

    from app.palette_profiles.bundled import bundled_profile
    from app.test_patterns import build_pattern

    pal = bundled_profile("picpak-bwry-calibrated").palette.as_tuples()[:4]
    png = build_pattern("palette_swatches", 400, 300, gamut="bwry_4", palette_override=pal)
    img = Image.open(io.BytesIO(png)).convert("RGB")
    # Four even columns; sample the middle of each, above the label.
    for i, expected in enumerate(pal):
        assert img.getpixel((i * 100 + 50, 10)) == expected, f"column {i}"


# -- UI surfaces ---------------------------------------------------------


def _device(app, gamut: str):
    from app.device_loader import Device

    reg = app.config["DEVICE_REGISTRY"]
    base = reg.devices["picpak_4_2"]
    return Device(
        id="t",
        path=base.path,
        manifest={**base.manifest, "panel": {"w": 400, "h": 300, "gamut": gamut}},
        module=base.module,
        data_dir=base.data_dir,
        kind_of="picpak_4_2",
    )


def test_bwry_swatch_grid_omits_blue_and_green(app):
    from app.settings.index_routes import _palette_profile_colors_for

    with app.test_request_context():
        colors = _palette_profile_colors_for(_device(app, "bwry_4"))
        assert [c["name"] for c in colors] == ["black", "white", "yellow", "red"]


def test_only_bwry_is_trimmed(app):
    """The swatch trim must apply to PicPak and nothing else. Trimming by
    the gamut's palette length generalises better but is not behaviour-
    neutral for other families, so it is deliberately not done."""
    from app.settings.index_routes import _palette_profile_colors_for

    with app.test_request_context():
        assert len(_palette_profile_colors_for(_device(app, "bwry_4"))) == 4
        # Everything else keeps the full six-slot grid it always had.
        for gamut in ("waveshare_e6", "spectra_6", ""):
            colors = _palette_profile_colors_for(_device(app, gamut))
            assert len(colors) == 6, f"{gamut!r} grid changed"


def test_inky_preview_threshold_is_unchanged():
    """Regression guard for a real hazard: an earlier revision derived
    this threshold from the gamut, which silently moved inky_7colour from
    6 required swatches to 7. Non-BWRY gamuts must keep the constant."""
    import inspect

    from app.settings import devices_routes

    src = inspect.getsource(devices_routes.devices_test_pattern_preview)
    assert 'required_swatches = 4 if str(params["gamut"]) == "bwry_4" else 6' in src


def test_spectra6_swatch_grid_still_has_six(app):
    from app.settings.index_routes import _palette_profile_colors_for

    with app.test_request_context():
        colors = _palette_profile_colors_for(_device(app, "waveshare_e6"))
        assert [c["name"] for c in colors] == [
            "black",
            "white",
            "yellow",
            "red",
            "blue",
            "green",
        ]


def test_bwry_now_self_heals_onto_its_own_family(app):
    """The inverse of the old "unsupported gamut" behaviour: a BWRY
    device with no stored slug is backfilled with the BWRY default, not
    left blank and not forced onto a Spectra 6 profile."""
    from app.settings.index_routes import _palette_profile_slug_for

    with app.test_request_context():
        slug = _palette_profile_slug_for(_device(app, "bwry_4"))
        assert slug == "nominal-bwry"


def test_mono_still_has_no_family(app):
    """Gamuts that genuinely have no bundled family must stay hidden."""
    from app.settings.index_routes import _palette_family_for

    with app.test_request_context():
        assert _palette_family_for(_device(app, "mono")) == ""
        assert _palette_family_for(_device(app, "bwry_4")) == "bwry_4"


def test_test_pattern_colors_exclude_inks_bwry_lacks(app):
    from app.settings.index_routes import _test_pattern_colors_for

    with app.test_request_context():
        labels = [c["label"] for c in _test_pattern_colors_for(_device(app, "bwry_4"))]
        assert labels == ["black", "white", "yellow", "red"]
        assert "blue" not in labels and "green" not in labels
