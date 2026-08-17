"""Editor (M15): page + cell CRUD with the layout / autosave model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import create_app
from app.state.page_store import PageStore


def _write_test_plugin(
    plugin_dir: Path,
    *,
    cell_options: list[dict[str, Any]],
    updates: dict[str, Any] | None = None,
) -> None:
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
    if updates is not None:
        manifest["updates"] = updates
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
        updates={
            "on_change": [{"source": "test.clock", "selector_option": "format"}],
            "on_schedule": [{"kind": "daily", "suggested_at": "07:00"}],
        },
    )
    _write_test_plugin(plugins_dir / "widget_b", cell_options=[])
    _write_test_plugin(
        plugins_dir / "widget_c",
        cell_options=[
            {
                "name": "folder",
                "type": "select",
                "label": "Folder",
                "default": "",
                "choices": [{"value": "family", "label": "family (5)"}],
            }
        ],
    )

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


def test_bulk_delete_removes_only_selected(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    a = _new(client, name="A")
    b = _new(client, name="B")
    c = _new(client, name="C")
    client.post("/pages/bulk/delete", data={"page_ids": [a, b]})
    store = _store(tmp_path)
    assert store.get(a) is None and store.get(b) is None
    assert store.get(c) is not None  # unselected survives


# -- duplicate ------------------------------------------------------


def test_duplicate_page_creates_copy_with_suffixed_name(app: Flask, tmp_path: Path) -> None:
    """First duplicate of ``"Home"`` lands as ``"Home copy"`` with a fresh
    page id; the source row stays unchanged."""
    client = app.test_client()
    _sign_in(client)
    src_id = _new(client, name="Home")
    resp = client.post(f"/pages/{src_id}/duplicate", follow_redirects=False)
    assert resp.status_code in (302, 303)
    new_id = resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    assert new_id != src_id
    store = _store(tmp_path)
    copy = store.get(new_id)
    assert copy is not None
    assert copy.name == "Home copy"
    assert store.get(src_id) is not None and store.get(src_id).name == "Home"


def test_duplicate_page_collision_suffixes_with_number(app: Flask, tmp_path: Path) -> None:
    """A second duplicate of the same source lands as ``"Home copy 2"``,
    a third as ``"Home copy 3"``, etc."""
    client = app.test_client()
    _sign_in(client)
    src_id = _new(client, name="Home")
    for expected_name in ("Home copy", "Home copy 2", "Home copy 3"):
        resp = client.post(f"/pages/{src_id}/duplicate", follow_redirects=False)
        new_id = resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        assert _store(tmp_path).get(new_id).name == expected_name


def test_duplicate_of_a_duplicate_strips_trailing_copy_suffix(app: Flask, tmp_path: Path) -> None:
    """Duplicating ``"Home copy"`` lands on ``"Home copy 2"`` (Finder /
    Google Docs convention), not ``"Home copy copy"``. Same for
    ``"Home copy 2"`` → ``"Home copy 3"``."""
    client = app.test_client()
    _sign_in(client)
    src_id = _new(client, name="Home")
    chain = [src_id]
    for expected_name in ("Home copy", "Home copy 2", "Home copy 3"):
        resp = client.post(f"/pages/{chain[-1]}/duplicate", follow_redirects=False)
        new_id = resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
        assert _store(tmp_path).get(new_id).name == expected_name
        chain.append(new_id)


def test_duplicate_page_regenerates_cell_ids_so_edits_dont_bleed(
    app: Flask, tmp_path: Path
) -> None:
    """Every cell in the copy gets a fresh id so a cell-level update on
    the copy can't write back to the source's cell. Plugin + options on
    the cells are preserved."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    src_id = _new(client, name="Home", layout="1_cell")
    src_cell = _store(tmp_path).get(src_id).cells[0]
    client.post(
        f"/pages/{src_id}/cells/{src_cell.id}",
        data={"plugin": "widget_a", "x": "0", "y": "0", "w": "800", "h": "600"},
    )
    resp = client.post(f"/pages/{src_id}/duplicate", follow_redirects=False)
    new_id = resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]
    copy = _store(tmp_path).get(new_id)
    assert len(copy.cells) == 1
    assert copy.cells[0].plugin == "widget_a"
    assert copy.cells[0].id != src_cell.id


def test_duplicate_missing_page_redirects_with_flash(app: Flask, tmp_path: Path) -> None:
    """Duplicating an unknown id flashes an error and redirects to the
    list rather than 500-ing or returning 404."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/pages/does-not-exist/duplicate", follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert resp.headers["Location"].rstrip("/").endswith("/pages")


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


# -- per-cell padding override (v0.71.x) ---------------------------


def test_padding_override_saves_when_checkbox_ticked(app: Flask, tmp_path: Path) -> None:
    """Form parser: checkbox on + slider value → clamped int stored."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "padding_override_enabled": "1",
            "padding_override": "24",
        },
    )
    page = _store(tmp_path).get(pid)
    assert page.cells[0].padding_override == 24


def test_padding_override_clears_when_checkbox_off(app: Flask, tmp_path: Path) -> None:
    """Form parser: checkbox absent from the form-submitted subset
    resets the override back to None (inherit page gap), even when
    the slider value is still present in the form body."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    # Turn it on first.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "padding_override_enabled": "1",
            "padding_override": "12",
        },
    )
    assert _store(tmp_path).get(pid).cells[0].padding_override == 12
    # Turn it off (checkbox absent).
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "padding_override": "12",
        },
    )
    assert _store(tmp_path).get(pid).cells[0].padding_override is None


def test_padding_override_ignored_when_partial_form_lacks_both_fields(
    app: Flask, tmp_path: Path
) -> None:
    """A partial-form autosave (e.g. only ``x/y/w/h`` from the layout
    editor) mustn't clobber the padding override the user set on the
    cell edit form."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "padding_override_enabled": "1",
            "padding_override": "16",
        },
    )
    # Partial POST with neither field present.
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a"})
    assert _store(tmp_path).get(pid).cells[0].padding_override == 16


