"""Orientation calibration: card generator + the answer→orientation map."""

from __future__ import annotations

import io

from PIL import Image

from app.calibration import (
    ORIENTATION_CYCLE,
    build_calibration_card,
    is_portrait,
    target_orientation,
)


def test_card_renders_at_requested_size() -> None:
    png = build_calibration_card(800, 480)
    img = Image.open(io.BytesIO(png))
    assert img.size == (800, 480)
    assert img.format == "PNG"


def test_card_is_not_blank() -> None:
    # The grid + digits + arrow mean it can't be a single flat colour.
    img = Image.open(io.BytesIO(build_calibration_card(400, 400))).convert("RGB")
    assert len(img.getcolors(maxcolors=100000) or []) > 1


def test_upright_answer_keeps_pushed_orientation() -> None:
    # If ① is already in the top-left, the card is upright, keep whatever
    # it was pushed with, for every starting orientation.
    for o in ORIENTATION_CYCLE:
        assert target_orientation(o, top_left_number=1) == o


def test_each_answer_maps_to_a_valid_orientation() -> None:
    for o in ORIENTATION_CYCLE:
        for n in (1, 2, 3, 4):
            assert target_orientation(o, n) in ORIENTATION_CYCLE


def test_answers_span_all_four_orientations() -> None:
    # From a fixed starting point the four possible answers must resolve
    # to the four distinct orientations (they're 90° apart).
    results = {target_orientation("landscape", n) for n in (1, 2, 3, 4)}
    assert results == set(ORIENTATION_CYCLE)


def test_180_off_answer_is_a_flip_of_upright() -> None:
    # ④ in the top-left means the card is 180° from upright; the fix must
    # share the pushed aspect but be its flipped variant.
    assert target_orientation("landscape", 4) == "landscape_flipped"
    assert target_orientation("portrait", 4) == "portrait_flipped"


def test_is_portrait() -> None:
    assert is_portrait("portrait") and is_portrait("portrait_flipped")
    assert not is_portrait("landscape") and not is_portrait("landscape_flipped")
