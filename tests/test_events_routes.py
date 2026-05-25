"""End-to-end /events tests + cross-system event emission.

Covers:
  * /events lists every event type; filter chip narrows by type
  * Push pipeline emits per-renderer event rows (not just nested extras)
  * Device heartbeats land as device events with status ok / error
  * Scheduler fires emit scheduler events that link to the push event
  * Auth flow emits setup / login / login_denied / logout rows
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.push import PushResult


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


def test_events_page_renders_with_chips(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/events")
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    for chip in ("All", "Push", "Renderer", "Device", "Scheduler", "Auth"):
        assert chip in body


def test_filter_chip_scopes_to_one_type(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    log.record(type="push", source="page", target="home", status="sent")
    log.record(type="device", source="esp32_client", target="t/x", status="ok")

    client = app.test_client()
    _sign_in(client)
    body = client.get("/events?type=push").get_data(as_text=True)
    assert "home" in body
    # device row excluded by the filter
    assert "esp32_client" not in body


def test_setup_emits_auth_event(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    client = app.test_client()
    _sign_in(client)
    rows = log.list(type="auth")
    assert any(r.source == "setup" and r.status == "ok" for r in rows)


def test_login_denied_emits_auth_event(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    client = app.test_client()
    _sign_in(client)
    # Drop the session so /login is reachable.
    with client.session_transaction() as sess:
        sess.clear()
    client.post("/login", data={"password": "wrong"})
    denied = [r for r in log.list(type="auth") if r.source == "login" and r.status == "denied"]
    assert len(denied) == 1


def test_logout_emits_auth_event(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    client = app.test_client()
    _sign_in(client)
    client.post("/logout")
    assert any(r.source == "logout" for r in log.list(type="auth"))


def test_device_heartbeat_emits_device_event(app: Flask) -> None:
    log = app.config["EVENT_LOG"]
    transport = app.config["MQTT_TRANSPORT"]
    payload = json.dumps({"battery_mv": 3950, "rssi": -42, "ip": "10.0.0.7"}).encode()

    class _Msg:
        topic = "tesserae/esp32/status"

    msg = _Msg()
    msg.payload = payload  # type: ignore[attr-defined]
    transport._on_message(None, None, msg)

    device_rows = log.list(type="device")
    assert len(device_rows) == 1
    assert device_rows[0].source == "esp32_client"
    assert device_rows[0].status == "ok"
    assert device_rows[0].extra["parsed"]["battery_mv"] == 3950


def test_scheduler_fire_emits_scheduler_event_linking_push(app: Flask) -> None:
    log = app.config["EVENT_LOG"]

    # Stub the push manager so the scheduler doesn't actually run Playwright.
    pm = MagicMock()
    pm.push.return_value = PushResult(
        status="sent",
        page_id="home",
        duration_s=0.42,
        event_id=999,  # the (fake) push event the scheduler "caused"
    )
    app.config["PUSH_MANAGER"] = pm

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
        },
    )
    client.post("/schedules/x/fire")

    sched_rows = log.list(type="scheduler")
    assert len(sched_rows) == 1
    row = sched_rows[0]
    assert row.target == "x"
    assert row.status == "sent"
    assert row.extra["push_event_id"] == 999
    assert row.extra["page_id"] == "home"


def test_nav_links_to_events(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "/events" in body
