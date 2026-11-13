"""Floating ICS times (no TZID, no Z) are the viewer's local time."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

_SERVER_PATH = Path(__file__).resolve().parent.parent / "server.py"


def _load_server() -> Any:
    spec = importlib.util.spec_from_file_location("calendar_core_server_floating", _SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load_server()

_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:floating@test
DTSTART:20261116T090000
DTEND:20261116T100000
SUMMARY:Floating
END:VEVENT
END:VCALENDAR
"""


def test_floating_event_is_read_in_the_app_timezone() -> None:
    with patch.object(server, "app_timezone", lambda: ZoneInfo("America/New_York")):
        events = server._expand_events_full(
            _ICS, datetime(2026, 11, 15, tzinfo=UTC), datetime(2026, 11, 18, tzinfo=UTC)
        )
    assert [(e["start"], e["end"]) for e in events] == [
        ("2026-11-16T14:00:00+00:00", "2026-11-16T15:00:00+00:00")
    ]