def test_dither_override_saves_selected_mode(app: Flask, tmp_path: Path) -> None:
    """Advanced pane (issue #86): checkbox on + a mode → stored on the cell."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "dither_override_enabled": "1", "dither_mode": "none"},
    )
    assert _store(tmp_path).get(pid).cells[0].dither == "none"
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "dither_override_enabled": "1", "dither_mode": "auto"},
    )
    assert _store(tmp_path).get(pid).cells[0].dither == "auto"


def test_dither_override_clears_when_checkbox_off(app: Flask, tmp_path: Path) -> None:
    """Switch off (checkbox absent, select still present) resets to None so
    the cell falls back to the widget's manifest dither hint."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "dither_override_enabled": "1", "dither_mode": "none"},
    )
    assert _store(tmp_path).get(pid).cells[0].dither == "none"
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "dither_mode": "none"},  # checkbox absent
    )
    assert _store(tmp_path).get(pid).cells[0].dither is None


def test_dither_override_ignored_when_partial_form_lacks_both_fields(
    app: Flask, tmp_path: Path
) -> None:
    """A partial-form autosave (geometry only) must not wipe the override."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "dither_override_enabled": "1", "dither_mode": "auto"},
    )
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a"})
    assert _store(tmp_path).get(pid).cells[0].dither == "auto"


# -- status bar toggle (v0.71.0) -----------------------------------


def test_status_bar_toggle_hides_native_checkbox_behind_switch(app: Flask, tmp_path: Path) -> None:
    """The shared field--switch styles hide the native checkbox so the
    custom track is the section's only visible toggle control."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")

    body = client.get(f"/pages/{pid}").get_data(as_text=True)

    assert body.count('name="status_bar_enabled"') == 1
    assert 'class="form form--inline dx-status-bar-switch field--switch"' in body


def test_status_bar_toggle_returns_to_the_editor_without_a_referer(
    app: Flask, tmp_path: Path
) -> None:
    """#197: the toggle posts natively, and the redirect used to follow
    ``Referer``. Behind a proxy that strips it (HA ingress) that landed the
    user on the dashboard LIST, so flipping the switch looked like it had
    thrown the editing session away. The redirect names the page instead."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")

    resp = client.post(f"/pages/{pid}/status-bar/toggle", data={})

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(f"/pages/{pid}")
    assert _store(tmp_path).get(pid).status_bar_enabled is True


def test_status_bar_toggle_inserts_cell_and_rescales(app: Flask, tmp_path: Path) -> None:
    """Flipping the status-bar switch on prepends a tesserae_status cell
    at (0, 0, panel.w, 48) and shifts + rescales existing cells into
    the (bar_h, panel.h) band so nothing overlaps."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    before = _store(tmp_path).get(pid)
    assert len(before.cells) == 4
    assert before.status_bar_enabled is False

    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    page = _store(tmp_path).get(pid)
    assert page.status_bar_enabled is True
    assert page.status_bar_cell_id is not None
    assert len(page.cells) == 5

    bar = page.cells[0]
    assert bar.id == page.status_bar_cell_id
    assert bar.plugin == "tesserae_status"
    assert (bar.x, bar.y, bar.w, bar.h) == (0, 0, 800, 48)
    assert bar.options["show_temperature"] is True
    assert bar.options["show_humidity"] is True
    assert bar.options["units"] == "metric"
    # Existing top-row cells rescaled into (48, 600), so their y starts
    # at 48 rather than 0, and their h shrinks by the (600-48)/600 ratio.
    top = [c for c in page.cells if c.plugin != "tesserae_status" and c.y < 300]
    assert top, "expected at least one top-row cell after rescale"
    assert all(c.y >= 48 for c in top)
    # Panel-full: bar top + rescaled cells reach the bottom of the panel.
    assert max(c.y + c.h for c in page.cells) <= 600


def test_status_bar_toggle_off_removes_cell_and_fills_vacated_space(
    app: Flask, tmp_path: Path
) -> None:
    """Flipping off removes the auto-managed cell and refits the
    remaining cells to the full panel, so the space the bar occupied
    is absorbed by the other widgets rather than left as a top strip
    of matting. The refit uses fit_cells_to_panel, so it's tolerant
    of layout edits made while the bar was on."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")

    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    client.post(f"/pages/{pid}/status-bar/toggle", data={})

    page = _store(tmp_path).get(pid)
    assert page.status_bar_enabled is False
    assert page.status_bar_cell_id is None
    assert len(page.cells) == 4
    # The refit fills the panel: the top row starts at y=0 and the
    # bottom row ends at (or very near) panel.h. No leftover strip.
    assert min(c.y for c in page.cells) == 0
    assert max(c.y + c.h for c in page.cells) >= 600 - 2


def test_status_bar_toggle_preserved_across_layout_change(app: Flask, tmp_path: Path) -> None:
    """Switching layout presets while the status bar is on keeps the
    bar cell at row 0 rather than remapping it into the preset's first
    slot."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    page = _store(tmp_path).get(pid)
    bar_id = page.status_bar_cell_id

    client.post(f"/pages/{pid}/layout", data={"layout": "2x2_grid"})
    page = _store(tmp_path).get(pid)
    assert page.status_bar_enabled is True
    assert page.status_bar_cell_id == bar_id
    assert page.cells[0].id == bar_id
    assert page.cells[0].plugin == "tesserae_status"
    # Four cells from the 2x2 preset, rescaled beneath the bar.
    assert len(page.cells) == 5
    for c in page.cells[1:]:
        assert c.y >= 48


