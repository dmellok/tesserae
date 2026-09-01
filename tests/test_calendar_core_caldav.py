"""calendar_core CalDAV support: per-feed basic/digest auth, VTODO
parsing via ``load_todos``, and PROPFIND collection discovery.

Exercises the plugin's server module directly (fetched from the live
PLUGIN_REGISTRY, same as the calendar-widget timezone tests) plus a
couple of admin routes through the test client.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from app.main import REPO_ROOT


def _load_calendar_core() -> Any:
    """Import calendar_core's server.py standalone. The functions under
    test (VTODO parse, auth opener, PROPFIND discovery) don't touch
    ``current_app``, so no full app fixture is needed, which also keeps
    the app fixture's temp files out of these tests' GC."""
    path = REPO_ROOT / "plugins" / "calendar_core" / "server.py"
    spec = importlib.util.spec_from_file_location("_calendar_core_under_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cc() -> Any:
    return _load_calendar_core()


ICS_TODOS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VTODO
UID:t1
SUMMARY:Buy milk
STATUS:NEEDS-ACTION
PRIORITY:1
DUE:20260720T090000Z
END:VTODO
BEGIN:VTODO
UID:t2
SUMMARY:File taxes
STATUS:COMPLETED
PERCENT-COMPLETE:100
END:VTODO
BEGIN:VTODO
UID:t3
SUMMARY:Water plants
DUE:20260719
END:VTODO
BEGIN:VTODO
UID:t4
END:VTODO
END:VCALENDAR
"""

PROPFIND_XML = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://apple.com/ns/ical/">
 <d:response>
  <d:href>/dav/cal/you/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat>
 </d:response>
 <d:response>
  <d:href>/dav/cal/you/home/</d:href>
  <d:propstat><d:prop>
   <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
   <d:displayname>Home</d:displayname>
   <cs:calendar-color>#FF5733FF</cs:calendar-color>
   <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
  </d:prop></d:propstat>
 </d:response>
 <d:response>
  <d:href>/dav/cal/you/tasks/</d:href>
  <d:propstat><d:prop>
   <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
   <d:displayname>Tasks</d:displayname>
   <c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set>
  </d:prop></d:propstat>
 </d:response>
</d:multistatus>
"""


# -- VTODO parsing -------------------------------------------------------


def test_parse_todos_normalises_status_due_priority(cc: Any) -> None:
    items = cc._parse_todos(ICS_TODOS)
    by = {i["summary"]: i for i in items}
    # t4 has no summary → dropped.
    assert set(by) == {"Buy milk", "File taxes", "Water plants"}
    assert by["Buy milk"]["status"] == "needs_action"
    assert by["Buy milk"]["priority"] == 1
    assert by["Buy milk"]["due"] == "2026-07-20T09:00:00+00:00"  # datetime → UTC ISO
    assert by["File taxes"]["status"] == "completed"
    assert by["File taxes"]["percent_complete"] == 100
    assert by["Water plants"]["due"] == "2026-07-19"  # date-only stays a date


def test_parse_todos_tolerates_garbage(cc: Any) -> None:
    assert cc._parse_todos(b"not a calendar") == []


# -- _expand_events_cached window slicing --------------------------------


def test_expand_events_cached_keeps_all_day_event_past_utc_midnight(cc: Any) -> None:
    """A negative-UTC-offset evening (e.g. ~7pm US Eastern) pushes the UTC
    calendar day one ahead of the local one. The warm-cache window slice
    used to compare an all-day event's bare "YYYY-MM-DD" date string
    against the query window's full UTC datetime string, which silently
    dropped today's all-day event once that UTC rollover happened."""
    from datetime import UTC, datetime, timedelta

    cc._EXPANSION_CACHE["feed1"] = (
        1.0,
        [{"summary": "Conference", "start": "2026-08-10", "end": "2026-08-11", "all_day": True}],
    )
    start = datetime(2026, 8, 11, 1, 0, tzinfo=UTC)  # 8/10 20:00 America/New_York
    end = start + timedelta(hours=24)
    out = cc._expand_events_cached("feed1", 1.0, b"", start, end)
    assert [e["summary"] for e in out] == ["Conference"]


# -- _expand_events_full duplicate-UID handling --------------------------

