"""calendar_core Home Assistant feed source: ``{"source": "ha"}`` feed
rows that read a ``calendar.*`` entity through ha_core instead of an ICS
URL.

Pure normalisation helpers are exercised on the standalone module (same
loader as the CalDAV tests); the ``load_events`` branch uses a stubbed
ha_core so no app context or network is needed; admin routes go through
the test client.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.main import REPO_ROOT


def _load_calendar_core() -> Any:
    path = REPO_ROOT / "plugins" / "calendar_core" / "server.py"
    spec = importlib.util.spec_from_file_location("_calendar_core_ha_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cc() -> Any:
    return _load_calendar_core()


# ----- normalisation --------------------------------------------------


def test_parse_ha_when_datetime_normalises_to_utc(cc: Any) -> None:
    iso, all_day = cc._parse_ha_when({"dateTime": "2026-08-29T10:00:00+10:00"})
    assert all_day is False
    assert iso == "2026-08-29T00:00:00+00:00"


def test_parse_ha_when_naive_datetime_assumed_utc(cc: Any) -> None:
    iso, all_day = cc._parse_ha_when({"dateTime": "2026-08-29T10:00:00"})
    assert all_day is False
    assert iso == "2026-08-29T10:00:00+00:00"


def test_parse_ha_when_date_is_all_day(cc: Any) -> None:
    assert cc._parse_ha_when({"date": "2026-08-29"}) == ("2026-08-29", True)


def test_parse_ha_when_bare_string_forms(cc: Any) -> None:
    assert cc._parse_ha_when("2026-08-29") == ("2026-08-29", True)
    iso, all_day = cc._parse_ha_when("2026-08-29T01:00:00+00:00")
    assert (iso, all_day) == ("2026-08-29T01:00:00+00:00", False)


def test_parse_ha_when_garbage_is_none(cc: Any) -> None:
    assert cc._parse_ha_when(None) is None
    assert cc._parse_ha_when({}) is None
    assert cc._parse_ha_when({"dateTime": "not a date"}) is None
    assert cc._parse_ha_when(42) is None


def test_normalise_ha_event_maps_fields(cc: Any) -> None:
    ev = cc._normalise_ha_event(
        {
            "summary": " Standup ",
            "location": "Office",
            "start": {"dateTime": "2026-08-29T09:00:00+00:00"},
            "end": {"dateTime": "2026-08-29T09:15:00+00:00"},
        }
    )
    assert ev == {
        "summary": "Standup",
        "location": "Office",
        "start": "2026-08-29T09:00:00+00:00",
        "end": "2026-08-29T09:15:00+00:00",
        "all_day": False,
    }


def test_normalise_ha_event_defaults(cc: Any) -> None:
    ev = cc._normalise_ha_event({"start": {"date": "2026-08-29"}})
    assert ev is not None
    assert ev["summary"] == "(untitled)"
    assert ev["all_day"] is True
    assert ev["end"] == "2026-08-29"  # missing end falls back to start
    assert cc._normalise_ha_event({"summary": "no start"}) is None


# ----- load_events branch ---------------------------------------------


class _StubCore:
    def __init__(self, payload: Any = None, err: Exception | None = None) -> None:
        self.calls: list[str] = []
        self.payload = payload if payload is not None else []
        self.err = err

    def request_json(self, path: str, timeout: int = 10) -> Any:
        self.calls.append(path)
        if self.err is not None:
            raise self.err
        return self.payload


def _write_ha_feed(data_dir: Path, **overrides: Any) -> None:
    feed = {
        "id": "family",
        "name": "Family",
        "source": "ha",
        "entity_id": "calendar.family",
        "colour": "#112233",
        "enabled": True,
        **overrides,
    }
    (data_dir / "feeds.json").write_text(json.dumps({"feeds": [feed]}), encoding="utf-8")


def test_load_events_ha_feed_tags_and_caches(cc: Any, tmp_path: Path) -> None:
    _write_ha_feed(tmp_path)
    stub = _StubCore(
        payload=[
            {
                "summary": "Dentist",
                "start": {"dateTime": "2026-08-29T04:00:00+00:00"},
                "end": {"dateTime": "2026-08-29T05:00:00+00:00"},
            },
            {"summary": "Bin day", "start": {"date": "2026-08-29"}, "end": {"date": "2026-08-30"}},
        ]
    )
    cc._ha_core = lambda: stub
    start = datetime(2026, 8, 28, tzinfo=UTC)
    end = start + timedelta(days=7)
    events = cc.load_events(None, start, end, data_dir=tmp_path)
    assert len(events) == 2
    assert all(e["feed_id"] == "family" for e in events)
    assert all(e["feed_name"] == "Family" for e in events)
    assert all(e["feed_colour"] == "#112233" for e in events)
    # All-day first, per the shared sort.
    assert events[0]["summary"] == "Bin day" and events[0]["all_day"] is True
    assert events[1]["summary"] == "Dentist" and events[1]["all_day"] is False
    assert len(stub.calls) == 1
    assert stub.calls[0].startswith("/api/calendars/calendar.family?start=")
    # Same window inside the TTL is served from the cache.
    cc.load_events(None, start, end, data_dir=tmp_path)
    assert len(stub.calls) == 1
    # Health records the successful fetch.
    health = json.loads((tmp_path / "feed_health.json").read_text(encoding="utf-8"))
    assert health["family"]["error"] is None


def test_load_events_ha_feed_filters_and_disabled(cc: Any, tmp_path: Path) -> None:
    _write_ha_feed(tmp_path, enabled=False)
    stub = _StubCore(payload=[{"summary": "X", "start": {"date": "2026-08-29"}}])
    cc._ha_core = lambda: stub
    start = datetime(2026, 8, 28, tzinfo=UTC)
    assert cc.load_events(None, start, start + timedelta(days=2), data_dir=tmp_path) == []
    assert stub.calls == []


def test_load_events_ha_feed_records_http_error(cc: Any, tmp_path: Path) -> None:
    _write_ha_feed(tmp_path)
    err = urllib.error.HTTPError("http://ha", 401, "unauthorized", None, io.BytesIO(b""))  # type: ignore[arg-type]
    stub = _StubCore(err=err)
    cc._ha_core = lambda: stub
    start = datetime(2026, 8, 28, tzinfo=UTC)
    assert cc.load_events(None, start, start + timedelta(days=2), data_dir=tmp_path) == []
    health = json.loads((tmp_path / "feed_health.json").read_text(encoding="utf-8"))
    assert health["family"]["error"] == "HTTP 401"
    assert health["family"]["failing_since"] is not None


def test_load_events_ha_feed_keeps_runtime_error_guidance(cc: Any, tmp_path: Path) -> None:
    _write_ha_feed(tmp_path)
    stub = _StubCore(err=RuntimeError("Home Assistant is not configured"))
    cc._ha_core = lambda: stub
    start = datetime(2026, 8, 28, tzinfo=UTC)
    assert cc.load_events(None, start, start + timedelta(days=2), data_dir=tmp_path) == []
    health = json.loads((tmp_path / "feed_health.json").read_text(encoding="utf-8"))
    assert health["family"]["error"] == "Home Assistant is not configured"


def test_load_todos_skips_ha_feeds(cc: Any, tmp_path: Path) -> None:
    _write_ha_feed(tmp_path)
    stub = _StubCore(payload=[{"summary": "X", "start": {"date": "2026-08-29"}}])
    cc._ha_core = lambda: stub
    assert cc.load_todos(None, data_dir=tmp_path) == []
    assert stub.calls == []


def test_feed_source_defaults_to_ics(cc: Any) -> None:
    assert cc.feed_source({"url": "http://x"}) == "ics"
    assert cc.feed_source({"source": "HA "}) == "ha"
    assert cc.feed_source({"source": "ics"}) == "ics"


# ----- admin routes ---------------------------------------------------


def _feeds(app: Any) -> list:
    plugin = app.config["PLUGIN_REGISTRY"].get("calendar_core")
    return plugin.server_module._load_feeds(plugin.data_dir).get("feeds") or []


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_create_feed_ha_stores_entity(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/plugins/calendar_core/feeds",
        data={"source": "ha", "name": "Family", "entity_id": "calendar.family"},
    )
    assert resp.status_code == 200
    feed = next(f for f in _feeds(app) if f["name"] == "Family")
    assert feed["source"] == "ha"
    assert feed["entity_id"] == "calendar.family"
    assert "url" not in feed and "auth_mode" not in feed


def test_create_feed_ha_rejects_non_calendar_entity(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/plugins/calendar_core/feeds",
        data={"source": "ha", "name": "Nope", "entity_id": "sensor.kitchen"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert not any(f["name"] == "Nope" for f in _feeds(app))


def test_update_auth_rejected_for_ha_feed(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={"source": "ha", "name": "Family", "entity_id": "calendar.family"},
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "Family")
    client.post(
        f"/plugins/calendar_core/feeds/{fid}/auth",
        data={"auth_mode": "basic", "username": "u", "password": "p"},
        follow_redirects=True,
    )
    feed = next(f for f in _feeds(app) if f["id"] == fid)
    assert "auth_mode" not in feed and "username" not in feed and "password" not in feed


def test_ha_list_route_unconfigured_flashes_guidance(app: Any) -> None:
    """Without an HA URL + token the listing flashes guidance instead of
    erroring; the page still renders."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/plugins/calendar_core/ha/list")
    assert resp.status_code == 200


