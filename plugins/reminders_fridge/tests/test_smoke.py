"""reminders_fridge smoke: fetch shapes the personal-data snapshot and reflects
freshness state. The cell renders read-only from PERSONAL_DATA_STORE, so we seed
the store directly rather than driving the Companion API."""

from __future__ import annotations

import time
from typing import Any

from flask import Flask
from flask.testing import FlaskClient

from plugins.reminders_fridge import server as fridge


def _seed(
    app: Flask, items: list[dict[str, Any]], *, generated_ago: float = 0, ttl_h: float = 47
) -> None:
    now = time.time()
    app.config["PERSONAL_DATA_STORE"].put(
        "reminders.fridge",
        snapshot={"data": {"items": items}},
        generated_epoch=now - generated_ago,
        expires_epoch=now - generated_ago + ttl_h * 3600,
    )


def _fetch(app: Flask, options: dict[str, Any] | None = None) -> dict[str, Any]:
    with app.app_context():
        return fridge.fetch(options or {}, {}, ctx={})


def test_empty_when_no_snapshot(app: Flask) -> None:
    data = _fetch(app)
    assert data["state"] == "empty"
    assert data["empty"] is True
    assert data["count"] == 0


def test_fresh_snapshot_shapes_items(app: Flask) -> None:
    today = time.strftime("%Y-%m-%d")
    _seed(
        app,
        [
            {"title": "Yogurt", "priority": "high", "due_date": today},
            {"title": "Spinach", "priority": "none", "due_date": None},
        ],
    )
    data = _fetch(app)
    assert data["state"] == "fresh"
    assert data["count"] == 2
    assert data["items"][0] == {"title": "Yogurt", "high": True, "due": "Today", "urgent": True}
    assert data["items"][1]["due"] == ""
    assert data["updated_label"]  # a humanized "just now"/"Nm ago"


def test_stale_and_expired_states(app: Flask) -> None:
    # Older than the 24h stale threshold, still within TTL -> stale.
    _seed(
        app,
        [{"title": "Milk", "priority": "none", "due_date": None}],
        generated_ago=30 * 3600,
        ttl_h=47,
    )
    assert _fetch(app)["state"] == "stale"

    # Past expires_at -> expired.
    _seed(
        app,
        [{"title": "Milk", "priority": "none", "due_date": None}],
        generated_ago=50 * 3600,
        ttl_h=47,
    )
    assert _fetch(app)["state"] == "expired"


def test_max_items_caps_shown_but_keeps_count(app: Flask) -> None:
    _seed(app, [{"title": f"Item {i}", "priority": "none", "due_date": None} for i in range(6)])
    data = _fetch(app, {"max_items": 3})
    assert data["count"] == 6
    assert data["shown"] == 3
    assert len(data["items"]) == 3


def test_renders_from_gallery_sample(client: FlaskClient) -> None:
    """client.js runs in-browser against the frozen sample and produces the
    framed cell without erroring."""
    resp = client.get("/_test/render?plugin=reminders_fridge&sample=1&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="reminders_fridge"' in resp.get_data(as_text=True)
