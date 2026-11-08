"""In-memory ring of recent server log records, for Settings → Logs.

A ``logging.Handler`` on the root logger keeps the newest records in a
bounded deque, each with a monotonic sequence number so the page can ask
for "everything after N" and an SSE stream can push new lines as they're
written. Nothing touches disk: this is the same output the process writes
to stderr, kept a little longer and made browsable.

Two levels are involved, and the page shows both:

* the **watch level** is a view filter, the minimum severity the page
  lists. It never changes what the server records.
* the **record level** is the root logger's level, i.e. what the process
  emits at all. Watching debug lines needs it at DEBUG. The page can
  change it for the running process; the lasting setting stays where it
  was (``--log-level``, ``TESSERAE_LOG_LEVEL``, the HA add-on option), so
  a restart puts it back.

The root logger is process-global, so the handler is too: ``install``
attaches one shared instance and is idempotent across the many apps a
test session creates.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

CAPACITY = 2000

LEVELS: tuple[tuple[str, int], ...] = (
    ("debug", logging.DEBUG),
    ("info", logging.INFO),
    ("warning", logging.WARNING),
    ("error", logging.ERROR),
    ("critical", logging.CRITICAL),
)
_BY_NAME = dict(LEVELS)

# Credentials that can legitimately appear in a log line (a device's
# ?k=<token> frame URL, an Authorization header echoed by a library) are
# masked before the line is kept, so the page never shows more than a
# terminal should. stderr itself is untouched.
_REDACT = (
    (re.compile(r"([?&](?:k|token|access_token|sig)=)[^&\s\"']+", re.I), r"\1…"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1…"),
    (
        re.compile(r"(X-(?:Tesserae-Token|Pairing-Code)['\"]?\s*[:=]\s*['\"]?)[^\s,'\"}]+", re.I),
        r"\1…",
    ),
)


def parse_level(name: object) -> int | None:
    """``"warning"`` / ``"WARNING"`` / ``30`` -> 30; anything else None."""
    if isinstance(name, int) and not isinstance(name, bool):
        return name if name in _BY_NAME.values() else None
    if isinstance(name, str):
        key = name.strip().lower()
        if key == "warn":
            key = "warning"
        return _BY_NAME.get(key)
    return None


def level_name(level: int) -> str:
    for name, value in LEVELS:
        if level <= value:
            return name
    return "critical"


def redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


@dataclass(frozen=True)
class LogLine:
    seq: int
    ts: float
    level: str
    levelno: int
    logger: str
    message: str
    exc: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "level": self.level,
            "levelno": self.levelno,
            "logger": self.logger,
            "message": self.message,
            "exc": self.exc,
        }


class LogBuffer(logging.Handler):
    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__(level=logging.NOTSET)
        self._lines: deque[LogLine] = deque(maxlen=capacity)
        self._seq = 0
        self._lock_ = threading.Lock()
        self._listeners: list[Callable[[LogLine], None]] = []
        self.started = time.time()

    # logging.Handler -------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = redact(record.getMessage())
            exc = None
            if record.exc_info:
                exc = redact(logging.Formatter().formatException(record.exc_info))
            elif record.exc_text:
                exc = redact(record.exc_text)
        except Exception:  # a bad %-format must never break the caller
            message, exc = redact(str(record.msg)), None
        with self._lock_:
            self._seq += 1
            line = LogLine(
                seq=self._seq,
                ts=record.created,
                level=level_name(record.levelno),
                levelno=record.levelno,
                logger=record.name,
                message=message,
                exc=exc,
            )
            self._lines.append(line)
            listeners = list(self._listeners)
        for fn in listeners:
            # A dead subscriber can't stop logging.
            with contextlib.suppress(Exception):
                fn(line)

    # reads -----------------------------------------------------------

    def lines(
        self,
        *,
        min_level: int = logging.NOTSET,
        after: int = 0,
        limit: int = CAPACITY,
        query: str | None = None,
    ) -> list[LogLine]:
        """Oldest first, at or above ``min_level``, newer than ``after``,
        the newest ``limit`` of them."""
        with self._lock_:
            snapshot = list(self._lines)
        q = (query or "").strip().lower()
        out = [
            ln
            for ln in snapshot
            if ln.seq > after
            and ln.levelno >= min_level
            and (not q or q in ln.message.lower() or q in ln.logger.lower())
        ]
        return out[-limit:] if limit else out

    def counts(self) -> dict[str, int]:
        with self._lock_:
            snapshot = list(self._lines)
        out = {name: 0 for name, _ in LEVELS}
        for ln in snapshot:
            out[ln.level] = out.get(ln.level, 0) + 1
        return out

    @property
    def capacity(self) -> int:
        return self._lines.maxlen or 0

    @property
    def last_seq(self) -> int:
        return self._seq

    def clear(self) -> None:
        with self._lock_:
            self._lines.clear()

    def add_listener(self, fn: Callable[[LogLine], None]) -> None:
        with self._lock_:
            self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[LogLine], None]) -> None:
        with self._lock_, contextlib.suppress(ValueError):
            self._listeners.remove(fn)


_SHARED: LogBuffer | None = None
_SHARED_LOCK = threading.Lock()


def install() -> LogBuffer:
    """Attach the shared buffer to the root logger (once) and return it."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = LogBuffer()
        root = logging.getLogger()
        if _SHARED not in root.handlers:
            root.addHandler(_SHARED)
        return _SHARED


def record_level() -> int:
    """What the process currently emits (the root logger's level)."""
    return logging.getLogger().getEffectiveLevel()


def set_record_level(level: int) -> None:
    """Change the root level for this process. Handlers that carry their
    own level (stderr's is NOTSET under basicConfig) keep it."""
    logging.getLogger().setLevel(level)
