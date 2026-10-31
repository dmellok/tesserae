"""Widgets page upload: a .zip or .tar.gz of a widget the operator wrote for their
own install goes into ``data/authored/`` and is live without a restart, and the
page can remove it again. The archive guards themselves are covered by
test_widget_push.py; this covers the zip repack and the page round trip."""

from __future__ import annotations

import io
import json
import stat
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import authored_widgets
from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _client(app: Flask) -> Any:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


def _files(*, name: str = "Sensor Notes", blueprint: bool = False) -> dict[str, str]:
    files = {
        "plugin.json": json.dumps(
            {
                "tesserae_compat": "1.x",
                "name": name,
                "version": "0.1.0",
                "kind": "widget",
                "supports": {"sizes": ["md"]},
            }
        ),
        "client.js": "export default function(s,c){s.innerHTML='ok';}",
    }
    if blueprint:
        files["server.py"] = (
            "from flask import Blueprint\n"
            "def fetch(options, settings, *, ctx):\n    return {}\n"
            "def blueprint():\n    return Blueprint('sensor_notes_admin', __name__)\n"
        )
    return files


def _zip(files: dict[str, str], top: str | None = "sensor_notes", junk: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(f"{top}/{name}" if top else name, content)
        if junk:
            # What Finder's Compress adds beside the folder.
            zf.writestr(f"__MACOSX/{top}/._plugin.json", "x")
            zf.writestr(f"{top}/.DS_Store", "x")
    return buf.getvalue()


def _tar(files: dict[str, str], top: str = "sensor_notes") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _upload(client: Any, data: bytes, filename: str = "sensor_notes.zip") -> Any:
    return client.post(
        "/plugins/widgets/upload",
        data={"archive": (io.BytesIO(data), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_a_zip_upload_is_live_without_a_restart(app: Flask, tmp_path: Path) -> None:
    client = _client(app)
    resp = _upload(client, _zip(_files()))
    body = resp.get_data(as_text=True)
    assert "Installed Sensor Notes (sensor_notes v0.1.0)" in body
    assert (tmp_path / "authored" / "sensor_notes" / "plugin.json").is_file()
    assert app.config["PLUGIN_REGISTRY"].get("sensor_notes") is not None
    assert not app.config.get("MARKETPLACE_RESTART_PENDING")
    # The page lists it with a Remove button.
    assert 'name="plugin_id" value="sensor_notes"' in body


def test_a_finder_zip_installs(app: Flask, tmp_path: Path) -> None:
    resp = _upload(_client(app), _zip(_files(), junk=True))
    assert "Installed Sensor Notes" in resp.get_data(as_text=True)
    installed = tmp_path / "authored" / "sensor_notes"
    assert not (installed / ".DS_Store").exists()
    assert not (tmp_path / "authored" / "__MACOSX").exists()


def test_a_tarball_upload_installs(app: Flask) -> None:
    resp = _upload(_client(app), _tar(_files()), "sensor_notes.tar.gz")
    assert "Installed Sensor Notes" in resp.get_data(as_text=True)


def test_a_bad_manifest_is_refused_with_the_reason(app: Flask, tmp_path: Path) -> None:
    files = _files()
    files["plugin.json"] = json.dumps({"name": "x", "kind": "widget"})
    body = _upload(_client(app), _zip(files)).get_data(as_text=True)
    assert "Upload refused: manifest schema" in body
    assert not (tmp_path / "authored" / "sensor_notes").exists()


def test_a_bundled_id_is_refused(app: Flask) -> None:
    body = _upload(_client(app), _zip(_files(), top="clock_analog")).get_data(as_text=True)
    assert "Upload refused" in body
    assert "collides with a bundled widget" in body


def test_not_an_archive_is_refused(app: Flask) -> None:
    body = _upload(_client(app), b"hello", "notes.zip").get_data(as_text=True)
    assert "Upload refused: could not extract tarball" in body


def test_no_file_is_refused(app: Flask) -> None:
    resp = _client(app).post("/plugins/widgets/upload", data={}, follow_redirects=True)
    assert "Choose a .zip or .tar.gz" in resp.get_data(as_text=True)


def test_an_admin_page_asks_for_a_restart(app: Flask) -> None:
    body = _upload(_client(app), _zip(_files(blueprint=True))).get_data(as_text=True)
    assert "appears after a restart" in body
    assert app.config["MARKETPLACE_RESTART_PENDING"] is True


def test_remove_deletes_an_uploaded_widget(app: Flask, tmp_path: Path) -> None:
    client = _client(app)
    _upload(client, _zip(_files()))
    resp = client.post(
        "/plugins/widgets/remove", data={"plugin_id": "sensor_notes"}, follow_redirects=True
    )
    assert "Removed sensor_notes." in resp.get_data(as_text=True)
    assert not (tmp_path / "authored" / "sensor_notes").exists()
    assert app.config["PLUGIN_REGISTRY"].get("sensor_notes") is None


def test_remove_never_touches_a_bundled_widget(app: Flask) -> None:
    resp = _client(app).post(
        "/plugins/widgets/remove", data={"plugin_id": "clock_analog"}, follow_redirects=True
    )
    assert "t an uploaded widget" in resp.get_data(as_text=True)
    assert (REPO_ROOT / "plugins" / "clock_analog" / "plugin.json").is_file()
    assert app.config["PLUGIN_REGISTRY"].get("clock_analog") is not None


def test_bundled_rows_have_no_remove_button(app: Flask) -> None:
    body = _client(app).get("/plugins/").get_data(as_text=True)
    assert "Upload widget" in body
    # The Disable form posts the same hidden plugin_id; only Remove is barred.
    assert body.count('action="/plugins/widgets/remove"') == 0
    assert "Remove" not in body.split('data-sort-id="clock_analog"', 1)[1].split("</li>", 1)[0]


# -- zip repack guards --------------------------------------------------


def test_zip_slip_is_refused(tmp_path: Path) -> None:
    files = {"../evil/plugin.json": "{}"}
    tar = authored_widgets.zip_to_tarball(_zip(files, top=None))
    with pytest.raises(authored_widgets.InstallError):
        authored_widgets.install_tarball(
            tar,
            data_root=tmp_path,
            plugins_dir=REPO_ROOT / "plugins",
            schema_path=REPO_ROOT / "schema" / "plugin.schema.json",
        )
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "authored" / "evil").exists()


def test_zip_symlinks_are_dropped() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        link = zipfile.ZipInfo("w/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
        zf.writestr("w/plugin.json", "{}")
    tar = authored_widgets.zip_to_tarball(buf.getvalue())
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        assert t.getnames() == ["w/plugin.json"]


def test_zip_uncompressed_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authored_widgets, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(authored_widgets.InstallError) as exc:
        authored_widgets.zip_to_tarball(_zip({"plugin.json": "x" * 100}))
    assert exc.value.status == 413
