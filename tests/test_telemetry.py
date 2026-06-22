"""Telemetry: instance-id, env-var off switch, disabled-state guarantees,
and event payload format (urlopen mocked, no network in tests)."""

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
    them in, even a user toggling the setting on can't send."""
    monkeypatch.setattr(tm, "POSTHOG_HOST", "")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path, app_version="0.3.0", settings_app={"telemetry_enabled": True}
    )
    assert t.enabled is False


def test_baked_constants_are_used_when_no_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://baked.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_baked")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path, app_version="0.3.0", settings_app={"telemetry_enabled": True}
    )
    assert t.enabled is True
    assert t.endpoint == "https://baked.posthog.example/i/v0/e/"
    t.shutdown(timeout=1.0)


def test_settings_cannot_override_baked_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even if a malicious settings file injects telemetry_host /
    project_key, they're ignored — the endpoint is baked."""
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://baked.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_baked")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.3.0",
        settings_app={
            "telemetry_enabled": True,
            "telemetry_host": "https://evil.example",
            "telemetry_project_key": "phc_evil",
        },
    )
    assert t.endpoint == "https://baked.posthog.example/i/v0/e/"
    t.shutdown(timeout=1.0)


def test_env_off_overrides_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://baked.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_baked")
    monkeypatch.setenv("TESSERAE_TELEMETRY", "0")
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.3.0",
        settings_app={"telemetry_enabled": True},
    )
    assert t.enabled is False


def test_env_vars_override_baked_constants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A dev convenience for the maintainer: env vars point a local
    Tesserae at a staging PostHog project without committing the values."""
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://prod.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_prod")
    monkeypatch.setenv("TESSERAE_TELEMETRY_HOST", "https://staging.posthog.example")
    monkeypatch.setenv("TESSERAE_TELEMETRY_PROJECT_KEY", "phc_staging")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    t = tm.Telemetry.from_settings(
        data_root=tmp_path, app_version="0.3.0", settings_app={"telemetry_enabled": True}
    )
    assert t.enabled is True
    assert t.endpoint == "https://staging.posthog.example/i/v0/e/"
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
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://test.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_test_1234")
    return tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.3.0",
        settings_app={"telemetry_enabled": True},
    )


def _drain(t: tm.Telemetry, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and t._queue.qsize() > 0:
        time.sleep(0.02)


def test_send_posts_posthog_shaped_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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

    # PostHog's server-side capture endpoint.
    assert captured["url"] == "https://test.posthog.example/i/v0/e/"
    headers = {k.lower(): v for k, v in captured["headers"].items()}  # type: ignore[union-attr]
    # Project key is sent both ways — body field for routing, header
    # for proxies that want bearer-auth.
    assert headers.get("authorization") == "Bearer phc_test_1234"
    assert headers.get("content-type") == "application/json"
    # PostHog SDKs use a simple UA; no need for the Aptabase-era
    # Mozilla shim.
    assert "tesserae-telemetry" in headers.get("user-agent", "")

    body = captured["body"]
    assert isinstance(body, dict)
    # PostHog's top-level fields.
    assert body["api_key"] == "phc_test_1234"
    assert body["event"] == "app.started"
    assert body["distinct_id"] == t.instance_id
    assert "timestamp" in body
    # Properties block carries the platform + privacy + tesserae fields.
    props = body["properties"]
    assert props["version"] == "0.3.0"
    assert props["$lib"] == "posthog-tesserae"
    assert props["$lib_version"] == "0.3.0"
    assert props["is_debug"] is False


def test_timezone_props_present_when_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the user has a real IANA timezone configured (or the host
    OS auto-detect finds one), every event carries ``timezone`` and
    ``timezone_region`` properties so the maintainer can answer
    "where is Tesserae running" without doing IP geolocation.
    Region is derived from the first segment of the IANA name."""
    captured: dict[str, object] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        captured["body"] = json.loads(req.data or b"{}")
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://test.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_test_1234")
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.64.4",
        settings_app={
            "telemetry_enabled": True,
            "timezone": "Australia/Melbourne",
        },
    )
    try:
        t.send("app.started", {})
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    body = captured["body"]
    assert isinstance(body, dict)
    props = body["properties"]
    assert props["timezone"] == "Australia/Melbourne"
    assert props["timezone_region"] == "Australia"


