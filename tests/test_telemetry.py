"""Telemetry: instance-id, env-var off switch, disabled-state guarantees,
and event payload format (urlopen mocked — no network in tests)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest

from app import telemetry as tm
from app.state.event_log import EventLog

# ----- instance id ----------------------------------------------------


def test_instance_id_is_stable_across_calls(tmp_path: Path) -> None:
    a = tm._ensure_instance_id(tmp_path)
    b = tm._ensure_instance_id(tmp_path)
    assert a == b
    # 36 chars = canonical UUID4 representation.
    assert len(a) == 36 and a.count("-") == 4


def test_instance_id_persists_on_disk(tmp_path: Path) -> None:
    a = tm._ensure_instance_id(tmp_path)
    assert (tmp_path / tm.INSTANCE_ID_FILE).read_text(encoding="utf-8").strip() == a


# ----- config resolution ----------------------------------------------


def test_disabled_when_baked_constants_are_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tesserae ships with the constants empty until the maintainer fills
    them in — even a user toggling the setting on can't send."""
    monkeypatch.setattr(tm, "APTABASE_HOST", "")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_APP_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path, app_version="0.3.0", settings_app={"telemetry_enabled": True}
    )
    assert t.enabled is False


def test_baked_constants_are_used_when_no_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://baked.example")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "BAKEDKEY")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_APP_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path, app_version="0.3.0", settings_app={"telemetry_enabled": True}
    )
    assert t.enabled is True
    assert t.endpoint == "https://baked.example/api/v0/event"
    t.shutdown(timeout=1.0)


def test_settings_cannot_override_baked_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a malicious settings file injects telemetry_host/app_key,
    they're ignored — the endpoint is baked."""
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://baked.example")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "BAKEDKEY")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_APP_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.3.0",
        settings_app={
            "telemetry_enabled": True,
            "telemetry_host": "https://evil.example",
            "telemetry_app_key": "EVIL",
        },
    )
    assert t.endpoint == "https://baked.example/api/v0/event"
    t.shutdown(timeout=1.0)


def test_env_off_overrides_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://baked.example")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "BAKEDKEY")
    monkeypatch.setenv("TESSERAE_TELEMETRY", "0")
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.3.0",
        settings_app={"telemetry_enabled": True},
    )
    assert t.enabled is False


def test_env_vars_override_baked_constants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dev convenience for the maintainer: env vars point a local
    Tesserae at a staging Aptabase without committing the values."""
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://prod.example")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "PROD")
    monkeypatch.setenv("TESSERAE_TELEMETRY_HOST", "https://staging.example")
    monkeypatch.setenv("TESSERAE_TELEMETRY_APP_KEY", "STAGING")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path, app_version="0.3.0", settings_app={"telemetry_enabled": True}
    )
    assert t.enabled is True
    assert t.endpoint == "https://staging.example/api/v0/event"
    t.shutdown(timeout=1.0)


def test_disabled_instance_never_starts_worker(tmp_path: Path) -> None:
    t = tm.Telemetry.disabled()
    assert t.enabled is False
    # send() is a no-op; nothing queued.
    t.send("app.started")
    assert t._queue.qsize() == 0
    # No worker thread spun up.
    assert t._thread is None


# ----- send / payload (mocked HTTP) -----------------------------------


class _FakeResp:
    def __init__(self, data: bytes = b"OK") -> None:
        self._buf = BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def _make_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tm.Telemetry:
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_APP_KEY", raising=False)
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://analytics.example.com")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "AK-1234")
    return tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.3.0",
        settings_app={"telemetry_enabled": True},
    )


def _drain(t: tm.Telemetry, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and t._queue.qsize() > 0:
        time.sleep(0.02)


def test_send_posts_aptabase_shaped_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = json.loads(req.data or b"{}")
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)

    t = _make_enabled(tmp_path, monkeypatch)
    try:
        t.send("app.started", {})
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)

    assert captured["url"] == "https://analytics.example.com/api/v0/event"
    headers = {k.lower(): v for k, v in captured["headers"].items()}  # type: ignore[union-attr]
    assert headers.get("App-key".lower()) == "AK-1234"
    assert headers.get("content-type") == "application/json"

    body = captured["body"]
    # /api/v0/event takes a bare object (see JS SDK), NOT an array.
    assert isinstance(body, dict)
    assert body["eventName"] == "app.started"
    assert body["sessionId"] == t.instance_id
    assert body["systemProps"]["appVersion"] == "0.3.0"
    assert body["systemProps"]["isDebug"] is False
    assert body["props"] == {}
    assert "timestamp" in body
    # JS-SDK-style User-Agent header — server rejects/silently drops
    # plain "tesserae/..." UA strings on some deployments.
    headers = {k.lower(): v for k, v in captured["headers"].items()}  # type: ignore[union-attr]
    ua = headers.get("user-agent", "")
    assert "Mozilla/5.0" in ua and "Not-A-Browser" in ua


def test_system_props_match_aptabase_strict_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aptabase 400s on a string ``isDebug`` or a slash in ``sdkVersion``.
    Lock the shape down so a future tidy-up can't regress it."""
    captured: dict[str, object] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        captured["body"] = json.loads(req.data or b"{}")
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        t.send("app.started", {})
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    body = captured["body"]
    assert isinstance(body, dict)
    sys_props = body["systemProps"]
    assert sys_props["isDebug"] is False, "Aptabase requires bool, not 'false'"
    assert "@" in sys_props["sdkVersion"], "Aptabase requires name@version, not name/version"
    assert "/" not in sys_props["sdkVersion"]
    # JS-SDK minimal field set: nothing beyond these four — server-side
    # validators on some self-hosted deployments reject extras.
    assert set(sys_props.keys()) == {"locale", "isDebug", "appVersion", "sdkVersion"}


