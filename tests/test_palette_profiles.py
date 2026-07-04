"""Palette-profile package: schema round-trip + bundled sanity + store."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.palette_profiles import (
    BUNDLED_PROFILES,
    PaletteColors,
    PaletteProfile,
    PaletteProfileStore,
    ToneSettings,
    bundled_profile,
    list_bundled,
    profile_from_dict,
    slugify,
)


def test_bundled_profiles_are_populated() -> None:
    assert len(BUNDLED_PROFILES) >= 5
    # At least one spectra6 + one inky_7colour.
    families = {p.family for p in BUNDLED_PROFILES}
    assert "spectra6" in families
    assert "inky_7colour" in families


def test_bundled_profile_lookup_by_slug() -> None:
    assert bundled_profile("paperlesspaper-spectra6") is not None
    assert bundled_profile("nope-nope") is None


def test_bundled_paperlesspaper_default_is_the_calibrated_baseline() -> None:
    from app.quantizer import WAVESHARE_E6_CALIBRATED_PALETTE

    default = bundled_profile("paperlesspaper-spectra6")
    assert default is not None
    tuples = default.palette.as_tuples()
    # Six RGB tuples in canonical order (black, white, yellow, red, blue, green).
    assert len(tuples) == 6
    # The paperlesspaper baseline is what the calibrated palette in
    # app.quantizer was seeded from, so the tuples should match exactly.
    assert tuples == WAVESHARE_E6_CALIBRATED_PALETTE


def test_inky_bundled_profile_has_orange() -> None:
    inky = bundled_profile("paperlesspaper-inky7")
    assert inky is not None
    assert inky.palette.orange is not None
    assert len(inky.palette.as_tuples()) == 7


def test_list_bundled_filters_by_family() -> None:
    spectra = list_bundled("spectra6")
    inky = list_bundled("inky_7colour")
    assert all(p.family == "spectra6" for p in spectra)
    assert all(p.family == "inky_7colour" for p in inky)
    assert len(spectra) + len(inky) <= len(BUNDLED_PROFILES)


def test_bundled_profiles_include_paperlesspaper_attribution() -> None:
    # At least one paperlesspaper-attributed profile per family; catches
    # future edits that accidentally strip the credit.
    epd_url = "https://github.com/paperlesspaper/epdoptimize"
    attributed = [p for p in BUNDLED_PROFILES if p.attribution == epd_url]
    assert len(attributed) >= 4


def test_profile_json_round_trip() -> None:
    original = PaletteProfile(
        slug="my-warm-study",
        name="My warm study",
        family="spectra6",
        palette=PaletteColors(
            black="#1A1A1A",
            white="#EEEEEE",
            yellow="#CCCC00",
            red="#AA0000",
            blue="#0000AA",
            green="#00AA00",
        ),
        tone=ToneSettings(contrast=1.2, saturation=1.4, s_curve=15),
        notes="Evening lamp light",
    )
    payload = original.to_dict()
    round_tripped = profile_from_dict(payload)
    assert round_tripped.slug == original.slug
    assert round_tripped.name == original.name
    assert round_tripped.family == original.family
    assert round_tripped.palette.as_tuples() == original.palette.as_tuples()
    assert round_tripped.tone.contrast == pytest.approx(1.2)
    assert round_tripped.tone.saturation == pytest.approx(1.4)
    assert round_tripped.tone.s_curve == 15
    assert round_tripped.notes == "Evening lamp light"


def test_profile_from_dict_clamps_out_of_range_tone() -> None:
    payload = {
        "slug": "clamped",
        "name": "Clamped",
        "family": "spectra6",
        "tone": {
            "exposure": 999,
            "contrast": 99.0,
            "saturation": -3.0,
            "s_curve": -500,
            "lab_compress_min": 200,
            "lab_compress_max": -50,
        },
    }
    p = profile_from_dict(payload)
    assert p.tone.exposure == 100
    assert p.tone.contrast == 3.0
    assert p.tone.saturation == 0.0
    assert p.tone.s_curve == -100
    assert p.tone.lab_compress_min == 100
    assert p.tone.lab_compress_max == 0


def test_bad_hex_falls_back_to_black() -> None:
    p = profile_from_dict(
        {
            "slug": "bad",
            "name": "Bad",
            "family": "spectra6",
            "palette": {"black": "not-a-hex"},
        }
    )
    # First tuple (black) parses to (0,0,0) despite garbage input.
    assert p.palette.as_tuples()[0] == (0, 0, 0)


def test_slugify_normalises_names() -> None:
    assert slugify("My Warm Study 5000K!") == "my-warm-study-5000k"
    assert slugify("   Multiple   spaces   ") == "multiple-spaces"
    assert slugify("42-degrees") == "p-42-degrees"  # leading-digit safety prefix
    assert slugify("") == "profile"


def test_store_round_trip(tmp_path: Path) -> None:
    store = PaletteProfileStore(tmp_path)
    assert store.list_all() == []
    profile = PaletteProfile(
        slug="my-eve",
        name="My eve",
        family="spectra6",
        palette=PaletteColors(),
        saved_at="2026-07-04T10:00:00+00:00",
    )
    store.save(profile)
    assert store.slug_available("my-eve") is False
    reloaded = store.load("my-eve")
    assert reloaded is not None
    assert reloaded.slug == "my-eve"
    assert reloaded.name == "My eve"
    assert len(store.list_all()) == 1
    assert store.delete("my-eve") is True
    assert store.load("my-eve") is None


def test_store_refuses_bundled_and_bad_slugs(tmp_path: Path) -> None:
    store = PaletteProfileStore(tmp_path)
    with pytest.raises(ValueError):
        store.save(
            PaletteProfile(
                slug="paperlesspaper-spectra6",
                name="Attempt",
                family="spectra6",
                bundled=True,
            )
        )
    with pytest.raises(ValueError):
        store.save(PaletteProfile(slug="INVALID slug", name="Bad", family="spectra6"))


def test_store_list_sorted_newest_first(tmp_path: Path) -> None:
    store = PaletteProfileStore(tmp_path)
    store.save(
        PaletteProfile(
            slug="old", name="Old", family="spectra6", saved_at="2026-01-01T00:00:00+00:00"
        )
    )
    store.save(
        PaletteProfile(
            slug="new", name="New", family="spectra6", saved_at="2026-07-04T00:00:00+00:00"
        )
    )
    listed = store.list_all()
    assert [p.slug for p in listed] == ["new", "old"]


def test_store_survives_garbage_json_files(tmp_path: Path) -> None:
    store = PaletteProfileStore(tmp_path)
    store.dir.mkdir()
    (store.dir / "broken.json").write_text("{this is not valid json", encoding="utf-8")
    (store.dir / "wrong-type.json").write_text('"a string, not an object"', encoding="utf-8")
    good = PaletteProfile(slug="good", name="Good", family="spectra6")
    store.save(good)
    listed = store.list_all()
    assert [p.slug for p in listed] == ["good"]
