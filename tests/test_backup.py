"""Backup module: create → list → restore round-trip + edge cases."""

from __future__ import annotations

import shutil
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


# ----- import keeps this server's identity (#349) ------------------------


def _seed_server(root: Path, *, name: str, session: bytes, token: str) -> None:
    """A data/ tree for one server: identity in settings + identity files,
    content (devices, a plugin secret wrapped with this server's key)."""
    import json

    from app.secret_box import SecretBox

    box = SecretBox.from_session_secret(session)
    settings = {
        "app": {
            "session_secret_secret": session.hex(),
            "instance_name": name,
            "server_colour": "red" if name == "A" else "blue",
            "public_url": f"http://{name.lower()}.lan:8765",
            "mcp_token_secret": f"mcp-{name}",
            "webhook_token_secret": f"hook-{name}",
            "timezone": f"tz-{name}",
        },
        "auth": {"password_salt": f"salt-{name}", "password_hash_secret": f"hash-{name}"},
        "relay": {"install_id": f"relay-{name}", "publisher_token_secret": box.wrap(name)},
        "broker": {"embedded_password_secret": f"broker-{name}"},
        "devices": {f"panel_{name}": {"name": name}},
        "plugins": {"ha": {"token_secret": box.wrap(token)}},
    }
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    for rel in bk.IDENTITY_FILES:
        (root / rel).write_text(f"{rel}-{name}", encoding="utf-8")
    (root / "core" / "pages.json").write_text(f'{{"from":"{name}"}}', encoding="utf-8")


def _hand_over(export: bk.Backup, root: Path) -> None:
    """Put another server's export where restore() looks for it."""
    dest = root / bk.BACKUPS_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(export.path, dest / export.path.name)


def _import_a_into_b(tmp_path: Path, *, keep_identity: bool) -> tuple[Path, bk.RestoreResult]:
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_server(a, name="A", session=b"A" * 32, token="tok-from-a")
    _seed_server(b, name="B", session=b"B" * 32, token="tok-from-b")
    export = bk.create(a, label="manual")
    _hand_over(export, b)
    return b, bk.restore(b, export.id, keep_identity=keep_identity)


def _settings(root: Path) -> dict:
    import json

    return json.loads((root / "core" / "settings.json").read_text(encoding="utf-8"))


def test_restore_keep_identity_keeps_this_servers_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.secret_box import SecretBox

    monkeypatch.delenv("TESSERAE_SECRET_KEY", raising=False)
    b, result = _import_a_into_b(tmp_path, keep_identity=True)
    s = _settings(b)

    # Identity stays B's.
    assert s["auth"] == {"password_salt": "salt-B", "password_hash_secret": "hash-B"}
    assert s["relay"]["install_id"] == "relay-B"
    for key in bk.IDENTITY_APP_KEYS:
        assert s["app"][key] == _expected_b_app()[key], key
    for rel in bk.IDENTITY_FILES:
        assert (b / rel).read_text(encoding="utf-8") == f"{rel}-B"
    # Content comes from A: devices, broker, other app settings, pages.
    assert s["devices"] == {"panel_A": {"name": "A"}}
    assert s["broker"] == {"embedded_password_secret": "broker-A"}
    assert s["app"]["timezone"] == "tz-A"
    assert (b / "core" / "pages.json").read_text(encoding="utf-8") == '{"from":"A"}'

    # A's connector secret was re-wrapped so it decrypts with B's key, and
    # B's own relay secret (kept as is) still decrypts too.
    b_box = SecretBox.from_session_secret(b"B" * 32)
    assert b_box.unwrap(s["plugins"]["ha"]["token_secret"]) == "tok-from-a"
    assert b_box.unwrap(s["relay"]["publisher_token_secret"]) == "B"
    assert result.unreadable_secrets == ()


