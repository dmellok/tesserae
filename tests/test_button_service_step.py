"""ButtonService.step: a remote (Home Assistant) next / previous for one
display. Same machinery as a physical left / right press, but it steps the
lineup the display is following right now, never consults the button map,
isn't deduplicated, leaves the panel's dedup fingerprint alone and emits no
input event."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.button_service import ButtonService
from app.push import PushResult
from app.state.deck_model import Deck, DeckLink, DeckPage
from app.state.deck_nav_store import DeckNavStore
from app.state.deck_store import DeckStore
from app.state.device_rotation_state_model import DeviceRotationState
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.event_log import EventLog
from app.state.legacy_projections import RotationProjection
from app.state.page_store import Page, PageStore
from app.state.settings_store import SettingsStore

NOW = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)


@dataclass
class FakePush:
    calls: list[dict[str, Any]] = field(default_factory=list)
    promotes: list[tuple[str, str]] = field(default_factory=list)
    warmed: set[tuple[str, str]] = field(default_factory=set)
    status: str = "sent"

    def push(
        self,
        page_id: str,
        *,
        device_ids: set[str] | None = None,
        respect_quiet_hours: bool = False,
        source: str = "page",
    ) -> PushResult:
        self.calls.append(
            {
                "page_id": page_id,
                "device_ids": device_ids,
                "respect_quiet_hours": respect_quiet_hours,
                "source": source,
            }
        )
        return PushResult(status=self.status, page_id=page_id, event_id=100 + len(self.calls))

    def promote_deck_page(self, device_id: str, page_id: str) -> bool:
        if (device_id, page_id) in self.warmed:
            self.promotes.append((device_id, page_id))
            return True
        return False

    def latest_render_for(self, _device_id: str) -> dict[str, Any] | None:
        return None


@dataclass
class Rig:
    svc: ButtonService
    push: FakePush
    decks: DeckStore
    nav: DeckNavStore
    state: DeviceRotationStateStore
    settings: SettingsStore
    events: EventLog


def _rig(tmp_path: Path, *, with_nav: bool = True) -> Rig:
    pages = PageStore(tmp_path / "pages.json")
    for pid in ("a", "b", "c", "x", "y", "solo"):
        pages.save(Page(id=pid, name=pid.upper(), device_ids=["kitchen", "hall"]))
    decks = DeckStore(tmp_path / "decks.json")
    nav = DeckNavStore(tmp_path / "nav.json")
    state = DeviceRotationStateStore(tmp_path / "state.json")
    settings = SettingsStore(tmp_path / "settings.json")
    events = EventLog(tmp_path / "events.db")
    push = FakePush()
    svc = ButtonService(
        rotation_store=RotationProjection(decks),
        state_store=state,
        settings_store=settings,
        page_store=pages,
        push_manager=push,  # type: ignore[arg-type]
        event_log=events,
        clock=lambda: NOW,
        deck_store=decks,
        deck_nav_store=nav if with_nav else None,
    )
    return Rig(svc, push, decks, nav, state, settings, events)


def _deck(did: str, pages: list[str], devices: list[str] | None = None, **kw: Any) -> Deck:
    return Deck(
        id=did,
        name=did.title(),
        device_ids=devices if devices is not None else ["kitchen"],
        pages=[DeckPage(page_id=p) for p in pages],
        **kw,
    )


# ---------- lineup path ----------


def test_step_moves_only_this_display_within_its_lineup(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b", "c"], ["kitchen", "hall"]))
    rig.nav.set("kitchen", "morning", "a")
    rig.nav.set("hall", "morning", "a")

    result = rig.svc.step(device_id="kitchen", forward=True)

    assert result.pushed_page_id == "b"
    assert rig.push.calls == [
        {
            "page_id": "b",
            "device_ids": {"kitchen"},
            # User-initiated: paints through quiet hours like a press does.
            "respect_quiet_hours": False,
            "source": "home_assistant",
        }
    ]
    assert rig.nav.current_page("kitchen", "morning") == "b"
    # The other display on the same lineup keeps its position.
    assert rig.nav.current_page("hall", "morning") == "a"


def test_step_back_wraps_to_the_last_page(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b", "c"]))
    rig.nav.set("kitchen", "morning", "a")
    assert rig.svc.step(device_id="kitchen", forward=False).pushed_page_id == "c"
    assert rig.nav.current_page("kitchen", "morning") == "c"


def test_step_follows_the_lineup_on_the_glass_not_the_first_bound(tmp_path: Path) -> None:
    # Two time-slot lineups bound to one display. The physical-button rule
    # (_bound_deck) picks the first; the nav record says dinner is live.
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("breakfast", ["a", "b"]))
    rig.decks.upsert(_deck("dinner", ["x", "y"]))
    rig.nav.set("kitchen", "dinner", "x")

    following = rig.svc.following_deck("kitchen")
    assert following is not None and following.id == "dinner"
    result = rig.svc.step(device_id="kitchen", forward=True)
    assert result.pushed_page_id == "y"
    assert result.action_spec == "deck:dinner:y"
    assert rig.nav.get("kitchen")["deck_id"] == "dinner"


def test_without_a_nav_record_the_first_enabled_lineup_is_followed(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("off", ["x", "y"], enabled=False))
    rig.decks.upsert(_deck("breakfast", ["a", "b"]))
    following = rig.svc.following_deck("kitchen")
    assert following is not None and following.id == "breakfast"
    # From the entry page (a), next is b.
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "b"


def test_nav_record_for_a_lineup_that_dropped_the_display_is_ignored(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("breakfast", ["a", "b"]))
    rig.decks.upsert(_deck("dinner", ["x", "y"], devices=["hall"]))
    rig.nav.set("kitchen", "dinner", "x")
    following = rig.svc.following_deck("kitchen")
    assert following is not None and following.id == "breakfast"


def test_graph_link_wins_over_lineup_order(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(
        Deck(
            id="graph",
            name="Graph",
            device_ids=["kitchen"],
            pages=[
                DeckPage(page_id="a", links=[DeckLink(target_page_id="c", button="right")]),
                DeckPage(page_id="b"),
                DeckPage(page_id="c"),
            ],
        )
    )
    rig.nav.set("kitchen", "graph", "a")
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "c"


def test_warmed_frame_is_promoted_without_a_render(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b"]))
    rig.nav.set("kitchen", "morning", "a")
    rig.push.warmed.add(("kitchen", "b"))
    result = rig.svc.step(device_id="kitchen", forward=True)
    assert rig.push.promotes == [("kitchen", "b")]
    assert rig.push.calls == []
    assert result.pushed_page_id == "b"
    assert rig.nav.current_page("kitchen", "morning") == "b"


def test_recorded_page_missing_from_the_lineup_steps_from_its_entry(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b", "c"]))
    rig.nav.set("kitchen", "morning", "gone")
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "b"


def test_one_page_lineup_is_a_noop_not_another_lineups_rotation(tmp_path: Path) -> None:
    # The live slot is a single-page timer lineup; a second (multi-page)
    # timer lineup is bound too, and sorts first. A remote step must not
    # wander into it, neither as a lineup (the first-bound rule) nor through
    # the rotation fallback (highest priority, then id).
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("aaa_other", ["a", "b"], advance="timer"))
    rig.decks.upsert(_deck("news", ["solo"], advance="timer"))
    rig.nav.set("kitchen", "news", "solo")

    result = rig.svc.step(device_id="kitchen", forward=True)

    assert result.pushed_page_id is None
    assert rig.push.calls == [] and rig.push.promotes == []
    assert rig.nav.get("kitchen")["deck_id"] == "news"
    assert rig.state.get("kitchen") is None
    rows = rig.events.list(limit=5)
    assert [(r.source, r.status) for r in rows] == [("home_assistant", "noop")]


def test_step_keeps_a_hold_in_place_on_the_lineup_path(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b"]))
    rig.nav.set("kitchen", "morning", "a")
    until = NOW + timedelta(minutes=90)
    rig.state.upsert(DeviceRotationState(device_id="kitchen", override_until=until))
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "b"
    assert rig.state.get("kitchen").override_until == until


# ---------- rotation path / nothing to step ----------


def test_without_a_lineup_the_rotation_steps_and_holds(tmp_path: Path) -> None:
    # A timer lineup bound to no display reaches this one through its
    # dashboards' bindings: a rotation, not a followed lineup.
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("cycle", ["a", "b", "c"], devices=[], advance="timer"))
    assert rig.svc.following_deck("kitchen") is None

    result = rig.svc.step(device_id="kitchen", forward=True)

    assert result.rotation_id == "cycle"
    assert result.pushed_page_id == "b"
    assert result.manual_override is True
    assert rig.push.calls[-1]["device_ids"] == {"kitchen"}
    assert rig.push.calls[-1]["source"] == "home_assistant"
    state = rig.state.get("kitchen")
    assert state is not None and state.rotation_id == "cycle" and state.step_index == 1
    assert state.override_until is not None and state.override_until > NOW


def test_display_following_nothing_is_a_noop(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    result = rig.svc.step(device_id="kitchen", forward=False)
    assert result.pushed_page_id is None
    assert rig.push.calls == []
    assert rig.state.get("kitchen") is None
    rows = rig.events.list(limit=5)
    assert [(r.source, r.status) for r in rows] == [("home_assistant", "noop")]


def test_without_nav_wiring_the_rotation_path_is_used(tmp_path: Path) -> None:
    rig = _rig(tmp_path, with_nav=False)
    rig.decks.upsert(_deck("cycle", ["a", "b"], advance="timer"))
    assert rig.svc.following_deck("kitchen") is None
    assert rig.svc.step(device_id="kitchen", forward=True).rotation_id == "cycle"


# ---------- what a remote step does NOT do ----------


def test_button_map_remap_does_not_change_a_step(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.settings.patch_section(
        "app", {"button_map": {"right": "webhook:http://127.0.0.1:9/x", "left": "refresh"}}
    )
    rig.decks.upsert(_deck("morning", ["a", "b", "c"]))
    rig.nav.set("kitchen", "morning", "a")
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "b"


def test_step_leaves_the_panels_dedup_fingerprint_alone(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b", "c"]))
    rig.nav.set("kitchen", "morning", "a")
    # The panel's last press: event 7.
    first = rig.svc.handle_button(device_id="kitchen", button="right", event_id=7)
    assert first.pushed_page_id == "b"
    before = rig.state.get("kitchen")

    rig.svc.step(device_id="kitchen", forward=True)  # b -> c
    rig.svc.step(device_id="kitchen", forward=True)  # c -> a: not deduped

    after = rig.state.get("kitchen")
    assert after.last_button_event_id == before.last_button_event_id == 7
    assert after.last_button_at == before.last_button_at
    assert rig.nav.current_page("kitchen", "morning") == "a"
    # A firmware retry of event 7 is still recognised as one.
    assert rig.svc.handle_button(device_id="kitchen", button="right", event_id=7).dedup is True


def test_step_emits_no_input_event(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b"]))
    seen: list[dict[str, Any]] = []
    rig.svc.add_listener(seen.append)
    rig.svc.step(device_id="kitchen", forward=True)
    assert seen == []


def test_step_runs_while_automation_is_paused(tmp_path: Path) -> None:
    # Automation pauses the scheduler only; manual actions go through, the
    # same as a physical press.
    rig = _rig(tmp_path)
    rig.settings.patch_section("app", {"automation_paused": True})
    rig.decks.upsert(_deck("morning", ["a", "b"]))
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "b"


def test_history_row_is_attributed_to_home_assistant_and_links_its_push(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    rig.decks.upsert(_deck("morning", ["a", "b"]))
    rig.svc.step(device_id="kitchen", forward=True)
    row = rig.events.list(limit=1)[0]
    assert row.source == "home_assistant"
    assert row.status == "dispatched"
    assert row.target == "kitchen"
    assert row.extra["button"] == "next"
    assert row.extra["deck_id"] == "morning"
    assert row.extra["push_event_id"] == 101


def test_failed_push_is_reported_and_keeps_the_position(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    rig.push.status = "failed"
    rig.decks.upsert(_deck("morning", ["a", "b", "c"]))
    rig.nav.set("kitchen", "morning", "a")
    result = rig.svc.step(device_id="kitchen", forward=True)
    assert result.push_result is not None and result.push_result.status == "failed"
    assert result.pushed_page_id is None
    # The panel never left "a": the next step tries "b" again, not "c".
    assert rig.nav.current_page("kitchen", "morning") == "a"
    rig.push.status = "sent"
    assert rig.svc.step(device_id="kitchen", forward=True).pushed_page_id == "b"
