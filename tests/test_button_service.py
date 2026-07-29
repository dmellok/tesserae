"""ButtonService integration tests.

Covers the pieces that wire together in production: rotation
resolution, per-device state, dedup by event_id and by time-window,
action dispatch, push triggering, and the response envelope shape.

The tests use real ``RotationStore`` / ``DeviceRotationStateStore`` /
``SettingsStore`` / ``PageStore`` instances backed by tmp files so
the JSON serialisation path is exercised too. The push manager is a
lightweight stub because ``PushManager`` itself needs a broker, a
renderer, a browser pool, etc., none of which is on the tested path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.button_service import ButtonService
from app.push import PushResult
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.event_log import EventLog
from app.state.page_store import Page, PageStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.settings_store import SettingsStore

# -- fixtures -----------------------------------------------------


class FakeClock:
    """Injectable clock so we can control time-window dedup without
    ``freezegun``. Advance by calling ``tick``."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 7, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def tick(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@dataclass
class StubPushManager:
    """Records every push so tests can assert on the call sequence.

    ``push`` is signature-compatible with ``PushManager.push`` for the
    kwargs ButtonService actually uses."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    latest_renders: dict[str, dict[str, Any]] = field(default_factory=dict)
    fail: bool = False

    def latest_render_for(self, device_id: str) -> dict[str, Any] | None:
        return self.latest_renders.get(device_id)

    def push(
        self,
        page_id: str,
        *,
        device_ids: set[str] | None = None,
        respect_quiet_hours: bool = False,
        source: str = "page",
    ) -> PushResult:
        if self.fail:
            raise RuntimeError("stub push failure")
        self.calls.append(
            {
                "page_id": page_id,
                "device_ids": device_ids,
                "respect_quiet_hours": respect_quiet_hours,
                "source": source,
            }
        )
        return PushResult(status="pushed", page_id=page_id)


@pytest.fixture
def rotation_store(tmp_path: Path) -> RotationStore:
    return RotationStore(tmp_path / "rotations.json")


@pytest.fixture
def state_store(tmp_path: Path) -> DeviceRotationStateStore:
    return DeviceRotationStateStore(tmp_path / "device_rotation_state.json")


@pytest.fixture
def settings_store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "settings.json")


@pytest.fixture
def page_store(tmp_path: Path) -> PageStore:
    store = PageStore(tmp_path / "pages.json")
    for pid in ("morning", "afternoon", "evening"):
        store.save(Page(id=pid, name=pid.title(), device_id="kitchen"))
    return store


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def push_manager() -> StubPushManager:
    return StubPushManager()


def _wire(
    *,
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog | None = None,
) -> ButtonService:
    return ButtonService(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,  # type: ignore[arg-type]
        clock=clock,
        event_log=event_log,
    )


def _seed_rotation(rotation_store: RotationStore) -> Rotation:
    """Three-step rotation bound to ``kitchen``."""
    rotation = Rotation(
        id="kitchen_rot",
        name="Kitchen",
        device_ids=["kitchen"],
        steps=[
            RotationStep(page_id="morning", dwell_minutes=30),
            RotationStep(page_id="afternoon", dwell_minutes=30),
            RotationStep(page_id="evening", dwell_minutes=30),
        ],
    )
    rotation_store.upsert(rotation)
    return rotation


# -- basic dispatch ------------------------------------------------


def test_right_button_advances_to_next_step_and_pushes(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="right", event_id=1)

    assert result.dedup is False
    assert result.unmapped is False
    assert result.step_index == 1
    assert result.step_page_id == "afternoon"
    assert result.pushed_page_id == "afternoon"
    assert push_manager.calls == [
        {
            "page_id": "afternoon",
            "device_ids": {"kitchen"},
            "respect_quiet_hours": False,
            "source": "button",
        }
    ]
    # Manual override is now in effect.
    persisted = state_store.get("kitchen")
    assert persisted is not None
    assert persisted.step_index == 1
    assert persisted.override_until is not None
    assert persisted.last_button_event_id == 1


def test_button_advances_rotation_with_empty_device_ids(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    """Regression (#122): the Rotations UI writes ``device_ids=[]``, which
    means "use the step pages' device bindings". A device bound to the pages
    (not listed on the rotation) must still advance the rotation on a button
    press; before the fix ``_resolve_rotation`` returned None and the press
    was a no-op."""
    rotation_store.upsert(
        Rotation(
            id="kitchen_rot",
            name="Kitchen",
            device_ids=[],  # what the UI form always saves
            steps=[
                RotationStep(page_id="morning", dwell_minutes=30),
                RotationStep(page_id="afternoon", dwell_minutes=30),
                RotationStep(page_id="evening", dwell_minutes=30),
            ],
        )
    )
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    # The fixture pages are bound to "kitchen", so the empty-device_ids
    # rotation falls through to them and the button advances it.
    result = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    assert result.step_index == 1
    assert result.pushed_page_id == "afternoon"
    assert push_manager.calls and push_manager.calls[0]["page_id"] == "afternoon"


def test_left_button_wraps_at_start(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="left", event_id=1)

    assert result.step_index == 2  # wrapped 0 -> 2
    assert result.step_page_id == "evening"


def test_refresh_forces_push_of_current_page(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="refresh", event_id=1)

    assert result.step_index == 0
    assert result.pushed_page_id == "morning"


def test_fetch_latest_does_not_push_or_set_manual_override(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    settings_store.patch_section("devices", {"kitchen": {"button_map": {"custom": "fetch_latest"}}})
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="custom", event_id=1)

    assert result.step_index == 0
    assert result.pushed_page_id is None
    assert result.manual_override is False
    assert result.force_download is True
    assert push_manager.calls == []


# -- dedup --------------------------------------------------------


def test_duplicate_event_id_is_noop(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    svc.handle_button(device_id="kitchen", button="right", event_id=42)
    push_manager.calls.clear()
    result = svc.handle_button(device_id="kitchen", button="right", event_id=42)

    assert result.dedup is True
    assert push_manager.calls == []


def test_lower_event_id_is_a_counter_restart_not_a_retry(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    """v0.187.2: dedup is equality-only. The firmware counter is
    RTC-backed and restarts at 0 on any power cycle without a re-pair,
    and the offline touch queue replays older ids after an outage; a
    LOWER id must therefore dispatch (pre-fix, a power-cycled panel had
    every button and touch swallowed). Only the SAME id is a retry."""
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    svc.handle_button(device_id="kitchen", button="right", event_id=42)
    push_manager.calls.clear()
    restarted = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    assert restarted.dedup is False
    assert push_manager.calls != []

    # The high-water mark adopts the restarted counter, and a true
    # retry of that event (same id) is still swallowed.
    push_manager.calls.clear()
    retry = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    assert retry.dedup is True
    assert push_manager.calls == []


def test_missing_event_id_uses_time_window_dedup(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    svc.handle_button(device_id="kitchen", button="right", event_id=None)
    push_manager.calls.clear()
    # Within the 3s default window -> dedup.
    clock.tick(1.0)
    result = svc.handle_button(device_id="kitchen", button="right", event_id=None)
    assert result.dedup is True
    assert push_manager.calls == []
    # Past the window -> fires again.
    clock.tick(10.0)
    result = svc.handle_button(device_id="kitchen", button="right", event_id=None)
    assert result.dedup is False
    assert len(push_manager.calls) == 1


def test_different_button_within_window_is_not_dedup(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    svc.handle_button(device_id="kitchen", button="right", event_id=None)
    push_manager.calls.clear()
    clock.tick(0.5)
    result = svc.handle_button(device_id="kitchen", button="left", event_id=None)
    assert result.dedup is False
    assert len(push_manager.calls) == 1


# -- unmapped / error handling ------------------------------------


def test_unmapped_button_returns_current_state_and_no_push(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="unicorn", event_id=1)
    assert result.unmapped is True
    assert push_manager.calls == []


def test_no_rotation_bound_button_is_noop(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    """No rotation touches this device -> default rotate_next resolves
    but has nothing to advance, so no push fires. State is still
    recorded so subsequent dedup works."""
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="orphan_device", button="right", event_id=1)
    assert result.rotation_id is None
    assert push_manager.calls == []


# -- config precedence --------------------------------------------


def test_per_device_button_map_overrides_global(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    settings_store.patch_section("app", {"button_map": {"right": "refresh"}})
    settings_store.patch_section("devices", {"kitchen": {"button_map": {"right": "rotate_next"}}})
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    # Per-device wins over global; step advances to 1.
    assert result.step_index == 1


def test_page_action_shortcut_pushes_target_without_moving_position(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    settings_store.patch_section(
        "devices", {"kitchen": {"button_map": {"custom1": "page:evening"}}}
    )
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="custom1", event_id=1)
    # Position stays at 0; target page pushed; no override set (page:
    # is a shortcut, not a rotation manipulation).
    assert result.step_index == 0
    assert result.pushed_page_id == "evening"
    assert push_manager.calls[0]["page_id"] == "evening"

    persisted = state_store.get("kitchen")
    assert persisted is not None
    assert persisted.step_index == 0
    assert persisted.override_until is None


# -- envelope shape -----------------------------------------------


def test_snapshot_reflects_time_based_default_when_no_override(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.snapshot("kitchen")
    assert result.rotation_id == "kitchen_rot"
    assert result.step_index == 0
    assert result.manual_override is False
    assert result.step_page_id == "morning"


def test_envelope_omits_rotation_fields_when_unbound(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    envelope = svc.snapshot("orphan").to_envelope()
    assert envelope["rotation_id"] is None
    assert envelope["step_index"] is None


# -- webhook action ------------------------------------------------


def test_webhook_action_fires_post_asynchronously(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """webhook:<url> mapped to a button should POST to that URL in a
    daemon thread with the standard payload, and the button handler
    itself must not block on the HTTP call. We patch ``urlopen`` so
    no network traffic actually happens; a threading Event lets the
    test wait deterministically."""
    import json as _json
    import threading as _threading

    _seed_rotation(rotation_store)
    settings_store.patch_section(
        "devices",
        {"kitchen": {"button_map": {"custom": "webhook:https://hook.example/x"}}},
    )
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    captured: dict[str, Any] = {}
    done = _threading.Event()

    class _StubResp:
        status = 200

        def __enter__(self) -> _StubResp:
            return self

        def __exit__(self, *_a: object) -> None:
            pass

    def _stub_urlopen(req: Any, timeout: float) -> _StubResp:
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        captured["timeout"] = timeout
        done.set()
        return _StubResp()

    monkeypatch.setattr("app.button_service.urllib.request.urlopen", _stub_urlopen)

    result = svc.handle_button(device_id="kitchen", button="custom", event_id=1)
    assert done.wait(2.0), "webhook thread did not run within 2s"
    assert result.dedup is False
    assert captured["url"] == "https://hook.example/x"
    assert captured["method"] == "POST"

    body = _json.loads(captured["body"])
    assert body["device_id"] == "kitchen"
    assert body["button"] == "custom"
    assert body["button_event_id"] == 1
    assert body["action_spec"] == "webhook:https://hook.example/x"
    assert body["rotation_id"] == "kitchen_rot"


def test_webhook_failure_is_swallowed(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable endpoint / DNS failure must not break the
    button handler. Fire-and-forget."""
    import threading as _threading

    _seed_rotation(rotation_store)
    settings_store.patch_section(
        "devices",
        {"kitchen": {"button_map": {"custom": "webhook:https://nope.example/x"}}},
    )
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    fired = _threading.Event()

    def _boom(req: Any, timeout: float) -> Any:
        fired.set()
        raise ConnectionError("nope")

    monkeypatch.setattr("app.button_service.urllib.request.urlopen", _boom)

    result = svc.handle_button(device_id="kitchen", button="custom", event_id=1)
    assert fired.wait(2.0)
    assert result.dedup is False


# -- event log emission -------------------------------------------


@pytest.fixture
def event_log(tmp_path: Path) -> EventLog:
    return EventLog(tmp_path / "events.db", cap=100, device_cap=25)


def _button_rows(event_log: EventLog) -> list[Any]:
    """Return every event log row with source=button, most recent last."""
    return list(reversed(list(event_log.list(type="push", source="button", limit=50))))


def test_dispatched_press_emits_history_row(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=event_log,
    )

    svc.handle_button(device_id="kitchen", button="right", event_id=1)

    rows = _button_rows(event_log)
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "button"
    assert row.target == "kitchen"
    assert row.status == "dispatched"
    assert row.extra["button"] == "right"
    assert row.extra["button_event_id"] == 1
    assert row.extra["action_spec"] == "rotate_next"
    assert row.extra["rotation_id"] == "kitchen_rot"
    assert row.extra["step_index"] == 1
    assert row.extra["pushed_page_id"] == "afternoon"


def test_deduped_press_emits_history_row(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=event_log,
    )

    svc.handle_button(device_id="kitchen", button="right", event_id=1)
    svc.handle_button(device_id="kitchen", button="right", event_id=1)  # retry

    rows = _button_rows(event_log)
    statuses = [r.status for r in rows]
    assert "dispatched" in statuses
    assert "deduped" in statuses


def test_unmapped_press_emits_history_row(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog,
) -> None:
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=event_log,
    )

    svc.handle_button(device_id="kitchen", button="unicorn", event_id=1)

    rows = _button_rows(event_log)
    assert len(rows) == 1
    assert rows[0].status == "unmapped"
    assert rows[0].extra["button"] == "unicorn"


