"""Pydantic model for schedules.

Two kinds of schedule:

* ``interval``, fires every ``interval_minutes``, optionally bounded by
  a time-of-day window and/or a day-of-week mask. ``every 15 minutes
  between 06:00 and 22:00 on weekdays``.
* ``daily``   , fires once per day at the time in ``fires_at`` (the date
  portion is ignored). Day-of-week filter still applies; the time-of-day
  window does not.

Higher ``priority`` schedules win when two are due at the same tick. Per-
renderer dither / saturation / etc. live on the renderer's own settings
section, schedules don't carry render-tuning knobs, they only decide
**when** to fire a push.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.state.conditions import Condition

DAYS_OF_WEEK_FULL: list[int] = [0, 1, 2, 3, 4, 5, 6]  # 0=Mon ... 6=Sun (ISO)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    page_id: str = Field(min_length=1)
    enabled: bool = True

    type: Literal["interval", "daily"]

    # interval-only
    interval_minutes: int | None = Field(default=None, ge=1, le=10_080)

    # daily-only, only the time portion is used; date is ignored.
    fires_at: datetime | None = None

    # Common windowing (interval only, daily ignores the window and only
    # honours days_of_week).
    days_of_week: list[int] = Field(default_factory=lambda: list(DAYS_OF_WEEK_FULL))
    time_of_day_start: str | None = None  # "HH:MM"
    time_of_day_end: str | None = None  # "HH:MM"

    priority: int = 0

    # Smart sync (issue #10): when enabled on an interval schedule,
    # the scheduler consults each bound device's telemetry-derived
    # ``predicted_next_wake_at`` and fires within ``smart_sync_lead_s``
    # seconds of a trusted device's wake (so the rendered frame is
    # waiting for the panel rather than being rendered after the
    # panel paint). Falls back to plain interval firing when no
    # bound device is trusted yet (warm-up window) or when the
    # schedule has no device bindings at all. ``interval_minutes``
    # stays in force as a floor so smart sync can't fire faster than
    # the configured cadence.
    smart_sync: bool = False
    smart_sync_lead_s: int = Field(default=10, ge=0, le=600)

    # Conditions are AND'd together; the schedule fires only when all
    # resolve to true. Empty list (the default) preserves the pre-0.48
    # "always ready when due" behaviour. See app.state.conditions for
    # the per-kind shape rules.
    conditions: list[Condition] = Field(default_factory=list)

    # Page to push when the schedule is due but conditions failed.
    # ``None`` (default) means "skip silently" (no History row, INFO-
    # level log only). When set, the scheduler pushes this page
    # instead of ``page_id`` and writes a normal History row tagged
    # ``"scheduler_fallback"`` so the user can see which path fired.
    fallback_page_id: str | None = None

    @field_validator("days_of_week")
    @classmethod
    def _validate_dow(cls, v: list[int]) -> list[int]:
        if not all(0 <= d <= 6 for d in v):
            raise ValueError("days_of_week entries must be 0..6 (0=Monday, 6=Sunday)")
        return sorted(set(v))

    @field_validator("time_of_day_start", "time_of_day_end")
    @classmethod
    def _validate_hhmm(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("time_of_day_* must be 'HH:MM' 24-hour")
        return v

    @model_validator(mode="after")
    def _check_type_fields(self) -> Schedule:
        if self.type == "interval" and self.interval_minutes is None:
            raise ValueError("interval schedule requires interval_minutes")
        if self.type == "daily" and self.fires_at is None:
            raise ValueError("daily schedule requires fires_at")
        return self
