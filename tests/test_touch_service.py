"""``ButtonService.handle_touch`` tests (issue #49): the guard chain
(dedup / no_frame / stale / no_target) and the dispatch path sharing the
button machinery (state persistence, push-on-same-wake, history rows).

Same store fixtures as ``test_button_service``; the push manager stub
grows the two lookups touch needs (``latest_render_for`` +
``touch_regions_for``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.button_service import ButtonService, TouchStroke
from app.push import PushResult
from app.state.device_rotation_state_store import DeviceRotationStateStore
from app.state.event_log import EventLog
from app.state.page_store import Page, PageStore
from app.state.rotation_model import Rotation, RotationStep
from app.state.rotation_store import RotationStore
from app.state.settings_store import SettingsStore


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 7, 17, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now

    def tick(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


@dataclass
class TouchStubPushManager:
    """Push stub with the render/region lookups ``handle_touch`` uses."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    latest: dict[str, Any] | None = None
    regions: list[dict[str, Any]] = field(default_factory=list)

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
        return PushResult(status="pushed", page_id=page_id)

    def latest_render_for(self, device_id: str) -> dict[str, Any] | None:
        return self.latest

    def touch_regions_for(self, comp_digest: str) -> list[dict[str, Any]]:
        return self.regions


@pytest.fixture
def stores(tmp_path: Path) -> dict[str, Any]:
    page_store = PageStore(tmp_path / "pages.json")
    for pid in ("morning", "afternoon", "evening", "lights"):
        page_store.save(Page(id=pid, name=pid.title(), device_id="kitchen"))
    return {
        "rotation_store": RotationStore(tmp_path / "rotations.json"),
        "state_store": DeviceRotationStateStore(tmp_path / "state.json"),
        "settings_store": SettingsStore(tmp_path / "settings.json"),
        "page_store": page_store,
        "event_log": EventLog(tmp_path / "events.db"),
    }


def _service(
    stores: dict[str, Any], push_manager: TouchStubPushManager, clock: FakeClock | None = None
) -> ButtonService:
    return ButtonService(
        rotation_store=stores["rotation_store"],
        state_store=stores["state_store"],
        settings_store=stores["settings_store"],
        page_store=stores["page_store"],
        push_manager=push_manager,  # type: ignore[arg-type]
        event_log=stores["event_log"],
        clock=clock or FakeClock(),
    )


def _seed_rotation(stores: dict[str, Any]) -> None:
    stores["rotation_store"].upsert(
        Rotation(
            id="kitchen_rot",
            name="Kitchen",
            device_ids=["kitchen"],
            steps=[
                RotationStep(page_id="morning", dwell_minutes=30),
                RotationStep(page_id="afternoon", dwell_minutes=30),
                RotationStep(page_id="evening", dwell_minutes=30),
            ],
        )
    )


def _latest(digest: str = "art123", comp: str = "comp456") -> dict[str, Any]:
    return {"digest": digest, "composition_digest": comp, "ext": "bin"}


def _tap_region(**overrides: Any) -> dict[str, Any]:
    region: dict[str, Any] = {
        "x": 0,
        "y": 0,
        "w": 400,
        "h": 300,
        "depth": 1,
        "order": 0,
        "tap": "page:lights",
        "swipe": {"up": "rotate_next"},
        "slide": None,
        "origin": "markup",
        "dangling": [],
    }
    region.update(overrides)
    return region


# -- guard chain ---------------------------------------------------------


def test_no_frame_yet_is_a_soft_outcome(stores: dict[str, Any]) -> None:
    svc = _service(stores, TouchStubPushManager(latest=None))
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="anything",
    )
    assert result.outcome == "no_frame"
    assert result.action_spec is None


def test_stale_digest_never_dispatches(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest("current"), regions=[_tap_region()])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="previous",
    )
    assert result.outcome == "stale"
    assert pm.calls == []


def test_tap_outside_all_regions_is_no_target(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region(w=100, h=100)])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=250, y0=250, x1=250, y1=250),
        frame_digest="art123",
    )
    assert result.outcome == "no_target"
    assert result.gesture == "tap"
    assert pm.calls == []


def test_gesture_without_declaration_is_no_target(stores: dict[str, Any]) -> None:
    """A swipe on a tap-only region no-ops rather than firing the tap."""
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region(swipe=None)])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=50, y0=250, x1=50, y1=20),
        frame_digest="art123",
    )
    assert result.outcome == "no_target"
    assert result.gesture == "swipe_up"
    assert pm.calls == []


def test_event_id_dedup_swallows_retries(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    first = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
        event_id=7,
    )
    assert first.outcome == "dispatched"
    retry = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
        event_id=7,
    )
    assert retry.outcome == "deduped"
    assert len(pm.calls) == 1


