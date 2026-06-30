"""End-to-end /devices/battery admin page tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.battery_history import BatteryHistory


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


def test_index_renders_empty_state_with_no_history(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/devices/battery")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "No battery history yet" in body
    # Window chips still render so the user can switch windows even
    # before any data lands.
    assert "7d" in body and "30d" in body


def test_index_renders_card_with_history_and_prediction(app: Flask, tmp_path: Path) -> None:
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    client = app.test_client()
    _sign_in(client)
    # v0.55.2 filter: only cards for currently-registered devices
    # render, so the instance has to exist in the registry before
    # planting history (deleted devices intentionally don't render).
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_attic", "kind": "esp32_client", "name": "Attic ESP"},
    )
    now = time.time()
    for i in range(10):
        store.record("esp32_attic", pct=100 - i * 5, timestamp=now - (10 - i) * 86400 / 2)
    resp = client.get("/devices/battery?window=7")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "esp32_attic" in body
    assert "battery-chart" in body
    # Prediction metadata should be on the page somewhere; the heading
    # is "Drain rate" when a regression succeeded.
    assert "Drain rate" in body or "Need at least 8 samples" in body


def test_index_hides_orphan_history_for_unregistered_devices(app: Flask, tmp_path: Path) -> None:
    """Historical battery rows for a device that's no longer in the
    registry must not render. The previous logic was
    ``current.keys() | store.device_ids()`` which kept showing pre-
    v0.55.1 deleted devices because SQLite rows survived the
    registry drop. v0.55.2 intersects with the live registry so
    orphan history is excluded at read time."""
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    now = time.time()
    # Plant history for a device id that's NOT registered. With the
    # old union logic the dashboard would render a card for it; with
    # the registry intersect it must not.
    for i in range(6):
        store.record("ghost_device", pct=80 - i * 2, timestamp=now - (6 - i) * 86400)
    client = app.test_client()
    _sign_in(client)
    body = client.get("/devices/battery?window=7").get_data(as_text=True)
    assert "ghost_device" not in body


def test_series_json_returns_points_and_prediction(app: Flask) -> None:
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    now = time.time()
    for i in range(12):
        store.record("photopainter", pct=90 - i * 4, timestamp=now - (12 - i) * 3600 * 12)
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/devices/battery/photopainter/series.json?window=7")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["device_id"] == "photopainter"
    assert payload["window_days"] == 7
    assert len(payload["series"]) == 12
    # 12 samples * 4% drop = strong negative slope, prediction populated.
    assert payload["prediction"] is not None
    assert payload["prediction"]["slope_per_day"] < 0


def test_window_query_param_clamps(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/devices/battery?window=999")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Clamped to 90 (WINDOW_DAYS_MAX); chip for 90 is marked active.
    assert "90d" in body


def test_topbar_battery_indicator_links_to_admin_page(app: Flask) -> None:
    """When a device's heartbeat carries a battery_pct, the topbar
    indicator should appear on every page with a link to
    /devices/battery so the user can click through to the charts
    instead of having to know the URL."""
    # The _collect_battery_status helper reads from DEVICE_REGISTRY +
    # DEVICE_STATUS; build a minimal fake of both that the template will
    # pick up. The registry needs a `devices.values()` iterable of
    # objects with `id`, `display_name`, and `kind_of`.

    class _FakeDevice:
        def __init__(self, id_: str, name: str, manifest: dict | None = None) -> None:
            self.id = id_
            self.display_name = name
            self.kind_of = "esp32_client"
            self.manifest = manifest or {}

    class _FakeRegistry:
        def __init__(self) -> None:
            self.devices = {"d1": _FakeDevice("d1", "Dining")}

    app.config["DEVICE_REGISTRY"] = _FakeRegistry()
    app.config["DEVICE_STATUS"] = {
        "d1": {"received_at": time.time(), "parsed": {"battery_pct": 42}},
    }

    client = app.test_client()
    _sign_in(client)
    # /events renders the base layout; the topbar indicator goes there.
    resp = client.get("/events")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Single-device case: the indicator itself is a link straight to
    # the admin page.
    assert 'href="/devices/battery"' in body, (
        "topbar battery indicator should link to /devices/battery so the "
        "user has a discoverable entry point"
    )


def test_topbar_battery_indicator_applies_per_device_offset(app: Flask) -> None:
    """The topbar battery number must show the offset-adjusted value,
    matching what the /devices/battery dashboard and the HA discovery
    sensors show. Regression guard: shipping the offset to the
    dashboard alone left the topbar still reading the raw firmware
    value, which looked like a half-finished feature."""
    from app.app_factory import _collect_battery_status

    class _FakeDevice:
        def __init__(self, id_: str, name: str, manifest: dict) -> None:
            self.id = id_
            self.display_name = name
            self.kind_of = "esp32_client"
            self.manifest = manifest

    class _FakeRegistry:
        def __init__(self, devices: dict) -> None:
            self.devices = devices

    # Two devices: one with no offset (raw 4% surfaces as 4%), one
    # with +30% offset (raw 4% surfaces as 34%, well above the
    # "critical" threshold).
    app.config["DEVICE_REGISTRY"] = _FakeRegistry(
        {
            "d_raw": _FakeDevice("d_raw", "Raw", {}),
            "d_off": _FakeDevice("d_off", "Offset", {"battery_offset": {"mv": 0, "pct": 30}}),
        }
    )
    app.config["DEVICE_STATUS"] = {
        "d_raw": {"received_at": time.time(), "parsed": {"battery_pct": 4}},
        "d_off": {"received_at": time.time(), "parsed": {"battery_pct": 4}},
    }

    with app.app_context():
        entries = _collect_battery_status(app)
    by_id = {e["id"]: e for e in entries}
    assert by_id["d_raw"]["pct"] == 4
    assert by_id["d_raw"]["tone"] == "critical"
    assert by_id["d_off"]["pct"] == 34
    assert by_id["d_off"]["tone"] == "ok"


def test_clear_history_drops_every_sample_for_device(app: Flask, tmp_path: Path) -> None:
    """The Clear battery history button on each device card must wipe
    every sample for that device only, leaving other devices' history
    alone. Destructive by design: the user is the only authoritative
    'this history is wrong, start fresh' source."""
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    client = app.test_client()
    _sign_in(client)
    # Two devices with parallel histories so the cross-device isolation
    # check has something to fail against.
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_attic", "kind": "esp32_client", "name": "Attic"},
    )
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_lounge", "kind": "esp32_client", "name": "Lounge"},
    )
    now = time.time()
    for i in range(8):
        store.record("esp32_attic", pct=80 - i * 5, timestamp=now - (8 - i) * 86400 / 2)
        store.record("esp32_lounge", pct=60 - i * 3, timestamp=now - (8 - i) * 86400 / 2)
    assert len(store.recent("esp32_attic", window_days=7)) == 8
    assert len(store.recent("esp32_lounge", window_days=7)) == 8

    resp = client.post("/devices/battery/esp32_attic/clear", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/devices/battery")

    assert store.recent("esp32_attic", window_days=7) == []
    assert len(store.recent("esp32_lounge", window_days=7)) == 8, (
        "clearing one device's history must not touch other devices' history"
    )


def test_series_json_applies_per_device_battery_offset(app: Flask, tmp_path: Path) -> None:
    """When the device manifest carries a battery_offset block, the
    /series.json endpoint must return offset-adjusted percents so the
    chart matches the dashboard's current-battery readout. Without
    this, the user would calibrate a device and see the headline jump
    but the chart still drawn from raw readings, which reads as a bug."""
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_attic", "kind": "esp32_client", "name": "Attic"},
    )
    # Save a +15% offset on the device.
    resp = client.post(
        "/settings/devices/esp32_attic/battery-offset",
        data={"battery_offset_mv": "0", "battery_offset_pct": "15"},
        follow_redirects=False,
    )
    assert resp.status_code == 302, f"offset POST failed: {resp.status_code} {resp.data!r}"
    now = time.time()
    for i in range(10):
        store.record(
            "esp32_attic",
            pct=50 - i,  # values around 41-50%
            battery_mv=3700,
            timestamp=now - (10 - i) * 86400 / 2,
        )

    payload = client.get("/devices/battery/esp32_attic/series.json?window=7").get_json()
    series = payload["series"]
    assert series, "expected non-empty series"
    # Each point should have the +15% bump applied (raw 41-50 → 56-65,
    # clamped at 100 if any exceeded that). All raw values were <50
    # so the bumped values are all well under 100; verify the shift
    # rather than just the clamp.
    pcts = [p["pct"] for p in series]
    assert all(p > 50 for p in pcts), pcts


def test_clear_history_button_renders_only_for_devices_with_samples(
    app: Flask, tmp_path: Path
) -> None:
    """An empty-history card has nothing to clear; not showing the
    button avoids a "press the button → flash 'cleared 0 samples'"
    UX dead-end."""
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={"id": "esp32_attic", "kind": "esp32_client", "name": "Attic"},
    )
    now = time.time()
    for i in range(4):
        store.record("esp32_attic", pct=80 - i * 5, timestamp=now - (4 - i) * 86400 / 2)
    body = client.get("/devices/battery?window=7").get_data(as_text=True)
    assert "Clear battery history" in body