ICS_DUP_UID = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:travel@test
DTSTART;VALUE=DATE:20260901
DTEND;VALUE=DATE:20260902
SUMMARY:Travel
END:VEVENT
BEGIN:VEVENT
UID:travel@test
DTSTART;VALUE=DATE:20260902
DTEND;VALUE=DATE:20260903
SUMMARY:Travel
END:VEVENT
END:VCALENDAR
"""

ICS_SERIES_WITH_OVERRIDE = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:series@test
DTSTART;VALUE=DATE:20260905
DTEND;VALUE=DATE:20260906
RRULE:FREQ=DAILY;COUNT=2
SUMMARY:Series
END:VEVENT
BEGIN:VEVENT
UID:series@test
RECURRENCE-ID;VALUE=DATE:20260906
DTSTART;VALUE=DATE:20260906
DTEND;VALUE=DATE:20260907
SUMMARY:Series (moved)
END:VEVENT
END:VCALENDAR
"""


def _september_window() -> tuple[Any, Any]:
    from datetime import UTC, datetime

    return datetime(2026, 8, 25, tzinfo=UTC), datetime(2026, 10, 1, tzinfo=UTC)


def test_expand_events_keeps_separate_vevents_that_share_a_uid(cc: Any) -> None:
    """Feeds from clients that reuse a UID across copied appointments
    (Outlook drag-copy) used to lose every copy but one, because
    ``recurring_ical_events`` folds same-UID components into a single
    event. Both 'Travel' days must survive expansion."""
    start, end = _september_window()
    out = cc._expand_events_full(ICS_DUP_UID, start, end)
    assert [(e["summary"], e["start"]) for e in out] == [
        ("Travel", "2026-09-01"),
        ("Travel", "2026-09-02"),
    ]


def test_expand_events_leaves_series_and_overrides_folded(cc: Any) -> None:
    """A recurring series and its RECURRENCE-ID override legitimately
    share a UID; the duplicate-UID rewrite must not split them, or the
    override would stop replacing its occurrence."""
    start, end = _september_window()
    out = cc._expand_events_full(ICS_SERIES_WITH_OVERRIDE, start, end)
    assert [(e["summary"], e["start"]) for e in out] == [
        ("Series", "2026-09-05"),
        ("Series (moved)", "2026-09-06"),
    ]


# -- load_todos (feeds.json + auth-aware fetch) --------------------------


def _write_feeds(data_dir: Path, feeds: list[dict[str, Any]]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "feeds.json").write_text(json.dumps({"feeds": feeds}), encoding="utf-8")