def test_no_event_id_means_no_dedup(stores: dict[str, Any]) -> None:
    """Two intentional taps in quick succession both dispatch when the
    firmware doesn't send a counter; there is no time-window fallback."""
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    for _ in range(2):
        result = svc.handle_touch(
            device_id="kitchen",
            stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
            frame_digest="art123",
        )
        assert result.outcome == "dispatched"
    assert len(pm.calls) == 2


# -- dispatch ------------------------------------------------------------


def test_tap_dispatches_page_action_and_pushes_same_wake(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=50, y0=50, x1=50, y1=50),
        frame_digest="art123",
    )
    assert result.outcome == "dispatched"
    assert result.gesture == "tap"
    assert result.action_spec == "page:lights"
    assert result.base.pushed_page_id == "lights"
    assert pm.calls == [
        {
            "page_id": "lights",
            "device_ids": {"kitchen"},
            "respect_quiet_hours": False,
            "source": "touch",
        }
    ]


def test_swipe_dispatches_rotation_step(stores: dict[str, Any]) -> None:
    _seed_rotation(stores)
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=50, y0=250, x1=50, y1=20),
        frame_digest="art123",
    )
    assert result.outcome == "dispatched"
    assert result.gesture == "swipe_up"
    assert result.action_spec == "rotate_next"
    assert result.base.step_index == 1
    assert pm.calls[0]["page_id"] == "afternoon"
    # Rotation state persisted, same as a physical button press.
    persisted = stores["state_store"].get("kitchen")
    assert persisted is not None and persisted.step_index == 1


def test_deepest_region_wins_hit_test_through_service(stores: dict[str, Any]) -> None:
    outer = _tap_region(tap="page:morning", depth=1, order=0)
    inner = _tap_region(x=100, y=100, w=50, h=50, tap="page:lights", depth=3, order=1)
    pm = TouchStubPushManager(latest=_latest(), regions=[outer, inner])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=120, y0=120, x1=120, y1=120),
        frame_digest="art123",
    )
    assert result.action_spec == "page:lights"


def test_unknown_action_spec_surfaces_as_error(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region(tap="warp_drive:9")])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "error"
    assert pm.calls == []


# -- provenance gate (phase 2) -------------------------------------------


def test_markup_origin_webhook_is_blocked(stores: dict[str, Any]) -> None:
    """A side-effecting action annotated in raw widget/code markup never
    dispatches; only editor/MCP-authored config actions may reach out."""
    region = _tap_region(tap="webhook:http://127.0.0.1:9/evil", origin="markup")
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "blocked"
    assert result.action_spec == "webhook:http://127.0.0.1:9/evil"
    assert pm.calls == []
    rows = list(stores["event_log"].list(type="push", source="touch", limit=10))
    assert rows and rows[0].status == "blocked"
    assert rows[0].extra["blocked_action_spec"] == "webhook:http://127.0.0.1:9/evil"


def test_config_origin_webhook_dispatches(stores: dict[str, Any]) -> None:
    region = _tap_region(tap="webhook:http://127.0.0.1:9/ok", origin="config")
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "webhook_dispatched"


def test_markup_origin_navigation_still_dispatches(stores: dict[str, Any]) -> None:
    """Navigation-class actions (page/rotate/refresh) are safe from any
    origin; the gate only guards side-effecting actions."""
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region(origin="markup")])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "dispatched"
    assert result.action_spec == "page:lights"


# -- history rows --------------------------------------------------------


def _touch_rows(event_log: EventLog) -> list[Any]:
    return list(reversed(list(event_log.list(type="push", source="touch", limit=50))))


def test_dispatched_touch_emits_history_row(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=50, y0=60, x1=50, y1=60, duration_ms=120),
        frame_digest="art123",
        event_id=3,
    )
    rows = _touch_rows(stores["event_log"])
    assert len(rows) == 1
    row = rows[0]
    assert row.source == "touch"
    assert row.status == "dispatched"
    assert row.extra["gesture"] == "tap"
    assert row.extra["action_spec"] == "page:lights"
    assert row.extra["touch"] == {"x0": 50, "y0": 60, "x1": 50, "y1": 60, "duration_ms": 120}
    assert row.extra["touch_event_id"] == 3
    assert row.extra["region"] == {"x": 0, "y": 0, "w": 400, "h": 300}


def test_stale_touch_emits_history_row(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest("current"))
    svc = _service(stores, pm)
    svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=1, y0=1, x1=1, y1=1),
        frame_digest="old",
    )
    rows = _touch_rows(stores["event_log"])
    assert len(rows) == 1
    assert rows[0].status == "stale"
