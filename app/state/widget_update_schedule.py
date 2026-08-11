"""Host-owned update schedules for individual widget placements."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator

_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class WidgetUpdateSchedule(BaseModel):
    """A placement-level refresh trigger.

    ``at=None`` means the local day boundary.  The first contract is daily
    only; keeping the discriminator in persisted state leaves room for future
    schedule kinds without overloading the existing page refresh cadence.
    """

    kind: Literal["daily"] = "daily"
    at: str | None = None

    @field_validator("at")
    @classmethod
    def _validate_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _TIME_RE.fullmatch(value):
            raise ValueError("at must be HH:MM in 24-hour time")
        return value
