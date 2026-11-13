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
    assert summary["subtitle"].endswith("2 papers, chosen: The New York Times")


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
    assert got["warnings"] and "quiet hours" in got["warnings"][0]


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


def test_create_refuses_days_and_times_it_cannot_take(app: Flask) -> None:
    store = app.config["DECK_STORE"]
    for bad in (
        {"when": "at", "start": "06:00", "days": ""},  # no day ticked
        {"when": "at", "start": "06:00", "days": "9"},  # not a weekday
        {"when": "sometimes", "start": "06:00"},
    ):
        got = _create(app, page_ids=["a"], **bad)
        assert got["ok"] is False, bad
    assert store.all() == []


def test_create_refuses_a_window_that_meets_another_lineup(app: Flask) -> None:
    _register(app, "hall")
    app.config["DECK_STORE"].upsert(
        Deck(
            id="other", name="Other", device_ids=["hall"], pages=[DeckPage(page_id="b")],
            advance="timer", advance_trigger="cycle", advance_anchor="09:00",
            advance_end_at="11:00",
        )
    )  # fmt: skip
    form = {"device_ids": "hall", "page_ids": ["a", "c"], "when": "between"}
    clash = app.test_client().post(
        "/decks/new/lineup", data={"name": "New", "start": "08:00", "end": "10:00", **form}
    )
    assert clash.status_code == 409 and "Other" in clash.get_json()["error"]
    assert [d.id for d in app.config["DECK_STORE"].all()] == ["other"]
    # Touching windows don't meet.
    assert _create(app, start="07:00", end="09:00", **form)["ok"] is True


def test_create_warns_about_quiet_hours(app: Flask) -> None:
    _register(app, "hall")
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "locale": "en-US",  # 12-hour times, whatever the host's locale is
            "quiet_hours_enabled": True,
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:00",
        },
    )
    got = _create(app, when="at", start="06:00", device_ids="hall", page_ids=["a"])
    assert got["ok"] and got["warnings"] == ["Skipped: 6\u202fAM is in hall's quiet hours."]


# -- review fixes: the save keeps what it doesn't show ----------------------------


def test_saving_keeps_every_display_a_lineup_is_bound_to(app: Flask) -> None:
    _register(app, "hall")
    _register(app, "study")
    _deck(app, device_ids=["hall", "study"])
    html = app.test_client().get("/decks/lu/edit").get_data(as_text=True)
    state = json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])
    assert (state["display"], state["displays"]) == ("hall", ["hall", "study"])
    assert _save(app, device_ids=["hall", "study"]).get_json()["ok"]
    assert _stored(app).device_ids == ["hall", "study"]
    _save(app, device_ids="hall")  # an older client posts just the first
    assert _stored(app).device_ids == ["hall", "study"]
    _save(app, device_ids=["study", "hall"])
    assert _stored(app).device_ids == ["study", "hall"]


def test_a_partial_save_keeps_every_field_it_leaves_out(app: Flask) -> None:
    _register(app, "hall")
    _deck(
        app, name="Morning", enabled=True, device_ids=["hall"], advance="timer",
        advance_trigger="daily", advance_fires_at="07:00", home_page_id="b",
        entry_page_id="b", home_timeout_minutes=20,
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )  # fmt: skip
    resp = app.test_client().post(
        "/decks/editor-save", data={"deck_id": "lu", "pages": "a,b", "respond": "json"}
    )
    assert resp.get_json()["ok"]
    d = _stored(app)
    assert (d.name, d.enabled, d.device_ids) == ("Morning", True, ["hall"])
    assert (d.home_page_id, d.entry_page_id, d.home_timeout_minutes) == ("b", "b", 20)
    # The switch posts a hidden "0" before "on": off is said, not left out.
    _save(app, pages="a,b", enabled="0")
    assert _stored(app).enabled is False
    # A home whose dashboard leaves the lineup goes with it.
    _save(app, pages="a")
    assert (_stored(app).home_page_id, _stored(app).entry_page_id) == (None, None)


