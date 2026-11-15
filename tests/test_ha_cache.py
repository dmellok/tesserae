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


def test_history_requests_the_full_window(core: Any, monkeypatch: pytest.MonkeyPatch):
    # HA ends the period a day after the start unless end_time is given,
    # so a 48 h request used to come back as only its oldest 24 h (#339).
    from datetime import UTC, datetime, timedelta
    from urllib.parse import parse_qs, unquote, urlsplit

    calls = []
    monkeypatch.setattr(
        core, "request_json", lambda path, **kw: calls.append(path) or _history_payload("1")
    )
    core.history("sensor.solar", hours=48)
    parts = urlsplit(calls[0])
    start = datetime.fromisoformat(unquote(parts.path.rsplit("/", 1)[1]))
    end = datetime.fromisoformat(parse_qs(parts.query)["end_time"][0])
    assert abs(end - datetime.now(UTC)) < timedelta(minutes=1)
    assert end - start == timedelta(hours=48)


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


# -- saying why HA can't be used (#258 follow-up) -------------------------


def _app_with(settings: dict[str, Any], unreadable: set[str] | None = None) -> Flask:
    """A bare app whose settings store answers the two questions the error
    helper asks: what's configured, and which secrets won't decrypt."""
    app = Flask(__name__)
    app.config["SETTINGS_STORE"] = SimpleNamespace(
        get_section=lambda name: {"ha_core": settings} if name == "plugins" else {},
        unreadable_secrets=lambda ns, item=None: set(unreadable or set()),
    )
    return app


