"""Per-feed symbol (#317).

A feed's colour is how the calendar widgets tell calendars apart, and a
black-and-white panel throws it away. A short marker, an emoji usually, set
once on the feed row does the same job in ink: every event from that feed is
stamped with it, and widgets put it in front of the title.

It is deliberately per feed and not per event. The question it answers is
"which calendar is this from", which per-event colours can't, and a single
marker per calendar is the cheapest thing that answers it.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SERVER_PATH = Path(__file__).resolve().parent.parent / "server.py"


def _load_server() -> Any:
    """See test_event_colours.py for why this avoids ``import server``."""
    spec = importlib.util.spec_from_file_location("calendar_core_symbol_under_test", _SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load_server()

_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:one@test
DTSTART:20260916T090000Z
DTEND:20260916T100000Z
SUMMARY:Standup
END:VEVENT
END:VCALENDAR
"""


def _store(tmp_path: Path, feeds: list[dict[str, Any]]) -> Path:
    (tmp_path / "feeds.json").write_text(json.dumps({"feeds": feeds}), encoding="utf-8")
    for feed in feeds:
        if feed.get("url"):
            server._ics_cache_path(tmp_path, feed["id"]).write_bytes(_ICS)
    return tmp_path


_WINDOW = (datetime(2026, 9, 15, tzinfo=UTC), datetime(2026, 9, 18, tzinfo=UTC))


def test_a_symbol_is_trimmed_and_capped() -> None:
    assert server._clean_symbol("  💼 ") == "💼"
    assert server._clean_symbol(None) == ""
    assert server._clean_symbol("a" * 20) == "a" * server.SYMBOL_MAX_CHARS


def test_a_multi_code_point_emoji_survives() -> None:
    """A flag is two code points and a family is a ZWJ sequence; both are
    one glyph to the person typing it and must not be cut in half."""
    assert server._clean_symbol("🇦🇺") == "🇦🇺"
    assert server._clean_symbol("👨‍👩‍👧") == "👨‍👩‍👧"


def test_control_characters_are_dropped() -> None:
    assert server._clean_symbol("★\n\t") == "★"


def test_an_ics_event_carries_the_feed_symbol(tmp_path: Path) -> None:
    dd = _store(
        tmp_path, [{"id": "work", "name": "Work", "url": "https://x/a.ics", "symbol": "💼"}]
    )
    events = server.load_events(None, *_WINDOW, data_dir=dd)
    assert [e["feed_symbol"] for e in events] == ["💼"]
    # The summary itself is untouched: prefixing is the widget's call.
    assert events[0]["summary"] == "Standup"


def test_a_feed_without_a_symbol_stamps_an_empty_one(tmp_path: Path) -> None:
    """Empty rather than absent, so a widget can read it without a guard and
    every feed that predates the field behaves as it always did."""
    dd = _store(tmp_path, [{"id": "home", "name": "Home", "url": "https://x/b.ics"}])
    events = server.load_events(None, *_WINDOW, data_dir=dd)
    assert events and events[0]["feed_symbol"] == ""


def test_a_home_assistant_feed_carries_the_symbol_too(tmp_path: Path, monkeypatch: Any) -> None:
    """The request that prompted this came from HA calendars, which go
    through a different fetch path from ICS feeds."""
    dd = _store(
        tmp_path,
        [
            {
                "id": "school",
                "name": "School",
                "source": "ha",
                "entity_id": "calendar.school",
                "symbol": "🎒",
            }
        ],
    )
    monkeypatch.setattr(
        server,
        "_fetch_ha_events",
        lambda feed, start, end, data_dir: [
            {
                "summary": "Assembly",
                "start": "2026-09-16T09:00:00+00:00",
                "end": None,
                "all_day": False,
            }
        ],
    )
    events = server.load_events(None, *_WINDOW, data_dir=dd)
    assert [(e["summary"], e["feed_symbol"]) for e in events] == [("Assembly", "🎒")]


def test_a_todo_carries_the_feed_symbol(tmp_path: Path) -> None:
    ics = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VTODO
UID:todo@test
SUMMARY:Buy milk
STATUS:NEEDS-ACTION
END:VTODO
END:VCALENDAR
"""
    (tmp_path / "feeds.json").write_text(
        json.dumps(
            {
                "feeds": [
                    {"id": "chores", "name": "Chores", "url": "https://x/c.ics", "symbol": "🧹"}
                ]
            }
        ),
        encoding="utf-8",
    )
    server._ics_cache_path(tmp_path, "chores").write_bytes(ics)
    todos = server.load_todos(None, data_dir=tmp_path)
    assert [(t["summary"], t["feed_symbol"]) for t in todos] == [("Buy milk", "🧹")]
