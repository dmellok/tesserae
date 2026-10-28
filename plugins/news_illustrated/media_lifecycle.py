"""How this widget retries a day's edition and cleans up its pictures.

* **Retry with a backoff.** After a failed try the next waits 30 minutes,
  then 1, 2, 4 hours, never more than 6 hours apart (:func:`retry_wait`).
  pipeline.py caps how many tries a day gets.
* **Clean up, gently.** A picture nothing refers to any more (a replaced
  cartoon, a removed feed's) is deleted once it has gone unused for
  ``GRACE_S``, so a render that read the old stories can still load it. The
  folder is kept under a size budget; past it the oldest unreferenced
  pictures go first. A picture still referenced (the one on screen) is never
  deleted (:func:`sweep`).
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterable
from pathlib import Path

RETRY_FIRST_S = 30 * 60
RETRY_MAX_S = 6 * 60 * 60
GRACE_S = 60 * 60
# Even over budget, a file this fresh stays: a render may be loading it.
MIN_AGE_S = 5 * 60


def retry_wait(failures: int, *, first: float = RETRY_FIRST_S, cap: float = RETRY_MAX_S) -> float:
    """Seconds to wait after the ``failures``-th failed try in a row (1, 2,
    ...): ``first`` doubling each time, at most ``cap``."""
    return float(min(first * 2 ** min(max(failures, 1) - 1, 16), cap))


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
) -> list[str]:
    """Delete the files in ``directory`` matching ``pattern`` that aren't in
    ``keep`` (names) once unused for ``grace_s``; then, while the folder is
    over ``max_bytes``, the oldest such files unused for at least
    ``MIN_AGE_S``. Files in ``keep`` are never deleted. Returns the names
    deleted.

    "Unused since" is when a file was first seen out of ``keep``, kept in
    ``ledger`` (a JSON file) when one is given: the grace starts when a
    picture is replaced, not when it was written, so a render that read the
    old state can still load it. Without a ledger it is the file's mtime."""
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
        with contextlib.suppress(OSError):
            path.unlink()
            total -= size
            gone.append(path.name)
    if ledger is not None:
        _write_ledger(ledger, {k: v for k, v in entries.items() if k in seen and k not in gone})
    return gone
