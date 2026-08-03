"""The "Help me choose" wizard (#167): the dialog ships on the Decks page and
the wz_* params preseed the existing new-record forms server-side. The wizard
owns no submission paths, so these tests cover the prefill rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Page


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


def test_wizard_dialog_ships_on_the_decks_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert 'id="timed-wizard"' in body
    assert "data-open-timed-wizard" in body
    assert "timed_wizard.js" in body
    # Every intent path is present.
    for value in ("daily", "interval", "cycle", "manual"):
        assert f'value="{value}"' in body


def test_daily_params_preseed_the_timed_send_form(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks?prefill_page=kitchen&wz_type=daily&wz_time=06:30").get_data(
        as_text=True
    )
    assert 'value="06:30"' in body
    # The daily cadence is selected on the new form and the card is open.
    assert '<option value="daily" selected' in body


def test_interval_params_preseed_the_cadence(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks?prefill_page=hall&wz_type=interval&wz_interval=45").get_data(
        as_text=True
    )
    assert 'value="45"' in body


def test_name_param_preseeds_both_forms(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks?prefill_page=kitchen&wz_type=daily&wz_name=Morning+kitchen").get_data(
        as_text=True
    )
    assert 'value="Morning kitchen"' in body


def test_wizard_walks_one_question_per_step(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    steps = ("intent", "page", "time", "minutes", "pages", "dwell", "name", "review", "manual")
    for step in steps:
        assert f'data-wizard-step="{step}"' in body
    assert "data-wizard-progress" in body
    assert "data-wizard-dwell-rows" in body  # one input per picked dashboard
    assert "data-wizard-review" in body


def test_garbage_params_degrade_to_the_plain_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/decks?wz_type=nope&wz_time=99:99&wz_interval=x&wz_pages=ghost")
    assert resp.status_code == 200


def test_wizard_direct_create_lands_highlighted(app: Flask) -> None:
    """The review step's Create submits the existing endpoints; the redirect
    lands on the unified list with the new card highlighted."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/rotations/new",
        data={
            "name": "Lobby loop",
            "enabled": "on",
            "anchor": "00:00",
            "step_page_ids[]": ["kitchen", "hall"],
            "step_dwell_minutes[]": ["10", "25"],
            "step_conditions_json[]": ["", ""],
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "hl=lobby_loop" in resp.location and "#udeck-lobby_loop" in resp.location
    body = client.get(resp.location).get_data(as_text=True)
    assert "is-new" in body and "Lobby loop" in body

    resp = client.post(
        "/schedules/new",
        data={
            "name": "Evening brief",
            "enabled": "on",
            "page_id": "kitchen",
            "type": "daily",
            "fires_at": "19:30",
        },
        follow_redirects=False,
    )
    assert "hl=evening_brief" in resp.location
    body = client.get(resp.location).get_data(as_text=True)
    assert "is-new" in body and "Evening brief" in body
