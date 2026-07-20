"""Per-device deck navigation state store."""

from __future__ import annotations

from pathlib import Path

from app.state.deck_nav_store import DeckNavStore


def test_absent_is_none(tmp_path: Path) -> None:
    store = DeckNavStore(tmp_path / "nav.json")
    assert store.get("dev") is None
    assert store.current_page("dev", "deck1") is None


def test_set_get_current_page(tmp_path: Path) -> None:
    store = DeckNavStore(tmp_path / "nav.json")
    store.set("dev", "deck1", "overview")
    assert store.current_page("dev", "deck1") == "overview"
    store.set("dev", "deck1", "weather")
    assert store.current_page("dev", "deck1") == "weather"


def test_current_page_guards_against_other_deck(tmp_path: Path) -> None:
    store = DeckNavStore(tmp_path / "nav.json")
    store.set("dev", "deck1", "overview")
    # A record for deck1 must not answer for deck2 (device moved decks).
    assert store.current_page("dev", "deck2") is None


def test_clear(tmp_path: Path) -> None:
    store = DeckNavStore(tmp_path / "nav.json")
    store.set("dev", "deck1", "overview")
    assert store.clear("dev") is True
    assert store.get("dev") is None
    assert store.clear("dev") is False
