"""Per-event colours from the .ics feed (#222).

A single calendar is often colour-coded by category -- work, school, holiday
-- and every one of those events arrived at the widget stamped with the
feed's one colour, so the distinction the user maintains upstream was thrown
away on the way in.

The colour is read off the event and carried on every event; whether it wins
over the feed's is a per-feed opt-in, because the two uses are opposite. One
calendar categorises by colour; another wants everything to read as "work"
whatever the producer set.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

_SERVER_PATH = Path(__file__).resolve().parent.parent / "server.py"


def _load_server() -> Any:
    """Load this plugin's ``server.py`` under a unique module name.

    Every plugin ships a module called ``server``, and the sibling suites
    reach theirs with ``sys.path.insert`` + ``import server``. Doing the same
    here would put whichever ran first into ``sys.modules["server"]`` and hand
    the other one the wrong module -- which is what happened: adding this file
    turned eleven passing calendar_day tests red without touching them.
    """
    spec = importlib.util.spec_from_file_location("calendar_core_server_under_test", _SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load_server()


class _Comp(dict):
    """The subset of an icalendar component the extractor touches."""


def test_an_rfc7986_colour_name_is_read() -> None:
    """`COLOR` carries a CSS3 name, which is what Google Calendar writes."""
    assert server._event_colour(_Comp({"COLOR": "tomato"})) == "#ff6347"


def test_a_colour_name_is_case_insensitive() -> None:
    assert server._event_colour(_Comp({"COLOR": "SlateBlue"})) == "#6a5acd"


def test_an_apple_hex_colour_is_read() -> None:
    """Apple and several CalDAV servers write a hex triple instead."""
    assert server._event_colour(_Comp({"X-APPLE-CALENDAR-COLOR": "#1E90FF"})) == "#1e90ff"


def test_an_alpha_byte_is_dropped() -> None:
    """`#RRGGBBAA` is common in the wild; the panel has no alpha."""
    assert server._event_colour(_Comp({"X-APPLE-CALENDAR-COLOR": "#1e90ffcc"})) == "#1e90ff"


def test_an_unrecognised_colour_is_absent_not_guessed() -> None:
    """Returning "" falls back to the feed colour, which is right.

    Inventing one would put a wrong colour on an event, which is worse than
    the feed's own: the whole point is that the colour carries meaning.
    """
    assert server._event_colour(_Comp({"COLOR": "burnt-sienna"})) == ""
    assert server._event_colour(_Comp({"X-APPLE-CALENDAR-COLOR": "rgb(1,2,3)"})) == ""


def test_an_event_with_no_colour_is_absent() -> None:
    assert server._event_colour(_Comp({})) == ""


def test_a_named_colour_wins_over_a_hex_one() -> None:
    """RFC 7986 is the standard field; the X- one is the fallback."""
    comp = _Comp({"COLOR": "tomato", "X-APPLE-CALENDAR-COLOR": "#000000"})
    assert server._event_colour(comp) == "#ff6347"


def test_every_mapped_name_is_a_six_digit_hex() -> None:
    """A typo in the table would ship a broken colour to a panel."""
    for name, value in server._CSS3_COLOURS.items():
        assert re.fullmatch(r"#[0-9a-f]{6}", value), f"{name} -> {value}"
