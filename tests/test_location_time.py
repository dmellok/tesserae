"""Widgets take "now" in the weather location's zone, not the server's (#351).

The reporter's server runs in Europe/Berlin with widgets set to
Melbourne. Open-Meteo's ``timezone=auto`` already returns sunrise /
sunset / daily dates in Melbourne time, so "now" has to be Melbourne
time too. These tests pin the server zone to Berlin and freeze the
instant so the location-zone answer and the server-zone answer differ.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app import composer
from app import location_time as lt
from app.location_time import is_iana_zone, location_now, payload_now
from app.page_routes import _coerce_cell_option
from plugins.clock_sunrise_sunset import server as sun_server
from plugins.weather_forecast import server as forecast_server
from plugins.weather_now import server as now_server
from plugins.weather_now_scenic import server as scenic_server

MELBOURNE = "Australia/Melbourne"
# 2026-10-08 00:30 UTC: Melbourne (AEDT, +11) 11:30 on the 8th,
# Berlin (CEST, +2) 02:30 on the 8th.
INSTANT_A = datetime(2026, 10, 8, 0, 30, tzinfo=UTC)
# 2026-10-07 14:00 UTC: Melbourne 01:00 on the 8th, Berlin 16:00 on the 7th.
INSTANT_B = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)


@pytest.fixture
def berlin_server() -> Iterator[None]:
    """Run the test with the process clock in Europe/Berlin."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def _freeze(monkeypatch: pytest.MonkeyPatch, module: Any, instant: datetime) -> None:
    """Make ``module.location_now`` see a fixed instant."""

    def _fixed(tz_name: Any = None, utc_offset_seconds: Any = None) -> datetime:
        return location_now(tz_name, utc_offset_seconds, now=instant)

    monkeypatch.setattr(module, "location_now", _fixed)


def _ctx(tmp_path: Path) -> dict[str, Any]:
    return {"data_dir": str(tmp_path)}


_OPTS = {"latitude": -37.8136, "longitude": 144.9631, "label": "Melbourne"}


# ---------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------


def test_location_now_uses_iana_zone(berlin_server: None) -> None:
    n = location_now(MELBOURNE, 7200, now=INSTANT_A)
    assert (n.hour, n.minute) == (11, 30)
    assert n.date().isoformat() == "2026-10-08"


def test_location_now_falls_back_to_offset_then_server_local(berlin_server: None) -> None:
    # Unknown zone name: the fixed offset wins.
    n = location_now("Not/AZone", 11 * 3600, now=INSTANT_A)
    assert (n.hour, n.minute) == (11, 30)
    # Neither usable: server local (Berlin here).
    n = location_now(None, None, now=INSTANT_A)
    assert (n.hour, n.minute) == (2, 30)
    n = location_now("", True, now=INSTANT_A)
    assert (n.hour, n.minute) == (2, 30)


def test_payload_now_reads_open_meteo_fields(berlin_server: None) -> None:
    n = payload_now({"timezone": MELBOURNE, "utc_offset_seconds": 39600}, now=INSTANT_B)
    assert n.date().isoformat() == "2026-10-08"
    assert n.hour == 1


def test_is_iana_zone_rejects_junk() -> None:
    assert is_iana_zone(MELBOURNE)
    assert is_iana_zone("UTC")
    for bad in (None, "", 42, "Mars/Olympus", "../etc/passwd", "/etc/localtime", "x" * 80):
        assert not is_iana_zone(bad), bad


def test_location_now_default_instant_is_aware() -> None:
    assert lt.location_now(MELBOURNE).tzinfo is not None
    assert lt.location_now().tzinfo is not None


# ---------------------------------------------------------------------
# weather_now
# ---------------------------------------------------------------------


def _weather_now_payload() -> dict[str, Any]:
    return {
        "timezone": MELBOURNE,
        "utc_offset_seconds": 39600,
        "current": {"temperature_2m": 18.0, "weather_code": 1, "is_day": 1},
        "daily": {
            "time": ["2026-10-08"],
            "sunrise": ["2026-10-08T06:30"],
            "sunset": ["2026-10-08T19:40"],
        },
    }