def test_status_bar_cell_grows_with_gap_so_padding_fits(app: Flask, tmp_path: Path) -> None:
    """v0.71.x: when a page has a non-zero gap, the auto-inserted
    status bar cell's h grows by ``outer_pad + inner_pad`` so the
    composer's gap padding paints matting on all four sides of the
    bar without eating into the widget's usable content area."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    # Set a page-level gap; combined-endpoint autosave writes ``gap``
    # into the page metadata.
    client.post(f"/pages/{pid}", data={"gap": "40"})
    assert _store(tmp_path).get(pid).gap == 40

    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    page = _store(tmp_path).get(pid)
    bar = page.cells[0]
    # gap=40 -> outer_pad=20, inner_pad=10 -> bar h = 48+20+10 = 78.
    assert bar.h == 78
    # Cells below start at the bar's bottom, not just past 48 px.
    for c in page.cells[1:]:
        assert c.y >= 78


def test_status_bar_refuses_on_very_short_panel(app: Flask, tmp_path: Path) -> None:
    """A panel that's too short can't fit a 48 px bar + at least
    STATUS_BAR_MIN_REMAINING_PX for the rest of the dashboard. Toggle
    endpoint refuses rather than crushing the layout."""
    _set_panel(app, 200, 120)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    page = _store(tmp_path).get(pid)
    assert page.status_bar_enabled is False


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


def test_update_on_change_is_opt_in_and_resets_on_widget_change(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id

    # Assigning a capable widget retains the compatibility default: off.
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a"})
    assert _store(tmp_path).get(pid).cells[0].update_on_change is False

    # The complete editor form can enable and explicitly disable the policy.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "update_on_change_present": "1",
            "update_on_change": "1",
        },
    )
    assert _store(tmp_path).get(pid).cells[0].update_on_change is True
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={"plugin": "widget_a", "update_on_change_present": "1"},
    )
    assert _store(tmp_path).get(pid).cells[0].update_on_change is False

    # Partial autosaves do not carry the checkbox marker and must preserve it.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "update_on_change_present": "1",
            "update_on_change": "1",
        },
    )
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a", "zoom": "1.2"})
    assert _store(tmp_path).get(pid).cells[0].update_on_change is True

    # A different widget owns a different dependency and always starts off.
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_b"})
    assert _store(tmp_path).get(pid).cells[0].update_on_change is False


def test_grid_editor_only_shows_update_switch_for_capable_widget(
    app: Flask, tmp_path: Path
) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id

    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a"})
    html = client.get(f"/pages/{pid}").get_data(as_text=True)
    assert 'name="update_on_change"' in html

    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_b"})
    html = client.get(f"/pages/{pid}").get_data(as_text=True)
    assert 'name="update_on_change"' not in html


def test_update_schedule_defaults_to_boundary_and_preserves_partial_posts(
    app: Flask, tmp_path: Path
) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a"})

    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "update_schedule_present": "1",
            "update_schedule_enabled": "1",
            "update_schedule_timing": "boundary",
            "update_schedule_at": "07:00",
        },
    )
    schedule = _store(tmp_path).get(pid).cells[0].update_schedule
    assert schedule is not None and schedule.at is None

    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a", "zoom": "1.2"})
    schedule = _store(tmp_path).get(pid).cells[0].update_schedule
    assert schedule is not None and schedule.at is None

    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "update_schedule_present": "1",
            "update_schedule_enabled": "1",
            "update_schedule_timing": "custom",
            "update_schedule_at": "08:30",
        },
    )
    schedule = _store(tmp_path).get(pid).cells[0].update_schedule
    assert schedule is not None and schedule.at == "08:30"

    # Clearing a custom time falls back to the day boundary without rejecting
    # the rest of the cell form.
    client.post(
        f"/pages/{pid}/cells/{cell_id}",
        data={
            "plugin": "widget_a",
            "zoom": "1.4",
            "update_schedule_present": "1",
            "update_schedule_enabled": "1",
            "update_schedule_timing": "custom",
            "update_schedule_at": "",
        },
    )
    cell = _store(tmp_path).get(pid).cells[0]
    assert cell.zoom == 1.4
    assert cell.update_schedule is not None and cell.update_schedule.at is None

    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_b"})
    assert _store(tmp_path).get(pid).cells[0].update_schedule is None


def test_grid_editor_only_shows_schedule_for_capable_widget(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id

    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_a"})
    html = client.get(f"/pages/{pid}").get_data(as_text=True)
    assert 'name="update_schedule_enabled"' in html
    assert 'value="07:00"' in html

    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_b"})
    html = client.get(f"/pages/{pid}").get_data(as_text=True)
    assert 'name="update_schedule_enabled"' not in html


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


def test_compose_preview_cells_are_drag_swappable(app: Flask, tmp_path: Path) -> None:
    """The preview overlay is the drag surface for reordering widgets
    (discussion #198): every non-status-bar cell is marked swappable and the
    drag script posts 'tesserae-cell-swap' up to the editor."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    body = client.get(
        f"/compose/{pid}?preview=1", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}
    ).get_data(as_text=True)
    assert body.count('data-swappable="1"') == 4
    assert body.count("is-draggable") >= 4
    assert "tesserae-cell-swap" in body


