"""Pydantic model for decks.

A deck is a small navigable graph of pages that Tesserae keeps pre-rendered
per bound device, so a button press or touch that moves between them serves an
already-rendered frame instead of rendering on the fly. Unlike a rotation
(which advances on a wall-clock timer), a deck is navigated by hand; the only
time-driven part is an optional periodic refresh that re-renders its pages in
the background so their data stays current.

Shape::

    Deck(
        id="kitchen_deck",
        name="Kitchen deck",
        device_ids=["kitchen_panel"],
        entry_page_id="overview",
        refresh_interval_minutes=15,
        pages=[
            DeckPage(page_id="overview", links=[
                DeckLink(target_page_id="calendar", button="right"),
                DeckLink(target_page_id="weather", zone=DeckZone(x=0.0, y=0.0, w=0.5, h=0.5)),
            ]),
            DeckPage(page_id="calendar", links=[
                DeckLink(target_page_id="overview", button="left"),
            ]),
            DeckPage(page_id="weather", links=[
                DeckLink(target_page_id="overview", button="left"),
            ]),
        ],
    )

A link fires on exactly one trigger: a physical ``button`` name, or a touch
``zone`` (a rectangle in normalised 0..1 panel coordinates). The graph is
directed; a page reachable only *into* is fine, but the editor should warn on
pages with no way back.

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.state.conditions import Condition

# 0=Mon .. 6=Sun (ISO weekday minus 1), mirrored from Rotation for the timer
# advance day-of-week filter.
_DAYS_ALL: list[int] = [0, 1, 2, 3, 4, 5, 6]


class DeckZone(BaseModel):
    """A touch target as a rectangle in normalised panel coordinates (0..1),
    so a link is independent of the panel's pixel size."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _within_bounds(self) -> DeckZone:
        if self.x + self.w > 1.0 + 1e-6 or self.y + self.h > 1.0 + 1e-6:
            raise ValueError("zone extends past the panel edge (x+w or y+h > 1.0)")
        return self


class DeckLink(BaseModel):
    """One directed edge in the deck graph: a trigger on the source page that
    navigates to ``target_page_id``. Exactly one of ``button`` / ``zone``."""

    model_config = ConfigDict(extra="forbid")

    target_page_id: str = Field(min_length=1)
    button: str | None = None
    zone: DeckZone | None = None

    @model_validator(mode="after")
    def _exactly_one_trigger(self) -> DeckLink:
        if (self.button is None) == (self.zone is None):
            raise ValueError("a link needs exactly one of 'button' or 'zone'")
        if self.button is not None and not self.button.strip():
            raise ValueError("button must be a non-empty name")
        return self


class DeckPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_id: str = Field(min_length=1)
    links: list[DeckLink] = Field(default_factory=list)

    # Per-page background refresh cadence, overriding the deck's default. None
    # inherits ``Deck.refresh_interval_minutes``; 0 means the scheduler doesn't
    # warm this page (it warms lazily on first navigation), so use a large
    # interval for a page that should be pre-warmed but seldom refreshed. Lets a
    # volatile tile refresh often while a photo page refreshes rarely in the
    # same deck.
    refresh_interval_minutes: int | None = Field(default=None, ge=0, le=1440)

    # Timer-advance dwell (Phase 1 of the rotations merge): how long this page is
    # shown before the deck advances to the next, when the deck's ``advance`` is
    # ``timer`` / ``both``. None inherits ``Deck.advance_interval_minutes``. Unused
    # when the deck advances on tap only.
    dwell_minutes: int | None = Field(default=None, ge=1, le=10_080)

    # Timer-advance conditions (rotations-merge parity): AND'd predicates that
    # gate this page in the cycle. An unmet condition advances past it in
    # ``scheduled`` mode, or makes it ineligible in ``priority`` mode. Empty
    # (default) = always eligible.
    conditions: list[Condition] = Field(default_factory=list)

    def effective_refresh_minutes(self, deck_default: int) -> int:
        """The refresh cadence to use for this page: its own override, else the
        deck default."""
        return (
            self.refresh_interval_minutes
            if self.refresh_interval_minutes is not None
            else deck_default
        )

    def effective_dwell_minutes(self, deck_default: int) -> int:
        """The timer-advance dwell for this page: its own override, else the deck
        default advance interval."""
        return self.dwell_minutes if self.dwell_minutes is not None else deck_default


