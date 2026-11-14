"""Backend contract for the step-by-step dashboard editor."""

from __future__ import annotations

from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from flask import template_rendered
from jinja2 import ChoiceLoader, DictLoader
from pydantic import ValidationError

from app import device_service
from app.main import REPO_ROOT, create_app
from app.panel import panel_groups_for_push, preview_groups_for_page, resolve_panel_for_page
from app.state.deck_model import Deck, DeckLink, DeckPage
from app.state.page_store import Cell, Page, PageStore, Panel


@pytest.fixture
def app(tmp_path):
    app = create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")
    app.config["SETTINGS_STORE"].update_section(
        "app", {"panel_preset": "custom", "panel_w": 1024, "panel_h": 768}
    )
    app.config["PAGE_STORE"].save(Page(id="home", name="Home"))
    # The other builder owns this template. Observe the render signal without
    # depending on its markup or creating a placeholder in the checkout.
    app.jinja_loader = ChoiceLoader([DictLoader({"page_editor_v2.html": ""}), app.jinja_loader])
    return app


def _editor(app, query=""):
    rendered = []

    def record(sender, template, context, **extra):
        rendered.append((template.name, context))

    with template_rendered.connected_to(record, app):
        response = app.test_client().get(f"/pages/home{query}")
    assert response.status_code == 200
    return rendered[-1]


