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
    assert "redit=loop" in body  # edit deep-link


def test_send_card_wires_schedule_endpoints_and_next_fire(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert "/schedules/brief/fire" in body
    assert "/schedules/brief/toggle" in body
    assert "sedit=brief" in body
    assert "Fires Kitchen every 30 min" in body


def test_edit_deeplinks_expand_the_matching_form(app: Flask) -> None:
    _seed_all_shapes(app)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks?redit=loop").get_data(as_text=True)
    assert 'id="rotation-edit-card"' in body
    assert "Edit Kitchen loop" in body
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
