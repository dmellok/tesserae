"""Evaluator for ``Condition`` records on schedules + rotation steps.

The scheduler refreshes the evaluator's HA state cache once per tick
(``refresh_ha_states``) so a tick with N conditions across M schedules
makes one HA call rather than M*N. Time + sun resolvers are pure
local computations against the app's configured timezone and lat /
lon, so they don't need refreshing.

Each evaluate call returns one ``ConditionResult`` per condition so
the "Test conditions" button can show users exactly which condition
failed and what value was observed at evaluate-time.

mypy --strict applies, see pyproject.toml.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.state.conditions import Condition

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConditionResult:
    """Per-condition outcome from ``evaluate`` so the editor's Test
    button can paint pass/fail rows with the actual observed value."""

    condition: Condition
    passed: bool
    observed: str  # human-readable: "binary_sensor.front_door = on",
    #             # "now=18:42, window=06:00-23:00", "elevation=-3.2"
    reason: str = ""


class ConditionEvaluator:
    """Resolves ``Condition`` records against three sources: HA entity
    states (cached + refreshed per tick), the app's configured wall
    clock, and locally-computed sun position.

    Thread-safe: the HA cache mutex protects refresh / read. Time +
    sun resolvers are stateless, so no locking needed there."""

    def __init__(
        self,
        *,
        ha_get_states: Any = None,  # callable returning list[{entity_id, state, last_changed}]
        timezone_provider: Any = None,  # callable returning ZoneInfo | None
        location_provider: Any = None,  # callable returning (lat, lon) | (None, None)
    ) -> None:
        self._ha_get_states = ha_get_states
        self._tz_provider = timezone_provider or (lambda: None)
        self._loc_provider = location_provider or (lambda: (None, None))
        self._ha_cache: dict[str, dict[str, Any]] = {}
        self._ha_cache_ts: float = 0.0
        self._ha_lock = threading.Lock()
        self._sun_cache: dict[
            tuple[date, float, float], tuple[datetime | None, datetime | None]
        ] = {}

    # -- HA state cache -------------------------------------------------

    def refresh_ha_states(self) -> bool:
        """Pull the full HA states snapshot into the in-process cache.

        Best-effort: if HA is unreachable we keep the previous snapshot
        and return False so the scheduler can decide whether to surface
        a warning. Per the design we fail-OPEN: any condition whose
        entity we can't observe evaluates to True so a HA outage
        doesn't pin every dashboard."""
        if self._ha_get_states is None:
            return False
        try:
            states = list(self._ha_get_states())
        except Exception as err:
            logger.warning("HA state refresh failed: %s", err)
            return False
        with self._ha_lock:
            self._ha_cache = {s["entity_id"]: s for s in states if "entity_id" in s}
        return True

    def ha_state(self, entity_id: str) -> dict[str, Any] | None:
        with self._ha_lock:
            return self._ha_cache.get(entity_id)

    # -- top-level evaluate --------------------------------------------

    def all_pass(self, conditions: list[Condition], *, when: datetime | None = None) -> bool:
        """Fast path: short-circuit on first failure. Used by the
        scheduler's hot loop where the per-condition detail isn't
        needed; the Test button uses ``evaluate`` for that."""
        if not conditions:
            return True
        return all(self._eval_one(cond, when=when).passed for cond in conditions)

    def evaluate(
        self, conditions: list[Condition], *, when: datetime | None = None
    ) -> list[ConditionResult]:
        """Full per-condition pass/fail with observed values. Used by
        the editor's Test button and by the running-state indicators."""
        return [self._eval_one(cond, when=when) for cond in conditions]

    # -- per-condition dispatch ----------------------------------------

    def _eval_one(self, cond: Condition, *, when: datetime | None) -> ConditionResult:
        try:
            if cond.is_ha_entity():
                return self._eval_ha(cond)
            if cond.is_time_window():
                return self._eval_time(cond, when=when)
            if cond.is_sun():
                return self._eval_sun(cond, when=when)
        except Exception as err:
            return ConditionResult(
                condition=cond,
                passed=True,  # fail-open
                observed="evaluation error",
                reason=f"{type(err).__name__}: {err}",
            )
        return ConditionResult(
            condition=cond,
            passed=True,
            observed="unknown source_kind",
            reason="unhandled source_kind (fail-open)",
        )

    # -- HA entity resolver --------------------------------------------

    def _eval_ha(self, cond: Condition) -> ConditionResult:
        state = self.ha_state(cond.source_id)
        if state is None:
            # Unknown entity = fail-open. Logging the miss helps the
            # user notice a typo in the entity_id without breaking
            # their dashboards.
            return ConditionResult(
                condition=cond,
                passed=True,
                observed=f"{cond.source_id}: not in HA cache",
                reason="entity not found (fail-open)",
            )
        raw = state.get("state", "")
        op = cond.operator
        if op in {"==", "!="}:
            target = str(cond.value)
            cmp_eq = str(raw) == target
            passed = cmp_eq if op == "==" else not cmp_eq
            return ConditionResult(
                condition=cond,
                passed=passed,
                observed=f"{cond.source_id}={raw}",
            )
        if op == "in":
            values = {str(v) for v in (cond.value or [])}
            passed = str(raw) in values
            return ConditionResult(
                condition=cond,
                passed=passed,
                observed=f"{cond.source_id}={raw}, want in {sorted(values)}",
            )
        if op in {">", "<", ">=", "<="}:
            try:
                obs = float(raw)
            except (TypeError, ValueError):
                return ConditionResult(
                    condition=cond,
                    passed=True,  # non-numeric reading is fail-open for numeric ops
                    observed=f"{cond.source_id}={raw} (not numeric)",
                    reason="numeric operator on non-numeric state (fail-open)",
                )
            target_f = float(cond.value)
            passed = {
                ">": obs > target_f,
                "<": obs < target_f,
                ">=": obs >= target_f,
                "<=": obs <= target_f,
            }[op]
            return ConditionResult(
                condition=cond,
                passed=passed,
                observed=f"{cond.source_id}={obs:g} (want {op} {target_f:g})",
            )
        if op == "present_within_seconds":
            last_changed = state.get("last_changed") or state.get("last_updated")
            if not isinstance(last_changed, str):
                return ConditionResult(
                    condition=cond,
                    passed=True,
                    observed=f"{cond.source_id}: no last_changed",
                    reason="missing last_changed (fail-open)",
                )
            try:
                lc = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
            except ValueError:
                return ConditionResult(
                    condition=cond,
                    passed=True,
                    observed=f"{cond.source_id}: bad last_changed",
                    reason="unparseable last_changed (fail-open)",
                )
            age_s = max(0.0, (datetime.now(UTC) - lc).total_seconds())
            window = float(cond.value)
            return ConditionResult(
                condition=cond,
                passed=age_s <= window,
                observed=f"{cond.source_id} updated {age_s:.0f}s ago (limit {window:.0f}s)",
            )
        return ConditionResult(
            condition=cond,
            passed=True,
            observed=f"unknown operator {op}",
            reason="fail-open",
        )

    # -- time window resolver ------------------------------------------

    def _eval_time(self, cond: Condition, *, when: datetime | None) -> ConditionResult:
        tz = self._tz_provider() or ZoneInfo("UTC")
        ts = (when or datetime.now(UTC)).astimezone(tz)
        value = cond.value or {}
        start = str(value.get("start_local", "00:00"))
        end = str(value.get("end_local", "23:59"))
        dows = value.get("days_of_week") or []
        # Empty dows list means "every day", same convention the rest
        # of the model uses.
        if dows and ts.weekday() not in dows:
            return ConditionResult(
                condition=cond,
                passed=False,
                observed=f"weekday {ts.strftime('%a')} not in {dows}",
            )
        if not _in_window(ts.strftime("%H:%M"), start, end):
            return ConditionResult(
                condition=cond,
                passed=False,
                observed=f"now={ts.strftime('%H:%M')}, window={start}-{end}",
            )
        return ConditionResult(
            condition=cond,
            passed=True,
            observed=f"now={ts.strftime('%H:%M')}, window={start}-{end}",
        )

    # -- sun resolver --------------------------------------------------

    def _eval_sun(self, cond: Condition, *, when: datetime | None) -> ConditionResult:
        lat_raw, lon_raw = self._loc_provider()
        if lat_raw is None or lon_raw is None:
            return ConditionResult(
                condition=cond,
                passed=True,
                observed="lat/lon unset",
                reason="settings.app.latitude/longitude empty (fail-open)",
            )
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except (TypeError, ValueError):
            return ConditionResult(
                condition=cond,
                passed=True,
                observed=f"lat/lon unparseable ({lat_raw!r}, {lon_raw!r})",
                reason="settings malformed (fail-open)",
            )
        tz = self._tz_provider() or ZoneInfo("UTC")
        now_local = (when or datetime.now(UTC)).astimezone(tz)
        rise_utc, set_utc = self._sun_times(lat, lon, now_local.date())
        if rise_utc is None or set_utc is None:
            return ConditionResult(
                condition=cond,
                passed=True,
                observed="polar day/night (sun never crosses horizon)",
                reason="no sunrise/sunset on this date (fail-open)",
            )
        offset = int((cond.value or {}).get("offset_minutes", 0))
        rise_offset = rise_utc + timedelta(minutes=offset)
        set_offset = set_utc + timedelta(minutes=offset)
        now_utc = now_local.astimezone(UTC)
        op = cond.operator
        if op == "before_sunrise":
            passed = now_utc < rise_offset
            obs = f"now={_fmt(now_utc, tz)}, sunrise={_fmt(rise_offset, tz)}"
        elif op == "after_sunset":
            passed = now_utc > set_offset
            obs = f"now={_fmt(now_utc, tz)}, sunset={_fmt(set_offset, tz)}"
        elif op == "is_day":
            passed = rise_offset <= now_utc <= set_offset
            obs = f"now={_fmt(now_utc, tz)}, day {_fmt(rise_offset, tz)}-{_fmt(set_offset, tz)}"
        elif op == "is_night":
            passed = not (rise_offset <= now_utc <= set_offset)
            obs = f"now={_fmt(now_utc, tz)}, day {_fmt(rise_offset, tz)}-{_fmt(set_offset, tz)}"
        else:
            return ConditionResult(
                condition=cond,
                passed=True,
                observed=f"unknown sun operator {op}",
                reason="fail-open",
            )
        return ConditionResult(condition=cond, passed=passed, observed=obs)

    def _sun_times(
        self, lat: float, lon: float, day: date
    ) -> tuple[datetime | None, datetime | None]:
        key = (day, round(lat, 4), round(lon, 4))
        cached = self._sun_cache.get(key)
        if cached is not None:
            return cached
        result = _compute_sunrise_sunset(lat, lon, day)
        self._sun_cache[key] = result
        # Trim stale dates so a long-running process doesn't accumulate
        # a year of cached values.
        if len(self._sun_cache) > 14:
            cutoff = day - timedelta(days=2)
            self._sun_cache = {k: v for k, v in self._sun_cache.items() if k[0] >= cutoff}
        return result


