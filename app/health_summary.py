"""Strict validation for Companion ``health.summary`` snapshots.

The iPhone is the authority for HealthKit selection and aggregation.  The
server accepts only the bounded, versioned summary contract and deliberately
does not accept raw samples, source/device identity, routes, heart rate,
metadata, or arbitrary extension fields.

Validation errors describe contract paths and rules only; they never include
the submitted value, so sensitive Health data cannot leak into API errors or
ordinary logs.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

HEALTH_SUMMARY_SOURCE_ID = "health.summary"
HEALTH_SUMMARY_MAX_BYTES = 256 * 1024

_ENVELOPE_FIELDS = frozenset({"version", "source_id", "generated_at", "expires_at", "data"})
_DATA_FIELDS = frozenset(
    {
        "time_zone",
        "window_start_date",
        "window_end_date",
        "activity",
        "sleep",
        "workouts",
    }
)
_ACTIVITY_DAY_FIELDS = frozenset(
    {
        "date",
        "steps",
        "walking_running_distance_meters",
        "move_mode",
        "active_energy_kcal",
        "active_energy_goal_kcal",
        "move_minutes",
        "move_goal_minutes",
        "exercise_minutes",
        "exercise_goal_minutes",
        "stand_hours",
        "stand_goal_hours",
    }
)
_SLEEP_NIGHT_FIELDS = frozenset(
    {
        "wake_date",
        "start_at",
        "end_at",
        "in_bed_minutes",
        "asleep_minutes",
        "awake_minutes",
        "core_minutes",
        "deep_minutes",
        "rem_minutes",
        "unspecified_minutes",
    }
)
_WORKOUT_FIELDS = frozenset(
    {
        "id",
        "activity_type",
        "start_at",
        "end_at",
        "duration_seconds",
        "active_energy_kcal",
        "walking_running_distance_meters",
        "cycling_distance_meters",
        "swimming_distance_meters",
        "wheelchair_distance_meters",
        "flights_climbed",
        "swimming_stroke_count",
        "segments",
        "segments_truncated",
    }
)
_SEGMENT_FIELDS = frozenset(
    {
        "ordinal",
        "activity_type",
        "start_at",
        "end_at",
        "duration_seconds",
        "active_energy_kcal",
        "walking_running_distance_meters",
        "cycling_distance_meters",
        "swimming_distance_meters",
        "wheelchair_distance_meters",
        "flights_climbed",
        "swimming_stroke_count",
    }
)
_TIME_ZONE_RE = re.compile(r"^[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*$")
_WORKOUT_ID_RE = re.compile(r"^[0-9a-f]{24}$")

_WORKOUT_ACTIVITY_TYPES = frozenset(
    {
        "american_football",
        "archery",
        "australian_football",
        "badminton",
        "barre",
        "baseball",
        "basketball",
        "bowling",
        "boxing",
        "cardio_dance",
        "climbing",
        "cooldown",
        "core_training",
        "cricket",
        "cross_country_skiing",
        "cross_training",
        "curling",
        "cycling",
        "dance",
        "dance_inspired_training",
        "disc_sports",
        "downhill_skiing",
        "elliptical",
        "equestrian_sports",
        "fencing",
        "fishing",
        "fitness_gaming",
        "flexibility",
        "functional_strength_training",
        "golf",
        "gymnastics",
        "hand_cycling",
        "handball",
        "high_intensity_interval_training",
        "hiking",
        "hockey",
        "hunting",
        "jump_rope",
        "kickboxing",
        "lacrosse",
        "martial_arts",
        "mind_and_body",
        "mixed_cardio",
        "mixed_metabolic_cardio_training",
        "other",
        "paddle_sports",
        "pickleball",
        "pilates",
        "play",
        "preparation_and_recovery",
        "racquetball",
        "rowing",
        "rugby",
        "running",
        "sailing",
        "skating_sports",
        "snow_sports",
        "snowboarding",
        "soccer",
        "social_dance",
        "softball",
        "squash",
        "stair_climbing",
        "stairs",
        "step_training",
        "surfing_sports",
        "swim_bike_run",
        "swimming",
        "table_tennis",
        "tai_chi",
        "tennis",
        "track_and_field",
        "traditional_strength_training",
        "transition",
        "underwater_diving",
        "volleyball",
        "walking",
        "water_fitness",
        "water_polo",
        "water_sports",
        "wheelchair_run_pace",
        "wheelchair_walk_pace",
        "wrestling",
        "yoga",
    }
)

_ACTIVITY_NULLABLE_INTS = {
    "steps": 1_000_000,
    "move_minutes": 1_440,
    "move_goal_minutes": 1_440,
    "exercise_minutes": 1_440,
    "exercise_goal_minutes": 1_440,
    "stand_hours": 24,
    "stand_goal_hours": 24,
}
_ACTIVITY_NULLABLE_NUMBERS = {
    "walking_running_distance_meters": 1_000_000,
    "active_energy_kcal": 100_000,
    "active_energy_goal_kcal": 100_000,
}
_SLEEP_NULLABLE_INTS = {
    "in_bed_minutes": 1_440,
    "asleep_minutes": 1_440,
    "awake_minutes": 1_440,
    "core_minutes": 1_440,
    "deep_minutes": 1_440,
    "rem_minutes": 1_440,
    "unspecified_minutes": 1_440,
}
_WORKOUT_NULLABLE_NUMBERS = {
    "active_energy_kcal": 100_000,
    "walking_running_distance_meters": 10_000_000,
    "cycling_distance_meters": 10_000_000,
    "swimming_distance_meters": 10_000_000,
    "wheelchair_distance_meters": 10_000_000,
}
_WORKOUT_NULLABLE_INTS = {
    "flights_climbed": 10_000_000,
    "swimming_stroke_count": 10_000_000,
}


class InvalidHealthSummary(ValueError):
    """A privacy-safe strict-contract validation failure."""


def _object(value: Any, fields: frozenset[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidHealthSummary(f"{path} must be an object")
    actual = set(value)
    missing = fields - actual
    if missing:
        raise InvalidHealthSummary(f"{path} is missing required fields")
    if actual - fields:
        raise InvalidHealthSummary(f"{path} has unexpected fields")
    return value


def _array(value: Any, path: str, *, maximum: int, exact: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidHealthSummary(f"{path} must be an array")
    if exact is not None and len(value) != exact:
        raise InvalidHealthSummary(f"{path} must contain exactly {exact} entries")
    if len(value) > maximum:
        raise InvalidHealthSummary(f"{path} exceeds its {maximum}-entry limit")
    return value


def _iso_date(value: Any, path: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise InvalidHealthSummary(f"{path} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidHealthSummary(f"{path} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise InvalidHealthSummary(f"{path} must be an ISO date")
    return parsed


def _utc_instant(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidHealthSummary(f"{path} must be a UTC ISO 8601 instant")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidHealthSummary(f"{path} must be a UTC ISO 8601 instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise InvalidHealthSummary(f"{path} must be a UTC ISO 8601 instant")
    return parsed.astimezone(UTC)


def _required_bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise InvalidHealthSummary(f"{path} must be a boolean")


def _required_int(value: Any, path: str, *, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= maximum):
        raise InvalidHealthSummary(f"{path} must be an integer from 0 to {maximum}")


def _nullable_int(value: Any, path: str, *, maximum: int) -> None:
    if value is None:
        return
    _required_int(value, path, maximum=maximum)


def _nullable_number(value: Any, path: str, *, maximum: float) -> None:
    if value is None:
        return
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not (0 <= float(value) <= maximum)
    ):
        raise InvalidHealthSummary(f"{path} must be a finite number from 0 to {maximum:g}")


def _activity_type(value: Any, path: str) -> None:
    if not isinstance(value, str) or value not in _WORKOUT_ACTIVITY_TYPES:
        raise InvalidHealthSummary(f"{path} must be a recognized activity type")


def _interval(
    value: dict[str, Any],
    path: str,
    *,
    maximum_seconds: int,
) -> tuple[datetime, datetime]:
    start = _utc_instant(value.get("start_at"), f"{path}.start_at")
    end = _utc_instant(value.get("end_at"), f"{path}.end_at")
    wall_seconds = (end - start).total_seconds()
    if not (0 < wall_seconds <= maximum_seconds):
        raise InvalidHealthSummary(
            f"{path} start_at/end_at must form a positive interval no longer than {maximum_seconds} seconds"
        )
    return start, end


def _validate_activity(
    raw: Any,
    *,
    expected_dates: list[date],
) -> None:
    activity = _object(raw, frozenset({"days"}), "data.activity")
    days = _array(activity["days"], "data.activity.days", maximum=7, exact=7)
    for index, raw_day in enumerate(days):
        path = f"data.activity.days[{index}]"
        day = _object(raw_day, _ACTIVITY_DAY_FIELDS, path)
        if _iso_date(day["date"], f"{path}.date") != expected_dates[index]:
            raise InvalidHealthSummary("data.activity.days dates must exactly match the window")
        for key, maximum in _ACTIVITY_NULLABLE_INTS.items():
            _nullable_int(day[key], f"{path}.{key}", maximum=maximum)
        for key, maximum in _ACTIVITY_NULLABLE_NUMBERS.items():
            _nullable_number(day[key], f"{path}.{key}", maximum=maximum)

        move_mode = day["move_mode"]
        if move_mode not in (None, "active_energy", "move_time"):
            raise InvalidHealthSummary(f"{path}.move_mode is invalid")
        if move_mode == "active_energy" and any(
            day[key] is not None for key in ("move_minutes", "move_goal_minutes")
        ):
            raise InvalidHealthSummary(f"{path} active_energy mode requires null move minutes")
        if move_mode == "move_time" and any(
            day[key] is not None for key in ("active_energy_kcal", "active_energy_goal_kcal")
        ):
            raise InvalidHealthSummary(f"{path} move_time mode requires null active energy")
        if move_mode is None and any(
            day[key] is not None
            for key in (
                "active_energy_kcal",
                "active_energy_goal_kcal",
                "move_minutes",
                "move_goal_minutes",
                "exercise_minutes",
                "exercise_goal_minutes",
                "stand_hours",
                "stand_goal_hours",
            )
        ):
            raise InvalidHealthSummary(f"{path} null move_mode requires null ring values")


def _validate_sleep(
    raw: Any,
    *,
    expected_dates: set[date],
    timezone: ZoneInfo,
) -> None:
    sleep = _object(raw, frozenset({"nights"}), "data.sleep")
    nights = _array(sleep["nights"], "data.sleep.nights", maximum=7)
    previous_wake_date: date | None = None
    for index, raw_night in enumerate(nights):
        path = f"data.sleep.nights[{index}]"
        night = _object(raw_night, _SLEEP_NIGHT_FIELDS, path)
        wake_date = _iso_date(night["wake_date"], f"{path}.wake_date")
        if wake_date not in expected_dates:
            raise InvalidHealthSummary(f"{path}.wake_date must be inside the window")
        if previous_wake_date is not None and wake_date <= previous_wake_date:
            raise InvalidHealthSummary("data.sleep.nights must have unique ascending wake dates")
        previous_wake_date = wake_date
        _start, end = _interval(night, path, maximum_seconds=86_400)
        if end.astimezone(timezone).date() != wake_date:
            raise InvalidHealthSummary(f"{path}.wake_date must contain end_at in data.time_zone")
        for key, maximum in _SLEEP_NULLABLE_INTS.items():
            _nullable_int(night[key], f"{path}.{key}", maximum=maximum)


def _validate_workout_metrics(value: dict[str, Any], path: str) -> None:
    for key, maximum in _WORKOUT_NULLABLE_NUMBERS.items():
        _nullable_number(value[key], f"{path}.{key}", maximum=maximum)
    for key, maximum in _WORKOUT_NULLABLE_INTS.items():
        _nullable_int(value[key], f"{path}.{key}", maximum=maximum)


def _validate_workouts(
    raw: Any,
    *,
    expected_dates: set[date],
    timezone: ZoneInfo,
) -> None:
    workouts = _object(raw, frozenset({"items", "items_truncated"}), "data.workouts")
    items = _array(workouts["items"], "data.workouts.items", maximum=100)
    _required_bool(workouts["items_truncated"], "data.workouts.items_truncated")

    seen_ids: set[str] = set()
    previous_start: datetime | None = None
    total_segments = 0
    for workout_index, raw_workout in enumerate(items):
        path = f"data.workouts.items[{workout_index}]"
        workout = _object(raw_workout, _WORKOUT_FIELDS, path)
        workout_id = workout["id"]
        if not isinstance(workout_id, str) or _WORKOUT_ID_RE.fullmatch(workout_id) is None:
            raise InvalidHealthSummary(f"{path}.id must be 24 lowercase hexadecimal characters")
        if workout_id in seen_ids:
            raise InvalidHealthSummary("data.workouts.items ids must be unique")
        seen_ids.add(workout_id)
        _activity_type(workout["activity_type"], f"{path}.activity_type")
        start, end = _interval(workout, path, maximum_seconds=604_800)
        if start.astimezone(timezone).date() not in expected_dates:
            raise InvalidHealthSummary(f"{path}.start_at must fall inside the window")
        if previous_start is not None and start < previous_start:
            raise InvalidHealthSummary("data.workouts.items must be sorted by start_at")
        previous_start = start
        _required_int(workout["duration_seconds"], f"{path}.duration_seconds", maximum=604_800)
        if workout["duration_seconds"] > (end - start).total_seconds():
            raise InvalidHealthSummary(f"{path}.duration_seconds cannot exceed its interval")
        _validate_workout_metrics(workout, path)
        _required_bool(workout["segments_truncated"], f"{path}.segments_truncated")

        segments = _array(workout["segments"], f"{path}.segments", maximum=64)
        if workout["segments_truncated"] and segments:
            raise InvalidHealthSummary(
                f"{path}.segments must be empty when segments_truncated is true"
            )
        total_segments += len(segments)
        if total_segments > 256:
            raise InvalidHealthSummary("data.workouts exceeds the 256-segment snapshot limit")

        previous_segment_start: datetime | None = None
        for segment_index, raw_segment in enumerate(segments):
            segment_path = f"{path}.segments[{segment_index}]"
            segment = _object(raw_segment, _SEGMENT_FIELDS, segment_path)
            if segment["ordinal"] != segment_index:
                raise InvalidHealthSummary(
                    f"{path}.segments ordinals must be zero-based and contiguous"
                )
            _activity_type(segment["activity_type"], f"{segment_path}.activity_type")
            segment_start, segment_end = _interval(
                segment,
                segment_path,
                maximum_seconds=604_800,
            )
            if segment_start < start or segment_end > end:
                raise InvalidHealthSummary(f"{segment_path} must stay inside its workout interval")
            if previous_segment_start is not None and segment_start < previous_segment_start:
                raise InvalidHealthSummary(f"{path}.segments must be sorted by start_at")
            previous_segment_start = segment_start
            _required_int(
                segment["duration_seconds"],
                f"{segment_path}.duration_seconds",
                maximum=604_800,
            )
            if segment["duration_seconds"] > (segment_end - segment_start).total_seconds():
                raise InvalidHealthSummary(
                    f"{segment_path}.duration_seconds cannot exceed its interval"
                )
            _validate_workout_metrics(segment, segment_path)


def validate_health_summary(
    source_id: str,
    body: Any,
    *,
    active_timezone: str,
    snapshot_version: str,
    maximum_ttl_seconds: int,
) -> tuple[dict[str, Any], float, float | None]:
    """Validate and return ``(snapshot, generated_epoch, expires_epoch)``."""

    snapshot = _object(body, _ENVELOPE_FIELDS, "snapshot")
    if snapshot["version"] != snapshot_version:
        raise InvalidHealthSummary("unsupported snapshot version")
    if source_id != HEALTH_SUMMARY_SOURCE_ID or snapshot["source_id"] != source_id:
        raise InvalidHealthSummary("source_id does not match the health.summary path")

    generated = _utc_instant(snapshot["generated_at"], "generated_at")
    expires = (
        _utc_instant(snapshot["expires_at"], "expires_at")
        if snapshot["expires_at"] is not None
        else None
    )
    if expires is not None:
        ttl_seconds = (expires - generated).total_seconds()
        if not (0 < ttl_seconds <= maximum_ttl_seconds):
            raise InvalidHealthSummary("expires_at exceeds the maximum retention window")

    data = _object(snapshot["data"], _DATA_FIELDS, "data")
    time_zone = data["time_zone"]
    if (
        not isinstance(time_zone, str)
        or not (1 <= len(time_zone) <= 64)
        or _TIME_ZONE_RE.fullmatch(time_zone) is None
    ):
        raise InvalidHealthSummary("data.time_zone must be a valid IANA time zone")
    # Compare before constructing, so ``ZoneInfo`` only ever sees this
    # instance's own zone. The key regex admits ``..`` as a path component,
    # and a key that escapes TZPATH raises ``ValueError`` rather than the
    # ``ZoneInfoNotFoundError`` a missing zone raises, which would leave the
    # request as an unhandled 500 instead of the contract's 400.
    if time_zone != active_timezone:
        raise InvalidHealthSummary("data.time_zone must match the active Tesserae instance")
    try:
        timezone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidHealthSummary("data.time_zone must be a valid IANA time zone") from exc

    window_start = _iso_date(data["window_start_date"], "data.window_start_date")
    window_end = _iso_date(data["window_end_date"], "data.window_end_date")
    if window_end - window_start != timedelta(days=6):
        raise InvalidHealthSummary("data date window must contain exactly seven calendar dates")
    if generated.astimezone(timezone).date() != window_end:
        raise InvalidHealthSummary(
            "data.window_end_date must contain generated_at in data.time_zone"
        )
    expected_dates = [window_start + timedelta(days=offset) for offset in range(7)]
    expected_date_set = set(expected_dates)

    sections = (data["activity"], data["sleep"], data["workouts"])
    if all(section is None for section in sections):
        raise InvalidHealthSummary("at least one Health summary section must be shared")
    if data["activity"] is not None:
        _validate_activity(data["activity"], expected_dates=expected_dates)
    if data["sleep"] is not None:
        _validate_sleep(data["sleep"], expected_dates=expected_date_set, timezone=timezone)
    if data["workouts"] is not None:
        _validate_workouts(data["workouts"], expected_dates=expected_date_set, timezone=timezone)

    return snapshot, generated.timestamp(), expires.timestamp() if expires is not None else None
