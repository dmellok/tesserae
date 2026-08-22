"""Plugin blueprint routes are behind the password gate.

The loopback exemption exists for one thing: the in-process Playwright
renderer pulling ``/plugins/<id>/client.js`` while composing, with no session
to present. It used to be matched by path shape, and plugin-provided
blueprints mount under that same ``/plugins/<id>/`` prefix, so every route a
plugin registered inherited the exemption: gallery folder deletes
(``shutil.rmtree``), calendar feed writes, and CalDAV discovery against an
operator-named URL, all answerable with no session.

Loopback is reachable by an attacker. The operator screenshot and remote-image
flows run with ``allow_local=True``, which skips the Chromium request
interceptor entirely (``app/renderer.py``), so a hostile page rendered by the
server can POST to ``127.0.0.1``. Form-encoded POSTs are not preflighted, so a
response the page cannot read still performs the action, and the app has no
CSRF tokens.

These tests drive the real gate from an unauthenticated client whose
``remote_addr`` is loopback, which is the exact shape of that attack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

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
    # Arm the gate: an install with no password set is open by design.
    a.test_client().post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return a


def _gallery_dir(app: Flask) -> Path:
    return Path(app.config["PLUGIN_REGISTRY"].get("picture_gallery").data_dir)


def _seed_folder(app: Flask, name: str) -> Path:
    folder = _gallery_dir(app) / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "holiday.jpg").write_bytes(b"irreplaceable")
    return folder


def test_the_renderer_can_still_fetch_a_plugin_asset(app: Flask) -> None:
    """The exemption has to keep working, or every composed panel loses its
    widget code."""
    resp = app.test_client().get("/plugins/weather_now/client.js")
    assert resp.status_code == 200
    # Consume + close: send_from_directory streams from an open file handle,
    # which pytest reports as an unraisable ResourceWarning if left dangling.
    assert resp.get_data()
    resp.close()


def test_the_renderer_can_still_fetch_a_gallery_image(app: Flask) -> None:
    """picture_gallery's widget data hands the panel an ``<img src>`` pointing
    at its own ``serve_image`` route, which the renderer fetches over loopback
    with no session. Gating the whole blueprint breaks every gallery photo on
    every panel, so the plugin declares its two read-only image endpoints in
    RENDER_SAFE_ENDPOINTS."""
    _seed_folder(app, "holidays")

    resp = app.test_client().get("/plugins/picture_gallery/folders/holidays/holiday.jpg")

    assert resp.status_code == 200
    assert resp.get_data() == b"irreplaceable"
    resp.close()


def test_render_safe_covers_reads_only_not_writes(app: Flask) -> None:
    """The opt-in must not become a hole: a declared endpoint is exempt, its
    sibling delete on the same blueprint is not."""
    declared = app.config["RENDER_SAFE_ENDPOINTS"]
    assert "picture_gallery_admin.serve_image" in declared
    assert not any("delete" in name or "upload" in name for name in declared), declared


def test_an_unauthenticated_loopback_post_cannot_delete_a_gallery_folder(app: Flask) -> None:
    folder = _seed_folder(app, "family_photos")

    resp = app.test_client().post("/plugins/picture_gallery/folders/family_photos/delete")

    assert resp.status_code != 200
    assert folder.exists(), "unauthenticated loopback request deleted the folder"


def test_an_unauthenticated_loopback_post_cannot_run_caldav_discovery(app: Flask) -> None:
    """``discover`` turns a caller-supplied URL into an outbound PROPFIND with
    no SSRF guard, which is fine for an operator naming their own LAN CalDAV
    server and not fine for an anonymous caller."""
    resp = app.test_client().post(
        "/plugins/calendar_core/discover",
        data={"base_url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert resp.status_code != 200


def test_an_unauthenticated_loopback_post_cannot_add_a_calendar_feed(app: Flask) -> None:
    resp = app.test_client().post(
        "/plugins/calendar_core/feeds",
        data={"name": "planted", "url": "http://169.254.169.254/latest/meta-data/"},
    )
    assert resp.status_code != 200


def test_an_unauthenticated_loopback_post_cannot_write_a_todo_list(app: Flask) -> None:
    resp = app.test_client().post("/plugins/todo/lists", data={"name": "planted"})
    assert resp.status_code != 200


def test_plugin_admin_pages_stay_gated(app: Flask) -> None:
    client = app.test_client()
    for path in ("/plugins/", "/plugins/picture_gallery/", "/plugins/calendar_core/"):
        assert client.get(path).status_code != 200, path


def test_an_authenticated_operator_can_still_delete_a_folder(app: Flask) -> None:
    """The gate is the only thing that should have changed."""
    folder = _seed_folder(app, "old_screenshots")
    client = app.test_client()
    client.post("/login", data={"password": "abcdefgh"})

    resp = client.post("/plugins/picture_gallery/folders/old_screenshots/delete")

    assert resp.status_code in (200, 302)
    assert not folder.exists()


# -- a plugin serving its own media (#255) --------------------------------


def test_a_plugin_can_serve_its_own_media_to_the_renderer(app: Flask) -> None:
    """A catalog widget that serves artwork from its own blueprint rendered a
    broken image on every panel: the RENDER_SAFE_ENDPOINTS declaration is an
    in-tree convention its author never saw. A read of a plugin's own
    sub-route is allowed from loopback again."""
    from app import auth

    assert (
        auth._path_is_loopback_only(
            "/plugins/album_art/artwork/deadbeef.jpg",
            "album_art_admin.artwork",
            frozenset(),
            "GET",
        )
        is True
    )


def test_a_write_to_a_plugin_route_is_still_refused(app: Flask) -> None:
    """Read-only is the line. Every dangerous route found when this was
    tightened was a mutation, and those must stay shut."""
    from app import auth

    for method in ("POST", "PUT", "DELETE", "PATCH"):
        assert (
            auth._path_is_loopback_only(
                "/plugins/picture_gallery/folders/holidays/delete",
                "picture_gallery_admin.delete_folder",
                frozenset(),
                method,
            )
            is False
        ), method


def test_a_plugin_index_is_still_refused_even_on_a_read(app: Flask) -> None:
    """#221: the index lists loader errors and plugin contents, and gtfs turns
    a query argument on it into an outbound request."""
    from app import auth

    for path, endpoint in [
        ("/plugins/gtfs/", "gtfs_admin.index"),
        ("/plugins/picture_gallery/", "picture_gallery_admin.index"),
        ("/plugins/", "plugins.plugins_index"),
    ]:
        assert auth._path_is_loopback_only(path, endpoint, frozenset(), "GET") is False, path


def test_the_live_gate_serves_a_plugin_read_and_refuses_its_write(app: Flask) -> None:
    """End to end through the real gate, from an unauthenticated loopback
    client: the exact shape of a render fetching a widget's media."""
    client = app.test_client()
    folder = _seed_folder(app, "family_photos")

    ok = client.get("/plugins/picture_gallery/folders/family_photos/holiday.jpg")
    assert ok.status_code == 200
    ok.close()

    denied = client.post("/plugins/picture_gallery/folders/family_photos/delete")
    assert denied.status_code != 200
    assert folder.exists()
