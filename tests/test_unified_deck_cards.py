"""The Lineups page: one section per display, one row per deck with a kind
badge and a kind-specific body (screen cards, 24h rail, steppers). Internal
noun: deck; kinds surface as By hand / Rotation / Schedule."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page
from app.state.rotation_model import Rotation, RotationStep
from app.state.schedule_model import Schedule


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    for pid in ("kitchen", "hall"):
        a.config["PAGE_STORE"].save(Page(id=pid, name=pid.title()))
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _seed_all_shapes(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(
        Deck(id="wayfind", name="Hall wayfinding", pages=[DeckPage(page_id="hall")])
    )
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="loop",
            name="Kitchen loop",
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )
    app.config["SCHEDULE_STORE"].upsert(
        Schedule(
            id="brief",
            name="Morning brief",
            page_id="kitchen",
            type="interval",
            interval_minutes=30,
        )
    )


def test_every_shape_renders_as_a_unified_card(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)

    for kind in ("nav", "cycle", "send"):
        assert f'data-kind="{kind}"' in body
    for name in ("Hall wayfinding", "Kitchen loop", "Morning brief"):
        assert name in body
    # One row anatomy everywhere: header strip + kind badge + body.
    assert body.count('class="dk-row-head"') == 3
    assert "Rotation" in body and "Schedule" in body and "By hand" in body
    # Kind-specific bodies: cycle chevrons, send 24h rail, nav steppers.
    assert "dk-body--cycle" in body and "dk-rail" in body and "dk-stepper" in body
    # Screen cards carry composer thumbnails.
    assert "/compose/" in body
    # The old listings stay gone.
    assert "Saved schedules" not in body
    assert 'class="card deck-card"' not in body
    # Filter tabs and the per-kind creation links are gone; the wizard is
    # the one entry point.
    assert "data-kind-filter" not in body
    assert "New timed send" not in body and "New timer cycle" not in body
    assert "data-open-timed-wizard" in body
    # With no registered displays, everything sits in the unbound group.
    assert "Not on a display yet" in body


def test_cycle_card_wires_play_fire_toggle_delete(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "/rotations/loop/fire" in body
    # Play step advances one dashboard: a single link to the next index.
    assert "/rotations/loop/play/" in body
    assert "/rotations/loop/toggle" in body
    assert "/rotations/loop/delete" in body
    assert "/decks/loop/edit" in body  # one editor for every shape (#167)


def test_send_card_wires_schedule_endpoints_and_next_fire(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "/schedules/brief/fire" in body
    assert "/schedules/brief/toggle" in body
    assert "sedit=brief" in body
    assert "Kitchen · every 30 min" in body
    # The 24h rail replaces the old timeline panel.
    assert "dk-rail-scale" in body and "Next 24 hours" not in body


def test_edit_paths_open_the_right_editor(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    # Cycles edit in the deck editor (the one editor for every shape),
    # including the per-page conditions fold and the old-URL deep link.
    body = client.get("/decks/loop/edit").get_data(as_text=True)
    assert "Kitchen loop" in body
    assert "Page conditions" in body
    assert 'name="conditions[kitchen]"' in body
    resp = client.get("/rotations?edit=loop", follow_redirects=False)
    assert resp.status_code in (302, 303) and "/decks/loop/edit" in resp.location
    # Timed sends keep their inline form via sedit.
    body = client.get("/decks?sedit=brief").get_data(as_text=True)
    assert 'value="Morning brief"' in body  # schedule edit form open


def test_condition_badge_marks_screens_with_conditions(app: Flask) -> None:
    """A screen whose page carries conditions gets the funnel badge linking
    to the record's editor; unconditioned screens stay clean."""
    _seed_all_shapes(app)
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="loop",
            name="Kitchen loop",
            steps=[
                RotationStep(
                    page_id="kitchen",
                    dwell_minutes=15,
                    conditions=[
                        {
                            "source_kind": "ha_entity",
                            "source_id": "binary_sensor.motion",
                            "operator": "==",
                            "value": "on",
                        }
                    ],
                ),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert body.count('class="dk-cond"') == 1
    assert 'class="dk-cond" href="/decks/loop/edit"' in body


def test_disabled_state_and_summary_render(app: Flask) -> None:
    _seed_all_shapes(app)
    schedule = app.config["SCHEDULE_STORE"].get("brief")
    app.config["SCHEDULE_STORE"].upsert(schedule.model_copy(update={"enabled": False}))
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "is-off" in body
    assert ">Enable<" in body


def test_editor_saves_per_page_conditions_and_lead(app: Flask) -> None:
    """#167 consolidation: the deck editor is where cycle conditions live now.
    Authored JSON persists; bad JSON falls back to the stored value instead
    of poisoning the record."""
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    cond = (
        '[{"source_kind": "ha_entity", "source_id": "binary_sensor.printer",'
        ' "operator": "==", "value": "on"}]'
    )
    resp = client.post(
        "/decks/editor-save",
        data={
            "deck_id": "loop",
            "name": "Kitchen loop",
            "pages": "kitchen,hall",
            "advance": "timer",
            "advance_smart_sync": "on",
            "advance_smart_sync_lead_s": "45",
            "conditions[kitchen]": cond,
            "conditions[hall]": "",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    deck = app.config["DECK_STORE"].get("loop")
    assert deck is not None
    assert deck.pages[0].conditions[0].source_id == "binary_sensor.printer"
    assert deck.pages[1].conditions == []
    assert deck.advance_smart_sync_lead_s == 45
    # A save omitting the fields preserves what's stored.
    client.post(
        "/decks/editor-save",
        data={"deck_id": "loop", "name": "Kitchen loop", "pages": "kitchen,hall"},
    )
    deck = app.config["DECK_STORE"].get("loop")
    assert deck.pages[0].conditions[0].source_id == "binary_sensor.printer"


def test_new_timer_cycle_entry_preselects_timer(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks/new?mode=timer").get_data(as_text=True)
    assert 'value="timer" checked' in body
    body = client.get("/decks/new").get_data(as_text=True)
    assert 'value="manual" checked' in body


def test_manual_stepper_moves_the_display(app: Flask) -> None:
    """The by-hand row's steppers move each bound display one dashboard
    through the deck order and record the nav position."""
    from unittest.mock import MagicMock

    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(
        Deck(
            id="wayfind",
            name="Hall wayfinding",
            device_ids=["panel"],
            pages=[DeckPage(page_id="kitchen"), DeckPage(page_id="hall")],
        )
    )
    push = MagicMock()
    push.promote_deck_page.return_value = False
    push.push.return_value = MagicMock(status="sent")
    app.config["PUSH_MANAGER"] = push
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/decks/wayfind/step", data={"dir": "next"}, follow_redirects=False)
    assert resp.status_code in (302, 303) and "hl=wayfind" in resp.location
    rec = app.config["DECK_NAV_STORE"].get("panel")
    assert rec is not None and rec["deck_id"] == "wayfind"
    first = rec["page_id"]
    client.post("/decks/wayfind/step", data={"dir": "next"})
    second = app.config["DECK_NAV_STORE"].get("panel")["page_id"]
    assert second != first
    client.post("/decks/wayfind/step", data={"dir": "prev"})
    assert app.config["DECK_NAV_STORE"].get("panel")["page_id"] == first


def test_display_group_carries_live_status_and_device_chip(app: Flask) -> None:
    """Rows group per display; the section header shows what's on glass and
    each row bar names the display it targets."""
    import json as _json

    from app.state.deck_model import Deck, DeckPage

    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "dk-group-live" not in body  # no registered display, nothing live

    # Register a real device instance (the live map only counts those),
    # bind the nav deck to it, and give it a nav record.
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=_json.dumps({"device_id": "panel", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201
    app.config["DECK_STORE"].upsert(
        Deck(
            id="wayfind",
            name="Hall wayfinding",
            device_ids=["panel"],
            pages=[DeckPage(page_id="hall")],
        )
    )
    app.config["DECK_NAV_STORE"].set("panel", "wayfind", "hall")
    body = client.get("/decks").get_data(as_text=True)
    # The display's section exists and reports what it is showing.
    assert 'id="display-panel"' in body
    assert "dk-group-live" in body and "showing Hall" in body
    # The section header names the display; the row no longer repeats it.
    assert "dk-devchip" not in body
    # The live screen card lights up on the playing row.
    assert "dk-screen is-live" in body


def _register_display(app: Flask, client, device_id: str) -> None:
    import json as _json

    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=_json.dumps({"device_id": device_id, "kind": "esp32_client"}),
    )
    assert resp.status_code == 201


@pytest.mark.parametrize("intent", ["manual", "cycle", "daily", "interval"])
@pytest.mark.parametrize("wipe_orphan", [False, True])
def test_deleted_display_lineup_remains_manageable(
    app: Flask, intent: str, wipe_orphan: bool
) -> None:
    """Deleting a display must not hide its retained Lineups from the web UI."""
    from app.lineup_authoring import build_lineup

    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "kobo")
    pages = app.config["PAGE_STORE"]
    pages.save(pages.get("kitchen").model_copy(update={"device_ids": ["kobo"]}))
    deck = build_lineup(
        intent=intent,
        lineup_id="kobo-lineup",
        name="Kobo lineup",
        page_ids=["kitchen"],
        device_ids=["kobo"],
        fires_at="08:00",
    ).model_copy(update={"enabled": False})
    app.config["DECK_STORE"].upsert(deck)
    before = client.get("/decks").get_data(as_text=True)
    assert 'id="display-kobo"' in before
    assert 'id="udeck-kobo-lineup"' in before

    deleted = client.post(
        "/settings/devices/kobo/delete", data={"wipe_orphan": "1"} if wipe_orphan else {}
    )
    assert deleted.status_code == 302
    assert app.config["DEVICE_REGISTRY"].get("kobo") is None
    body = client.get("/decks").get_data(as_text=True)
    assert "Unavailable displays" in body
    assert 'id="display-kobo"' not in body
    assert body.count('id="udeck-kobo-lineup"') == 1
    assert app.config["DECK_STORE"].get(deck.id) == deck

    edit_url = (
        "/decks/kobo-lineup/edit"
        if intent in ("manual", "cycle")
        else "/decks?sedit=kobo-lineup#schedule-form-card"
    )
    assert f'href="{edit_url}"' in body
    editor = client.get(edit_url)
    assert editor.status_code == 200
    editor_html = editor.get_data(as_text=True)
    assert "Kobo lineup" in editor_html
    # The editors must keep the stale binding selectable: the deck editor's
    # display select would otherwise fall back to "Choose a display" and a
    # plain save would unbind the deck; the schedule form would preselect
    # the first dashboard once the wiped page vanished from the list.
    if intent in ("manual", "cycle"):
        assert '<option value="kobo" selected>kobo (unavailable)</option>' in editor_html
        saved = client.post(
            "/decks/editor-save",
            data={
                "deck_id": "kobo-lineup",
                "name": "Kobo lineup renamed",
                "pages": "kitchen",
                "device_ids": "kobo",
                # Keep a cycle lineup on its timer so it stays a rotation.
                "advance": "timer" if intent == "cycle" else "manual",
            },
        )
    else:
        if wipe_orphan:
            assert (
                '<option value="kitchen" selected>kitchen (missing dashboard)</option>'
                in editor_html
            )
        saved = client.post(
            "/schedules/kobo-lineup/update",
            data={
                "name": "Kobo lineup renamed",
                "page_id": "kitchen",
                "type": "daily",
                "fires_at": "08:00",
            },
        )
    assert saved.status_code == 302
    after_save = app.config["DECK_STORE"].get(deck.id)
    assert after_save.name == "Kobo lineup renamed"
    assert after_save.device_ids == ["kobo"]
    assert [dp.page_id for dp in after_save.pages] == ["kitchen"]
    delete_kind = {"manual": "decks", "cycle": "rotations"}.get(intent, "schedules")
    delete_url = f"/{delete_kind}/kobo-lineup/delete"
    assert f'action="{delete_url}"' in body
    assert client.post(delete_url).status_code == 302
    assert app.config["DECK_STORE"].get(deck.id) is None
    assert 'id="udeck-kobo-lineup"' not in client.get("/decks").get_data(as_text=True)


def test_unavailable_group_preserves_shared_and_unassigned_lineups(app: Flask) -> None:
    """Only Lineups with targets but no surviving display need the fallback."""
    from app.lineup_authoring import build_lineup

    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel_a")
    _register_display(app, client, "panel_b")
    pages = app.config["PAGE_STORE"]
    pages.save(pages.get("kitchen").model_copy(update={"device_ids": ["deleted"]}))
    specs = [
        ("missing", "manual", ["deleted", "also-deleted"], "hall"),
        ("partial", "cycle", ["panel_a", "deleted"], "hall"),
        ("shared", "manual", ["panel_a", "panel_b"], "hall"),
        ("inherited", "daily", [], "kitchen"),
        ("unassigned", "manual", [], "hall"),
    ]
    for lineup_id, intent, device_ids, page_id in specs:
        app.config["DECK_STORE"].upsert(
            build_lineup(
                intent=intent,
                lineup_id=lineup_id,
                name=lineup_id,
                page_ids=[page_id],
                device_ids=device_ids,
                fires_at="08:00",
            ).model_copy(update={"enabled": False})
        )

    body = client.get("/decks").get_data(as_text=True)
    groups = re.findall(r'<section class="dk-group"[^>]*>.*?</section>', body, re.S)
    unavailable = next(g for g in groups if "Unavailable displays" in g)
    unassigned = next(g for g in groups if "Not on a display yet" in g)
    panel_a = next(g for g in groups if 'id="display-panel_a"' in g)
    panel_b = next(g for g in groups if 'id="display-panel_b"' in g)
    assert set(re.findall(r'id="udeck-([^"]+)"', unavailable)) == {"missing", "inherited"}
    assert set(re.findall(r'id="udeck-([^"]+)"', unassigned)) == {"unassigned"}
    assert 'id="udeck-partial"' in panel_a
    assert '<span class="dk-name">shared</span>' in panel_a
    assert '<span class="dk-name">shared</span>' in panel_b
    assert body.count('id="udeck-shared"') == 1
    assert app.config["DECK_STORE"].get("partial").device_ids == ["panel_a", "deleted"]


def test_unbound_rotation_spanning_displays_warns(app: Flask) -> None:
    """A rotation with no binding whose member dashboards live on different
    displays sends each page to its own panel, so nothing visibly rotates;
    the card says so instead of implying it plays on both."""
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel_a")
    _register_display(app, client, "panel_b")
    store = app.config["PAGE_STORE"]
    store.save(store.get("kitchen").model_copy(update={"device_ids": ["panel_a"]}))
    store.save(store.get("hall").model_copy(update={"device_ids": ["panel_b"]}))
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="split",
            name="Split loop",
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )
    body = client.get("/decks").get_data(as_text=True)
    assert "dk-warn" in body
    assert "different displays" in body


def test_bound_rotation_with_foreign_page_warns(app: Flask) -> None:
    """A rotation bound to one display can't render a member dashboard bound
    only to another (the push intersects bindings and fails); the card names
    the stranded dashboard."""
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel_a")
    _register_display(app, client, "panel_b")
    store = app.config["PAGE_STORE"]
    store.save(store.get("kitchen").model_copy(update={"device_ids": ["panel_a"]}))
    store.save(store.get("hall").model_copy(update={"device_ids": ["panel_b"]}))
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="pinned",
            name="Pinned loop",
            device_ids=["panel_a"],
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )
    body = client.get("/decks").get_data(as_text=True)
    assert "dk-warn" in body
    assert "Hall" in body and "cannot show here" in body


def test_single_display_setup_has_no_warning(app: Flask) -> None:
    """The healthy shape stays quiet: everything on one display."""
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel_a")
    store = app.config["PAGE_STORE"]
    store.save(store.get("kitchen").model_copy(update={"device_ids": ["panel_a"]}))
    store.save(store.get("hall").model_copy(update={"device_ids": ["panel_a"]}))
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="healthy",
            name="Healthy loop",
            device_ids=["panel_a"],
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )
    body = client.get("/decks").get_data(as_text=True)
    assert "dk-warn" not in body


def test_lineups_page_carries_live_refresh_hooks(app: Flask) -> None:
    """The Lineups list self-updates: the swappable region, the SSE feed
    subscription, the fallback poll, and the thumb retry handler all ship."""
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "data-lineups-live" in body
    assert "/events/stream" in body
    assert "EventSource" in body
    # Thumbnails opt into content freshness (see compose_preview ?refresh).
    assert "refresh=300" in body


def test_both_advance_deck_is_not_labelled_by_hand(app: Flask) -> None:
    """A "both" deck advances on a timer AND accepts taps. Its card used to be
    hardcoded to "By hand", which reads as "nothing happens on its own" and
    hides the cadence it is running on."""
    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(
        Deck(
            id="mixed",
            name="Lounge mixed",
            pages=[DeckPage(page_id="hall"), DeckPage(page_id="kitchen")],
            advance="both",
            advance_interval_minutes=20,
        )
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)

    assert "Lounge mixed" in body
    assert "Every 20 min + tap" in body
    assert "auto-advance, button, tap, swipe" in body


def test_manual_deck_still_says_by_hand(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(
        Deck(id="handy", name="Hall wayfinding", pages=[DeckPage(page_id="hall")])
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "By hand" in body


# ---- refreshes per day (#278) ---------------------------------------------


def _local_midnight():
    from datetime import datetime

    from app.tz_resolve import app_timezone

    return datetime.now(app_timezone()).replace(hour=0, minute=0, second=0, microsecond=0)


def test_cycle_fires_walk_the_days_dwell_grid() -> None:
    """A rotation pushes at every dwell window it opens today: from the
    anchor to ``end_at`` (or midnight), one push per step start."""
    from app.deck_routes import _cycle_fires_on

    day = _local_midnight()
    two_by_fifteen = [
        RotationStep(page_id="kitchen", dwell_minutes=15),
        RotationStep(page_id="hall", dwell_minutes=15),
    ]
    all_day = Rotation(id="r", name="r", steps=two_by_fifteen)
    assert len(_cycle_fires_on(all_day, day)) == 96

    office_hours = Rotation(id="r", name="r", steps=two_by_fifteen, anchor="08:00", end_at="20:00")
    fires = _cycle_fires_on(office_hours, day)
    assert len(fires) == 48
    assert fires[0].strftime("%H:%M") == "08:00"
    assert fires[-1].strftime("%H:%M") == "19:45"

    # An end before the anchor stops at midnight, as the engine does.
    late = Rotation(
        id="r",
        name="r",
        steps=[RotationStep(page_id="kitchen", dwell_minutes=30)],
        anchor="22:00",
        end_at="06:00",
    )
    assert len(_cycle_fires_on(late, day)) == 4

    # Disabled or off-day records project nothing.
    off = Rotation(id="r", name="r", steps=two_by_fifteen, enabled=False)
    assert _cycle_fires_on(off, day) == []
    other_days = Rotation(
        id="r", name="r", steps=two_by_fifteen, days_of_week=[(day.weekday() + 1) % 7]
    )
    assert _cycle_fires_on(other_days, day) == []


def test_timed_cards_show_refreshes_today(app: Flask) -> None:
    """Each timed row carries its projected pushes for the day; a by-hand
    deck has nothing to project and shows no count."""
    from app.state.deck_model import Deck, DeckPage

    _seed_all_shapes(app)  # brief: every 30 min; loop: 2 x 15 min, all day
    app.config["DECK_STORE"].upsert(
        Deck(
            id="mixed",
            name="Lounge mixed",
            pages=[DeckPage(page_id="hall"), DeckPage(page_id="kitchen")],
            advance="both",
            advance_interval_minutes=20,
        )
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)

    def count_block(n: int) -> str:
        return f'<span>refreshes today</span><strong class="is-ink">{n}</strong>'

    assert count_block(48) in body  # schedule, 24h / 30 min
    assert count_block(96) in body  # rotation, 24h / 15 min per step
    assert count_block(72) in body  # both-mode deck, 24h / 20 min per page
    # Three timed rows, one count each; the by-hand deck adds none.
    assert body.count("refreshes today</span>") == 3
    # The both-mode note no longer claims there is no timer.
    assert "advances on its timer, or on a press" in body
    assert body.count("waiting on a press") == 1


def test_display_header_sums_refreshes_across_its_rows(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    for pid in ("kitchen", "hall"):
        app.config["PAGE_STORE"].save(Page(id=pid, name=pid.title(), device_ids=["panel"]))
    _seed_all_shapes(app)
    app.config["DECK_STORE"].upsert(
        Deck(
            id="wayfind",
            name="Hall wayfinding",
            device_ids=["panel"],
            pages=[DeckPage(page_id="hall")],
        )
    )
    body = client.get("/decks").get_data(as_text=True)
    assert 'id="display-panel"' in body
    # 48 (schedule) + 96 (rotation); the by-hand deck contributes nothing.
    assert "144 refreshes today" in body
    assert "dk-group-count" in body


def test_disabled_timed_rows_show_no_count(app: Flask) -> None:
    app.config["SCHEDULE_STORE"].upsert(
        Schedule(
            id="brief",
            name="Morning brief",
            page_id="kitchen",
            type="interval",
            interval_minutes=30,
            enabled=False,
        )
    )
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "Morning brief" in body
    assert "refreshes today" not in body


# -- rotation rows: what the panel holds vs what the server intends ----------


def _seed_loop_on(app: Flask, device_id: str) -> None:
    """A two-step rotation bound to ``device_id``, anchored at midnight so
    it is active whenever the test runs."""
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="loop",
            name="Kitchen loop",
            device_ids=[device_id],
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )


def _intended_page(app: Flask) -> str:
    from app.rotation_routes import _current_step_for_each

    with app.test_request_context("/"):
        cur = _current_step_for_each(app.config["ROTATION_STORE"].all())
    page = cur["loop"]["page_id"]
    assert page in ("kitchen", "hall")
    return page


def _other(page: str) -> str:
    return "hall" if page == "kitchen" else "kitchen"


def _stamp_render(app: Flask, device_id: str, **fields) -> None:
    push = app.config["PUSH_MANAGER"]
    push._latest_renders[device_id] = {
        "digest": "new",
        "ext": "png",
        "filename": "new.png",
        "renderer_id": "r",
        "timestamp": 1_700_000_000.0,
        **fields,
    }


def test_rotation_row_marks_intended_step_waiting_until_the_panel_fetches_it(
    app: Flask,
) -> None:
    """A REST display still holding the previous step: the on-panel column
    shows that frame with its fetch time, the intended step carries the
    WAITING ribbon with how far behind the panel is and when it polls next,
    and the strip has no live step."""
    import time as _time

    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    device = app.config["DEVICE_REGISTRY"].devices["panel"]
    assert device.transport == "rest"
    _seed_loop_on(app, "panel")
    intended = _intended_page(app)
    now = _time.time()
    push = app.config["PUSH_MANAGER"]
    # Latest render is the intended page; the panel last fetched the older
    # digest, whose grace copy names the other page.
    _stamp_render(
        app,
        "panel",
        page_id=intended,
        last_served_digest="old",
        last_served_at=now - 1800,
    )
    push._previous_renders["panel"] = {
        "digest": "old",
        "page_id": _other(intended),
        "superseded_at": now - 600,
    }
    app.config["DEVICE_TELEMETRY"].record_heartbeat(
        "panel", received_at=now - 60, parsed={"next_sleep_s": 900}, configured_sleep_s=900
    )

    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert "dk-screen dk-screen--panel is-live" in section
    assert re.search(r'class="dk-screen-at">fetched \d\d:\d\d<', section)
    # The header agrees with the on-panel column (#280): it names the page
    # the panel holds, not the one the server intends, with the fetch time
    # as its title. The badge and border still follow server intent.
    other_name = _other(intended).title()
    assert re.search(
        r'class="dk-group-live" title="fetched \d\d:\d\d">.*?showing ' + other_name,
        section,
        re.S,
    )
    assert f"showing {intended.title()}" not in section
    assert "dk-row is-playing" in section and "Playing · Rotation" in section
    assert "dk-screen is-waiting" in section
    assert 'class="tg tg--sm tg--upper tg--warn dk-ribbon">waiting' in section
    assert re.search(r'class="dk-behind">\d+ min behind · poll ≈ \d\d:\d\d<', section)
    # The intended step is waiting, not live; only the on-panel frame is live.
    assert 'class="dk-screen is-live"' not in section
    assert "dk-progress" in section
    assert "Send now" not in body and "next fire" not in body
    assert "Push now" in body


def test_rotation_row_reads_live_once_the_panel_shows_the_intended_step(
    app: Flask,
) -> None:
    """An MQTT display whose last publish is the intended page: no waiting
    state, the strip's intended step is live, the on-panel caption says
    when it was sent."""
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    device = app.config["DEVICE_REGISTRY"].devices["panel"]
    device.manifest["transport"] = "mqtt"
    assert device.transport == "mqtt"
    _seed_loop_on(app, "panel")
    _stamp_render(app, "panel", page_id=_intended_page(app))

    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert re.search(r'class="dk-screen-at">sent \d\d:\d\d<', section)
    assert "is-waiting" not in section and "dk-behind" not in section
    assert 'class="dk-screen is-live"' in section
    assert re.search(r'role="progressbar"[^>]*aria-valuenow="\d+"', section)


def test_rotation_row_raises_no_alarm_when_the_panel_page_is_unknown(app: Flask) -> None:
    """Nothing served yet: an empty on-panel column, and the intended step
    stays live rather than waiting on evidence the server does not have."""
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    _seed_loop_on(app, "panel")

    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert "dk-screen dk-screen--panel is-empty" in section
    assert "not fetched yet" in section
    assert "is-waiting" not in section
    assert 'class="dk-screen is-live"' in section


def test_cycle_card_view_model_carries_panel_and_dwell_fields(app: Flask) -> None:
    import time as _time

    from app.deck_routes import _design_cards
    from app.rotation_routes import _current_step_for_each

    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    _seed_loop_on(app, "panel")
    intended = _intended_page(app)
    now = _time.time()
    _stamp_render(
        app,
        "panel",
        page_id=intended,
        last_served_digest="old",
        last_served_at=now - 1800,
    )
    app.config["PUSH_MANAGER"]._previous_renders["panel"] = {
        "digest": "old",
        "page_id": _other(intended),
        "superseded_at": now - 600,
    }
    rotations = app.config["ROTATION_STORE"].all()
    pages = app.config["PAGE_STORE"].list_active()
    devices = [d for d in app.config["DEVICE_REGISTRY"].all() if d.kind_of is not None]
    with app.test_request_context("/decks"):
        design = _design_cards(
            nav_decks=[],
            rotations=rotations,
            schedules=[],
            current_step=_current_step_for_each(rotations),
            schedule_status={},
            pages=pages,
            devices=devices,
        )
    card = design["groups"][0]["cards"][0]
    assert card["planned_page_id"] == intended
    assert design["groups"][0]["showing"] == _other(intended).title()
    assert design["groups"][0]["showing_title"].startswith("fetched ")
    assert card["planned_step_index"] == card["step_index"]
    assert card["on_panel"]["page_id"] == _other(intended)
    assert card["on_panel"]["at_label"].startswith("fetched ")
    assert card["waiting"] is True
    assert isinstance(card["behind_minutes"], int) and card["behind_minutes"] >= 0
    assert card["behind_label"].endswith(" behind")
    assert card["next_poll_label"] is None  # no telemetry, no guess
    assert 0 <= card["dwell_pct"] <= 100
    assert re.fullmatch(r"\d\d:\d\d", card["dwell_start_label"])
    assert card["next_in_label"].startswith("in ")
    assert [s["waiting"] for s in card["screens"]].count(True) == 1
    assert not any(s["live"] for s in card["screens"])
    # The flat list resolves the row against its first display the same way.
    flat = next(c for c in design["cards"] if c["kind"] == "cycle")
    assert flat["waiting"] is True and flat["on_panel"]["page_id"] == _other(intended)


def test_mins_label_shapes() -> None:
    from app.deck_routes import _mins_label

    assert _mins_label(0) == "<1 min"
    assert _mins_label(32) == "32 min"
    assert _mins_label(60) == "1 h"
    assert _mins_label(125) == "2 h 5 min"


def _local_hhmm() -> str:
    return r"\d\d:\d\d"


def test_rotation_row_says_the_display_holds_a_manual_push(app: Flask) -> None:
    """An enabled rotation whose display shows a page someone pushed by
    hand: the row loses Playing and gains a pill naming the page, the
    manual push, and when the rotation takes the panel back (#280)."""
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    device = app.config["DEVICE_REGISTRY"].devices["panel"]
    device.manifest["transport"] = "mqtt"
    _seed_loop_on(app, "panel")
    other = _other(_intended_page(app))
    _stamp_render(app, "panel", page_id=other)
    app.config["EVENT_LOG"].record(
        type="push",
        source="page",
        target=other,
        status="sent",
        digest="new",
        extra={"device_ids": ["panel"]},
    )

    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert "dk-row is-playing" not in section
    assert re.search(
        rf'class="tg tg--warn dk-paused"[^>]*>\s*<i[^>]*></i>showing {other.title()} from a manual push'
        rf" · resumes {_local_hhmm()}\s*<",
        section,
    )


def test_rotation_row_names_the_lineup_holding_the_display(app: Flask) -> None:
    from app.state.deck_model import Deck, DeckPage

    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    _seed_loop_on(app, "panel")
    other = _other(_intended_page(app))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="wayfind",
            name="Hall wayfinding",
            device_ids=["panel"],
            pages=[DeckPage(page_id=other)],
        )
    )
    app.config["DECK_NAV_STORE"].set("panel", "wayfind", other)

    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert f"showing {other.title()} from Hall wayfinding" in section
    assert "from a manual push" not in section


def test_rotation_row_waits_for_the_panel_when_its_frame_is_unknown(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    _seed_loop_on(app, "panel")
    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert "waiting for the panel" in section


def test_disabled_rotation_carries_no_paused_pill(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    app.config["ROTATION_STORE"].upsert(
        Rotation(
            id="loop",
            name="Kitchen loop",
            enabled=False,
            device_ids=["panel"],
            steps=[
                RotationStep(page_id="kitchen", dwell_minutes=15),
                RotationStep(page_id="hall", dwell_minutes=15),
            ],
        )
    )
    other = "hall"
    _stamp_render(app, "panel", page_id=other)
    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert "dk-row is-off" in section
    assert "dk-paused" not in section


def test_playing_rotation_carries_no_paused_pill(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _register_display(app, client, "panel")
    device = app.config["DEVICE_REGISTRY"].devices["panel"]
    device.manifest["transport"] = "mqtt"
    _seed_loop_on(app, "panel")
    _stamp_render(app, "panel", page_id=_intended_page(app))
    body = client.get("/decks").get_data(as_text=True)
    section = body[body.index('id="display-panel"') :]
    assert "dk-row is-playing" in section
    assert "dk-paused" not in section


def test_lineups_points_at_the_automation_pause_switch(app: Flask) -> None:
    """The pause switch stays in Settings (it also halts schedules and
    buttons); Lineups shows a band while it is on and a quiet hint to it
    otherwise."""
    client = app.test_client()
    _sign_in(client)
    _seed_all_shapes(app)
    target = "/settings/server#server-automation_paused"

    body = client.get("/decks").get_data(as_text=True)
    assert "data-automation-paused" not in body
    hint = body[body.index("data-automation-hint") - 200 : body.index("data-automation-hint") + 200]
    assert f'href="{target}"' in hint
    assert "Pause everything from Settings › Server › Automation" in body

    app.config["SETTINGS_STORE"].update_section("app", {"automation_paused": True})
    body = client.get("/decks").get_data(as_text=True)
    assert "data-automation-hint" not in body
    band = body[body.index("data-automation-paused") : body.index("data-automation-paused") + 400]
    assert "Automation is paused. Nothing on this page will push until it is resumed." in band
    assert f'href="{target}">Resume in Settings</a>' in band
    # Rows still render beneath the band.
    assert "Kitchen loop" in body
