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
    assert isinstance(body, dict)
    assert body["eventName"] == "app.started"
    assert body["sessionId"] == t.instance_id
    assert body["systemProps"]["appVersion"] == "0.3.0"
    assert body["systemProps"]["osName"]  # something non-empty
    assert body["props"] == {}
    assert "timestamp" in body


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
