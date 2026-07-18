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


def test_swipe_starting_outside_zone_falls_back_to_end_point(stores: dict[str, Any]) -> None:
    """A swipe that starts just outside a small zone but moves INTO it still
    fires: hit-testing falls back to the stroke's end point (issue #49
    swipe forgiveness). The region is (0,0,400,300) with swipe up ->
    rotate_next; the stroke starts at y=500 (below it) and ends inside."""
    _seed_rotation(stores)
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=200, y0=500, x1=200, y1=50),
        frame_digest="art123",
    )
    assert result.gesture == "swipe_up"
    assert result.outcome == "dispatched"
    assert result.action_spec == "rotate_next"


def test_swipe_fully_outside_zone_is_no_target(stores: dict[str, Any]) -> None:
    """Neither endpoint in the zone -> still no_target (the fallback rescues
    a swipe onto a zone, it doesn't fire zones the stroke never touched)."""
    _seed_rotation(stores)
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=600, y0=250, x1=600, y1=20),
        frame_digest="art123",
    )
    assert result.outcome == "no_target"


def test_tap_still_uses_start_point_only(stores: dict[str, Any]) -> None:
    """The swipe fallback must not change tap behaviour: a tap outside every
    zone stays no_target (no end-point rescue for a point)."""
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=600, y0=600, x1=600, y1=600),
        frame_digest="art123",
    )
    assert result.gesture == "tap"
    assert result.outcome == "no_target"


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
    rows = list(stores["event_log"].list(type="touch", source="touch", limit=10))
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
    return list(reversed(list(event_log.list(type="touch", source="touch", limit=50))))


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


# -- structured HA actions + sliders (phase 3) ---------------------------


def _ha_region(**overrides: Any) -> dict[str, Any]:
    region = _tap_region(
        tap={
            "action": "ha",
            "domain": "light",
            "service": "toggle",
            "data": {"entity_id": "light.x"},
        },
        swipe=None,
        origin="config",
    )
    region.update(overrides)
    return region


def _stub_ha(svc: ButtonService) -> list[tuple[str, str, dict[str, Any]]]:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake(domain: str, service: str, data: dict[str, Any]) -> None:
        calls.append((domain, service, data))

    svc._call_ha = fake  # type: ignore[method-assign]
    return calls


def test_ha_tap_natural_shape_dispatches(stores: dict[str, Any]) -> None:
    """The shape an agent naturally writes, no 'action' key, entity_id at
    the top level, now dispatches (previously a silent no-op)."""
    region = _tap_region(
        tap={
            "domain": "light",
            "service": "turn_on",
            "entity_id": "light.desk",
            "data": {},
        },
        swipe=None,
        origin="config",
    )
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    calls = _stub_ha(svc)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "ha_dispatched"
    assert calls == [("light", "turn_on", {"entity_id": "light.desk"})]


def test_slide_natural_ha_shape_with_dollar_value(stores: dict[str, Any]) -> None:
    """A dimmer written the natural way: no 'action' key, $value alias."""
    region = _tap_region(
        tap=None,
        swipe=None,
        origin="config",
        x=100,
        y=100,
        w=40,
        h=200,
        slide={
            "axis": "y",
            "action": {
                "domain": "light",
                "service": "turn_on",
                "entity_id": "light.x",
                "data": {"brightness_pct": "$value"},
            },
        },
    )
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    calls = _stub_ha(svc)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=120, y0=290, x1=120, y1=150),  # -> 75
        frame_digest="art123",
    )
    assert result.outcome == "ha_dispatched"
    assert result.value == 75
    assert calls == [("light", "turn_on", {"brightness_pct": 75, "entity_id": "light.x"})]


def test_ha_tap_dispatches_service_call(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_ha_region()])
    svc = _service(stores, pm)
    calls = _stub_ha(svc)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "ha_dispatched"
    assert calls == [("light", "toggle", {"entity_id": "light.x"})]
    rows = list(stores["event_log"].list(type="touch", source="touch", limit=10))
    assert rows and rows[0].status == "ha_dispatched"


def test_ha_tap_refreshes_rotation_page_same_wake(stores: dict[str, Any]) -> None:
    _seed_rotation(stores)
    pm = TouchStubPushManager(latest=_latest(), regions=[_ha_region()])
    svc = _service(stores, pm)
    _stub_ha(svc)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "ha_dispatched"
    # The current rotation page was re-pushed so this wake's frame shows
    # the post-action HA state.
    assert pm.calls and pm.calls[0]["page_id"] == "morning"
    assert pm.calls[0]["source"] == "touch"


def test_ha_from_markup_origin_is_blocked(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_ha_region(origin="markup")])
    svc = _service(stores, pm)
    calls = _stub_ha(svc)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "blocked"
    assert calls == []


def test_ha_failure_reports_ha_failed(stores: dict[str, Any]) -> None:
    pm = TouchStubPushManager(latest=_latest(), regions=[_ha_region()])
    svc = _service(stores, pm)

    def boom(domain: str, service: str, data: dict[str, Any]) -> None:
        raise RuntimeError("HA is down")

    svc._call_ha = boom  # type: ignore[method-assign]
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )
    assert result.outcome == "ha_failed"
    assert pm.calls == []  # no refresh push on failure
    rows = list(stores["event_log"].list(type="touch", source="touch", limit=10))
    assert rows and rows[0].status == "ha_failed"
    assert "HA is down" in (rows[0].error or "")


