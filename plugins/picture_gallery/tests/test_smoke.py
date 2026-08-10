"""picture_gallery smoke: fetch + choices + admin blueprint endpoints,
all running against a real (empty) data_dir seeded by the test fixture."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from flask import Flask
from flask.testing import FlaskClient
from PIL import Image


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "gallery_server",
        Path(__file__).resolve().parents[1] / "server.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_image(folder_path: Path, name: str, size=(80, 60)) -> Path:
    folder_path.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", size, (255, 200, 0))
    out = folder_path / name
    img.save(out, format="JPEG")
    return out


def test_fetch_empty_folder_returns_friendly_error(app: Flask) -> None:
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    out = server.fetch(
        {"folder": ""},
        {},
        ctx={"data_dir": str(plugin.data_dir)},
    )
    assert out["url"] is None
    assert "No images" in out["error"]


def test_fetch_picks_a_seeded_image(app: Flask) -> None:
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    _seed_image(plugin.data_dir, "cat.jpg")
    out = server.fetch(
        {"folder": ""},
        {},
        ctx={"data_dir": str(plugin.data_dir)},
    )
    assert out["url"].endswith("/cat.jpg")
    assert out["count"] == 1
    assert out["folder"] == "_root"


def test_choices_lists_folders(app: Flask) -> None:
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    _seed_image(plugin.data_dir / "vacation", "beach.jpg")
    _seed_image(plugin.data_dir, "homepage.jpg")
    with app.test_request_context("/"):
        result = server.choices("folders")
    values = [c["value"] for c in result]
    assert "_root" in values
    assert "vacation" in values


def test_admin_index_page_loads(app: Flask, client: FlaskClient) -> None:
    with client.session_transaction() as sess:
        sess["authed"] = True
    resp = client.get("/plugins/picture_gallery/")
    assert resp.status_code == 200
    assert b"Gallery" in resp.data


def test_create_internal_folder_then_view_it(app: Flask, client: FlaskClient) -> None:
    with client.session_transaction() as sess:
        sess["authed"] = True
    resp = client.post(
        "/plugins/picture_gallery/folders",
        data={"name": "trips", "external_path": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # Folder page should now render
    resp2 = client.get("/plugins/picture_gallery/folders/trips")
    assert resp2.status_code == 200
    assert b"trips" in resp2.data


def test_create_folder_rejects_bad_name(app: Flask, client: FlaskClient) -> None:
    with client.session_transaction() as sess:
        sess["authed"] = True
    resp = client.post(
        "/plugins/picture_gallery/folders",
        data={"name": "BAD NAME!", "external_path": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    # No 'BAD NAME!' folder should exist
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    assert not (plugin.data_dir / "BAD NAME!").exists()


def test_serve_image_and_thumbnail(app: Flask, client: FlaskClient) -> None:
    with client.session_transaction() as sess:
        sess["authed"] = True
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    _seed_image(plugin.data_dir / "x", "tiny.jpg", size=(120, 90))
    resp = client.get("/plugins/picture_gallery/folders/x/tiny.jpg")
    try:
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("image/")
        # Read the body so Flask closes the underlying send_file handle.
        assert resp.get_data()
    finally:
        resp.close()
    thumb = client.get("/plugins/picture_gallery/folders/x/tiny.jpg/thumb")
    try:
        assert thumb.status_code == 200
        assert thumb.headers["Content-Type"] == "image/jpeg"
        assert thumb.get_data()
    finally:
        thumb.close()


# -- sequential cursor (#209) -------------------------------------------


def _sequential(server, plugin, *, preview: bool) -> str:
    ctx = {"data_dir": str(plugin.data_dir)}
    if preview:
        ctx["preview"] = True
    out = server.fetch({"folder": "", "mode": "sequential"}, {}, ctx=ctx)
    return str(out["filename"])


def test_a_panel_render_walks_the_album(app: Flask) -> None:
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _seed_image(plugin.data_dir, name)
    seen = [_sequential(server, plugin, preview=False) for _ in range(3)]
    assert sorted(seen) == ["a.jpg", "b.jpg", "c.jpg"], "each paint takes the next photo"


def test_a_preview_does_not_consume_a_photo(app: Flask) -> None:
    """Opening the editor, hovering a dashboard card, or probing the widget
    all call fetch(). Each one used to advance the album, so the panel then
    skipped whatever the preview ate."""
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _seed_image(plugin.data_dir, name)

    first_paint = _sequential(server, plugin, preview=False)
    previews = [_sequential(server, plugin, preview=True) for _ in range(4)]
    next_paint = _sequential(server, plugin, preview=False)

    # A preview shows what the panel will paint next, and stays there.
    assert len(set(previews)) == 1
    assert previews[0] != first_paint
    assert next_paint == previews[0], "the panel gets the photo the preview promised"