# -- helpers -----------------------------------------------------------


def _in_window(hhmm: str, start: str, end: str) -> bool:
    """True iff ``hhmm`` is within the [start, end] wall-clock window,
    honouring wrap-around (start > end means overnight: 22:00 -> 06:00).
    Boundaries are inclusive on both sides for predictability at exact
    minute boundaries."""
    if start == end:
        return hhmm == start
    if start < end:
        return start <= hhmm <= end
    return hhmm >= start or hhmm <= end


def _fmt(dt: datetime, tz: ZoneInfo) -> str:
    """Compact local-time string for observed reasons."""
    return dt.astimezone(tz).strftime("%H:%M")


def safe_zoneinfo(name: str | None) -> ZoneInfo:
    """Parse an IANA zone string with a UTC fallback. Shared by the
    location_provider wiring + tests."""
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


# -- sun geometry: NOAA-style approximation ----------------------------
#
# This is the standard "general solar position algorithm" boiled down
# to the bits we need: sunrise + sunset for a given date and location.
# Accuracy is within ~1 min for typical latitudes, which is fine for
# conditional scheduling (the use case is "show this dashboard after
# sunset", not "fire at the exact astronomical moment"). Reference:
# https://en.wikipedia.org/wiki/Sunrise_equation


def _compute_sunrise_sunset(
    lat: float, lon: float, day: date
) -> tuple[datetime | None, datetime | None]:
    # Julian date at 0h UTC on the given day.
    a = (14 - day.month) // 12
    y = day.year + 4800 - a
    m = day.month + 12 * a - 3
    jdn = day.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    # Days since J2000.0 (corrected for longitude so noon stays solar)
    n = jdn - 2451545.0 + 0.0008 - lon / 360.0
    # Mean solar noon
    j_star = n
    # Solar mean anomaly (degrees)
    M = (357.5291 + 0.98560028 * j_star) % 360
    M_rad = math.radians(M)
    # Equation of the centre
    C = 1.9148 * math.sin(M_rad) + 0.02 * math.sin(2 * M_rad) + 0.0003 * math.sin(3 * M_rad)
    # Ecliptic longitude (degrees)
    lam = (M + C + 180 + 102.9372) % 360
    lam_rad = math.radians(lam)
    # Solar transit (Julian date)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(M_rad) - 0.0069 * math.sin(2 * lam_rad)
    # Declination of the sun
    sin_decl = math.sin(lam_rad) * math.sin(math.radians(23.4397))
    decl = math.asin(sin_decl)
    # Hour angle for sunrise (using -0.83 degree apparent radius +
    # refraction adjustment, same as the Wikipedia formula).
    lat_rad = math.radians(lat)
    cos_omega = (math.sin(math.radians(-0.83)) - math.sin(lat_rad) * sin_decl) / (
        math.cos(lat_rad) * math.cos(decl)
    )
    if cos_omega < -1.0 or cos_omega > 1.0:
        return None, None
    omega_deg = math.degrees(math.acos(cos_omega))
    j_rise = j_transit - omega_deg / 360.0
    j_set = j_transit + omega_deg / 360.0
    return _julian_to_datetime(j_rise), _julian_to_datetime(j_set)


def _julian_to_datetime(j: float) -> datetime:
    # Julian date -> Unix timestamp -> UTC datetime
    unix_s = (j - 2440587.5) * 86400.0
    return datetime.fromtimestamp(unix_s, tz=UTC)
