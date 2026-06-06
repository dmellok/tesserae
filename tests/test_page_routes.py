"""Editor (M15): page + cell CRUD with the layout / autosave model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import create_app
from app.state.page_store import PageStore


def _write_test_plugin(plugin_dir: Path, *, cell_options: list[dict[str, Any]]) -> None:
    """Drop a minimal widget plugin at ``plugin_dir`` for editor tests
    that only need a valid plugin id + cell_option defaults."""
    plugin_dir.mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": plugin_dir.name.replace("_", " ").title(),
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["sm", "md", "lg"]},
        "cell_options": cell_options,
    }
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
    (plugin_dir / "client.js").write_text("export default function () {}\n")


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    # Synthetic plugin sandbox: the editor tests need plugin ids that
    # exist + carry known cell-option defaults, but nothing renders here,
    # so we don't need the bundled widget set.
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write_test_plugin(
        plugins_dir / "widget_a",
        cell_options=[
            {"name": "format", "type": "string", "label": "Format", "default": "24h"},
            {"name": "show_date", "type": "boolean", "label": "Show date", "default": True},
            {"name": "show_seconds", "type": "boolean", "label": "Show seconds", "default": False},
        ],
    )
    _write_test_plugin(plugins_dir / "widget_b", cell_options=[])

    a = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=plugins_dir,
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _store(tmp_path: Path) -> PageStore:
    return PageStore(tmp_path / "data" / "core" / "pages.json")


def _new(client, **data: Any) -> str:
    """Create a dashboard, return its (random, opaque) id. Page ids are no
    longer derived from the name, so tests capture the id from the
    create redirect instead of guessing a slug."""
    resp = client.post("/pages/new", data=data or None, follow_redirects=False)
    assert resp.status_code in (302, 303), resp.status_code
    return resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


# -- /pages list + new -----------------------------------------------


def test_preview_returns_hydrated_state_per_panel(app: Flask, tmp_path: Path) -> None:
    """AJAX callers (the editor) get a JSON envelope back with the
    hydrated page state per requested panel size, so the iframe can
    patch in place rather than full-reload on every keystroke. Without
    the X-Requested-With header we keep the old flash-redirect shape so
    nothing existing breaks."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    from werkzeug.datastructures import MultiDict

    resp = client.post(
        f"/pages/{pid}/preview",
        data=MultiDict(
            [
                ("panels[]", "800x600"),
                ("panels[]", "400x300"),
            ]
        ),
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    groups = body["groups"]
    assert {(g["w"], g["h"]) for g in groups} == {(800, 600), (400, 300)}
    state = next(g["state"] for g in groups if g["w"] == 800)
    assert isinstance(state["cells"], list)
    assert len(state["cells"]) == 1
    assert state["cells"][0]["w"] == 800


def test_preview_falls_back_to_redirect_for_non_ajax(app: Flask, tmp_path: Path) -> None:
    """Plain-browser POSTs (no X-Requested-With) still flash + redirect
    so a stray direct hit doesn't 500 or leak JSON."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    resp = client.post(f"/pages/{pid}/preview", data={"name": "Home"})
    # _flash_save returns either redirect or 200 depending on the helper;
    # the important thing is the body isn't JSON.
    assert resp.content_type != "application/json"


def test_empty_list_renders_with_create_link(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/pages").get_data(as_text=True)
    assert "No dashboards yet" in body
    assert "/pages/new" in body


def _set_panel(app, w: int, h: int) -> None:
    """Force the panel dims for the test app. After M16 the page editor
    no longer takes panel_w/h directly, both come from settings."""
    settings = app.config["SETTINGS_STORE"]
    settings.update_section("app", {"panel_preset": "custom", "panel_w": w, "panel_h": h})


def test_create_page_with_default_layout(app: Flask, tmp_path: Path) -> None:
    """New pages create cells from a layout template at the settings-
    derived panel size. With the default '1_cell' layout, one full-panel
    unassigned cell appears."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    page = _store(tmp_path).get(pid)
    assert page is not None
    assert page.panel is None  # derived from settings
    assert len(page.cells) == 1
    assert page.cells[0].plugin is None
    assert (page.cells[0].x, page.cells[0].y, page.cells[0].w, page.cells[0].h) == (0, 0, 800, 600)


def test_create_page_with_2x2_grid_layout(app: Flask, tmp_path: Path) -> None:
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Grid", layout="2x2_grid")
    page = _store(tmp_path).get(pid)
    assert page is not None
    assert len(page.cells) == 4
    assert (page.cells[0].x, page.cells[0].y, page.cells[0].w, page.cells[0].h) == (0, 0, 400, 300)
    assert (page.cells[3].x, page.cells[3].y, page.cells[3].w, page.cells[3].h) == (
        400,
        300,
        400,
        300,
    )


def test_duplicate_names_get_distinct_random_ids(app: Flask, tmp_path: Path) -> None:
    """Ids are random + opaque (not name-derived, hidden from the UI), so
    two dashboards with the same name coexist with distinct ids, no slug
    collision handling needed."""
    client = app.test_client()
    _sign_in(client)
    a = _new(client, name="Home")
    b = _new(client, name="Home")
    assert a != b
    store = _store(tmp_path)
    assert {p.id for p in store.list()} == {a, b}
    assert all(p.name == "Home" for p in store.list())


# -- page edit -------------------------------------------------------


def test_update_page_metadata_autosave_json(app: Flask, tmp_path: Path) -> None:
    """Autosave clients POST with X-Requested-With: fetch and get JSON
    back. Panel dims aren't part of the page edit anymore, they come
    from settings."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home")
    resp = client.post(
        f"/pages/{pid}",
        data={
            "name": "Living room",
            "gap": "16",
            "corner_radius": "8",
            "bleed_color": "#202020",
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    assert resp.json == {"ok": True, "message": "Page saved."}
    page = _store(tmp_path).get(pid)
    assert page is not None
    assert page.name == "Living room"
    assert page.gap == 16


def test_update_is_a_merge_not_a_replace(app: Flask, tmp_path: Path) -> None:
    """The editor posts TWO forms to this endpoint: the header rename form
    (just ``name``) and the dashboard form (icon / device_ids / …). Each
    must touch only the fields it carries, or the rename wipes the
    device bindings and the dashboard's stale state reverts the rename.
    Regression for both reported editor bugs."""
    client = app.test_client()
    _sign_in(client)
    # Bind a device so we can prove device_ids survives a name-only save.
    client.post("/onboarding/device", data={"id": "esp32_lab", "kind": "esp32_client"})
    pid = _new(client, name="Home")

    # Dashboard-shaped save (no name; sentinel marks it owns device_ids).
    client.post(
        f"/pages/{pid}",
        data={
            "icon": "music-notes",
            "device_ids": ["esp32_lab"],
            "device_ids_present": "1",
        },
        headers={"X-Requested-With": "fetch"},
    )
    page = _store(tmp_path).get(pid)
    assert page.device_ids == ["esp32_lab"] and page.icon == "music-notes"

    # Name-only save (the header rename form) must NOT wipe icon/device.
    client.post(
        f"/pages/{pid}", data={"name": "Living room"}, headers={"X-Requested-With": "fetch"}
    )
    page = _store(tmp_path).get(pid)
    assert page.name == "Living room"  # rename applied
    assert page.icon == "music-notes"  # icon survives too
    assert page.device_ids == ["esp32_lab"]  # device binding survives too

    # Dashboard save (no name) must NOT revert the rename.
    client.post(
        f"/pages/{pid}",
        data={"device_ids": ["esp32_lab"], "device_ids_present": "1"},
        headers={"X-Requested-With": "fetch"},
    )
    assert _store(tmp_path).get(pid).name == "Living room"  # bug 2: rename sticks

    # Dashboard form with the sentinel + no checkboxes clears the binding.
    client.post(
        f"/pages/{pid}",
        data={"device_ids_present": "1"},
        headers={"X-Requested-With": "fetch"},
    )
    assert _store(tmp_path).get(pid).device_ids == []


def test_panel_change_in_settings_propagates_to_compose(app: Flask) -> None:
    """The whole point of moving panel into settings: changing it
    re-sizes every page's render without per-page edits."""
    client = app.test_client()
    _sign_in(client)
    _set_panel(app, 600, 400)
    pid = _new(client, name="Home")
    resp = client.get(f"/compose/{pid}", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert b"width: 600px;" in resp.data
    # Now resize the panel via settings.
    _set_panel(app, 1000, 700)
    resp = client.get(f"/compose/{pid}", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert b"width: 1000px;" in resp.data


def test_delete_page_removes_it(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Doomed")
    client.post(f"/pages/{pid}/delete")
    assert _store(tmp_path).get(pid) is None


# -- layout apply ---------------------------------------------------


def test_apply_layout_reuses_existing_cells(app: Flask, tmp_path: Path) -> None:
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    page = _store(tmp_path).get(pid)
    cell_id = page.cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "x": "0", "y": "0", "w": "800", "h": "600"},
    )
    client.post(f"/pages/{pid}/layout", data={"layout": "2x2_grid"})
    page = _store(tmp_path).get(pid)
    assert len(page.cells) == 4
    assert page.cells[0].plugin == "widget_a"
    assert (page.cells[0].x, page.cells[0].y, page.cells[0].w, page.cells[0].h) == (0, 0, 400, 300)
    assert all(c.plugin is None for c in page.cells[1:])


def test_apply_layout_drops_surplus_cells(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    client.post(f"/pages/{pid}/layout", data={"layout": "1_cell"})
    page = _store(tmp_path).get(pid)
    assert len(page.cells) == 1


def test_apply_unknown_layout_via_fetch_returns_json(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home")
    resp = client.post(
        f"/pages/{pid}/layout",
        data={"layout": "nope"},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    assert resp.json == {"ok": False, "message": "Unknown layout 'nope'."}


# -- cell CRUD ------------------------------------------------------


def test_add_empty_cell(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    # 1_cell default seeded one cell; add another empty.
    client.post(f"/pages/{pid}/cells")
    page = _store(tmp_path).get(pid)
    assert len(page.cells) == 2
    assert page.cells[-1].plugin is None


def test_assign_plugin_seeds_options(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "x": "0", "y": "0", "w": "400", "h": "300"},
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.plugin == "widget_a"
    assert cell.options.get("format") == "24h"
    assert cell.options.get("show_date") is True


def test_change_plugin_resets_options(app: Flask, tmp_path: Path) -> None:
    """In the editor, picking a plugin reloads the form so the new
    plugin's option fields are rendered before the user touches them.
    Server-side, any submit that changes the plugin resets options to
    the new plugin's defaults, option keys from the old plugin would
    be stale against the new schema."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    # Step 1: assign widget_a. Options are seeded from widget_a's defaults.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "x": "0", "y": "0", "w": "400", "h": "300"},
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.plugin == "widget_a"
    assert cell.options["format"] == "24h"
    assert cell.options["show_seconds"] is False
    # Step 2: edit an option. plugin unchanged -> options come from the form.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "x": "0",
            "y": "0",
            "w": "400",
            "h": "300",
            "opt_show_seconds": "on",
            "opt_format": "12h",
        },
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.options["show_seconds"] is True
    assert cell.options["format"] == "12h"
    # Step 3: swap to widget_b -> widget_a's options shouldn't carry over.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_b", "x": "0", "y": "0", "w": "400", "h": "300"},
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.plugin == "widget_b"
    assert "show_seconds" not in cell.options
    assert "format" not in cell.options


def test_update_cell_zoom_round_trips(app: Flask, tmp_path: Path) -> None:
    """Per-cell content zoom is persisted on the cell so the slider
    survives saves and rides through to the panel render."""
    _set_panel(app, 400, 300)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "zoom": "1.5"},
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.zoom == 1.5


def test_update_cell_zoom_clamps_to_model_bounds(app: Flask, tmp_path: Path) -> None:
    """The slider exposes 0.7–2.0; the persistence layer clamps the
    wider 0.5–3.0 envelope from _apply_cell_form so a wild explicit
    POST can't write a value the pydantic Cell rejects."""
    _set_panel(app, 400, 300)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a", "zoom": "10"})
    assert _store(tmp_path).get(pid).cells[0].zoom == 3.0
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a", "zoom": "0.1"})
    assert _store(tmp_path).get(pid).cells[0].zoom == 0.5


def test_update_cell_clamps_out_of_bounds(app: Flask, tmp_path: Path) -> None:
    _set_panel(app, 400, 300)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "x": "999", "y": "-50", "w": "5000", "h": "5000"},
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.x == 399
    assert cell.y == 0
    assert cell.w == 400
    assert cell.h == 300


def test_delete_cell(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2_columns")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(f"/pages/{pid}/cells/{cell_id}/delete")
    assert len(_store(tmp_path).get(pid).cells) == 1


# -- /compose iframe access ----------------------------------------


def test_compose_preview_overlay_present(app: Flask) -> None:
    """preview=1 turns on the per-cell overlay (number tag + click shim)
    used by the editor iframe."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home")
    resp = client.get(f"/compose/{pid}?preview=1", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 200
    assert b"cell-tag" in resp.data
    assert b"cell-click-shim" in resp.data


def test_compose_without_preview_has_no_overlay(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home")
    resp = client.get(f"/compose/{pid}", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 200
    assert b"cell-tag" not in resp.data
    assert b"cell-click-shim" not in resp.data


def test_compose_still_403s_from_lan_without_session(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home")
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get(f"/compose/{pid}", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
    assert resp.status_code == 403


def test_nav_link_present(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "/pages" in body


def test_page_icon_normalises_ph_prefix() -> None:
    """Phosphor names are stored bare; a stray 'ph-' prefix (legacy data
    or pasted value) is stripped so templates don't render 'ph-ph-x'."""
    from app.state.page_store import Page

    assert Page(id="a", name="A", icon="ph-house").icon == "house"
    assert Page(id="b", name="B", icon="house").icon == "house"
    assert Page(id="c", name="C", icon="ph-").icon is None
    assert Page(id="d", name="D", icon=None).icon is None


# -- device binding (multi-head) ------------------------------------


def test_page_device_id_migrates_to_device_ids() -> None:
    """Legacy pages stored a single ``device_id``; the model now keeps a
    ``device_ids`` list. A before-validator migrates old records on load
    so saved dashboards survive the schema change."""
    from app.state.page_store import Page

    legacy = Page.model_validate({"id": "a", "name": "A", "device_id": "pi_bin_lounge"})
    assert legacy.device_ids == ["pi_bin_lounge"]
    null_legacy = Page.model_validate({"id": "b", "name": "B", "device_id": None})
    assert null_legacy.device_ids == []
    fresh = Page.model_validate({"id": "c", "name": "C"})
    assert fresh.device_ids == []
    new_shape = Page.model_validate({"id": "d", "name": "D", "device_ids": ["x", "y"]})
    assert new_shape.device_ids == ["x", "y"]


def test_update_page_binds_multiple_devices_and_drops_unknown(app: Flask, tmp_path: Path) -> None:
    """The editor's device picker is now checkboxes (``device_ids``).
    Multiple ids are kept; unknown ids and built-in kinds are dropped so
    a page can't bind to something unbindable."""
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/device", data={"id": "esp32_lab", "kind": "esp32_client"})
    client.post("/onboarding/device", data={"id": "esp32_den", "kind": "esp32_client"})
    pid = _new(client, name="Home")
    resp = client.post(
        f"/pages/{pid}",
        data={
            "name": "Home",
            # unknown 'ghost' + built-in kind 'esp32_client' both dropped.
            "device_ids": ["esp32_lab", "esp32_den", "ghost", "esp32_client"],
        },
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    page = _store(tmp_path).get(pid)
    assert page is not None
    assert page.device_ids == ["esp32_lab", "esp32_den"]


def test_editor_shows_one_preview_per_aspect_and_checked_devices(app: Flask) -> None:
    """Binding two devices of differing aspect ratios renders a preview
    card per aspect, and the picker pre-checks the bound devices."""
    client = app.test_client()
    _sign_in(client)
    client.post("/onboarding/device", data={"id": "esp32_wide", "kind": "esp32_client"})
    client.post(
        "/onboarding/device",
        data={"id": "esp32_tall", "kind": "esp32_client", "panel_orientation": "portrait"},
    )
    pid = _new(client, name="Home")
    client.post(
        f"/pages/{pid}",
        data={"name": "Home", "device_ids": ["esp32_wide", "esp32_tall"]},
        headers={"X-Requested-With": "fetch"},
    )
    body = client.get(f"/pages/{pid}").get_data(as_text=True)
    # One landscape (800x480) + one portrait (480x800) preview = two cards.
    assert body.count('class="preview-frame"') == 2
    assert "w=800&amp;h=480" in body
    assert "w=480&amp;h=800" in body
    # Both devices are pre-checked checkboxes named device_ids.
    assert body.count('name="device_ids"') == 2
    assert body.count("checked") >= 2


def test_multiselect_cell_option_coercion() -> None:
    """A ``multiselect`` option normalises to a clean string list across the
    two form shapes: a Werkzeug MultiDict (persist) and the plain dict the
    preview demux builds (scalar for one pick, list for several)."""
    from werkzeug.datastructures import MultiDict

    from app.page_routes import _coerce_cell_option

    field = {"name": "entities", "type": "multiselect", "default": []}

    # Persist path, several checked boxes arrive as repeated keys.
    md = MultiDict([("opt_entities", "light.a"), ("opt_entities", "light.b")])
    assert _coerce_cell_option(field, md.get("opt_entities"), md) == ["light.a", "light.b"]

    # Preview path, demux already promoted >1 to a list.
    plain = {"opt_entities": ["light.a", "light.b"]}
    assert _coerce_cell_option(field, plain["opt_entities"], plain) == ["light.a", "light.b"]

    # Preview path, a single check stays scalar; absent / blank → empty.
    assert _coerce_cell_option(field, "light.a", {"opt_entities": "light.a"}) == ["light.a"]
    assert _coerce_cell_option(field, None, {}) == []
    assert _coerce_cell_option(field, "", {"opt_entities": ""}) == []
