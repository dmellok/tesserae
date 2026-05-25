"""Editor: page CRUD + cell CRUD + compose iframe access via authed session."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import PageStore


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


# -- /pages list + new ---------------------------------------------


def test_empty_list_renders_with_create_link(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/pages").get_data(as_text=True)
    assert "No pages yet" in body
    assert "/pages/new" in body


def test_create_page_lands_on_edit(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/pages/new",
        data={
            "name": "Home",
            "id": "",
            "panel_w": "800",
            "panel_h": "600",
            "gap": "8",
            "corner_radius": "4",
            "bleed_color": "#ffffff",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.location.endswith("/pages/home")
    store = PageStore(tmp_path / "core" / "pages.json")
    page = store.get("home")
    assert page is not None
    assert page.name == "Home"
    assert page.panel.w == 800
    assert page.panel.h == 600
    assert page.gap == 8


def test_create_page_rejects_duplicate_id(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300"},
        follow_redirects=True,
    )
    assert b"already exists" in resp.data


def test_create_page_rejects_bad_id(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/pages/new",
        data={"name": "Home", "id": "Has Spaces", "panel_w": "400", "panel_h": "300"},
        follow_redirects=True,
    )
    assert b"snake_case" in resp.data or b"Bad id" in resp.data


# -- page edit ----------------------------------------------------


def test_update_page_metadata(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    client.post(
        "/pages/home",
        data={
            "name": "Living room",
            "panel_w": "640",
            "panel_h": "400",
            "theme": "embers",
            "gap": "16",
            "corner_radius": "8",
            "bleed_color": "#202020",
        },
    )
    page = PageStore(tmp_path / "core" / "pages.json").get("home")
    assert page is not None
    assert page.name == "Living room"
    assert page.panel.w == 640
    assert page.theme == "embers"
    assert page.gap == 16


def test_delete_page_removes_it(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Doomed", "panel_w": "400", "panel_h": "300"})
    client.post("/pages/doomed/delete")
    assert PageStore(tmp_path / "core" / "pages.json").get("doomed") is None


# -- cell CRUD ----------------------------------------------------


def test_create_cell_picks_plugin_defaults(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.post("/pages/home/cells", data={"plugin": "clock"}, follow_redirects=False)
    assert resp.status_code == 302
    page = PageStore(tmp_path / "core" / "pages.json").get("home")
    assert page is not None
    assert len(page.cells) == 1
    cell = page.cells[0]
    assert cell.plugin == "clock"
    # Plugin's manifest cell_option defaults flow through automatically.
    assert cell.options.get("format") == "24h"
    assert cell.options.get("show_date") is True
    # Redirect lands on the cell edit page.
    assert f"/pages/home/cells/{cell.id}" in resp.location


def test_create_cell_rejects_missing_plugin(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.post("/pages/home/cells", data={}, follow_redirects=True)
    assert b"Pick a widget plugin" in resp.data


def test_update_cell_position_and_options(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "800", "panel_h": "600"})
    client.post("/pages/home/cells", data={"plugin": "clock"})
    cell_id = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0].id

    client.post(
        f"/pages/home/cells/{cell_id}",
        data={
            "x": "50",
            "y": "60",
            "w": "200",
            "h": "150",
            "theme": "cobalt",
            "opt_format": "12h",
            "opt_show_seconds": "on",
            # show_date checkbox NOT in form -> False
        },
    )
    updated = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0]
    assert (updated.x, updated.y, updated.w, updated.h) == (50, 60, 200, 150)
    assert updated.theme == "cobalt"
    assert updated.options["format"] == "12h"
    assert updated.options["show_seconds"] is True
    assert updated.options["show_date"] is False


def test_update_cell_clamps_out_of_bounds(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    client.post("/pages/home/cells", data={"plugin": "clock"})
    cell_id = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0].id

    client.post(
        f"/pages/home/cells/{cell_id}",
        data={"x": "999", "y": "-50", "w": "5000", "h": "5000"},
    )
    cell = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0]
    # x clamped to panel.w - 1, y to >= 0, w/h to <= panel dims.
    assert cell.x == 399
    assert cell.y == 0
    assert cell.w == 400
    assert cell.h == 300


def test_delete_cell(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    client.post("/pages/home/cells", data={"plugin": "clock"})
    cell_id = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0].id
    client.post(f"/pages/home/cells/{cell_id}/delete")
    assert PageStore(tmp_path / "core" / "pages.json").get("home").cells == []


def test_cell_palette_overrides_round_trip(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    client.post("/pages/home/cells", data={"plugin": "clock"})
    cell_id = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0].id
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={
            "x": "0",
            "y": "0",
            "w": "100",
            "h": "80",
            "override_accent_hex": "#112233",
            "override_bg_hex": "",  # explicit empty -> not stored
            "override_accent": "#112233",
            "override_bg": "#ffffff",
        },
    )
    cell = PageStore(tmp_path / "core" / "pages.json").get("home").cells[0]
    # The current form parser only reads override_<token> (not the _hex pair);
    # the JS keeps them in sync. Empty values inherit (no override stored).
    assert cell.palette_overrides is not None
    assert cell.palette_overrides.get("accent") == "#112233"


# -- /compose iframe access ----------------------------------------


def test_compose_reachable_from_authed_session(app: Flask) -> None:
    """The editor previews via an iframe pointing at /compose/<id>. With
    M14 the auth gate lets an authed session through even from a
    non-loopback origin."""
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.get("/compose/home", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 200
    assert b"data-cell-id" not in resp.data  # no cells yet
    assert b'<div class="panel"' in resp.data


def test_compose_still_403s_from_lan_without_session(app: Flask) -> None:
    """A LAN request without an admin session should still get 403'd —
    we relaxed for authed sessions, not for everyone."""
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    # Drop the session.
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get("/compose/home", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 403


def test_nav_link_present(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "/pages" in body
