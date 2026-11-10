"""The lineup editor's server side: the item pages (/decks/dashboard-items),
what a row says about a dashboard and which display options apply, and the
save (/decks/editor-save) and create (/decks/new/lineup) paths: time with the
Lineups page's semantics, Smart sync that can be switched off, a dashboard's
own Updates and pin, clashes refused."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
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
    store = a.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    ps = a.config["PAGE_STORE"]
    for pid in ("a", "b", "c"):
        ps.save(Page(id=pid, name=pid.upper()))
    return a


def _register(app: Flask, device_id: str, kind: str = "esp32_client") -> None:
    code = app.config["PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": kind}),
    )
    assert resp.status_code == 201


def _deck(app: Flask, **fields: Any) -> Deck:
    base: dict[str, Any] = {"id": "lu", "name": "Lu", "pages": [DeckPage(page_id="a")]}
    deck = Deck(**{**base, **fields})
    app.config["DECK_STORE"].upsert(deck)
    return deck


def _save(app: Flask, **form: Any) -> Any:
    data = {"deck_id": "lu", "name": "Lu", "pages": "a", "enabled": "on", "respond": "json", **form}
    return app.test_client().post("/decks/editor-save", data=data)


def _stored(app: Flask, deck_id: str = "lu") -> Deck:
    deck = app.config["DECK_STORE"].get(deck_id)
    assert deck is not None
    return deck


def _install(app: Flask, monkeypatch: pytest.MonkeyPatch, **widgets: dict[str, Any]) -> None:
    reg = app.config["PLUGIN_REGISTRY"]
    real = reg.get

    def get(plugin_id: str) -> Any:
        if plugin_id in widgets:
            spec = widgets[plugin_id]
            return SimpleNamespace(
                manifest=spec.get("manifest", {}), server_module=spec.get("module")
            )
        return real(plugin_id)

    monkeypatch.setattr(reg, "get", get)


def _widget_page(app: Flask, plugin: str, options: dict[str, Any], page_id: str = "w") -> Page:
    page = Page(
        id=page_id,
        name=page_id,
        cells=[Cell(id="c1", plugin=plugin, x=0, y=0, w=10, h=10, options=options)],
    )
    app.config["PAGE_STORE"].save(page)
    return page


def _gallery(app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, n: int) -> Page:
    import app.step_items as items

    module = app.config["PLUGIN_REGISTRY"].get("picture_gallery").server_module
    names = [f"IMG_{i:04d}.jpg" for i in range(n - 1)] + ["beach.jpg"]
    monkeypatch.setattr(module, "list_folder_files", lambda folder: list(names))
    monkeypatch.setattr(module, "_folder_path", lambda name, data_dir: tmp_path)
    monkeypatch.setattr(items, "_GALLERY_CACHE", {})
    return _widget_page(app, "picture_gallery", {"folder": "trip", "mode": "sequential"}, "photos")


PAPERS = [{"key": "nyt", "label": "The New York Times"}, {"key": "wsj", "label": "WSJ"}]


# -- items, a page at a time ---------------------------------------------------


def test_dashboard_items_pages_past_the_view_cap_and_filters(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.step_items import MAX_STEP_ITEMS

    _gallery(app, monkeypatch, tmp_path, 412)
    c = app.test_client()
    first = c.get("/decks/dashboard-items?page_id=photos&limit=18").get_json()
    assert (first["mode"], first["total"], first["matched"], first["noun"]) == (
        "pick",
        412,
        412,
        "photos",
    )
    assert [it["index"] for it in first["items"]] == list(range(18))
    assert first["items"][0]["thumb_url"]
    late = c.get(f"/decks/dashboard-items?page_id=photos&offset={MAX_STEP_ITEMS + 300}").get_json()
    assert late["items"][0]["index"] == MAX_STEP_ITEMS + 300  # past the view's cap
    found = c.get("/decks/dashboard-items?page_id=photos&q=BEACH").get_json()
    assert found["matched"] == 1 and found["items"][0]["key"] == "beach.jpg"
    assert found["items"][0]["index"] == 411  # its place in the whole list
    assert c.get("/decks/dashboard-items?page_id=nope").status_code == 400


def test_a_pinned_widget_still_lists_its_items_and_says_which(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = SimpleNamespace(lineup_items=lambda options: PAPERS)
    _install(
        app,
        monkeypatch,
        papers={
            "manifest": {"lineups": {"pin_option": "paper", "noun": "papers"}},
            "module": module,
        },
    )
    _widget_page(app, "papers", {"paper": "nyt"}, "news")
    got = app.test_client().get("/decks/dashboard-items?page_id=news").get_json()
    assert (got["total"], got["noun"], got["pinnable"]) == (2, "papers", True)
    assert (got["pinned"], got["pinned_label"]) == ("nyt", "The New York Times")
    with app.test_request_context():
        from app.lineup_editor import dashboard_summary

        summary = dashboard_summary(app.config["PAGE_STORE"].get("news"))
    assert summary["subtitle"].endswith("2 papers, pinned: The New York Times")


def test_saving_a_pin_writes_the_widget_option_and_unpins(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = SimpleNamespace(lineup_items=lambda options: PAPERS)
    _install(
        app,
        monkeypatch,
        papers={"manifest": {"lineups": {"pin_option": "paper"}}, "module": module},
    )
    _widget_page(app, "papers", {"paper": ""}, "news")
    _deck(app, pages=[DeckPage(page_id="news")])
    pages = app.config["PAGE_STORE"]
    assert _save(app, pages="news", **{"pin[news]": "wsj"}).get_json()["ok"]
    assert pages.get("news").cells[0].options["paper"] == "wsj"
    _save(app, pages="news", **{"pin[news]": "not-a-paper"})
    assert pages.get("news").cells[0].options["paper"] == "wsj"  # refused, kept
    _save(app, pages="news", **{"pin[news]": ""})
    assert pages.get("news").cells[0].options["paper"] == ""


def test_a_draw_widget_reports_a_count_only_through_its_optional_hook(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.lineup_editor import dashboard_summary

    _install(
        app,
        monkeypatch,
        counted={
            "manifest": {"name": "Picture, Immich album", "lineups": {"draw": True}},
            "module": SimpleNamespace(lineup_item_count=lambda options: 212),
        },
        silent={"manifest": {"lineups": {"draw": True}}, "module": SimpleNamespace()},
        broken={
            "manifest": {"lineups": {"draw": True}},
            "module": SimpleNamespace(lineup_item_count=lambda options: 1 / 0),
        },
    )
    with app.test_request_context():
        counted = dashboard_summary(_widget_page(app, "counted", {}, "p1"))
        silent = dashboard_summary(_widget_page(app, "silent", {}, "p2"))
        broken = dashboard_summary(_widget_page(app, "broken", {}, "p3"))
    assert counted["items"] == {**counted["items"], "mode": "draw", "count": 212}
    assert counted["subtitle"] == "Immich album · 212 items, random each time"
    assert silent["items"]["count"] == 0 and silent["subtitle"].endswith("a fresh draw each time")
    assert broken["items"]["count"] == 0


def test_a_row_says_what_the_dashboard_is(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.lineup_editor import dashboard_summary

    photos = _gallery(app, monkeypatch, tmp_path, 41)
    ha = _widget_page(app, "ha_entities", {"entities": [f"sensor.s{i}" for i in range(12)]}, "ha")
    with app.test_request_context():
        assert dashboard_summary(photos)["subtitle"] == "Gallery · 41 photos, in order"
        summary = dashboard_summary(ha.model_copy(update={"refresh_minutes": 5}))
    assert summary["subtitle"] == "Home Assistant · 12 entities"
    assert summary["fresh"] == "New picture every 5 min while shown."


# -- display options ------------------------------------------------------------


def test_display_options_follow_what_the_display_can_do(app: Flask) -> None:
    from app.lineup_editor import display_caps

    _register(app, "sleeper")
    _register(app, "tv", "opendisplay_ha")
    reg = app.config["DEVICE_REGISTRY"]
    reg.get("tv").manifest["buttons"] = False
    with app.test_request_context():
        sleeper = display_caps(reg.get("sleeper"))
        tv = display_caps(reg.get("tv"))
    assert sleeper["sleeps"] and sleeper["inputs"]
    assert not tv["sleeps"] and not tv["inputs"]


# -- the save --------------------------------------------------------------------


def test_smart_sync_switches_off_from_the_editor(app: Flask) -> None:
    """The box posts nothing when unticked, so the editor sends a hidden "0"
    first; the save reads that as off instead of keeping the stored value."""
    _deck(app, advance="timer", advance_smart_sync=True)
    _save(app, advance_smart_sync="0")
    assert _stored(app).advance_smart_sync is False
    c = app.test_client()
    c.post(
        "/decks/editor-save",
        data={"deck_id": "lu", "name": "Lu", "pages": "a", "advance_smart_sync": ["0", "on"]},
    )
    assert _stored(app).advance_smart_sync is True
    _save(app)  # a display that never sleeps posts neither: kept
    assert _stored(app).advance_smart_sync is True


def _when(app: Flask, mode: str, start: str = "06:00", end: str = "", **form: Any) -> Any:
    return _save(
        app, when_changed="1", when_mode=mode, when_start=start, when_end=end, **form
    ).get_json()


def test_an_end_time_turns_a_push_into_a_rotation_or_a_window(app: Flask) -> None:
    pages = [DeckPage(page_id="a"), DeckPage(page_id="b"), DeckPage(page_id="c")]
    _deck(app, advance="timer", advance_trigger="daily", advance_fires_at="06:00", pages=pages)
    assert _when(app, "between", "06:00", "16:30", pages="a,b,c")["ok"]
    d = _stored(app)
    assert (d.advance, d.advance_trigger, d.advance_anchor, d.advance_end_at) == (
        "timer",
        "cycle",
        "06:00",
        "16:30",
    )
    assert d.advance_fires_at is None
    # One dashboard repeats through the window instead.
    _deck(app, advance="timer", advance_trigger="daily", advance_fires_at="06:00")
    _when(app, "between", "07:00", "24:00")
    d = _stored(app)
    assert (d.advance_trigger, d.advance_window_start, d.advance_window_end) == (
        "interval",
        "07:00",
        None,
    )


def test_removing_the_end_and_by_hand_round_trip(app: Flask) -> None:
    pages = [DeckPage(page_id="a"), DeckPage(page_id="b")]
    _deck(
        app,
        advance="both",
        advance_trigger="cycle",
        advance_anchor="06:30",
        advance_end_at="10:00",
        pages=pages,
    )
    _when(app, "at", "06:30", pages="a,b")
    d = _stored(app)
    assert (d.advance, d.advance_trigger, d.advance_fires_at) == ("timer", "daily", "06:30")
    _when(app, "manual", "", pages="a,b")
    assert _stored(app).advance == "manual"
    _when(app, "between", "08:00", "12:00", pages="a,b", taps_flip="1")
    d = _stored(app)
    assert (d.advance, d.advance_trigger, d.advance_anchor) == ("both", "cycle", "08:00")
    assert _when(app, "between", "12:00", "08:00", pages="a,b")["ok"] is False


def test_both_never_stays_on_a_daily_or_interval_lineup(app: Flask) -> None:
    """The scheduler only runs "timer" daily and interval lineups, so a saved
    "both" there never fired; the editor saves it as "timer"."""
    _deck(app, advance="both", advance_trigger="daily", advance_fires_at="07:00")
    _save(app)
    assert _stored(app).advance == "timer"
    _deck(
        app,
        advance="both",
        advance_trigger="cycle",
        advance_anchor="07:00",
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )
    _save(app, pages="a,b")
    assert _stored(app).advance == "both"  # a rotation takes taps
    _save(app, pages="a,b", taps_flip="0")
    assert _stored(app).advance == "timer"


def test_a_window_that_meets_another_lineup_is_refused(app: Flask) -> None:
    _register(app, "hall")
    _deck(
        app,
        device_ids=["hall"],
        advance="timer",
        advance_trigger="cycle",
        advance_anchor="06:00",
        advance_end_at="08:00",
    )
    app.config["DECK_STORE"].upsert(
        Deck(
            id="other",
            name="Other",
            device_ids=["hall"],
            pages=[DeckPage(page_id="b")],
            advance="timer",
            advance_trigger="cycle",
            advance_anchor="09:00",
            advance_end_at="11:00",
        )
    )
    got = _when(app, "between", "06:00", "10:00", device_ids="hall")
    assert got["ok"] is False and "Other" in got["error"]
    assert _stored(app).advance_end_at == "08:00"


def test_a_window_in_quiet_hours_saves_with_a_warning(app: Flask) -> None:
    _register(app, "hall")
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {"quiet_hours_enabled": True, "quiet_hours_start": "23:00", "quiet_hours_end": "07:00"},
    )
    _deck(
        app, device_ids=["hall"], advance="timer", advance_trigger="daily", advance_fires_at="09:00"
    )
    got = _when(app, "at", "06:00", device_ids="hall")
    assert got["ok"] and got["url"] == "/decks/lu/edit"
    assert got["warnings"] and "Quiet hours" in got["warnings"][0]


def test_updates_edits_the_dashboards_own_refresh(app: Flask) -> None:
    _deck(app)
    _save(app, **{"page_refresh[a]": "30"})
    assert app.config["PAGE_STORE"].get("a").refresh_minutes == 30
    _save(app, **{"page_refresh[a]": "bad"})
    assert app.config["PAGE_STORE"].get("a").refresh_minutes == 30


def test_the_plain_form_still_redirects_to_the_editor(app: Flask) -> None:
    _deck(app)
    resp = app.test_client().post(
        "/decks/editor-save", data={"deck_id": "lu", "name": "Lu", "pages": "a"}
    )
    assert resp.status_code == 302 and resp.location.endswith("/decks/lu/edit")
    empty = _save(app, pages="")
    assert empty.status_code == 400 and empty.get_json()["ok"] is False


# -- create ---------------------------------------------------------------------


def _create(app: Flask, **form: Any) -> dict[str, Any]:
    resp = app.test_client().post("/decks/new/lineup", data={"name": "New", **form})
    data = resp.get_json()
    assert data is not None
    return data


def test_the_new_lineup_flow_creates_through_the_lineup_path(app: Flask) -> None:
    store = app.config["DECK_STORE"]
    made = _create(app, when="at", start="06:00", page_ids=["a", "b"], days=["0", "6"])
    assert made["ok"] and made["editor_url"] == f"/decks/{made['id']}/edit"
    d = store.get(made["id"])
    assert (d.advance, d.advance_trigger, d.advance_fires_at) == ("timer", "daily", "06:00")
    assert d.page_ids == ["a", "b"] and d.advance_days_of_week == [0, 6]
    rot = store.get(
        _create(app, when="between", start="06:00", end="16:30", page_ids=["a", "b"])["id"]
    )
    assert (rot.advance_trigger, rot.advance_anchor, rot.advance_end_at) == (
        "cycle",
        "06:00",
        "16:30",
    )
    one = store.get(_create(app, when="between", start="06:00", end="09:00", page_ids=["a"])["id"])
    assert (one.advance_trigger, one.advance_window_start) == ("interval", "06:00")
    assert _create(app, when="between", start="09:00", end="06:00", page_ids=["a"])["ok"] is False
    assert _create(app, when="at", start="25:00", page_ids=["a"])["ok"] is False
