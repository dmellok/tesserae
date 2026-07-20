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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    # Background re-render cadence in minutes so the pre-rendered pages keep
    # their data current. 0 disables periodic refresh (pages are warmed once
    # and only re-rendered when their data is pushed by other means).
    refresh_interval_minutes: int = Field(default=15, ge=0, le=1440)

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
        return self

    @property
    def resolved_entry_page_id(self) -> str:
        """The landing page: the explicit entry, else the first page."""
        return self.entry_page_id or self.pages[0].page_id

    @property
    def page_ids(self) -> list[str]:
        return [p.page_id for p in self.pages]

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
