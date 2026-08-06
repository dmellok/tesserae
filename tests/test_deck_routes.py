"""Deck admin routes: create / update / toggle / delete + graph validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page


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
    ps = a.config["PAGE_STORE"]
    ps.save(Page(id="overview", name="Overview"))
    ps.save(Page(id="calendar", name="Calendar"))
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


GRAPH = json.dumps(
    [
        {"page_id": "overview", "links": [{"target_page_id": "calendar", "button": "right"}]},
        {"page_id": "calendar", "links": [{"target_page_id": "overview", "button": "left"}]},
    ]
)


def _decks(app: Flask) -> list:
    return app.config["DECK_STORE"].all()


def test_editor_save_persists_timer_advance(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/decks/editor-save",
        data={
            "name": "Loop",
            "pages": "overview,calendar",
            "advance": "timer",
            "advance_interval_minutes": "15",
            "advance_anchor": "07:30",
            "dwell[overview]": "45",
            "home": "overview",
        },
        follow_redirects=True,
    )
    decks = _decks(app)
    assert len(decks) == 1
    d = decks[0]
    assert d.advance == "timer"
    assert d.advance_interval_minutes == 15 and d.advance_anchor == "07:30"
    assert d.page("overview").dwell_minutes == 45
    assert d.page("calendar").dwell_minutes is None


def test_editor_save_defaults_to_manual(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/decks/editor-save",
        data={"name": "Tap", "pages": "overview,calendar"},
        follow_redirects=True,
    )
    assert _decks(app)[0].advance == "manual"


def test_editor_save_persists_advanced_timing(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/decks/editor-save",
        data={
            "name": "Adv",
            "pages": "overview,calendar",
            "advance": "timer",
            "advance_days": ["0", "2", "4"],
            "advance_end_at": "22:00",
            "advance_mode": "priority",
            "advance_min_hold_minutes": "10",
            "advance_priority": "7",
            "advance_smart_sync": "on",
            "advance_smart_sync_lead_s": "20",
        },
        follow_redirects=True,
    )
    d = _decks(app)[0]
    assert d.advance_days_of_week == [0, 2, 4] and d.advance_end_at == "22:00"
    assert d.advance_mode == "priority" and d.advance_min_hold_minutes == 10
    assert d.advance_priority == 7 and d.advance_smart_sync is True
    assert d.advance_smart_sync_lead_s == 20


def test_create_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/new",
        data={"name": "Kitchen", "refresh_interval_minutes": "10", "graph_json": GRAPH},
    )
    assert resp.status_code in (302, 200)
    decks = _decks(app)
    assert len(decks) == 1
    assert decks[0].name == "Kitchen"
    assert decks[0].refresh_interval_minutes == 10
    assert decks[0].page_ids == ["overview", "calendar"]


def test_index_renders_and_lists_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "Kdeck", "graph_json": GRAPH})
    resp = client.get("/decks")
    assert resp.status_code == 200
    assert "Kdeck" in resp.get_data(as_text=True)


def test_invalid_graph_json_flashes_without_crash(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/new", data={"name": "Bad", "graph_json": "{not json"}, follow_redirects=True
    )
    assert resp.status_code == 200
    assert _decks(app) == []


def test_link_to_page_not_in_deck_rejected(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    bad = json.dumps(
        [{"page_id": "overview", "links": [{"target_page_id": "ghost", "button": "right"}]}]
    )
    client.post("/decks/new", data={"name": "Bad", "graph_json": bad}, follow_redirects=True)
    assert _decks(app) == []


def test_update_preserves_enabled_and_changes_name(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(f"/decks/{did}/toggle")  # disable
    assert app.config["DECK_STORE"].get(did).enabled is False
    client.post(f"/decks/{did}/update", data={"name": "Renamed", "graph_json": GRAPH})
    updated = app.config["DECK_STORE"].get(did)
    assert updated.name == "Renamed"
    assert updated.enabled is False  # toggle state preserved across an edit


def test_delete_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(f"/decks/{did}/delete")
    assert app.config["DECK_STORE"].get(did) is None


def test_suggestions_render_from_page_links(app: Flask) -> None:
    ps = app.config["PAGE_STORE"]
    ps.save(
        Page(
            id="overview",
            name="Overview",
            cells=[Cell(id="c1", x=0, y=0, w=12, h=6, on_tap="page:calendar")],
        )
    )
    ps.save(
        Page(
            id="calendar",
            name="Calendar",
            cells=[Cell(id="c2", x=0, y=0, w=12, h=6, on_tap="page:overview")],
        )
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    # Suggestions render inside the setup wizard as its intro screen.
    assert 'data-wizard-view="suggest"' in body
    assert "Your dashboards already link up" in body
    # A pre-filled create form is rendered for the suggestion.
    assert "graph_json" in body and "wizard-suggestion" in body


def test_update_sets_per_page_refresh(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(
        f"/decks/{did}/update",
        data={
            "name": "K",
            "refresh_interval_minutes": "20",
            "page_refresh_overview": "5",
            "page_refresh_calendar": "",
        },
    )
    deck = app.config["DECK_STORE"].get(did)
    assert deck.refresh_interval_minutes == 20
    assert deck.page("overview").refresh_interval_minutes == 5
    assert deck.page("calendar").refresh_interval_minutes is None  # blank -> inherit


def test_update_preserves_graph(app: Flask) -> None:
    # The management update doesn't submit the graph; links must survive.
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    client.post(f"/decks/{did}/update", data={"name": "Renamed"})
    deck = app.config["DECK_STORE"].get(did)
    assert deck.name == "Renamed"
    assert any(lnk.target_page_id == "calendar" for lnk in deck.page("overview").links)


def test_sync_rederives_graph_from_page_links(app: Flask) -> None:
    ps = app.config["PAGE_STORE"]
    ps.save(Page(id="ov", name="Ov", cells=[Cell(id="c", x=0, y=0, w=12, h=6, on_tap="page:cal")]))
    ps.save(Page(id="cal", name="Cal", cells=[Cell(id="c", x=0, y=0, w=12, h=6, on_tap="page:ov")]))
    client = app.test_client()
    _sign_in(client)
    graph = json.dumps([{"page_id": "ov", "links": []}, {"page_id": "cal", "links": []}])
    client.post("/decks/new", data={"name": "D", "graph_json": graph})
    did = _decks(app)[0].id
    assert app.config["DECK_STORE"].get(did).page("ov").links == []
    client.post(f"/decks/{did}/sync")
    deck = app.config["DECK_STORE"].get(did)
    assert any(lnk.target_page_id == "cal" for lnk in deck.page("ov").links)


def test_edit_graph_advanced_replaces_graph(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "K", "graph_json": GRAPH})
    did = _decks(app)[0].id
    new_graph = json.dumps(
        [
            {"page_id": "overview", "links": []},
            {"page_id": "weather", "links": [{"target_page_id": "overview", "button": "left"}]},
        ]
    )
    client.post(f"/decks/{did}/graph", data={"graph_json": new_graph})
    assert set(app.config["DECK_STORE"].get(did).page_ids) == {"overview", "weather"}


def test_push_warms_all_pages_and_sends_entry(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Push button: warm every page x device, push the entry page,
    seed the nav position. No Playwright: pusher methods are stubbed."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/decks/new",
        data={"name": "Live", "graph_json": GRAPH, "device_ids": "panel_a", "entry_page_id": ""},
    )
    deck = _decks(app)[0]
    assert deck.device_ids == ["panel_a"]

    warmed: list = []
    pushed: list = []

    class _Push:
        @staticmethod
        def warm_deck_page(page_id, device_id):
            warmed.append((device_id, page_id))
            return page_id != "calendar"  # one page fails to warm

        @staticmethod
        def push(page_id, **kwargs):
            pushed.append((page_id, tuple(sorted(kwargs.get("device_ids") or ()))))
            from app.push import PushResult

            return PushResult(status="sent", page_id=page_id)

    monkeypatch.setitem(app.config, "PUSH_MANAGER", _Push())
    resp = client.post(f"/decks/{deck.id}/push", follow_redirects=False)
    assert resp.status_code == 302
    assert set(warmed) == {("panel_a", "overview"), ("panel_a", "calendar")}
    assert pushed == [("overview", ("panel_a",))]
    nav = app.config["DECK_NAV_STORE"].get("panel_a")
    assert nav is not None and nav["page_id"] == "overview" and nav["deck_id"] == deck.id


def test_push_requires_bound_devices(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/decks/new", data={"name": "Unbound", "graph_json": GRAPH, "device_ids": ""})
    deck = _decks(app)[0]
    resp = client.post(f"/decks/{deck.id}/push")
    assert resp.status_code == 302
    # No crash, deck untouched, nav empty.
    assert app.config["DECK_NAV_STORE"].get("panel_a") is None


# -- deck editor (dense rail + inspector) -----------------------------------


def test_editor_renders_blank_and_existing(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/decks/new").status_code == 200
    client.post(
        "/decks/new",
        data={"name": "Live", "graph_json": GRAPH, "device_ids": ""},
    )
    deck = _decks(app)[0]
    html = client.get(f"/decks/{deck.id}/edit").get_data(as_text=True)
    assert "Flip order" in html and "dxe-data" in html
    assert 'name="member" value="overview" checked' in html.replace("\n", " ") or "overview" in html


def test_editor_save_js_shape_creates_deck_with_home(app: Flask) -> None:
    """The JS path: ordered CSV in ``pages``, home radio, timeout range,
    per-page override, devices chips."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/editor-save",
        data={
            "deck_id": "",
            "name": "Kitchen stack",
            "pages": "overview,calendar",
            "home": "calendar",
            "timeout": "15",
            "refresh_interval_minutes": "30",
            "override[calendar]": "5",
            "enabled": "on",
        },
    )
    assert resp.status_code == 302
    deck = _decks(app)[0]
    assert [p.page_id for p in deck.pages] == ["overview", "calendar"]
    assert deck.home_page_id == "calendar"
    assert deck.home_timeout_minutes == 15
    assert deck.resolved_entry_page_id == "calendar"  # entry defaults to home
    cal = next(p for p in deck.pages if p.page_id == "calendar")
    assert cal.refresh_interval_minutes == 5
    assert deck.enabled is True


