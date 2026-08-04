"""The design-handoff Decks page: one row per deck with a kind badge and a
kind-specific body (screen cards, 24h rail, steppers), an on-air bar when
something is live, and filter tabs. Noun: deck."""

from __future__ import annotations

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
    assert "Timer cycle" in body and "Timed send" in body and "By hand" in body
    # Kind-specific bodies: cycle chevrons, send 24h rail, nav steppers.
    assert "dk-body--cycle" in body and "dk-rail" in body and "dk-stepper" in body
    # Screen cards carry composer thumbnails.
    assert "/compose/" in body
    # The old listings stay gone.
    assert "Saved schedules" not in body
    assert 'class="card deck-card"' not in body
    # Filter tabs ship.
    assert 'data-kind-filter="all"' in body


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


def test_onair_bar_appears_only_when_something_is_live(app: Flask) -> None:
    """The dark bar renders only while a registered display is live."""
    import json as _json

    from app.state.deck_model import Deck, DeckPage

    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "dk-onair" not in body  # nothing live yet

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
    assert "dk-onair" in body and "On air" in body
    assert "display live" in body
    # The live screen card lights up on the playing row.
    assert "dk-screen is-live" in body
