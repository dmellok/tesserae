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
    resp = client.get("/schedules")
    assert resp.status_code == 200
    assert b"No schedules yet" in resp.data


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
    body = client.get("/schedules").get_data(as_text=True)
    assert "Morning refresh" in body
    assert "every 15 min" in body


def test_create_rejects_bad_id(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/schedules/new",
        data={
            "id": "Has Spaces",
            "name": "Whatever",
            "page_id": "home",
            "type": "interval",
            "interval_minutes": "15",
        },
        follow_redirects=True,
    )
    body = resp.get_data(as_text=True)
    assert "snake_case" in body or "Invalid" in body


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
    from app.state.schedule_store import ScheduleStore

    store = ScheduleStore(tmp_path / "core" / "schedules.json")
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
    from app.state.schedule_store import ScheduleStore

    store = ScheduleStore(tmp_path / "core" / "schedules.json")
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
    # Try to rename via the update endpoint — the route force-pins to the
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
    from app.state.schedule_store import ScheduleStore

    store = ScheduleStore(tmp_path / "core" / "schedules.json")
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
    pm.push.assert_called_once_with("home")


def test_nav_links_to_schedules(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "Schedules" in body
    assert "/schedules" in body