def test_compose_preview_excludes_the_status_bar_from_dragging(app: Flask) -> None:
    """The status bar is pinned and the server refuses to swap it, so it is
    neither a drag source nor a drop target."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    body = client.get(
        f"/compose/{pid}?preview=1", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}
    ).get_data(as_text=True)
    # Five cells now, but the bar isn't one of the swappable ones.
    assert body.count("cell-click-shim") >= 5
    assert body.count('data-swappable="1"') == 4


def test_push_render_carries_no_drag_code(app: Flask) -> None:
    """The drag layer is editor-only. A push render must not ship it: it would
    be dead weight in every frame and the headless renderer would screenshot
    the outlines."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    body = client.get(
        f"/compose/{pid}?for_push=1", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}
    ).get_data(as_text=True)
    assert "tesserae-cell-swap" not in body
    assert "data-swappable" not in body
    assert "swap-ghost" not in body


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


def _render_multiselect(app: Flask, value: Any, options: list[dict[str, str]]) -> str:
    with app.app_context():
        tmpl = app.jinja_env.from_string(
            "{% from '_components.html' import multiselect_field %}"
            "{{ multiselect_field('f', 'entities', 'Entities', value=value, options=options) }}"
        )
        return tmpl.render(value=value, options=options)


def _multiselect_checkbox_order(app: Flask, value: Any, options: list[dict[str, str]]) -> list[str]:
    """Render the ``multiselect_field`` macro and pull the checkbox
    values back out in DOM order (which is the order they'd submit in)."""
    import re

    html = _render_multiselect(app, value, options)
    return re.findall(r'type="checkbox"[^>]*value="([^"]*)"', html)


def test_multiselect_renders_saved_selection_first_in_saved_order(app: Flask) -> None:
    """Regression for #94: the editor must render the checked options in
    the saved (``value``) order ahead of the unchecked ones, so a user's
    drag-reordered selection round-trips instead of reverting to the raw
    ``choices()`` order. Without this, saving any cell (e.g. a theme
    change fans out a save of every cell) rewrites the entity order to
    the default arrangement and wipes the user's ordering."""
    # choices() hands them back in a fixed (alphabetical) order...
    options = [
        {"value": "light.a", "label": "A"},
        {"value": "light.b", "label": "B"},
        {"value": "light.c", "label": "C"},
    ]
    # ...but the user saved a custom order with the middle one first.
    order = _multiselect_checkbox_order(app, ["light.c", "light.a"], options)
    # Checked entries lead, in saved order; the unchecked one trails.
    assert order == ["light.c", "light.a", "light.b"]


def test_multiselect_tolerates_bare_string_and_missing_value(app: Flask) -> None:
    """The macro accepts a bare-string or empty ``value`` (unset option)
    without dropping or reordering the unchecked choices."""
    options = [
        {"value": "light.a", "label": "A"},
        {"value": "light.b", "label": "B"},
    ]
    assert _multiselect_checkbox_order(app, "light.b", options) == ["light.b", "light.a"]
    assert _multiselect_checkbox_order(app, None, options) == ["light.a", "light.b"]


def test_multiselect_renders_accessible_reorder_handles(app: Flask) -> None:
    html = _render_multiselect(
        app,
        ["light.b", "light.a"],
        [
            {"value": "light.a", "label": "Desk"},
            {"value": "light.b", "label": "Hallway"},
        ],
    )

    assert html.count('class="multiselect-drag"') == 2
    assert html.count('draggable="true"') == 2
    assert 'aria-label="Reorder Hallway"' in html
    assert 'aria-keyshortcuts="ArrowUp ArrowDown Home End"' in html
    assert 'data-ms-order-status aria-live="polite"' in html


def test_multiselect_component_wires_mouse_touch_and_keyboard_reordering() -> None:
    source = (Path(__file__).parents[1] / "static" / "components.js").read_text()
    styles = (Path(__file__).parents[1] / "static" / "style" / "forms.css").read_text()

    assert 'handle.addEventListener("dragstart"' in source
    assert 'list.addEventListener("drop"' in source
    assert 'handle.addEventListener("pointermove"' in source
    assert 'handle.addEventListener("keydown"' in source
    assert 'new Event("input", { bubbles: true })' in source
    assert ".multiselect-drag .ph" in styles
    assert "radial-gradient(circle, currentColor 1.5px" in styles
    assert ".multiselect-opt:has(input:checked) + .multiselect-opt:has(input:checked)" in styles
    assert "border-top-color: transparent" in styles
    drag_button = styles.split(".multiselect-drag {", 1)[1].split("}", 1)[0]
    assert "border-left" not in drag_button
    assert "box-shadow: none" in drag_button
    assert "transform: none" in drag_button


def test_select_with_unmatched_value_does_not_visually_choose_first_option(
    app: Flask, tmp_path: Path
) -> None:
    """An empty dynamic option must stay visibly empty instead of looking
    like the browser's first available choice is already saved.

    This is the Gallery failure mode: a newly selected widget stores
    ``folder=""`` (root), while the editor used to display ``family`` because
    it was the first runtime choice. The preview still rendered root, and the
    next unrelated save silently changed the folder to family.
    """
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Gallery", layout="1_cell")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    client.post(f"/pages/{pid}/cells/{cell_id}", data={"plugin": "widget_c"})

    body = client.get(f"/pages/{pid}").get_data(as_text=True)

    assert '<option value="" selected data-unmatched-option>(not set)</option>' in body
    assert '<option value="family" selected' not in body
    assert '<option value="family" >family (5)</option>' in body


