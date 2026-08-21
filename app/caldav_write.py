"""Create a calendar event over CalDAV (#90, phase 4).

The narrowest possible write path: one ``PUT`` of one ``VEVENT`` into a
collection Tesserae already reads. Credentials, the collection URL and
the HTTP opener all come from ``calendar_core``, which discovered and
stored them when the feed was added, so this adds no new credential
storage and no new auth surface.

Scope, stated plainly: **basic and digest auth only**. That covers
Radicale, Baikal, Nextcloud and iCloud app-specific passwords. Google and
Microsoft 365 require OAuth with a registered application and a consent
flow, which is a different project; a room on one of those calendars
books through its own endpoint instead (see ``book_url``).

Deliberately not a CalDAV client library. Reading is already solved by
``calendar_core``; the only write anyone needs here is "add an event",
and a single PUT is the whole of it.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

TIMEOUT_S = 15
PRODID = "-//Tesserae//Rooms//EN"


class CalDavWriteError(RuntimeError):
    """A booking could not be written. Carries a message fit to show an
    operator, not a stack trace."""


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _escape(text: str) -> str:
    """iCalendar text escaping (RFC 5545 §3.3.11). A room called
    ``Kestrel; Level 3`` would otherwise emit a property parameter."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def build_event_ics(
    *,
    uid: str,
    summary: str,
    start: datetime,
    end: datetime,
    location: str = "",
    now: datetime | None = None,
) -> str:
    """One VEVENT wrapped in a VCALENDAR, CRLF-terminated per RFC 5545."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_stamp(now or datetime.now(UTC))}",
        f"DTSTART:{_stamp(start)}",
        f"DTEND:{_stamp(end)}",
        f"SUMMARY:{_escape(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def event_url(collection_url: str, uid: str) -> str:
    """Where the event resource lives. CalDAV addresses an event by its
    own URL inside the collection, so the client chooses it."""
    base = collection_url.split("?", 1)[0]
    if not base.endswith("/"):
        base += "/"
    return urllib.parse.urljoin(base, f"{uid}.ics")


def put_event(
    collection_url: str,
    ics: str,
    uid: str,
    *,
    opener: Any,
    timeout: int = TIMEOUT_S,
) -> str:
    """PUT the event and return its URL.

    ``If-None-Match: *`` makes this a create, never an overwrite: a UID
    collision fails instead of silently replacing somebody's meeting.
    """
    url = event_url(collection_url, uid)
    request = urllib.request.Request(
        url,
        data=ics.encode("utf-8"),
        method="PUT",
        headers={
            "Content-Type": "text/calendar; charset=utf-8",
            "If-None-Match": "*",
            "User-Agent": "tesserae/1.0 (+rooms)",
        },
    )
    try:
        with opener.open(request, timeout=timeout) as resp:
            status = getattr(resp, "status", 0) or resp.getcode()
    except urllib.error.HTTPError as err:
        if err.code in (401, 403):
            raise CalDavWriteError("The calendar rejected the credentials on this feed.") from err
        if err.code == 412:
            raise CalDavWriteError("That slot was taken while booking.") from err
        if err.code == 405:
            raise CalDavWriteError(
                "The calendar refused a write. This feed may be an export-only URL "
                "rather than a CalDAV collection."
            ) from err
        raise CalDavWriteError(f"The calendar returned HTTP {err.code}.") from err
    except urllib.error.URLError as err:
        raise CalDavWriteError(f"Could not reach the calendar: {err.reason}") from err
    except Exception as err:  # surfaced to the operator, never a stack trace
        raise CalDavWriteError(f"{type(err).__name__}: {err}") from err
    if status not in (200, 201, 204):
        raise CalDavWriteError(f"The calendar returned HTTP {status}.")
    return url


def new_uid() -> str:
    return f"{uuid.uuid4()}@tesserae"
