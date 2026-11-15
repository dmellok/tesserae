"""Wikipedia On This Day picks its date in the configured app timezone, not UTC."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask


def _freeze_datetime(instant: datetime) -> type[datetime]:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            if tz is None:
                return instant.replace(tzinfo=None)
            return instant.astimezone(tz)

    return FrozenDateTime


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers: dict[str, str] = {}

    def read(self, *_a: Any) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def _fetch_at(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, instant: datetime
) -> tuple[dict[str, Any], list[str]]:
    plugin = app.config["PLUGIN_REGISTRY"].get("news_wikipedia_otd")
    assert plugin is not None and plugin.server_module is not None
    mod = plugin.server_module
    urls: list[str] = []

    def urlopen(req: Any, timeout: float = 0) -> _Resp:
        del timeout
        urls.append(req.full_url)
        return _Resp(json.dumps({"events": [{"year": 1999, "text": "x", "pages": []}]}).encode())

    monkeypatch.setattr(mod, "datetime", _freeze_datetime(instant))
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    with app.app_context():
        out = mod.fetch({"type": "events"}, {}, ctx={"data_dir": str(tmp_path)})
    return out, urls


def test_phoenix_evening_still_shows_local_date(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "America/Phoenix"})
    # 17:00 in Phoenix (UTC-7, no DST) on 14 July is 00:00 UTC on 15 July.
    out, urls = _fetch_at(app, monkeypatch, tmp_path, datetime(2026, 7, 15, 0, 0, tzinfo=UTC))
    assert out["date"] == "14 July"
    assert urls == ["https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/events/07/14"]
    assert (tmp_path / "otd_07_14_events_6.json").exists()


def test_phoenix_after_local_midnight_moves_on(
    app: Flask, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app.config["SETTINGS_STORE"].update_section("app", {"timezone": "America/Phoenix"})
    # 00:00 in Phoenix on 15 July is 07:00 UTC.
    out, urls = _fetch_at(app, monkeypatch, tmp_path, datetime(2026, 7, 15, 7, 0, tzinfo=UTC))
    assert out["date"] == "15 July"
    assert urls[0].endswith("/events/07/15")
