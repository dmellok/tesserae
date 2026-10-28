"""How the comics retry a source that failed, and clean up old pictures.

The widget downloads pictures in the background and keeps them on disk
(images.py). A source that is down behaves the same whichever it is:

* **Retry with a backoff.** After a failed try the source is asked again
  30 minutes later, then after 1, 2, 4 hours, never more than 6 hours
  apart, and at most ``TRIES_PER_DAY`` times a day; the next day starts
  over. A forced fetch (Fetch now, a refresh) ignores the wait. Every try
  is logged.
* **Reuse the last good picture.** A failed try never replaces what is on
  disk, so the last good picture stays up (with the small "not updated"
  note) until a try succeeds; the new one replaces it at once.
* **Clean up, gently.** A picture nothing refers to any more (a past
  edition, a removed strip, a replaced upload) is deleted once it has gone
  unused for ``GRACE_S``, so a render that read the old state can still
  load it. The folder is kept under a size budget; past it the oldest
  unreferenced pictures go first. A picture still referenced (the one on
  screen) is never deleted.

Only the standard library: server.py and images.py load it beside them.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

RETRY_FIRST_S = 30 * 60
RETRY_MAX_S = 6 * 60 * 60
TRIES_PER_DAY = 6
GRACE_S = 60 * 60
# Even over budget, a file this fresh stays: a render may be loading it.
MIN_AGE_S = 5 * 60


def retry_wait(failures: int, *, first: float = RETRY_FIRST_S, cap: float = RETRY_MAX_S) -> float:
    """Seconds to wait after the ``failures``-th failed try in a row (1, 2,
    ...): ``first`` doubling each time, at most ``cap``."""
    return float(min(first * 2 ** min(max(failures, 1) - 1, 16), cap))


def failed(
    record: dict[str, Any] | None,
    today: str,
    now: float,
    *,
    first: float = RETRY_FIRST_S,
    cap: float = RETRY_MAX_S,
) -> dict[str, Any]:
    """A source's retry record after a failed try: today's count of failed
    tries (a new day starts at one) and when it may be tried again."""
    tries = 1
    if isinstance(record, dict) and record.get("day") == today:
        count = record.get("tries")
        tries = (count if isinstance(count, int) and not isinstance(count, bool) else 0) + 1
    return {"day": today, "tries": tries, "after": now + retry_wait(tries, first=first, cap=cap)}


def may_try(
    record: dict[str, Any] | None,
    today: str,
    now: float,
    *,
    force: bool = False,
    tries_per_day: int = TRIES_PER_DAY,
) -> bool:
    """Whether a source with retry ``record`` may be tried now. No record
    (or one from another day) may; ``force`` always may."""
    if force or not isinstance(record, dict) or record.get("day") != today:
        return True
    tries, after = record.get("tries"), record.get("after")
    if not isinstance(tries, int) or isinstance(tries, bool):
        return True
    if tries >= tries_per_day:
        return False
    return not isinstance(after, (int, float)) or now >= after


def describe(
    record: dict[str, Any] | None, today: str, now: float, *, tries_per_day: int = TRIES_PER_DAY
) -> str:
    """The retry state in words for a log line or a page: "next try in 30
    min (try 2 of 6)", "no more tries today", or ""."""
    if not isinstance(record, dict) or record.get("day") != today:
        return ""
    tries = record.get("tries")
    if not isinstance(tries, int) or isinstance(tries, bool):
        return ""
    if tries >= tries_per_day:
        return "no more tries today"
    after = record.get("after")
    wait = max(0.0, float(after) - now) if isinstance(after, (int, float)) else 0.0
    minutes = round(wait / 60)
    span = f"{minutes} min" if minutes < 60 else f"{minutes / 60:.1f} h".replace(".0 h", " h")
    return f"next try in {span} (try {tries + 1} of {tries_per_day})"


def _read_ledger(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): float(v)
        for k, v in data.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def retire_reset(ledger: Path, name: str) -> None:
    """Forget that ``name`` was unused: a writer about to point at it again
    (a content-addressed picture that came back) calls this, under the
    same lock as its cleanup, so the grace starts over."""
    entries = _read_ledger(ledger)
    if entries.pop(name, None) is not None:
        _write_ledger(ledger, entries)


def _write_ledger(path: Path, entries: dict[str, float]) -> None:
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(entries), encoding="utf-8")
        os.replace(tmp, path)


def sweep(
    directory: Path,
    *,
    keep: Iterable[str],
    max_bytes: int,
    grace_s: float = GRACE_S,
    now: float | None = None,
    pattern: str = "*",
    ledger: Path | None = None,
    still_unused: Callable[[str], bool] | None = None,
) -> list[str]:
    """Delete the files in ``directory`` matching ``pattern`` that aren't in
    ``keep`` (names) once unused for ``grace_s``; then, while the folder is
    over ``max_bytes``, the oldest such files unused for at least
    ``MIN_AGE_S``. Files in ``keep`` are never deleted. Returns the names
    deleted.

    "Unused since" is when a file was first seen out of ``keep``, kept in
    ``ledger`` (a JSON file) when one is given: the grace starts when a
    picture is replaced, not when it was written, so a render that read the
    old state can still load it. Without a ledger it is the file's mtime
    (for a widget that touches a file each time it is used).
    ``still_unused(name)``, when given, is asked right before each delete,
    so a caller can check its references once more. Callers that change
    references concurrently hold one lock around both."""
    now = time.time() if now is None else now
    kept = set(keep)
    entries = _read_ledger(ledger) if ledger is not None else {}
    files: list[tuple[float, int, Path]] = []
    total = 0
    seen: set[str] = set()
    with contextlib.suppress(OSError):
        for path in directory.glob(pattern):
            with contextlib.suppress(OSError):
                if not path.is_file() or (ledger is not None and path == ledger):
                    continue
                stat = path.stat()
                total += stat.st_size
                if path.name in kept:
                    continue
                seen.add(path.name)
                since = entries.setdefault(path.name, now) if ledger is not None else stat.st_mtime
                files.append((since, stat.st_size, path))
    files.sort(key=lambda item: item[0])
    gone: list[str] = []
    for since, size, path in files:
        unused = now - since
        if not (unused > grace_s or (total > max_bytes and unused > MIN_AGE_S)):
            continue
        if still_unused is not None and not still_unused(path.name):
            entries.pop(path.name, None)
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            total -= size
            gone.append(path.name)
    if ledger is not None:
        _write_ledger(ledger, {k: v for k, v in entries.items() if k in seen and k not in gone})
    return gone