def test_weather_now_now_min_is_location_time(
    berlin_server: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _freeze(monkeypatch, now_server, INSTANT_A)
    monkeypatch.setattr(now_server, "fetch_json", lambda *a, **k: _weather_now_payload())
    out = now_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert out["sun"]["nowMin"] == 11 * 60 + 30  # Melbourne, not Berlin's 150
    assert out["tz"] == MELBOURNE


def test_weather_now_recomputes_now_min_on_cache_hit(
    berlin_server: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _fetch(*a: Any, **k: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _weather_now_payload()

    monkeypatch.setattr(now_server, "fetch_json", _fetch)
    _freeze(monkeypatch, now_server, INSTANT_A)
    first = now_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert first["sun"]["nowMin"] == 690

    _freeze(monkeypatch, now_server, INSTANT_A + timedelta(minutes=7))
    second = now_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert calls["n"] == 1  # served from cache
    assert second["sun"]["nowMin"] == 697


# ---------------------------------------------------------------------
# weather_forecast
# ---------------------------------------------------------------------


def _forecast_payload() -> dict[str, Any]:
    return {
        "timezone": MELBOURNE,
        "utc_offset_seconds": 39600,
        "daily": {
            "time": ["2026-10-08", "2026-10-09", "2026-10-10"],
            "temperature_2m_max": [20, 21, 22],
            "temperature_2m_min": [10, 11, 12],
            "weather_code": [1, 2, 3],
            "precipitation_probability_max": [0, 10, 20],
        },
    }


def test_weather_forecast_today_and_time_follow_location(
    berlin_server: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Berlin is still on the 7th; Melbourne is on the 8th.
    _freeze(monkeypatch, forecast_server, INSTANT_B)
    monkeypatch.setattr(forecast_server, "fetch_json", lambda *a, **k: _forecast_payload())
    out = forecast_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert [d["today"] for d in out["days"]] == [True, False, False]
    assert out["time"] == "01:00"


def test_weather_forecast_recomputes_today_on_cache_hit(
    berlin_server: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def _fetch(*a: Any, **k: Any) -> dict[str, Any]:
        calls["n"] += 1
        return _forecast_payload()

    monkeypatch.setattr(forecast_server, "fetch_json", _fetch)
    _freeze(monkeypatch, forecast_server, INSTANT_B)
    forecast_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))

    # A day later in Melbourne, still inside the cache TTL as far as the
    # file mtime is concerned.
    _freeze(monkeypatch, forecast_server, INSTANT_B + timedelta(days=1, minutes=5))
    out = forecast_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert calls["n"] == 1
    assert [d["today"] for d in out["days"]] == [False, True, False]
    assert out["time"] == "01:05"


# ---------------------------------------------------------------------
# weather_now_scenic
# ---------------------------------------------------------------------


def test_weather_now_scenic_returns_location_zone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _weather_now_payload()
    monkeypatch.setattr(scenic_server, "fetch_json", lambda *a, **k: payload)
    out = scenic_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert out["tz"] == MELBOURNE
    assert out["utc_offset_seconds"] == 39600


# ---------------------------------------------------------------------
# clock_sunrise_sunset cache
# ---------------------------------------------------------------------


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self._raw = json.dumps(body).encode()
        self.headers: dict[str, str] = {}

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def _sun_payload(day: str) -> dict[str, Any]:
    return {
        "timezone": MELBOURNE,
        "utc_offset_seconds": 39600,
        "daily": {
            "time": [day],
            "sunrise": [f"{day}T06:30"],
            "sunset": [f"{day}T19:40"],
            "daylight_duration": [47000.0],
        },
    }


def test_sunrise_cache_refetches_after_location_midnight(
    berlin_server: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Melbourne's 7th, 23:00 (Berlin 14:00 the same day).
    _freeze(monkeypatch, sun_server, datetime(2026, 10, 7, 12, 0, tzinfo=UTC))
    with patch("urllib.request.urlopen", return_value=_Resp(_sun_payload("2026-10-07"))):
        first = sun_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert first["sunrise"] == "2026-10-07T06:30"
    assert first["tz"] == MELBOURNE

    # Two hours later it is the 8th in Melbourne but still the 7th in
    # Berlin, and the 6 h cache has not expired: the old entry must not
    # be served.
    _freeze(monkeypatch, sun_server, INSTANT_B)
    fetched = MagicMock(return_value=_Resp(_sun_payload("2026-10-08")))
    with patch("urllib.request.urlopen", fetched):
        second = sun_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert fetched.call_count == 1
    assert second["sunrise"] == "2026-10-08T06:30"

    # Same Melbourne day: served from cache.
    fetched = MagicMock(side_effect=AssertionError("should be cached"))
    with patch("urllib.request.urlopen", fetched):
        third = sun_server.fetch(dict(_OPTS), {}, ctx=_ctx(tmp_path))
    assert third["sunrise"] == "2026-10-08T06:30"
    assert third["date"] == "2026-10-08"


# ---------------------------------------------------------------------
# Location picker: optional ``timezone`` inside the location value
# ---------------------------------------------------------------------

_LOC_SPEC: dict[str, Any] = {"name": "location", "type": "location_search", "default": ""}


def _loc(tz: Any) -> str:
    return json.dumps(
        {
            "name": "Melbourne",
            "country": "Australia",
            "admin1": "Victoria",
            "latitude": -37.814,
            "longitude": 144.96332,
            "timezone": tz,
        }
    )


def test_location_whitelist_keeps_valid_timezone() -> None:
    out = _coerce_cell_option(_LOC_SPEC, _loc(MELBOURNE), {})
    assert out["timezone"] == MELBOURNE
    assert out["latitude"] == -37.814


@pytest.mark.parametrize("bad", ["Mars/Olympus", "../../etc/passwd", "", 10, None])
def test_location_whitelist_drops_invalid_timezone(bad: Any) -> None:
    out = _coerce_cell_option(_LOC_SPEC, _loc(bad), {})
    assert "timezone" not in out
    assert out["name"] == "Melbourne"


def test_settings_location_whitelist_keeps_valid_timezone() -> None:
    from app.settings._shared import coerce_form_value

    assert coerce_form_value(_LOC_SPEC, _loc(MELBOURNE))["timezone"] == MELBOURNE
    assert "timezone" not in coerce_form_value(_LOC_SPEC, _loc("Nowhere/Land"))


def test_geocode_keeps_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [
            {"name": "Melbourne", "latitude": -37.814, "longitude": 144.96, "timezone": MELBOURNE}
        ]
    }
    monkeypatch.setattr(composer, "fetch_json", lambda *a, **k: payload)
    composer._GEOCODE_CACHE.clear()
    try:
        out = composer._geocode("Melbourne")
        assert out is not None
        assert out["timezone"] == MELBOURNE

        payload["results"][0]["timezone"] = "Bogus/Zone"
        out = composer._geocode("Hobart")
        assert out is not None
        assert "timezone" not in out
    finally:
        composer._GEOCODE_CACHE.clear()


def test_resolved_options_passes_location_timezone_through() -> None:
    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = {"location": "", "label": ""}
    registry = MagicMock()
    registry.get.return_value = plugin
    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    fake_app.config["SETTINGS_STORE"].get_section.return_value = {}
    loc = json.loads(_loc(MELBOURNE))
    with patch.object(composer, "current_app", fake_app):
        out = composer._resolved_options("weather_now", {"location": loc})
    assert out["location"]["timezone"] == MELBOURNE
    # Not promoted to a top-level option (clock widgets own "timezone").
    assert "timezone" not in out


def _fake_app(defaults: dict[str, object], app_section: dict[str, object]) -> MagicMock:
    plugin = MagicMock()
    plugin.cell_option_defaults.return_value = defaults
    registry = MagicMock()
    registry.get.return_value = plugin
    fake_app = MagicMock()
    fake_app.config = {"PLUGIN_REGISTRY": registry, "SETTINGS_STORE": MagicMock()}
    fake_app.config["SETTINGS_STORE"].get_section.return_value = app_section
    return fake_app


def test_resolved_options_writes_a_geocoded_string_back_as_the_place() -> None:
    hit = {"name": "Melbourne", "latitude": -37.81, "longitude": 144.96, "timezone": MELBOURNE}
    fake_app = _fake_app({"location": "", "label": ""}, {})
    with (
        patch.object(composer, "current_app", fake_app),
        patch.object(composer, "_geocode", return_value=hit),
    ):
        out = composer._resolved_options("sky_tonight", {"location": "Melbourne"})
    assert out["location"]["timezone"] == MELBOURNE
    assert out["latitude"] == -37.81


def test_resolved_options_hands_the_app_location_to_a_widget_with_a_location_option() -> None:
    app_loc = {"name": "Melbourne", "latitude": -37.81, "longitude": 144.96, "timezone": MELBOURNE}
    with (
        patch.object(composer, "current_app", _fake_app({"location": "", "label": ""}, {})),
        patch.object(composer, "_app_location_dict", return_value=app_loc),
    ):
        out = composer._resolved_options("sky_tonight", {})
    assert out["location"]["timezone"] == MELBOURNE
    # A widget with no location option gets the coordinates but not the key.
    with (
        patch.object(composer, "current_app", _fake_app({"label": ""}, {})),
        patch.object(composer, "_app_location_dict", return_value=app_loc),
    ):
        out = composer._resolved_options("clock_digital", {})
    assert "location" not in out
    assert out["latitude"] == -37.81