def test_editor_save_nojs_fallback_membership_and_order(app: Flask) -> None:
    """Without JS: member checkboxes + order[<id>] numerics decide the
    flip order; ``pages`` is empty."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/editor-save",
        data={
            "deck_id": "",
            "name": "Fallback",
            "pages": "",
            "member": ["overview", "calendar"],
            "order[calendar]": "1",
            "order[overview]": "2",
            "home": "calendar",
            "timeout": "0",
            "refresh_interval_minutes": "60",
            "enabled": "on",
        },
    )
    assert resp.status_code == 302
    deck = _decks(app)[0]
    assert [p.page_id for p in deck.pages] == ["calendar", "overview"]
    assert deck.home_timeout_minutes == 0


def test_editor_save_updates_existing_preserving_id(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/decks/editor-save",
        data={"deck_id": "", "name": "First", "pages": "overview,calendar", "enabled": "on"},
    )
    deck = _decks(app)[0]
    client.post(
        "/decks/editor-save",
        data={
            "deck_id": deck.id,
            "name": "Renamed",
            "pages": "calendar",  # drop a page
            "home": "calendar",
            "timeout": "30",
            "enabled": "on",
        },
    )
    fresh = app.config["DECK_STORE"].get(deck.id)
    assert fresh.name == "Renamed"
    assert [p.page_id for p in fresh.pages] == ["calendar"]
    assert fresh.home_timeout_minutes == 30


def test_editor_save_rejects_empty_page_set(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/editor-save",
        data={"deck_id": "", "name": "Empty", "pages": "", "enabled": "on"},
    )
    assert resp.status_code == 302
    assert _decks(app) == []


def test_editor_save_derives_links_from_page_actions(app: Flask) -> None:
    """Pages carrying page:<id> tap links get their graph derived on
    save, same as Sync from links."""
    client = app.test_client()
    _sign_in(client)
    ps = app.config["PAGE_STORE"]
    ps.save(Page(id="a", name="A", cells=[Cell(id="c", x=0, y=0, w=12, h=6, on_tap="page:b")]))
    ps.save(Page(id="b", name="B", cells=[Cell(id="c", x=0, y=0, w=12, h=6, on_tap="page:a")]))
    client.post(
        "/decks/editor-save",
        data={"deck_id": "", "name": "Linked", "pages": "a,b", "enabled": "on"},
    )
    deck = _decks(app)[0]
    a = next(p for p in deck.pages if p.page_id == "a")
    assert any(link.target_page_id == "b" for link in a.links)


# -- "both" advance: timer AND taps (issue report, 2026-08-07) -------------


def _save_both_deck(client, name: str = "Mixed") -> None:
    client.post(
        "/decks/editor-save",
        data={
            "name": name,
            "pages": "overview,calendar",
            "advance": "both",
            "advance_interval_minutes": "20",
            "home": "overview",
        },
        follow_redirects=True,
    )


def test_editor_save_persists_both_advance(app: Flask) -> None:
    """``both`` is a valid mode (auto-cycle AND accept taps) and must survive
    the editor round trip like ``timer`` does."""
    client = app.test_client()
    _sign_in(client)
    _save_both_deck(client)
    d = _decks(app)[0]
    assert d.advance == "both"
    assert d.advance_interval_minutes == 20


def test_management_update_keeps_the_advance_mode(app: Flask) -> None:
    """The management form carries no advance controls. It used to rebuild the
    deck from only its own fields, so saving a name change silently reset an
    auto-advancing deck to manual, which is both "it stopped moving" and the
    "By hand" label at once."""
    client = app.test_client()
    _sign_in(client)
    _save_both_deck(client)
    deck_id = _decks(app)[0].id

    client.post(
        f"/decks/{deck_id}/update",
        data={"name": "Renamed", "refresh_interval_minutes": "10"},
        follow_redirects=True,
    )
    d = _decks(app)[0]
    assert d.name == "Renamed"
    assert d.advance == "both"  # not reset to the model default
    assert d.advance_interval_minutes == 20


def test_graph_edit_keeps_the_advance_mode(app: Flask) -> None:
    """Same reset via the advanced graph form, whose docstring already claimed
    the management fields were kept."""
    client = app.test_client()
    _sign_in(client)
    _save_both_deck(client)
    deck_id = _decks(app)[0].id

    client.post(
        f"/decks/{deck_id}/graph",
        data={"graph_json": '[{"page_id": "overview"}, {"page_id": "calendar"}]'},
        follow_redirects=True,
    )
    d = _decks(app)[0]
    assert d.advance == "both"
    assert d.advance_interval_minutes == 20


def test_editor_save_keeps_a_non_cycle_trigger(app: Flask) -> None:
    """The deck editor only authors the cycle shape. Re-saving an interval or
    daily deck through it must not silently convert it to a cycle."""
    client = app.test_client()
    _sign_in(client)
    _save_both_deck(client)
    store = app.config["DECK_STORE"]
    deck = store.all()[0]
    store.upsert(deck.model_copy(update={"advance_trigger": "daily", "advance_fires_at": "08:00"}))

    client.post(
        "/decks/editor-save",
        data={
            "deck_id": deck.id,
            "name": "Mixed",
            "pages": "overview,calendar",
            "advance": "both",
            "advance_interval_minutes": "20",
        },
        follow_redirects=True,
    )
    # Guard the premise: the save must have updated that deck, not created a
    # second one, or the assertions below pass without testing anything.
    assert len(store.all()) == 1
    d = store.all()[0]
    assert d.id == deck.id
    assert d.advance_trigger == "daily"
    assert d.advance_fires_at == "08:00"


def test_editor_exposes_the_return_home_wrapper_for_the_mode_toggle(app: Flask) -> None:
    """deck_editor.js hides return-home for timer / both by this id. If the id
    moves, the control silently stays visible for modes it doesn't apply to."""
    client = app.test_client()
    _sign_in(client)
    _save_both_deck(client)
    deck_id = _decks(app)[0].id
    body = client.get(f"/decks/{deck_id}/edit").get_data(as_text=True)
    assert 'id="dxe-returnhome-wrap"' in body
    assert 'id="dxe-returnhome"' in body


def test_auto_advancing_save_without_a_timeout_stores_zero(app: Flask) -> None:
    """The editor disables the return-home control for timer / both, so the
    field isn't submitted. The server must read that as "off" rather than
    carrying a stale timeout that would park the panel on home between
    advances."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/decks/editor-save",
        data={
            "name": "Loop",
            "pages": "overview,calendar",
            "advance": "manual",
            "home": "overview",
            "timeout": "5",
        },
        follow_redirects=True,
    )
    deck_id = _decks(app)[0].id
    assert _decks(app)[0].home_timeout_minutes == 5

    # Switch to an auto-advancing mode; the disabled control sends nothing.
    client.post(
        "/decks/editor-save",
        data={
            "deck_id": deck_id,
            "name": "Loop",
            "pages": "overview,calendar",
            "advance": "both",
            "advance_interval_minutes": "20",
            "home": "overview",
        },
        follow_redirects=True,
    )
    d = _decks(app)[0]
    assert d.advance == "both"
    assert d.home_timeout_minutes == 0
