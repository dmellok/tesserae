"""media_lifecycle.py: the retry backoff and the cleanup of the pages
folder."""

from __future__ import annotations

import os
import time
from pathlib import Path

from plugins.news_front_pages import media_lifecycle as ml

DAY = "2026-10-25"


def test_the_wait_doubles_from_thirty_minutes_to_six_hours() -> None:
    assert [ml.retry_wait(n) / 60 for n in range(1, 8)] == [30, 60, 120, 240, 360, 360, 360]
    assert ml.retry_wait(0) == ml.RETRY_FIRST_S


def test_failed_counts_tries_per_day_and_may_try_follows_them() -> None:
    now = 1_000_000.0
    record = ml.failed(None, DAY, now)
    assert record == {"day": DAY, "tries": 1, "after": now + 1800}
    assert not ml.may_try(record, DAY, now + 1799)
    assert ml.may_try(record, DAY, now + 1800)
    assert ml.may_try(record, DAY, now, force=True)
    assert ml.may_try(record, "2026-10-26", now)  # a new day starts over
    for _ in range(ml.TRIES_PER_DAY - 1):
        record = ml.failed(record, DAY, now)
    assert record["tries"] == ml.TRIES_PER_DAY
    assert not ml.may_try(record, DAY, now + 86400)
    assert ml.failed(record, "2026-10-26", now)["tries"] == 1
    assert ml.may_try(None, DAY, now) and ml.may_try({"day": DAY, "tries": "x"}, DAY, now)


def test_describe_says_when_and_which_try() -> None:
    now = 1_000_000.0
    assert ml.describe(ml.failed(None, DAY, now), DAY, now) == "next try in 30 min (try 2 of 6)"
    record = {"day": DAY, "tries": 3, "after": now + 4 * 3600}
    assert ml.describe(record, DAY, now) == "next try in 4 h (try 4 of 6)"
    assert ml.describe({"day": DAY, "tries": 6, "after": 0}, DAY, now) == "no more tries today"
    assert ml.describe(None, DAY, now) == "" and ml.describe(record, "2026-10-26", now) == ""


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


def test_the_server_uses_this_module() -> None:
    """server.py loads its copy from the same file (the host imports
    server.py by path, so it can't import its neighbours as a package)."""
    from plugins.news_front_pages import server

    assert Path(server.lifecycle.__file__).resolve() == Path(ml.__file__).resolve()
    assert server.lifecycle.TRIES_PER_DAY == ml.TRIES_PER_DAY
