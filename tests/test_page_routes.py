"""Editor (M15): page + cell CRUD with the layout / autosave model."""

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


def _store(tmp_path: Path) -> PageStore:
    return PageStore(tmp_path / "core" / "pages.json")


# -- /pages list + new -----------------------------------------------


def test_empty_list_renders_with_create_link(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/pages").get_data(as_text=True)
    assert "No pages yet" in body
    assert "/pages/new" in body


def test_create_page_with_default_layout(app: Flask, tmp_path: Path) -> None:
    """New pages now create cells from a layout template. With the
    default '1_cell' layout, one full-panel unassigned cell appears."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/pages/new",
        data={
            "name": "Home",
            "panel_w": "800",
            "panel_h": "600",
            "layout": "1_cell",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    page = _store(tmp_path).get("home")
    assert page is not None
    assert len(page.cells) == 1
    assert page.cells[0].plugin is None  # unassigned
    assert (page.cells[0].x, page.cells[0].y) == (0, 0)
    assert (page.cells[0].w, page.cells[0].h) == (800, 600)


def test_create_page_with_2x2_grid_layout(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Grid", "panel_w": "800", "panel_h": "600", "layout": "2x2_grid"},
    )
    page = _store(tmp_path).get("grid")
    assert page is not None
    assert len(page.cells) == 4
    # Top-left quadrant.
    assert (page.cells[0].x, page.cells[0].y, page.cells[0].w, page.cells[0].h) == (0, 0, 400, 300)
    # Bottom-right quadrant snaps to the panel edge.
    assert (page.cells[3].x, page.cells[3].y, page.cells[3].w, page.cells[3].h) == (
        400, 300, 400, 300,
    )


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


# -- page edit -------------------------------------------------------


def test_update_page_metadata_autosave_json(app: Flask, tmp_path: Path) -> None:
    """Autosave clients POST with X-Requested-With: fetch and get JSON
    back. A native form submit still gets a redirect + flash."""
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.post(
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
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    assert resp.json == {"ok": True, "message": "Page saved."}
    page = _store(tmp_path).get("home")
    assert page is not None
    assert page.name == "Living room"
    assert page.panel.w == 640
    assert page.theme == "embers"


def test_delete_page_removes_it(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Doomed", "panel_w": "400", "panel_h": "300"})
    client.post("/pages/doomed/delete")
    assert _store(tmp_path).get("doomed") is None


# -- layout apply ---------------------------------------------------


def test_apply_layout_reuses_existing_cells(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "800", "panel_h": "600", "layout": "1_cell"},
    )
    page = _store(tmp_path).get("home")
    cell_id = page.cells[0].id
    # Assign a plugin to the single cell.
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={"plugin": "clock", "x": "0", "y": "0", "w": "800", "h": "600"},
    )
    # Apply 2x2 grid — first cell keeps its plugin, three more empty appear.
    client.post("/pages/home/layout", data={"layout": "2x2_grid"})
    page = _store(tmp_path).get("home")
    assert len(page.cells) == 4
    assert page.cells[0].plugin == "clock"
    assert (page.cells[0].x, page.cells[0].y, page.cells[0].w, page.cells[0].h) == (0, 0, 400, 300)
    assert all(c.plugin is None for c in page.cells[1:])


def test_apply_layout_drops_surplus_cells(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "800", "panel_h": "600", "layout": "2x2_grid"},
    )
    client.post("/pages/home/layout", data={"layout": "1_cell"})
    page = _store(tmp_path).get("home")
    assert len(page.cells) == 1


def test_apply_unknown_layout_via_fetch_returns_json(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.post(
        "/pages/home/layout",
        data={"layout": "nope"},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    assert resp.json == {"ok": False, "message": "Unknown layout 'nope'."}


# -- cell CRUD ------------------------------------------------------


def test_add_empty_cell(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300", "layout": "1_cell"},
    )
    # 1_cell default seeded one cell; add another empty.
    client.post("/pages/home/cells")
    page = _store(tmp_path).get("home")
    assert len(page.cells) == 2
    assert page.cells[-1].plugin is None


def test_assign_plugin_seeds_options(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300", "layout": "1_cell"},
    )
    cell_id = _store(tmp_path).get("home").cells[0].id
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={"plugin": "clock", "x": "0", "y": "0", "w": "400", "h": "300"},
    )
    cell = _store(tmp_path).get("home").cells[0]
    assert cell.plugin == "clock"
    assert cell.options.get("format") == "24h"
    assert cell.options.get("show_date") is True


def test_change_plugin_resets_options(app: Flask, tmp_path: Path) -> None:
    """In the editor, picking a plugin reloads the form so the new
    plugin's option fields are rendered before the user touches them.
    Server-side, any submit that changes the plugin resets options to
    the new plugin's defaults — option keys from the old plugin would
    be stale against the new schema."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300", "layout": "1_cell"},
    )
    cell_id = _store(tmp_path).get("home").cells[0].id
    # Step 1: assign the plugin. Options are seeded from clock defaults.
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={"plugin": "clock", "x": "0", "y": "0", "w": "400", "h": "300"},
    )
    cell = _store(tmp_path).get("home").cells[0]
    assert cell.plugin == "clock"
    assert cell.options["format"] == "24h"
    assert cell.options["show_seconds"] is False
    # Step 2: edit an option. plugin unchanged -> options come from the form.
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={
            "plugin": "clock", "x": "0", "y": "0", "w": "400", "h": "300",
            "opt_show_seconds": "on", "opt_format": "12h",
        },
    )
    cell = _store(tmp_path).get("home").cells[0]
    assert cell.options["show_seconds"] is True
    assert cell.options["format"] == "12h"
    # Step 3: swap plugins -> clock's options shouldn't carry over.
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={"plugin": "year_progress", "x": "0", "y": "0", "w": "400", "h": "300"},
    )
    cell = _store(tmp_path).get("home").cells[0]
    assert cell.plugin == "year_progress"
    assert "show_seconds" not in cell.options
    assert "format" not in cell.options


