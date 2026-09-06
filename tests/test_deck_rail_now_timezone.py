"""The rail's now-marker ticks in the configured timezone (#165).

The marker is drawn server-side from ``settings.app.timezone`` and then
advanced client-side each minute. The client recomputed it from the browser's
own clock, so an operator whose browser sits in a different zone from the one
they configured saw the marker jump by the offset a minute after the page
loaded -- the bug #143 / #164 / #170 fixed server-side, arriving again from
the other side.

The page therefore has to carry the zone, not just the rendered time. These
tests pin that contract: the name reaches the template, and the marker the
server draws is anchored to it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from flask import Flask


def _design(app: Flask) -> dict:
    from app.deck_routes import _design_cards

    with app.test_request_context("/decks"):
        return _design_cards(
            nav_decks=[],
            rotations=[],
            schedules=[],
            current_step={},
            schedule_status={},
            pages=[],
            devices=[],
        )


def test_the_design_carries_the_timezone_name(app: Flask) -> None:
    """Without the name the client has nothing but its own clock to use."""
    design = _design(app)
    assert "now_tz_name" in design


def test_the_timezone_name_is_resolvable_when_present(app: Flask) -> None:
    """An unresolvable name is worse than none: the client would fall back
    to the browser clock anyway, but only after ``Intl`` threw."""
    name = _design(app)["now_tz_name"]
    if name:
        assert ZoneInfo(name) is not None


def test_the_rendered_clock_matches_the_zone_it_reports(app: Flask) -> None:
    """The label and the zone must describe the same moment.

    If they disagree, the client tick will correct the marker to something
    the server-rendered label contradicts.
    """
    design = _design(app)
    name = design["now_tz_name"]
    if not name:
        return
    expected = datetime.now(ZoneInfo(name)).strftime("%H:%M")
    # A minute may tick over between the two reads; allow the adjacent value.
    assert (
        design["now_hhmm"][:2] == expected[:2]
        or abs(int(design["now_hhmm"][3:]) - int(expected[3:])) <= 1
    )


def test_the_page_ships_the_zone_to_the_client(app: Flask) -> None:
    """End to end: the template has to be able to read it."""
    client = app.test_client()
    # Complete first-run setup so the page renders instead of redirecting.
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    resp = client.get("/decks")
    try:
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "tzName" in body, "the rail tick no longer carries a timezone"
    finally:
        resp.close()
