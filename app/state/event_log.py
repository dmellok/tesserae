"""SQLite-backed event log.

A single ``events`` table records anything worth surfacing in the admin
timeline: push attempts (this milestone), and, in M8, renderer events,
device heartbeats, scheduler ticks. Designed for that future generalisation
from day one: the row shape is generic so M8 only adds new ``type`` values
rather than reshaping the schema.

The log is append-mostly. Deletes happen only via explicit user action
("forget this row") and from the cap-evictor (oldest beyond ``cap`` rows
get removed on insert).

mypy --strict applies to this module, see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventRow:
    """One event. ``extra`` is the kind-specific payload dict.

    Common columns:
      type:      event kind, currently only ``"push"``; M8 adds more
      source:    who triggered it (``page``, ``file``, ``url``, ``webpage``,
                 ``scheduler``, ``webhook``, ``home_assistant``, ``manual``,
                 ``resend``)
      target:    what was pushed (page id, source label, URL)
      status:    ``sent`` | ``failed`` | ``busy`` | ``not_found``
      digest:    composition PNG digest (used as thumbnail reference);
                 None if no PNG was produced
      error:     short error message; None on success
      duration_s:render-to-publish wall time
    """

    id: int
    type: str
    timestamp: float
    source: str
    target: str
    status: str
    digest: str | None
    error: str | None
    duration_s: float
    extra: dict[str, Any]


class EventLog:
    """Thread-safe SQLite event store.

    SQLite is plenty for a single-process admin tool, pages.json and
    settings.json are JSON because they're hand-editable; the event log
    isn't, so the slightly faster (and indexable) SQLite wins.
    """

    SCHEMA: str = """
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        type        TEXT    NOT NULL,
        timestamp   REAL    NOT NULL,
        source      TEXT    NOT NULL,
        target      TEXT    NOT NULL,
        status      TEXT    NOT NULL,
        digest      TEXT,
        error       TEXT,
        duration_s  REAL    NOT NULL DEFAULT 0,
        extra_json  TEXT    NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS events_by_time ON events (timestamp DESC);
    CREATE INDEX IF NOT EXISTS events_by_digest ON events (digest);
    CREATE INDEX IF NOT EXISTS events_by_type_time ON events (type, timestamp DESC);
    """

    def __init__(self, path: Path, *, cap: int = 500, device_cap: int | None = None) -> None:
        self._path = path
        self._cap = cap
        # High-volume device heartbeats get their own sub-cap so they can't
        # crowd push / scheduler / auth history out of the global ``cap``.
        self._device_cap = device_cap
        self._lock = threading.Lock()
        # Per-row listeners, used by the SSE /events/stream endpoint.
        # Fired after each successful insert, outside the DB lock.
        self._listener_lock = threading.Lock()
        self._listeners: list[Callable[[EventRow], None]] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    # -- listeners ------------------------------------------------------

    def add_listener(self, callback: Callable[[EventRow], None]) -> None:
        with self._listener_lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[EventRow], None]) -> None:
        with self._listener_lock, contextlib.suppress(ValueError):
            self._listeners.remove(callback)

    def _fire(self, row: EventRow) -> None:
        with self._listener_lock:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(row)
            except Exception:
                logger.exception("event log listener %r raised", cb)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Open + close a SQLite connection per call. sqlite3 connections
        used as context managers commit/rollback but do NOT close, so
        we'd leak file descriptors. ``check_same_thread=False`` lets the
        scheduler / MQTT dispatcher threads share the same EventLog
        instance, the lock around this helper serialises access."""
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # -- writes -----------------------------------------------------------

    def record(
        self,
        *,
        type: str,
        source: str,
        target: str,
        status: str,
        digest: str | None = None,
        error: str | None = None,
        duration_s: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """Append a row, evict the oldest if over cap, return the new id."""
        timestamp = time.time()
        extra_dict = dict(extra or {})
        payload = json.dumps(extra_dict, default=str)
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO events "
                "(type, timestamp, source, target, status, digest, error, duration_s, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    type,
                    timestamp,
                    source,
                    target,
                    status,
                    digest,
                    error,
                    duration_s,
                    payload,
                ),
            )
            new_id = int(cur.lastrowid or 0)
            self._evict(conn)
            conn.commit()
        # Fire listeners outside the DB lock so a slow subscriber can't
        # back-pressure the writer.
        self._fire(
            EventRow(
                id=new_id,
                type=type,
                timestamp=timestamp,
                source=source,
                target=target,
                status=status,
                digest=digest,
                error=error,
                duration_s=duration_s,
                extra=extra_dict,
            )
        )
        return new_id

    def _evict(self, conn: sqlite3.Connection) -> None:
        """Cap-based eviction. We over-delete in one shot rather than
        one-per-insert so a backlog (e.g. after a long offline window)
        recovers in a single transaction."""
        # First bound the high-volume 'device' type on its own, so a flood
        # of heartbeats evicts old heartbeats rather than push history.
        if self._device_cap is not None:
            dev_count = int(
                conn.execute("SELECT COUNT(*) FROM events WHERE type = 'device'").fetchone()[0]
            )
            if dev_count > self._device_cap:
                conn.execute(
                    "DELETE FROM events WHERE id IN "
                    "(SELECT id FROM events WHERE type = 'device' ORDER BY id ASC LIMIT ?)",
                    (dev_count - self._device_cap,),
                )
        cur = conn.execute("SELECT COUNT(*) FROM events")
        count = int(cur.fetchone()[0])
        if count <= self._cap:
            return
        excess = count - self._cap
        conn.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id ASC LIMIT ?)",
            (excess,),
        )

    def delete(self, event_id: int) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.commit()
            return cur.rowcount > 0

    def delete_all(self) -> int:
        """Delete every event. Returns the number removed. The caller is
        responsible for pruning any now-orphaned render artifacts."""
        with self._lock, self._conn() as conn:
            removed = int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
            conn.execute("DELETE FROM events")
            conn.commit()
            return removed

    def delete_older_than(self, cutoff: float) -> int:
        """Delete every event with a timestamp strictly older than ``cutoff``
        (epoch seconds). Returns the number removed."""
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            conn.commit()
            return int(cur.rowcount)

    def delete_many(self, event_ids: list[int]) -> int:
        """Delete the given event ids in one transaction. Returns the number
        removed. The caller prunes any now-orphaned render artifacts."""
        ids = [int(i) for i in event_ids]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        with self._lock, self._conn() as conn:
            cur = conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", ids)
            conn.commit()
            return int(cur.rowcount)

    def delete_by_type_target(self, *, type: str, target: str) -> int:
        """Delete every event of ``type`` for ``target``. Returns the number
        removed. The per-device touch monitor's Clear uses this: the monitor
        seeds from touch history on load, so a view-only clear reappears on
        refresh; removing the rows makes the clear stick."""
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM events WHERE type = ? AND target = ?", (type, target))
            conn.commit()
            return int(cur.rowcount)

    def digest_in_use(self, digest: str) -> bool:
        """Used by the artifact GC: don't delete a thumbnail PNG if other
        history rows still reference it."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM events WHERE digest = ? LIMIT 1", (digest,)
            ).fetchone()
        return row is not None

    def referenced_digests(self) -> set[str]:
        """Every digest still referenced by some event. The render GC keeps
        only artifacts whose filename digest is in this set."""
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT digest FROM events WHERE digest IS NOT NULL AND digest != ''"
            ).fetchall()
        return {str(r[0]) for r in rows}

    # -- reads ------------------------------------------------------------

    def get(self, event_id: int) -> EventRow | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def list(
        self,
        *,
        type: str | None = None,
        source: str | None = None,
        exclude_statuses: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[EventRow]:
        sql = "SELECT * FROM events"
        clauses: list[str] = []
        params: list[Any] = []
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if exclude_statuses:
            placeholders = ",".join("?" * len(exclude_statuses))
            clauses.append(f"status NOT IN ({placeholders})")
            params.extend(exclude_statuses)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_event(r) for r in rows]

    def last_event_by_target(
        self,
        *,
        type: str,
        targets: Sequence[str] | None = None,
        statuses: tuple[str, ...] = ("sent", "ok"),
    ) -> dict[str, float]:
        """Most-recent timestamp per ``target`` for rows matching the
        given ``type`` (and optional ``statuses``). Used by the
        dashboards list to surface "last pushed at" per saved page
        (one SQL roundtrip instead of N).

        Returns a ``{target: timestamp}`` dict; targets with no
        matching row are absent from the result."""
        sql = "SELECT target, MAX(timestamp) AS ts FROM events WHERE type = ? AND target != '' "
        params: list[Any] = [type]
        if statuses:
            placeholders = ",".join("?" * len(statuses))
            sql += f" AND status IN ({placeholders}) "
            params.extend(statuses)
        if targets is not None:
            if not targets:
                return {}
            placeholders = ",".join("?" * len(targets))
            sql += f" AND target IN ({placeholders}) "
            params.extend(targets)
        sql += " GROUP BY target"
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(r["target"]): float(r["ts"]) for r in rows}

    def count(self, *, type: str | None = None) -> int:
        if type is None:
            sql = "SELECT COUNT(*) FROM events"
            params: tuple[Any, ...] = ()
        else:
            sql = "SELECT COUNT(*) FROM events WHERE type = ?"
            params = (type,)
        with self._lock, self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0])

    def source_counts(self, *, type: str | None = None) -> dict[str, int]:
        """Return ``{source: count}`` for events matching ``type`` (or all
        types when ``type`` is None). Used by the history page to render
        filter chips with live counts beside each label."""
        if type is None:
            sql = "SELECT source, COUNT(*) FROM events GROUP BY source"
            params: tuple[Any, ...] = ()
        else:
            sql = "SELECT source, COUNT(*) FROM events WHERE type = ? GROUP BY source"
            params = (type,)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}


def _row_to_event(row: sqlite3.Row) -> EventRow:
    extra_raw = row["extra_json"] or "{}"
    try:
        extra = json.loads(extra_raw)
        if not isinstance(extra, dict):
            extra = {"raw": extra}
    except json.JSONDecodeError:
        extra = {"raw": extra_raw}
    return EventRow(
        id=int(row["id"]),
        type=str(row["type"]),
        timestamp=float(row["timestamp"]),
        source=str(row["source"]),
        target=str(row["target"]),
        status=str(row["status"]),
        digest=row["digest"],
        error=row["error"],
        duration_s=float(row["duration_s"]),
        extra=extra,
    )