def test_update_cell_clamps_out_of_bounds(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300", "layout": "1_cell"},
    )
    cell_id = _store(tmp_path).get("home").cells[0].id
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={"plugin": "clock", "x": "999", "y": "-50", "w": "5000", "h": "5000"},
    )
    cell = _store(tmp_path).get("home").cells[0]
    assert cell.x == 399
    assert cell.y == 0
    assert cell.w == 400
    assert cell.h == 300


def test_delete_cell(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300", "layout": "2_columns"},
    )
    cell_id = _store(tmp_path).get("home").cells[0].id
    client.post(f"/pages/home/cells/{cell_id}/delete")
    assert len(_store(tmp_path).get("home").cells) == 1


def test_cell_palette_overrides_round_trip(app: Flask, tmp_path: Path) -> None:
    """Overrides only land when the per-token checkbox is checked. The
    color input alone (every picker has SOME value) isn't enough."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/pages/new",
        data={"name": "Home", "panel_w": "400", "panel_h": "300", "layout": "1_cell"},
    )
    cell_id = _store(tmp_path).get("home").cells[0].id
    client.post(
        f"/pages/home/cells/{cell_id}",
        data={
            "plugin": "clock",
            "x": "0", "y": "0", "w": "100", "h": "80",
            "override_accent_enabled": "on",
            "override_accent": "#112233",
            # bg picker set but checkbox NOT checked — ignored.
            "override_bg": "#ffffff",
        },
    )
    cell = _store(tmp_path).get("home").cells[0]
    assert cell.palette_overrides == {"accent": "#112233"}


# -- /compose iframe access ----------------------------------------


def test_compose_preview_overlay_present(app: Flask) -> None:
    """preview=1 turns on the per-cell overlay (number tag + click shim)
    used by the editor iframe."""
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.get(
        "/compose/home?preview=1", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}
    )
    assert resp.status_code == 200
    assert b"cell-tag" in resp.data
    assert b"cell-click-shim" in resp.data


def test_compose_without_preview_has_no_overlay(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    resp = client.get(
        "/compose/home", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}
    )
    assert resp.status_code == 200
    assert b"cell-tag" not in resp.data
    assert b"cell-click-shim" not in resp.data


def test_compose_still_403s_from_lan_without_session(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/new", data={"name": "Home", "panel_w": "400", "panel_h": "300"})
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get("/compose/home", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 403


def test_nav_link_present(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "/pages" in body
