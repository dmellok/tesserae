"""The display setup wizard (#167, redesigned): a three-step dialog on the
Decks page (behaviour → details/pages → review → created). Timed modes create
through the schedule / rotation endpoints with respond=json; deck mode creates
the deck and hands off to the editor. The wz_* params still preseed the full
timed-send form (the review step's escape hatch)."""

from __future__ import annotations

import json
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
    # Every behaviour card is present.
    for mode in ("daily", "interval", "cycle", "deck"):
        assert f'data-wizard-mode="{mode}"' in body


def test_wizard_ships_the_three_step_views(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    for view in ("behaviour", "details", "review", "created"):
        assert f'data-wizard-view="{view}"' in body
    assert "data-wizard-stepper" in body
    assert "data-wizard-progress" in body
    assert "data-wizard-summary" in body
    assert "data-wizard-results" in body
    assert "data-wizard-duration-rows" in body
    # The dashboard list ships as a JSON island for the searchable picker.
    assert "data-wizard-dashboards" in body
    assert '"id": "kitchen"' in body


def test_wizard_carries_the_create_endpoints(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/decks").get_data(as_text=True)
    assert 'data-create-schedule-url="/schedules/new"' in body
    assert 'data-create-rotation-url="/rotations/new"' in body
    assert 'data-create-deck-url="/decks/new"' in body
    assert "__DECK_ID__/edit" in body


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


def test_garbage_params_degrade_to_the_plain_page(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/decks?wz_type=nope&wz_time=99:99&wz_interval=x&wz_pages=ghost")
    assert resp.status_code == 200


def test_wizard_direct_create_lands_highlighted(app: Flask) -> None:
    """Plain form posts (no respond=json) keep the redirect + highlight."""
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


def test_schedule_create_returns_json_for_the_wizard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/schedules/new",
        data={
            "respond": "json",
            "name": "Kitchen at 7:00 AM",
            "enabled": "on",
            "page_id": "kitchen",
            "type": "daily",
            "fires_at": "07:00",
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["id"] == "kitchen_at_7_00_am"
    assert "hl=kitchen_at_7_00_am" in data["url"]


def test_rotation_create_returns_json_for_the_wizard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/rotations/new",
        data={
            "respond": "json",
            "name": "2-dashboard rotation",
            "enabled": "on",
            "anchor": "00:00",
            "step_page_ids[]": ["kitchen", "hall"],
            "step_dwell_minutes[]": ["5", "5"],
            "step_conditions_json[]": ["", ""],
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "hl=" in data["url"]


def test_deck_create_returns_editor_url_for_the_wizard(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/decks/new",
        data={
            "respond": "json",
            "name": "2-page deck",
            "enabled": "on",
            "graph_json": json.dumps([{"page_id": "kitchen"}, {"page_id": "hall"}]),
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["editor_url"].endswith(f"/decks/{data['id']}/edit")
    deck = app.config["DECK_STORE"].get(data["id"])
    assert [p.page_id for p in deck.pages] == ["kitchen", "hall"]


def test_wizard_json_errors_come_back_as_400(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/schedules/new",
        data={"respond": "json", "name": "", "page_id": "", "type": "daily"},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert data["error"]

    resp = client.post(
        "/decks/new",
        data={"respond": "json", "name": "Broken deck", "graph_json": "not json"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def _add_device(client, device_id: str, name: str) -> None:
    resp = client.post(
        "/settings/devices/add",
        data={
            "id": device_id,
            "kind": "esp32_client",
            "name": name,
            "panel_w": "640",
            "panel_h": "384",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_wizard_ships_display_picker_with_page_bindings(app: Flask) -> None:
    """The multi-pick modes scope to one display: the details step carries a
    display select and the dashboards JSON island carries each page's device
    bindings so the picker can filter."""
    client = app.test_client()
    _sign_in(client)
    _add_device(client, "esp32_a", "Kitchen panel")
    store = app.config["PAGE_STORE"]
    store.save(store.get("kitchen").model_copy(update={"device_ids": ["esp32_a"]}))

    body = client.get("/decks").get_data(as_text=True)
    assert "data-wizard-device" in body
    assert 'data-wizard-block="display"' in body
    assert '<option value="esp32_a">' in body
    assert '"devices": ["esp32_a"]' in body  # kitchen's binding, for the filter
    assert '"devices": []' in body  # hall is unbound


def test_rotation_create_binds_display_and_unassigned_pages(app: Flask) -> None:
    """The wizard posts its chosen display: the rotation stores the binding
    (so the whole cycle plays on that panel) and picked dashboards that
    weren't on any display yet are bound to it, mirroring the deck editor."""
    client = app.test_client()
    _sign_in(client)
    _add_device(client, "esp32_a", "Kitchen panel")
    store = app.config["PAGE_STORE"]
    store.save(store.get("kitchen").model_copy(update={"device_ids": ["esp32_a"]}))

    resp = client.post(
        "/rotations/new",
        data={
            "respond": "json",
            "name": "Kitchen loop",
            "enabled": "on",
            "anchor": "00:00",
            "device_ids": "esp32_a",
            "step_page_ids[]": ["kitchen", "hall"],
            "step_dwell_minutes[]": ["5", "5"],
            "step_conditions_json[]": ["", ""],
        },
    )
    assert resp.status_code == 200
    rotation_id = resp.get_json()["id"]
    rotation = app.config["ROTATION_STORE"].get(rotation_id)
    assert rotation.device_ids == ["esp32_a"]
    deck = app.config["DECK_STORE"].get(rotation_id)
    assert deck.device_ids == ["esp32_a"]
    # hall had no display; creating the bound rotation adopted it.
    assert store.get("hall").device_ids == ["esp32_a"]
    # kitchen's existing binding is untouched.
    assert store.get("kitchen").device_ids == ["esp32_a"]