def test_timezone_props_omitted_when_no_iana_name_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the stored timezone is empty AND the system auto-detect
    can't find an IANA name (no ``TZ`` env, no readable
    ``/etc/localtime``), the timezone props are *omitted* from the
    event instead of shipping junk like ``UTC`` or ``C`` that would
    collapse every Docker install into one bucket."""
    captured: dict[str, object] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        captured["body"] = json.loads(req.data or b"{}")
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://test.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_test_1234")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    # Force ``_resolve_iana_timezone`` down its empty-string branch by
    # stubbing the symlink read too.
    monkeypatch.setattr("app.telemetry.os.readlink", lambda _path: (_ for _ in ()).throw(OSError()))
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.64.4",
        settings_app={"telemetry_enabled": True, "timezone": ""},
    )
    try:
        t.send("app.started", {})
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    body = captured["body"]
    assert isinstance(body, dict)
    props = body["properties"]
    assert "timezone" not in props
    assert "timezone_region" not in props


def test_resolve_iana_timezone_validates_against_zoneinfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale or hand-edited settings file with a bogus IANA name
    (``Foo/Bar``) falls through to system auto-detect instead of
    shipping the bogus value. Real names round-trip."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("app.telemetry.os.readlink", lambda _path: (_ for _ in ()).throw(OSError()))
    assert tm._resolve_iana_timezone("Foo/Bar") == ""
    assert tm._resolve_iana_timezone("Australia/Melbourne") == "Australia/Melbourne"
    monkeypatch.setenv("TZ", "Europe/London")
    assert tm._resolve_iana_timezone("system") == "Europe/London"


def test_privacy_props_present_on_every_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every event must carry the PostHog privacy kill-switch
    ``$process_person_profile: false`` so a future SDK default-flip
    can't quietly start creating person profiles for the install UUID.

    ``$geoip_disable`` is deliberately NOT set — the maintainer wants
    country / region columns and PostHog derives them from the
    request IP at ingestion before dropping it. IP suppression is NOT
    done at the SDK layer; it's a project-level setting (Discard
    client IP data), so ``$ip`` is intentionally absent from the
    payload."""
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
    props = body["properties"]
    assert props["$process_person_profile"] is False
    # $ip explicitly NOT sent — see docstring; suppression is project-level.
    assert "$ip" not in props
    # $geoip_disable explicitly not set — geo enrichment is wanted.
    assert "$geoip_disable" not in props


def test_heartbeat_fires_app_heartbeat_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The heartbeat thread should send an ``app.heartbeat`` after each
    HEARTBEAT_INTERVAL_S window. We shorten the interval to a few ms so
    the test isn't slow."""
    captured_names: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        body = json.loads(req.data or b"{}")
        captured_names.append(body.get("event", ""))
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(tm, "HEARTBEAT_INTERVAL_S", 0.05)
    t = _make_enabled(tmp_path, monkeypatch)
    try:
        # Give the heartbeat loop room for at least two ticks.
        time.sleep(0.18)
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    assert captured_names.count("app.heartbeat") >= 2


def test_heartbeat_carries_props_from_registered_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once a provider is registered, the heartbeat folds its dict into
    the event's ``properties``. The maintainer's PostHog view sees fleet
    shape + activity counters under the same event name."""
    captured: list[dict[str, object]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        body = json.loads(req.data or b"{}")
        if body.get("event") == "app.heartbeat":
            captured.append(body.get("properties") or {})
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(tm, "HEARTBEAT_INTERVAL_S", 0.05)
    t = _make_enabled(tmp_path, monkeypatch)
    t.set_heartbeat_props_provider(lambda: {"n_devices": "3", "device_kinds": "esp32_client"})
    try:
        time.sleep(0.18)
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    assert any(p.get("n_devices") == "3" for p in captured)
    assert any(p.get("device_kinds") == "esp32_client" for p in captured)


def test_heartbeat_survives_provider_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that raises must not silence the heartbeat, the event
    still fires with empty props so the maintainer's liveness signal
    keeps working even when the props provider is broken."""
    captured_names: list[str] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        body = json.loads(req.data or b"{}")
        captured_names.append(body.get("event", ""))
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(tm, "HEARTBEAT_INTERVAL_S", 0.05)
    t = _make_enabled(tmp_path, monkeypatch)

    def boom() -> dict[str, str]:
        raise RuntimeError("provider broke")

    t.set_heartbeat_props_provider(boom)
    try:
        time.sleep(0.18)
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    assert captured_names.count("app.heartbeat") >= 2


def test_heartbeat_stops_on_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``shutdown()`` must cancel the heartbeat thread cleanly, without
    this an idle Tesserae process would prevent a clean test teardown."""
    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", lambda req, timeout=0: _FakeResp())
    monkeypatch.setattr(tm, "HEARTBEAT_INTERVAL_S", 0.05)
    t = _make_enabled(tmp_path, monkeypatch)
    assert t._heartbeat_thread is not None and t._heartbeat_thread.is_alive()
    t.shutdown(timeout=1.0)
    assert not t._heartbeat_thread.is_alive()


def test_is_debug_flag_threads_through_to_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--dev`` should mark events as debug traffic so the maintainer's
    PostHog dashboard can separate dev sessions from prod via a property
    filter on ``is_debug = false``."""
    captured: dict[str, object] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _FakeResp:
        del timeout
        captured["body"] = json.loads(req.data or b"{}")
        return _FakeResp()

    monkeypatch.setattr("app.telemetry.urllib.request.urlopen", fake_urlopen)
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://test.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_test_1234")
    t = tm.Telemetry.from_settings(
        data_root=tmp_path,
        app_version="0.4.6",
        settings_app={"telemetry_enabled": True},
        is_debug=True,
    )
    try:
        t.send("app.started", {})
        _drain(t)
    finally:
        t.shutdown(timeout=1.0)
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["properties"]["is_debug"] is True


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
    monkeypatch.setattr(tm, "POSTHOG_HOST", "https://baked.posthog.example")
    monkeypatch.setattr(tm, "POSTHOG_PROJECT_KEY", "phc_baked")
    monkeypatch.delenv("TESSERAE_TELEMETRY", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_HOST", raising=False)
    monkeypatch.delenv("TESSERAE_TELEMETRY_PROJECT_KEY", raising=False)
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
