"""MCP widget push/install API: install/upsert/reload/delete/list + faithful render,
with the extraction guards (tar-slip, oversize, non-widget, bundled collision) and auth.

The renderer and the process updater are mocked so no Chromium is spun up and no test
process re-execs itself.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"
_REMOTE = {"REMOTE_ADDR": "203.0.113.9"}


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _enable(app: Flask) -> None:
    app.config["SETTINGS_STORE"].patch_section("experiments", {"mcp": True})


def _widget_files(
    *,
    name: str = "Air Quality",
    version: str = "0.1.0",
    kind: str = "widget",
    with_server: bool = False,
    with_blueprint: bool = False,
) -> dict[str, str]:
    manifest: dict[str, Any] = {
        "tesserae_compat": "1.x",
        "name": name,
        "version": version,
        "kind": kind,
        "supports": {"sizes": ["md"]},
    }
    files = {
        "plugin.json": json.dumps(manifest),
        "client.js": "export default function(s,c){s.innerHTML='<div class=\"w\">ok</div>';}",
    }
    if with_server:
        files["server.py"] = "def fetch(options, settings, *, ctx):\n    return {'value': 42}\n"
    if with_blueprint:
        files["server.py"] = (
            "from flask import Blueprint\n"
            "def fetch(options, settings, *, ctx):\n    return {'value': 1}\n"
            "def blueprint():\n    return Blueprint('x', __name__)\n"
        )
    return files


def _make_tar(files: dict[str, str], top: str | None = "w") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            path = f"{top}/{name}" if top else name
            data = content.encode()
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _install(client: Any, tar: bytes, query: str = "") -> Any:
    return client.post(
        f"/api/mcp/widgets/install{query}", data=tar, content_type="application/gzip"
    )


# -- install + reload ---------------------------------------------------


def test_install_reloads_in_process_and_goes_live(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    tar = _make_tar(_widget_files(with_server=True), top="air_quality")
    resp = _install(client, tar)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] and body["id"] == "air_quality"
    assert body["reload"] == "in_process" and body["restarting"] is False
    assert body["active"] is True

    # On the persistent volume, and live in the registry / catalog.
    assert (Path(app.config["DATA_ROOT"]) / "authored" / "air_quality" / "plugin.json").is_file()
    cat = client.get("/api/mcp/catalog").get_json()["widgets"]
    assert any(w["key"] == "air_quality" for w in cat)

    # Its real fetch() runs through the probe.
    data = client.post("/api/mcp/widgets/air_quality/data", json={}).get_json()
    assert data["data_source"] == "live" and data["data"]["value"] == 42


def test_pushed_widget_serves_client_js_after_in_process_reload(app: Flask) -> None:
    """A brand-new widget's client.js is served over /plugins/<id>/ right after the
    in-process reload, no restart. The asset routes read the registry fresh per
    request, so the swapped-in registry's new plugin id resolves (regression: the
    closed-over startup registry 404'd it)."""
    _enable(app)
    client = app.test_client()
    _install(client, _make_tar(_widget_files(), top="air_quality"))
    resp = client.get("/plugins/air_quality/client.js")
    try:
        assert resp.status_code == 200
        assert b"export default" in resp.get_data()
    finally:
        resp.close()  # release the send_from_directory file handle


