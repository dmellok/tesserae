"""Unit tests for the daily heartbeat (app.heartbeat).

Uses a lightweight fake app so the tests stay fast and don't start the daemon
thread. Network is never hit: online.send_heartbeat is monkeypatched.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import heartbeat, online


class _FakeApp:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config


def _device(kind: str, transport: str, device_id: str = "") -> SimpleNamespace:
    return SimpleNamespace(kind_of=kind, transport=transport, id=device_id)


def _app(
    tmp_path: Path,
    *,
    install: str,
    online_on: bool = True,
    devices: list[Any] | None = None,
    ha: bool = False,
    status: dict[str, Any] | None = None,
) -> tuple[_FakeApp, list[dict[str, Any]]]:
    app_section: dict[str, Any] = {"ha_discovery_enabled": ha, "online_features": online_on}
    settings = SimpleNamespace(get_section=lambda name, _s=app_section: _s if name == "app" else {})
    registry = SimpleNamespace(all=lambda _d=list(devices or []): _d)
    records: list[dict[str, Any]] = []
    event_log = SimpleNamespace(record=lambda **kw: records.append(kw))
    config = {
        "SETTINGS_STORE": settings,
        "DATA_ROOT": tmp_path,
        "INSTALL_ID": install,
        "APP_VERSION": "0.94.2",
        "DEVICE_REGISTRY": registry,
        "DEVICE_STATUS": status or {},
        "EVENT_LOG": event_log,
    }
    return _FakeApp(config), records


def test_build_payload_shape(tmp_path: Path, test_install_uuid: Any) -> None:
    uid = test_install_uuid()
    app, _ = _app(
        tmp_path,
        install=uid,
        devices=[_device("pimoroni_inky_4", "mqtt"), _device("waveshare_x", "rest")],
        ha=True,
    )
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["install"] == uid and uid.startswith("7e57c0de-")
    assert p["version"] == "0.94.2"
    assert p["channel"] == "stable"
    assert p["transport"] == "both"  # one mqtt + one rest device
    assert p["devices"] == "2-3"  # bucketed, not exact
    assert p["device_kinds"] == ["pimoroni_inky_4", "waveshare_x"]
    assert p["fw_by_kind"] == {}  # no status heartbeats in this fixture
    assert p["ha"] is True
    assert p["py"].startswith("3.") and p["py"].count(".") == 1
    assert p["os"] in ("linux", "macos", "windows", "other")
    assert p["arch"] in ("x86_64", "arm64", "arm", "other")
    assert p["deploy"] in ("ha_addon", "docker", "lxc", "source", "pip")


def test_build_payload_no_devices(tmp_path: Path, test_install_uuid: Any) -> None:
    app, _ = _app(tmp_path, install=test_install_uuid(), devices=[])
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["devices"] == "0" and p["device_kinds"] == [] and p["transport"] == "none"


def test_build_payload_ignores_builtin_kinds(tmp_path: Path, test_install_uuid: Any) -> None:
    """``registry.all()`` returns the built-in device kinds + hardware
    SKUs (``kind_of is None``) alongside real instances. Only instances are
    the operator's hardware, so counting the catalog kinds made every install
    report "10+". The count, transport, and kinds must key off instances."""
    devices = [
        # 22 catalog kinds (kind_of None): 8 built-ins + 14 SKUs in the repo.
        *[_device(None, "mqtt") for _ in range(22)],
        # One real instance the operator actually configured.
        _device("pi_bin_client", "rest"),
    ]
    app, _ = _app(tmp_path, install=test_install_uuid(), devices=devices)
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["devices"] == "1"  # one instance, not "10+"
    assert p["device_kinds"] == ["pi_bin_client"]
    assert p["transport"] == "rest"  # the kinds' mqtt transport is ignored


def test_build_payload_all_kinds_no_instances(tmp_path: Path, test_install_uuid: Any) -> None:
    """A brand-new install with the catalog loaded but no device added yet
    reports zero devices, not the kind count."""
    app, _ = _app(
        tmp_path, install=test_install_uuid(), devices=[_device(None, "rest") for _ in range(22)]
    )
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["devices"] == "0" and p["device_kinds"] == [] and p["transport"] == "none"


def test_build_payload_firmware_by_kind(tmp_path: Path, test_install_uuid: Any) -> None:
    """Firmware versions are aggregated per device kind from each
    instance's latest status heartbeat, deduped + sorted. Instances that
    haven't reported firmware are simply absent from the map."""
    devices = [
        _device("pi_bin_client", "rest", device_id="pi-a"),
        _device("pi_bin_client", "rest", device_id="pi-b"),
        _device("esp32_client", "mqtt", device_id="esp-a"),
        _device("esp32_client", "mqtt", device_id="esp-silent"),  # no fw reported
    ]
    status = {
        "pi-a": {"parsed": {"fw_version": "1.3.1"}},
        "pi-b": {"parsed": {"fw_version": "1.2.0"}},
        "esp-a": {"parsed": {"fw_version": "0.9.0"}},
        "esp-silent": {"parsed": {}},
    }
    app, _ = _app(tmp_path, install=test_install_uuid(), devices=devices, status=status)
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["fw_by_kind"] == {
        "pi_bin_client": ["1.2.0", "1.3.1"],  # deduped + sorted across two panels
        "esp32_client": ["0.9.0"],  # the silent one contributes nothing
    }


