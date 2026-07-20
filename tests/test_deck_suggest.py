"""Deck suggestions derived from page:<id> tap/swipe links across pages."""

from __future__ import annotations

from app.deck_suggest import suggest_decks
from app.state.deck_model import Deck, DeckLink, DeckPage
from app.state.page_store import Cell, Page


def _page(pid: str, name: str, cells: list[Cell], devices: tuple[str, ...] = ("panel",)) -> Page:
    return Page(id=pid, name=name, device_ids=list(devices), cells=cells)


def test_suggests_deck_from_tap_links_with_zones() -> None:
    pages = [
        _page(
            "overview",
            "Overview",
            [
                Cell(id="a", x=0, y=0, w=6, h=6, on_tap="page:calendar"),
                Cell(id="b", x=6, y=0, w=6, h=6, on_tap="page:weather"),
            ],
        ),
        _page("calendar", "Calendar", [Cell(id="c", x=0, y=0, w=12, h=1, on_tap="page:overview")]),
        _page("weather", "Weather", [Cell(id="d", x=0, y=0, w=12, h=1, on_tap="page:overview")]),
    ]
    out = suggest_decks(pages, [])
    assert len(out) == 1
    deck = out[0]
    assert set(deck.page_ids) == {"overview", "calendar", "weather"}
    assert deck.entry_page_id == "overview"  # highest out-degree (2 links)
    assert deck.device_ids == ["panel"]

    ov = deck.page("overview")
    assert ov is not None
    assert {lnk.target_page_id for lnk in ov.links} == {"calendar", "weather"}
    # The calendar tile occupies the left half (6 of 12 cols) -> zone x=0 w=0.5.
    zone = next(lnk.zone for lnk in ov.links if lnk.target_page_id == "calendar")
    assert zone is not None
    assert zone.x == 0.0 and zone.w == 0.5


def test_swipe_link_becomes_a_button_link() -> None:
    pages = [
        _page("a", "A", [Cell(id="c", x=0, y=0, w=12, h=12, on_swipe={"left": "page:b"})]),
        _page("b", "B", [Cell(id="c", x=0, y=0, w=12, h=12, on_swipe={"right": "page:a"})]),
    ]
    out = suggest_decks(pages, [])
    assert len(out) == 1
    a = out[0].page("a")
    assert a is not None
    assert a.links[0].button == "left" and a.links[0].target_page_id == "b"


def test_excludes_pages_already_in_a_deck() -> None:
    pages = [
        _page("a", "A", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:b")]),
        _page("b", "B", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:a")]),
    ]
    existing = [
        Deck(
            id="d",
            name="D",
            pages=[
                DeckPage(page_id="a", links=[DeckLink(target_page_id="b", button="right")]),
                DeckPage(page_id="b"),
            ],
        )
    ]
    assert suggest_decks(pages, existing) == []


def test_no_page_links_yields_nothing() -> None:
    assert suggest_decks([_page("a", "A", [Cell(id="c", x=0, y=0, w=12, h=12)])], []) == []


def test_self_link_is_not_a_cluster() -> None:
    pages = [_page("a", "A", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:a")])]
    assert suggest_decks(pages, []) == []


def test_two_separate_clusters() -> None:
    pages = [
        _page("a", "A", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:b")]),
        _page("b", "B", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:a")]),
        _page("x", "X", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:y")]),
        _page("y", "Y", [Cell(id="c", x=0, y=0, w=12, h=12, on_tap="page:x")]),
    ]
    out = suggest_decks(pages, [])
    assert len(out) == 2
    clusters = sorted(sorted(d.page_ids) for d in out)
    assert clusters == [["a", "b"], ["x", "y"]]