def test_install_upserts_new_version(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    _install(client, _make_tar(_widget_files(version="0.1.0"), top="air_quality"))
    _install(client, _make_tar(_widget_files(version="0.2.0"), top="air_quality"))
    listing = client.get("/api/mcp/widgets?origin=authored").get_json()["widgets"]
    entry = next(w for w in listing if w["id"] == "air_quality")
    assert entry["version"] == "0.2.0" and entry["active"] is True


def test_uninstall_removes_and_deactivates(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    _install(client, _make_tar(_widget_files(), top="air_quality"))
    resp = client.delete("/api/mcp/widgets/air_quality")
    assert resp.status_code == 200 and resp.get_json()["active"] is False
    assert not (Path(app.config["DATA_ROOT"]) / "authored" / "air_quality").exists()
    cat = client.get("/api/mcp/catalog").get_json()["widgets"]
    assert not any(w["key"] == "air_quality" for w in cat)
    # A second delete 404s (nothing left).
    assert client.delete("/api/mcp/widgets/air_quality").status_code == 404


def test_list_requires_origin(app: Flask) -> None:
    _enable(app)
    client = app.test_client()
    _install(client, _make_tar(_widget_files(), top="air_quality"))
    assert client.get("/api/mcp/widgets").status_code == 400
    listing = client.get("/api/mcp/widgets?origin=authored").get_json()["widgets"]
    assert [w["id"] for w in listing] == ["air_quality"]


def test_reload_endpoint(app: Flask) -> None:
    _enable(app)
    resp = app.test_client().post("/api/mcp/reload", json={"mode": "in_process"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] and body["mode"] == "in_process" and body["restarting"] is False


def test_blueprint_widget_schedules_restart(app: Flask) -> None:
    _enable(app)
    app.config["UPDATER"] = MagicMock()  # never actually re-exec the test process
    client = app.test_client()
    tar = _make_tar(_widget_files(with_blueprint=True), top="admin_thing")
    body = _install(client, tar).get_json()
    assert body["reload"] == "restart" and body["restarting"] is True
    app.config["UPDATER"].restart.assert_called_once()


# -- validation + security ---------------------------------------------


def test_tar_slip_rejected(app: Flask) -> None:
    _enable(app)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../evil.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(data))
    resp = _install(app.test_client(), buf.getvalue())
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    assert not (Path(app.config["DATA_ROOT"]) / "authored" / "evil.txt").exists()


def test_oversize_rejected(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    from app import authored_widgets

    monkeypatch.setattr(authored_widgets, "MAX_COMPRESSED_BYTES", 16)
    resp = _install(app.test_client(), _make_tar(_widget_files(), top="air_quality"))
    assert resp.status_code == 413


def test_non_widget_kind_rejected(app: Flask) -> None:
    _enable(app)
    tar = _make_tar(_widget_files(kind="font"), top="a_font")
    resp = _install(app.test_client(), tar)
    assert resp.status_code == 422 and "widget" in resp.get_json()["error"]


def test_missing_manifest_rejected(app: Flask) -> None:
    _enable(app)
    tar = _make_tar({"client.js": "export default function(){}"}, top="nope")
    resp = _install(app.test_client(), tar)
    assert resp.status_code == 422


def test_bundled_id_collision_rejected(app: Flask) -> None:
    _enable(app)
    # clock_analog is a bundled widget; a pushed id that shadows it would be a
    # silent no-op (bundled wins in discover), so reject it.
    tar = _make_tar(_widget_files(), top="whatever")
    resp = _install(app.test_client(), tar, query="?id=clock_analog")
    assert resp.status_code == 409


def test_unauthenticated_remote_401(app: Flask) -> None:
    _enable(app)
    tar = _make_tar(_widget_files(), top="air_quality")
    resp = app.test_client().post(
        "/api/mcp/widgets/install",
        data=tar,
        content_type="application/gzip",
        environ_overrides=_REMOTE,
    )
    assert resp.status_code == 401


def test_surface_404_when_experiment_off(app: Flask) -> None:
    # mcp experiment off (default) -> the whole surface 404s, push included.
    assert app.test_client().post("/api/mcp/widgets/install", data=b"x").status_code == 404


# -- faithful render ----------------------------------------------------


def test_render_png(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(app)
    monkeypatch.setattr("app.renderer.render_to_png", lambda req, pool=None: _FAKE_PNG)
    client = app.test_client()
    resp = client.get("/api/mcp/widgets/clock_analog/render.png?size=md")
    assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert resp.get_data() == _FAKE_PNG
    # Unknown widget + bad size.
    assert client.get("/api/mcp/widgets/nope/render.png").status_code == 404
    assert client.get("/api/mcp/widgets/clock_analog/render.png?size=huge").status_code == 400


def test_render_png_screenshots_widget_not_login_with_password(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for the 0.109 login-screenshot bug: with a password set, the URL
    render.png hands to the headless renderer must itself render the widget over
    loopback, not redirect to /login."""
    from app import auth

    _enable(app)
    auth.set_password(app.config["SETTINGS_STORE"], "pw12345678")
    client = app.test_client()  # fresh, unauthed, loopback

    captured: dict[str, str] = {}

    def _capture(req: Any, pool: Any = None) -> bytes:
        captured["url"] = req.url
        return _FAKE_PNG

    monkeypatch.setattr("app.renderer.render_to_png", _capture)
    resp = client.get("/api/mcp/widgets/clock_analog/render.png?size=md")
    assert resp.status_code == 200

    # Fetch the exact URL the renderer was told to screenshot, over loopback with
    # no session: it must return the widget markup, not the login page.
    from urllib.parse import urlsplit

    parts = urlsplit(captured["url"])
    inner = client.get(f"{parts.path}?{parts.query}")
    assert inner.status_code == 200
    body = inner.get_data(as_text=True)
    assert 'data-plugin="clock_analog"' in body and "password" not in body.lower()


def test_test_render_loopback_bypass_with_password(app: Flask) -> None:
    """The /_test/render loopback bypass (the fix): with a password set, a
    loopback caller with no session reaches the widget render, while a remote
    unauthed caller is still refused (403). The fixture builds the app with the
    auth gate installed (create_app(testing=False))."""
    from app import auth

    auth.set_password(app.config["SETTINGS_STORE"], "pw12345678")
    client = app.test_client()  # fresh, unauthed
    ok = client.get("/_test/render?plugin=clock_analog&size=md")  # loopback by default
    assert ok.status_code == 200 and 'data-plugin="clock_analog"' in ok.get_data(as_text=True)
    blocked = client.get(
        "/_test/render?plugin=clock_analog&size=md",
        environ_overrides={"REMOTE_ADDR": "203.0.113.9"},
    )
    assert blocked.status_code == 403
    assert 'data-plugin="clock_analog"' not in blocked.get_data(as_text=True)
