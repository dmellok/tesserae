"""The artwork admin page the host renders for any collection."""

from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from flask import Flask
from PIL import Image

from app import artwork, fal
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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), (10, 120, 200)).save(buf, "JPEG")
    return buf.getvalue()


def test_the_page_lists_pictures_by_dashboard_and_acts_on_them(app: Flask, monkeypatch) -> None:
    registry = app.config["PLUGIN_REGISTRY"]
    plugin = next(p for p in registry.plugins.values() if p.kind == "widget")
    data_dir = Path(plugin.data_dir)

    def make(job: artwork.Job):
        return _jpeg(), {"credit": "test credit", "style": job.payload.get("style", "plain")}

    c = artwork.Collection(
        plugin.id,
        "tests",
        make=make,
        label="Test art",
        title="Making",
        facet="style",
        facet_label=lambda s: s.title(),
    )
    c.action("shout", "Shout", lambda key, d: f"shouted {key}")
    monkeypatch.setattr(fal, "resolve_key", lambda settings=None: "k")
    c.keep(
        "robin--plain",
        data_dir,
        _jpeg(),
        {
            "title": "Robin",
            "style": "plain",
            "payload": {"style": "plain"},
            "credit": "test credit",
            "link": "https://ref.example/r",
            "by": "A. Photographer",
        },
    )
    c.note_dashboard(
        data_dir,
        {"page_name": "Study", "page_icon": "ph-bird"},
        "s1",
        "plain",
        {
            "robin--plain": {"payload": {"style": "plain"}, "title": "Robin"},
            "wren--plain": {"payload": {"style": "plain"}, "title": "Wren"},
        },
    )

    client = app.test_client()
    _sign_in(client)
    body = client.get(f"/plugins/{plugin.id}/artwork/tests/").get_data(as_text=True)
    assert "Study" in body and "Robin" in body and "Wren" in body and "1 still to make" in body
    assert (
        "test credit" in body
        and "A. Photographer" in body
        and ">Shout<" in body
        and "Plain" in body
    )
    assert client.get(f"/plugins/{plugin.id}/artwork/nope/").status_code == 404
    with client.get(f"/plugins/{plugin.id}/artwork/tests/file/robin--plain.jpg") as picture:
        assert picture.status_code == 200 and picture.get_data()[:3] == b"\xff\xd8\xff"
    assert client.get(f"/plugins/{plugin.id}/artwork/tests/file/../secret").status_code == 404

    prog = client.get(f"/plugins/{plugin.id}/artwork/tests/progress").get_json()
    assert prog["cap_text"] == "US$4.00" and prog["used"] == 0

    r = client.post(
        f"/plugins/{plugin.id}/artwork/tests/robin--plain/action/shout", follow_redirects=True
    )
    assert "shouted robin--plain" in r.get_data(as_text=True)
    r = client.post(f"/plugins/{plugin.id}/artwork/tests/render-now", follow_redirects=True)
    assert "Painting 1 of the 1" in r.get_data(as_text=True)
    end = time.time() + 5
    while time.time() < end and c.path("wren--plain", data_dir) is None:
        time.sleep(0.05)
    assert c.path("wren--plain", data_dir) is not None, (
        "Render now painted the one the dashboard was missing"
    )
    r = client.post(
        f"/plugins/{plugin.id}/artwork/tests/robin--plain/delete", follow_redirects=True
    )
    assert (
        "kept as an earlier version" in r.get_data(as_text=True)
        and c.path("robin--plain", data_dir) is None
    )
    assert len(c.versions("robin--plain", data_dir)) == 1
    stem = c.versions("robin--plain", data_dir)[0]["stem"]
    client.post(f"/plugins/{plugin.id}/artwork/tests/archive/{stem}/use", follow_redirects=True)
    assert c.path("robin--plain", data_dir) is not None
    r = client.post(f"/plugins/{plugin.id}/artwork/tests/purge", follow_redirects=True)
    assert "Removed 1 picture" in r.get_data(as_text=True) or "Removed 2 picture" in r.get_data(
        as_text=True
    )


def test_href_carries_the_ingress_prefix(app: Flask) -> None:
    """Widgets redirect their Plugins menu entry to ``href``; under Home
    Assistant's ingress it must keep the prefix or the browser lands on the HA
    host root and gets a 404. The tray keeps the bare path."""
    c = artwork.Collection("some_widget", "prefixed", make=lambda job: (b"", {}), label="Art")
    assert c.href == "/plugins/some_widget/artwork/prefixed/"
    with app.test_request_context("/", base_url="http://ha.local/api/hassio_ingress/abc123"):
        assert c.href == "/api/hassio_ingress/abc123/plugins/some_widget/artwork/prefixed/"
    assert c.page_path == "/plugins/some_widget/artwork/prefixed/"
    assert c._tray.href == c.page_path


def test_corrupt_budget_keeps_recovery_page_available(app, monkeypatch):
    registry = app.config["PLUGIN_REGISTRY"]
    plugin = next(p for p in registry.plugins.values() if p.kind == "widget")
    data_dir = Path(plugin.data_dir)
    c = artwork.Collection(plugin.id, "corrupt", make=lambda job: (b"", {}), label="Test art")
    c.folder(data_dir).mkdir(parents=True)
    (c.folder(data_dir) / "budget.json").write_text("partial record")
    monkeypatch.setattr(fal, "resolve_key", lambda settings=None: "k")
    client = app.test_client()
    _sign_in(client)
    url = f"/plugins/{plugin.id}/artwork/corrupt/"
    response = client.get(url)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "budget could not be read" in body
    assert ">Reset</button>" in body
    progress = client.get(url + "progress").get_json()
    assert progress["used"] is None
    assert progress["used_text"] == "Unavailable"
    client.post(url + "budget-reset")
    assert c.budget_used(data_dir) == 0
