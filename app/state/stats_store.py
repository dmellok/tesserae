"""SQLite-backed daily counters for the local stats page.

The event log answers "what happened recently" and is capped at a couple
of thousand rows, so it is a rolling few days on a busy install. Anything
that spans months (how many frames this install has painted, which panel
does the most work, whether pushes fail more than they used to) has to be
aggregated as it happens or it is simply gone.

This store is that aggregate, and it is deliberately the least
interesting file on the disk: one row per (day, metric, dimension) with
an integer count. No payloads, no URLs, no page titles, no timestamps
finer than a date. Dimensions are ids the operator already knows (a
device id, an event type); names are resolved for display and never
written here, so an exported stats file is boring to read and safe to
paste into an issue.

Nothing in here is transmitted anywhere. There is no client, no upload
path, and no scheduled job that reads it; the only readers are the stats
page and the operator's own export button.

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Metric names. Kept as constants so the recorder and the page can't
# drift, and so the "what is recorded" disclosure on the page can be
# generated from the same list rather than hand-maintained.
PUSHES_BY_STATUS = "pushes.status"
PUSHES_BY_SOURCE = "pushes.source"
FRAMES_BY_DEVICE = "frames.device"
PUSH_MS_SUM = "pushes.ms_sum"
DEVICE_WAKES = "device.wakes"
ACTIVITY_BY_TYPE = "activity.type"

METRICS: tuple[tuple[str, str], ...] = (
    (PUSHES_BY_STATUS, "Pushes, counted by outcome (sent, failed, skipped)."),
    (PUSHES_BY_SOURCE, "Pushes, counted by what asked for them."),
    (FRAMES_BY_DEVICE, "Frames painted, counted by the display they went to."),
    (PUSH_MS_SUM, "Total render-to-publish milliseconds, for a daily average."),
    (DEVICE_WAKES, "Times each display checked in."),
    (ACTIVITY_BY_TYPE, "Other logged activity, counted by kind."),
)


def today(now: float | None = None) -> str:
    """The local ``YYYY-MM-DD`` bucket key.

    Local rather than UTC: the operator reading "frames today" means
    their day, and an install that paints a dashboard at 11pm should not
    see it land on tomorrow's bar."""
    return time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))


def days_back(count: int, now: float | None = None) -> list[str]:
    """The last ``count`` day keys, oldest first, including today.

    Built by walking days rather than subtracting 86400 from a timestamp
    so a DST change doesn't drop or duplicate a bucket."""
    base = now if now is not None else time.time()
    out: list[str] = []
    for offset in range(count - 1, -1, -1):
        out.append(today(base - offset * 86_400))
    return out


class StatsStore:
    """Thread-safe daily counters. Same file-per-concern convention as
    the event log and battery history, at ``data/core/stats.db``."""

    SCHEMA: str = """
    CREATE TABLE IF NOT EXISTS daily (
        day    TEXT NOT NULL,
        metric TEXT NOT NULL,
        dim    TEXT NOT NULL DEFAULT '',
        value  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, metric, dim)
    );
    CREATE INDEX IF NOT EXISTS idx_daily_metric ON daily(metric, day);
    CREATE TABLE IF NOT EXISTS meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._init_db()

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(self.SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('since', ?)",
                (today(),),
            )

    # -- writing --------------------------------------------------------

    def bump(self, metric: str, dim: str = "", n: int = 1, *, day: str | None = None) -> None:
        """Add ``n`` to one counter. Silent no-op while collection is
        paused, and never raises: a stats write must not be able to break
        a render, so callers can use it without a guard of their own."""
        if n == 0:
            return
        try:
            if self.paused():
                return
            with self._lock, self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO daily (day, metric, dim, value) VALUES (?, ?, ?, ?)
                    ON CONFLICT(day, metric, dim) DO UPDATE SET value = value + excluded.value
                    """,
                    (day or today(), metric, dim, int(n)),
                )
        except Exception:
            logger.debug("stats: bump failed for %s/%s", metric, dim, exc_info=True)

    # -- reading --------------------------------------------------------

    def series(self, metric: str, *, days: int) -> dict[str, dict[str, int]]:
        """``{day: {dim: value}}`` over the last ``days`` days, with a
        (possibly empty) entry for every day in the window so a chart
        gets a continuous axis without filling gaps itself."""
        window = days_back(days)
        out: dict[str, dict[str, int]] = {day: {} for day in window}
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(
                    "SELECT day, dim, value FROM daily WHERE metric = ? AND day >= ?",
                    (metric, window[0]),
                ).fetchall()
        except Exception:
            logger.debug("stats: series failed for %s", metric, exc_info=True)
            return out
        for row in rows:
            if row["day"] in out:
                out[row["day"]][row["dim"]] = int(row["value"])
        return out

    def by_dim(self, metric: str, *, days: int | None = None) -> dict[str, int]:
        """``{dim: total}`` over the window, or all time when ``days`` is
        None."""
        sql = "SELECT dim, SUM(value) AS total FROM daily WHERE metric = ?"
        params: list[Any] = [metric]
        if days is not None:
            sql += " AND day >= ?"
            params.append(days_back(days)[0])
        sql += " GROUP BY dim"
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception:
            logger.debug("stats: by_dim failed for %s", metric, exc_info=True)
            return {}
        return {str(row["dim"]): int(row["total"] or 0) for row in rows}

    def total(self, metric: str, *, dim: str | None = None, days: int | None = None) -> int:
        """One number: the metric summed over the window (all time by
        default), optionally for a single dimension."""
        sql = "SELECT SUM(value) AS total FROM daily WHERE metric = ?"
        params: list[Any] = [metric]
        if dim is not None:
            sql += " AND dim = ?"
            params.append(dim)
        if days is not None:
            sql += " AND day >= ?"
            params.append(days_back(days)[0])
        try:
            with self._lock, self._conn() as conn:
                row = conn.execute(sql, params).fetchone()
        except Exception:
            logger.debug("stats: total failed for %s", metric, exc_info=True)
            return 0
        return int(row["total"] or 0) if row else 0

    # -- operator controls ----------------------------------------------

    def since(self) -> str:
        """The day counting started, so the page can say how much of a
        window the numbers actually cover."""
        return self._meta("since") or today()

    def paused(self) -> bool:
        return self._meta("paused") == "1"

    def set_paused(self, paused: bool) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('paused', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("1" if paused else "0",),
            )

    def _meta(self, key: str) -> str:
        try:
            with self._lock, self._conn() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except Exception:
            return ""
        return str(row["value"]) if row else ""

    def export(self) -> dict[str, Any]:
        """Everything this store holds, as plain JSON. This is the whole
        file: if it looks dull, that's the point."""
        try:
            with self._lock, self._conn() as conn:
                rows = conn.execute(
                    "SELECT day, metric, dim, value FROM daily ORDER BY day, metric, dim"
                ).fetchall()
        except Exception:
            logger.debug("stats: export failed", exc_info=True)
            rows = []
        return {
            "schema": 1,
            "since": self.since(),
            "exported_at": today(),
            "metrics": {name: description for name, description in METRICS},
            "daily": [
                {
                    "day": row["day"],
                    "metric": row["metric"],
                    "dim": row["dim"],
                    "value": int(row["value"]),
                }
                for row in rows
            ],
        }

    def delete_all(self) -> int:
        """Drop every counter and restart the "collecting since" clock.
        Returns the number of rows removed."""
        with self._lock, self._conn() as conn:
            count = int(conn.execute("SELECT COUNT(*) AS n FROM daily").fetchone()["n"])
            conn.execute("DELETE FROM daily")
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('since', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (today(),),
            )
        return count