def test_fetch_latest_press_emits_fetched_history_row(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog,
) -> None:
    settings_store.patch_section("devices", {"kitchen": {"button_map": {"custom": "fetch_latest"}}})
    push_manager.latest_renders["kitchen"] = {
        "digest": "wire123",
        "composition_digest": "composition456",
    }
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=event_log,
    )

    svc.handle_button(device_id="kitchen", button="custom", event_id=1)

    rows = _button_rows(event_log)
    assert len(rows) == 1
    assert rows[0].status == "fetched"
    assert rows[0].digest is None
    assert rows[0].extra["device_ids"] == ["kitchen"]
    assert rows[0].extra["action_spec"] == "fetch_latest"
    assert rows[0].extra["composition_digest"] == "composition456"
    assert rows[0].extra["pushed_page_id"] is None
    assert push_manager.calls == []


def test_fetch_latest_without_existing_frame_keeps_preview_empty(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog,
) -> None:
    settings_store.patch_section("devices", {"kitchen": {"button_map": {"custom": "fetch_latest"}}})
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=event_log,
    )

    svc.handle_button(device_id="kitchen", button="custom", event_id=1)

    row = _button_rows(event_log)[0]
    assert row.status == "fetched"
    assert row.digest is None
    assert row.extra["device_ids"] == ["kitchen"]
    assert "composition_digest" not in row.extra


