"""SQLite-backed battery history per device.

Every MQTT heartbeat with a ``battery_pct`` field appends one row to
``battery_history`` via :meth:`BatteryHistory.record`. The admin page
at ``/devices/battery`` reads back ranges of rows for charts, and the
``device_battery`` widget calls :meth:`predict` for a 7-day
days-to-empty estimate.

We don't downsample yet; at ~96 heartbeats per device per day (the
default 15-min wake cadence) the table grows by ~35 k rows / device /
year. SQLite handles low-millions easily; we'll add a rolldown when
someone hits multi-year retention.

The prediction separates the trailing charging phase from the latest
clean discharge phase, then fits each phase with a robust Theil-Sen
median slope. ``slope_per_day`` is reserved for drain data (negative
or zero); live charging speed is exposed separately as
``charge_rate_per_day``. We bail out (return ``None``) when:

* there are fewer than ``MIN_SAMPLES`` points in the window, OR
* no fitted phase has enough distinct timestamps.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import statistics
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# A 7-day window means the slope reflects the user's current
# sleep-interval setting, not a long-ago tweak.
DEFAULT_WINDOW_DAYS: int = 7
MIN_SAMPLES: int = 8
# Sane upper bound. A slope of <0.01 %/day implies "this is plugged
# in"; we return None rather than predicting in years.
MIN_DRAIN_PER_DAY: float = 0.01
# A charge event = a pct jump >= this between consecutive samples.
# Used to find the most recent discharge segment so a regression
# isn't confounded by a recharge mid-window (which would otherwise
# read as a flat / positive slope and return no projection).
CHARGE_JUMP_PCT: int = 5
# Once we've isolated the most recent discharge segment we still
# need enough points in it for the regression to be meaningful;
# fall back to the full window otherwise. Lower than MIN_SAMPLES
# because a segment is by definition more recent: a device on a
# 15-min wake cadence produces ~96 samples/day, so 4 samples
# represents at least an hour of post-charge discharge data.
MIN_SEGMENT_SAMPLES: int = 4
# Time-to-full prediction kicks in when the recent slope is
# meaningfully positive (battery is actively charging). Mirrors
# ``MIN_DRAIN_PER_DAY`` but on the upward side: a slope of
# <0.5 %/day reads as "noise", not "charging".
MIN_CHARGE_PER_DAY: float = 0.5
# The charging slope is fit to the most recent CHARGING segment
# (the inverse of ``_last_discharge_segment``), to keep an old
# discharge episode mid-window from pulling the regression
# downward and masking an in-progress charge.
MIN_CHARGE_SEGMENT_SAMPLES: int = 4
# A two-point net rise is enough to distinguish a real charge from the
# common +/-1 percentage-point ADC wobble.
MIN_CHARGE_GAIN_PCT: int = 2
# Theil-Sen considers every pair (O(n^2)). A deterministic, evenly
# spaced cap keeps long high-frequency windows cheap while retaining
# the shape of the full series.
MAX_THEIL_SEN_SAMPLES: int = 300


@dataclass(frozen=True)
class Prediction:
    """One-shot regression result for a device's last N days.

    ``slope_per_day`` is negative on a draining battery (e.g. -3.2
    means it loses 3.2 % per day), zero when flat, and ``None`` when
    no clean discharge segment is available. It is never a charging
    slope. ``charge_rate_per_day`` carries the positive live charging
    slope when ``is_charging`` is true.
    ``days_to_20pct`` / ``days_to_empty`` are projections from
    ``current_pct``; both ``None`` when the battery's flat or rising.
    ``days_to_full`` is the inverse: present only when the recent
    samples show sustained charging (positive slope on the latest
    charging segment); ``None`` when flat or discharging.
    """

    device_id: str
    current_pct: float
    slope_per_day: float | None
    is_charging: bool
    charge_rate_per_day: float | None
    days_to_20pct: float | None
    days_to_empty: float | None
    days_to_full: float | None
    samples: int
    window_days: int


@dataclass(frozen=True)
class BatteryRow:
    """One persisted sample."""

    timestamp: float
    pct: int
    battery_mv: int | None


class BatteryHistory:
    """Thread-safe SQLite store of per-device battery samples.

    One row per heartbeat; cheap to write, fast to scan for a single
    device's recent window thanks to ``(device_id, timestamp)``.
    """

    SCHEMA: str = """
    CREATE TABLE IF NOT EXISTS battery_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id   TEXT    NOT NULL,
        timestamp   REAL    NOT NULL,
        pct         INTEGER NOT NULL,
        battery_mv  INTEGER
    );
    CREATE INDEX IF NOT EXISTS battery_history_by_device_time
        ON battery_history (device_id, timestamp DESC);
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- writes ---------------------------------------------------------

    def record(
        self,
        device_id: str,
        *,
        pct: int,
        battery_mv: int | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Append one sample for ``device_id``. Silently no-ops on
        invalid input rather than raising; heartbeats are
        fire-and-forget."""
        try:
            pct_int = int(pct)
        except (TypeError, ValueError):
            return
        pct_int = max(0, min(100, pct_int))
        ts = float(timestamp) if timestamp is not None else time.time()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO battery_history (device_id, timestamp, pct, battery_mv) "
                "VALUES (?, ?, ?, ?)",
                (device_id, ts, pct_int, battery_mv),
            )
            conn.commit()

    # -- reads ----------------------------------------------------------

    def recent(
        self,
        device_id: str,
        *,
        window_days: float = DEFAULT_WINDOW_DAYS,
        limit: int | None = None,
    ) -> list[BatteryRow]:
        """Rows for ``device_id`` within the last ``window_days``,
        ordered oldest first so callers can plot left-to-right or
        regress without a sort."""
        cutoff = time.time() - window_days * 86400.0
        sql = (
            "SELECT timestamp, pct, battery_mv FROM battery_history "
            "WHERE device_id = ? AND timestamp >= ? "
            "ORDER BY timestamp ASC"
        )
        params: tuple[Any, ...] = (device_id, cutoff)
        if limit is not None:
            sql += " LIMIT ?"
            params = (device_id, cutoff, int(limit))
        with self._conn() as conn:
            cur = conn.execute(sql, params)
            return [
                BatteryRow(
                    timestamp=float(row["timestamp"]),
                    pct=int(row["pct"]),
                    battery_mv=int(row["battery_mv"]) if row["battery_mv"] is not None else None,
                )
                for row in cur.fetchall()
            ]

    def device_ids(self) -> list[str]:
        """Distinct device ids with any history at all."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT device_id FROM battery_history GROUP BY device_id ORDER BY device_id"
            )
            return [str(row["device_id"]) for row in cur.fetchall()]

    def forget(self, device_id: str) -> None:
        """Drop every sample for a device, e.g. when the user unregisters
        the device or factory-resets it."""
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM battery_history WHERE device_id = ?", (device_id,))
            conn.commit()

    # -- predictions ----------------------------------------------------

    def predict(
        self,
        device_id: str,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> Prediction | None:
        """Robust phase-aware battery trend over the last
        ``window_days``.

        A trailing rising phase is fitted independently and reported
        through ``is_charging`` / ``charge_rate_per_day`` /
        ``days_to_full``. While charging, ``slope_per_day`` comes only
        from the preceding clean discharge phase, so a positive charge
        ramp can never surface as a drain rate. All fits use Theil-Sen
        to keep a single glitched reading or unplug relaxation drop
        from dominating the estimate."""
        all_rows = self.recent(device_id, window_days=window_days)
        if len(all_rows) < MIN_SAMPLES:
            return None

        current_pct = max(0.0, min(100.0, float(all_rows[-1].pct)))
        charging_rows = _last_charging_segment(all_rows)
        charge_slope = _theil_sen_slope(charging_rows) if charging_rows else None
        is_charging = (
            charge_slope is not None
            and charge_slope >= MIN_CHARGE_PER_DAY
            and max(r.pct for r in charging_rows) - min(r.pct for r in charging_rows)
            >= MIN_CHARGE_GAIN_PCT
        )

        if is_charging:
            assert charge_slope is not None
            prior_rows = all_rows[: len(all_rows) - len(charging_rows)]
            drain_rows = _last_discharge_segment(prior_rows)
            drain_slope = (
                _theil_sen_slope(drain_rows) if len(drain_rows) >= MIN_SEGMENT_SAMPLES else None
            )
            if drain_slope is not None and drain_slope >= -MIN_DRAIN_PER_DAY:
                drain_slope = None
            days_to_full = (
                0.0 if current_pct >= 100.0 else max(0.0, (100.0 - current_pct) / charge_slope)
            )
            return Prediction(
                device_id=device_id,
                current_pct=current_pct,
                slope_per_day=drain_slope,
                is_charging=True,
                charge_rate_per_day=charge_slope,
                days_to_20pct=None,
                days_to_empty=None,
                days_to_full=days_to_full,
                samples=len(drain_rows) if drain_slope is not None else len(charging_rows),
                window_days=window_days,
            )

        rows = _last_discharge_segment(all_rows)
        if len(rows) < MIN_SEGMENT_SAMPLES:
            # A single recent jump is not enough to establish a charge
            # phase. Preserve the older fallback for that ambiguous
            # case, but never expose a positive result as drain.
            rows = all_rows
        slope = _theil_sen_slope(rows)
        if slope is None:
            return None
        n = len(rows)

        if slope >= -MIN_DRAIN_PER_DAY:
            return Prediction(
                device_id=device_id,
                current_pct=current_pct,
                slope_per_day=min(0.0, slope),
                is_charging=False,
                charge_rate_per_day=None,
                days_to_20pct=None,
                days_to_empty=None,
                days_to_full=None,
                samples=n,
                window_days=window_days,
            )
        days_to_20pct = (current_pct - 20.0) / (-slope) if current_pct > 20.0 else 0.0
        days_to_empty = current_pct / (-slope)
        return Prediction(
            device_id=device_id,
            current_pct=current_pct,
            slope_per_day=slope,
            is_charging=False,
            charge_rate_per_day=None,
            days_to_20pct=max(0.0, days_to_20pct),
            days_to_empty=max(0.0, days_to_empty),
            days_to_full=None,
            samples=n,
            window_days=window_days,
        )


def _last_discharge_segment(rows: list[BatteryRow]) -> list[BatteryRow]:
    """Return the slice of ``rows`` starting just after the most
    recent charge event (a pct jump of ``CHARGE_JUMP_PCT`` or more
    between consecutive samples). When no such jump exists, returns
    ``rows`` unchanged. Caller decides whether the resulting slice
    is long enough to fit on."""
    last_charge_idx = -1
    for i in range(1, len(rows)):
        if rows[i].pct - rows[i - 1].pct >= CHARGE_JUMP_PCT:
            last_charge_idx = i
    if last_charge_idx <= 0:
        return rows
    return rows[last_charge_idx:]


def _last_charging_segment(rows: list[BatteryRow]) -> list[BatteryRow]:
    """Return the candidate trailing charging phase.

    A large upward step is the strongest boundary signal, and its high
    sample starts the fit so an instantaneous firmware correction does
    not inflate the charge rate. Without a step, walk backward to the
    latest trough, tolerating one point of ADC wobble. This stops a slow
    prior discharge from being absorbed into the charging fit (the old
    "break only on a two-point drop" rule could consume the whole
    previous discharge).
    """
    if len(rows) < MIN_CHARGE_SEGMENT_SAMPLES:
        return []

    for i in range(len(rows) - 1, 0, -1):
        if rows[i].pct - rows[i - 1].pct >= CHARGE_JUMP_PCT:
            segment = rows[i:]
            if len(segment) >= MIN_CHARGE_SEGMENT_SAMPLES:
                return segment

    min_pct = rows[-1].pct
    min_idx = len(rows) - 1
    for i in range(len(rows) - 2, -1, -1):
        pct = rows[i].pct
        if pct > min_pct + 1:
            break
        if pct < min_pct:
            min_pct = pct
            min_idx = i
    segment = rows[min_idx:]
    if len(segment) < MIN_CHARGE_SEGMENT_SAMPLES:
        return []
    return segment


def _theil_sen_slope(rows: list[BatteryRow]) -> float | None:
    """Median pairwise slope in percentage points per day.

    Long series are evenly subsampled to cap the quadratic pair count.
    Duplicate timestamps are skipped; ``None`` means no usable pair.
    """
    if len(rows) < 2:
        return None
    fitted_rows = rows
    if len(rows) > MAX_THEIL_SEN_SAMPLES:
        last = len(rows) - 1
        indices = {
            round(i * last / (MAX_THEIL_SEN_SAMPLES - 1)) for i in range(MAX_THEIL_SEN_SAMPLES)
        }
        fitted_rows = [rows[i] for i in sorted(indices)]

    slopes: list[float] = []
    for i, left in enumerate(fitted_rows[:-1]):
        for right in fitted_rows[i + 1 :]:
            elapsed_days = (right.timestamp - left.timestamp) / 86400.0
            if elapsed_days == 0:
                continue
            slopes.append((right.pct - left.pct) / elapsed_days)
    return float(statistics.median(slopes)) if slopes else None
