"""M12 polish: timezone field + Scheduler tz, diagnostics buttons, SSE."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.push import PushResult
from app.scheduler import Scheduler
from app.state.event_log import EventLog
from app.state.schedule_model import Schedule
from app.state.schedule_store import ScheduleStore

# --- Timezone-aware scheduler -----------------------------------------


def test_scheduler_resolves_timezone_via_provider(tmp_path: Path) -> None:
    """A 07:00-Australia/Melbourne daily schedule fires when 07:00 in
    Melbourne, regardless of the server's host timezone."""
    store = ScheduleStore(tmp_path / "schedules.json")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    mel_tz = ZoneInfo("Australia/Melbourne")
    sched = Scheduler(
        store=store,
        push_manager=lambda: pm,
        timezone_provider=lambda: mel_tz,
    )
    fires_at = datetime(2026, 6, 1, 7, 0, tzinfo=mel_tz)
    store.upsert(
        Schedule(id="morning", name="Morning", page_id="home", type="daily", fires_at=fires_at)
    )
    # Tick at 06:30 Melbourne — target hasn't passed; first_seen records.
    pre_target_utc = datetime(2026, 6, 1, 6, 30, tzinfo=mel_tz).astimezone(UTC)
    sched.find_due(pre_target_utc)
    # Tick at 07:30 Melbourne — target has passed; should be due.
    post_target_utc = datetime(2026, 6, 1, 7, 30, tzinfo=mel_tz).astimezone(UTC)
    due = sched.find_due(post_target_utc)
    assert [s.id for s in due] == ["morning"]


def test_scheduler_provider_returning_none_uses_host_local(tmp_path: Path) -> None:
    """A None tz provider falls back to host-local — the existing M6
    behaviour. Sanity check: an interval schedule still becomes due."""
    store = ScheduleStore(tmp_path / "schedules.json")
    pm = MagicMock()
    pm.push.return_value = PushResult(status="sent", page_id="home")
    sched = Scheduler(store=store, push_manager=lambda: pm, timezone_provider=lambda: None)
    store.upsert(Schedule(id="i", name="I", page_id="home", type="interval", interval_minutes=15))
    due = sched.find_due(datetime(2026, 6, 1, 12, tzinfo=UTC))
    assert [s.id for s in due] == ["i"]


# --- App factory wires the tz provider to settings.app.timezone ------


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


def test_factory_wires_timezone_from_settings(app: Flask, tmp_path: Path) -> None:
    settings = app.config["SETTINGS_STORE"]
    settings.update_section("app", {"timezone": "Australia/Melbourne"})
    # Resolve via the same callable the scheduler holds.
    scheduler = app.config["SCHEDULER"]
    resolved = scheduler._tz_provider()
    assert resolved is not None
    assert resolved.key == "Australia/Melbourne"


def test_factory_unknown_timezone_falls_back_to_host_local(app: Flask) -> None:
    settings = app.config["SETTINGS_STORE"]
    settings.update_section("app", {"timezone": "Not/A/Real/Zone"})
    scheduler = app.config["SCHEDULER"]
    # Bad value -> provider returns None (host-local fallback).
    assert scheduler._tz_provider() is None


# --- Diagnostics: test broker + test push -----------------------------


def test_diagnostics_test_broker_with_no_host_flashes_error(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/diagnostics/test_broker", follow_redirects=True)
    assert resp.status_code == 200
    assert b"no host configured" in resp.data


def test_diagnostics_test_push_invokes_push_image(app: Flask) -> None:
    pm = MagicMock()
    pm.push_image.return_value = PushResult(
        status="sent",
        page_id="diagnostics_test",
        composition_digest="abc",
        duration_s=0.05,
    )
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/diagnostics/test_push", follow_redirects=True)
    assert resp.status_code == 200
    pm.push_image.assert_called_once()
    _args, kwargs = pm.push_image.call_args
    assert kwargs["source_label"] == "diagnostics_test"
    assert b"Test push ok" in resp.data


def test_diagnostics_test_push_reports_failed_renderers(app: Flask) -> None:
    pm = MagicMock()
    pm.push_image.return_value = PushResult(
        status="failed", page_id="diagnostics_test", error="kaboom"
    )
    app.config["PUSH_MANAGER"] = pm
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/settings/diagnostics/test_push", follow_redirects=True)
    assert b"Test push failed" in resp.data
    assert b"kaboom" in resp.data


# --- SSE event stream -------------------------------------------------


def test_event_log_listener_fires_on_record(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    received: list[str] = []

    def on(row) -> None:
        received.append(row.type)

    log.add_listener(on)
    log.record(type="push", source="page", target="home", status="sent")
    log.record(type="device", source="esp32", target="t/x", status="ok")
    assert received == ["push", "device"]


def test_event_log_remove_listener(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.db")
    received: list[str] = []

    def on(row) -> None:
        received.append(row.type)

    log.add_listener(on)
    log.record(type="push", source="x", target="x", status="sent")
    log.remove_listener(on)
    log.record(type="push", source="x", target="x", status="sent")
    # Only the first event reached the listener.
    assert received == ["push"]


def test_stream_emits_recorded_event(app: Flask) -> None:
    """The SSE endpoint produces a `log` event for each row written
    while the connection is open. We read one chunk synchronously by
    interleaving a record() with the generator's iteration."""
    client = app.test_client()
    _sign_in(client)
    log = app.config["EVENT_LOG"]
    # Build the response, then iterate its generator manually so we can
    # control when record() fires relative to the read.
    resp = client.get("/events/stream", buffered=False)
    gen = resp.iter_encoded()
    # The first chunk is always the :connected comment.
    first = next(gen)
    assert b":connected" in first
    # Record an event from this same request thread — the listener fires
    # synchronously, populating the queue the generator reads from.
    log.record(type="push", source="page", target="home_sse", status="sent")
    # The next yield should be either a :keepalive (if our record raced)
    # or the event. Spin briefly to find the event frame.
    deadline = time.time() + 2.0
    payload = b""
    while time.time() < deadline:
        chunk = next(gen)
        payload += chunk
        if b"event: log" in payload:
            break
    assert b"event: log" in payload
    assert b"home_sse" in payload
    # Done — close the response so the generator's finally: unregisters
    # the listener.
    resp.close()


def test_stream_type_filter_drops_non_matching(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    log = app.config["EVENT_LOG"]
    resp = client.get("/events/stream?type=push", buffered=False)
    gen = resp.iter_encoded()
    next(gen)  # :connected
    log.record(type="device", source="esp32", target="t/x", status="ok")
    log.record(type="push", source="page", target="home", status="sent")
    deadline = time.time() + 2.0
    saw_log = False
    saw_device = False
    while time.time() < deadline:
        chunk = next(gen)
        if b"event: log" in chunk:
            saw_log = True
            # The matched event must be the push one; device was filtered.
            assert b"home" in chunk
            assert b"esp32" not in chunk
            break
        if b"device" in chunk:
            saw_device = True
            break
    assert saw_log
    assert not saw_device
    resp.close()


def test_stream_sets_correct_content_type(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/events/stream", buffered=False)
    assert resp.mimetype == "text/event-stream"
    assert resp.headers.get("Cache-Control") == "no-cache"
    resp.close()
