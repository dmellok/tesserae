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


def test_system_page_swaps_update_card_under_docker(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the official Docker image (``TESSERAE_IN_DOCKER=1``) the
    in-app self-update card is hidden and replaced by a ``docker
    compose pull`` hint — a ``git pull`` inside a layered filesystem
    would lose changes on the next image rebuild."""
    monkeypatch.setenv("TESSERAE_IN_DOCKER", "1")
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/system").get_data(as_text=True)
    assert "docker compose pull" in body
    # The check / apply / rollback forms are not in the rendered Update card.
    assert "Check for updates" not in body
    assert "Update &amp; restart" not in body


def test_update_apply_refused_under_docker(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-crafted POST to /settings/system/update/apply under the
    Docker image is server-side refused too, not just hidden in the UI."""
    monkeypatch.setenv("TESSERAE_IN_DOCKER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/system/update/apply", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "docker compose pull" in body


def test_telemetry_test_button_is_dev_only(app: Flask) -> None:
    """The "Send test event" button is dev-only — the card is hidden in
    production builds and the route is gated to ``current_app.debug``.
    Hitting it without debug should be a silent no-op redirect, not a
    flash that admits the route exists."""
    client = app.test_client()
    _sign_in(client)
    # Test mode runs with debug=False, so the route returns the system
    # redirect without flashing anything.
    resp = client.post("/settings/system/telemetry/test", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Telemetry is off" not in body


def test_telemetry_test_button_flashes_when_disabled(app: Flask) -> None:
    """In dev mode, when telemetry is off, the test-event button should
    bounce back with a friendly explanation rather than pretending to
    send."""
    app.debug = True
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/system/telemetry/test", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Telemetry is off" in body


def test_telemetry_test_button_records_event_when_enabled(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With telemetry enabled and a stubbed urlopen, hitting the test
    route should write a telemetry row to the EventLog. The route is
    dev-gated, so flip ``app.debug`` for the test."""
    app.debug = True
    import dataclasses
    from io import BytesIO

    from app import telemetry as tm

    class _OK:
        def read(self, n: int = -1) -> bytes:
            return BytesIO(b"OK").read(n)

        def __enter__(self) -> _OK:
            return self

        def __exit__(self, *a: object) -> bool:
            return False

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", lambda req, timeout=0: _OK())
    # Spin telemetry up live without touching real settings.
    telemetry: tm.Telemetry = app.config["TELEMETRY"]
    telemetry._cfg = dataclasses.replace(
        telemetry._cfg,
        enabled=True,
        host="https://analytics.example.com",
        app_key="AK-test",
    )
    # Wire the in-process event log into the live telemetry so the test
    # route's record() lands in the same log the assertion reads.
    telemetry._event_log = app.config["EVENT_LOG"]

    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/system/telemetry/test", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Test event delivered" in body

    log = app.config["EVENT_LOG"]
    rows = log.list(type="telemetry", limit=10)
    assert any(r.source == "app.started" and r.status == "sent" for r in rows)
