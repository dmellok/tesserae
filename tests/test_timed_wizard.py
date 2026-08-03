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


def test_cycle_params_preseed_step_rows(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks?wz_pages=kitchen,hall&wz_dwell=20").get_data(as_text=True)
    # Two seeded step rows with the chosen dwell; unknown ids are dropped.
    assert body.count('name="step_page_ids[]"') >= 2
    assert 'value="20"' in body


def test_name_param_preseeds_both_forms(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks?prefill_page=kitchen&wz_type=daily&wz_name=Morning+kitchen").get_data(
        as_text=True
    )
    assert 'value="Morning kitchen"' in body
    body = client.get("/decks?wz_pages=kitchen&wz_name=Lobby+loop").get_data(as_text=True)
    assert 'value="Lobby loop"' in body


def test_wizard_walks_one_question_per_step(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    for step in ("intent", "page", "time", "minutes", "pages", "dwell", "name", "manual"):
        assert f'data-wizard-step="{step}"' in body
    assert "data-wizard-progress" in body


def test_garbage_params_degrade_to_the_plain_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/decks?wz_type=nope&wz_time=99:99&wz_interval=x&wz_pages=ghost")
    assert resp.status_code == 200
