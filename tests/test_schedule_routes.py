"""End-to-end /schedules CRUD via the test client."""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask

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


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_empty_list_renders(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    # #167 Phase 3: /schedules redirects to the unified Decks page.
    resp = client.get("/schedules", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Nothing here yet" in resp.data  # unified empty state (#167)


def test_create_persists_and_lists(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/schedules/new",
        data={
            "id": "morning_refresh",
            "name": "Morning refresh",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "15",
            "priority": "0",
            "enabled": "on",
            "days_of_week": ["0", "1", "2", "3", "4"],
        },
        follow_redirects=False,
    )
    body = client.get("/schedules", follow_redirects=True).get_data(as_text=True)
    assert "Morning refresh" in body
    assert "every 15 min" in body


def test_duplicate_name_uniquifies_id(app: Flask, tmp_path: Path) -> None:
    """User never enters an id; submitting the same name twice produces
    'x' and 'x_2', not an error."""

    client = app.test_client()
    _sign_in(client)
    for _ in range(2):
        client.post(
            "/schedules/new",
            data={
                "name": "Morning refresh",
                "page_id": "home",
                "type": "interval",
                "interval_minutes": "15",
            },
        )
    # #167 Phase 2b: schedules live in the deck store; read through the projection.
    store = app.config["SCHEDULE_STORE"]
    ids = sorted(s.id for s in store.all())
    assert ids == ["morning_refresh", "morning_refresh_2"]


def test_toggle_flips_enabled(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/schedules/new",
        data={
            "id": "x",
            "name": "X",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "15",
            "enabled": "on",
        },
    )
    client.post("/schedules/x/toggle")

    # #167 Phase 2b: schedules live in the deck store; read through the projection.
    store = app.config["SCHEDULE_STORE"]
    s = store.get("x")
    assert s is not None and s.enabled is False
    client.post("/schedules/x/toggle")
    assert store.get("x").enabled is True


def test_delete_removes_row(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/schedules/new",
        data={
            "id": "doomed",
            "name": "Doomed",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "15",
        },
    )
    client.post("/schedules/doomed/delete")

    # #167 Phase 2b: schedules live in the deck store; read through the projection.
    store = app.config["SCHEDULE_STORE"]
    assert store.get("doomed") is None


def test_update_preserves_id_even_if_form_attempts_rename(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/schedules/new",
        data={
            "id": "stable",
            "name": "Stable",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "15",
        },
    )
    # Try to rename via the update endpoint, the route force-pins to the
    # URL id, so a typo'd 'id' field doesn't fork into a second record.
    client.post(
        "/schedules/stable/update",
        data={
            "id": "different",
            "name": "Renamed name",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "30",
        },
    )

    # #167 Phase 2b: schedules live in the deck store; read through the projection.
    store = app.config["SCHEDULE_STORE"]
    assert store.get("stable") is not None
    assert store.get("stable").name == "Renamed name"
    assert store.get("different") is None


def test_fire_now_invokes_push(app: Flask) -> None:
    from unittest.mock import MagicMock

    from app.push import PushResult

    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    app.config["PUSH_MANAGER"] = pm

    client = app.test_client()
    _sign_in(client)
    client.post(
        "/schedules/new",
        data={
            "id": "manual",
            "name": "Manual",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "15",
        },
    )
    resp = client.post("/schedules/manual/fire", follow_redirects=True)
    assert resp.status_code == 200
    # "Fire now" is a manual user action, quiet hours don't apply.
    pm.push.assert_called_once_with("home", respect_quiet_hours=False, source="scheduler")


def test_nav_has_single_decks_entry(app: Flask) -> None:
    # #167 Phase 3: Schedules + Rotations nav entries retired; timed content
    # lives on the Decks page and the old URLs redirect there.
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "Decks" in body
    assert 'href="/schedules"' not in body
    assert 'href="/rotations"' not in body


def test_timeline_now_uses_configured_timezone(app: Flask) -> None:
    """#164 / #170: the Next-24h timeline anchored "now" to the server's
    container TZ (UTC on Docker) instead of the configured app timezone, so
    the now-marker and hour ticks read an offset off the user's wall clock.
    An interval schedule guarantees fires in any 24h window regardless of the
    wall-clock hour the test runs at; a daily schedule additionally exercises
    the tz-aware fire-time comparison that would otherwise raise once "now"
    became tz-aware."""
    from datetime import datetime, timedelta

    from app.schedule_routes import _build_timeline
    from app.state.schedule_model import Schedule

    with app.app_context():
        app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
        interval = Schedule(
            id="every30",
            name="Every 30",
            page_id="p1",
            type="interval",
            interval_minutes=30,
        )
        daily = Schedule(
            id="morning",
            name="Morning",
            page_id="p2",
            type="daily",
            fires_at=datetime(2020, 1, 1, 7, 0),
        )
        tl = _build_timeline([interval, daily])

    now = tl["now"]
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=8)  # Hong Kong, no DST
    # The interval schedule always projects fires; all must carry the zone.
    fires = tl["rows"][0]["fires"]
    assert fires, "an interval schedule should project fires within 24h"
    assert all(f["at"].utcoffset() == timedelta(hours=8) for f in fires)
    # The daily projection must not raise on the tz-aware comparison, and any
    # fires it does yield carry the same zone.
    assert all(f["at"].utcoffset() == timedelta(hours=8) for f in tl["rows"][1]["fires"])


def test_last_fired_abs_uses_configured_timezone(app: Flask) -> None:
    """The last-fired tooltip rendered its absolute time in the container TZ;
    it must use the configured app timezone (#170)."""
    from app.schedule_routes import _last_fired_view

    with app.app_context():
        app.config["SETTINGS_STORE"].update_section("app", {"timezone": "Asia/Hong_Kong"})
        # 1609459200 == 2021-01-01 00:00 UTC == 08:00 in Hong Kong (+8).
        view = _last_fired_view(1609459200.0)

    assert view is not None
    assert view["abs"] == "2021-01-01 08:00"