class Deck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    enabled: bool = True

    # Devices this deck is pre-rendered for and navigable on. Empty means the
    # deck is defined but not bound yet (nothing is warmed).
    device_ids: list[str] = Field(default_factory=list)

    # The graph. At least one page; a single-page deck is legal (just a warmed
    # page with no navigation).
    pages: list[DeckPage] = Field(min_length=1)

    # Which page a device lands on when it first enters the deck. Defaults to
    # the first page when unset.
    entry_page_id: str | None = None

    # Home card (deck editor redesign): the page the deck RETURNS to
    # after ``home_timeout_minutes`` of no interaction, and the page
    # the Push action sends to the panel first. ``None`` = the first
    # page. ``home_timeout_minutes`` 0 = never return automatically.
    # The timeout counts from the last button press or tap (the nav
    # record's updated_at); enforced server-side by the scheduler for
    # server-navigated devices and shipped in the sync manifest's
    # ``home`` block so SD-cache firmware can enforce it offline.
    home_page_id: str | None = None
    home_timeout_minutes: int = Field(default=0, ge=0, le=120)

    # Background re-render cadence in minutes so the pre-rendered pages keep
    # their data current. 0 disables periodic refresh (pages are warmed once
    # and only re-rendered when their data is pushed by other means).
    refresh_interval_minutes: int = Field(default=15, ge=0, le=1440)

    # How the deck advances between pages (Phase 1 of the rotations merge):
    #   manual — move on a tap / button / swipe (the classic deck).
    #   timer  — auto-cycle through the pages in order on a wall-clock anchor
    #            (what a Rotation did); links are ignored for advancing.
    #   both   — auto-cycle AND accept taps.
    # ``manual`` (the default) keeps every existing deck behaving exactly as before.
    advance: Literal["manual", "timer", "both"] = "manual"

    # Default per-step dwell for timer advance, in minutes. A page can override
    # it via ``DeckPage.dwell_minutes``. Only used when ``advance`` != ``manual``.
    advance_interval_minutes: int = Field(default=30, ge=1, le=10_080)

    # Daily re-anchor for timer advance (local HH:MM). The cycle reseeds at this
    # wall-clock moment each day, so DST flips and long cycles stay aligned, same
    # semantics as the old Rotation anchor. Only used when ``advance`` != ``manual``.
    advance_anchor: str = "00:00"

    # Timer-advance parity with rotations (Tier A + B). All only apply when
    # ``advance`` != ``manual``; defaults reproduce a plain daily cycle.
    advance_end_at: str | None = None  # optional daily stop (HH:MM); None = until midnight
    advance_days_of_week: list[int] = Field(default_factory=lambda: list(_DAYS_ALL))
    advance_priority: int = 0  # vs schedules + other timer decks when both are due
    advance_smart_sync: bool = False
    advance_smart_sync_lead_s: int = Field(default=10, ge=0, le=600)
    advance_mode: Literal["scheduled", "priority"] = "scheduled"
    advance_min_hold_minutes: int = Field(default=5, ge=0, le=120)

    # Trigger shape for timer advance (#167 Phase 2). ``cycle`` is the classic
    # anchor-based dwell cycle (what rotations do). ``interval`` fires the
    # eligible page on a cooldown floor inside an optional time-of-day window,
    # drifting with actual fire times rather than aligning to the anchor, so a
    # migrated interval schedule keeps its exact cadence semantics. ``daily``
    # fires once per local day at ``advance_fires_at`` with the schedule
    # backfill guard. Only used when ``advance`` != ``manual``.
    advance_trigger: Literal["cycle", "interval", "daily"] = "cycle"

    # ``daily`` trigger: local HH:MM fire time. Required for that trigger.
    advance_fires_at: str | None = None

    # ``interval`` trigger: optional HH:MM window, wrap-around allowed
    # (22:00 -> 06:00 spans midnight). Same semantics as the old
    # Schedule.time_of_day_start/_end, deliberately NOT the anchor gate
    # (which treats before-anchor as inactive rather than wrapping).
    advance_window_start: str | None = None
    advance_window_end: str | None = None

    # When no page's conditions pass, fire this page instead of holding. A
    # fire target rather than a nav node, so it may reference a page outside
    # the deck, matching Schedule.fallback_page_id.
    advance_fallback_page_id: str | None = None

    # Set on records migrated from the legacy rotation / schedule stores so
    # the compatibility projections know which UI / API surface owns them.
    # None for decks authored as decks.
    legacy_kind: Literal["rotation", "schedule"] | None = None

    @field_validator("advance_fires_at", "advance_window_start", "advance_window_end")
    @classmethod
    def _validate_trigger_times(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("trigger times must be 'HH:MM' 24-hour or empty")
        return v

    @model_validator(mode="after")
    def _validate_trigger(self) -> Deck:
        if self.advance_trigger == "daily" and self.advance_fires_at is None:
            raise ValueError("advance_trigger 'daily' requires advance_fires_at")
        return self

    @field_validator("advance_anchor")
    @classmethod
    def _validate_advance_anchor(cls, v: str) -> str:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("advance_anchor must be 'HH:MM' 24-hour")
        return v

    @field_validator("advance_end_at")
    @classmethod
    def _validate_advance_end_at(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise ValueError("advance_end_at must be 'HH:MM' 24-hour or empty")
        return v

    @field_validator("advance_days_of_week")
    @classmethod
    def _validate_advance_dow(cls, v: list[int]) -> list[int]:
        if not all(0 <= d <= 6 for d in v):
            raise ValueError("advance_days_of_week entries must be 0..6 (0=Mon, 6=Sun)")
        return sorted(set(v))

    @model_validator(mode="after")
    def _validate_graph(self) -> Deck:
        page_ids = [p.page_id for p in self.pages]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("duplicate page_id in deck pages")
        known = set(page_ids)
        for page in self.pages:
            for link in page.links:
                if link.target_page_id not in known:
                    raise ValueError(
                        f"link target {link.target_page_id!r} is not a page in this deck"
                    )
        if self.entry_page_id is not None and self.entry_page_id not in known:
            raise ValueError(f"entry_page_id {self.entry_page_id!r} is not a page in this deck")
        if self.home_page_id is not None and self.home_page_id not in known:
            raise ValueError(f"home_page_id {self.home_page_id!r} is not a page in this deck")
        return self

    @property
    def resolved_home_page_id(self) -> str:
        """The home card: the explicit home, else the first page."""
        return self.home_page_id or self.pages[0].page_id

    @property
    def resolved_entry_page_id(self) -> str:
        """The landing page for a freshly-bound device: the explicit
        entry, else home (which itself defaults to the first page)."""
        return self.entry_page_id or self.resolved_home_page_id

    @property
    def page_ids(self) -> list[str]:
        return [p.page_id for p in self.pages]

    @property
    def advance_cycle_minutes(self) -> int:
        """Total timer-advance cycle length: the sum of each page's effective
        dwell (its override, else ``advance_interval_minutes``)."""
        return sum(p.effective_dwell_minutes(self.advance_interval_minutes) for p in self.pages)

    def advance_page_at(self, offset_minutes: float) -> str:
        """The page id shown at ``offset_minutes`` into the timer cycle. Wraps at
        the cycle length; used by the scheduler for ``timer`` / ``both`` decks."""
        if not self.pages:
            return ""
        cycle = self.advance_cycle_minutes
        if cycle <= 0:
            return self.pages[0].page_id
        pos = offset_minutes % cycle
        acc = 0.0
        for p in self.pages:
            acc += p.effective_dwell_minutes(self.advance_interval_minutes)
            if pos < acc:
                return p.page_id
        return self.pages[-1].page_id

    def page(self, page_id: str) -> DeckPage | None:
        for p in self.pages:
            if p.page_id == page_id:
                return p
        return None

    def resolve_button(self, page_id: str, button: str) -> str | None:
        """Target page id for a button press on ``page_id``, or None if this
        page has no link bound to that button."""
        page = self.page(page_id)
        if page is None:
            return None
        for link in page.links:
            if link.button is not None and link.button == button:
                return link.target_page_id
        return None

    def resolve_zone(self, page_id: str, nx: float, ny: float) -> str | None:
        """Target page id for a normalised touch point (``nx``/``ny`` in 0..1)
        on ``page_id``. First matching zone wins; None when no zone contains it."""
        page = self.page(page_id)
        if page is None:
            return None
        for link in page.links:
            z = link.zone
            if z is not None and z.x <= nx <= z.x + z.w and z.y <= ny <= z.y + z.h:
                return link.target_page_id
        return None