def test_an_unreadable_token_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The message an operator actually sees on every widget. "Not
    configured" sends them to set up something they already set up; the
    truth is that the stored token stopped decrypting when the encryption
    key changed, and re-entering it is the fix."""
    core = _load_plugin("ha_core")
    app = _app_with({"base_url": "http://ha.local:8123", "token_secret": ""}, {"token_secret"})
    with app.app_context(), pytest.raises(RuntimeError) as err:
        core.get_states()
    assert "can no longer be decrypted" in str(err.value)
    assert "re-enter it" in str(err.value).lower()


def test_a_missing_token_is_distinguished_from_no_setup() -> None:
    core = _load_plugin("ha_core")
    with (
        _app_with({"base_url": "http://ha.local:8123"}).app_context(),
        pytest.raises(RuntimeError) as err,
    ):
        core.get_states()
    assert "access token is missing" in str(err.value)

    with _app_with({}).app_context(), pytest.raises(RuntimeError) as err:
        core.get_states()
    assert str(err.value) == "Home Assistant is not configured"


def test_the_helper_survives_a_store_that_cannot_answer() -> None:
    """An older store, or one that raises, must not turn a configuration
    error into a crash inside a widget render."""
    core = _load_plugin("ha_core")
    app = Flask(__name__)
    app.config["SETTINGS_STORE"] = SimpleNamespace(
        get_section=lambda name: {"ha_core": {}},
        unreadable_secrets=lambda ns, item=None: (_ for _ in ()).throw(AttributeError("old")),
    )
    with app.app_context(), pytest.raises(RuntimeError) as err:
        core.get_states()
    assert "not configured" in str(err.value)


# ---------------------------------------------------------------------------
# ha_core.theme_names / theme_choices


def _themes_result(*names: str) -> dict[str, Any]:
    return {
        "success": True,
        "result": {"themes": {n: {} for n in names}, "default_theme": "default"},
    }


def test_theme_choices_lead_with_the_default_and_sort_by_name(
    core: Any, monkeypatch: pytest.MonkeyPatch
):
    sent = []
    monkeypatch.setattr(core, "is_configured", lambda: True)
    monkeypatch.setattr(
        core,
        "ws_command",
        lambda command, **kw: sent.append(command) or _themes_result("paper", "Graphite"),
    )
    assert core.theme_choices() == [
        {"value": "", "label": "Home Assistant default"},
        {"value": "Graphite", "label": "Graphite"},
        {"value": "paper", "label": "paper"},
    ]
    assert sent == [{"type": "frontend/get_themes"}]


def test_theme_names_share_one_request_within_ttl(core: Any, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr(
        core, "ws_command", lambda command, **kw: calls.append(command) or _themes_result("Ink")
    )
    assert core.theme_names() == ["Ink"]
    assert core.theme_names() == ["Ink"]
    assert len(calls) == 1


def test_theme_failure_is_cached_and_shown_as_unreachable(
    core: Any, monkeypatch: pytest.MonkeyPatch
):
    calls = []

    def boom(command: dict[str, Any], **kw: Any) -> dict[str, Any]:
        calls.append(command)
        raise TimeoutError("no answer")

    monkeypatch.setattr(core, "is_configured", lambda: True)
    monkeypatch.setattr(core, "ws_command", boom)
    first = core.theme_choices()
    assert first == core.theme_choices()
    assert len(first) == 1 and first[0]["label"].startswith("Home Assistant unreachable")
    assert len(calls) == 1


def test_theme_choices_explain_a_missing_config(core: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core, "is_configured", lambda: False)
    rows = core.theme_choices()
    assert len(rows) == 1 and rows[0]["label"].startswith("Set HA URL + token")


# ---------------------------------------------------------------------------
# ha_core.ws_command, against a real local socket


@pytest.fixture
def ha_socket(core: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A stand-in for HA's WebSocket API: the auth handshake, then one
    scripted answer per command type. Yields the dict of answers to fill
    and the list of commands it received."""
    import json

    from websockets.sync.server import serve

    answers: dict[str, dict[str, Any]] = {}
    received: list[dict[str, Any]] = []

    def handler(ws: Any) -> None:
        ws.send(json.dumps({"type": "auth_required"}))
        auth = json.loads(ws.recv())
        if auth.get("access_token") != "tok":
            ws.send(json.dumps({"type": "auth_invalid"}))
            return
        ws.send(json.dumps({"type": "auth_ok"}))
        command = json.loads(ws.recv())
        received.append(command)
        # An event for another subscription first: the caller must skip it.
        ws.send(json.dumps({"id": 99, "type": "event"}))
        ws.send(json.dumps({"id": command["id"], "type": "result", **answers[command["type"]]}))

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(core, "base_url", lambda: f"http://127.0.0.1:{port}")
        monkeypatch.setattr(core, "is_configured", lambda: True)
        yield SimpleNamespace(answers=answers, received=received)
        server.shutdown()
        thread.join(timeout=2)


def test_ws_command_authenticates_and_returns_the_result(core: Any, ha_socket: Any):
    ha_socket.answers["frontend/get_themes"] = _themes_result("Ink", "Graphite")
    assert core.theme_names() == ["Graphite", "Ink"]
    assert ha_socket.received == [{"type": "frontend/get_themes", "id": 1}]


def test_ws_command_reports_a_refused_token(
    core: Any, ha_socket: Any, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(core, "token", lambda: "wrong")
    with pytest.raises(RuntimeError, match="refused the access token"):
        core.ws_command({"type": "frontend/get_themes"}, timeout=2)


def test_remove_media_treats_removed_and_already_gone_as_done(core: Any, ha_socket: Any):
    kind = "media_source/local_source/remove"
    ha_socket.answers[kind] = {"success": True, "result": None}
    assert core.remove_media("media-source://media_source/local/a.png", timeout=2) is True
    assert ha_socket.received[0]["media_content_id"] == "media-source://media_source/local/a.png"
    ha_socket.answers[kind] = {"success": False, "error": {"code": "not_found"}}
    assert core.remove_media("x", timeout=2) is True
    ha_socket.answers[kind] = {"success": False, "error": {"code": "unauthorized", "message": "no"}}
    with pytest.raises(RuntimeError, match="would not remove the file: no"):
        core.remove_media("x", timeout=2)
