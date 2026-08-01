"""Settings → System: page renders + backup action routes round-trip.

The update routes (check / apply / rollback) hit ``git``/``pip`` against
the real repo, so they're covered by ``tests/test_updater.py`` instead -
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
    assert "Online features" in body  # master api.tesserae.ink switch
    # The Updater's current_state() resolves against the real repo, the
    # version string from pyproject should appear.
    assert "v0." in body  # e.g. "v0.2.0"


def test_online_features_toggle(app: Flask) -> None:
    """The master switch flips settings.app.online_features. Checkbox absent
    means off; present means on."""
    client = app.test_client()
    _sign_in(client)
    store = app.config["SETTINGS_STORE"]
    # Absent checkbox -> off.
    resp = client.post("/settings/system/online-features/toggle", data={}, follow_redirects=False)
    assert resp.status_code == 302
    assert store.get_section("app").get("online_features") is False
    # Present -> on.
    client.post("/settings/system/online-features/toggle", data={"online_features": "1"})
    assert store.get_section("app").get("online_features") is True


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
    compose pull`` hint, a ``git pull`` inside a layered filesystem
    would lose changes on the next image rebuild."""
    monkeypatch.setenv("TESSERAE_IN_DOCKER", "1")
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/system").get_data(as_text=True)
    assert "docker compose pull" in body
    # The check / apply / rollback forms are not in the rendered Update card.
    assert "Check for updates" not in body
    assert "Update &amp; restart" not in body


def test_update_apply_refused_under_docker(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hand-crafted POST to /settings/system/update/apply under the
    Docker image is server-side refused too, not just hidden in the UI."""
    monkeypatch.setenv("TESSERAE_IN_DOCKER", "1")
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/system/update/apply", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "docker compose pull" in body


def test_data_import_runs_under_docker(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data-import flow only writes to the persistent ``data/``
    volume, so the docker refusal doesn't apply (unlike self-update,
    which needs the git tree). Regression: pre-0.36 the HA add-on
    (which inherits ``TESSERAE_IN_DOCKER=1`` from the base image) bailed
    on import with a misleading "use docker compose pull" flash."""
    import json
    import time
    from io import BytesIO

    from app import backup as _backup_mod
    from app.updater import Updater

    monkeypatch.setenv("TESSERAE_IN_DOCKER", "1")
    # Restart would otherwise os.execv the pytest process out from under us.
    monkeypatch.setattr(Updater, "restart", lambda self, **kw: None)

    client = app.test_client()
    _sign_in(client)

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            _backup_mod.META_NAME,
            json.dumps(
                {
                    "tool": "tesserae",
                    "version": _backup_mod.META_VERSION,
                    "created_at": time.time(),
                    "label": "test",
                    "excluded_subpaths": [],
                }
            ),
        )
    buf.seek(0)
    resp = client.post(
        "/settings/system/data/import",
        data={"archive": (buf, "test-export.zip")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The "Updates aren't supported..." string is unique to the refusal
    # flash; the bare "docker compose pull" hint also appears in the
    # Updates-card upgrade instructions on the system page.
    assert "aren&#39;t supported in the Docker image" not in body
    assert "Data imported" in body


def test_backup_restore_runs_under_docker(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same reasoning as the import test: restore only touches the
    persistent volume, so it must work in the HA add-on / Docker image."""
    from app.updater import Updater

    # Create the backup before flipping the env so create() takes its
    # normal path; restore is the one we care about gating.
    (tmp_path / "core").mkdir(parents=True, exist_ok=True)
    (tmp_path / "core" / "sentinel.txt").write_text("hello-restore")

    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/backup/create", data={"note": "smoke"})
    backup_id = next((tmp_path / "core" / "backups").glob("*.zip")).stem

    monkeypatch.setenv("TESSERAE_IN_DOCKER", "1")
    monkeypatch.setattr(Updater, "restart", lambda self, **kw: None)

    resp = client.post(f"/settings/system/backup/{backup_id}/restore", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The "Updates aren't supported..." string is unique to the refusal
    # flash; the bare "docker compose pull" hint also appears in the
    # Updates-card upgrade instructions on the system page.
    assert "aren&#39;t supported in the Docker image" not in body
    assert f"Restored from {backup_id}" in body


# -- experiments card ---------------------------------------------------


def test_experiments_card_renders_and_toggles(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/system").get_data(as_text=True)
    assert "Experiments" in body and "Template marketplace" in body

    # Enable the templates experiment via the card's form.
    resp = client.post(
        "/settings/system/experiments/toggle",
        data={"name": "templates", "enable": "1"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert app.config["SETTINGS_STORE"].get_section("experiments").get("templates") is True

    # Disable round-trips too (enable="0" must NOT parse truthy).
    client.post(
        "/settings/system/experiments/toggle",
        data={"name": "templates", "enable": "0"},
    )
    assert app.config["SETTINGS_STORE"].get_section("experiments").get("templates") is False


def test_experiments_toggle_rejects_unknown_and_env_forced(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/experiments/toggle", data={"name": "nope", "enable": "1"})
    assert "nope" not in (app.config["SETTINGS_STORE"].get_section("experiments") or {})

    monkeypatch.setenv("TESSERAE_EXPERIMENT_TEMPLATES", "0")
    client.post("/settings/system/experiments/toggle", data={"name": "templates", "enable": "1"})
    assert (app.config["SETTINGS_STORE"].get_section("experiments") or {}).get("templates") is None


def test_mcp_toggle_disable_actually_disables(app: Flask) -> None:
    """Regression: the disable button posts enable="0", and bool("0") is True,
    which used to re-enable the experiment instead of disabling it."""
    client = app.test_client()
    _sign_in(client)
    client.post("/settings/system/mcp/toggle", data={"enable": "1"})
    assert app.config["SETTINGS_STORE"].get_section("experiments").get("mcp") is True
    client.post("/settings/system/mcp/toggle", data={"enable": "0"})
    assert app.config["SETTINGS_STORE"].get_section("experiments").get("mcp") is False
