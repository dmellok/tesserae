"""Deck model validation + graph resolution, and the deck store."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.state.deck_model import Deck, DeckLink, DeckPage, DeckZone
from app.state.deck_store import DeckStore


def _deck(**over) -> Deck:
    base = dict(
        id="kitchen_deck",
        name="Kitchen",
        device_ids=["kitchen_panel"],
        pages=[
            DeckPage(
                page_id="overview",
                links=[
                    DeckLink(target_page_id="calendar", button="right"),
                    DeckLink(target_page_id="weather", zone=DeckZone(x=0.0, y=0.0, w=0.5, h=0.5)),
                ],
            ),
            DeckPage(
                page_id="calendar", links=[DeckLink(target_page_id="overview", button="left")]
            ),
            DeckPage(page_id="weather", links=[DeckLink(target_page_id="overview", button="left")]),
        ],
    )
    base.update(over)
    return Deck(**base)


def test_valid_deck_resolves_entry_and_pages() -> None:
    deck = _deck()
    assert deck.resolved_entry_page_id == "overview"
    assert deck.page_ids == ["overview", "calendar", "weather"]
    assert deck.page("calendar") is not None
    assert deck.page("nope") is None


def test_explicit_entry_page() -> None:
    deck = _deck(entry_page_id="calendar")
    assert deck.resolved_entry_page_id == "calendar"


def test_button_and_zone_resolution() -> None:
    deck = _deck()
    assert deck.resolve_button("overview", "right") == "calendar"
    assert deck.resolve_button("overview", "left") is None
    # Point inside the top-left quadrant hits the weather zone.
    assert deck.resolve_zone("overview", 0.1, 0.1) == "weather"
    # Point outside every zone misses.
    assert deck.resolve_zone("overview", 0.9, 0.9) is None


def test_link_needs_exactly_one_trigger() -> None:
    with pytest.raises(ValidationError):
        DeckLink(target_page_id="x")  # neither
    with pytest.raises(ValidationError):
        DeckLink(target_page_id="x", button="a", zone=DeckZone(x=0, y=0, w=0.1, h=0.1))  # both


def test_zone_must_fit_panel() -> None:
    with pytest.raises(ValidationError):
        DeckZone(x=0.8, y=0.0, w=0.5, h=0.1)  # x+w > 1.0


def test_link_target_must_be_a_page() -> None:
    with pytest.raises(ValidationError):
        Deck(
            id="d",
            name="d",
            pages=[DeckPage(page_id="a", links=[DeckLink(target_page_id="ghost", button="right")])],
        )


def test_entry_page_must_be_a_page() -> None:
    with pytest.raises(ValidationError):
        Deck(id="d", name="d", entry_page_id="ghost", pages=[DeckPage(page_id="a")])


def test_duplicate_page_id_rejected() -> None:
    with pytest.raises(ValidationError):
        Deck(id="d", name="d", pages=[DeckPage(page_id="a"), DeckPage(page_id="a")])


def test_single_page_deck_is_legal() -> None:
    deck = Deck(id="d", name="d", pages=[DeckPage(page_id="solo")])
    assert deck.resolved_entry_page_id == "solo"


def test_refresh_interval_bounds() -> None:
    assert _deck(refresh_interval_minutes=0).refresh_interval_minutes == 0
    with pytest.raises(ValidationError):
        _deck(refresh_interval_minutes=99999)


def test_deck_page_refresh_override() -> None:
    assert DeckPage(page_id="a", refresh_interval_minutes=5).effective_refresh_minutes(15) == 5
    assert DeckPage(page_id="b").effective_refresh_minutes(15) == 15  # inherits deck default
    assert DeckPage(page_id="c", refresh_interval_minutes=0).effective_refresh_minutes(15) == 0
    with pytest.raises(ValidationError):
        DeckPage(page_id="d", refresh_interval_minutes=99999)


def test_store_round_trip(tmp_path: Path) -> None:
    store = DeckStore(tmp_path / "decks.json")
    assert store.all() == []
    store.upsert(_deck())
    got = store.get("kitchen_deck")
    assert got is not None and got.name == "Kitchen"
    assert [d.id for d in store.for_device("kitchen_panel")] == ["kitchen_deck"]
    assert store.for_device("other") == []


def test_store_upsert_replaces_and_delete(tmp_path: Path) -> None:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(_deck())
    store.upsert(_deck(name="Renamed"))
    assert store.get("kitchen_deck").name == "Renamed"
    assert len(store.all()) == 1
    assert store.delete("kitchen_deck") is True
    assert store.delete("kitchen_deck") is False


def test_store_disabled_deck_excluded_from_for_device(tmp_path: Path) -> None:
    store = DeckStore(tmp_path / "decks.json")
    store.upsert(_deck(enabled=False))
    assert store.for_device("kitchen_panel") == []
