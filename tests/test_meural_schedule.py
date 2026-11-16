"""Native weekly sleep events are the source of truth when Netgear exposes them."""

from datetime import UTC, datetime

import pytest

from app.meural_auth import CannotConnect
from tests.test_meural_login import app as app
from tests.test_meural_login import held_frame
from tests.test_meural_login import provider as provider

DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
EVENTS = [
    {"time": "2300", "days": DAYS, "command": "sleep"},
    {"time": "0700", "days": DAYS, "command": "wake"},
]


def schedule(app, monkeypatch):
    held_frame(app, app.test_client())
    calls = []
    events = [dict(row) for row in EVENTS]

    def request(self, method, path, **kw):
        calls.append((method, path, kw))
        if method == "PUT" and path.endswith("/scheduler"):
            events[:] = kw["json_data"]["events"]
        if path.endswith("/scheduler"):
            return {"data": {"events": events}}
        return {"data": {"schedulerEnabled": True, "timezone": "America/Phoenix"}}

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.request", request)
    return calls, events


def test_native_schedule_is_cached_with_source_and_timestamp(app, provider, monkeypatch):
    from app.meural_schedule import sync_schedule

    calls, _ = schedule(app, monkeypatch)
    device = app.config["DEVICE_REGISTRY"].get("test_frame")
    first = sync_schedule(app, device)
    assert first["source"] == "frame"
    assert first["available"] and first["synced_at"] > 0
    assert first["start"] == "23:00" and first["end"] == "07:00"
    assert sync_schedule(app, device) == first
    assert len(calls) == 2
    sync_schedule(app, device, force=True)
    assert len(calls) == 4


@pytest.mark.parametrize("hour,quiet", [(5, False), (6, True), (13, True), (14, False)])
def test_native_window_uses_the_frame_timezone(app, provider, monkeypatch, hour, quiet):
    from app.meural_schedule import in_sleep_window, sync_schedule

    schedule(app, monkeypatch)
    state = sync_schedule(app, app.config["DEVICE_REGISTRY"].get("test_frame"))
    assert in_sleep_window(state, datetime(2026, 11, 17, hour, tzinfo=UTC)) is quiet


def test_schedule_failure_explicitly_falls_back_to_tesserae(app, provider, monkeypatch):
    from app.meural_schedule import sync_schedule

    schedule(app, monkeypatch)
    device = app.config["DEVICE_REGISTRY"].get("test_frame")
    sync_schedule(app, device)

    def offline(*a, **kw):
        raise CannotConnect("offline")

    monkeypatch.setattr("app.meural_cloud.MeuralCloudClient.request", offline)
    state = sync_schedule(app, device, force=True)
    assert state["source"] == "tesserae" and not state["available"]
    assert state["synced_at"] > 0
    assert "Tesserae quiet hours" in state["message"]


def test_schedule_refresh_and_edit_preserve_artwork_events(app, provider, monkeypatch):
    calls, events = schedule(app, monkeypatch)
    events.append({"time": "0900", "days": DAYS, "command": "gallery", "galleryId": 27})
    client = app.test_client()
    assert (
        client.post("/settings/devices/test_frame/meural/sleep/refresh").json["sleep"]["source"]
        == "frame"
    )
    response = client.post(
        "/settings/devices/test_frame/meural/sleep",
        json={"start": "22:00", "end": "06:00", "days": DAYS, "enabled": True},
    )
    assert response.status_code == 200
    assert response.json["sleep"]["start"] == "22:00"
    written = next(
        kw["json_data"]["events"]
        for method, path, kw in calls
        if method == "PUT" and path.endswith("/scheduler")
    )
    assert {"time": "0900", "days": DAYS, "command": "gallery", "galleryId": 27} in written
    assert len(written) == 3
    assert calls[-1][0] == "GET"


