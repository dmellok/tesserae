"""Server-side ingest of the device OTA state report (#121).

The device extends the ``ota`` capability object in its heartbeat with lifecycle
report fields (contract: docs/ota/contract.md, "State reporting"). The server
records the latest report on the device's live status and event-logs each
transition once, deduping a terminal report re-sent every heartbeat."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.ota import report as otar

# -- unit: parse_report / report_changed --------------------------------------


def test_parse_report_ignores_capability_only_and_idle() -> None:
    assert otar.parse_report(json.dumps({"ota": {"schema": 1}})) is None
    assert otar.parse_report(json.dumps({"ota": {"schema": 1, "phase": "idle"}})) is None
    assert otar.parse_report(json.dumps({"battery_pct": 80})) is None
    assert otar.parse_report(b"not json") is None


def test_parse_report_rejects_unknown_phase() -> None:
    assert otar.parse_report(json.dumps({"ota": {"phase": "teleporting"}})) is None


def test_parse_report_extracts_fields_and_caps_detail() -> None:
    body = {
        "ota": {
            "schema": 1,
            "phase": "  confirmed  ",
            "reason": "ok",
            "target_fw": "1.6.0",
            "attempt_id": "a3f19c",
            "detail": "x" * 500,
        }
    }
    report = otar.parse_report(json.dumps(body))
    assert report == {
        "phase": "confirmed",
        "reason": "ok",
        "target_fw": "1.6.0",
        "attempt_id": "a3f19c",
        "detail": "x" * 200,
    }


def test_report_changed_dedups_identical_terminal_report() -> None:
    a = {"phase": "confirmed", "target_fw": "1.6.0", "received_at": 100.0}
    b = {"phase": "confirmed", "target_fw": "1.6.0", "received_at": 200.0}
    # received_at is not an identity field, so a re-sent report is unchanged.
    assert otar.report_changed(a, b) is False
    assert otar.report_changed(None, a) is True
    assert otar.report_changed(a, {"phase": "confirmed", "target_fw": "1.7.0"}) is True


def test_is_failure() -> None:
    assert otar.is_failure({"phase": "rolled_back"}) is True
    assert otar.is_failure({"phase": "failed"}) is True
    assert otar.is_failure({"phase": "confirmed"}) is False


# -- integration: record_status_heartbeat -------------------------------------


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


def _beat(app: Flask, device: object, body: dict) -> None:
    from app.transport_wiring import record_status_heartbeat

    record_status_heartbeat(
        app=app,
        device=device,
        payload=json.dumps(body).encode(),
        status_cache=app.config["DEVICE_STATUS"],
        event_log=app.config["EVENT_LOG"],
        event_target="rest://dev/status",
    )


def _device(app: Flask) -> object:
    from app import device_service

    res = device_service.create_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        data_root=app.config["DEVICE_DATA_ROOT"],
        instance_id="canary",
        kind_id="esp32_client",
        orientation="landscape",
    )
    assert res.device is not None
    return res.device


def test_heartbeat_stores_report_and_logs_once(app: Flask) -> None:
    with app.app_context():
        dev = _device(app)
        log = app.config["EVENT_LOG"]
        # Downloading, then confirmed: two distinct lifecycle events.
        _beat(app, dev, {"battery_pct": 80, "ota": {"schema": 1, "phase": "downloading"}})
        _beat(
            app,
            dev,
            {"battery_pct": 80, "ota": {"schema": 1, "phase": "confirmed", "target_fw": "1.6.0"}},
        )
        stored = app.config["DEVICE_STATUS"]["canary"]["ota"]
        assert stored["phase"] == "confirmed"
        assert stored["target_fw"] == "1.6.0"
        assert "received_at" in stored

        ota_events = [e for e in log.list(source="canary") if "ota" in e.extra]
        assert len(ota_events) == 2
        assert ota_events[0].extra["ota"]["phase"] == "confirmed"  # newest first

        # Re-sending the same terminal report must not log again.
        _beat(
            app,
            dev,
            {"battery_pct": 80, "ota": {"schema": 1, "phase": "confirmed", "target_fw": "1.6.0"}},
        )
        assert len([e for e in log.list(source="canary") if "ota" in e.extra]) == 2


def test_idle_heartbeat_preserves_last_report(app: Flask) -> None:
    with app.app_context():
        dev = _device(app)
        _beat(app, dev, {"ota": {"schema": 1, "phase": "confirmed", "target_fw": "1.6.0"}})
        # A later capability-only / idle beat leaves the outcome chip standing.
        _beat(app, dev, {"battery_pct": 79, "ota": {"schema": 1}})
        stored = app.config["DEVICE_STATUS"]["canary"]["ota"]
        assert stored["phase"] == "confirmed"
        assert stored["target_fw"] == "1.6.0"


def test_failure_report_logs_at_error(app: Flask) -> None:
    with app.app_context():
        dev = _device(app)
        log = app.config["EVENT_LOG"]
        _beat(
            app,
            dev,
            {"ota": {"schema": 1, "phase": "rolled_back", "reason": "boot_failed"}},
        )
        events = [e for e in log.list(source="canary") if "ota" in e.extra]
        assert len(events) == 1
        assert events[0].status == "error"
        assert events[0].extra["ota"]["reason"] == "boot_failed"