def test_the_editor_state_does_not_pin_home_to_the_first_dashboard(app: Flask) -> None:
    _deck(app, pages=[DeckPage(page_id="a"), DeckPage(page_id="b")])
    html = app.test_client().get("/decks/lu/edit").get_data(as_text=True)
    state = json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])
    assert state["home"] == ""
    _save(app, pages="b,a")
    assert _stored(app).home_page_id is None  # home still follows the order


def test_a_rotation_without_a_return_home_field_stores_none(app: Flask) -> None:
    """A rotation reclaims its display at each step, so a return home it can't
    show is off; a daily lineup keeps the one it has."""
    _deck(
        app, advance="timer", advance_trigger="cycle", home_timeout_minutes=15,
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
    )  # fmt: skip
    _save(app, pages="a,b")
    assert _stored(app).home_timeout_minutes == 0


# -- review fixes: a window's shape follows its dashboards ------------------------


def test_an_interval_given_a_second_dashboard_becomes_a_rotation(app: Flask) -> None:
    _deck(
        app, advance="timer", advance_trigger="interval", advance_window_start="06:00",
        advance_window_end="18:00", legacy_kind="schedule",
    )  # fmt: skip
    assert _save(app, pages="a,b").get_json()["ok"]  # no time edit
    d = _stored(app)
    assert (d.advance_trigger, d.advance_anchor, d.advance_end_at) == ("cycle", "06:00", "18:00")
    assert (d.advance_window_start, d.advance_window_end, d.legacy_kind) == (None, None, None)
    # Back to one dashboard: an interval again, the same window.
    _save(app, pages="a")
    d = _stored(app)
    assert (d.advance_trigger, d.advance_window_start, d.advance_window_end) == (
        "interval",
        "06:00",
        "18:00",
    )
    # With the time edited too.
    got = _when(app, "between", "07:00", "24:00", pages="a,b")
    d = _stored(app)
    assert got["ok"] and (d.advance_trigger, d.advance_anchor, d.advance_end_at) == (
        "cycle",
        "07:00",
        None,
    )


def test_a_rotation_cut_to_one_dashboard_keeps_its_cadence(app: Flask) -> None:
    """The remaining dashboard's dwell becomes the interval, not the 30 minute default."""
    _deck(
        app, advance="timer", advance_trigger="cycle", advance_anchor="06:00",
        advance_end_at="18:00", advance_interval_minutes=30,
        pages=[DeckPage(page_id="a", dwell_minutes=5), DeckPage(page_id="b")],
    )  # fmt: skip
    # The editor posts the lineup default and the dashboard's own dwell.
    assert _save(app, pages="a", advance_interval_minutes="30", **{"dwell[a]": "5"}).get_json()[
        "ok"
    ]
    d = _stored(app)
    assert (d.advance_trigger, d.advance_interval_minutes) == ("interval", 5)
    assert [p.dwell_minutes for p in d.pages] == [None]  # no dormant dwell left behind
    # A form without those fields (an older client) still keeps it.
    _deck(
        app, advance="timer", advance_trigger="cycle", advance_anchor="06:00",
        advance_end_at="18:00", advance_interval_minutes=30,
        pages=[DeckPage(page_id="a", dwell_minutes=5), DeckPage(page_id="b")],
    )  # fmt: skip
    assert _save(app, pages="a").get_json()["ok"]
    assert _stored(app).advance_interval_minutes == 5


def test_an_interval_drops_a_dwell_it_never_reads(app: Flask) -> None:
    _deck(
        app, advance="timer", advance_trigger="interval", advance_window_start="06:00",
        advance_window_end="18:00", advance_interval_minutes=30,
        pages=[DeckPage(page_id="a", dwell_minutes=5)],
    )  # fmt: skip
    assert _save(app, pages="a", advance_interval_minutes="30").get_json()["ok"]
    d = _stored(app)
    assert (d.advance_interval_minutes, d.pages[0].dwell_minutes) == (30, None)


def test_an_interval_past_midnight_cannot_take_a_second_dashboard(app: Flask) -> None:
    _deck(
        app, advance="timer", advance_trigger="interval", advance_window_start="22:00",
        advance_window_end="02:00",
    )  # fmt: skip
    got = _save(app, pages="a,b").get_json()
    assert got["ok"] is False and "midnight" in got["error"]
    assert _stored(app).page_ids == ["a"]


