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


def _device(kind: str, transport: str) -> SimpleNamespace:
    return SimpleNamespace(kind_of=kind, transport=transport)


def _app(
    tmp_path: Path,
    *,
    install: str,
    online_on: bool = True,
    devices: list[Any] | None = None,
    ha: bool = False,
) -> tuple[_FakeApp, list[dict[str, Any]]]:
    app_section: dict[str, Any] = {"ha_discovery_enabled": ha}
    if not online_on:
        app_section["online_features"] = False
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
    assert p["ha"] is True
    assert p["py"].startswith("3.") and p["py"].count(".") == 1
    assert p["os"] in ("linux", "macos", "windows", "other")
    assert p["arch"] in ("x86_64", "arm64", "arm", "other")
    assert p["deploy"] in ("ha_addon", "docker", "lxc", "source", "pip")


def test_build_payload_no_devices(tmp_path: Path, test_install_uuid: Any) -> None:
    app, _ = _app(tmp_path, install=test_install_uuid(), devices=[])
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["devices"] == "0" and p["device_kinds"] == [] and p["transport"] == "none"


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


def test_maybe_send_retries_sooner_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any
) -> None:
    app, records = _app(tmp_path, install=test_install_uuid())
    monkeypatch.setattr(online, "send_heartbeat", lambda f: False)
    assert heartbeat.maybe_send(app, now=1000.0) is False  # type: ignore[arg-type]
    due = json.loads((tmp_path / "core" / "heartbeat.json").read_text())["next_due"]
    assert due <= 1000.0 + heartbeat._RETRY_SECONDS + 1  # retry within ~1h, not a full day
    assert records and records[0]["status"] == "failed"