def test_select_with_removed_value_preserves_and_labels_it(app: Flask) -> None:
    """A choice that disappeared from a dynamic source remains the selected
    submitted value until the user deliberately picks a replacement."""
    with app.app_context():
        tmpl = app.jinja_env.from_string(
            "{% from '_components.html' import select_field %}"
            "{{ select_field('f', 'entity', 'Entity', value=value, options=options) }}"
        )
        html = tmpl.render(
            value="sensor.removed",
            options=[{"value": "sensor.current", "label": "Current"}],
        )

    assert (
        '<option value="sensor.removed" selected data-unmatched-option>'
        "Unavailable: sensor.removed</option>"
    ) in html
    assert '<option value="sensor.current" selected' not in html


def _render_select(app: Flask, **kwargs: object) -> str:
    with app.app_context():
        tmpl = app.jinja_env.from_string(
            "{% from '_components.html' import select_field %}"
            "{{ select_field('f', 'n', 'L', value=value, options=options,"
            " blank_label=blank) }}"
        )
        return tmpl.render({"blank": None, **kwargs})


def test_select_matches_choice_of_a_different_type(app: Flask) -> None:
    """A value stored as a string still selects its numeric choice, rather
    than rendering an 'Unavailable' duplicate of an option that's right
    there in the list (kind defaults hold integer sleep intervals)."""
    html = _render_select(
        app,
        value="300",
        options=[{"value": 60, "label": "1 minute"}, {"value": 300, "label": "5 minutes"}],
    )

    assert "data-unmatched-option" not in html
    assert '<option value="300" selected>5 minutes</option>' in html


def test_select_with_blank_label_marks_only_the_unmatched_option(app: Flask) -> None:
    """A picker that offers its own blank choice must not end up with two
    selected options when the saved value is missing from the list; browsers
    honour the last one, which would silently move the value."""
    html = _render_select(
        app,
        value="sensor.removed",
        options=[{"value": "sensor.current", "label": "Current"}],
        blank="(default)",
    )

    assert html.count("selected") == 1
    assert '<option value="" >(default)</option>' in html
    assert (
        '<option value="sensor.removed" selected data-unmatched-option>'
        "Unavailable: sensor.removed</option>"
    ) in html


def test_select_with_blank_label_keeps_blank_selected_when_unset(app: Flask) -> None:
    """The caller's own blank option still covers the empty case, so no
    synthetic '(not set)' entry is added alongside it."""
    html = _render_select(
        app,
        value="",
        options=[{"value": "a", "label": "A"}],
        blank="(default)",
    )

    assert "data-unmatched-option" not in html
    assert '<option value="" selected>(default)</option>' in html


def test_select_accepts_tuple_options(app: Flask) -> None:
    """The (value, label) tuple form takes the same unmatched treatment as
    the dict form."""
    html = _render_select(app, value="zz", options=[("a", "A"), ("b", "B")])

    assert ('<option value="zz" selected data-unmatched-option>Unavailable: zz</option>') in html
    assert html.count("selected") == 1


# -- _ensure_cells_fit_panel ------------------------------------------------


def _stub_panel(w: int, h: int):
    """Minimal duck-typed Panel for _ensure_cells_fit_panel."""

    class _P:
        pass

    p = _P()
    p.w = w
    p.h = h
    return p


def _make_page(cells: list[tuple[int, int, int, int]]):
    """Stand-alone Page object with stub plugin-less cells."""
    from app.state.page_store import Cell, Page

    return Page(
        id="p",
        name="P",
        cells=[
            Cell(id=f"c{i}", plugin=None, x=x, y=y, w=w, h=h)
            for i, (x, y, w, h) in enumerate(cells)
        ],
    )


def test_ensure_fit_leaves_cells_alone_when_in_bounds_same_orientation(app: Flask) -> None:
    """The most common multi-device case: bind a new device with a
    different aspect ratio but the same orientation, the existing cells
    fit within the new panel's bounds. Saved coords must NOT change —
    non-uniform rescaling here is what destroys edge alignment and
    "garbles" the layout."""
    from app.page_routes import _ensure_cells_fit_panel

    page = _make_page([(0, 0, 400, 240), (400, 0, 400, 240), (0, 240, 800, 240)])
    larger_landscape = _stub_panel(1024, 768)  # same landscape, different aspect
    with app.app_context():
        out = _ensure_cells_fit_panel(page, larger_landscape)
    assert [(c.x, c.y, c.w, c.h) for c in out.cells] == [
        (0, 0, 400, 240),
        (400, 0, 400, 240),
        (0, 240, 800, 240),
    ]


def test_ensure_fit_rescales_when_cells_overflow(app: Flask) -> None:
    """If any cell's right or bottom edge is past the new panel, the
    layout genuinely doesn't fit — rescale is required."""
    from app.page_routes import _ensure_cells_fit_panel

    page = _make_page([(0, 0, 800, 480), (200, 100, 700, 400)])
    smaller = _stub_panel(400, 300)
    with app.app_context():
        out = _ensure_cells_fit_panel(page, smaller)
    # All cells now within smaller panel bounds.
    for c in out.cells:
        assert c.x + c.w <= 400
        assert c.y + c.h <= 300


def test_ensure_fit_rescales_on_orientation_flip(app: Flask) -> None:
    """Landscape design → portrait panel needs a 90° rotation; saved
    coords are otherwise nonsensical."""
    from app.page_routes import _ensure_cells_fit_panel

    page = _make_page([(0, 0, 400, 240), (400, 0, 400, 240)])  # landscape
    portrait = _stub_panel(480, 800)
    with app.app_context():
        out = _ensure_cells_fit_panel(page, portrait)
    # Shouldn't be identical (rotation happened), all in bounds.
    coords_after = [(c.x, c.y, c.w, c.h) for c in out.cells]
    assert coords_after != [(0, 0, 400, 240), (400, 0, 400, 240)]
    for c in out.cells:
        assert c.x + c.w <= 480
        assert c.y + c.h <= 800


