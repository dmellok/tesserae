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

logger = logging.getLogger(__name__)

# A 7-day window means the slope reflects the user's current
# sleep-interval setting, not a long-ago tweak.
DEFAULT_WINDOW_DAYS: int = 7
MIN_SAMPLES: int = 8
# Sane upper bound. A slope of <0.01 %/day implies "this is plugged
# in"; we return None rather than predicting in years.
MIN_DRAIN_PER_DAY: float = 0.01


@dataclass(frozen=True)
class Prediction:
    """One-shot regression result for a device's last N days.

    ``slope_per_day`` is negative on a draining battery (e.g. -3.2
    means it loses 3.2 % per day). ``days_to_20pct`` / ``days_to_empty``
    are projections from the current_pct; both ``None`` when the
    battery's flat or rising.
    """

    device_id: str
    current_pct: float
    slope_per_day: float
    days_to_20pct: float | None
    days_to_empty: float | None
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
        params: tuple = (device_id, cutoff)
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
        ``window_days``. ``None`` when there isn't enough data or the
        battery's flat / charging."""
        rows = self.recent(device_id, window_days=window_days)
        if len(rows) < MIN_SAMPLES:
            return None
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
        current_pct = max(0.0, min(100.0, ys[-1]))

        if slope >= -MIN_DRAIN_PER_DAY:
            # Flat or charging. Return the slope so the caller can show
            # "+0.5%/day" if it wants, but no projection.
            return Prediction(
                device_id=device_id,
                current_pct=current_pct,
                slope_per_day=slope,
                days_to_20pct=None,
                days_to_empty=None,
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
            samples=n,
            window_days=window_days,
        )