def test_load_todos_tags_feed_and_honours_enabled(
    cc: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dd = tmp_path / "cc"
    _write_feeds(
        dd,
        [
            {"id": "tasks", "name": "Tasks", "url": "http://x/tasks", "colour": "#123456"},
            {"id": "off", "name": "Off", "url": "http://x/off", "enabled": False},
        ],
    )
    seen: list[str] = []

    def fake_http_get(url: str, auth: Any, **kwargs: Any) -> bytes:
        seen.append(url)
        return ICS_TODOS

    monkeypatch.setattr(cc, "_http_get", fake_http_get)
    todos = cc.load_todos(None, data_dir=dd)
    # Disabled feed never fetched.
    assert seen == ["http://x/tasks"]
    assert all(t["feed_id"] == "tasks" for t in todos)
    assert todos[0]["feed_name"] == "Tasks"
    assert todos[0]["feed_colour"] == "#123456"


def test_load_todos_passes_feed_auth_through(
    cc: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dd = tmp_path / "cc"
    _write_feeds(
        dd,
        [
            {
                "id": "tasks",
                "name": "Tasks",
                "url": "http://x/tasks",
                "auth_mode": "digest",
                "username": "bern",
                "password": "pw",
            }
        ],
    )
    captured: dict[str, Any] = {}

    def fake_http_get(url: str, auth: Any, **kwargs: Any) -> bytes:
        captured["auth"] = auth
        return ICS_TODOS

    monkeypatch.setattr(cc, "_http_get", fake_http_get)
    cc.load_todos(["tasks"], data_dir=dd)
    assert captured["auth"] == {"mode": "digest", "username": "bern", "password": "pw"}


# -- auth opener ---------------------------------------------------------


def test_build_opener_plain_when_no_auth(cc: Any) -> None:
    op = cc._build_opener("http://x/", {"mode": "none", "username": "", "password": ""})
    assert not any("Digest" in type(h).__name__ for h in op.handlers)


def test_build_opener_installs_digest_handler(cc: Any) -> None:
    op = cc._build_opener("http://x/", {"mode": "digest", "username": "u", "password": "p"})
    assert any(isinstance(h, urllib.request.HTTPDigestAuthHandler) for h in op.handlers)


def test_build_opener_installs_basic_handler(cc: Any) -> None:
    op = cc._build_opener("http://x/", {"mode": "basic", "username": "u", "password": "p"})
    assert any(isinstance(h, urllib.request.HTTPBasicAuthHandler) for h in op.handlers)


# -- discovery -----------------------------------------------------------


class _FakeResp(io.BytesIO):
    def __init__(self, data: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(data)
        self.headers: dict[str, str] = headers or {}

    def __enter__(self) -> Any:
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()


def test_discover_collections_parses_calendars_and_todos(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:

    class _FakeOpener:
        def open(self, req: Any, timeout: float = 0) -> Any:
            return _FakeResp(PROPFIND_XML)

    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _FakeOpener())
    result = cc.discover_collections("http://baikal.lan/dav/cal/you/", None)
    assert result["error"] is None
    cols = {c["name"]: c for c in result["collections"]}
    # The bare collection (no <calendar/>) is skipped.
    assert set(cols) == {"Home", "Tasks"}
    assert cols["Home"]["components"] == ["VEVENT"]
    assert cols["Home"]["colour"] == "#FF5733"  # #RRGGBBAA → #RRGGBB
    assert cols["Tasks"]["components"] == ["VTODO"]
    # export URL is absolute + ?export, resolved against the base.
    assert cols["Tasks"]["export_url"] == "http://baikal.lan/dav/cal/you/tasks/?export"


def test_discover_collections_decodes_gzip_response(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Nextcloud behind a compressing proxy can return gzip even though we
    ask for identity; urllib doesn't decompress, so discovery must decode it
    rather than choke on the binary blob (#168)."""
    import gzip

    payload = gzip.compress(PROPFIND_XML)

    class _FakeOpener:
        def open(self, req: Any, timeout: float = 0) -> Any:
            return _FakeResp(payload, headers={"Content-Encoding": "gzip"})

    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _FakeOpener())
    result = cc.discover_collections("http://nextcloud.lan/remote.php/dav/calendars/you/", None)
    assert result["error"] is None
    assert {c["name"] for c in result["collections"]} == {"Home", "Tasks"}


def test_discover_collections_tolerates_junk_before_xml_declaration(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A BOM / stray newline before ``<?xml`` (a PHP / Nextcloud output-
    buffering quirk) makes a strict parser reject an otherwise-valid 207.
    Discovery must trim it and still enumerate the calendars (#168)."""
    dirty = b"\xef\xbb\xbf\n   " + PROPFIND_XML.lstrip()

    class _FakeOpener:
        def open(self, req: Any, timeout: float = 0) -> Any:
            return _FakeResp(dirty)

    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _FakeOpener())
    result = cc.discover_collections("http://nextcloud.lan/remote.php/dav/calendars/you/", None)
    assert result["error"] is None
    assert {c["name"] for c in result["collections"]} == {"Home", "Tasks"}


# sabre/dav (Baikal, Nextcloud) splits a response's props across a 200
# propstat (props it has) and a 404 propstat (props it lacks), and the
# 404 block can come first. A colourless calendar therefore has its
# <resourcetype> in a block that isn't the first one. #124.
PROPFIND_XML_SABRE = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:ical="http://apple.com/ns/ical/">
 <d:response>
  <d:href>/baikal/html/dav.php/calendars/bablokb/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
   <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/baikal/html/dav.php/calendars/bablokb/private/</d:href>
  <d:propstat><d:prop><ical:calendar-color/></d:prop>
   <d:status>HTTP/1.1 404 Not Found</d:status></d:propstat>
  <d:propstat><d:prop>
   <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype>
   <d:displayname>Private</d:displayname>
   <cal:supported-calendar-component-set><cal:comp name="VEVENT"/><cal:comp name="VTODO"/></cal:supported-calendar-component-set>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>
"""


def test_discover_collections_handles_split_propstats(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A colourless Baikal calendar with its resourcetype in a non-first
    propstat block is still detected (regression for #124)."""

    class _FakeOpener:
        def open(self, req: Any, timeout: float = 0) -> Any:
            return _FakeResp(PROPFIND_XML_SABRE)

    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _FakeOpener())
    result = cc.discover_collections(
        "http://baikal-dev/baikal/html/dav.php/calendars/bablokb/", None
    )
    assert result["error"] is None
    cols = {c["name"]: c for c in result["collections"]}
    # The bare calendar-home (no <calendar/>) is skipped; the colourless
    # calendar is found despite its 404 propstat coming first.
    assert set(cols) == {"Private"}
    assert cols["Private"]["components"] == ["VEVENT", "VTODO"]
    assert cols["Private"]["colour"] == cc.DEFAULT_COLOUR


# A principal URL (what bablokb pointed discovery at in #124): no calendars
# live here, but the principal advertises calendar-home-set pointing at the
# collection that holds them.
PRINCIPAL_XML = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
 <d:response>
  <d:href>/baikal/html/dav.php/principals/bablokb/</d:href>
  <d:propstat><d:prop>
   <d:resourcetype><d:collection/><d:principal/></d:resourcetype>
   <d:displayname>Bernhard Bablok</d:displayname>
   <cal:calendar-home-set><d:href>/baikal/html/dav.php/calendars/bablokb/</d:href></cal:calendar-home-set>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>
"""

CAL_HOME_XML = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
 <d:response><d:href>/baikal/html/dav.php/calendars/bablokb/</d:href>
  <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
   <d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/baikal/html/dav.php/calendars/bablokb/private/</d:href>
  <d:propstat><d:prop>
   <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype><d:displayname>Private</d:displayname>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
 <d:response><d:href>/baikal/html/dav.php/calendars/bablokb/dilbert/</d:href>
  <d:propstat><d:prop>
   <d:resourcetype><d:collection/><cal:calendar/></d:resourcetype><d:displayname>Dilbert</d:displayname>
  </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>
</d:multistatus>
"""


def test_discover_collections_follows_calendar_home_from_principal(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A principal URL resolves to its calendar-home-set and enumerates the
    calendars there (regression for #124's real cause)."""

    def fake_build(url: str, auth: Any) -> Any:
        xml = CAL_HOME_XML if "/calendars/" in url else PRINCIPAL_XML

        class _FakeOpener:
            def open(self, req: Any, timeout: float = 0) -> Any:
                return _FakeResp(xml)

        return _FakeOpener()

    monkeypatch.setattr(cc, "_build_opener", fake_build)
    result = cc.discover_collections(
        "http://baikal-dev/baikal/html/dav.php/principals/bablokb/", None
    )
    assert result["error"] is None
    cols = {c["name"] for c in result["collections"]}
    assert cols == {"Private", "Dilbert"}


def test_discover_collections_reports_auth_failure(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:

    class _FakeOpener:
        def open(self, req: Any, timeout: float = 0) -> Any:
            raise urllib.error.HTTPError("http://x", 401, "Unauthorized", None, io.BytesIO(b""))  # type: ignore[arg-type]

    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _FakeOpener())
    result = cc.discover_collections("http://x/", None)
    assert result["collections"] == []
    assert "Authentication failed" in result["error"]


def test_discover_collections_no_calendars_hints_calendar_home(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = b'<d:multistatus xmlns:d="DAV:"></d:multistatus>'

    class _FakeOpener:
        def open(self, req: Any, timeout: float = 0) -> Any:
            return _FakeResp(empty)

    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _FakeOpener())
    result = cc.discover_collections("http://x/", None)
    assert result["collections"] == []
    assert "calendar home" in result["error"]


def test_export_url_and_colour_helpers(cc: Any) -> None:
    assert cc._export_url("http://x/c/") == "http://x/c/?export"
    assert cc._export_url("http://x/c/?foo=1") == "http://x/c/?foo=1&export"
    assert cc._normalise_colour("#abcdefff") == "#abcdef"
    assert cc._normalise_colour("bogus") == cc.DEFAULT_COLOUR


# -- admin routes (auth fields + discovery) ------------------------------


def _live_core(app: Any) -> Any:
    plugin = app.config["PLUGIN_REGISTRY"].get("calendar_core")
    return plugin.server_module


def _feeds(app: Any) -> list:
    plugin = app.config["PLUGIN_REGISTRY"].get("calendar_core")
    return plugin.server_module._load_feeds(plugin.data_dir).get("feeds") or []


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_create_feed_stores_auth_block(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/plugins/calendar_core/feeds",
        data={
            "name": "Baikal Tasks",
            "url": "http://baikal.lan/dav/tasks/?export",
            "colour": "#123456",
            "auth_mode": "digest",
            "username": "bern",
            "password": "pw",
        },
    )
    assert resp.status_code in (302, 200)
    feeds = _feeds(app)
    feed = next(f for f in feeds if f["name"] == "Baikal Tasks")
    assert feed["auth_mode"] == "digest"
    assert feed["username"] == "bern"
    assert feed["password"] == "pw"


def test_create_feed_without_auth_omits_credentials(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={"name": "Public", "url": "http://x/basic.ics", "auth_mode": "none"},
    )
    feed = next(f for f in _feeds(app) if f["name"] == "Public")
    assert "auth_mode" not in feed and "password" not in feed


def test_update_auth_keeps_password_when_blank(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={
            "name": "T",
            "url": "http://x/t",
            "auth_mode": "basic",
            "username": "u",
            "password": "orig",
        },
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "T")
    # Change username, leave password blank → keep the stored one.
    client.post(
        f"/plugins/calendar_core/feeds/{fid}/auth",
        data={"auth_mode": "basic", "username": "u2", "password": ""},
    )
    feed = next(f for f in _feeds(app) if f["id"] == fid)
    assert feed["username"] == "u2"
    assert feed["password"] == "orig"


def test_update_auth_none_clears_credentials(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={
            "name": "T",
            "url": "http://x/t",
            "auth_mode": "digest",
            "username": "u",
            "password": "p",
        },
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "T")
    client.post(
        f"/plugins/calendar_core/feeds/{fid}/auth",
        data={"auth_mode": "none", "username": "", "password": ""},
    )
    feed = next(f for f in _feeds(app) if f["id"] == fid)
    assert "auth_mode" not in feed and "username" not in feed and "password" not in feed


def test_discover_route_lists_collections(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()
    _sign_in(client)
    core = _live_core(app)

    def fake_discover(base_url: str, auth: Any) -> dict[str, Any]:
        return {
            "collections": [
                {
                    "name": "Tasks",
                    "url": "http://baikal.lan/dav/tasks/",
                    "export_url": "http://baikal.lan/dav/tasks/?export",
                    "colour": "#0d8c7e",
                    "components": ["VTODO"],
                }
            ],
            "error": None,
        }

    monkeypatch.setattr(core, "discover_collections", fake_discover)
    resp = client.post(
        "/plugins/calendar_core/discover",
        data={
            "base_url": "http://baikal.lan/dav/",
            "auth_mode": "digest",
            "username": "u",
            "password": "p",
        },
    )
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Tasks" in body
    assert "http://baikal.lan/dav/tasks/?export" in body


def test_discover_route_keeps_url_on_empty_result(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discovery that finds nothing must not wipe the URL the user typed
    (regression for #124's UI half)."""
    client = app.test_client()
    _sign_in(client)
    core = _live_core(app)
    monkeypatch.setattr(
        core, "discover_collections", lambda base_url, auth: {"collections": [], "error": None}
    )
    typed = "http://baikal-dev/baikal/html/dav.php/calendars/bablokb/"
    resp = client.post(
        "/plugins/calendar_core/discover",
        data={"base_url": typed, "auth_mode": "digest", "username": "u", "password": "p"},
    )
    assert resp.status_code == 200
    # The base_url input is re-rendered with the typed value, not emptied.
    assert f'value="{typed}"' in resp.get_data(as_text=True)


def _two_collections(base_url: str, auth: Any) -> dict[str, Any]:
    return {
        "collections": [
            {
                "name": "Tasks",
                "url": "http://baikal.lan/dav/tasks/",
                "export_url": "http://baikal.lan/dav/tasks/?export",
                "colour": "#0d8c7e",
                "components": ["VTODO"],
            },
            {
                "name": "Personal",
                "url": "http://baikal.lan/dav/personal/",
                "export_url": "http://baikal.lan/dav/personal/?export",
                "colour": "#123456",
                "components": ["VEVENT"],
            },
        ],
        "error": None,
    }


def test_add_from_discovery_keeps_the_other_collections(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding one discovered calendar re-renders the discovery list rather than
    redirecting to a bare index, so the remaining collections don't vanish
    (regression for #124: 4 calendars, add one, the other three disappear)."""
    client = app.test_client()
    _sign_in(client)
    monkeypatch.setattr(_live_core(app), "discover_collections", _two_collections)

    resp = client.post(
        "/plugins/calendar_core/feeds",
        data={
            "name": "Tasks",
            "url": "http://baikal.lan/dav/tasks/?export",
            "colour": "#0d8c7e",
            "auth_mode": "digest",
            "username": "u",
            "password": "p",
            "discover_base_url": "http://baikal.lan/dav/",
        },
    )
    # Re-renders the discovered list (200), does not redirect (302).
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The un-added collection is still listed.
    assert "Personal" in body
    assert "http://baikal.lan/dav/personal/?export" in body
    # The added one was stored.
    assert any(f["name"] == "Tasks" for f in _feeds(app))


def test_manual_add_without_discovery_still_redirects(app: Any) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/plugins/calendar_core/feeds",
        data={"name": "Manual", "url": "http://x/manual.ics", "auth_mode": "none"},
    )
    assert resp.status_code == 302
    assert any(f["name"] == "Manual" for f in _feeds(app))


# -- CalDAV REPORT fallback (iCloud) -------------------------------------
#
# iCloud has no GET-able .ics export: a plain GET is answered with a 403
# that carries no auth challenge, and events are only readable through
# REPORT calendar-query requests. The fetch path must fall back to
# REPORT, rebuild the collection into one VCALENDAR, and remember the
# URL so later refreshes skip the doomed GET (#272).

REPORT_EVENTS_XML = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:response>
  <d:href>/cal/home/e1.ics</d:href>
  <d:propstat><d:prop><c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:e1
DTSTART;TZID=Europe/Berlin:20260910T100000
DTEND;TZID=Europe/Berlin:20260910T110000
SUMMARY:Standup
END:VEVENT
END:VCALENDAR
</c:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response>
  <d:href>/cal/home/e2.ics</d:href>
  <d:propstat><d:prop><c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:e2
DTSTART:20260911T090000Z
DTEND:20260911T093000Z
SUMMARY:Review
END:VEVENT
END:VCALENDAR
</c:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>
"""

REPORT_TODOS_XML = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
 <d:response>
  <d:href>/cal/home/t1.ics</d:href>
  <d:propstat><d:prop><c:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VTODO
UID:t1
SUMMARY:Water plants
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR
</c:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>
"""


class _ICloudishOpener:
    """GET → challenge-less 403 (what iCloud actually does); REPORT →
    canned multistatus, VTODO or VEVENT flavour by the query body."""

    def __init__(self) -> None:
        self.gets = 0
        self.reports: list[Any] = []

    def open(self, req: Any, timeout: float = 0) -> Any:
        if req.get_method() == "GET":
            self.gets += 1
            raise urllib.error.HTTPError(
                req.full_url,
                403,
                "Forbidden",
                None,
                io.BytesIO(b""),  # type: ignore[arg-type]
            )
        self.reports.append(req)
        body = req.data or b""
        return _FakeResp(REPORT_TODOS_XML if b'name="VTODO"' in body else REPORT_EVENTS_XML)


def test_auth_headers_preemptive_basic_only(cc: Any) -> None:
    hdrs = cc._auth_headers({"mode": "basic", "username": "you@x", "password": "app-pw"})
    assert hdrs["Authorization"] == "Basic eW91QHg6YXBwLXB3"
    assert cc._auth_headers({"mode": "digest", "username": "u", "password": "p"}) == {}
    assert cc._auth_headers({"mode": "none", "username": "", "password": ""}) == {}
    assert cc._auth_headers(None) == {}


def test_fetch_feed_blob_falls_back_to_report(cc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _ICloudishOpener()
    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: server)
    auth = {"mode": "basic", "username": "you@x", "password": "app-pw"}
    errors: list[str] = []
    blob = cc._fetch_feed_blob(
        "https://p43-caldav.icloud.com/1/calendars/home/?export", auth, error_out=errors
    )
    assert blob is not None
    # Both events and the todo were stitched into one calendar, with the
    # duplicated VTIMEZONE kept once.
    assert b"SUMMARY:Standup" in blob
    assert b"SUMMARY:Review" in blob
    assert b"SUMMARY:Water plants" in blob
    assert blob.count(b"BEGIN:VTIMEZONE") == 1
    # The REPORT addressed the collection itself (?export stripped), with
    # Depth 1, preemptive basic credentials, and a time-range only on the
    # VEVENT query.
    ev_req, todo_req = server.reports
    assert ev_req.full_url.endswith("/calendars/home/")
    assert ev_req.get_header("Depth") == "1"
    assert ev_req.get_header("Authorization") == "Basic eW91QHg6YXBwLXB3"
    assert b"time-range" in ev_req.data and b'name="VEVENT"' in ev_req.data
    assert b"time-range" not in todo_req.data and b'name="VTODO"' in todo_req.data
    # Downstream parsers see a normal feed blob.
    assert [t["summary"] for t in cc._parse_todos(blob)] == ["Water plants"]


def test_fetch_feed_blob_remembers_report_urls(cc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    server = _ICloudishOpener()
    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: server)
    url = "https://p43-caldav.icloud.com/1/calendars/home/"
    assert cc._fetch_feed_blob(url, None) is not None
    assert server.gets == 1
    # Second fetch goes straight to REPORT, no doomed GET.
    assert cc._fetch_feed_blob(url, None) is not None
    assert server.gets == 1


def test_fetch_feed_blob_falls_back_when_get_returns_html(
    cc: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 that isn't ICS (a login page, a WebDAV listing) must not be
    cached as a calendar; it triggers the REPORT fallback instead."""

    class _HtmlThenReport(_ICloudishOpener):
        def open(self, req: Any, timeout: float = 0) -> Any:
            if req.get_method() == "GET":
                self.gets += 1
                return _FakeResp(b"<html>sign in</html>")
            return super().open(req, timeout)

    server = _HtmlThenReport()
    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: server)
    errors: list[str] = []
    blob = cc._fetch_feed_blob("https://x/cal/", None, error_out=errors)
    assert blob is not None and b"SUMMARY:Standup" in blob
    assert "response wasn't an .ics calendar" in errors


def test_load_events_via_report_and_health(
    cc: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end through load_events: an iCloud-shaped feed yields its
    events via REPORT, and the fetch outcome lands in feed health (the
    events path used to skip health recording entirely)."""
    dd = tmp_path / "cc"
    _write_feeds(dd, [{"id": "icloud", "name": "iCloud", "url": "https://x/cal/home/"}])
    server = _ICloudishOpener()
    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: server)
    start, end = _september_window()
    out = cc.load_events(None, start, end, data_dir=dd)
    assert {e["summary"] for e in out} == {"Standup", "Review"}
    assert all(e["feed_id"] == "icloud" for e in out)
    assert cc.load_health(data_dir=dd)["icloud"]["error"] is None


def test_load_events_records_failure_reasons(
    cc: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both paths fail, feed health carries each distinct reason, so
    the operator sees an HTTP status instead of a generic shrug."""

    class _AllRefused:
        def open(self, req: Any, timeout: float = 0) -> Any:
            code = 403 if req.get_method() == "GET" else 401
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                "nope",
                None,
                io.BytesIO(b""),  # type: ignore[arg-type]
            )

    dd = tmp_path / "cc"
    _write_feeds(dd, [{"id": "icloud", "name": "iCloud", "url": "https://x/cal/home/"}])
    monkeypatch.setattr(cc, "_build_opener", lambda url, auth: _AllRefused())
    start, end = _september_window()
    assert cc.load_events(None, start, end, data_dir=dd) == []
    assert cc.load_health(data_dir=dd)["icloud"]["error"] == "HTTP 403 / HTTP 401"


def test_refresh_flash_carries_fetch_reason(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Refresh button used to flash a generic "couldn't reach the feed
    URL" for every failure; it now surfaces the recorded reason (#272)."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/plugins/calendar_core/feeds",
        data={"name": "IC", "url": "https://x/cal/", "auth_mode": "none"},
    )
    fid = next(f["id"] for f in _feeds(app) if f["name"] == "IC")
    core = _live_core(app)

    def fail(url: str, auth: Any, *, error_out: Any = None) -> None:
        if error_out is not None:
            error_out.append("HTTP 403")
        return None

    monkeypatch.setattr(core, "_fetch_feed_blob", fail)
    resp = client.post(f"/plugins/calendar_core/feeds/{fid}/refresh", follow_redirects=True)
    assert "HTTP 403" in resp.get_data(as_text=True)
