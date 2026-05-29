"""Backup module: create → list → restore round-trip + edge cases."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app import backup as bk


def _seed(root: Path) -> None:
    """Lay down a representative data/ tree (json + nested + sqlite)."""
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "settings.json").write_text('{"app":{"x":1}}')
    (root / "core" / "pages.json").write_text('{"pages":[]}')
    (root / "plugins" / "weather_now").mkdir(parents=True)
    (root / "plugins" / "weather_now" / "cache.json").write_text('{"temp":22}')
    # A real SQLite db so we exercise the online-backup path.
    db = sqlite3.connect(root / "core" / "events.db")
    db.execute("CREATE TABLE t (k TEXT, v INTEGER)")
    db.executemany("INSERT INTO t VALUES (?,?)", [("a", 1), ("b", 2)])
    db.commit()
    db.close()


def _read_db_rows(db_path: Path) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return list(conn.execute("SELECT k, v FROM t ORDER BY k"))
    finally:
        conn.close()


def test_create_then_restore_round_trips_everything(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _seed(root)

    backup = bk.create(root, label="manual", note="round-trip")
    assert backup.path.exists()
    assert backup.bytes > 0
    assert backup.label == "manual"

    # Mutate data after backup; restore should undo these.
    (root / "core" / "settings.json").write_text('{"app":{"x":999}}')
    (root / "plugins" / "weather_now" / "cache.json").unlink()
    conn = sqlite3.connect(root / "core" / "events.db")
    try:
        conn.execute("DELETE FROM t")
        conn.commit()
    finally:
        conn.close()

    bk.restore(root, backup.id)

    assert (root / "core" / "settings.json").read_text() == '{"app":{"x":1}}'
    assert (root / "plugins" / "weather_now" / "cache.json").read_text() == '{"temp":22}'
    assert _read_db_rows(root / "core" / "events.db") == [("a", 1), ("b", 2)]


def test_backups_directory_is_excluded_from_snapshot(tmp_path: Path) -> None:
    """A backup must not bundle prior backups — that would grow O(n²)."""
    root = tmp_path / "data"
    _seed(root)
    first = bk.create(root, label="manual")
    second = bk.create(root, label="manual")
    # The second .zip must not contain the first one inside it.
    import zipfile

    with zipfile.ZipFile(second.path) as zf:
        names = zf.namelist()
    assert all(bk.BACKUPS_SUBDIR not in n for n in names), names
    assert first.path.exists()  # still around, just not inside the new zip


def test_list_returns_newest_first_with_metadata(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _seed(root)
    a = bk.create(root, label="manual", note="first")
    b = bk.create(root, label="pre-update", note="second")
    items = bk.list_all(root)
    assert [i.id for i in items[:2]] == [b.id, a.id]
    assert items[0].label == "pre-update"
    assert items[0].note == "second"


def test_delete_removes_the_zip(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _seed(root)
    backup = bk.create(root)
    assert bk.delete(root, backup.id) is True
    assert not backup.path.exists()
    assert bk.delete(root, backup.id) is False  # already gone


def test_restore_unknown_id_raises(tmp_path: Path) -> None:
    root = tmp_path / "data"
    _seed(root)
    with pytest.raises(FileNotFoundError):
        bk.restore(root, "does-not-exist")
