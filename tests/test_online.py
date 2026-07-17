"""Unit tests for app.online: the master online-features switch and the
best-effort api.tesserae.ink calls."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app import online


class _Store:
    def __init__(self, section: dict[str, Any]) -> None:
        self._section = section

    def get_section(self, name: str) -> dict[str, Any]:
        return self._section if name == "app" else {}


def test_online_enabled_default_off() -> None:
    # A fresh install (no keys) is offline until the user opts in (opt-in model).
    assert online.online_enabled(_Store({})) is False


def test_online_enabled_explicit_off() -> None:
    assert online.online_enabled(_Store({"online_features": False})) is False
    assert online.online_enabled(_Store({"online_features": "off"})) is False


def test_online_enabled_legacy_firmware_optout_is_honoured() -> None:
    # Someone who explicitly disabled the old firmware lookup stays opted out.
    assert online.online_enabled(_Store({"check_firmware_updates": False})) is False
    # Explicit legacy on (no master key) -> enabled.
    assert online.online_enabled(_Store({"check_firmware_updates": True})) is True
    # The master key always wins over the legacy one.
    assert (
        online.online_enabled(_Store({"online_features": True, "check_firmware_updates": False}))
        is True
    )


def test_online_enabled_none_store() -> None:
    assert online.online_enabled(None) is False


def test_online_enabled_false_in_ephemeral_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Precise dev/CI markers never phone home, even with the switch on.
    for var, val in (
        ("GITHUB_ACTIONS", "true"),
        ("CODESPACES", "true"),
        ("GITPOD_WORKSPACE_ID", "ws-1"),
    ):
        monkeypatch.setenv(var, val)
        assert online.online_enabled(_Store({})) is False
        assert online.online_enabled(_Store({"online_features": True})) is False
        monkeypatch.delenv(var, raising=False)


def test_online_enabled_not_blocked_by_generic_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    # An opted-in install that happens to carry CI=true (leaked from a pipeline
    # or a base image) must still phone home; only precise markers gate.
    monkeypatch.setenv("CI", "true")
    assert online.online_enabled(_Store({"online_features": True})) is True


class _FakeResp:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


def test_report_widget_install_posts_expected_payload(
    monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> _FakeResp:
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return _FakeResp(204)

    uid = test_install_uuid()
    monkeypatch.setattr(online.urllib.request, "urlopen", fake_urlopen)
    ok = online.report_widget_install("spotify", uid, "0.93.0", api_base="https://x.test")
    assert ok is True
    assert captured["url"] == "https://x.test/widgets/install"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {
        "widget": "spotify",
        "install": uid,
        "version": "0.93.0",
    }


def test_latest_version_parses_channel_response(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    body = json.dumps(
        {
            "channel": "stable",
            "current": "0.130.0",
            "latest": {"version": "0.140.0", "url": "https://gh/rel"},
            "is_current": False,
            "versions_behind": 3,
        }
    ).encode()

    def fake_urlopen(req: Any, timeout: float) -> _FakeResp:
        captured["url"] = req.full_url
        return _FakeResp(200, body)

    monkeypatch.setattr(online.urllib.request, "urlopen", fake_urlopen)
    out = online.latest_version("stable", "0.130.0", "abc", api_base="https://x.test")
    assert out is not None
    assert "channel=stable" in captured["url"]
    assert "current=0.130.0" in captured["url"] and "install=abc" in captured["url"]
    assert out["latest"]["version"] == "0.140.0" and out["versions_behind"] == 3


def test_latest_version_best_effort_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: Any, timeout: float) -> _FakeResp:
        raise OSError("offline")

    monkeypatch.setattr(online.urllib.request, "urlopen", boom)
    assert online.latest_version("stable", "0.1.0", api_base="https://x.test") is None


def test_report_widget_install_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: Any, timeout: float) -> _FakeResp:
        raise OSError("network down")

    monkeypatch.setattr(online.urllib.request, "urlopen", boom)
    assert online.report_widget_install("spotify", "u", "0", api_base="https://x.test") is False
    # Empty widget id never calls out.
    assert online.report_widget_install("", "u", "0") is False


def test_send_heartbeat_posts(monkeypatch: pytest.MonkeyPatch, test_install_uuid: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float) -> _FakeResp:
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return _FakeResp(204)

    uid = test_install_uuid()
    monkeypatch.setattr(online.urllib.request, "urlopen", fake_urlopen)
    ok = online.send_heartbeat({"install": uid, "version": "1"}, api_base="https://x.test")
    assert ok is True
    assert captured["url"] == "https://x.test/heartbeat"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"])["install"] == uid


def test_send_heartbeat_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(req: Any, timeout: float) -> _FakeResp:
        raise OSError("down")

    monkeypatch.setattr(online.urllib.request, "urlopen", boom)
    assert online.send_heartbeat({"install": "u"}, api_base="https://x.test") is False


def test_widget_install_counts_parses_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    online.clear_counts_cache()
    body = json.dumps({"counts": {"spotify": 42, "weather_now": 7, "bad": "x", "b": True}}).encode()
    monkeypatch.setattr(online.urllib.request, "urlopen", lambda req, timeout: _FakeResp(200, body))
    counts = online.widget_install_counts(api_base="https://x.test", ttl=0)
    assert counts == {"spotify": 42, "weather_now": 7}


def test_widget_install_counts_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    online.clear_counts_cache()

    def boom(req: Any, timeout: float) -> _FakeResp:
        raise OSError("down")

    monkeypatch.setattr(online.urllib.request, "urlopen", boom)
    assert online.widget_install_counts(api_base="https://x.test", ttl=0) == {}