# -- pages-list grouping by device ----------------------------------


class _StubDevice:
    """Minimal duck-typed stand-in for ``app.device_loader.Device`` for
    the grouping helper's purposes: it only reads ``id``, ``display_name``
    and ``icon``."""

    def __init__(self, did: str, *, display_name: str, icon: str = "monitor") -> None:
        self.id = did
        self.display_name = display_name
        self.icon = icon


class _StubRegistry:
    def __init__(self, devices: list[_StubDevice]) -> None:
        self.devices = {d.id: d for d in devices}


def _page(pid: str, name: str, device_ids: list[str] | None = None):
    from app.state.page_store import Page

    return Page(id=pid, name=name, device_ids=device_ids or [], cells=[])


def test_group_pages_sorts_by_device_then_name() -> None:
    """Two devices with multiple pages each: device groups come out
    alphabetical by ``display_name``, and pages inside each group are
    case-insensitive alphabetical by ``name``."""
    from app.page_routes import _group_pages_for_index

    kitchen = _StubDevice("kitchen", display_name="Kitchen Panel")
    studio = _StubDevice("studio", display_name="Studio Display")
    registry = _StubRegistry([kitchen, studio])
    pages = [
        _page("s2", "Sunset board", ["studio"]),
        _page("k2", "Pantry list", ["kitchen"]),
        _page("k1", "Calendar", ["kitchen"]),
        _page("s1", "Morning briefing", ["studio"]),
    ]
    groups = _group_pages_for_index(pages, registry)
    assert [d.id for d, _ in groups] == ["kitchen", "studio"]
    assert [p.id for p in groups[0][1]] == ["k1", "k2"]
    assert [p.id for p in groups[1][1]] == ["s1", "s2"]


def test_group_pages_unbound_sorts_last() -> None:
    """An unbound page (no device_ids) lands in a None-keyed Unbound
    group that always sits after every bound device group."""
    from app.page_routes import _group_pages_for_index

    kitchen = _StubDevice("kitchen", display_name="Kitchen Panel")
    registry = _StubRegistry([kitchen])
    pages = [
        _page("u1", "Floating dashboard", []),
        _page("k1", "Calendar", ["kitchen"]),
    ]
    groups = _group_pages_for_index(pages, registry)
    assert len(groups) == 2
    assert groups[0][0].id == "kitchen"
    assert groups[1][0] is None
    assert [p.id for p in groups[1][1]] == ["u1"]


def test_group_pages_multi_device_binding_appears_under_every_device() -> None:
    """v0.69.6 (issue #52 item 1): a page bound to N live devices renders
    under each of their section heads, not just the first. Previously
    (pre-v0.69.6) the helper picked the first live device as a "primary"
    and hid the page from the others' groups, so a dashboard bound to
    "living-room" + "kitchen" only surfaced from one of them."""
    from app.page_routes import _group_pages_for_index

    kitchen = _StubDevice("kitchen", display_name="Kitchen Panel")
    studio = _StubDevice("studio", display_name="Studio Display")
    registry = _StubRegistry([kitchen, studio])
    pages = [_page("shared", "Family agenda", ["kitchen", "studio"])]
    groups = _group_pages_for_index(pages, registry)
    assert [d.id for d, _ in groups] == ["kitchen", "studio"]
    # Same page appears under both device groups.
    assert [p.id for p in groups[0][1]] == ["shared"]
    assert [p.id for p in groups[1][1]] == ["shared"]


def test_group_pages_primary_device_deletion_falls_through() -> None:
    """When a page's first device id no longer resolves, the helper
    walks to the next device in the list. The page lands in the group
    of its first live device, NOT Unbound."""
    from app.page_routes import _group_pages_for_index

    kitchen = _StubDevice("kitchen", display_name="Kitchen Panel")
    registry = _StubRegistry([kitchen])
    pages = [_page("p1", "Recipes", ["ghost", "kitchen"])]
    groups = _group_pages_for_index(pages, registry)
    assert len(groups) == 1
    assert groups[0][0].id == "kitchen"


def test_group_pages_all_devices_missing_falls_to_unbound() -> None:
    """A page bound only to deleted devices falls all the way through
    to Unbound rather than rendering against a phantom device."""
    from app.page_routes import _group_pages_for_index

    registry = _StubRegistry([])
    pages = [_page("p1", "Recipes", ["ghost-a", "ghost-b"])]
    groups = _group_pages_for_index(pages, registry)
    assert len(groups) == 1
    assert groups[0][0] is None
    assert [p.id for p in groups[0][1]] == ["p1"]


def test_group_pages_no_device_registry_treats_everything_as_unbound() -> None:
    """``devices=None`` (bare/test boot without a devices/ dir): every
    page falls through to Unbound rather than crashing on a missing
    registry attribute."""
    from app.page_routes import _group_pages_for_index

    pages = [_page("p1", "A", ["k1"]), _page("p2", "B", [])]
    groups = _group_pages_for_index(pages, devices=None)
    assert len(groups) == 1
    assert groups[0][0] is None
    assert [p.id for p in groups[0][1]] == ["p1", "p2"]


