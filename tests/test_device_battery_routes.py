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
        def __init__(self, id_: str, name: str) -> None:
            self.id = id_
            self.display_name = name
            self.kind_of = "esp32_client"

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