def test_unknown_schedule_fields_are_not_guessed(app, provider, monkeypatch):
    from app.meural_schedule import sync_schedule

    schedule(app, monkeypatch)
    monkeypatch.setattr(
        "app.meural_cloud.MeuralCloudClient.request",
        lambda *a, **kw: {"data": {"alarmTime": "2300", "alarmEndTime": "0700"}},
    )
    state = sync_schedule(app, app.config["DEVICE_REGISTRY"].get("test_frame"), force=True)
    assert state["source"] == "tesserae"
    assert not state["available"]


def test_native_sleep_window_stops_cloud_delivery(app, provider, monkeypatch):
    from PIL import Image

    from app.meural import MeuralPublisher
    from app.meural_schedule import sync_schedule

    calls, _ = schedule(app, monkeypatch)
    device = app.config["DEVICE_REGISTRY"].get("test_frame")
    sync_schedule(app, device)
    monkeypatch.setattr("app.meural_schedule.in_sleep_window", lambda state, now=None: True)
    Image.new("RGB", (108, 192), "red").save(app.config["RENDERS_DIR"] / "example.png")
    publisher = MeuralPublisher(
        app=app,
        devices=app.config["DEVICE_REGISTRY"],
        settings=app.config["SETTINGS_STORE"],
        renders_dir=app.config["RENDERS_DIR"],
        latest_render_fn=lambda _: {"composition_digest": "example"},
        run_async=False,
    )
    monkeypatch.setattr("app.meural.MeuralClient.is_sleeping", lambda self: False)
    publisher.poll_once()
    assert not any(method == "POST" and path == "items" for method, path, _ in calls)


def test_takeover_keeps_native_sleep_scheduler_enabled(app, provider, monkeypatch):
    calls, _ = schedule(app, monkeypatch)
    from app.meural_cloud import get_cloud_client

    get_cloud_client(app, "test_frame").pause_rotation("101")
    assert calls[-1][2]["json_data"] == {"galleryRotation": False}


def test_native_schedule_overrides_tesserae_in_automation_gate(app, provider, monkeypatch):
    from app.meural_schedule import sync_schedule
    from app.quiet_hours import device_is_quiet

    schedule(app, monkeypatch)
    device = app.config["DEVICE_REGISTRY"].get("test_frame")
    sync_schedule(app, device)
    app_hours = {
        "quiet_hours_enabled": True,
        "quiet_hours_start": "12:00",
        "quiet_hours_end": "13:00",
    }
    # 23:00 Phoenix: native sleep begins despite Tesserae's different hours.
    assert device_is_quiet(app_hours, device, datetime(2026, 11, 17, 6, tzinfo=UTC), UTC)
    # Noon Phoenix: native frame is awake, so Tesserae's old window cannot block it.
    assert not device_is_quiet(app_hours, device, datetime(2026, 11, 17, 19, tzinfo=UTC), UTC)


def test_sleep_page_shows_native_hours_and_labels_fallback(app, provider, monkeypatch):
    from app.meural_schedule import sync_schedule

    schedule(app, monkeypatch)
    device = app.config["DEVICE_REGISTRY"].get("test_frame")
    sync_schedule(app, device)
    client = app.test_client()
    page = client.get("/settings/devices/test_frame").text
    assert "Frame sleeps 23:00–07:00 (from the frame)." in page
    assert "Last synced:" in page
    assert 'action="/settings/devices/test_frame/meural/sleep/refresh"' in page
    assert "Tesserae quiet hours (fallback)" not in page
    app.config["SETTINGS_STORE"].update_item("devices", "test_frame", {"meural_auth_failed": True})
    page = client.get("/settings/devices/test_frame").text
    assert "Tesserae quiet hours (fallback)" in page
    assert "Change the frame schedule in the Meural app" in page


def test_sleep_refresh_form_returns_to_the_device(app, provider, monkeypatch):
    schedule(app, monkeypatch)
    response = app.test_client().post(
        "/settings/devices/test_frame/meural/sleep/refresh", data={"return_to_device": "1"}
    )
    assert response.status_code == 302
    assert response.location == "/settings/devices/test_frame#frame-sleep"
