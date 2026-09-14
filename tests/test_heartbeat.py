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
    companion: int | None = None,
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
    if companion is not None:
        # Only ``len(list_active())`` is read; the contents don't matter.
        config["COMPANION_TOKENS"] = SimpleNamespace(
            list_active=lambda _n=companion: list(range(_n))
        )
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
    assert p["channel"] in ("stable", "main", "edge")  # derived from the build, see channel()
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


def test_build_payload_companion_is_bucketed(tmp_path: Path, test_install_uuid: Any) -> None:
    app, _ = _app(tmp_path, install=test_install_uuid(), companion=2)
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    # Bucketed, never an exact count, and only the count, no client identity.
    assert p["companion"] == "2-3"


def test_build_payload_companion_defaults_to_zero_when_unwired(
    tmp_path: Path, test_install_uuid: Any
) -> None:
    app, _ = _app(tmp_path, install=test_install_uuid())
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["companion"] == "0"


def test_build_payload_ignores_builtin_kinds(tmp_path: Path, test_install_uuid: Any) -> None:
    """``registry.all()`` returns the built-in device kinds + hardware
    SKUs (``kind_of is None``) alongside real instances. Only instances are
    the operator's hardware, so counting the catalog kinds made every install
    report "10+". The count, transport, and kinds must key off instances."""
    devices = [
        # Catalog kinds (kind_of None); the repo ships 33 (10 built-ins +
        # 23 SKUs), the exact count here doesn't matter.
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


# richer payload: channel, age, kinds, features, ota ---------------------------


def test_build_payload_kinds_objects_carry_firmware_and_panel_facts(
    tmp_path: Path, test_install_uuid: Any
) -> None:
    """``kinds`` is the per-kind object list the API reads: newest firmware,
    panel gamut + landscape-normalised resolution, bucketed wake cadence."""
    a = _device("esp32_client", "mqtt", device_id="esp-a")
    a.panel = {"w": 480, "h": 800, "gamut": "gray_4"}  # portrait instance
    a.config_schema = {"sleep_interval_s": {"default": 900}}
    a.manifest = {"touch": True}
    b = _device("esp32_client", "mqtt", device_id="esp-b")
    b.panel = None
    b.config_schema = {}
    b.manifest = {}
    status = {
        "esp-a": {"parsed": {"fw_version": "1.2.0"}},
        "esp-b": {"parsed": {"fw_version": "1.3.1"}},
    }
    app, _ = _app(tmp_path, install=test_install_uuid(), devices=[a, b], status=status)
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    assert p["kinds"] == [
        {
            "kind": "esp32_client",
            "gamut": "gray_4",
            "res": "800x480",
            "sleep": "5-15m",
            "fw_version": "1.3.1",
        }
    ]
    # Legacy shape still present for older API builds.
    assert p["fw_by_kind"] == {"esp32_client": ["1.2.0", "1.3.1"]}
    assert p["features"] == {"relay": False, "touch": True, "mcp": False, "quiet_hours": False}
    assert p["lineups"] == "0"
    assert p["ota"] == {"offered": "0", "applied": "0", "failed": "0"}
    assert p["age"] == "unknown"  # no install-id metadata file in the fixture


def test_build_payload_age_bucket_from_install_metadata(
    tmp_path: Path, test_install_uuid: Any
) -> None:
    uid = test_install_uuid()
    from app import install_id as install_id_module

    path = install_id_module.install_id_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": uid, "created_at": "2020-01-01T00:00:00+00:00"}), encoding="utf-8"
    )
    app, _ = _app(tmp_path, install=uid)
    assert heartbeat.build_payload(app)["age"] == "90+"  # type: ignore[arg-type]


def test_age_bucket_edges() -> None:
    from datetime import UTC, datetime, timedelta

    def ago(days: float) -> str:
        return (datetime.now(UTC) - timedelta(days=days)).isoformat()

    assert heartbeat._age_bucket(ago(0.2)) == "0"
    assert heartbeat._age_bucket(ago(3)) == "1-7"
    assert heartbeat._age_bucket(ago(20)) == "8-30"
    assert heartbeat._age_bucket(ago(60)) == "31-90"
    assert heartbeat._age_bucket(ago(400)) == "90+"
    assert heartbeat._age_bucket("garbage") == "unknown"
    assert heartbeat._age_bucket("") == "unknown"


