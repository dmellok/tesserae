"""#167 Phase 4: rotate_next / rotate_prev / step:<n> act on a bound TIMED
deck when no rotation targets the device, through the same engine adapter the
scheduler uses. Covers the fallback resolution, priority arbitration, the
/frame envelope carrying the deck id, and manual decks staying graph-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.button_service import ButtonService
from app.push import PushResult
from app.state.deck_model import Deck, DeckPage
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.legacy_projections import RotationProjection
from app.state.page_store import Page, PageStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.settings_store import SettingsStore


@dataclass
class StubPushManager:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def latest_render_for(self, _device_id: str) -> dict[str, Any] | None:
        return None

    def push(
        self,
        page_id: str,
        *,
        device_ids: set[str] | None = None,
        respect_quiet_hours: bool = False,
        source: str = "page",
    ) -> PushResult:
        self.calls.append({"page_id": page_id, "device_ids": device_ids, "source": source})
        return PushResult(status="sent", page_id=page_id)


def _timed_deck(did: str = "cycle", priority: int = 0, advance: str = "timer") -> Deck:
    return Deck(
        id=did,
        name=did.title(),
        device_ids=["kitchen"],
        pages=[DeckPage(page_id="morning"), DeckPage(page_id="afternoon")],
        advance=advance,
        advance_priority=priority,
    )


@pytest.fixture
def wiring(tmp_path: Path):
    deck_store = DeckStore(tmp_path / "decks.json")
    page_store = PageStore(tmp_path / "pages.json")
    for pid in ("morning", "afternoon"):
        page_store.save(Page(id=pid, name=pid.title(), device_ids=["kitchen"]))
    push = StubPushManager()
    service = ButtonService(
        rotation_store=RotationProjection(deck_store),
        state_store=DeviceRotationStateStore(tmp_path / "state.json"),
        settings_store=SettingsStore(tmp_path / "settings.json"),
        page_store=page_store,
        push_manager=push,  # type: ignore[arg-type]
        deck_store=deck_store,
        clock=lambda: datetime(2026, 7, 3, 12, 0, tzinfo=UTC),
    )
    return service, push, deck_store


def test_rotate_next_advances_a_bound_timed_deck(wiring) -> None:
    service, push, decks = wiring
    decks.upsert(_timed_deck())
    result = service.handle_button(device_id="kitchen", button="right", event_id=1)
    assert result.rotation_id == "cycle"
    assert result.step_count == 2
    assert result.manual_override is True
    assert push.calls and push.calls[-1]["device_ids"] == {"kitchen"}


def test_envelope_reports_the_timed_deck_on_plain_wakes(wiring) -> None:
    service, _push, decks = wiring
    decks.upsert(_timed_deck())
    snap = service.snapshot("kitchen")
    assert snap.rotation_id == "cycle"
    assert snap.step_count == 2


def test_manual_decks_stay_graph_only(wiring) -> None:
    service, push, decks = wiring
    decks.upsert(_timed_deck(advance="manual"))
    result = service.handle_button(device_id="kitchen", button="right", event_id=1)
    assert result.rotation_id is None
    assert not push.calls  # rotate_next no-ops without a timed target


def test_real_rotation_outranks_the_timed_deck(wiring) -> None:
    service, _push, decks = wiring
    decks.upsert(_timed_deck())
    RotationProjection(decks).upsert(
        Rotation(
            id="legacy",
            name="Legacy",
            device_ids=["kitchen"],
            steps=[
                RotationStep(page_id="morning", dwell_minutes=15),
                RotationStep(page_id="afternoon", dwell_minutes=15),
            ],
        )
    )
    snap = service.snapshot("kitchen")
    assert snap.rotation_id == "legacy"


def test_highest_advance_priority_deck_wins(wiring) -> None:
    service, _push, decks = wiring
    decks.upsert(_timed_deck("low", priority=0))
    decks.upsert(_timed_deck("high", priority=5))
    assert service.snapshot("kitchen").rotation_id == "high"
