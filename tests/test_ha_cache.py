"""Editor-lag fixes: ha_core's short-TTL caches and ha_sensor's parallel
history fetch.

Loads the plugin server modules standalone (same pattern as
test_calendar_core_caldav) and stubs the network layer, so no Flask app
fixture or live HA is involved except a bare app for the ha_sensor
fetch, which reads the plugin registry off current_app.
"""

from __future__ import annotations

import importlib.util
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT


def _load_plugin(name: str) -> Any:
    path = REPO_ROOT / "plugins" / name / "server.py"
    spec = importlib.util.spec_from_file_location(f"_{name}_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch) -> Any:
    mod = _load_plugin("ha_core")
    monkeypatch.setattr(mod, "base_url", lambda: "http://ha.local:8123")
    monkeypatch.setattr(mod, "token", lambda: "tok")
    return mod


# ---------------------------------------------------------------------------
# ha_core.get_states cache


def test_get_states_shares_one_fetch_within_ttl(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        core, "request_json", lambda path, **kw: calls.append(path) or [{"entity_id": "sensor.a"}]
    )
    first = core.get_states()
    second = core.get_states()
    assert first == [{"entity_id": "sensor.a"}]
    assert second is first
    assert calls == ["/api/states"]


def test_get_states_refetches_after_ttl(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(core, "request_json", lambda path, **kw: calls.append(path) or [])
    monkeypatch.setattr(core, "_STATES_TTL_S", 0.0)
    core.get_states()
    core.get_states()
    assert len(calls) == 2


def test_get_states_caches_errors_for_fast_failure(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []

    def boom(path: str, **kw: Any) -> Any:
        calls.append(path)
        raise RuntimeError("down")

    monkeypatch.setattr(core, "request_json", boom)
    with pytest.raises(RuntimeError):
        core.get_states()
    # The 12 sibling dropdown resolvers hitting this must not each pay a
    # network timeout while HA is down.
    with pytest.raises(RuntimeError):
        core.get_states()
    assert len(calls) == 1


def test_get_states_settings_change_invalidates(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(core, "request_json", lambda path, **kw: calls.append(path) or [])
    core.get_states()
    monkeypatch.setattr(core, "base_url", lambda: "http://other.local:8123")
    core.get_states()
    assert len(calls) == 2


class _FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def test_call_service_invalidates_states_cache(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(core, "request_json", lambda path, **kw: calls.append(path) or [])
    core.get_states()
    # A touch action toggles something; the tap-echo re-render must see
    # the post-toggle state, not a dump from a few seconds ago.
    monkeypatch.setattr(core, "_ssl_context", lambda: None)
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda req, **kw: _FakeResp(b"[]"))
    core.call_service("light", "turn_on", data={"entity_id": "light.x"})
    core.get_states()
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# ha_core.history cache


def _history_payload(*states: str) -> list[list[dict[str, Any]]]:
    return [[{"state": s} for s in states]]


def test_history_cached_per_entity(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        core, "request_json", lambda path, **kw: calls.append(path) or _history_payload("1", "2")
    )
    a1 = core.history("sensor.a")
    a2 = core.history("sensor.a")
    core.history("sensor.b")
    assert a1 == a2 == [{"state": "1"}, {"state": "2"}]
    assert len(calls) == 2  # one per distinct entity


def test_history_errors_are_not_cached(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []

    def flaky(path: str, **kw: Any) -> Any:
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("blip")
        return _history_payload("1")

    monkeypatch.setattr(core, "request_json", flaky)
    with pytest.raises(RuntimeError):
        core.history("sensor.a")
    assert core.history("sensor.a") == [{"state": "1"}]
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# ha_sensor parallel history


class _FakeCore:
    def __init__(self) -> None:
        self.history_calls: list[tuple[str, int]] = []
        # Two parties: proves two history fetches were in flight at the
        # same time, without any wall-clock timing assertions.
        self.barrier = threading.Barrier(2, timeout=10)

    def get_states(self) -> list[dict[str, Any]]:
        return [{"entity_id": f"sensor.s{i}", "state": str(i), "attributes": {}} for i in range(4)]

    def history(self, entity_id: str, *, hours: int = 24, timeout: int = 12) -> list:
        self.history_calls.append((entity_id, timeout))
        self.barrier.wait()
        return [{"state": "1.0"}, {"state": "2.0"}]

    @staticmethod
    def friendly_name(state: dict[str, Any]) -> str:
        return str(state.get("entity_id") or "")

    @staticmethod
    def coerce_error(err: Exception) -> str:
        return str(err)


def test_ha_sensor_fetches_history_in_parallel() -> None:
    sensor = _load_plugin("ha_sensor")
    fake = _FakeCore()
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(server_module=fake))
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = registry

    with app.app_context():
        data = sensor.fetch({"entities": [f"sensor.s{i}" for i in range(4)]}, {}, ctx={})

    # If the fetches ran sequentially the barrier would have timed out
    # and raised BrokenBarrierError out of pool.map.
    assert [i["sparkline"] for i in data["items"]] == [[1.0, 2.0]] * 4
    assert {i["trend"] for i in data["items"]} == {"up"}
    assert sorted(eid for eid, _t in fake.history_calls) == [f"sensor.s{i}" for i in range(4)]
    # Every call must use the editor-budget timeout, not ha_core's 12 s.
    assert {t for _eid, t in fake.history_calls} == {sensor._HISTORY_TIMEOUT_S}


def test_ha_sensor_skips_history_for_non_numeric_and_missing() -> None:
    sensor = _load_plugin("ha_sensor")
    fake = _FakeCore()

    def states() -> list[dict[str, Any]]:
        return [
            {"entity_id": "sensor.text", "state": "on", "attributes": {}},
            {"entity_id": "sensor.gone", "state": "unavailable", "attributes": {}},
        ]

    fake.get_states = states  # type: ignore[method-assign]
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(server_module=fake))
    app = Flask(__name__)
    app.config["PLUGIN_REGISTRY"] = registry

    with app.app_context():
        data = sensor.fetch({"entities": ["sensor.text", "sensor.gone"]}, {}, ctx={})

    assert fake.history_calls == []
    assert [i["name"] for i in data["items"]] == ["sensor.text", "sensor.gone"]