def test_sleep_bucket() -> None:
    assert heartbeat._sleep_bucket({"always_on": True, "sleep_interval_s": 60}, {}) == "always_on"
    assert heartbeat._sleep_bucket({"sleep_interval_s": 60}, {}) == "<5m"
    assert heartbeat._sleep_bucket({"sleep_interval_s": 900}, {}) == "5-15m"
    assert heartbeat._sleep_bucket({"sleep_interval_s": 3600}, {}) == "15-60m"
    assert heartbeat._sleep_bucket({"sleep_interval_s": 7200}, {}) == "1-6h"
    assert heartbeat._sleep_bucket({"sleep_interval_s": 86400}, {}) == "6h+"
    assert heartbeat._sleep_bucket({}, {"sleep_interval_s": {"default": 300}}) == "5-15m"
    assert heartbeat._sleep_bucket({}, {}) == "unknown"
    assert heartbeat._sleep_bucket({"sleep_interval_s": "soon"}, {}) == "unknown"


def test_channel_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heartbeat, "_channel_cache", None)
    monkeypatch.setenv("TESSERAE_CHANNEL", "edge")
    assert heartbeat.channel() == "edge"
    monkeypatch.setattr(heartbeat, "_channel_cache", None)
    monkeypatch.setenv("TESSERAE_CHANNEL", "nonsense")
    assert heartbeat.channel() in ("stable", "main")  # falls through to detection


def test_channel_source_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESSERAE_CHANNEL", raising=False)
    monkeypatch.setattr(heartbeat, "_channel_cache", None)
    monkeypatch.setattr(heartbeat, "_git_exact_tag", lambda _root: False)
    from app.main import REPO_ROOT

    expected = "main" if (REPO_ROOT / ".git").exists() else "stable"
    assert heartbeat.channel() == expected
    monkeypatch.setattr(heartbeat, "_channel_cache", None)
    monkeypatch.setattr(heartbeat, "_git_exact_tag", lambda _root: True)
    assert heartbeat.channel() == "stable"
    monkeypatch.setattr(heartbeat, "_channel_cache", None)


def test_ota_snapshot_counts_offered_applied_failed(tmp_path: Path, test_install_uuid: Any) -> None:
    devices = [
        _device("esp32_client", "mqtt", device_id="canary"),
        _device("esp32_client", "mqtt", device_id="bystander"),
        _device("pi_bin_client", "rest", device_id="pi"),
    ]
    releases = {
        "esp32_client": {
            "state": "canary",
            "fw_version": "v1.4.0",
            "canary_device_ids": ["canary"],
        },
        "pi_bin_client": {"state": "promoted", "fw_version": "2.0.0"},
    }
    status = {
        "canary": {"parsed": {"fw_version": "1.4.0"}},
        "pi": {"parsed": {"fw_version": "1.9.0"}, "ota": {"phase": "rolled_back"}},
    }
    app, _ = _app(tmp_path, install=test_install_uuid(), devices=devices, status=status)
    app.config["OTA_RELEASE"] = SimpleNamespace(all=lambda: releases)
    p = heartbeat.build_payload(app)  # type: ignore[arg-type]
    # canary (offered + applied) and pi (offered + failed); bystander not offered.
    assert p["ota"] == {"offered": "2-3", "applied": "1", "failed": "1"}


def test_lineups_bucketed(tmp_path: Path, test_install_uuid: Any) -> None:
    app, _ = _app(tmp_path, install=test_install_uuid())
    app.config["DECK_STORE"] = SimpleNamespace(all=lambda: [1, 2, 3, 4, 5])
    assert heartbeat.build_payload(app)["lineups"] == "4-9"  # type: ignore[arg-type]


def test_kick_sends_when_due_and_is_noop_when_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any
) -> None:
    """``kick`` runs off-thread; under pytest it is a no-op (same guard as the
    daemon), so exercise the due gate + send path through ``maybe_send`` and
    the guard through ``kick`` itself."""
    app, _ = _app(tmp_path, install=test_install_uuid())
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(online, "send_heartbeat", lambda payload: sent.append(payload) or True)
    assert heartbeat.is_due(app) is True  # type: ignore[arg-type]
    assert heartbeat.kick(app) is False  # type: ignore[arg-type]  # pytest guard
    assert heartbeat.maybe_send(app) is True  # type: ignore[arg-type]
    assert heartbeat.is_due(app) is False  # type: ignore[arg-type]  # next_due persisted
    assert len(sent) == 1 and sent[0]["channel"] in ("stable", "main", "edge")
