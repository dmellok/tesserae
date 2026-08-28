"""Synchronized wake (wake alignment): grid math, the /status wiring,
the lead EWMA, and the Schedule-tab save path.

The whole feature is server-side arithmetic — devices sharing a grid
sync because the wall clock is the coordinator — so the unit tests pin
the grid math with a fixed clock + UTC, and the endpoint tests pin the
app timezone to UTC so boundary assertions don't depend on the host tz.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from flask import Flask

from app import wake_alignment as wa
from app.main import REPO_ROOT, create_app
from app.quiet_hours import QuietHoursWindow, _parse_hhmm
from app.state.device_telemetry import TelemetryStore


def _epoch(hh: int, mm: int, ss: int = 0) -> float:
    """An arbitrary fixed UTC day at HH:MM:SS."""
    return datetime(2026, 8, 26, hh, mm, ss, tzinfo=UTC).timestamp()


# -- parsing -------------------------------------------------------------


def test_parse_hhmm_accepts_valid_and_rejects_junk() -> None:
    assert wa.parse_hhmm("07:30") == (7, 30)
    assert wa.parse_hhmm("7:05") == (7, 5)
    assert wa.parse_hhmm("23:59") == (23, 59)
    for bad in ("24:00", "12:60", "noon", "", None, 7, "07:5"):
        assert wa.parse_hhmm(bad) is None


def test_parse_times_list_normalises_dedupes_and_sorts() -> None:
    assert wa.parse_times_list("19:00, 7:00,07:00 12:30") == ["07:00", "12:30", "19:00"]
    assert wa.parse_times_list(["08:00", "junk", "8:00"]) == ["08:00"]
    assert wa.parse_times_list("") == []
    assert wa.parse_times_list(None) == []


def test_alignment_from_stored_modes() -> None:
    assert wa.alignment_from_stored(None) is None
    assert wa.alignment_from_stored({}) is None
    assert wa.alignment_from_stored({"wake_align_mode": "off"}) is None
    interval = wa.alignment_from_stored({"wake_align_mode": "interval"})
    assert interval is not None and interval.mode == "interval" and interval.anchor == "00:00"
    anchored = wa.alignment_from_stored(
        {"wake_align_mode": "interval", "wake_align_anchor": "0:05"}
    )
    assert anchored is not None and anchored.anchor == "00:05"
    times = wa.alignment_from_stored(
        {"wake_align_mode": "times", "wake_align_times": ["19:00", "07:00"]}
    )
    assert times is not None and times.times == ("07:00", "19:00")
    # Times mode with nothing usable is off, not an error.
    assert wa.alignment_from_stored({"wake_align_mode": "times", "wake_align_times": "x"}) is None


# -- grid math -----------------------------------------------------------


def test_interval_mode_lands_on_the_next_grid_point() -> None:
    alignment = wa.WakeAlignment(mode="interval")
    got = wa.next_aligned_wake_epoch(alignment, now=_epoch(10, 7), tz=UTC, interval_s=900)
    assert got == _epoch(10, 15)


def test_interval_mode_honours_the_anchor_offset() -> None:
    alignment = wa.WakeAlignment(mode="interval", anchor="00:05")
    got = wa.next_aligned_wake_epoch(alignment, now=_epoch(10, 7), tz=UTC, interval_s=900)
    assert got == _epoch(10, 20)


def test_interval_mode_subtracts_the_lead() -> None:
    alignment = wa.WakeAlignment(mode="interval")
    got = wa.next_aligned_wake_epoch(
        alignment, now=_epoch(10, 7), tz=UTC, interval_s=900, lead_s=20
    )
    assert got == _epoch(10, 14, 40)


def test_interval_mode_skips_a_grid_point_too_close_to_now() -> None:
    """A device checking in just before a grid point isn't told to come
    straight back: the wake must be at least MIN_DELTA_S out."""
    alignment = wa.WakeAlignment(mode="interval")
    got = wa.next_aligned_wake_epoch(alignment, now=_epoch(10, 14, 50), tz=UTC, interval_s=900)
    assert got == _epoch(10, 30)


def test_interval_mode_clamps_a_silly_lead() -> None:
    alignment = wa.WakeAlignment(mode="interval")
    got = wa.next_aligned_wake_epoch(
        alignment, now=_epoch(10, 0), tz=UTC, interval_s=3600, lead_s=10_000
    )
    assert got == _epoch(11, 0) - wa.LEAD_MAX_S


def test_interval_mode_skips_grid_points_inside_quiet_hours() -> None:
    quiet = QuietHoursWindow(_parse_hhmm("10:10"), _parse_hhmm("11:00"))
    alignment = wa.WakeAlignment(mode="interval")
    got = wa.next_aligned_wake_epoch(
        alignment, now=_epoch(10, 7), tz=UTC, interval_s=900, quiet=quiet
    )
    assert got == _epoch(11, 15)


def test_interval_mode_gives_up_when_quiet_hours_swallow_the_horizon() -> None:
    quiet = QuietHoursWindow(_parse_hhmm("00:00"), _parse_hhmm("23:59"))
    alignment = wa.WakeAlignment(mode="interval")
    got = wa.next_aligned_wake_epoch(
        alignment, now=_epoch(10, 7), tz=UTC, interval_s=900, quiet=quiet
    )
    assert got is None


def test_interval_mode_needs_a_positive_interval() -> None:
    alignment = wa.WakeAlignment(mode="interval")
    assert wa.next_aligned_wake_epoch(alignment, now=_epoch(10, 7), tz=UTC, interval_s=0) is None


def test_times_mode_picks_the_next_listed_time_today() -> None:
    alignment = wa.WakeAlignment(mode="times", times=("07:00", "19:00"))
    got = wa.next_aligned_wake_epoch(alignment, now=_epoch(8, 0), tz=UTC, interval_s=900)
    assert got == _epoch(19, 0)


def test_times_mode_rolls_to_tomorrow_after_the_last_time() -> None:
    alignment = wa.WakeAlignment(mode="times", times=("07:00", "19:00"))
    got = wa.next_aligned_wake_epoch(alignment, now=_epoch(20, 0), tz=UTC, interval_s=900)
    assert got == _epoch(7, 0) + 24 * 3600


def test_times_mode_skips_a_quiet_time_to_the_next_listed_one() -> None:
    quiet = QuietHoursWindow(_parse_hhmm("18:00"), _parse_hhmm("20:00"))
    alignment = wa.WakeAlignment(mode="times", times=("19:00", "22:00"))
    got = wa.next_aligned_wake_epoch(
        alignment, now=_epoch(8, 0), tz=UTC, interval_s=900, quiet=quiet
    )
    assert got == _epoch(22, 0)


# -- telemetry: server directive + lead EWMA -----------------------------


def test_note_server_directive_overwrites_a_stale_firmware_prediction(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.json")
    now = time.time()
    store.record_heartbeat(
        "panel", received_at=now, parsed={"next_sleep_s": 900}, configured_sleep_s=900
    )
    entry = store.note_server_directive("panel", wake_at=now + 512, interval_s=512)
    assert entry is not None
    assert entry.prediction_source == "server"
    assert entry.predicted_next_wake_at == pytest.approx(now + 512)
    assert entry.last_sleep_interval_s == 512


def test_lead_ewma_learns_from_arrivals_against_server_directives(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.json")
    t0 = time.time() - 2000
    store.record_heartbeat("panel", received_at=t0, parsed={}, configured_sleep_s=900)
    store.note_server_directive("panel", wake_at=t0 + 900, interval_s=900)

    # Arrives 25 s after the directed wake: first lead sample.
    entry = store.record_heartbeat("panel", received_at=t0 + 925, parsed={}, configured_sleep_s=900)
    assert entry.wake_lead_ewma_s == pytest.approx(25.0)

    store.note_server_directive("panel", wake_at=t0 + 1800, interval_s=875)
    entry = store.record_heartbeat(
        "panel", received_at=t0 + 1835, parsed={}, configured_sleep_s=900
    )
    assert entry.wake_lead_ewma_s == pytest.approx(0.7 * 25 + 0.3 * 35)


def test_lead_ewma_ignores_outage_sized_offsets(tmp_path: Path) -> None:
    store = TelemetryStore(tmp_path / "telemetry.json")
    t0 = time.time() - 5000
    store.record_heartbeat("panel", received_at=t0, parsed={}, configured_sleep_s=900)
    store.note_server_directive("panel", wake_at=t0 + 900, interval_s=900)
    entry = store.record_heartbeat(
        "panel", received_at=t0 + 900 + 600, parsed={}, configured_sleep_s=900
    )
    assert entry.wake_lead_ewma_s is None


def test_lead_ewma_survives_a_reload(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.json"
    store = TelemetryStore(path)
    t0 = time.time() - 2000
    store.record_heartbeat("panel", received_at=t0, parsed={}, configured_sleep_s=900)
    store.note_server_directive("panel", wake_at=t0 + 900, interval_s=900)
    store.record_heartbeat("panel", received_at=t0 + 930, parsed={}, configured_sleep_s=900)

    reloaded = TelemetryStore(path).get("panel")
    assert reloaded is not None
    assert reloaded.wake_lead_ewma_s == pytest.approx(30.0)
    # The last heartbeat re-derived a configured prediction, and the
    # source now persists across restarts too.
    assert reloaded.prediction_source == "configured"


# -- REST /status wiring -------------------------------------------------


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
    # Pin the grid's timezone so boundary assertions don't depend on the
    # host machine's local zone.
    store = a.config["SETTINGS_STORE"]
    app_section = dict(store.get_section("app") or {})
    app_section["timezone"] = "UTC"
    store.update_section("app", app_section)
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _pair(app: Flask, device_id: str = "grid_panel") -> tuple:
    client = app.test_client()
    _sign_in(client)
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": device_id,
                "kind": "pico_bin_client",
                "panel_w": 1600,
                "panel_h": 1200,
                "fw_version": "0.1.0",
            }
        ),
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return client, resp.get_json()["device_token"]


def _configure(app: Flask, device_id: str, **fields) -> None:
    store = app.config["SETTINGS_STORE"]
    entry = dict((store.get_section("devices") or {}).get(device_id) or {})
    entry.update(fields)
    store.patch_section("devices", {device_id: entry})


def _status(client, token: str, device_id: str = "grid_panel") -> dict:
    resp = client.post(
        f"/api/v1/device/{device_id}/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 80}),
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def _expected_grid_delta(interval: int, now: float) -> int:
    delta = interval - int(now) % interval
    if delta < wa.MIN_DELTA_S:
        delta += interval
    return delta


def test_status_aligns_next_poll_to_the_clock_grid(app: Flask) -> None:
    client, token = _pair(app)
    _configure(app, "grid_panel", sleep_interval_s=900, wake_align_mode="interval")

    before = time.time()
    body = _status(client, token)
    poll = int(body["next_poll_s"])
    # The wake must land on a 15-minute UTC boundary (anchor 00:00).
    target = int(before) + poll
    assert abs(target % 900) <= 2 or 900 - (target % 900) <= 2
    assert poll <= 900 + 2
    assert _expected_grid_delta(900, before) - 2 <= poll <= _expected_grid_delta(900, before) + 2
    # And the same instant rides along as an absolute epoch for capable
    # firmware.
    assert body.get("wake_at") == pytest.approx(int(before) + poll, abs=2)


def test_status_without_alignment_has_no_wake_at(app: Flask) -> None:
    client, token = _pair(app)
    _configure(app, "grid_panel", sleep_interval_s=900)
    body = _status(client, token)
    assert "wake_at" not in body
    assert int(body["next_poll_s"]) == 900


def test_times_mode_sleeps_past_the_configured_interval(app: Flask) -> None:
    """Set-times mode is the one case that may exceed the configured
    ceiling: the operator asked for exact wake moments."""
    client, token = _pair(app)
    now_utc = datetime.now(UTC)
    # Pick a listed time ~2 hours out so the delta clearly exceeds the
    # 900 s interval (and stays >= MIN_DELTA_S regardless of when the
    # test runs).
    target = now_utc.timestamp() + 2 * 3600
    hhmm = datetime.fromtimestamp(target, UTC).strftime("%H:%M")
    _configure(
        app, "grid_panel", sleep_interval_s=900, wake_align_mode="times", wake_align_times=[hhmm]
    )
    body = _status(client, token)
    poll = int(body["next_poll_s"])
    assert poll > 900
    # The absolute wake instant lands on the listed HH:MM (grid points
    # are whole minutes; wake_at carries at most a second of rounding).
    wake_at = int(body["wake_at"])
    landed = datetime.fromtimestamp(round(wake_at / 60) * 60, UTC).strftime("%H:%M")
    assert landed == hhmm


def test_aligned_status_records_a_server_prediction(app: Flask) -> None:
    client, token = _pair(app)
    _configure(app, "grid_panel", sleep_interval_s=900, wake_align_mode="interval")
    body = _status(client, token)
    entry = app.config["DEVICE_TELEMETRY"].get("grid_panel")
    assert entry is not None
    assert entry.prediction_source == "server"
    assert entry.predicted_next_wake_at == pytest.approx(body["wake_at"], abs=1)


def test_alignment_falls_back_to_the_interval_when_quiet_hours_swallow_it(app: Flask) -> None:
    """A quiet window covering the whole day leaves no grid point; the
    device keeps its plain relative interval rather than stranding."""
    client, token = _pair(app)
    _configure(app, "grid_panel", sleep_interval_s=600, wake_align_mode="interval")
    store = app.config["SETTINGS_STORE"]
    app_section = dict(store.get_section("app") or {})
    app_section.update(
        {"quiet_hours_enabled": True, "quiet_hours_start": "00:00", "quiet_hours_end": "23:59"}
    )
    store.update_section("app", app_section)
    body = _status(client, token)
    assert int(body["next_poll_s"]) == 600
    assert "wake_at" not in body


# -- Settings save path --------------------------------------------------


@pytest.fixture
def ui_app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _add_device(client, instance_id: str) -> None:
    resp = client.post(
        "/settings/devices/add",
        data={
            "kind": "circuitpython_generic",
            "id": instance_id,
            "name": instance_id,
            "transport": "rest",
        },
    )
    assert resp.status_code in (200, 302)


def test_saving_interval_alignment_persists(ui_app: Flask) -> None:
    client = ui_app.test_client()
    _sign_in(client)
    _add_device(client, "kitchen")

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={"wake_align_mode": "interval", "wake_align_anchor": "00:05"},
    )
    assert resp.status_code == 302
    stored = ui_app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})
    assert stored.get("wake_align_mode") == "interval"
    assert stored.get("wake_align_anchor") == "00:05"


def test_saving_times_alignment_normalises_the_list(ui_app: Flask) -> None:
    client = ui_app.test_client()
    _sign_in(client)
    _add_device(client, "kitchen")

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={"wake_align_mode": "times", "wake_align_times": "19:00, 7:00 07:00"},
    )
    assert resp.status_code == 302
    stored = ui_app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})
    assert stored.get("wake_align_mode") == "times"
    assert stored.get("wake_align_times") == ["07:00", "19:00"]


def test_saving_off_drops_the_alignment_block(ui_app: Flask) -> None:
    client = ui_app.test_client()
    _sign_in(client)
    _add_device(client, "kitchen")
    client.post(
        "/settings/devices/kitchen/save",
        data={"wake_align_mode": "interval", "wake_align_anchor": "00:05"},
    )

    resp = client.post("/settings/devices/kitchen/save", data={"wake_align_mode": ""})
    assert resp.status_code == 302
    stored = ui_app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})
    assert "wake_align_mode" not in stored
    assert "wake_align_anchor" not in stored


def test_times_mode_without_valid_times_is_rejected(ui_app: Flask) -> None:
    client = ui_app.test_client()
    _sign_in(client)
    _add_device(client, "kitchen")

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={"wake_align_mode": "times", "wake_align_times": "not a time"},
        follow_redirects=True,
    )
    assert b"synchronized wake" in resp.data
    stored = ui_app.config["SETTINGS_STORE"].get_section("devices").get("kitchen", {})
    assert "wake_align_mode" not in stored


def test_apply_all_copies_the_grid_to_every_rest_device(ui_app: Flask) -> None:
    client = ui_app.test_client()
    _sign_in(client)
    _add_device(client, "kitchen")
    _add_device(client, "hall")
    _add_device(client, "office")

    resp = client.post(
        "/settings/devices/kitchen/save",
        data={
            "wake_align_mode": "interval",
            "wake_align_anchor": "00:00",
            "wake_align_apply_all": "on",
        },
    )
    assert resp.status_code == 302
    devices_section = ui_app.config["SETTINGS_STORE"].get_section("devices")
    for instance_id in ("kitchen", "hall", "office"):
        assert devices_section.get(instance_id, {}).get("wake_align_mode") == "interval", (
            instance_id
        )


def test_wake_align_form_renders_on_the_devices_page(ui_app: Flask) -> None:
    client = ui_app.test_client()
    _sign_in(client)
    _add_device(client, "kitchen")
    body = client.get("/settings/devices").get_data(as_text=True)
    assert 'name="wake_align_mode"' in body
    assert "Synchronized wake" in body
