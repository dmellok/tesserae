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

The prediction is a plain linear regression of percentage vs unix
time over the most recent ``window_days`` of samples. ``slope_per_day``
is a negative number for a draining battery; we project to the 20%
and 0% intercepts. We bail out (return ``None``) when:

* there are fewer than ``MIN_SAMPLES`` points in the window, OR
* the slope is non-negative (battery flat or charging), OR
* the slope is so shallow the projection would exceed 10 years (the
  battery's probably plugged in).
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
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


@dataclass(frozen=True)
class Prediction:
    """One-shot regression result for a device's last N days.

    ``slope_per_day`` is negative on a draining battery (e.g. -3.2
    means it loses 3.2 % per day) and positive when charging.
    ``days_to_20pct`` / ``days_to_empty`` are projections from
    ``current_pct``; both ``None`` when the battery's flat or rising.
    ``days_to_full`` is the inverse: present only when the recent
    samples show sustained charging (positive slope on the latest
    charging segment); ``None`` when flat or discharging.
    """

    device_id: str
    current_pct: float
    slope_per_day: float
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
        """Linear regression of percent vs time over the last
        ``window_days``. ``None`` when there isn't enough data; a
        Prediction with ``days_to_*=None`` when the battery's flat
        or charging.

        Critically, if the window contains a charge event (a jump
        of ``CHARGE_JUMP_PCT`` or more between consecutive samples),
        the regression fits ONLY the most recent discharge segment
        — otherwise the upward jump dominates the linear fit and we
        end up projecting a non-draining battery on a fleet that's
        actually draining between charges. Falls back to the full
        window when the latest segment is too short to be useful."""
        all_rows = self.recent(device_id, window_days=window_days)
        if len(all_rows) < MIN_SAMPLES:
            return None
        rows = _last_discharge_segment(all_rows)
        if len(rows) < MIN_SEGMENT_SAMPLES:
            # Either no charge event in the window, or the segment
            # after the last charge is too short for a meaningful
            # fit. Fall back to the full window so a long flat or
            # gently-draining battery still gets a slope reading.
            # ``all_rows`` already cleared MIN_SAMPLES above, so we
            # don't re-check here (segments intentionally accept
            # fewer points than the full window).
            rows = all_rows
        # x is days since the oldest sample so the regression's
        # numerical conditioning is reasonable.
        t0 = rows[0].timestamp
        xs = [(r.timestamp - t0) / 86400.0 for r in rows]
        ys = [float(r.pct) for r in rows]
        n = len(xs)
        sum_x = sum(xs)
        sum_y = sum(ys)
        mean_x = sum_x / n
        mean_y = sum_y / n
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
        den = sum((x - mean_x) ** 2 for x in xs)
        if den == 0:
            return None
        slope = num / den  # %-points per day
        intercept = mean_y - slope * mean_x
        # Current pct: take the LAST sample of the full series, not
        # the segment, so the projection projects from where the
        # device actually is right now (the segment may end on an
        # older sample if we fell back).
        current_pct = max(0.0, min(100.0, float(all_rows[-1].pct)))

        if slope >= -MIN_DRAIN_PER_DAY:
            # Flat or charging. Fit a separate regression to just the
            # latest charging segment so a half-window-old discharge
            # doesn't drag the slope below the charging threshold.
            # When the segment fit shows sustained positive slope we
            # produce a days_to_full projection; otherwise it's a
            # genuinely flat battery and only the slope reading goes
            # back.
            days_to_full = _maybe_days_to_full(all_rows, current_pct)
            return Prediction(
                device_id=device_id,
                current_pct=current_pct,
                slope_per_day=slope,
                days_to_20pct=None,
                days_to_empty=None,
                days_to_full=days_to_full,
                samples=n,
                window_days=window_days,
            )
        del intercept  # not exposed; kept for clarity if we add a chart trendline
        days_to_20pct = (current_pct - 20.0) / (-slope) if current_pct > 20.0 else 0.0
        days_to_empty = current_pct / (-slope)
        return Prediction(
            device_id=device_id,
            current_pct=current_pct,
            slope_per_day=slope,
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
    """Return the trailing slice of ``rows`` where the percent is
    monotonically non-decreasing across at least
    ``MIN_CHARGE_SEGMENT_SAMPLES`` consecutive samples ending at the
    last row.

    Walks back from the tail until the first sample that DROPS, which
    marks the boundary between "the last discharge" and "the charge
    currently in progress". Wobble of one percent point is allowed
    within the segment (firmware ADC noise; a 4.16 V cell can read as
    99 / 100 / 99 / 100 over four consecutive heartbeats), so we look
    for drops of more than one percent rather than strict monotonicity.
    Returns an empty list when the latest segment is too short to fit
    on; the caller treats that as "not currently charging"."""
    if not rows:
        return []
    end = len(rows) - 1
    start = end
    for i in range(end - 1, -1, -1):
        # A drop of more than one percent breaks the segment; this
        # cell IS discharging (or at best flat) over this gap.
        if rows[i + 1].pct - rows[i].pct < -1:
            break
        start = i
    segment = rows[start : end + 1]
    if len(segment) < MIN_CHARGE_SEGMENT_SAMPLES:
        return []
    return segment


def _maybe_days_to_full(rows: list[BatteryRow], current_pct: float) -> float | None:
    """Return the projected days until ``current_pct`` reaches 100%,
    or ``None`` when the latest samples don't show sustained charging.

    Uses :func:`_last_charging_segment` to isolate the trailing
    charging slice, then fits the same simple linear regression as
    ``predict()`` on that subset. Two reasons to gate this beyond the
    main slope:

    * The main slope is fit to the most recent DISCHARGE segment for
      the days-to-empty projection; it's the wrong signal for the
      charge case (it can be slightly negative even while a charge is
      mid-progress).
    * A genuinely flat battery shouldn't get a "Full in ∞" indicator;
      requiring ``MIN_CHARGE_PER_DAY`` of charging slope filters that
      out cleanly.

    Returns 0 (not None) when the battery is already at or above 100%
    so the UI can render "Full now" rather than hiding the indicator.
    """
    if current_pct >= 100.0:
        return 0.0
    segment = _last_charging_segment(rows)
    if not segment:
        return None
    t0 = segment[0].timestamp
    xs = [(r.timestamp - t0) / 86400.0 for r in segment]
    ys = [float(r.pct) for r in segment]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    slope = num / den
    if slope < MIN_CHARGE_PER_DAY:
        return None
    return max(0.0, (100.0 - current_pct) / slope)
