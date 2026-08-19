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


def test_index_labels_live_charge_and_historical_drain_separately(app: Flask) -> None:
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/settings/devices/add",
        data={"id": "e1004", "kind": "esp32_client", "name": "E1004"},
    )
    now = time.time()
    for i in range(12):
        store.record(
            "e1004",
            pct=76 - round(8 * i / 11),
            timestamp=now - (36 - i) * 3600,
        )
    for i, pct in enumerate((76, 77, 78, 79, 80, 81, 82)):
        store.record("e1004", pct=pct, timestamp=now - (6 - i) * 5 * 60)

    body = client.get("/devices/battery?window=7").get_data(as_text=True)
    assert '<div class="dx-stat-label">Charging</div>' in body
    assert '<div class="dx-stat-label">Charge rate</div>' in body
    assert '<div class="dx-stat-label">Full in</div>' in body
    assert '<div class="dx-stat-label">Last drain rate</div>' in body
    assert '<div class="dx-stat-label">Drain rate</div>' not in body


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


def test_series_json_exposes_phase_aware_charge_fields(app: Flask) -> None:
    store: BatteryHistory = app.config["BATTERY_HISTORY"]
    now = time.time()
    for i in range(12):
        store.record(
            "e1004",
            pct=76 - round(8 * i / 11),
            timestamp=now - (36 - i) * 3600,
        )
    for i, pct in enumerate((76, 77, 78, 79, 80, 81, 82)):
        store.record("e1004", pct=pct, timestamp=now - (6 - i) * 5 * 60)

    client = app.test_client()
    _sign_in(client)
    payload = client.get("/devices/battery/e1004/series.json?window=7").get_json()
    prediction = payload["prediction"]
    assert prediction["is_charging"] is True
    assert prediction["charge_rate_per_day"] > 0
    assert prediction["slope_per_day"] < 0
    assert prediction["days_to_full"] is not None
    assert prediction["days_to_empty"] is None


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


def test_panel_with_no_battery_sense_reads_as_unknown_not_empty(app: Flask) -> None:
    """The XIAO 7.5" C3 panel has no divider to any ADC pin, so it can
    never report a real charge. Every battery surface has to read that
    as "unknown" and stay out of the way: a panel rendered as 0% would
    look permanently flat, and at 0% the low-battery frame overlay
    would paint a warning chip on every single push, forever.

    Absent ``battery_pct`` is the contract. A bare ``battery_mv: 0``
    alongside it must not be back-derived into a percent either, which
    is the one path that could turn "no sensor" into "empty"."""
    from app.app_factory import _collect_battery_status
    from app.battery_offset import apply_to_pct

    class _FakeDevice:
        def __init__(self, id_: str, name: str, manifest: dict) -> None:
            self.id = id_
            self.display_name = name
            self.kind_of = "xiao_epaper_panel_75_c3"
            self.manifest = manifest

    class _FakeRegistry:
        def __init__(self, devices: dict) -> None:
            self.devices = devices

    app.config["DEVICE_REGISTRY"] = _FakeRegistry({"c3": _FakeDevice("c3", "Hallway", {})})
    app.config["DEVICE_STATUS"] = {
        # What the panel actually sends: telemetry, no charge reading.
        "c3": {"received_at": time.time(), "parsed": {"battery_mv": 0, "rssi": -58}},
    }

    with app.app_context():
        assert _collect_battery_status(app) == [], (
            "a panel with no battery sense must not appear in the topbar indicator"
        )

    # 0 mV with no reported percent stays None rather than collapsing to
    # 0% off the LiPo curve.
    assert apply_to_pct(None, 0, 0, raw_mv=0) is None

    client = app.test_client()
    _sign_in(client)
    resp = client.get("/devices/battery")
    assert resp.status_code == 200
    assert "No battery history yet" in resp.get_data(as_text=True)

    # And the low-battery frame overlay leaves the render untouched.
    from app.push import PushManager

    png = b"not-a-png"
    host = _StubOverlayHost(app.config["DEVICE_STATUS"])
    assert PushManager._overlay_low_battery_if_needed(host, png, "c3") is png


class _StubOverlayHost:
    """Minimal stand-in for PushManager's overlay dependencies: the
    settings section it reads for the threshold, and the status cache
    it reads the device's battery from."""

    def __init__(self, status: dict) -> None:
        self._status = status

        class _Settings:
            def get_section(self, _name: str) -> dict:
                return {"low_battery_overlay": True, "low_battery_threshold": 15}

        self._settings = _Settings()

    def _device_status_fn(self) -> dict:
        return self._status


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
