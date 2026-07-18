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

    def fake_http_get(url: str, auth: Any) -> bytes:
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

    def fake_http_get(url: str, auth: Any) -> bytes:
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
