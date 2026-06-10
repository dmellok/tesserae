"""Rotation pydantic model invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.state.rotation_model import Rotation, RotationStep


def _make_steps(*pairs: tuple[str, int]) -> list[RotationStep]:
    return [RotationStep(page_id=p, dwell_minutes=d) for p, d in pairs]


def test_minimal_rotation_validates() -> None:
    r = Rotation(
        id="kitchen",
        name="Kitchen",
        steps=_make_steps(("morning", 30), ("afternoon", 30)),
    )
    assert r.cycle_minutes == 60
    assert r.enabled is True
    assert r.anchor == "00:00"
    assert r.days_of_week == [0, 1, 2, 3, 4, 5, 6]


def test_id_pattern_rejects_uppercase() -> None:
    with pytest.raises(ValidationError):
        Rotation(id="Kitchen", name="x", steps=_make_steps(("p", 1)))


def test_steps_required() -> None:
    with pytest.raises(ValidationError):
        Rotation(id="x", name="x", steps=[])


def test_dwell_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Rotation(id="x", name="x", steps=_make_steps(("p", 0)))


def test_anchor_must_be_hhmm() -> None:
    with pytest.raises(ValidationError):
        Rotation(id="x", name="x", anchor="9am", steps=_make_steps(("p", 1)))
    with pytest.raises(ValidationError):
        Rotation(id="x", name="x", anchor="25:00", steps=_make_steps(("p", 1)))


def test_anchor_accepts_valid_hhmm() -> None:
    Rotation(id="x", name="x", anchor="06:30", steps=_make_steps(("p", 1)))
    Rotation(id="x", name="x", anchor="23:59", steps=_make_steps(("p", 1)))


def test_days_of_week_validates_range() -> None:
    with pytest.raises(ValidationError):
        Rotation(id="x", name="x", days_of_week=[7], steps=_make_steps(("p", 1)))


def test_days_of_week_dedupes_and_sorts() -> None:
    r = Rotation(id="x", name="x", days_of_week=[5, 1, 1, 3], steps=_make_steps(("p", 1)))
    assert r.days_of_week == [1, 3, 5]


def test_total_cycle_capped_at_one_week() -> None:
    """A rotation summing to >7 days is rejected to keep modulo math
    sensible. User can split into multiple rotations if they really
    want that."""
    with pytest.raises(ValidationError):
        Rotation(
            id="x",
            name="x",
            steps=_make_steps(("a", 5000), ("b", 5000), ("c", 5000)),
        )


def test_cycle_minutes_property_sums_steps() -> None:
    r = Rotation(
        id="x",
        name="x",
        steps=_make_steps(("a", 15), ("b", 30), ("c", 45)),
    )
    assert r.cycle_minutes == 90
