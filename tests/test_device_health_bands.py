"""The status dot's freshness bands follow a sleeping device's own cycle."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.settings._shared import STATUS_FRESH_S, STATUS_WARN_S, health_for_age
from app.state.device_telemetry import DeviceTelemetry


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    return create_app(testing=True, data_root=tmp_path, plugins_dir=REPO_ROOT / "plugins")


class _Telemetry:
    def __init__(self, entry: object | None) -> None:
        self._entry = entry

    def get(self, device_id: str) -> object | None:
        return self._entry


def _health(app: Flask, entry: object | None, age: float) -> str:
    app.config["DEVICE_TELEMETRY"] = _Telemetry(entry)
    with app.app_context():
        return health_for_age("frame", age)


def test_a_device_with_no_known_cycle_keeps_the_fixed_bands(app: Flask) -> None:
    assert _health(app, None, STATUS_FRESH_S) == "ok"
    assert _health(app, None, STATUS_FRESH_S + 1) == "warn"
    assert _health(app, None, STATUS_WARN_S + 1) == "stale"


def test_a_thirty_minute_sleeper_is_not_red_between_wakes(app: Flask) -> None:
    frame = SimpleNamespace(last_sleep_interval_s=1800, always_on=False)
    assert _health(app, frame, 26 * 60) == "ok"  # mid-cycle
    assert _health(app, frame, 38 * 60) == "ok"  # a little late
    assert _health(app, frame, 50 * 60) == "warn"  # missed a wake
    assert _health(app, frame, 90 * 60) == "stale"  # missed several


def test_an_always_on_device_keeps_the_fixed_bands(app: Flask) -> None:
    kiosk = SimpleNamespace(last_sleep_interval_s=1800, always_on=True)
    assert _health(app, kiosk, 10 * 60) == "stale"


def test_a_short_cycle_never_tightens_the_fixed_bands(app: Flask) -> None:
    quick = SimpleNamespace(last_sleep_interval_s=30, always_on=False)
    assert _health(app, quick, STATUS_FRESH_S) == "ok"


# -- every other place that colours or words a device's freshness ------------


@pytest.fixture
def frame(app: Flask) -> Any:
    """A registered device whose last heartbeat was 35 minutes ago."""
    code = app.config["PAIRING_STORE"].issue(note="d").code
    resp = app.test_client().post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        json={"device_id": "frame", "kind": "pico_bin_client", "panel_w": 1600, "panel_h": 1200},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    app.config["DEVICE_STATUS"]["frame"] = {"received_at": time.time() - 35 * 60, "parsed": {}}
    return app.config["DEVICE_REGISTRY"].get("frame")


def _sleeper(app: Flask) -> None:
    app.config["DEVICE_TELEMETRY"] = _Telemetry(
        DeviceTelemetry(
            device_id="frame",
            last_heartbeat_at=time.time() - 35 * 60,
            last_sleep_interval_s=1800,
        )
    )


def test_the_dashboards_page_dot_follows_the_cycle(app: Flask, frame: Any) -> None:
    from app.page_routes import _device_health

    app.config["DEVICE_TELEMETRY"] = _Telemetry(None)
    with app.app_context():
        assert _device_health("frame") == "stale"
    _sleeper(app)
    with app.app_context():
        assert _device_health("frame") == "ok"


def test_the_device_page_header_and_devices_list_follow_the_cycle(app: Flask, frame: Any) -> None:
    from app.settings.index_routes import _status_view

    macros = app.jinja_env.get_template("_device_parts.html").make_module({})

    def header() -> tuple[str, str]:
        with app.test_request_context():
            view = _status_view(frame)
            return view["health"], str(macros.device_status({"status": view}))

    app.config["DEVICE_TELEMETRY"] = _Telemetry(None)
    health, text = header()
    assert health == "stale"
    assert "stale" in text

    _sleeper(app)
    health, text = header()
    assert health == "ok"
    assert "online" in text
    assert "stale" not in text


def test_the_companion_freshness_flag_follows_the_cycle(app: Flask, frame: Any) -> None:
    from app.device_capability import heartbeat_freshness

    status = app.config["DEVICE_STATUS"]["frame"]
    with app.app_context():
        app.config["DEVICE_TELEMETRY"] = _Telemetry(None)
        assert heartbeat_freshness(frame, status)[0] == "stale"  # 35 min > the 5 min floor
        _sleeper(app)
        assert heartbeat_freshness(frame, status)[0] == "fresh"  # 35 min < 3 x 30 min


def test_a_cycle_telemetry_cannot_use_is_ignored() -> None:
    from app.state.device_telemetry import known_sleep_cycle_s

    for bad in (None, 0, -5, True, "1800"):
        entry = SimpleNamespace(last_sleep_interval_s=bad, always_on=False)
        assert known_sleep_cycle_s(_Telemetry(entry), "frame") is None
    assert known_sleep_cycle_s(None, "frame") is None