def test_failure_silent_when_endpoint_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del req, timeout
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", boom)
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        t.send("app.started")
        _drain(t)  # worker swallows and continues
    finally:
        t.shutdown(timeout=1.0)
    # No exception propagated; queue drained.
    assert t._queue.qsize() == 0


# ----- runtime enable + synchronous test_send -----------------------


def test_set_enabled_off_to_on_starts_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Telemetry created disabled (because settings said off) must be
    able to come alive when the user flips the toggle, without a
    restart."""
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://baked.example")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "BAKEDKEY")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_APP_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.4.1",
        settings_app={"telemetry_enabled": False},
    )
    assert t.enabled is False
    assert t._thread is None

    t.set_enabled(True)
    try:
        assert t.enabled is True
        assert t._thread is not None and t._thread.is_alive()
    finally:
        t.shutdown(timeout=1.0)


def test_set_enabled_on_to_off_stops_sending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        assert t.enabled is True
        t.set_enabled(False)
        assert t.enabled is False
        # send() is gated by enabled and never queues when off.
        t.send("app.started")
        assert t._queue.qsize() == 0
    finally:
        t.shutdown(timeout=1.0)


def test_test_send_returns_none_on_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        assert t.test_send() is None
    finally:
        t.shutdown(timeout=1.0)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_test_send_returns_error_string_on_http_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # urllib.error.HTTPError's internal fp uses a _TemporaryFileCloser
    # whose finalizer fires after the test on Python 3.14 — pytest
    # surfaces that as PytestUnraisableExceptionWarning. The exception
    # itself is irrelevant to what we're testing here, so we ignore it.
    def http_error(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del req, timeout
        raise urllib.error.HTTPError(
            url="https://x/api/v0/event",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", http_error)
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        err = t.test_send()
    finally:
        t.shutdown(timeout=1.0)
    assert err is not None
    assert "401" in err and "Unauthorized" in err


def test_test_send_returns_error_string_on_connection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del req, timeout
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", refused)
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        err = t.test_send()
    finally:
        t.shutdown(timeout=1.0)
    assert err is not None and "connection refused" in err


def test_test_send_when_disabled_returns_short_message(tmp_path: Path) -> None:
    t = tm.Telemetry.disabled()
    err = t.test_send()
    assert err is not None and "disabled" in err.lower()


# ----- event log wiring ---------------------------------------------


def _make_enabled_with_event_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[tm.Telemetry, EventLog]:
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_APP_KEY", raising=False)
    monkeypatch.setattr(tm, "APTABASE_HOST", "https://analytics.example.com")
    monkeypatch.setattr(tm, "APTABASE_APP_KEY", "AK-1234")
    log = EventLog(tmp_path / "events.db")
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.4.2",
        settings_app={"telemetry_enabled": True},
        event_log=log,
    )
    return t, log


def test_test_send_records_success_row_in_event_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())
    t, log = _make_enabled_with_event_log(tmp_path, monkeypatch)
    try:
        assert t.test_send() is None
    finally:
        t.shutdown(timeout=1.0)
    rows = log.list(type="telemetry", limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "app.started"
    assert row.status == "sent"
    assert row.error is None
    assert row.target == "https://analytics.example.com"
    # The exact payload is exposed in ``extra`` so the Events tab can
    # surface what we shipped — this is what made the v0.4.2 -> v0.4.3
    # 400-debugging session quick (the body shape was visible).
    payload = row.extra["payload"]
    assert isinstance(payload, dict)
    assert payload["eventName"] == "app.started"
    assert payload["sessionId"] == t.instance_id
    assert payload["systemProps"]["isDebug"] is False
    assert "@" in payload["systemProps"]["sdkVersion"]
    assert row.extra["endpoint"].endswith("/api/v0/event")


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
def test_test_send_records_failure_row_in_event_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def http_error(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del req, timeout
        raise urllib.error.HTTPError(
            url="https://x/api/v0/event",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", http_error)
    t, log = _make_enabled_with_event_log(tmp_path, monkeypatch)
    try:
        err = t.test_send()
    finally:
        t.shutdown(timeout=1.0)
    assert err is not None
    rows = log.list(type="telemetry", limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "failed"
    assert row.error is not None and "401" in row.error


def test_async_send_records_row_via_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Background ``send()`` path must also log a telemetry row when it
    runs (otherwise the Events tab would only ever see test_send rows)."""
    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())
    t, log = _make_enabled_with_event_log(tmp_path, monkeypatch)
    try:
        t.send("update.applied", {"from": "deadbee", "to": "cafef00"})
        _drain(t)
        # Give the worker a beat to call _post + record after dequeue.
        time.sleep(0.1)
    finally:
        t.shutdown(timeout=1.0)
    rows = log.list(type="telemetry", limit=10)
    assert any(r.source == "update.applied" and r.status == "sent" for r in rows)


def test_disabled_send_makes_no_http_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        calls.append(req)
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    t = tm.Telemetry.disabled()
    t.send("app.started")
    time.sleep(0.05)
    assert calls == []
