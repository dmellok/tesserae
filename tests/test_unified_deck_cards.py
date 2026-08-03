"""The one deck list (#167): every shape renders as the same card with a
status block, kind chip, summary, and uniform actions; the old per-shape
listings are gone."""

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
    # One card anatomy everywhere: status block + kind chip + actions.
    assert body.count('class="ucard-status"') == 3
    assert "ucard-kind--cycle" in body and "ucard-kind--send" in body
    # The old per-shape listings are gone.
    assert "Saved schedules" not in body
    assert "rotations-list" not in body
    assert 'class="card deck-card"' not in body
    # Filter chips ship.
    assert 'data-kind-filter="all"' in body


def test_cycle_card_wires_play_fire_toggle_delete(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "/rotations/loop/fire" in body
    assert "/rotations/loop/play/0" in body and "/rotations/loop/play/1" in body
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
    assert "Fires Kitchen every 30 min" in body


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
    assert "is-disabled" in body
    assert ">Enable</span>" in body


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