def test_slide_sets_value_and_substitutes_into_ha_data(stores: dict[str, Any]) -> None:
    region = _tap_region(
        tap=None,
        swipe=None,
        origin="config",
        x=100,
        y=100,
        w=40,
        h=200,
        slide={
            "axis": "y",
            "action": {
                "action": "ha",
                "domain": "light",
                "service": "turn_on",
                "data": {"entity_id": "light.x", "brightness_pct": "{value}"},
            },
        },
    )
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    calls = _stub_ha(svc)
    # Press near the bottom, drag to 25% from the top -> value 75.
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=120, y0=290, x1=120, y1=150),
        frame_digest="art123",
    )
    assert result.outcome == "ha_dispatched"
    assert result.gesture == "slide"
    assert result.value == 75
    assert calls == [("light", "turn_on", {"entity_id": "light.x", "brightness_pct": 75})]


def test_slide_tap_sets_absolute_value_at_point(stores: dict[str, Any]) -> None:
    """A plain tap on a slider region sets the value at the tap point."""
    region = _tap_region(
        tap=None,
        swipe=None,
        origin="config",
        x=0,
        y=0,
        w=200,
        h=40,
        slide={"axis": "x", "action": "webhook:http://127.0.0.1:9/level/{value}"},
    )
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=100, y0=20, x1=100, y1=20),
        frame_digest="art123",
    )
    assert result.outcome == "webhook_dispatched"
    assert result.gesture == "slide"
    assert result.value == 50
    assert result.action_spec == "webhook:http://127.0.0.1:9/level/50"


def test_slide_from_markup_with_webhook_is_blocked(stores: dict[str, Any]) -> None:
    region = _tap_region(
        tap=None,
        swipe=None,
        origin="markup",
        x=0,
        y=0,
        w=200,
        h=40,
        slide={"axis": "x", "action": "webhook:http://127.0.0.1:9/level/{value}"},
    )
    pm = TouchStubPushManager(latest=_latest(), regions=[region])
    svc = _service(stores, pm)
    result = svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=100, y0=20, x1=100, y1=20),
        frame_digest="art123",
    )
    assert result.outcome == "blocked"


# -- adjacent-page prewarm (issue #49 linger) -----------------------------


@dataclass
class PrewarmStubPushManager(TouchStubPushManager):
    """TouchStubPushManager that also records prewarm_page calls."""

    prewarmed: list[tuple[str, str]] = field(default_factory=list)

    def prewarm_page(self, page_id: str, *, device_id: str) -> bool:
        self.prewarmed.append((page_id, device_id))
        return True


def test_prewarm_adjacent_targets_prev_and_next_steps(stores: dict[str, Any]) -> None:
    """After a touch on a rotation-bound device, the steps either side of
    the current one get prewarmed (the likely swipe targets), nothing
    else."""
    _seed_rotation(stores)
    pm = PrewarmStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    # Rotation kitchen_rot: morning / afternoon / evening, current step 0.
    svc._prewarm_adjacent("kitchen")
    assert ("afternoon", "kitchen") in pm.prewarmed  # next
    assert ("evening", "kitchen") in pm.prewarmed  # prev (wraps)
    assert all(p != "morning" for p, _ in pm.prewarmed)  # current already rendered


def test_prewarm_skips_without_rotation_or_single_step(stores: dict[str, Any]) -> None:
    pm = PrewarmStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    svc._prewarm_adjacent("kitchen")  # no rotation at all
    assert pm.prewarmed == []
    stores["rotation_store"].upsert(
        Rotation(
            id="solo",
            name="Solo",
            device_ids=["kitchen"],
            steps=[RotationStep(page_id="morning", dwell_minutes=30)],
        )
    )
    svc._prewarm_adjacent("kitchen")  # one step: nowhere to go
    assert pm.prewarmed == []


def test_prewarm_tolerates_stub_without_hook(stores: dict[str, Any]) -> None:
    """Push managers that don't implement prewarm_page (older stubs,
    minimal transports) are skipped, not crashed on."""
    _seed_rotation(stores)
    pm = TouchStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    svc._prewarm_adjacent("kitchen")  # must not raise


def test_handle_touch_spawns_prewarm_only_for_live_session(stores: dict[str, Any]) -> None:
    """The prewarm fires after any live-session stroke (even no_target),
    but not on the guard exits where there's no current-frame session."""
    _seed_rotation(stores)
    pm = PrewarmStubPushManager(latest=_latest(), regions=[_tap_region()])
    svc = _service(stores, pm)
    spawned: list[str] = []
    svc._spawn_prewarm = spawned.append  # synchronous, records device ids

    svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=50, y0=50, x1=50, y1=50),
        frame_digest="art123",
        event_id=1,
    )
    assert spawned == ["kitchen"]

    # Stale stroke: no session on the current frame, no prewarm.
    svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=50, y0=50, x1=50, y1=50),
        frame_digest="old_digest",
        event_id=2,
    )
    assert spawned == ["kitchen"]

    # no_target still counts as a live session (user is interacting).
    pm.regions = []
    svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=900, y0=900, x1=900, y1=900),
        frame_digest="art123",
        event_id=3,
    )
    assert spawned == ["kitchen", "kitchen"]
