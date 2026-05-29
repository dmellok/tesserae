"""Settings → System: page renders + backup action routes round-trip.

The update routes (check / apply / rollback) hit ``git``/``pip`` against
the real repo, so they're covered by ``tests/test_updater.py`` instead —
exercising them through the test client would mutate the working tree.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: FlaskClient) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_settings_system_page_renders(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/system")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Updates" in body
    assert "Backups" in body
    # The Updater's current_state() resolves against the real repo — the
    # version string from pyproject should appear.
    assert "v0." in body  # e.g. "v0.2.0"


def test_create_then_download_then_delete_backup(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    # Seed a sentinel file so we know the backup actually captured data/.
    (tmp_path / "core").mkdir(parents=True, exist_ok=True)
    (tmp_path / "core" / "sentinel.txt").write_text("hello-backup")

    resp = client.post("/settings/system/backup/create", data={"note": "smoke"})
    assert resp.status_code == 302

    backup_dir = tmp_path / "core" / "backups"
    zips = list(backup_dir.glob("*.zip"))
    assert len(zips) == 1
    backup_id = zips[0].stem

    # The sentinel landed inside the zip.
    with zipfile.ZipFile(zips[0]) as zf:
        assert "core/sentinel.txt" in zf.namelist()

    dl = client.get(f"/settings/system/backup/{backup_id}/download")
    assert dl.status_code == 200
    assert dl.headers["Content-Type"] == "application/zip"
    assert dl.data.startswith(b"PK")  # zip magic

    rm = client.post(f"/settings/system/backup/{backup_id}/delete")
    assert rm.status_code == 302
    assert not zips[0].exists()


def test_backup_download_404_when_missing(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/settings/system/backup/does-not-exist/download")
    assert resp.status_code == 404