def _expected_b_app() -> dict[str, str]:
    return {
        "session_secret_secret": (b"B" * 32).hex(),
        "instance_name": "B",
        "server_colour": "blue",
        "public_url": "http://b.lan:8765",
        "mcp_token_secret": "mcp-B",
        "webhook_token_secret": "hook-B",
    }


def test_restore_without_keep_identity_takes_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same-machine restore (and the import opt-in) is a plain restore."""
    monkeypatch.delenv("TESSERAE_SECRET_KEY", raising=False)
    b, result = _import_a_into_b(tmp_path, keep_identity=False)
    s = _settings(b)
    assert s["auth"]["password_hash_secret"] == "hash-A"
    assert s["app"]["session_secret_secret"] == (b"A" * 32).hex()
    assert s["app"]["instance_name"] == "A"
    for rel in bk.IDENTITY_FILES:
        assert (b / rel).read_text(encoding="utf-8") == f"{rel}-A"
    assert result.unreadable_secrets == ()


def test_restore_keep_identity_drops_identity_this_server_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Identity the current server doesn't have isn't borrowed from the
    export either: no name, no install id file, no relay link."""
    import json

    monkeypatch.delenv("TESSERAE_SECRET_KEY", raising=False)
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_server(a, name="A", session=b"A" * 32, token="tok")
    (b / "core").mkdir(parents=True)
    (b / "core" / "settings.json").write_text(
        json.dumps({"app": {"session_secret_secret": (b"B" * 32).hex()}}), encoding="utf-8"
    )
    export = bk.create(a)
    _hand_over(export, b)
    bk.restore(b, export.id, keep_identity=True)
    s = _settings(b)
    assert "auth" not in s and "relay" not in s
    assert "instance_name" not in s["app"]
    assert s["app"]["session_secret_secret"] == (b"B" * 32).hex()
    for rel in bk.IDENTITY_FILES:
        assert not (b / rel).exists()


def test_restore_keep_identity_reports_secrets_it_cannot_rewrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret wrapped with a key neither the env nor the export's
    session secret gives (an export from a server with its own
    TESSERAE_SECRET_KEY) is left alone and reported."""
    import json

    from app.secret_box import SecretBox

    monkeypatch.delenv("TESSERAE_SECRET_KEY", raising=False)
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_server(a, name="A", session=b"A" * 32, token="tok")
    _seed_server(b, name="B", session=b"B" * 32, token="tok")
    settings = _settings(a)
    stranger = SecretBox(b"Z" * 32).wrap("lost")
    settings["plugins"]["other"] = {"api_key_secret": stranger}
    (a / "core" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    export = bk.create(a)
    _hand_over(export, b)
    result = bk.restore(b, export.id, keep_identity=True)
    assert result.unreadable_secrets == ("plugins.other.api_key_secret",)
    s = _settings(b)
    assert s["plugins"]["other"]["api_key_secret"] == stranger
    b_box = SecretBox.from_session_secret(b"B" * 32)
    assert b_box.unwrap(s["plugins"]["ha"]["token_secret"]) == "tok"


def test_restore_keep_identity_with_shared_env_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both servers on the same TESSERAE_SECRET_KEY: secrets wrapped with
    it still open after the import."""
    import json

    from app.secret_box import SecretBox

    key = "11" * 32
    monkeypatch.setenv("TESSERAE_SECRET_KEY", key)
    env_box = SecretBox(bytes.fromhex(key))
    a, b = tmp_path / "a", tmp_path / "b"
    _seed_server(a, name="A", session=b"A" * 32, token="tok")
    _seed_server(b, name="B", session=b"B" * 32, token="tok")
    settings = _settings(a)
    settings["plugins"]["ha"]["token_secret"] = env_box.wrap("env-tok")
    (a / "core" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    export = bk.create(a)
    _hand_over(export, b)
    result = bk.restore(b, export.id, keep_identity=True)
    assert result.unreadable_secrets == ()
    assert env_box.unwrap(_settings(b)["plugins"]["ha"]["token_secret"]) == "env-tok"
