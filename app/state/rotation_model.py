"""Pydantic model for dashboard rotations.

A rotation is an ordered list of (page, dwell_minutes) steps that cycle
on a fixed wall-clock anchor. The scheduler reads the rotation on each
tick, computes which step's window the current time falls into via
``(now_local - anchor_today) % cycle_minutes``, and if that step's page
differs from what was last pushed for this rotation, fires a push.

Example: morning dashboard for 30 minutes, afternoon dashboard for 30
minutes, repeating from ``00:00`` local:

    Rotation(
        id="kitchen_rotation",
        name="Kitchen alternation",
        anchor="00:00",
        device_ids=["kitchen_panel"],
        steps=[
            RotationStep(page_id="morning_briefing", dwell_minutes=30),
            RotationStep(page_id="afternoon_calendar", dwell_minutes=30),
        ],
    )

The total cycle is 60 minutes; at ``00:00`` step 0 starts, at ``00:30``
step 1 starts, at ``01:00`` step 0 again, etc. The anchor is a daily
re-anchor: at midnight local each day the cycle reseeds, so DST flips
don't desync long rotations.

Rotations and ``Schedule`` records can coexist; ``priority`` lets a
schedule preempt a rotation (e.g. a daily 09:00 schedule with priority
10 will fire that morning even when the rotation has a different step
current at 09:00).

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 0=Mon ... 6=Sun (ISO weekday minus 1). Mirrored from Schedule for
# consistency across the two scheduling primitives.
DAYS_OF_WEEK_FULL: list[int] = [0, 1, 2, 3, 4, 5, 6]

# A single step in a rotation cycle. Page + how long to dwell on it.
# Validated separately so the editor can use it as a row-shape.
DwellMinutes = Annotated[int, Field(ge=1, le=10_080)]


class RotationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1)
    dwell_minutes: DwellMinutes


class Rotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    enabled: bool = True

    # Devices this rotation pushes to. Empty means "use the device
    # bindings on each step's page" (same fall-through as schedules
    # without explicit ``device_ids``).
    device_ids: list[str] = Field(default_factory=list)

    # Ordered cycle. At least one step; degenerate "single step"
    # rotations are allowed and behave like a long-cadence schedule.
    steps: list[RotationStep] = Field(min_length=1)

    # When step 0 starts within the day. Local-time HH:MM. The cycle
    # reseeds at this anchor each local day so DST flips and long
    # rotations stay aligned to a sensible wall-clock moment.
    anchor: str = "00:00"

    # Day-of-week filter (0=Mon..6=Sun). On a non-matching day the
    # rotation doesn't fire; the scheduler picks back up on the next
    # matching day at the anchor.
    days_of_week: list[int] = Field(default_factory=lambda: list(DAYS_OF_WEEK_FULL))

    # Priority is compared against ``Schedule.priority`` when both a
    # rotation and a schedule are due on the same tick. Higher wins.
    priority: int = 0

    @field_validator("days_of_week")
    @classmethod
    def _validate_dow(cls, v: list[int]) -> list[int]:
        if not all(0 <= d <= 6 for d in v):
            raise ValueError("days_of_week entries must be 0..6 (0=Monday, 6=Sunday)")
        return sorted(set(v))

    @field_validator("anchor")
    @classmethod
    def _validate_anchor(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("anchor must be 'HH:MM' 24-hour")
        return v

    @model_validator(mode="after")
    def _validate_cycle(self) -> Rotation:
        # Total cycle must be at least 1 min (enforced by step bound)
        # but cap the WHOLE cycle at a week so an accidental 99-step
        # rotation with 10080-min dwells doesn't break math elsewhere.
        total = sum(s.dwell_minutes for s in self.steps)
        if total > 10_080:
            raise ValueError(
                f"total cycle ({total} min) exceeds one week (10080 min); "
                "split into multiple rotations or shorten dwell times"
            )
        return self

    @property
    def cycle_minutes(self) -> int:
        """Sum of all step dwell times (defines the cycle length)."""
        return sum(s.dwell_minutes for s in self.steps)