def test_editor_defaults_to_new_and_classic_can_switch_back(app):
    template, context = _editor(app, "?new=1")
    assert template == "page_editor_v2.html"
    assert context["editor_version"] == "new"
    assert context["is_new"] is True
    client = app.test_client()
    response = client.post(
        "/pages/editor-version", data={"version": "classic", "next": "/pages/home?new=1"}
    )
    assert response.headers["Location"] == "/pages/home?new=1"
    assert app.config["SETTINGS_STORE"].get_section("ui")["dashboard_editor"] == "classic"
    template, context = _editor(app)
    assert template == "page_editor.html"
    assert context["editor_version"] == "classic"
    assert context["is_new"] is False
    body = client.get("/pages/home").text
    assert "Try the new editor" in body
    assert 'action="/pages/editor-version"' in body
    assert 'name="version" value="new"' in body
    client.post("/pages/editor-version", data={"version": "new", "next": "/pages/home"})
    assert _editor(app)[0] == "page_editor_v2.html"


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/pages/home",
        "//evil.example",
        "/\\evil.example",
        "///evil.example",
        "javascript:alert(1)",
    ],
)
def test_editor_version_rejects_external_redirects(app, target):
    response = app.test_client().post(
        "/pages/editor-version",
        data={"version": "classic", "next": target},
        headers={"Referer": "http://localhost/pages/home?new=1"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/pages/home?new=1"


def test_editor_version_validates_input_and_has_safe_fallback(app):
    client = app.test_client()
    assert client.post("/pages/editor-version", data={"version": "other"}).status_code == 400
    response = client.post(
        "/pages/editor-version",
        data={"version": "classic"},
        headers={"Origin": "http://localhost", "Referer": "https://evil.example/pages/home"},
    )
    assert response.headers["Location"] == "/pages"


def test_grid_create_redirects_to_new_mode(app):
    response = app.test_client().post("/pages/new", data={"name": "New"})
    location = urlsplit(response.headers["Location"])
    assert location.query == "new=1"
    assert app.config["PAGE_STORE"].get(location.path.rsplit("/", 1)[-1]).name == "New"


def test_grid_create_keeps_chosen_design_size_in_test_display(app):
    response = app.test_client().post("/pages/new", data={"design_size": "480x800"})
    page_id = urlsplit(response.headers["Location"]).path.rsplit("/", 1)[-1]
    page = app.config["PAGE_STORE"].get(page_id)
    assert (page.test_display_w, page.test_display_h) == (480, 800)


def test_test_display_context_defaults_to_virtual_and_catalog_uses_picker_names(app):
    _, context = _editor(app)
    assert context["test_display"] == {"w": 1024, "h": 768}
    assert context["test_display_presets"] == [[800, 480], [480, 800], [1200, 825], [1920, 1080]]
    catalog = context["widget_catalog"]
    assert catalog["widgets"]
    for widget in catalog["widgets"]:
        assert set(widget) == {"key", "name", "family", "icon", "description", "category"}
    assert all(c["key"] != "fits" for c in catalog["categories"])
    for category in catalog["categories"]:
        assert category["count"] == sum(
            w["category"] == category["key"] for w in catalog["widgets"]
        )
    clock = next(w for w in catalog["widgets"] if w["key"] == "clock_analog")
    assert clock["name"] == "Analog"
    assert clock["family"] == "Clock"


def test_test_display_save_merges_and_survives_store_reload(app):
    client = app.test_client()
    client.post("/pages/home", data={"test_display_w": "1920", "test_display_h": "1080"})
    client.post("/pages/home", data={"name": "Renamed"})
    client.post("/pages/home", data={"test_display_w": "1200"})
    page = app.config["PAGE_STORE"].get("home")
    assert (page.test_display_w, page.test_display_h, page.name) == (1200, 1080, "Renamed")
    reloaded = PageStore(app.config["DATA_ROOT"] / "core" / "pages.json").get("home")
    assert (reloaded.test_display_w, reloaded.test_display_h) == (1200, 1080)
    _, context = _editor(app)
    assert context["test_display"] == {"w": 1200, "h": 1080}
    assert (context["panel"].w, context["panel"].h) == (1200, 1080)
    assert (context["preview_groups"][0]["w"], context["preview_groups"][0]["h"]) == (1200, 1080)


@pytest.mark.parametrize("value", ["0", "-1", "99", "4001", "abc", "1.5", ""])
def test_test_display_invalid_values_do_not_save_partial_updates(app, value):
    response = app.test_client().post(
        "/pages/home",
        data={"name": "Should not save", "test_display_w": value, "test_display_h": "800"},
        headers={"X-Requested-With": "fetch"},
    )
    assert response.get_json()["ok"] is False
    assert app.config["PAGE_STORE"].get("home").name == "Home"


@pytest.mark.parametrize("value", [0, 99, 4001])
def test_page_model_validates_test_display_size(value):
    with pytest.raises(ValidationError):
        Page(id="p", name="P", test_display_w=value)


def test_unbound_panel_keeps_legacy_size_until_test_display_is_set(app):
    settings = app.config["SETTINGS_STORE"]
    devices = app.config["DEVICE_REGISTRY"]
    old = Page(id="old", name="Old", panel=Panel(w=800, h=480))
    custom = Page(
        id="custom", name="Custom", panel=old.panel, test_display_w=1920, test_display_h=1080
    )
    for page, dims in [(old, (800, 480)), (custom, (1920, 1080))]:
        panel = resolve_panel_for_page(page, devices, settings)
        assert (panel.w, panel.h) == dims
        push_panel = panel_groups_for_push(page, devices, settings)[0][0]
        assert (push_panel.w, push_panel.h) == dims
        group = preview_groups_for_page(page, devices, settings)[0]
        assert (group["w"], group["h"]) == dims


def test_unbound_editor_and_compose_agree_with_test_display(app):
    page = Page(id="home", name="Home", panel=Panel(w=800, h=480))
    app.config["PAGE_STORE"].save(page)
    _, context = _editor(app)
    assert context["test_display"] == {"w": 800, "h": 480}
    assert (context["panel"].w, context["panel"].h) == (800, 480)
    group = context["preview_groups"][0]
    assert (group["w"], group["h"]) == (800, 480)
    body = app.test_client().get("/compose/home").text
    assert "width: 800px;" in body and "height: 480px;" in body


@pytest.mark.parametrize("editor_version", ["new", "classic"])
def test_opening_legacy_portrait_dashboard_leaves_stored_cells_byte_identical(app, editor_version):
    app.config["SETTINGS_STORE"].update_section("ui", {"dashboard_editor": editor_version})
    app.config["PAGE_STORE"].save(
        Page(
            id="home",
            name="Home",
            panel=Panel(w=480, h=800),
            cells=[
                Cell(id="top", x=0, y=0, w=480, h=400),
                Cell(id="bottom", x=0, y=400, w=480, h=400),
            ],
        )
    )
    path = app.config["DATA_ROOT"] / "core" / "pages.json"
    before = path.read_bytes()
    _, context = _editor(app)
    assert path.read_bytes() == before
    assert context["test_display"] == {"w": 480, "h": 800}
    assert (context["panel"].w, context["panel"].h) == (480, 800)


@pytest.mark.parametrize("editor_version", ["new", "classic"])
@pytest.mark.parametrize("status_bar", [False, True])
def test_refit_rotated_cells_follow_reading_order_without_changing_widgets(
    app, editor_version, status_bar
):
    app.config["SETTINGS_STORE"].update_section("ui", {"dashboard_editor": editor_version})
    client = app.test_client()
    client.post("/pages/home", data={"test_display_w": "800", "test_display_h": "480"})
    client.post("/pages/home/layout", data={"layout": "2x2_grid"})
    if status_bar:
        client.post("/pages/home/status-bar/toggle")
    before = app.config["PAGE_STORE"].get("home")
    widgets = {c.id: c.model_dump(exclude={"x", "y", "w", "h"}) for c in before.cells}
    client.post("/pages/home", data={"test_display_w": "480", "test_display_h": "800"})
    response = client.post("/pages/home/refit", headers={"X-Requested-With": "fetch"})
    assert response.get_json()["ok"] is True
    after = app.config["PAGE_STORE"].get("home")
    positions = [(c.y, c.x) for c in after.cells]
    assert positions == sorted(positions)
    assert {c.id: c.model_dump(exclude={"x", "y", "w", "h"}) for c in after.cells} == widgets
    if status_bar:
        assert after.cells[0].id == before.status_bar_cell_id


@pytest.mark.parametrize("editor_version", ["new", "classic"])
@pytest.mark.parametrize("layout", ["1_cell", "2x2_grid", "hero_left", "hero_sandwich"])
@pytest.mark.parametrize("gap", [0, 24])
def test_status_bar_does_not_hide_the_active_preset(app, editor_version, layout, gap):
    app.config["SETTINGS_STORE"].update_section("ui", {"dashboard_editor": editor_version})
    client = app.test_client()
    client.post("/pages/home", data={"gap": str(gap)})
    client.post("/pages/home/layout", data={"layout": layout})
    assert _editor(app)[1]["active_layout"] == layout
    client.post("/pages/home/status-bar/toggle")
    assert _editor(app)[1]["active_layout"] == layout
    # Applying a preset while the strip exists must use its actual height,
    # including the extra padding needed for the dashboard gap.
    client.post("/pages/home/layout", data={"layout": layout})
    assert _editor(app)[1]["active_layout"] == layout


def _lineup(app, *, page_ids=("other",), devices=()):
    app.config["PAGE_STORE"].save(Page(id="other", name="Other"))
    deck = Deck(
        id="loop",
        name="Loop",
        pages=[DeckPage(page_id=p) for p in page_ids],
        device_ids=list(devices),
        advance="timer",
        advance_interval_minutes=45,
    )
    app.config["DECK_STORE"].upsert(deck)
    return deck


@pytest.mark.parametrize("trigger", ["daily", "interval"])
@pytest.mark.parametrize("legacy_kind", [None, "schedule"])
def test_editor_includes_schedules_in_the_unified_lineup_list(app, trigger, legacy_kind):
    _lineup(app)
    schedule = Deck(
        id="morning",
        name="Morning",
        pages=[DeckPage(page_id="home")],
        advance="timer",
        advance_trigger=trigger,
        advance_fires_at="08:00",
        legacy_kind=legacy_kind,
    )
    app.config["DECK_STORE"].upsert(schedule)
    context = _editor(app)[1]
    assert [row["id"] for row in context["lineups"]] == ["loop", "morning"]
    row = context["lineups"][1]
    assert row["is_schedule"] is True
    assert row["joined"] is True
    assert row["schedule_dialog"] == "schedule-dialog-morning"
    assert row["when"]
    assert [row.id for row in context["schedules_for_page"]] == ["morning"]


@pytest.mark.parametrize("action", ["join", "leave"])
@pytest.mark.parametrize("trigger", ["daily", "interval"])
def test_schedule_can_be_removed_but_cannot_gain_extra_dashboards(app, action, trigger):
    schedule = Deck(
        id="morning",
        name="Morning",
        pages=[DeckPage(page_id="home")],
        advance="timer",
        advance_trigger=trigger,
        advance_fires_at="08:00",
    )
    app.config["DECK_STORE"].upsert(schedule)
    response = app.test_client().post(
        f"/pages/home/lineups/morning/{action}", json={"add_device": False}
    )
    assert response.status_code == (404 if action == "join" else 200)
    assert app.config["DECK_STORE"].get("morning") == (schedule if action == "join" else None)


def test_lineups_are_filtered_by_shape_not_migration_marker(app):
    deck = _lineup(app)
    app.config["DECK_STORE"].upsert(deck.model_copy(update={"legacy_kind": "schedule"}))
    assert [row["id"] for row in _editor(app)[1]["lineups"]] == ["loop"]
    assert (
        app.test_client()
        .post("/pages/home/lineups/loop/join", json={"add_device": False})
        .status_code
        == 200
    )


def test_lineup_join_is_idempotent_and_preserves_settings(app):
    _lineup(app)
    client = app.test_client()
    for _ in range(2):
        response = client.post("/pages/home/lineups/loop/join", json={"add_device": False})
        assert response.status_code == 200
        body = response.get_json()
        assert body["ok"] is True and body["device_ids"] == []
        assert len(body["lineups"]) == 1
        row = body["lineups"][0]
        assert {
            k: row[k]
            for k in ("id", "name", "device_ids", "device_names", "same_display", "joined")
        } == {
            "id": "loop",
            "name": "Loop",
            "device_ids": [],
            "device_names": [],
            "same_display": True,
            "joined": True,
        }
    deck = app.config["DECK_STORE"].get("loop")
    assert deck.page_ids == ["other", "home"]
    assert deck.advance_interval_minutes == 45
    assert _editor(app)[1]["lineups"] == body["lineups"]


def test_lineup_leave_removes_graph_pointers_only_in_selected_deck(app):
    deck = _lineup(app, page_ids=("other", "home"))
    deck = deck.model_copy(
        update={
            "pages": [
                DeckPage(page_id="other", links=[DeckLink(target_page_id="home", button="right")]),
                DeckPage(page_id="home"),
            ],
            "entry_page_id": "home",
            "home_page_id": "home",
            "advance_fallback_page_id": "home",
        }
    )
    app.config["DECK_STORE"].upsert(deck)
    app.config["DECK_STORE"].upsert(Deck(id="keep", name="Keep", pages=deck.pages))
    response = app.test_client().post("/pages/home/lineups/loop/leave")
    assert response.get_json()["ok"] is True
    saved = app.config["DECK_STORE"].get("loop")
    assert saved.page_ids == ["other"]
    assert saved.pages[0].links == []
    assert saved.entry_page_id is saved.home_page_id is saved.advance_fallback_page_id is None
    assert "home" in app.config["DECK_STORE"].get("keep").page_ids
    assert app.config["PAGE_STORE"].get("home") is not None


def test_leaving_last_page_removes_empty_lineup(app):
    _lineup(app, page_ids=("home",))
    response = app.test_client().post("/pages/home/lineups/loop/leave")
    assert response.get_json() == {"ok": True, "lineups": [], "device_ids": []}
    assert app.config["DECK_STORE"].get("loop") is None


def _device(app, device_id):
    device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id=device_id,
        kind_id="esp32_client",
    )


def test_lineup_join_adds_displays_with_confirmation_and_invalidates_navigation(app):
    _device(app, "screen")
    _device(app, "second")
    _lineup(app, devices=("screen", "second"))
    nav = app.config["DECK_NAV_STORE"]
    nav.set("screen", "loop", "other")
    push = Mock()
    app.config["PUSH_MANAGER"] = push
    client = app.test_client()
    response = client.post("/pages/home/lineups/loop/join", json={"add_device": False})
    assert response.status_code == 409
    assert app.config["DECK_STORE"].get("loop").page_ids == ["other"]
    response = client.post("/pages/home/lineups/loop/join", json={"add_device": True})
    assert response.status_code == 200
    body = response.get_json()
    assert body["device_ids"] == ["screen", "second"]
    assert body["lineups"][0]["device_ids"] == ["screen", "second"]
    assert body["lineups"][0]["same_display"] is True
    assert len(body["lineups"][0]["device_names"]) == 2
    assert nav.get("screen") is None
    push.clear_deck_cache.assert_any_call("screen")
    push.clear_deck_cache.assert_any_call("second")
    page = app.config["PAGE_STORE"].get("home")
    page = page.model_copy(update={"test_display_w": 1920, "test_display_h": 1080})
    panel = resolve_panel_for_page(
        page, app.config["DEVICE_REGISTRY"], app.config["SETTINGS_STORE"]
    )
    assert (panel.w, panel.h) == (800, 480)
    response = client.post("/pages/home/lineups/loop/leave")
    assert response.get_json()["device_ids"] == ["screen", "second"]


def test_join_keeps_existing_displays_and_adds_missing_lineup_displays(app):
    for name in ("screen", "second", "third"):
        _device(app, name)
    page = app.config["PAGE_STORE"].get("home")
    app.config["PAGE_STORE"].save(page.model_copy(update={"device_ids": ["screen", "third"]}))
    _lineup(app, devices=("screen", "second"))
    assert _editor(app)[1]["lineups"][0]["same_display"] is False
    response = app.test_client().post("/pages/home/lineups/loop/join", json={"add_device": True})
    assert response.get_json()["device_ids"] == ["screen", "third", "second"]


def test_join_rebinds_existing_membership_and_clears_navigation(app):
    _device(app, "screen")
    _lineup(app, page_ids=("other", "home"), devices=("screen",))
    nav = app.config["DECK_NAV_STORE"]
    nav.set("screen", "loop", "other")
    response = app.test_client().post("/pages/home/lineups/loop/join", json={"add_device": True})
    assert response.get_json()["device_ids"] == ["screen"]
    assert nav.get("screen") is None


def test_editor_mutations_use_existing_csrf_protection(app):
    client = app.test_client()
    client.set_cookie(app.config["SESSION_COOKIE_NAME"], "session")
    response = client.post(
        "/pages/editor-version",
        data={"version": "classic"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert app.config["SETTINGS_STORE"].get_section("ui").get("dashboard_editor") is None
    _lineup(app)
    response = client.post(
        "/pages/home/lineups/loop/join",
        json={"add_device": False},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert app.config["DECK_STORE"].get("loop").page_ids == ["other"]


@pytest.mark.parametrize(
    "path",
    [
        "/pages/missing/lineups/loop/join",
        "/pages/home/lineups/missing/join",
        "/pages/missing/lineups/loop/leave",
        "/pages/home/lineups/missing/leave",
    ],
)
def test_lineup_routes_require_existing_records(app, path):
    _lineup(app)
    assert app.test_client().post(path, json={"add_device": False}).status_code == 404
