"""Pydantic model for meeting rooms.

A room is configuration, never state. It names a calendar feed, the
panels showing it, and optionally an endpoint that books it. Everything
displayed is derived at render time from the feed; nothing about a
booking is stored here, so Tesserae never becomes the source of truth for
whether a room is free.

That constraint is what keeps the feature cheap to own. A room generates
an ordinary page bound to an ordinary device, and if rooms were deleted
tomorrow those pages would keep working exactly as a hand-composed one
does today.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

ROOM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Where a generated page's id comes from. Namespaced so a room can never
# collide with a hand-made page, and so orphan cleanup can recognise its
# own output without keeping a second index.
PAGE_ID_PREFIX = "room_"


def page_id_for(room_id: str) -> str:
    return f"{PAGE_ID_PREFIX}{room_id}"


class Room(BaseModel):
    """One meeting room.

    ``feed_id`` names a ``calendar_core`` feed. ``location_filter``
    handles the case where one calendar carries several rooms: events are
    matched on their ``LOCATION`` field, which is how Exchange and Google
    tag a room booking. Blank means "every event in this feed is this
    room", which is the normal one-calendar-per-room setup.

    ``book_url`` is a string, deliberately, not an integration. Tesserae
    POSTs to it and repaints; whatever owns the calendar does the booking,
    keeping identity, conflicts and availability out of here.
    """

    id: str
    name: str
    feed_id: str = ""
    location_filter: str = ""
    device_ids: list[str] = Field(default_factory=list)
    book_url: str = ""
    # Direct CalDAV booking (#90 phase 4). When on, a tap writes the event
    # into the room's own calendar instead of POSTing to book_url. Only
    # possible on a feed discovered over CalDAV, since an ICS export URL
    # is not writable, and only with basic/digest auth.
    book_caldav: bool = False
    book_minutes: int = 30
    book_summary: str = "Booked from the panel"
    enabled: bool = True
    # The page this room generated. Tracked so a rename or unbind can
    # update the right page, and so deleting the room can offer to remove
    # it. Empty until the room has been synced once.
    page_id: str = ""

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ROOM_ID_RE.match(v):
            raise ValueError(
                "room id must be lowercase letters, digits, underscore or hyphen (max 64)"
            )
        return v

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("room name is required")
        return v

    @field_validator("book_minutes")
    @classmethod
    def _check_book_minutes(cls, v: int) -> int:
        if not (5 <= v <= 480):
            raise ValueError("booking length must be between 5 and 480 minutes")
        return v

    @field_validator("book_url")
    @classmethod
    def _check_book_url(cls, v: str) -> str:
        v = v.strip()
        if v and not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("book URL must be http(s)")
        return v

    def resolved_page_id(self) -> str:
        return self.page_id or page_id_for(self.id)

    def to_json(self) -> dict[str, Any]:
        return self.model_dump()