def test_maybe_send_skips_when_online_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any
) -> None:
    app, records = _app(tmp_path, install=test_install_uuid(), online_on=False)
    calls: list[Any] = []
    monkeypatch.setattr(online, "send_heartbeat", lambda f: bool(calls.append(f)) or True)
    assert heartbeat.maybe_send(app, now=1000.0) is False  # type: ignore[arg-type]
    assert calls == []
    assert not (tmp_path / "core" / "heartbeat.json").exists()  # no state written
    assert records == []


def test_maybe_send_sends_then_dedupes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any
) -> None:
    app, records = _app(tmp_path, install=test_install_uuid(), devices=[_device("k", "rest")])
    calls: list[Any] = []
    monkeypatch.setattr(online, "send_heartbeat", lambda f: bool(calls.append(f)) or True)

    assert heartbeat.maybe_send(app, now=1000.0) is True  # type: ignore[arg-type]
    assert len(calls) == 1
    # A second call shortly after does nothing (next_due is ~a day out).
    assert heartbeat.maybe_send(app, now=1001.0) is False  # type: ignore[arg-type]
    assert len(calls) == 1
    # It logged a telemetry heartbeat event.
    assert records and records[0]["type"] == "telemetry" and records[0]["source"] == "heartbeat"
    assert records[0]["status"] == "sent"
    # Once next_due passes, it sends again.
    due = json.loads((tmp_path / "core" / "heartbeat.json").read_text())["next_due"]
    assert due >= 1000.0 + heartbeat._INTERVAL_SECONDS - heartbeat._JITTER_SECONDS
    assert heartbeat.maybe_send(app, now=due + 1) is True  # type: ignore[arg-type]
    assert len(calls) == 2


def test_start_is_noop_under_pytest(tmp_path: Path, test_install_uuid: Any) -> None:
    """``start`` must not spin up the daemon during the suite, even when the app
    was built with create_app(testing=False) and TESTING isn't set yet (the
    fixtures' pattern). PYTEST_CURRENT_TEST is always set here, so it no-ops."""
    import threading

    app, _ = _app(tmp_path, install=test_install_uuid())
    app.config.pop("TESTING", None)  # mimic mid-create_app, before TESTING is set
    heartbeat.start(app)  # type: ignore[arg-type]
    assert not any(t.name == "tesserae-heartbeat" for t in threading.enumerate())


def test_maybe_send_retries_sooner_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any
) -> None:
    app, records = _app(tmp_path, install=test_install_uuid())
    monkeypatch.setattr(online, "send_heartbeat", lambda f: False)
    assert heartbeat.maybe_send(app, now=1000.0) is False  # type: ignore[arg-type]
    due = json.loads((tmp_path / "core" / "heartbeat.json").read_text())["next_due"]
    assert due <= 1000.0 + heartbeat._RETRY_SECONDS + 1  # retry within ~1h, not a full day
    assert records and records[0]["status"] == "failed"
