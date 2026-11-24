"""HA add-on: take MQTT credentials from Supervisor when none are entered (#600).

On Home Assistant OS the app defaulted to ``core-mosquitto`` with no username, and the Mosquitto
broker app refuses anonymous clients. Other apps get the broker's credentials from Supervisor's
``services/mqtt`` endpoint. Values entered in the app options still win.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from app import ha_options
from app.ha_options import apply_ha_options
from app.state.settings_store import SettingsStore

SERVICE = {
    "host": "core-mosquitto",
    "port": 1883,
    "ssl": False,
    "protocol": "3.1.1",
    "username": "addons",
    "password": "s3cret",
}


@pytest.fixture
def addon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESSERAE_HA_INGRESS", "1")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")


def _options(tmp_path: Path, **values: Any) -> Path:
    payload = {
        "mqtt_host": "core-mosquitto",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
    }
    payload.update(values)
    path = tmp_path / "options.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _settings(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


def _serve(monkeypatch: pytest.MonkeyPatch, answer: dict[str, Any] | None) -> list[str]:
    calls: list[str] = []

    def _fetch() -> dict[str, Any] | None:
        calls.append("services/mqtt")
        return answer

    monkeypatch.setattr(ha_options, "fetch_supervisor_mqtt", _fetch)
    return calls


@pytest.mark.usefixtures("addon")
def test_blank_credentials_take_the_supervisor_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, SERVICE)
    settings = _settings(tmp_path)
    apply_ha_options(settings, options_path=_options(tmp_path))
    broker = settings.get_section("broker")
    assert broker["host"] == "core-mosquitto"
    assert broker["port"] == 1883
    assert broker["username"] == "addons"
    assert broker["password_secret"] == "s3cret"


@pytest.mark.usefixtures("addon")
def test_entered_credentials_are_used_as_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _serve(monkeypatch, SERVICE)
    settings = _settings(tmp_path)
    apply_ha_options(
        settings, options_path=_options(tmp_path, mqtt_username="me", mqtt_password="mine")
    )
    broker = settings.get_section("broker")
    assert (broker["username"], broker["password_secret"]) == ("me", "mine")
    assert calls == []


@pytest.mark.usefixtures("addon")
def test_another_broker_entered_in_the_options_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, SERVICE)
    settings = _settings(tmp_path)
    apply_ha_options(settings, options_path=_options(tmp_path, mqtt_host="broker.lan"))
    broker = settings.get_section("broker")
    assert broker["host"] == "broker.lan"
    assert broker["username"] == ""


@pytest.mark.usefixtures("addon")
def test_no_mqtt_service_behaves_as_before(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, None)
    settings = _settings(tmp_path)
    apply_ha_options(settings, options_path=_options(tmp_path))
    broker = settings.get_section("broker")
    assert broker["host"] == "core-mosquitto"
    assert broker["username"] == ""


@pytest.mark.usefixtures("addon")
def test_a_tls_service_is_not_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _serve(monkeypatch, {**SERVICE, "ssl": True})
    settings = _settings(tmp_path)
    with caplog.at_level(logging.WARNING, logger="app.ha_options"):
        apply_ha_options(settings, options_path=_options(tmp_path))
    assert settings.get_section("broker")["username"] == ""
    assert any("TLS" in rec.getMessage() for rec in caplog.records)


def test_outside_the_addon_supervisor_is_not_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TESSERAE_HA_INGRESS", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    calls = _serve(monkeypatch, SERVICE)
    apply_ha_options(_settings(tmp_path), options_path=_options(tmp_path))
    assert calls == []


def test_fetch_reads_the_service_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    seen: dict[str, Any] = {}

    class _Resp:
        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"result": "ok", "data": SERVICE}).encode()

    def _urlopen(req: Any, timeout: float) -> _Resp:
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(ha_options.urllib.request, "urlopen", _urlopen)
    assert ha_options.fetch_supervisor_mqtt() == SERVICE
    assert seen == {"url": "http://supervisor/services/mqtt", "auth": "Bearer token"}


def test_fetch_returns_none_when_supervisor_has_no_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")

    def _urlopen(req: Any, timeout: float) -> Any:
        # Supervisor answers an error status when no broker app provides the service.
        raise ha_options.urllib.error.URLError("service not available")

    monkeypatch.setattr(ha_options.urllib.request, "urlopen", _urlopen)
    assert ha_options.fetch_supervisor_mqtt() is None