def test_webhook_press_emits_webhook_dispatched_row(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
    event_log: EventLog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_rotation(rotation_store)
    settings_store.patch_section(
        "devices",
        {"kitchen": {"button_map": {"custom": "webhook:https://hook.example/x"}}},
    )
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=event_log,
    )

    # Suppress the actual daemon-thread POST; test only cares about the row.
    class _StubResp:
        status = 200

        def __enter__(self) -> _StubResp:
            return self

        def __exit__(self, *_a: object) -> None:
            pass

    monkeypatch.setattr(
        "app.button_service.urllib.request.urlopen",
        lambda req, timeout: _StubResp(),
    )

    svc.handle_button(device_id="kitchen", button="custom", event_id=1)

    rows = _button_rows(event_log)
    assert len(rows) == 1
    assert rows[0].status == "webhook_dispatched"
    assert rows[0].extra["action_spec"] == "webhook:https://hook.example/x"


def test_no_event_log_wired_is_a_silent_noop(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    push_manager: StubPushManager,
    clock: FakeClock,
) -> None:
    """Constructing without an event log (tests, offline paths) must
    keep dispatch working; the emission is best-effort."""
    _seed_rotation(rotation_store)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
        event_log=None,
    )

    result = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    assert result.dedup is False
    assert result.pushed_page_id == "afternoon"


# -- push failure resilience --------------------------------------


def test_push_failure_still_persists_state(
    rotation_store: RotationStore,
    state_store: DeviceRotationStateStore,
    settings_store: SettingsStore,
    page_store: PageStore,
    clock: FakeClock,
) -> None:
    """A failing PushManager shouldn't leave rotation state stuck; the
    next timer wake or button press should still see the new position
    even if this push blew up."""
    _seed_rotation(rotation_store)
    push_manager = StubPushManager(fail=True)
    svc = _wire(
        rotation_store=rotation_store,
        state_store=state_store,
        settings_store=settings_store,
        page_store=page_store,
        push_manager=push_manager,
        clock=clock,
    )

    result = svc.handle_button(device_id="kitchen", button="right", event_id=1)
    # Position updated even though the push errored out.
    assert result.step_index == 1
    persisted = state_store.get("kitchen")
    assert persisted is not None
    assert persisted.step_index == 1
