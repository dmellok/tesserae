"""Condition primitives for conditional schedules + rotation steps.

A ``Condition`` is a 4-tuple (``source_kind``, ``source_id``,
``operator``, ``value``) that the scheduler evaluates at fire time.
All conditions on a schedule or rotation step are AND'd; a schedule
or step is considered "ready" only when every condition resolves to
true.

Three source kinds in v1:

* ``ha_entity``  - state of a single Home Assistant entity. Source id
  is the entity_id ("binary_sensor.front_door"). Operators cover
  state equality, numeric comparison, set membership, and freshness.
* ``time_window`` - wall-clock window in the app's configured
  timezone, with an optional day-of-week mask. Always uses operator
  ``"in"``; the value field carries start / end / days.
* ``sun``        - sun position derived from settings.app latitude /
  longitude. Operators are membership-style (``before_sunrise``,
  ``after_sunset``, ``is_day``, ``is_night``) and the value carries
  an optional ``offset_minutes`` so "30 min before sunset" is
  expressible without inventing a new operator.

The evaluator (``app.scheduler_conditions``) consumes these models
and returns a per-condition pass/fail with the actual value observed
at evaluate-time, so the editor's "Test conditions" button can show
the user exactly why a condition failed.

Pre-1.0: the shape is purely additive on existing Schedule /
RotationStep / Rotation models. Empty list = no conditions = always
ready, which matches every saved record predating this feature.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SourceKind = Literal["ha_entity", "time_window", "sun"]

# Operators by kind. The schema validators below dispatch on
# source_kind + operator to confirm the shape of ``value``.
HA_ENTITY_OPS: frozenset[str] = frozenset(
    {"==", "!=", ">", "<", ">=", "<=", "in", "present_within_seconds"}
)
TIME_WINDOW_OPS: frozenset[str] = frozenset({"in"})
SUN_OPS: frozenset[str] = frozenset({"before_sunrise", "after_sunset", "is_day", "is_night"})

# Day-of-week mask matches Schedule / Rotation: 0=Mon..6=Sun.
_DOW_VALID: frozenset[int] = frozenset(range(7))

_HHMM_RE: re.Pattern[str] = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Condition(BaseModel):
    """One condition row. The editor renders 0..N of these per
    schedule / step; the evaluator decides pass/fail."""

    model_config = ConfigDict(extra="forbid")

    source_kind: SourceKind
    # Entity id for ha_entity; empty string for time_window / sun.
    source_id: str = ""
    operator: str
    # Shape depends on source_kind + operator:
    #   ha_entity ==/!=/in:         state string / list of strings
    #   ha_entity >/</>=/<=:        number
    #   ha_entity present_within_seconds: number (seconds)
    #   time_window in:             {"start_local", "end_local", "days_of_week"}
    #   sun before_sunrise/...:     {"offset_minutes": int} (offset optional)
    value: Any = None

    @field_validator("source_id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        return (v or "").strip()

    @model_validator(mode="after")
    def _validate_shape(self) -> Condition:
        kind = self.source_kind
        op = self.operator
        if kind == "ha_entity":
            if op not in HA_ENTITY_OPS:
                raise ValueError(
                    f"unknown ha_entity operator {op!r}; allowed: {sorted(HA_ENTITY_OPS)}"
                )
            if not self.source_id:
                raise ValueError("ha_entity conditions require source_id (entity_id)")
            if op == "in":
                if not isinstance(self.value, list) or not all(
                    isinstance(x, (str, int, float)) for x in self.value
                ):
                    raise ValueError("'in' operator requires a list of scalars as value")
            elif op in {">", "<", ">=", "<="}:
                if not isinstance(self.value, (int, float)):
                    raise ValueError(f"numeric operator {op!r} requires a numeric value")
            elif op == "present_within_seconds" and (
                not isinstance(self.value, (int, float)) or self.value <= 0
            ):
                raise ValueError("present_within_seconds requires a positive numeric value")
            # ==, != accept any scalar; string compare wins because HA
            # entity states are stringly-typed on the wire even for
            # numeric sensors. Numeric coercion happens in the evaluator.
        elif kind == "time_window":
            if op not in TIME_WINDOW_OPS:
                raise ValueError(
                    f"unknown time_window operator {op!r}; allowed: {sorted(TIME_WINDOW_OPS)}"
                )
            if not isinstance(self.value, dict):
                raise ValueError("time_window value must be an object")
            start = self.value.get("start_local")
            end = self.value.get("end_local")
            if not (isinstance(start, str) and _HHMM_RE.match(start)):
                raise ValueError("time_window value.start_local must be HH:MM 24h")
            if not (isinstance(end, str) and _HHMM_RE.match(end)):
                raise ValueError("time_window value.end_local must be HH:MM 24h")
            dows = self.value.get("days_of_week", [])
            if not isinstance(dows, list) or not all(
                isinstance(d, int) and d in _DOW_VALID for d in dows
            ):
                raise ValueError("time_window value.days_of_week must be a list of 0..6 ints")
        elif kind == "sun":
            if op not in SUN_OPS:
                raise ValueError(f"unknown sun operator {op!r}; allowed: {sorted(SUN_OPS)}")
            value = self.value or {}
            if not isinstance(value, dict):
                raise ValueError("sun value must be an object or null")
            offset = value.get("offset_minutes", 0)
            if not isinstance(offset, int) or not (-720 <= offset <= 720):
                raise ValueError("sun value.offset_minutes must be an int in [-720, 720]")
        else:
            raise ValueError(f"unknown source_kind {kind!r}")
        return self

    def is_ha_entity(self) -> bool:
        return self.source_kind == "ha_entity"

    def is_time_window(self) -> bool:
        return self.source_kind == "time_window"

    def is_sun(self) -> bool:
        return self.source_kind == "sun"


def time_window(
    *,
    start_local: str,
    end_local: str,
    days_of_week: list[int] | None = None,
) -> Condition:
    """Convenience constructor: ``Condition.time_window("06:00", "23:00")``."""
    return Condition(
        source_kind="time_window",
        operator="in",
        value={
            "start_local": start_local,
            "end_local": end_local,
            "days_of_week": list(days_of_week) if days_of_week is not None else [],
        },
    )


def sun_condition(operator: str, *, offset_minutes: int = 0) -> Condition:
    """Convenience constructor for sun conditions. Keeps the value
    shape consistent (always a dict so callers don't need to remember
    when ``offset_minutes=0`` is implicit)."""
    return Condition(
        source_kind="sun",
        operator=operator,
        value={"offset_minutes": offset_minutes},
    )


def ha_condition(entity_id: str, operator: str, value: Any) -> Condition:
    """Convenience constructor for HA-entity conditions."""
    return Condition(
        source_kind="ha_entity",
        source_id=entity_id,
        operator=operator,
        value=value,
    )
