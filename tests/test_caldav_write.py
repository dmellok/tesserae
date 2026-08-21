"""CalDAV booking (#90 phase 4).

No network. The opener is stubbed, so what's under test is the request
this builds, the ICS it emits, and how it reports failure. The one thing
that must never happen is a booking that quietly didn't happen: every
failure path raises rather than returning.
"""

from __future__ import annotations

import urllib.error
from datetime import UTC, datetime
from typing import Any

import pytest

from app.caldav_write import (
    CalDavWriteError,
    build_event_ics,
    event_url,
    new_uid,
    put_event,
)

START = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)
END = datetime(2026, 8, 21, 14, 30, tzinfo=UTC)


class _Resp:
    def __init__(self, status: int) -> None:
        self.status = status

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class _Opener:
    def __init__(self, status: int = 201, raises: Exception | None = None) -> None:
        self._status = status
        self._raises = raises
        self.requests: list[Any] = []

    def open(self, request: Any, timeout: int = 0) -> _Resp:
        self.requests.append(request)
        if self._raises is not None:
            raise self._raises
        return _Resp(self._status)


# -- the ICS -------------------------------------------------------------


def test_ics_carries_the_booking() -> None:
    ics = build_event_ics(
        uid="u1",
        summary="Booked from the panel",
        start=START,
        end=END,
        location="Kestrel",
        now=START,
    )
    assert "BEGIN:VEVENT" in ics and "END:VEVENT" in ics
    assert "UID:u1" in ics
    assert "DTSTART:20260821T140000Z" in ics
    assert "DTEND:20260821T143000Z" in ics
    assert "SUMMARY:Booked from the panel" in ics
    assert "LOCATION:Kestrel" in ics


def test_ics_uses_crlf_line_endings() -> None:
    """RFC 5545 requires CRLF, and some servers reject bare LF."""
    ics = build_event_ics(uid="u1", summary="x", start=START, end=END, now=START)
    assert "\r\n" in ics
    assert ics.endswith("\r\n")
    assert "\n" not in ics.replace("\r\n", "")


def test_ics_escapes_text_that_would_change_the_grammar() -> None:
    """A room called 'Kestrel; Level 3' would otherwise emit a property
    parameter rather than a location."""
    ics = build_event_ics(
        uid="u1", summary="A, B; C", start=START, end=END, location="Kestrel; L3", now=START
    )
    assert "SUMMARY:A\\, B\\; C" in ics
    assert "LOCATION:Kestrel\\; L3" in ics


def test_ics_omits_location_when_absent() -> None:
    assert "LOCATION" not in build_event_ics(uid="u1", summary="x", start=START, end=END, now=START)


def test_naive_datetimes_are_stamped_as_utc() -> None:
    """A naive datetime slipping through must not emit a floating time
    that a server would interpret in its own zone."""
    ics = build_event_ics(uid="u1", summary="x", start=START, end=END, now=START)
    assert ics.count("Z\r\n") >= 3


# -- the URL -------------------------------------------------------------


def test_event_url_sits_inside_the_collection() -> None:
    assert (
        event_url("https://dav.example/cal/kestrel/", "u1")
        == "https://dav.example/cal/kestrel/u1.ics"
    )


def test_event_url_tolerates_a_missing_trailing_slash() -> None:
    assert (
        event_url("https://dav.example/cal/kestrel", "u1")
        == "https://dav.example/cal/kestrel/u1.ics"
    )


def test_event_url_drops_an_export_query() -> None:
    """Feeds often carry the ?export form; PUTting to that would be a
    write to the wrong resource."""
    assert (
        event_url("https://dav.example/cal/kestrel/?export", "u1")
        == "https://dav.example/cal/kestrel/u1.ics"
    )


def test_uids_are_unique() -> None:
    assert new_uid() != new_uid()


# -- the request ---------------------------------------------------------


def test_put_sends_the_ics_with_the_right_method_and_type() -> None:
    opener = _Opener(201)
    put_event("https://dav.example/cal/k/", "ICSBODY", "u1", opener=opener)
    req = opener.requests[0]
    assert req.get_method() == "PUT"
    assert req.data == b"ICSBODY"
    assert req.headers["Content-type"].startswith("text/calendar")


def test_put_refuses_to_overwrite_an_existing_event() -> None:
    """If-None-Match: * makes this a create. Without it a UID collision
    would silently replace somebody else's meeting."""
    opener = _Opener(201)
    put_event("https://dav.example/cal/k/", "x", "u1", opener=opener)
    assert opener.requests[0].headers["If-none-match"] == "*"


@pytest.mark.parametrize("status", [200, 201, 204])
def test_put_accepts_every_success_status_a_server_may_use(status: int) -> None:
    assert put_event("https://dav.example/cal/k/", "x", "u1", opener=_Opener(status))


def test_put_rejects_an_unexpected_success_status() -> None:
    with pytest.raises(CalDavWriteError):
        put_event("https://dav.example/cal/k/", "x", "u1", opener=_Opener(302))


# -- failure reporting ---------------------------------------------------


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://dav.example", code, "no", {}, None)  # type: ignore[arg-type]


def test_bad_credentials_say_so() -> None:
    with pytest.raises(CalDavWriteError, match="credentials"):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=_http_error(401)))


def test_a_forbidden_write_is_not_reported_as_a_password_problem() -> None:
    """Verified against live Radicale: a collection that does not exist
    answers 403, not 404. Lumping that in with 401 sent an operator off
    to re-check a password that was never wrong."""
    with pytest.raises(CalDavWriteError, match="accepted the login"):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=_http_error(403)))


def test_a_precondition_failure_reads_as_a_taken_slot() -> None:
    with pytest.raises(CalDavWriteError, match="taken"):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=_http_error(412)))


def test_a_missing_collection_says_so_rather_than_showing_a_status() -> None:
    """Measured against live Radicale: a collection that doesn't exist
    answers 409, not 404. It's the likeliest real misconfiguration (a
    feed naming a calendar that was renamed), so it earns a message."""
    with pytest.raises(CalDavWriteError, match="No calendar exists"):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=_http_error(409)))


def test_method_not_allowed_points_at_an_export_only_url() -> None:
    """The likeliest real misconfiguration: the feed is an ICS export
    rather than a CalDAV collection, so nothing can be written to it."""
    with pytest.raises(CalDavWriteError, match="export-only"):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=_http_error(405)))


def test_an_unreachable_server_is_reported_not_swallowed() -> None:
    err = urllib.error.URLError("connection refused")
    with pytest.raises(CalDavWriteError, match="Could not reach"):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=err))


def test_an_unexpected_error_still_raises() -> None:
    """A booking that quietly didn't happen is the worst outcome here."""
    with pytest.raises(CalDavWriteError):
        put_event("https://d/c/", "x", "u1", opener=_Opener(raises=ValueError("weird")))