def test_update_colour_changes_saved_feed(app: Any) -> None:
    """#276: a feed's colour is editable after it was saved."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={"source": "ha", "name": "Family", "entity_id": "calendar.family"},
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "Family")
    resp = client.post(f"/plugins/calendar_core/feeds/{fid}/colour", data={"colour": "#FF8800"})
    assert resp.status_code == 302
    feed = next(f for f in _feeds(app) if f["id"] == fid)
    assert feed["colour"] == "#ff8800"


def test_update_colour_rejects_bad_value(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={"source": "ha", "name": "Family", "entity_id": "calendar.family"},
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "Family")
    before = next(f for f in _feeds(app) if f["id"] == fid)["colour"]
    client.post(
        f"/plugins/calendar_core/feeds/{fid}/colour",
        data={"colour": "red"},
        follow_redirects=True,
    )
    assert next(f for f in _feeds(app) if f["id"] == fid)["colour"] == before
    assert (
        client.post(
            "/plugins/calendar_core/feeds/nope/colour", data={"colour": "#000000"}
        ).status_code
        == 404
    )


def test_feed_row_renders_colour_input(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={
            "source": "ha",
            "name": "Family",
            "entity_id": "calendar.family",
            "colour": "#123456",
        },
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "Family")
    html = client.get("/plugins/calendar_core/").get_data(as_text=True)
    assert f"/plugins/calendar_core/feeds/{fid}/colour" in html
    assert 'name="colour" value="#123456"' in html