def test_materialize_cell_options_surfaces_re_enter_sentinel_on_secretbox_error() -> None:
    """v0.64.24 regression target (issue #29).

    When a widget's ``choices()`` function raises ``SecretBoxError``
    (because the plugin holds a ``_secret``-suffixed setting whose
    on-disk ciphertext can't be decrypted with the current SecretBox
    key, typically: ``TESSERAE_SECRET_KEY`` env var was set or
    changed across container restarts, or the data volume was
    restored from a backup), the editor used to swallow the
    exception and render the picker empty. The empty picker was
    impossible to diagnose from the UI.

    The current behaviour surfaces a single sentinel choice with a
    label pointing the user at where to re-enter the secret. Other
    exception types still fall through to the empty-list path so a
    transient network error in a different plugin doesn't shout
    ``re-enter the secret`` at the user."""
    from types import SimpleNamespace

    from app.page_routes import _materialize_cell_options
    from app.secret_box import SecretBoxError

    def boom_choices(name: str) -> Any:
        raise SecretBoxError("AES-GCM authentication failed: simulated")

    plugin = SimpleNamespace(
        id="ha_zones",
        manifest={
            "name": "Home Assistant Zones",
            "cell_options": [{"name": "entity", "type": "select", "choices_from": "entity"}],
        },
        server_module=SimpleNamespace(choices=boom_choices),
    )

    out = _materialize_cell_options([plugin])

    choices = out["ha_zones"][0]["choices"]
    assert len(choices) == 1, choices
    assert choices[0]["value"] == ""
    assert "Home Assistant Zones" in choices[0]["label"]
    assert "re-enter" in choices[0]["label"].lower()
    assert "Settings" in choices[0]["label"]


def test_materialize_cell_options_keeps_empty_fallback_for_unrelated_errors() -> None:
    """A network timeout or any non-SecretBoxError still gets the
    bare empty-list treatment, so the editor doesn't mis-blame
    decryption when the real issue is upstream (HA is offline, the
    plugin's HTTP request timed out, etc.). Catches the
    too-eager-blanket-catch regression."""
    from types import SimpleNamespace

    from app.page_routes import _materialize_cell_options

    def timeout_choices(name: str) -> Any:
        raise TimeoutError("HA reachable check timed out")

    plugin = SimpleNamespace(
        id="ha_zones",
        manifest={
            "name": "Home Assistant Zones",
            "cell_options": [{"name": "entity", "type": "select", "choices_from": "entity"}],
        },
        server_module=SimpleNamespace(choices=timeout_choices),
    )

    out = _materialize_cell_options([plugin])

    assert out["ha_zones"][0]["choices"] == []


# -- freeform (canvas) dashboards integration (issue #60) ----------------


