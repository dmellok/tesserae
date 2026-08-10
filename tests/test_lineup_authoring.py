"""One write path for creating a Lineup (issue #204).

The setup wizard's four buttons used to post to three routes backed by three
stores, so what a record ended up being depended on which button made it.
They all land on one create now and become a Deck. The scheduler already
runs decks natively, so these tests also pin that a record made this way is
actually picked up rather than sitting inert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.lineup_authoring import build_lineup
from app.main import REPO_ROOT, create_app


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


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _seed_page(app: Flask, page_id: str, device_ids: list[str] | None = None) -> None:
    from app.state.page_store import Page

    app.config["PAGE_STORE"].save(
        Page(id=page_id, name=page_id.title(), layout_kind="grid", device_ids=device_ids or [])
    )


def _create(client: Any, **fields: Any) -> Any:
    from werkzeug.datastructures import MultiDict

    data = MultiDict()
    for key, value in fields.items():
        if isinstance(value, list):
            for item in value:
                data.add(key, str(item))
        else:
            data.add(key, str(value))
    return client.post("/decks/new/lineup", data=data)


# -- the mapping --------------------------------------------------------


def test_daily_maps_to_a_daily_trigger() -> None:
    deck = build_lineup(
        intent="daily", lineup_id="morning", name="Morning", page_ids=["a"], fires_at="07:30"
    )
    assert (deck.advance, deck.advance_trigger, deck.advance_fires_at) == (
        "timer",
        "daily",
        "07:30",
    )


def test_interval_maps_to_an_interval_trigger() -> None:
    deck = build_lineup(
        intent="interval",
        lineup_id="fresh",
        name="Fresh",
        page_ids=["a"],
        interval_minutes=45,
    )
    assert deck.advance_trigger == "interval"
    assert deck.advance_interval_minutes == 45


def test_cycle_carries_per_dashboard_dwell() -> None:
    deck = build_lineup(
        intent="cycle",
        lineup_id="loop",
        name="Loop",
        page_ids=["a", "b"],
        dwell_minutes={"a": 3, "b": 7},
    )
    assert deck.advance_trigger == "cycle"
    assert [p.dwell_minutes for p in deck.pages] == [3, 7]


def test_manual_is_not_on_a_timer() -> None:
    deck = build_lineup(intent="manual", lineup_id="flip", name="Flip", page_ids=["a", "b"])
    assert deck.advance == "manual"


def test_a_record_made_here_carries_no_advanced_fields() -> None:
    """What makes it round-trip through the app's native editor: the four
    intents don't set conditions, fallbacks, windows or priority mode, so a
    Lineup created from the wizard is one the app may also edit."""
    deck = build_lineup(intent="cycle", lineup_id="loop", name="Loop", page_ids=["a", "b"])
    assert not any(p.conditions for p in deck.pages)
    assert deck.advance_fallback_page_id is None
    assert deck.advance_mode == "scheduled"
    assert deck.advance_smart_sync is False
    assert deck.advance_window_start is None and deck.advance_window_end is None


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"intent": "nope", "page_ids": ["a"]}, "unknown intent"),
        ({"intent": "cycle", "page_ids": []}, "at least one dashboard"),
        ({"intent": "daily", "page_ids": ["a", "b"], "fires_at": "07:00"}, "exactly one"),
        ({"intent": "daily", "page_ids": ["a"]}, "time of day"),
    ],
)
def test_bad_input_is_refused(kwargs: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        build_lineup(lineup_id="x", name="X", **kwargs)


# -- the route ----------------------------------------------------------


def test_every_intent_creates_one_deck(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed_page(app, "pantry")
    _seed_page(app, "weather")
    cases = [
        {"intent": "daily", "name": "Daily", "page_ids": ["pantry"], "fires_at": "07:30"},
        {"intent": "interval", "name": "Fresh", "page_ids": ["pantry"]},
        {"intent": "cycle", "name": "Loop", "page_ids": ["pantry", "weather"]},
        {"intent": "manual", "name": "Flip", "page_ids": ["pantry", "weather"]},
    ]
    for case in cases:
        resp = _create(client, **case)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["ok"] is True
    # All four are decks, differing only in how they advance. The legacy
    # stores still SHOW them (they're projections over the deck store since
    # #167), which is the point: one record, several views.
    stored = {d.name: d for d in app.config["DECK_STORE"].all()}
    assert set(stored) == {"Daily", "Fresh", "Loop", "Flip"}
    assert stored["Daily"].advance_trigger == "daily"
    assert stored["Fresh"].advance_trigger == "interval"
    assert stored["Loop"].advance_trigger == "cycle"
    assert stored["Flip"].advance == "manual"


def test_the_scheduler_picks_up_a_record_made_this_way(app: Flask) -> None:
    """A Lineup that isn't scheduled is just a list. The engine adapts decks
    the legacy stores don't represent, so a create here has to land in that
    net."""
    client = app.test_client()
    _sign_in(client)
    _seed_page(app, "pantry")
    _seed_page(app, "weather")
    _create(client, intent="cycle", name="Loop", page_ids=["pantry", "weather"])
    scheduler = app.config["SCHEDULER"]
    with app.app_context():
        assert any(r.name == "Loop" for r in scheduler._cycle_records())

    _create(client, intent="daily", name="Daily", page_ids=["pantry"], fires_at="07:30")
    with app.app_context():
        assert any(r.name == "Daily" for r in scheduler._timed_records())


def test_an_unassigned_dashboard_binds_to_the_display(app: Flask) -> None:
    """Otherwise a freshly-made Lineup has steps that can't render."""
    client = app.test_client()
    _sign_in(client)
    _seed_page(app, "pantry")
    _seed_page(app, "weather", device_ids=["other"])
    _create(
        client,
        intent="cycle",
        name="Loop",
        page_ids=["pantry", "weather"],
        device_ids=["kitchen"],
    )
    pages = app.config["PAGE_STORE"]
    assert pages.get("pantry").device_ids == ["kitchen"]
    # A dashboard already on another display is left alone.
    assert pages.get("weather").device_ids == ["other"]


def test_a_bad_intent_answers_with_json(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed_page(app, "pantry")
    resp = _create(client, intent="whenever", name="X", page_ids=["pantry"])
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
