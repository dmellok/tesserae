"""SQLite-backed event log.

A single ``events`` table records anything worth surfacing in the admin
timeline: push attempts (this milestone), and — in M8 — renderer events,
device heartbeats, scheduler ticks. Designed for that future generalisation
from day one: the row shape is generic so M8 only adds new ``type`` values
rather than reshaping the schema.

The log is append-mostly. Deletes happen only via explicit user action
("forget this row") and from the cap-evictor (oldest beyond ``cap`` rows
get removed on insert).

mypy --strict applies to this module — see pyproject.toml.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EventRow:
    """One event. ``extra`` is the kind-specific payload dict.

    Common columns:
      type:      event kind — currently only ``"push"``; M8 adds more
      source:    who triggered it (``page``, ``file``, ``url``, ``webpage``,
                 ``scheduler``, ``manual``, ``resend``)
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

    SQLite is plenty for a single-process admin tool — pages.json and
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

    def __init__(self, path: Path, *, cap: int = 500) -> None:
        self._path = path
        self._cap = cap
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """Open + close a SQLite connection per call. sqlite3 connections
        used as context managers commit/rollback but do NOT close, so
        we'd leak file descriptors. ``check_same_thread=False`` lets the
        scheduler / MQTT dispatcher threads share the same EventLog
        instance — the lock around this helper serialises access."""
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
        payload = json.dumps(extra or {}, default=str)
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO events "
                "(type, timestamp, source, target, status, digest, error, duration_s, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    type,
                    time.time(),
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
            return new_id

    def _evict(self, conn: sqlite3.Connection) -> None:
        """Cap-based eviction. We over-delete in one shot rather than
        one-per-insert so a backlog (e.g. after a long offline window)
        recovers in a single transaction."""
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

    def digest_in_use(self, digest: str) -> bool:
        """Used by the artifact GC: don't delete a thumbnail PNG if other
        history rows still reference it."""
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM events WHERE digest = ? LIMIT 1", (digest,)
            ).fetchone()
        return row is not None

    # -- reads ------------------------------------------------------------

    def get(self, event_id: int) -> EventRow | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def list(
        self,
        *,
        type: str | None = None,
        limit: int = 100,
    ) -> list[EventRow]:
        sql = "SELECT * FROM events"
        params: list[Any] = []
        if type is not None:
            sql += " WHERE type = ?"
            params.append(type)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_event(r) for r in rows]

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
