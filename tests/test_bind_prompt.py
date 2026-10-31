"""A dashboard bound only to another display can never show on a lineup's
display. Every write path that could put it there asks for consent first
(`bind_pages`, `bind_also` for "keep its other display") and refuses without
it; nothing is bound silently. Existing bad data still loads and can be fixed
with one call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page


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
    store.patch_section("experiments", {"mcp": True})
    c = a.test_client()
    for dev in ("hallway", "frame"):
        code = a.config["PAIRING_STORE"].issue(note="t").code
        resp = c.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps({"device_id": dev, "kind": "esp32_client"}),
        )
        assert resp.status_code == 201
    pages = a.config["PAGE_STORE"]
    pages.save(Page(id="photos", name="Google Photos", device_ids=["frame"]))
    pages.save(Page(id="clock", name="Clock", device_ids=["hallway"]))
    pages.save(Page(id="loose", name="Loose"))
    return a


def _bound(app: Flask, page_id: str) -> list[str]:
    return list(app.config["PAGE_STORE"].get(page_id).device_ids)


def _lineup_form(**extra: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "intent": "interval",
        "name": "Photos every 60 min",
        "page_ids": ["photos"],
        "device_ids": ["hallway"],
        "interval_minutes": "60",
    }
    form.update(extra)
    return form


# -- the wizard / lineup create ----------------------------------------------------


def test_create_lineup_refuses_a_dashboard_bound_elsewhere(app: Flask) -> None:
    resp = app.test_client().post("/decks/new/lineup", data=_lineup_form())
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["code"] == "pages_not_bound" and body["ok"] is False
    assert [p["id"] for p in body["unbound_pages"]] == ["photos"]
    assert body["unbound_pages"][0]["bound_to"][0]["id"] == "frame"
    assert body["display"]["id"] == "hallway"
    assert "bind_pages" in body["error"] and "Google Photos" in body["error"]
    assert app.config["DECK_STORE"].all() == []
    assert _bound(app, "photos") == ["frame"]


def test_create_lineup_with_consent_moves_the_binding(app: Flask) -> None:
    resp = app.test_client().post("/decks/new/lineup", data=_lineup_form(bind_pages=["photos"]))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert len(app.config["DECK_STORE"].all()) == 1
    assert _bound(app, "photos") == ["hallway"]


def test_create_lineup_bind_also_keeps_the_other_display(app: Flask) -> None:
    resp = app.test_client().post("/decks/new/lineup", data=_lineup_form(bind_also=["photos"]))
    assert resp.status_code == 200
    assert _bound(app, "photos") == ["frame", "hallway"]


def test_create_lineup_leaves_unassigned_and_matching_dashboards_alone(app: Flask) -> None:
    for pid in ("clock", "loose"):
        resp = app.test_client().post("/decks/new/lineup", data=_lineup_form(page_ids=[pid]))
        assert resp.status_code == 200, pid


# -- the editor's save --------------------------------------------------------------


def _editor_form(**extra: Any) -> dict[str, Any]:
    form: dict[str, Any] = {
        "name": "Photos",
        "pages": "photos,clock",
        "device_ids": "hallway",
        "advance": "timer",
    }
    form.update(extra)
    return form


def test_editor_save_refuses_without_consent(app: Flask) -> None:
    resp = app.test_client().post("/decks/editor-save", data=_editor_form(), follow_redirects=True)
    assert app.config["DECK_STORE"].all() == []
    assert b"not bound to" in resp.data
    assert _bound(app, "photos") == ["frame"]


def test_editor_save_binds_consented_pages_with_the_save(app: Flask) -> None:
    app.test_client().post(
        "/decks/editor-save", data=_editor_form(bind_pages="photos"), follow_redirects=True
    )
    assert [d.page_ids for d in app.config["DECK_STORE"].all()] == [["photos", "clock"]]
    assert _bound(app, "photos") == ["hallway"]


def test_editor_save_bind_both(app: Flask) -> None:
    app.test_client().post(
        "/decks/editor-save",
        data=_editor_form(bind_pages="photos", bind_also="photos"),
        follow_redirects=True,
    )
    assert _bound(app, "photos") == ["frame", "hallway"]


def test_editor_display_switch_is_enforced(app: Flask) -> None:
    # A lineup on `hallway` holding only hallway dashboards is switched to `frame`:
    # `clock` would be stranded there, so the save needs consent for it.
    app.config["DECK_STORE"].upsert(
        Deck(id="mine", name="Mine", device_ids=["hallway"], pages=[DeckPage(page_id="clock")])
    )
    c = app.test_client()
    form = _editor_form(deck_id="mine", pages="clock", device_ids="frame")
    c.post("/decks/editor-save", data=form, follow_redirects=True)
    assert app.config["DECK_STORE"].get("mine").device_ids == ["hallway"]
    c.post("/decks/editor-save", data={**form, "bind_pages": "clock"}, follow_redirects=True)
    assert app.config["DECK_STORE"].get("mine").device_ids == ["frame"]
    assert _bound(app, "clock") == ["frame"]


def test_editor_page_flags_off_display_members(app: Flask) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(id="bad", name="Bad", device_ids=["hallway"], pages=[DeckPage(page_id="photos")])
    )
    html = app.test_client().get("/decks/bad/edit").get_data(as_text=True)
    state = json.loads(html.split('id="dxe-data">', 1)[1].split("</script>", 1)[0])
    assert {d["id"] for d in state["devices"]} >= {"hallway", "frame"}
    assert next(p for p in state["pages"] if p["id"] == "photos")["devices"] == ["frame"]


# -- the other write routes ----------------------------------------------------------


def test_schedule_create_is_guarded(app: Flask) -> None:
    c = app.test_client()
    form = {
        "name": "Photos",
        "page_id": "photos",
        "type": "interval",
        "interval_minutes": "60",
        "enabled": "on",
        "device_ids": "hallway",
    }
    resp = c.post("/schedules/new", data={**form, "respond": "json"})
    assert resp.status_code == 409 and resp.get_json()["code"] == "pages_not_bound"
    assert app.config["SCHEDULE_STORE"].all() == []
    c.post("/schedules/new", data=form, follow_redirects=True)
    assert app.config["SCHEDULE_STORE"].all() == []  # the classic form flashes, saves nothing
    ok = c.post("/schedules/new", data={**form, "bind_pages": "photos"})
    assert ok.status_code in (200, 302)
    assert len(app.config["SCHEDULE_STORE"].all()) == 1
    assert _bound(app, "photos") == ["hallway"]


def test_rotation_create_is_guarded(app: Flask) -> None:
    c = app.test_client()
    resp = c.post(
        "/rotations/new",
        data={
            "name": "Loop",
            "respond": "json",
            "device_ids": "hallway",
            "step_page_ids[]": ["photos", "clock"],
            "step_dwell_minutes[]": ["5", "5"],
            "enabled": "on",
        },
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert [p["id"] for p in resp.get_json()["unbound_pages"]] == ["photos"]
    assert app.config["ROTATION_STORE"].all() == []


def test_bind_check_endpoint(app: Flask) -> None:
    out = (
        app.test_client()
        .post(
            "/decks/bind-check",
            json={"page_ids": ["photos", "clock", "loose"], "device_ids": ["hallway"]},
        )
        .get_json()
    )
    assert [p["id"] for p in out["unbound_pages"]] == ["photos"]
    assert out["display"]["id"] == "hallway"


# -- MCP ------------------------------------------------------------------------------


def test_mcp_create_deck_schedule_rotation_refuse_with_the_fix(app: Flask) -> None:
    c = app.test_client()
    deck = {"id": "d", "name": "D", "device_ids": ["hallway"], "pages": [{"page_id": "photos"}]}
    sched = {
        "id": "s", "name": "S", "page_id": "photos", "type": "interval",
        "interval_minutes": 60, "device_ids": ["hallway"],
    }  # fmt: skip
    rot = {
        "id": "r", "name": "R", "device_ids": ["hallway"],
        "steps": [{"page_id": "photos", "dwell_minutes": 5}],
    }  # fmt: skip
    for path, body in (("decks", deck), ("schedules", sched), ("rotations", rot)):
        resp = c.post(f"/api/mcp/{path}", json=body)
        assert resp.status_code == 409, path
        out = resp.get_json()
        assert out["code"] == "pages_not_bound" and out["fix"] == {"bind_pages": ["photos"]}
        assert "bind_pages" in out["error"]
    assert _bound(app, "photos") == ["frame"]
    ok = c.post("/api/mcp/schedules", json={**sched, "bind_pages": ["photos"]})
    assert ok.status_code == 200
    assert _bound(app, "photos") == ["hallway"]
    assert c.post("/api/mcp/decks", json={**deck, "bind_also": ["photos"]}).status_code == 200
    assert _bound(app, "photos") == ["hallway"]  # already moved; `also` has nothing left to do


def test_mcp_replace_of_a_broken_record_is_not_blocked(app: Flask) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(id="old", name="Old", device_ids=["hallway"], pages=[DeckPage(page_id="photos")])
    )
    body = {
        "id": "old",
        "name": "Renamed",
        "device_ids": ["hallway"],
        "pages": [{"page_id": "photos"}],
    }
    assert app.test_client().post("/api/mcp/decks", json=body).status_code == 200


# -- existing bad data ----------------------------------------------------------------


def test_one_click_bind_fixes_an_existing_lineup(app: Flask) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(
            id="bad", name="Bad", device_ids=["hallway"],
            pages=[DeckPage(page_id="photos"), DeckPage(page_id="clock")],
        )
    )  # fmt: skip
    c = app.test_client()
    vm = c.get("/decks/view.json").get_json()
    hallway = next(d for d in vm["displays"] if d["id"] == "hallway")
    steps = hallway["lineups"][0]["steps"]
    assert [s["off_display"] for s in steps] == [True, False]
    assert steps[0]["thumb_url"] == "" and "Bound to" in steps[0]["off_display_note"]
    bind_url = hallway["lineups"][0]["actions"]["bind_url"]
    assert bind_url.endswith("/decks/bad/bind-pages")
    assert c.post(bind_url, json={"page_ids": ["clock", "nope"]}).status_code == 400
    ok = c.post(bind_url, json={"page_ids": ["photos"], "display_id": "hallway", "also": True})
    assert ok.get_json()["ok"] is True
    assert _bound(app, "photos") == ["frame", "hallway"]
    again = c.get("/decks/view.json").get_json()
    hallway = next(d for d in again["displays"] if d["id"] == "hallway")
    assert [s["off_display"] for s in hallway["lineups"][0]["steps"]] == [False, False]


def test_lineups_page_flags_the_step_and_offers_the_button(app: Flask) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(id="bad", name="Bad", device_ids=["hallway"], pages=[DeckPage(page_id="photos")])
    )
    html = app.test_client().get("/decks").get_data(as_text=True)
    assert 'class="lu-step is-offdisplay"' in html.replace(" is-now", "").replace(" is-next", "")
    assert "Not on this display" in html and 'data-bind="bad:0"' in html
    assert "lu-push-form" not in html.split('id="lu-row-bad"', 1)[1].split("</li>", 1)[0]


def test_unbound_unassigned_and_matching_lineups_are_not_flagged(app: Flask) -> None:
    app.config["DECK_STORE"].upsert(
        Deck(
            id="fine", name="Fine", device_ids=["hallway"],
            pages=[DeckPage(page_id="clock"), DeckPage(page_id="loose")],
        )
    )  # fmt: skip
    vm = app.test_client().get("/decks/view.json").get_json()
    hallway = next(d for d in vm["displays"] if d["id"] == "hallway")
    assert not any(s["off_display"] for s in hallway["lineups"][0]["steps"])