# -- review fixes: stale and deleted saves -------------------------------------------


def _revision(app: Flask) -> str:
    html = app.test_client().get("/decks/lu/edit").get_data(as_text=True)
    return json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])["revision"]


def test_a_save_from_a_stale_page_is_refused(app: Flask) -> None:
    _deck(app)
    loaded = _revision(app)
    store = app.config["DECK_STORE"]
    store.upsert(_stored(app).model_copy(update={"name": "Renamed elsewhere"}))
    resp = _save(app, name="Mine", revision=loaded)
    assert resp.status_code == 409
    assert resp.get_json() == {
        "ok": False,
        "error": "Changed elsewhere; reload to see it.",
        "reload": True,
    }
    assert _stored(app).name == "Renamed elsewhere"
    assert _save(app, name="Mine", revision=_revision(app)).get_json()["ok"]
    assert _stored(app).name == "Mine"


def test_a_save_of_a_deleted_lineup_is_refused_not_remade(app: Flask) -> None:
    _deck(app)
    loaded = _revision(app)
    app.config["DECK_STORE"].delete("lu")
    resp = _save(app, revision=loaded)
    assert resp.status_code == 409 and resp.get_json()["error"] == "This lineup was deleted."
    assert app.config["DECK_STORE"].all() == []


def test_a_save_still_carrying_another_displays_dashboard_is_refused(app: Flask) -> None:
    _register(app, "hall")
    _register(app, "study")
    app.config["PAGE_STORE"].save(Page(id="b", name="B", device_ids=["study"]))
    _deck(app, device_ids=["hall"])
    resp = _save(app, pages="a,b", device_ids="hall")
    assert resp.status_code == 409 and resp.get_json()["code"] == "pages_not_bound"
    assert _stored(app).page_ids == ["a"]


def test_a_reordered_rotation_resyncs_and_others_do_not(app: Flask) -> None:
    calls: list[str] = []
    app.config["SCHEDULER"] = SimpleNamespace(resync_cycle=calls.append)
    two = [DeckPage(page_id="a"), DeckPage(page_id="b")]
    _deck(app, advance="timer", advance_trigger="cycle", pages=two)
    _save(app, pages="a,b")
    assert calls == []  # same order
    _save(app, pages="b,a")
    assert calls == ["lu"]
    _deck(app, advance="timer", advance_trigger="daily", advance_fires_at="07:00", pages=two)
    _save(app, pages="a,b")
    assert calls == ["lu"]


# -- review fixes: overlaps that are not clashes -------------------------------------


@pytest.mark.parametrize(
    "other",
    [
        {"device_ids": ["study"]},  # another display
        {"advance_anchor": "10:00", "advance_end_at": "12:00"},  # starts as this ends
        {"advance_days_of_week": [5, 6]},  # other days
    ],
)
def test_a_window_next_to_another_lineup_saves(app: Flask, other: dict[str, Any]) -> None:
    _register(app, "hall")
    _register(app, "study")
    _deck(app, device_ids=["hall"], advance="timer", advance_trigger="daily",
          advance_fires_at="06:00", advance_days_of_week=[0, 1, 2, 3, 4])  # fmt: skip
    fields: dict[str, Any] = {
        "id": "other", "name": "Other", "device_ids": ["hall"], "pages": [DeckPage(page_id="b")],
        "advance": "timer", "advance_trigger": "cycle", "advance_anchor": "08:00",
        "advance_end_at": "12:00", **other,
    }  # fmt: skip
    app.config["DECK_STORE"].upsert(Deck(**fields))
    got = _when(app, "between", "06:00", "10:00", device_ids="hall", pages="a,c")
    assert got["ok"] is True, got


# -- review fixes: dashboard edits never fail a stored save ---------------------------


