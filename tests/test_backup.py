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


def test_user_themes_included_in_snapshot(tmp_path: Path) -> None:
    """``data/themes/user.json`` is the only place user-saved themes
    live; it must ride along in the snapshot so a restore on a fresh
    install brings back the user's curated palette."""
    import json
    import zipfile

    root = tmp_path / "data"
    _seed(root)
    themes_dir = root / "themes"
    themes_dir.mkdir()
    (themes_dir / "user.json").write_text(
        json.dumps([{"id": "user-sunset", "name": "Sunset"}]),
        encoding="utf-8",
    )

    backup = bk.create(root, label="manual")
    with zipfile.ZipFile(backup.path) as zf:
        names = zf.namelist()
        assert "themes/user.json" in names
        data = json.loads(zf.read("themes/user.json"))
    assert data == [{"id": "user-sunset", "name": "Sunset"}]


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
    """A backup must not bundle prior backups, that would grow O(n²)."""
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


# ----- gallery (and any excluded subpath) handling ----------------------


def _seed_with_gallery(root: Path) -> None:
    """Layout typical of a live system: small config + a gallery dir with
    a dotfile (config) and a multi-MB "image" that should NOT make it
    into the backup."""
    _seed(root)
    gallery = root / "plugins" / "picture_gallery"
    gallery.mkdir(parents=True)
    (gallery / ".folders.json").write_text('{"holidays":{"label":"Holidays"}}')
    (gallery / "holidays").mkdir()
    (gallery / "holidays" / "sunset.jpg").write_bytes(b"BIGIMG" * 200_000)
    (gallery / "root_photo.png").write_bytes(b"BIGPNG" * 100_000)


def test_gallery_images_are_excluded_but_config_is_kept(tmp_path: Path) -> None:
    import zipfile

    root = tmp_path / "data"
    _seed_with_gallery(root)
    backup = bk.create(root, label="manual")
    with zipfile.ZipFile(backup.path) as zf:
        names = set(zf.namelist())
    # The small config rides along.
    assert "plugins/picture_gallery/.folders.json" in names
    # The image files are excluded, both nested and root-level.
    assert "plugins/picture_gallery/holidays/sunset.jpg" not in names
    assert "plugins/picture_gallery/root_photo.png" not in names
    # And the backup is dramatically smaller than the gallery footprint.
    gallery_bytes = sum(
        p.stat().st_size for p in (root / "plugins" / "picture_gallery").rglob("*") if p.is_file()
    )
    assert backup.bytes < gallery_bytes // 4


def test_excluded_subpaths_recorded_in_metadata(tmp_path: Path) -> None:
    import json
    import zipfile

    root = tmp_path / "data"
    _seed_with_gallery(root)
    backup = bk.create(root)
    with zipfile.ZipFile(backup.path) as zf:
        meta = json.loads(zf.read(bk.META_NAME))
    assert "plugins/picture_gallery" in meta["excluded_subpaths"]
    assert "core/renders" in meta["excluded_subpaths"]
    assert "core/companion_personal_data.json" in meta["excluded_subpaths"]
    assert meta["version"] >= 2


def test_render_cache_artifacts_are_excluded(tmp_path: Path) -> None:
    """core/renders is the content-addressed push cache, regenerable
    and potentially large, so it shouldn't ride along in backups."""
    import zipfile

    root = tmp_path / "data"
    _seed(root)
    renders = root / "core" / "renders"
    renders.mkdir(parents=True)
    (renders / "abc1234.png").write_bytes(b"\x89PNG" + b"x" * 200_000)
    (renders / "abc1234.bin").write_bytes(b"y" * 400_000)

    backup = bk.create(root)
    with zipfile.ZipFile(backup.path) as zf:
        names = set(zf.namelist())
    assert not any(n.startswith("core/renders/") for n in names), [
        n for n in names if n.startswith("core/renders/")
    ]


def test_personal_data_snapshots_are_excluded(tmp_path: Path) -> None:
    """Personal-data values are latest-only and must never enter backups."""
    import zipfile

    root = tmp_path / "data"
    _seed(root)
    personal_data = root / "core" / "companion_personal_data.json"
    personal_data.write_text('{"snapshot":{"title":"private"}}', encoding="utf-8")

    backup = bk.create(root)

    with zipfile.ZipFile(backup.path) as zf:
        assert "core/companion_personal_data.json" not in zf.namelist()


def test_restore_preserves_users_gallery_photos(tmp_path: Path) -> None:
    """Restoring a backup that excluded the gallery must NOT delete the
    user's current photos on disk, they're not in the backup."""
    root = tmp_path / "data"
    _seed_with_gallery(root)
    backup = bk.create(root)

    # Drop a new photo AFTER the backup. It should survive the restore.
    new_photo = root / "plugins" / "picture_gallery" / "holidays" / "new.jpg"
    new_photo.write_bytes(b"AFTER-BACKUP")
    # Also mutate a non-excluded file so we can confirm THAT one rolls back.
    (root / "core" / "settings.json").write_text('{"mutated":true}')

    bk.restore(root, backup.id)

    # Excluded file: the photo dropped after the backup is still there.
    assert new_photo.read_bytes() == b"AFTER-BACKUP"
    # Original gallery image (excluded; never in backup, never deleted) intact.
    assert (root / "plugins" / "picture_gallery" / "holidays" / "sunset.jpg").exists()
    # Non-excluded file rolled back from the snapshot.
    assert (root / "core" / "settings.json").read_text() == '{"app":{"x":1}}'
