"""media_lifecycle: the retry backoff and the picture cleanup."""

from __future__ import annotations

import os
import time
from pathlib import Path

from plugins.news_illustrated import media_lifecycle as ml


def test_the_wait_doubles_from_thirty_minutes_to_six_hours() -> None:
    assert [ml.retry_wait(n) / 60 for n in range(1, 8)] == [30, 60, 120, 240, 360, 360, 360]
    assert ml.retry_wait(0) == ml.RETRY_FIRST_S


def _file(folder: Path, name: str, size: int, age: float) -> Path:
    path = folder / name
    path.write_bytes(b"x" * size)
    when = time.time() - age
    os.utime(path, (when, when))
    return path


def test_sweep_honours_the_grace_period_and_never_touches_kept_files(tmp_path: Path) -> None:
    shown = _file(tmp_path, "shown.jpg", 10, 9 * 3600)
    old = _file(tmp_path, "old.jpg", 10, 2 * 3600)
    fresh = _file(tmp_path, "fresh.jpg", 10, 60)
    gone = ml.sweep(tmp_path, keep={"shown.jpg"}, max_bytes=10**9)
    assert gone == ["old.jpg"]
    assert shown.exists() and fresh.exists() and not old.exists()


def test_sweep_keeps_the_folder_under_budget_oldest_first(tmp_path: Path) -> None:
    _file(tmp_path, "shown.jpg", 100, 9 * 3600)
    for n, age in enumerate((40 * 60, 30 * 60, 20 * 60, 60)):
        _file(tmp_path, f"spare{n}.jpg", 100, age)
    gone = ml.sweep(tmp_path, keep={"shown.jpg"}, max_bytes=250)
    # Over budget: oldest unreferenced first, but nothing younger than
    # MIN_AGE_S (spare3), and never the kept file.
    assert gone == ["spare0.jpg", "spare1.jpg", "spare2.jpg"]
    assert (tmp_path / "shown.jpg").exists() and (tmp_path / "spare3.jpg").exists()


def test_with_a_ledger_the_grace_starts_when_a_file_stops_being_used(tmp_path: Path) -> None:
    """A picture written days ago and replaced just now stays for the grace
    period: a render that read the old state may still be loading it."""
    old = _file(tmp_path, "old.jpg", 10, 3 * 86400)
    ledger = tmp_path / "retired.json"
    now = time.time()
    assert ml.sweep(tmp_path, keep={"old.jpg"}, max_bytes=10**9, ledger=ledger, now=now) == []
    # Replaced: no longer kept. Unused from now on, not since it was written.
    assert ml.sweep(tmp_path, keep=set(), max_bytes=10**9, ledger=ledger, now=now) == []
    assert ml.sweep(tmp_path, keep=set(), max_bytes=10**9, ledger=ledger, now=now + 3000) == []
    assert old.exists()
    gone = ml.sweep(tmp_path, keep=set(), max_bytes=10**9, ledger=ledger, now=now + 3700)
    assert gone == ["old.jpg"]
    # Used again before the grace ran out: the clock starts over.
    again = _file(tmp_path, "again.jpg", 10, 3 * 86400)
    ml.sweep(tmp_path, keep=set(), max_bytes=10**9, ledger=ledger, now=now)
    ml.sweep(tmp_path, keep={"again.jpg"}, max_bytes=10**9, ledger=ledger, now=now + 3000)
    assert ml.sweep(tmp_path, keep=set(), max_bytes=10**9, ledger=ledger, now=now + 3700) == []
    assert again.exists()
