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


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/settings/system/webhook/regenerate", {}),
        ("/settings/system/webhook/set", {"clear": "1"}),
        (
            "/settings/system/webhook/set",
            {"webhook_token": "a-token-of-my-own-choosing-0123456789"},
        ),
        ("/settings/system/mcp/clear", {}),
    ],
)
def test_token_actions_leave_the_rest_of_app_alone(
    app: Flask, path: str, data: dict[str, str]
) -> None:
    """Setting one token used to replace the whole app section, taking the
    session secret (and with it every stored connector secret) along."""
    client = app.test_client()
    _sign_in(client)
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"timezone": "Europe/Berlin"})
    before = store.get_section("app")
    assert before.get("session_secret_secret")
    client.post(path, data=data)
    after = store.get_section("app")
    assert after.get("session_secret_secret") == before["session_secret_secret"]
    assert after.get("timezone") == "Europe/Berlin"


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


# -- import keeps this server's identity (#349) ------------------------

_SECRET_FIELD = [{"name": "token", "secret": True}]


def _make_app(root: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=root,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _export_from_server_a(root: Path) -> bytes:
    """Server A: password ``password-a``, a name, and a connector secret
    wrapped with A's key. Returns its data export."""
    a = _make_app(root)
    client = a.test_client()
    client.post("/setup", data={"password": "password-a", "password_confirm": "password-a"})
    store = a.config["SETTINGS_STORE"]
    store.patch_section("app", {"instance_name": "Server A"})
    store.update_for_namespace("plugins", "probe", {"token": "tok-from-a"}, _SECRET_FIELD)
    resp = client.get("/settings/system/data/export")
    assert resp.status_code == 200
    return resp.data


def _import_into_server_b(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, take_identity: bool
) -> tuple[Flask, str]:
    """Export from A, import into B (password ``password-b``), then boot
    B again the way the post-import restart would."""
    from io import BytesIO

    from app.updater import Updater

    monkeypatch.delenv("TESSERAE_SECRET_KEY", raising=False)
    monkeypatch.setattr(Updater, "restart", lambda self, **kw: None)
    exported = _export_from_server_a(tmp_path / "a")

    b_root = tmp_path / "b"
    b = _make_app(b_root)
    client = b.test_client()
    client.post("/setup", data={"password": "password-b", "password_confirm": "password-b"})
    form: dict[str, object] = {"archive": (BytesIO(exported), "tesserae-data.zip")}
    if take_identity:
        form["take_identity"] = "1"
    resp = client.post(
        "/settings/system/data/import",
        data=form,
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Data imported" in body
    return _make_app(b_root), body


def _logs_in(app: Flask, password: str) -> bool:
    resp = app.test_client().post("/login", data={"password": password})
    return resp.status_code == 302


def test_data_import_keeps_this_servers_password_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b, body = _import_into_server_b(tmp_path, monkeypatch, take_identity=False)
    assert _logs_in(b, "password-b")
    assert not _logs_in(b, "password-a")
    store = b.config["SETTINGS_STORE"]
    # B keeps its identity, the content (here a connector secret wrapped
    # with A's key) comes across and still decrypts with B's key.
    assert "instance_name" not in store.get_section("app")
    assert store.unreadable_secrets("plugins", "probe") == set()
    assert store.get_for_runtime("plugins", "probe", _SECRET_FIELD) == {"token": "tok-from-a"}
    # A pre-import snapshot of B's own data is in the Backups list.
    from app import backup as _backup_mod

    labels = [x.label for x in _backup_mod.list_all(tmp_path / "b")]
    assert labels == [_backup_mod.LABEL_PRE_IMPORT]
    assert "couldn&#39;t be re-encrypted" not in body


def test_data_import_can_take_the_exports_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    b, _ = _import_into_server_b(tmp_path, monkeypatch, take_identity=True)
    assert _logs_in(b, "password-a")
    assert not _logs_in(b, "password-b")
    store = b.config["SETTINGS_STORE"]
    assert store.get_section("app").get("instance_name") == "Server A"
    assert store.get_for_runtime("plugins", "probe", _SECRET_FIELD) == {"token": "tok-from-a"}


def _chunked_import(client: FlaskClient, data: bytes, pieces: int, **form: str) -> str:
    """Send ``data`` the way the page does for a big zip: numbered pieces as
    raw bodies, then one form post that joins and imports them."""
    upload = "0123456789abcdef0123456789abcdef"
    size = -(-len(data) // pieces)
    for i in range(pieces):
        resp = client.post(
            f"/settings/system/data/import/chunk?upload={upload}&index={i}",
            data=data[i * size : (i + 1) * size],
            content_type="application/octet-stream",
        )
        assert resp.status_code == 200, resp.get_data(as_text=True)
    resp = client.post(
        "/settings/system/data/import/finish",
        data={"upload": upload, "parts": str(pieces), "filename": "big.zip", **form},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_data_import_in_pieces_matches_a_single_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zip too big for one request through Home Assistant's ingress proxy
    (16 MiB) arrives in pieces and imports exactly as a single upload."""
    import tempfile

    from app.updater import Updater

    monkeypatch.delenv("TESSERAE_SECRET_KEY", raising=False)
    monkeypatch.setattr(Updater, "restart", lambda self, **kw: None)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    exported = _export_from_server_a(tmp_path / "a")
    b = _make_app(tmp_path / "b")
    client = b.test_client()
    client.post("/setup", data={"password": "password-b", "password_confirm": "password-b"})

    body = _chunked_import(client, exported, 3)
    assert "Data imported" in body
    b = _make_app(tmp_path / "b")
    assert _logs_in(b, "password-b")
    store = b.config["SETTINGS_STORE"]
    assert store.get_for_runtime("plugins", "probe", _SECRET_FIELD) == {"token": "tok-from-a"}
    # The pieces are gone once joined.
    assert not list((tmp_path / "tmp" / "tesserae-import").iterdir())


def test_data_import_pieces_refuse_bad_input(
    app: Flask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    client = app.test_client()
    _sign_in(client)
    good = "0123456789abcdef0123456789abcdef"
    for query in ("upload=../../etc&index=0", f"upload={good}&index=-1", f"upload={good}&index=x"):
        resp = client.post(
            f"/settings/system/data/import/chunk?{query}",
            data=b"x",
            content_type="application/octet-stream",
        )
        assert resp.status_code == 400, query
    resp = client.post(
        f"/settings/system/data/import/chunk?upload={good}&index=0",
        data=b"x" * (12 * 1024 * 1024 + 1),
        content_type="application/octet-stream",
    )
    assert resp.status_code == 400
    # A missing piece is refused and nothing is imported.
    client.post(
        f"/settings/system/data/import/chunk?upload={good}&index=0",
        data=b"part",
        content_type="application/octet-stream",
    )
    resp = client.post(
        "/settings/system/data/import/finish",
        data={"upload": good, "parts": "2"},
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "part of the upload is missing" in body
    assert "Data imported" not in body
    assert not list((tmp_path / "tmp" / "tesserae-import").iterdir())


def test_settings_page_offers_chunked_import(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/system").get_data(as_text=True)
    assert "data-chunk-url=" in body and "/settings/system/data/import/chunk" in body
    assert "data-finish-url=" in body


def test_settings_page_offers_the_identity_opt_in(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/system").get_data(as_text=True)
    assert 'name="take_identity"' in body
    assert 'name="take_identity" value="1" checked' not in body


# -- features card ------------------------------------------------------


def test_features_card_renders_and_toggles(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings/system").get_data(as_text=True)
    assert "Features" in body and "Template marketplace" in body and "Canvas editor" in body
    # Nothing on the card is billed as experimental any more.
    card = body.split('id="features"', 1)[1].split("</section>", 1)[0]
    assert "xperimental" not in card and "feature flag" not in card.lower()

    # Switch the template marketplace off via the card's form (on by default).
    resp = client.post(
        "/settings/system/experiments/toggle",
        data={"name": "templates", "enable": "0"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert app.config["SETTINGS_STORE"].get_section("experiments").get("templates") is False

    # And back on (enable="0" above must NOT have parsed truthy).
    client.post(
        "/settings/system/experiments/toggle",
        data={"name": "templates", "enable": "1"},
    )
    assert app.config["SETTINGS_STORE"].get_section("experiments").get("templates") is True


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
