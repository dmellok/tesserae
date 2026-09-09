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


def _sequential(server, plugin, *, preview: bool, device: str | None = None) -> str:
    ctx = {"data_dir": str(plugin.data_dir)}
    if preview:
        ctx["preview"] = True
    if device is not None:
        ctx["target_device_id"] = device
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


def test_client_maps_every_scale_choice_to_an_object_fit() -> None:
    # Issue #296: the Scale dropdown was declared in plugin.json but the
    # client never read it, so every choice rendered as Fill (the shared
    # .is-bleed cover rule). Keep the manifest choices and the client's
    # object-fit table in step.
    import json

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    scale = next(o for o in manifest["cell_options"] if o["name"] == "scale")
    client = (root / "client.js").read_text(encoding="utf-8")
    for choice in scale["choices"]:
        assert f'{choice["value"]}: "' in client, choice["value"]
    assert "opts.scale" in client
    assert "object-fit:${objectFit}" in client


def test_two_devices_walk_the_album_independently(app: Flask) -> None:
    """The half of #209 the preview guard does not cover.

    Keyed by folder and orientation alone, the cursor was global: two
    dashboards pointing at one folder shared a position and each advanced it,
    so a panel woken between the other's renders skipped whatever they
    consumed. Each device now has its own cursor.

    Asserted as the literal interleaved sequence: under the shared cursor
    kitchen, hallway, hallway, kitchen gave a, b, c, a.
    """
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _seed_image(plugin.data_dir, name)

    seen = [
        _sequential(server, plugin, preview=False, device=device)
        for device in ("kitchen", "hallway", "hallway", "kitchen")
    ]
    assert seen == ["a.jpg", "a.jpg", "b.jpg", "b.jpg"]


def test_one_devices_paints_do_not_move_another_devices_position(app: Flask) -> None:
    """The failure an operator actually sees: a skipped photo.

    Interleaving the two panels must not make either skip. Under the shared
    cursor the second device continued where the first left off.
    """
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _seed_image(plugin.data_dir, name)

    first_kitchen = _sequential(server, plugin, preview=False, device="kitchen")
    for _ in range(2):
        _sequential(server, plugin, preview=False, device="hallway")
    second_kitchen = _sequential(server, plugin, preview=False, device="kitchen")

    order = sorted(["a.jpg", "b.jpg", "c.jpg"])
    expected = order[(order.index(first_kitchen) + 1) % len(order)]
    assert second_kitchen == expected, (
        "the kitchen panel skipped a photo: the hallway's paints moved its cursor"
    )


def _cursor_files(plugin) -> list[str]:
    return sorted(
        p.name for p in plugin.data_dir.iterdir() if p.name.startswith(".sequential_index")
    )


def test_a_render_with_no_device_keeps_the_shared_cursor(app: Flask) -> None:
    """An unbound or virtual-panel render has no device to walk for.

    It keeps the folder-scoped file every album written before the
    per-device cursor already uses, and a device render writes a second
    file named after the device rather than touching the shared one.
    """
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _seed_image(plugin.data_dir, name)

    _sequential(server, plugin, preview=False)
    unbound = _cursor_files(plugin)
    assert len(unbound) == 1, f"expected one cursor file, got {unbound}"

    _sequential(server, plugin, preview=False, device="kitchen")
    with_device = _cursor_files(plugin)
    assert with_device == [unbound[0], f"{unbound[0]}_kitchen"], (
        "a device render writes its own, readable cursor beside the shared one"
    )
    assert (plugin.data_dir / unbound[0]).read_text(encoding="utf-8") == "0", (
        "the device render must not move the shared cursor"
    )


def test_a_devices_first_render_continues_from_the_shared_cursor(app: Flask) -> None:
    """Binding is the delivery model, so after the upgrade every real push
    arrives with a device id and opens a fresh per-device file. It seeds
    from the shared cursor instead of restarting the album at photo 0."""
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        _seed_image(plugin.data_dir, name)

    before_upgrade = [_sequential(server, plugin, preview=False) for _ in range(2)]
    assert before_upgrade == ["a.jpg", "b.jpg"]

    assert _sequential(server, plugin, preview=False, device="kitchen") == "c.jpg"
    assert _sequential(server, plugin, preview=False, device="hallway") == "c.jpg", (
        "each device seeds from the shared position, not from another device"
    )
    assert _sequential(server, plugin, preview=False, device="kitchen") == "a.jpg"


def test_a_device_id_never_reaches_the_filename_raw(app: Flask) -> None:
    """A registry id (``DEVICE_ID_RE``) is used as-is so the file names its
    panel. Anything else is hashed: a separator or a traversal segment in a
    device id must not be able to steer the write out of the widget's data
    dir."""
    server = _load_server()
    plugin = app.config["PLUGIN_REGISTRY"].get("picture_gallery")
    _seed_image(plugin.data_dir, "a.jpg")

    _sequential(server, plugin, preview=False, device="../../etc/passwd")
    _sequential(server, plugin, preview=False, device="Kitchen Panel")

    written = [p for p in plugin.data_dir.iterdir() if p.name.startswith(".sequential_index_")]
    assert len(written) == 2, "each render wrote its own cursor"
    for path in written:
        assert ".." not in path.name
        assert "/" not in path.name
        assert " " not in path.name
        assert "passwd" not in path.name and "Kitchen" not in path.name
        assert path.parent == plugin.data_dir
