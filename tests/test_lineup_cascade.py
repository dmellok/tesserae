"""Taking a deleted dashboard out of the lineups that use it."""

from __future__ import annotations

from pathlib import Path

from app.lineup_cascade import deck_without_page, emptied_by, references, remove_page_from_lineups
from app.state.deck_model import Deck, DeckLink, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore


def _linked() -> Deck:
    return Deck(
        id="d",
        name="D",
        pages=[
            DeckPage(page_id="a", links=[DeckLink(target_page_id="b", button="right")]),
            DeckPage(page_id="b", links=[DeckLink(target_page_id="a", button="left")]),
        ],
        home_page_id="b",
        advance_fallback_page_id="b",
    )


def test_deck_without_page_drops_step_links_and_pointers() -> None:
    deck = deck_without_page(_linked(), "b")
    assert deck is not None
    assert deck.page_ids == ["a"] and deck.pages[0].links == []
    assert deck.home_page_id is None and deck.advance_fallback_page_id is None


def test_deck_without_its_only_page_is_none() -> None:
    deck = Deck(id="d", name="D", pages=[DeckPage(page_id="a")])
    assert deck_without_page(deck, "a") is None


def test_fallback_outside_the_deck_is_cleared() -> None:
    deck = Deck(id="d", name="D", pages=[DeckPage(page_id="a")], advance_fallback_page_id="z")
    assert references(deck, "z")
    cleaned = deck_without_page(deck, "z")
    assert cleaned is not None and cleaned.advance_fallback_page_id is None
    assert cleaned.page_ids == ["a"]


def test_cascade_rewrites_removes_and_is_idempotent(tmp_path: Path) -> None:
    decks = DeckStore(tmp_path / "decks.json")
    nav = DeckNavStore(tmp_path / "nav.json")
    decks.upsert(_linked())
    decks.upsert(Deck(id="solo", name="Solo", pages=[DeckPage(page_id="b")]))
    decks.upsert(Deck(id="free", name="Free", pages=[DeckPage(page_id="x")]))
    nav.set("kobo", "d", "b")
    first = remove_page_from_lineups(decks, nav, "b")
    assert first.edited == ["D"] and first.removed == ["Solo"]
    assert decks.get("solo") is None and decks.get("free") is not None
    assert decks.get("d").page_ids == ["a"]
    assert nav.get("kobo") is None
    second = remove_page_from_lineups(decks, nav, "b")
    assert not second.touched


def test_cascade_without_stores_does_nothing() -> None:
    assert not remove_page_from_lineups(None, None, "b").touched


def test_emptied_by_names_lineups_whose_pages_are_all_selected(tmp_path: Path) -> None:
    decks = DeckStore(tmp_path / "decks.json")
    decks.upsert(Deck(id="solo", name="Solo", pages=[DeckPage(page_id="a")]))
    decks.upsert(Deck(id="pair", name="Pair", pages=[DeckPage(page_id="a"), DeckPage(page_id="b")]))
    assert emptied_by(decks, ["a"]) == ["Solo"]
    assert emptied_by(decks, ["a", "b"]) == ["Solo", "Pair"]
    assert emptied_by(decks, ["z"]) == [] and emptied_by(None, ["a"]) == []
