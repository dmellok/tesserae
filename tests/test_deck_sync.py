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
            "composition_digest": f"comp-{page_id}",
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
        # Synthesized default: the graph declared no "left", so the
        # manifest fills prev-in-deck-order (wrapping).
        {"button": "left", "zone": None, "target_page_id": "weather"},
        # Explicit direction-named button links mirror as swipe entries;
        # the silent direction gets the paging default (left = next).
        {"swipe": "right", "zone": None, "target_page_id": "weather"},
        {"swipe": "left", "zone": None, "target_page_id": "weather"},
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


# -- default navigation links (firmware bench: graph-less decks) -----------


def _linkless_deck(n: int = 3) -> Deck:
    return Deck(
        id="plain",
        name="Plain",
        device_ids=["frame01"],
        pages=[DeckPage(page_id=f"p{i}") for i in range(n)],
    )


def test_manifest_defaults_buttons_prev_next_wrapping(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _linkless_deck()
    for p in deck.pages:
        source.seed("frame01", p.page_id, f"frame-{p.page_id}".encode())
    manifest = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    by_id = {p["page_id"]: p["links"] for p in manifest["pages"]}
    buttons0 = {
        (link["button"], link["target_page_id"]) for link in by_id["p0"] if link.get("button")
    }
    assert buttons0 == {("left", "p2"), ("right", "p1")}  # left wraps
    buttons2 = {
        (link["button"], link["target_page_id"]) for link in by_id["p2"] if link.get("button")
    }
    assert buttons2 == {("left", "p1"), ("right", "p0")}  # right wraps


def test_manifest_default_zones_only_on_touch_without_regions(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _linkless_deck(2)
    for p in deck.pages:
        source.seed("frame01", p.page_id, f"frame-{p.page_id}".encode())

    # Touch panel, page has no markup touch regions: half-zones appear.
    manifest = deck_sync.build_manifest(
        deck,
        "frame01",
        push_mgr=source,
        renders_dir=tmp_path,
        warm_missing=False,
        touch=True,
        regions_lookup=lambda comp: [],
    )
    zones = [link for link in manifest["pages"][0]["links"] if link["zone"] is not None]
    assert [(z["zone"]["x"], z["target_page_id"]) for z in zones] == [
        (0.0, "p1"),
        (0.5, "p1"),
    ]

    # Page HAS touch regions: no default zones (they'd swallow the
    # page's own tap targets device-side), buttons still filled.
    manifest2 = deck_sync.build_manifest(
        deck,
        "frame01",
        push_mgr=source,
        renders_dir=tmp_path,
        warm_missing=False,
        touch=True,
        regions_lookup=lambda comp: [{"x": 1, "y": 1, "w": 2, "h": 2}],
    )
    assert all(link["zone"] is None for link in manifest2["pages"][0]["links"])

    # Non-touch device: never zones.
    manifest3 = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False, touch=False
    )
    assert all(link["zone"] is None for link in manifest3["pages"][0]["links"])


def test_single_page_deck_gets_no_default_links(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _linkless_deck(1)
    source.seed("frame01", "p0", b"frame-p0")
    manifest = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False, touch=True
    )
    assert manifest["pages"][0]["links"] == []


def test_explicit_button_links_win_over_defaults(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = Deck(
        id="mix",
        name="Mix",
        device_ids=["frame01"],
        pages=[
            DeckPage(page_id="a", links=[DeckLink(target_page_id="c", button="right")]),
            DeckPage(page_id="b"),
            DeckPage(page_id="c"),
        ],
    )
    for pid in ("a", "b", "c"):
        source.seed("frame01", pid, f"frame-{pid}".encode())
    manifest = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    a_links = manifest["pages"][0]["links"]
    rights = [link for link in a_links if link.get("button") == "right"]
    assert rights == [{"button": "right", "zone": None, "target_page_id": "c"}]  # explicit, no dup
    lefts = [link for link in a_links if link.get("button") == "left"]
    assert lefts == [{"button": "left", "zone": None, "target_page_id": "c"}]  # default wrap


# -- swipe triggers, home block, capacity trim (deck editor backend) --------


def test_manifest_default_swipe_links(tmp_path: Path) -> None:
    """Paging convention: swipe left pulls the NEXT page in, swipe
    right goes back. Explicit direction-named button links mirror as
    swipe entries with the author's own direction."""
    source = FakeRenderSource(tmp_path)
    deck = _linkless_deck(3)
    for p in deck.pages:
        source.seed("frame01", p.page_id, f"frame-{p.page_id}".encode())
    manifest = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    p0 = manifest["pages"][0]["links"]
    swipes = {link["swipe"]: link["target_page_id"] for link in p0 if link.get("swipe")}
    assert swipes == {"left": "p1", "right": "p2"}  # left=next, right=prev(wrap)


def test_manifest_home_block_only_when_timeout_set(tmp_path: Path) -> None:
    source = FakeRenderSource(tmp_path)
    deck = _linkless_deck(2)
    for p in deck.pages:
        source.seed("frame01", p.page_id, f"frame-{p.page_id}".encode())
    m = deck_sync.build_manifest(
        deck, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    assert "home" not in m

    deck2 = deck.model_copy(update={"home_page_id": "p1", "home_timeout_minutes": 15})
    m2 = deck_sync.build_manifest(
        deck2, "frame01", push_mgr=source, renders_dir=tmp_path, warm_missing=False
    )
    assert m2["home"] == {"page_id": "p1", "timeout_s": 900}
    assert m2["version"] != m["version"]  # home rides the version digest


def test_manifest_capacity_marks_far_pages_uncached(tmp_path: Path) -> None:
    """Ring-from-home priority: with room for only 2 of 4 frames, home
    and its nearest neighbour cache; the far pages get cache=false but
    keep their links."""
    source = FakeRenderSource(tmp_path)
    deck = _linkless_deck(4).model_copy(update={"home_page_id": "p0"})
    payloads = {p.page_id: b"x" * 100 for p in deck.pages}
    for pid, payload in payloads.items():
        source.seed("frame01", pid, payload)
    manifest = deck_sync.build_manifest(
        deck,
        "frame01",
        push_mgr=source,
        renders_dir=tmp_path,
        warm_missing=False,
        capacity_bytes=250,  # fits 2 x 100-byte frames + change
    )
    by_id = {p["page_id"]: p for p in manifest["pages"]}
    cached = {pid for pid, p in by_id.items() if p.get("cache") is not False}
    # Home (p0) always first; ring distance 1 = p1 and p3 (tie -> lower
    # index wins), so p1 caches and p2/p3 spill.
    assert "p0" in cached and "p1" in cached
    assert by_id["p2"].get("cache") is False
    assert by_id["p3"].get("cache") is False
    assert by_id["p2"]["links"]  # links survive for network-fallback nav


def test_deck_model_home_validation() -> None:
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError):
        Deck(
            id="bad",
            name="Bad",
            pages=[DeckPage(page_id="a")],
            home_page_id="not_a_page",
        )
    deck = Deck(
        id="ok",
        name="Ok",
        pages=[DeckPage(page_id="a"), DeckPage(page_id="b")],
        home_page_id="b",
    )
    assert deck.resolved_home_page_id == "b"
    assert deck.resolved_entry_page_id == "b"  # entry defaults to home
