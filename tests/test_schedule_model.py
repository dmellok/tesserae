"""Schedule model validation. Type-specific required-field invariants,
HH:MM format guards, day-of-week normalisation."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.state.schedule_model import Schedule


def test_interval_requires_minutes() -> None:
    with pytest.raises(ValidationError, match="interval_minutes"):
        Schedule(id="x", name="x", page_id="home", type="interval")


def test_daily_requires_fires_at() -> None:
    with pytest.raises(ValidationError, match="fires_at"):
        Schedule(id="x", name="x", page_id="home", type="daily")


def test_dow_normalisation_dedups_and_sorts() -> None:
    s = Schedule(
        id="x",
        name="x",
        page_id="home",
        type="interval",
        interval_minutes=15,
        days_of_week=[3, 1, 1, 5, 3],
    )
    assert s.days_of_week == [1, 3, 5]


def test_dow_rejects_out_of_range() -> None:
    with pytest.raises(ValidationError, match=r"0\.\.6"):
        Schedule(
            id="x",
            name="x",
            page_id="home",
            type="interval",
            interval_minutes=15,
            days_of_week=[9],
        )


def test_hhmm_format_enforced() -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        Schedule(
            id="x",
            name="x",
            page_id="home",
            type="interval",
            interval_minutes=15,
            time_of_day_start="9:00",
        )


def test_id_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        Schedule(id="Has Spaces", name="x", page_id="home", type="interval", interval_minutes=15)


def test_round_trip_through_model_dump() -> None:
    s = Schedule(
        id="morning",
        name="Morning push",
        page_id="home",
        type="daily",
        fires_at=datetime(2026, 1, 1, 7, 0),
    )
    raw = s.model_dump(mode="json", exclude_none=True)
    assert Schedule.model_validate(raw) == s