def test_create_freeform_dashboard_redirects_to_composer(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/pages/new", data={"name": "Wall", "layout_kind": "canvas"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert "/pages/canvas/c/" in resp.location
    pid = resp.location.rsplit("/", 1)[1]
    page = app.config["PAGE_STORE"].get(pid)
    assert page is not None and page.layout_kind == "canvas" and page.canvas is not None


def test_edit_canvas_page_redirects_to_composer(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout

    app.config["PAGE_STORE"].save(
        Page(id="cv", name="C", layout_kind="canvas", canvas=CanvasLayout())
    )
    resp = client.get("/pages/cv", follow_redirects=False)
    assert resp.status_code == 302 and resp.location.endswith("/pages/canvas/c/cv")


def test_dashboards_list_shows_freeform_badge_and_choice(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    from app.state.page_store import Page
    from app.state.panel_store import CanvasLayout

    app.config["PAGE_STORE"].save(
        Page(id="cv", name="Corner", layout_kind="canvas", canvas=CanvasLayout())
    )
    body = client.get("/pages").get_data(as_text=True)
    assert "Corner" in body and "Freeform" in body
    assert 'name="layout_kind"' in body  # create form offers Grid / Freeform


def test_swap_cells_exchanges_contents_not_boxes(app: Flask, tmp_path: Path) -> None:
    """Dragging one cell card onto another swaps the widgets while both
    boxes stay put (discussion #198). Geometry and cell ids belong to the
    slot, so list order, the status-bar index and preset detection are all
    unaffected by a move."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    page = _store(tmp_path).get(pid)
    first, last = page.cells[0], page.cells[3]
    ids_before = [c.id for c in page.cells]
    # Assign, then configure: a plugin change resets options to the new
    # widget's defaults, so the option has to land on a second post.
    client.post(f"/pages/{pid}/cells/{first.id}", data={"plugin": "widget_a"})
    client.post(
        f"/pages/{pid}/cells/{first.id}",
        data={"plugin": "widget_a", "opt_format": "12h"},
    )
    client.post(f"/pages/{pid}/cells/{last.id}", data={"plugin": "widget_b"})

    resp = client.post(
        f"/pages/{pid}/cells/{first.id}/swap",
        json={"target": last.id},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True

    page = _store(tmp_path).get(pid)
    assert [c.id for c in page.cells] == ids_before  # ids + list order untouched
    assert page.cells[0].plugin == "widget_b"
    assert page.cells[3].plugin == "widget_a"
    # The widget's own settings travel with it.
    assert page.cells[3].options.get("format") == "12h"
    # Boxes never move.
    assert (page.cells[0].x, page.cells[0].y) == (0, 0)
    assert (page.cells[3].x, page.cells[3].y) == (400, 300)


def test_swap_cells_refuses_the_status_bar(app: Flask, tmp_path: Path) -> None:
    """``status_bar_cell_id`` names one specific cell; swapping its contents
    would leave the page claiming a status bar that renders something else."""
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    client.post(f"/pages/{pid}/status-bar/toggle", data={})
    page = _store(tmp_path).get(pid)
    assert page.status_bar_cell_id == page.cells[0].id

    resp = client.post(
        f"/pages/{pid}/cells/{page.cells[0].id}/swap",
        json={"target": page.cells[1].id},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.get_json()["ok"] is False
    assert "status bar" in resp.get_json()["message"].lower()


def test_swap_cells_rejects_an_unknown_target(app: Flask, tmp_path: Path) -> None:
    _set_panel(app, 800, 600)
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    cell_id = _store(tmp_path).get(pid).cells[0].id
    resp = client.post(
        f"/pages/{pid}/cells/{cell_id}/swap",
        json={"target": "nope"},
        headers={"X-Requested-With": "fetch"},
    )
    assert resp.get_json()["ok"] is False


def test_compose_is_never_cacheable(app: Flask) -> None:
    """A composition is live widget data; caching one is always wrong. It used
    to go out with no cache directives at all, which lets an intermediary cache
    it heuristically: behind a caching reverse proxy the editor's preview can be
    served a composition rendered by an older Tesserae, missing whatever the
    template gained since."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    for query in ("?preview=1", "?for_push=1", ""):
        resp = client.get(f"/compose/{pid}{query}", environ_overrides={"REMOTE_ADDR": "10.0.0.5"})
        assert resp.status_code == 200, query
        assert "no-store" in resp.headers.get("Cache-Control", ""), query


def test_editor_preview_iframe_carries_a_version_buster(app: Flask) -> None:
    """An upgrade must not be able to reuse a composition cached from the
    previous version, so the initial iframe src is version-stamped."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="2x2_grid")
    body = client.get(f"/pages/{pid}").get_data(as_text=True)
    assert f"v={app.config['APP_VERSION']}" in body


# -- Updates cadence control -----------------------------------------


def test_preset_cadence_saves_as_itself(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(f"/pages/{pid}", data={"refresh_minutes": "30"})
    assert _store(tmp_path).get(pid).refresh_minutes == 30


def test_custom_cadence_comes_from_the_minutes_box(app: Flask, tmp_path: Path) -> None:
    """A cadence the presets don't cover (matching a battery panel's own
    wake interval) posts the sentinel plus a minutes value."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(
        f"/pages/{pid}",
        data={"refresh_minutes": "custom", "refresh_minutes_custom": "45"},
    )
    assert _store(tmp_path).get(pid).refresh_minutes == 45


def test_custom_cadence_without_a_value_keeps_the_stored_one(app: Flask, tmp_path: Path) -> None:
    """The sentinel on its own must not read as zero, which would quietly
    turn a refreshing dashboard into a push-only one."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(f"/pages/{pid}", data={"refresh_minutes": "15"})
    client.post(f"/pages/{pid}", data={"refresh_minutes": "custom", "refresh_minutes_custom": ""})
    assert _store(tmp_path).get(pid).refresh_minutes == 15


def test_custom_cadence_is_clamped_to_a_day(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(
        f"/pages/{pid}",
        data={"refresh_minutes": "custom", "refresh_minutes_custom": "99999"},
    )
    assert _store(tmp_path).get(pid).refresh_minutes == 1440


def test_a_non_preset_cadence_renders_as_custom_in_the_list(app: Flask, tmp_path: Path) -> None:
    """Before the custom box existed, a value set outside the dropdown had
    no matching option and the list rendered it as "only when pushed"."""
    client = app.test_client()
    _sign_in(client)
    pid = _new(client, name="Home", layout="1_cell")
    client.post(
        f"/pages/{pid}",
        data={"refresh_minutes": "custom", "refresh_minutes_custom": "45"},
    )
    body = client.get("/pages").get_data(as_text=True)
    assert '<option value="custom" selected>custom…</option>' in body
    assert 'value="45"' in body


def test_push_button_counts_only_live_bindings(app: Flask) -> None:
    """Issue #229: a deleted device leaves its id on any dashboard it shared,
    and the Push button counted raw ids, so the same row could read "unbound"
    (grouping resolves against the registry) and "Push to 2" at once."""
    from app.state.page_store import Page

    client = app.test_client()
    _sign_in(client)
    app.config["PAGE_STORE"].save(
        Page(id="orphaned", name="Orphaned", device_ids=["gone_a", "gone_b"], cells=[])
    )
    body = client.get("/pages").get_data(as_text=True)
    assert "Push to 2" not in body
    assert "no linked devices" in body


# -- a binding is a set of devices (issue #229) ---------------------------


def test_page_model_collapses_a_repeated_binding() -> None:
    """A repeated id used to double-list the dashboard under that device
    and inflate its "Push to N linked devices" count. Collapsing on load
    means an existing pages.json heals rather than needing a migration."""
    from app.state.page_store import Page

    page = Page(id="d", name="D", device_ids=["kitchen", "kitchen", "studio"], cells=[])
    assert page.device_ids == ["kitchen", "studio"]

    # Order is first-seen, so the page's primary device doesn't move.
    assert Page(id="d", name="D", device_ids=["b", "a", "b"], cells=[]).device_ids == ["b", "a"]


def test_group_pages_lists_a_repeated_binding_once() -> None:
    """Belt and braces for a binding assigned in-process, after the model
    validator has run."""
    from app.page_routes import _group_pages_for_index

    kitchen = _StubDevice("kitchen", display_name="Kitchen Panel")
    registry = _StubRegistry([kitchen])
    page = _page("d", "Family agenda", ["kitchen"])
    page.device_ids = ["kitchen", "kitchen"]  # assignment skips validation
    groups = _group_pages_for_index([page], registry)
    assert [d.id for d, _ in groups] == ["kitchen"]
    assert [p.id for p in groups[0][1]] == ["d"]