def test_a_pin_the_widget_cannot_list_warns(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(options: dict[str, Any]) -> list[dict[str, Any]]:
        raise OSError("offline")

    _install(
        app,
        monkeypatch,
        papers={
            "manifest": {"lineups": {"pin_option": "paper"}},
            "module": SimpleNamespace(lineup_items=broken),
        },
    )
    _widget_page(app, "papers", {"paper": ""}, "news")
    _deck(app, pages=[DeckPage(page_id="news")])
    got = _save(app, pages="news", **{"pin[news]": "wsj"}).get_json()
    assert got["ok"] and got["warnings"] == ["Couldn't pin news; its list didn't load."]


def test_a_dashboard_write_that_fails_warns_instead_of_500(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deck(app)
    pages = app.config["PAGE_STORE"]

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(pages, "update_if_revision", fail)
    resp = _save(app, name="Kept", **{"page_refresh[a]": "30"})
    assert resp.status_code == 200
    assert resp.get_json()["warnings"] == ["Saved, but A's settings weren't applied."]
    assert _stored(app).name == "Kept"


def test_a_dashboard_edited_meanwhile_keeps_that_edit(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Updates write is a compare-and-swap: a composer save between its
    read and its write is kept, and Updates lands on top of it."""
    _deck(app)
    pages = app.config["PAGE_STORE"]
    real = pages.update_if_revision
    raced: list[bool] = []

    def racing(page_id: str, revision: str, transform: Any) -> Any:
        if not raced:
            raced.append(True)
            pages.save(pages.get(page_id).model_copy(update={"name": "Renamed in the composer"}))
        return real(page_id, revision, transform)

    monkeypatch.setattr(pages, "update_if_revision", racing)
    assert _save(app, **{"page_refresh[a]": "30"}).get_json()["warnings"] == []
    page = pages.get("a")
    assert (page.name, page.refresh_minutes) == ("Renamed in the composer", 30)


def test_unicode_digits_never_crash_a_request(app: Flask) -> None:
    _deck(app)
    c = app.test_client()
    assert c.get("/decks/dashboard-items?page_id=a&offset=²&limit=²").status_code == 200
    assert _save(app, **{"page_refresh[a]": "²"}).status_code == 200
    assert _save(app, advance_days=["²", "1"]).get_json()["ok"]
    assert _stored(app).advance_days_of_week == [1]


def test_dashboard_items_bounds(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.step_items import MAX_PAGE_ITEMS

    _gallery(app, monkeypatch, tmp_path, 412)
    c = app.test_client()

    def get(query: str) -> dict[str, Any]:
        resp = c.get(f"/decks/dashboard-items?page_id=photos&{query}")
        assert resp.status_code == 200, query
        return resp.get_json()

    assert len(get("limit=100000")["items"]) == MAX_PAGE_ITEMS
    assert get("offset=5000")["items"] == []
    assert get("offset=abc&limit=2")["items"][0]["index"] == 0
    assert get("q=" + "x" * 5000)["matched"] == 0  # a long query is cut, not refused


def test_a_widget_whose_list_hook_raises_still_lists_nothing(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(options: dict[str, Any]) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    _install(
        app,
        monkeypatch,
        papers={
            "manifest": {"lineups": {"pin_option": "paper"}},
            "module": SimpleNamespace(lineup_items=broken),
        },
    )
    _widget_page(app, "papers", {"paper": ""}, "news")
    got = app.test_client().get("/decks/dashboard-items?page_id=news").get_json()
    assert got["ok"] and (got["mode"], got["items"]) == ("none", [])
    _deck(app, pages=[DeckPage(page_id="news")])
    assert app.test_client().get("/decks/lu/edit").status_code == 200


# -- review fixes: what the editor is told -------------------------------------------


def test_the_editor_knows_which_dashboards_other_lineups_share(app: Flask) -> None:
    _deck(app, pages=[DeckPage(page_id="a"), DeckPage(page_id="b")])
    app.config["DECK_STORE"].upsert(Deck(id="other", name="O", pages=[DeckPage(page_id="b")]))
    html = app.test_client().get("/decks/lu/edit").get_data(as_text=True)
    state = json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])
    assert (state["pages"]["a"]["shared"], state["pages"]["b"]["shared"]) == (0, 1)


def test_a_random_gallery_says_so(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.lineup_editor import dashboard_summary

    photos = _gallery(app, monkeypatch, tmp_path, 5)
    shuffled = photos.model_copy(
        update={"cells": [photos.cells[0].model_copy(update={"options": {"folder": "trip"}})]}
    )
    with app.test_request_context():
        assert dashboard_summary(photos)["items"]["order"] == "in turn"
        assert dashboard_summary(shuffled)["items"]["order"] == "random"


def test_a_widget_may_name_its_items_in_its_manifest() -> None:
    import jsonschema

    schema = json.loads((REPO_ROOT / "schema" / "plugin.schema.json").read_text(encoding="utf-8"))
    manifest = {
        "tesserae_compat": "1.x",
        "name": "Papers",
        "version": "0.1.0",
        "kind": "widget",
        "description": "x",
        "supports": {"sizes": ["lg"]},
        "lineups": {"pin_option": "paper", "noun": "papers"},
    }
    jsonschema.validate(manifest, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**manifest, "lineups": {"noun": ""}}, schema)


def test_deleting_says_lineup(app: Flask) -> None:
    _deck(app)
    resp = app.test_client().post("/decks/lu/delete", follow_redirects=True)
    assert b"Lineup deleted." in resp.data


# -- item hooks: render context and preview images --------------------------------


def test_an_item_hook_gets_ctx_only_when_it_asks_and_old_hooks_still_work(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(app, "tv")
    seen: list[dict[str, Any]] = []

    def new_hook(options: dict[str, Any], *, ctx: dict[str, Any] | None = None) -> list[Any]:
        seen.append(ctx or {})
        return PAPERS

    def count_hook(options: dict[str, Any], *, ctx: dict[str, Any] | None = None) -> int:
        seen.append(ctx or {})
        return 7

    manifest = {"lineups": {"pin_option": "paper", "noun": "papers"}}
    _install(
        app,
        monkeypatch,
        newpaper={"manifest": manifest, "module": SimpleNamespace(lineup_items=new_hook)},
        oldpaper={
            "manifest": manifest,
            "module": SimpleNamespace(lineup_items=lambda options: PAPERS),
        },
        drawer={
            "manifest": {"lineups": {"draw": True}},
            "module": SimpleNamespace(lineup_item_count=count_hook),
        },
    )
    ps = app.config["PAGE_STORE"]
    for pid, plugin in (("p1", "newpaper"), ("p2", "oldpaper"), ("p3", "drawer")):
        ps.save(
            Page(
                id=pid,
                name=pid,
                device_ids=["tv"],
                cells=[Cell(id="c1", plugin=plugin, x=0, y=0, w=10, h=10, options={})],
            )
        )
    c = app.test_client()
    got = c.get("/decks/dashboard-items?page_id=p1&display_id=tv").get_json()
    assert got["total"] == 2
    ctx = seen[-1]
    assert set(ctx) >= {
        "data_dir", "cell_w", "cell_h", "placement_id", "device_id", "panel_w", "panel_h"
    }  # fmt: skip
    assert ctx["placement_id"] == "c1" and ctx["device_id"] == "tv"
    assert ctx["cell_w"] > 0 and ctx["cell_h"] > 0 and ctx["panel_w"] > 0
    # Without a display: the dashboard's first bound one.
    c.get("/decks/dashboard-items?page_id=p1")
    assert seen[-1]["device_id"] == "tv"
    # A hook with the old one-argument signature is called as before.
    assert c.get("/decks/dashboard-items?page_id=p2").get_json()["total"] == 2
    # The count hook gets it too.
    assert c.get("/decks/dashboard-items?page_id=p3").get_json()["total"] == 7
    assert seen[-1]["placement_id"] == "c1"


def test_an_item_can_carry_a_preview_image_and_only_safe_urls_survive(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = [
        {"key": "a", "label": "A", "thumb": "https://img.example/a.jpg"},
        {"key": "b", "label": "B", "thumb": "/plugins/x/b.png"},
        {"key": "c", "label": "C", "thumb": "javascript:alert(1)"},
        {"key": "d", "label": "D", "thumb": "//evil.example/d.jpg"},
        {"key": "e", "label": "E", "thumb": 'https://x.example/"onerror=1'},
        {"key": "f", "label": "F"},
    ]
    _install(
        app,
        monkeypatch,
        papers={
            "manifest": {"lineups": {"pin_option": "paper"}},
            "module": SimpleNamespace(lineup_items=lambda options: items),
        },
    )
    _widget_page(app, "papers", {}, "news")
    got = app.test_client().get("/decks/dashboard-items?page_id=news").get_json()
    assert [it["thumb_url"] for it in got["items"]] == [
        "https://img.example/a.jpg",
        "/plugins/x/b.png",
        "",
        "",
        "",
        "",
    ]


def test_a_gallery_set_to_fit_says_its_previews_are_letterboxed(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _gallery(app, monkeypatch, tmp_path, 5)
    c = app.test_client()
    assert c.get("/decks/dashboard-items?page_id=photos").get_json()["fit"] is True
    _widget_page(
        app, "picture_gallery", {"folder": "trip", "mode": "sequential", "scale": "fill"}, "fills"
    )
    assert c.get("/decks/dashboard-items?page_id=fills").get_json()["fit"] is False


def test_a_list_widget_that_lists_nothing_is_flagged_empty_not_plain(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.lineup_editor import dashboard_summary
    from app.step_items import step_choices, step_empty

    _install(
        app,
        monkeypatch,
        none={
            "manifest": {"lineups": {"pin_option": "pick"}},
            "module": SimpleNamespace(lineup_items=lambda options: []),
        },
        one={
            "manifest": {"lineups": {"pin_option": "pick"}},
            "module": SimpleNamespace(lineup_items=lambda options: [{"key": "a", "label": "A"}]),
        },
    )
    with app.test_request_context():
        empty_page = _widget_page(app, "none", {}, "p1")
        one_page = _widget_page(app, "one", {}, "p2")
        assert step_empty(empty_page) and not step_empty(one_page)
        found = step_choices(empty_page)
        assert found is not None and found.mode == "empty"
        summary = dashboard_summary(empty_page)["items"]
    assert summary["mode"] == "none" and summary["empty"] is True


def test_a_widget_declaring_a_list_and_a_draw_draws_when_it_lists_nothing(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.lineup_editor import dashboard_summary
    from app.step_items import step_choices, step_draws

    manifest = {"lineups": {"pin_option": "pinned_asset", "draw": True}}
    _install(
        app,
        monkeypatch,
        bare={"manifest": manifest, "module": SimpleNamespace(lineup_items=lambda o: [])},
        counted={
            "manifest": manifest,
            "module": SimpleNamespace(lineup_items=lambda o: [], lineup_item_count=lambda o: 40),
        },
        listed={
            "manifest": manifest,
            "module": SimpleNamespace(
                lineup_items=lambda o: [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}]
            ),
        },
    )
    with app.test_request_context():
        bare = _widget_page(app, "bare", {}, "p1")
        assert step_draws(bare)
        found = step_choices(bare)
        assert found is not None and found.mode == "draw"
        assert dashboard_summary(bare)["items"]["mode"] == "draw"
        assert dashboard_summary(bare)["items"]["empty"] is False
        assert dashboard_summary(_widget_page(app, "counted", {}, "p2"))["items"]["count"] == 40
        listed = _widget_page(app, "listed", {}, "p3")
        assert not step_draws(listed)
        assert dashboard_summary(listed)["items"]["mode"] == "pick"


def test_recent_sent_lists_the_pictures_a_dashboard_sent_newest_first(app: Flask) -> None:
    import hashlib
    import io

    from PIL import Image

    from app.deck_routes import _recent_sent

    digests = []
    for i in range(10):
        buf = io.BytesIO()
        Image.new("RGB", (8, 8), (i * 20, 0, 0)).save(buf, "PNG")
        digest = hashlib.sha256(buf.getvalue()).hexdigest()[:16]
        (app.config["RENDERS_DIR"] / f"{digest}.png").write_bytes(buf.getvalue())
        app.config["EVENT_LOG"].record(
            type="push",
            source="page",
            target="album",
            status="sent",
            extra={"composition_digest": digest},
        )
        digests.append(digest)
    with app.test_request_context():
        got = _recent_sent("album")
        assert _recent_sent("elsewhere") == []
    assert len(got) == 8
    assert [r["thumb"].split("/")[-1].split(".")[0] for r in got] == digests[::-1][:8]


def test_widget_thumbs_follow_the_ingress_prefix(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    ingress = "/api/hassio_ingress/abc"
    listed = [
        {"key": "a", "thumb": "/plugins/picture_immich/image/l/a?thumb=1"},
        {"key": "b", "thumb": f"{ingress}/plugins/picture_immich/image/l/b?thumb=1"},
        {"key": "c", "thumb": "https://civitai.example/x.jpg"},
    ]
    module = SimpleNamespace(lineup_items=lambda options: listed)
    _install(
        app,
        monkeypatch,
        photos={"manifest": {"lineups": {"pin_option": "photo"}}, "module": module},
    )
    _widget_page(app, "photos", {}, "ph")
    c = app.test_client()

    def thumbs(**kw: Any) -> list[str]:
        got = c.get("/decks/dashboard-items?page_id=ph", **kw).get_json()
        return [it["thumb_url"] for it in got["items"]]

    assert thumbs(headers={"X-Ingress-Path": ingress}) == [
        f"{ingress}/plugins/picture_immich/image/l/a?thumb=1",
        f"{ingress}/plugins/picture_immich/image/l/b?thumb=1",
        "https://civitai.example/x.jpg",
    ]
    assert thumbs() == [
        "/plugins/picture_immich/image/l/a?thumb=1",
        f"{ingress}/plugins/picture_immich/image/l/b?thumb=1",
        "https://civitai.example/x.jpg",
    ]


def _quiet(app: Flask, start: str = "01:00", end: str = "08:00", **extra: Any) -> None:
    app.config["SETTINGS_STORE"].patch_section(
        "app",
        {
            "locale": "en-US",
            "quiet_hours_enabled": True,
            "quiet_hours_start": start,
            "quiet_hours_end": end,
            **extra,
        },
    )


def _cycle(app: Flask, **fields: Any) -> None:
    _deck(
        app,
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
        device_ids=["hall"],
        advance="timer",
        advance_trigger="cycle",
        advance_anchor="06:00",
        advance_end_at="18:30",
        advance_days_of_week=[0, 1, 2, 3, 4],
        **fields,
    )


def test_save_warns_in_words_about_what_quiet_hours_do(app: Flask) -> None:
    _register(app, "hall")
    _quiet(app)
    _cycle(app)
    nb = "\u202f"
    got = _when(app, "between", "06:00", "17:00", device_ids="hall")
    assert got["warnings"] == [f"Starts at 8{nb}AM: 6–8{nb}AM is in hall's quiet hours."]
    got = _when(app, "between", "02:00", "05:00", device_ids="hall")
    assert got["warnings"] == ["Sends nothing: all of it is in hall's quiet hours."]
    _quiet(app, "17:00", "19:00")
    got = _when(app, "between", "06:00", "18:00", device_ids="hall")
    assert got["warnings"] == [f"Stops at 5{nb}PM: 5–6{nb}PM is in hall's quiet hours."]
    _quiet(app, "12:00", "13:00")
    got = _when(app, "between", "06:00", "18:15", device_ids="hall")
    assert got["warnings"] == [f"Pauses 12–1{nb}PM for hall's quiet hours."]


def test_save_warning_follows_the_clock_style_and_day_masks(app: Flask) -> None:
    _register(app, "hall")
    _quiet(app, locale="de-DE")
    _cycle(app)
    got = _when(app, "between", "06:00", "17:00", device_ids="hall")
    assert got["warnings"] == ["Starts at 08:00: 06:00–08:00 is in hall's quiet hours."]
    # Quiet hours that only apply on the weekend leave a Mon-Fri lineup alone.
    _quiet(app, quiet_hours_days=[5, 6])
    got = _when(app, "between", "06:00", "17:00", device_ids="hall")
    assert got["warnings"] == []


def test_a_save_that_leaves_times_days_and_display_alone_does_not_warn(app: Flask) -> None:
    _register(app, "hall")
    _quiet(app)
    _cycle(app)
    # Saving an unrelated field (the name) on a lineup already in quiet hours.
    got = _save(app, name="Renamed", device_ids="hall", pages="a,b").get_json()
    assert got["ok"] and got["warnings"] == []
    # Changing the days is a timing change: the warning comes back.
    got = _save(
        app, name="Renamed", device_ids="hall", pages="a,b", when_changed="1", when_mode="between",
        when_start="06:00", when_end="18:30", advance_days=["0", "1"],
    ).get_json()  # fmt: skip
    assert got["ok"] and len(got["warnings"]) == 1


def test_inline_edit_warns_only_when_the_edit_changed_the_times(app: Flask) -> None:
    _register(app, "hall")
    _quiet(app)
    _cycle(app)
    client = app.test_client()
    same = client.post("/decks/lu/window", json={"start": "06:00", "end": "18:30"}).get_json()
    assert same["warnings"] == []
    moved = client.post("/decks/lu/window", json={"start": "05:00"}).get_json()
    assert len(moved["warnings"]) == 1 and moved["warnings"][0].startswith("Starts at ")


# -- item hooks are held to the widget's own limits ----------------------------


def test_item_hooks_run_inside_the_widgets_capability_scope(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook's network calls are checked against the widget's allowlist, as
    its data fetch is, so the scope in force during the call is the widget's."""
    from app import capabilities
    from app.step_items import list_items

    caps = capabilities.parse("scoped", {"network": ["photos.example"]})
    seen: list[Any] = []

    def lineup_items(options: dict[str, Any]) -> Any:
        seen.append(capabilities._active.get())
        yield from PAPERS  # read inside the scope too
        seen.append(capabilities._active.get())

    reg = app.config["PLUGIN_REGISTRY"]
    real = reg.get
    plugin = SimpleNamespace(
        manifest={"lineups": {"pin_option": "paper"}},
        server_module=SimpleNamespace(lineup_items=lineup_items),
        capabilities=caps,
    )
    monkeypatch.setattr(reg, "get", lambda pid: plugin if pid == "scoped" else real(pid))
    with app.test_request_context():
        found = list_items(_widget_page(app, "scoped", {}, "p1"))
    assert found.total == 2
    assert seen == [caps, caps]
    assert capabilities._active.get() is None


def test_an_item_listing_is_capped_and_reused_for_a_moment(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    import itertools

    from app.step_items import MAX_HOOK_ITEMS, list_items

    calls: list[int] = []

    def lineup_items(options: dict[str, Any]) -> Any:
        calls.append(1)
        return ({"key": f"k{i}", "label": f"Item {i}"} for i in itertools.count())

    _install(
        app,
        monkeypatch,
        endless={
            "manifest": {"lineups": {"pin_option": "paper"}},
            "module": SimpleNamespace(lineup_items=lineup_items),
        },
    )
    with app.test_request_context():
        page = _widget_page(app, "endless", {}, "p1")
        assert list_items(page).total == MAX_HOOK_ITEMS
        assert list_items(page, query="item 7").matched > 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("declared", "shown"),
    [
        ("papers", "papers"),
        ("front pages", "front pages"),
        ('"><img src=x onerror=alert(1)>', "items"),
        ("x" * 40, "items"),
        (7, "items"),
    ],
)
def test_a_widgets_noun_is_letters_only(
    app: Flask, monkeypatch: pytest.MonkeyPatch, declared: Any, shown: str
) -> None:
    from app.step_items import _noun

    _install(app, monkeypatch, named={"manifest": {"lineups": {"noun": declared}}})
    with app.test_request_context():
        assert _noun("named") == shown


def test_moving_a_lineup_clears_the_display_it_left(app: Flask) -> None:
    """The old display's warmed frames and its place in the lineup go when the
    lineup moves; before, only the new display was cleared."""
    _register(app, "hall")
    _register(app, "study")
    app.config["PAGE_STORE"].save(Page(id="a", name="A", device_ids=["hall", "study"]))
    _deck(app, device_ids=["hall"])
    cleared: list[str] = []
    app.config["PUSH_MANAGER"] = SimpleNamespace(clear_deck_cache=cleared.append)
    nav = app.config["DECK_NAV_STORE"]
    nav.set("hall", "lu", "a")
    resp = _save(app, device_ids="study")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _stored(app).device_ids == ["study"]
    assert sorted(cleared) == ["hall", "study"]
    assert nav.get("hall") is None
