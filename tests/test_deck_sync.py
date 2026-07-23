"""Unit tests for app.deck_sync: capability parsing, TTLs, manifest
building, version digests, and digest-addressed frame lookup. The REST
endpoints over these are covered in tests/test_rest_deck.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app import deck_sync
from app.state.deck_model import Deck, DeckLink, DeckPage, DeckZone


class FakeRenderSource:
    """Stands in for PushManager: deck_render_for + warm_deck_page."""

    def __init__(self, renders_dir: Path) -> None:
        self.renders_dir = renders_dir
        self.renders: dict[tuple[str, str], dict[str, Any]] = {}
        self.warmed: list[tuple[str, str]] = []

    def seed(self, device_id: str, page_id: str, payload: bytes) -> str:
        digest = hashlib.sha256(payload).hexdigest()[:16]
        filename = f"{digest}.bin"
        (self.renders_dir / filename).write_bytes(payload)
        self.renders[(device_id, page_id)] = {
            "digest": digest,
            "ext": "bin",
            "filename": filename,
        }
        return digest

    def deck_render_for(self, device_id: str, page_id: str) -> dict[str, Any] | None:
        info = self.renders.get((device_id, page_id))
        return dict(info) if info is not None else None

    def warm_deck_page(self, page_id: str, device_id: str) -> bool:
        self.warmed.append((device_id, page_id))
        self.seed(device_id, page_id, f"warmed-{page_id}".encode())
        return True


def _deck(**overrides: Any) -> Deck:
    base: dict[str, Any] = {
        "id": "kitchen",
        "name": "Kitchen",
        "device_ids": ["frame01"],
        "refresh_interval_minutes": 15,
        "pages": [
            DeckPage(
                page_id="overview",
                links=[
                    DeckLink(target_page_id="weather", button="right"),
                    DeckLink(
                        target_page_id="weather",
                        zone=DeckZone(x=0.0, y=0.0, w=0.5, h=0.5),
                    ),
                ],
            ),
            DeckPage(page_id="weather", links=[DeckLink(target_page_id="overview", button="left")]),
        ],
    }
    base.update(overrides)
    return Deck(**base)


# -- advertised_deck_cache ----------------------------------------------


def test_capability_parses_valid_shapes() -> None:
    cap = {"deck_cache": {"schema": 1, "capacity_bytes": 7_900_000}}
    expected = {"schema": 1, "capacity_bytes": 7_900_000}
    assert deck_sync.advertised_deck_cache(cap) == expected
    assert deck_sync.advertised_deck_cache(
        b'{"deck_cache": {"schema": 1, "capacity_bytes": 0}}'
    ) == {
        "schema": 1,
        "capacity_bytes": 0,
    }


def test_capability_rejects_malformed() -> None:
    for payload in (
        {},
        {"deck_cache": None},
        {"deck_cache": {}},
        {"deck_cache": {"schema": 0, "capacity_bytes": 1}},
        {"deck_cache": {"schema": True, "capacity_bytes": 1}},
        {"deck_cache": {"schema": 1, "capacity_bytes": -1}},
        {"deck_cache": {"schema": 1, "capacity_bytes": "big"}},
        {"deck_cache": {"schema": "1", "capacity_bytes": 1}},
        b"not json",
        b"[]",
    ):
        assert deck_sync.advertised_deck_cache(payload) is None


# -- page_ttl_s ----------------------------------------------------------


def test_page_ttl_uses_override_then_deck_default_then_day() -> None:
    deck = _deck()
    page = deck.pages[0]
    assert deck_sync.page_ttl_s(deck, page) == 15 * 60
    override = DeckPage(page_id="x", refresh_interval_minutes=5)
    assert deck_sync.page_ttl_s(deck, override) == 5 * 60
    no_refresh_deck = _deck(refresh_interval_minutes=0)
    assert deck_sync.page_ttl_s(no_refresh_deck, no_refresh_deck.pages[0]) == 86400


# -- build_manifest / version_digest --------------------------------------


def test_manifest_shape_and_stable_version(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _deck()
    d1 = source.seed("frame01", "overview", b"frame-overview")
    source.seed("frame01", "weather", b"frame-weather")

    m1 = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    m2 = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    assert m1 == m2
    assert m1["deck_id"] == "kitchen"
    assert m1["entry_page_id"] == "overview"
    assert len(m1["version"]) == 16
    overview = m1["pages"][0]
    assert overview["page_id"] == "overview"
    assert overview["digest"] == d1
    assert overview["bytes"] == len(b"frame-overview")
    assert overview["ttl_s"] == 900
    assert overview["links"] == [
        {"button": "right", "zone": None, "target_page_id": "weather"},
        {
            "button": None,
            "zone": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
            "target_page_id": "weather",
        },
    ]
    assert source.warmed == []  # nothing was cold


def test_version_changes_when_a_frame_changes(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _deck()
    source.seed("frame01", "overview", b"one")
    source.seed("frame01", "weather", b"frame-weather")
    v1 = deck_sync.current_version(deck, "frame01", push_mgr=source, renders_dir=tmp_path)
    source.seed("frame01", "overview", b"two")
    v2 = deck_sync.current_version(deck, "frame01", push_mgr=source, renders_dir=tmp_path)
    assert v1 != v2


def test_warm_missing_renders_cold_pages(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _deck()
    source.seed("frame01", "overview", b"frame-overview")

    manifest = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=True
    )
    assert source.warmed == [("frame01", "weather")]
    weather = manifest["pages"][1]
    assert weather["digest"] != "" and weather["bytes"] > 0


def test_cold_pages_contribute_empty_digest_without_warming(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _deck()
    source.seed("frame01", "overview", b"frame-overview")

    manifest = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    assert source.warmed == []
    assert manifest["pages"][1]["digest"] == ""
    assert manifest["pages"][1]["bytes"] == 0


# -- frame_entry_by_digest -------------------------------------------------


def test_frame_entry_by_digest_matches_and_misses(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _deck()
    digest = source.seed("frame01", "overview", b"frame-overview")

    hit = deck_sync.frame_entry_by_digest(deck, "frame01", digest, push_mgr=source)
    assert hit is not None and hit["digest"] == digest
    assert deck_sync.frame_entry_by_digest(deck, "frame01", "0" * 16, push_mgr=source) is None
    assert deck_sync.frame_entry_by_digest(deck, "frame01", "", push_mgr=source) is None
