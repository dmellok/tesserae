"""Companion API: the per-display upcoming timeline (#232).

The projection's arithmetic is pinned in ``tests/test_device_upcoming.py``.
What these tests cover is the seam: which records the scheduler decides
reach a given display, how the runtime state it holds reaches the
projection, and the response envelope the client decodes.

``current_frame_at`` gets its own attention because its definition is the
one the app's progress bar is measured from: the delivery-side handover for
a REST display, the publish moment otherwise, and null rather than a guess
when the server can't establish either.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

from ._schema import json_schema, schema_for

_CLIENT = {
    "name": "Test iPhone",
    "platform": "ios",
    "app_version": "0.1.0",
    "installation_id": "A1B2C3D4-E5F6-47A8-9012-3456789ABCDE",
}


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
    return a


def _token(app: Flask) -> str:
    code = app.config["COMPANION_PAIRING_STORE"].issue(note="t").code
    resp = app.test_client().post(
        "/api/app/v1/pair",
        data=json.dumps({"code": code, "client": _CLIENT}),
        content_type="application/json",
    )
    return str(resp.get_json()["token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_device(app: Flask, device_id: str = "picpak") -> str:
    code = app.config["PAIRING_STORE"].issue(note="d").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "1.8.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return device_id


def _seed_pages(app: Flask, device_ids: list[str], ids: tuple[str, ...]) -> None:
    from app.state.page_store import Page

    for page_id in ids:
        app.config["PAGE_STORE"].save(
            Page(
                id=page_id,
                name=page_id.title(),
                layout_kind="grid",
                device_ids=list(device_ids),
            )
        )


def _seed_lineup(app: Flask, **overrides: Any) -> Any:
    from app.state.deck_model import Deck, DeckPage

    fields: dict[str, Any] = {
        "id": "playroom",
        "name": "Playroom Deck",
        "pages": [DeckPage(page_id="todo"), DeckPage(page_id="weather")],
        "advance": "timer",
        "advance_trigger": "cycle",
        "advance_interval_minutes": 30,
        "advance_anchor": "00:00",
        "advance_min_hold_minutes": 0,
    }
    fields.update(overrides)
    deck = Deck(**fields)
    app.config["DECK_STORE"].upsert(deck)
    return deck


def _get(app: Flask, token: str, device_id: str, query: str = "") -> Any:
    return app.test_client().get(
        f"/api/app/v1/devices/{device_id}/upcoming{query}", headers=_auth(token)
    )


def _validate(body: Any) -> None:
    import jsonschema

    jsonschema.validate(body, json_schema(schema_for("DeviceUpcomingResponse")))


# -- envelope ------------------------------------------------------------


def test_the_response_matches_the_agreed_shape(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("todo", "weather"))
    _seed_lineup(app, device_ids=[device])
    token = _token(app)
    resp = _get(app, token, device)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    _validate(body)
    assert body["device_id"] == device
    assert body["timezone"] == "UTC"
    generated = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    through = datetime.fromisoformat(body["through_at"].replace("Z", "+00:00"))
    assert through - generated == timedelta(hours=24)
    assert body["events"]
    first = body["events"][0]
    assert first["cause"] == "cycle"
    assert first["lineup_id"] == "playroom"
    assert first["lineup_name"] == "Playroom Deck"
    assert first["dashboard_id"] in ("todo", "weather")
    assert first["id"].startswith(f"{device}:playroom:")


def test_a_display_with_nothing_scheduled_answers_with_an_empty_list(app: Flask) -> None:
    """Empty is a real answer here, not a 404: the display exists and the
    client renders "no scheduled update" from it."""
    device = _seed_device(app)
    token = _token(app)
    body = _get(app, token, device).get_json()
    _validate(body)
    assert body["events"] == []


def test_an_unknown_display_is_a_404(app: Flask) -> None:
    token = _token(app)
    resp = _get(app, token, "nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "not_found"


def test_the_endpoint_needs_a_credential(app: Flask) -> None:
    device = _seed_device(app)
    resp = app.test_client().get(f"/api/app/v1/devices/{device}/upcoming")
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "query", ["?hours=0", "?hours=200", "?limit=0", "?limit=99", "?hours=soon"]
)
def test_out_of_range_query_bounds_are_refused(app: Flask, query: str) -> None:
    device = _seed_device(app)
    token = _token(app)
    resp = _get(app, token, device, query)
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_request"


def test_the_window_and_the_cap_are_honoured(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("todo", "weather"))
    _seed_lineup(app, device_ids=[device], advance_interval_minutes=10)
    token = _token(app)
    body = _get(app, token, device, "?hours=1&limit=3").get_json()
    _validate(body)
    assert len(body["events"]) == 3
    generated = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
    through = datetime.fromisoformat(body["through_at"].replace("Z", "+00:00"))
    assert through - generated == timedelta(hours=1)
    for event in body["events"]:
        at = datetime.fromisoformat(event["scheduled_at"].replace("Z", "+00:00"))
        assert generated <= at <= through


# -- which records reach which display -----------------------------------


def test_a_lineup_bound_to_another_display_is_not_projected_here(app: Flask) -> None:
    kitchen = _seed_device(app, "kitchen")
    study = _seed_device(app, "study")
    _seed_pages(app, [kitchen], ("todo", "weather"))
    _seed_lineup(app, device_ids=[kitchen])
    token = _token(app)
    assert _get(app, token, kitchen).get_json()["events"]
    assert _get(app, token, study).get_json()["events"] == []


def test_an_unbound_lineup_reaches_the_displays_its_dashboards_are_on(app: Flask) -> None:
    """A schedule-shaped record carries no display binding of its own and
    fires wherever its dashboards live, so the display has to be resolved
    through the bindings rather than read off the record."""
    device = _seed_device(app)
    _seed_pages(app, [device], ("todo", "weather"))
    _seed_lineup(app, device_ids=[])
    token = _token(app)
    body = _get(app, token, device).get_json()
    _validate(body)
    assert body["events"]


def test_a_keep_fresh_lineup_repaints_rather_than_changes(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("weather",))
    from app.state.deck_model import DeckPage

    _seed_lineup(
        app,
        id="fresh",
        name="Weather",
        device_ids=[device],
        pages=[DeckPage(page_id="weather")],
        advance_trigger="interval",
        advance_interval_minutes=15,
    )
    token = _token(app)
    body = _get(app, token, device).get_json()
    _validate(body)
    assert body["events"]
    assert body["events"][0]["cause"] == "interval"
    assert body["events"][0]["effect"] == "refresh_screen"
    assert body["events"][0]["dashboard_id"] == "weather"


def test_a_disabled_lineup_projects_nothing(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("todo", "weather"))
    _seed_lineup(app, device_ids=[device], enabled=False)
    token = _token(app)
    assert _get(app, token, device).get_json()["events"] == []


def test_the_scheduler_runtime_state_moves_the_next_update(app: Flask) -> None:
    """The gate lives in the engine, not in the stored record: a fire the
    scheduler has already made is what decides when the next one is due.
    Nothing about the record changes between these two reads."""
    from app.state.deck_model import DeckPage

    device = _seed_device(app)
    _seed_pages(app, [device], ("weather",))
    _seed_lineup(
        app,
        id="fresh",
        name="Weather",
        device_ids=[device],
        pages=[DeckPage(page_id="weather")],
        advance_trigger="interval",
        advance_interval_minutes=60,
    )
    scheduler = app.config["SCHEDULER"]
    now = datetime.now(UTC)
    never_fired = scheduler.upcoming_for_device(device, now=now, hours=6, limit=1)
    assert never_fired[0].scheduled_at <= now

    scheduler._last_fired["fresh"] = now.timestamp()
    after_firing = scheduler.upcoming_for_device(device, now=now, hours=6, limit=1)
    # Second resolution: the projection reports whole seconds, so compare
    # against a truncated now rather than the sub-second one taken above.
    assert after_firing[0].scheduled_at >= now.replace(microsecond=0) + timedelta(minutes=60)


def test_quiet_hours_remove_updates_that_would_not_repaint(app: Flask) -> None:
    device = _seed_device(app)
    _seed_pages(app, [device], ("todo", "weather"))
    _seed_lineup(app, device_ids=[device])
    app.config["SETTINGS_STORE"].update_section(
        "app",
        {
            "quiet_hours_enabled": True,
            "quiet_hours_start": "00:00",
            "quiet_hours_end": "23:59",
        },
    )
    token = _token(app)
    body = _get(app, token, device).get_json()
    _validate(body)
    assert body["events"] == []


# -- current_frame_at ----------------------------------------------------


def test_current_frame_at_is_null_without_a_baseline(app: Flask) -> None:
    device = _seed_device(app)
    token = _token(app)
    body = _get(app, token, device).get_json()
    _validate(body)
    assert body["current_frame_at"] is None


def test_current_frame_at_reports_the_rest_handover(app: Flask) -> None:
    """The delivery-side snapshot the push manager already persists, not a
    claim the panel finished repainting."""
    device = _seed_device(app)
    manager = app.config["PUSH_MANAGER"]
    manager._latest_renders[device] = {"digest": "abc", "timestamp": 1_000.0}
    manager.record_frame_served(device, {"digest": "abc"})
    token = _token(app)
    body = _get(app, token, device).get_json()
    _validate(body)
    assert body["current_frame_at"] is not None
    served = manager.last_served_render_for(device)
    assert served["served_at"] is not None
    at = datetime.fromisoformat(body["current_frame_at"].replace("Z", "+00:00"))
    assert abs(at.timestamp() - served["served_at"]) < 1.5


def test_re_serving_the_same_frame_does_not_move_the_handover(app: Flask) -> None:
    """A poll is not a handover. Restamping on every 304 would answer "when
    did the display last check in", which is a different question and would
    reset the app's progress bar on every wake."""
    device = _seed_device(app)
    manager = app.config["PUSH_MANAGER"]
    manager._latest_renders[device] = {"digest": "abc", "timestamp": 1_000.0}
    manager.record_frame_served(device, {"digest": "abc"})
    first = manager.last_served_render_for(device)["served_at"]
    manager.record_frame_served(device, {"digest": "abc"})
    assert manager.last_served_render_for(device)["served_at"] == first
    manager.record_frame_served(device, {"digest": "def"})
    assert manager.last_served_render_for(device)["served_at"] != first
