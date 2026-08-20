"""Post-action reconcile (the async, debounced repaint after an HA
touch action): scheduling off the wake, burst coalescing, the page
fallback chain (rotation -> deck -> last-pushed page), and the
patch-capable routing that prefers ``reconcile_via_patches`` over a
full push."""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass
class StubPushManager:
    calls: list[dict[str, Any]] = field(default_factory=list)
    patch_calls: list[dict[str, Any]] = field(default_factory=list)
    patch_outcome: str = "patched"
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
        self.calls.append({"page_id": page_id, "device_ids": device_ids, "source": source})
        return PushResult(status="sent", page_id=page_id)

    def latest_render_for(self, device_id: str) -> dict[str, Any] | None:
        return self.latest

    def touch_regions_for(self, comp_digest: str) -> list[dict[str, Any]]:
        return self.regions

    def reconcile_via_patches(self, device_id: str, page_id: str, *, panel: dict[str, Any]) -> str:
        self.patch_calls.append({"device_id": device_id, "page_id": page_id, "panel": panel})
        return self.patch_outcome


@pytest.fixture
def stores(tmp_path: Path) -> dict[str, Any]:
    page_store = PageStore(tmp_path / "pages.json")
    for pid in ("morning", "lights"):
        page_store.save(Page(id=pid, name=pid.title(), device_id="kitchen"))
    return {
        "rotation_store": RotationStore(tmp_path / "rotations.json"),
        "state_store": DeviceRotationStateStore(tmp_path / "state.json"),
        "settings_store": SettingsStore(tmp_path / "settings.json"),
        "page_store": page_store,
        "event_log": EventLog(tmp_path / "events.db"),
    }


def _service(
    stores: dict[str, Any],
    pm: StubPushManager,
    *,
    device_status: dict[str, Any] | None = None,
) -> ButtonService:
    return ButtonService(
        rotation_store=stores["rotation_store"],
        state_store=stores["state_store"],
        settings_store=stores["settings_store"],
        page_store=stores["page_store"],
        push_manager=pm,  # type: ignore[arg-type]
        event_log=stores["event_log"],
        device_status=(lambda: device_status or {}),
    )


def _ha_region() -> dict[str, Any]:
    return {
        "x": 0,
        "y": 0,
        "w": 400,
        "h": 300,
        "depth": 1,
        "order": 0,
        "tap": {"action": "ha", "domain": "light", "service": "toggle", "data": {}},
        "swipe": None,
        "slide": None,
        "origin": "config",
        "dangling": [],
    }


def _stub_ha(svc: ButtonService) -> None:
    svc._call_ha = lambda domain, service, data: None  # type: ignore[method-assign]


def _latest(page_id: str | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {"digest": "art123", "composition_digest": "comp456", "ext": "bin"}
    if page_id is not None:
        info["page_id"] = page_id
    return info


def _tap(svc: ButtonService) -> Any:
    return svc.handle_touch(
        device_id="kitchen",
        stroke=TouchStroke(x0=10, y0=10, x1=10, y1=10),
        frame_digest="art123",
    )


# -- scheduling off the wake ----------------------------------------------


def test_ha_tap_never_pushes_inside_the_wake(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest(), regions=[_ha_region()])
    svc = _service(stores, pm)
    _stub_ha(svc)
    scheduled: list[str] = []
    svc._spawn_reconcile = scheduled.append  # type: ignore[method-assign]
    result = _tap(svc)
    assert result.outcome == "ha_dispatched"
    assert result.base.pushed_page_id is None
    assert pm.calls == []  # the wake returned without any synchronous push
    assert scheduled == ["kitchen"]


def test_burst_of_taps_arms_one_worker(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest(), regions=[_ha_region()])
    svc = _service(stores, pm)
    scheduled: list[str] = []
    svc._spawn_reconcile = scheduled.append  # type: ignore[method-assign]
    for _ in range(5):
        svc._schedule_ha_reconcile("kitchen")
    assert scheduled == ["kitchen"]  # re-arms the window, no extra workers


def test_worker_drains_and_clears_pending(stores: dict[str, Any]) -> None:
    stores["settings_store"].update_section("app", {"touch_repush_debounce_s": 0})
    stores["rotation_store"].upsert(
        Rotation(
            id="rot",
            name="Rot",
            device_ids=["kitchen"],
            steps=[RotationStep(page_id="morning", dwell_minutes=30)],
        )
    )
    pm = StubPushManager(latest=_latest(), regions=[_ha_region()])
    svc = _service(stores, pm)
    svc._spawn_reconcile = lambda device_id: None  # type: ignore[method-assign]
    svc._schedule_ha_reconcile("kitchen")
    svc._schedule_ha_reconcile("kitchen")
    svc._reconcile_worker("kitchen")
    assert [c["page_id"] for c in pm.calls] == ["morning"]  # coalesced to one
    assert svc._reconcile_last == {}


# -- page fallback chain --------------------------------------------------


def test_reconcile_uses_rotation_current_step(stores: dict[str, Any]) -> None:
    stores["rotation_store"].upsert(
        Rotation(
            id="rot",
            name="Rot",
            device_ids=["kitchen"],
            steps=[RotationStep(page_id="morning", dwell_minutes=30)],
        )
    )
    pm = StubPushManager(latest=_latest())
    svc = _service(stores, pm)
    svc._run_reconcile("kitchen")
    assert pm.calls and pm.calls[0]["page_id"] == "morning"
    assert pm.calls[0]["source"] == "touch"
    assert pm.calls[0]["device_ids"] == {"kitchen"}


def test_reconcile_falls_back_to_last_pushed_page(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest(page_id="lights"))
    svc = _service(stores, pm)
    svc._run_reconcile("kitchen")
    assert pm.calls and pm.calls[0]["page_id"] == "lights"


def test_reconcile_without_any_page_is_a_noop(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest())  # no rotation, no deck, no page_id
    svc = _service(stores, pm)
    svc._run_reconcile("kitchen")
    assert pm.calls == []
    assert pm.patch_calls == []


# -- patch-capable routing ------------------------------------------------


def test_patch_capable_device_reconciles_via_patches(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest(page_id="lights"))
    svc = _service(
        stores, pm, device_status={"kitchen": {"overlay": {"schema": 2, "max_targets": 32}}}
    )
    svc._run_reconcile("kitchen")
    assert pm.patch_calls == [{"device_id": "kitchen", "page_id": "lights", "panel": {}}]
    assert pm.calls == []  # no full push when the patch path succeeded


def test_schema_1_device_gets_a_full_push(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest(page_id="lights"))
    svc = _service(stores, pm, device_status={"kitchen": {"overlay": {"schema": 1}}})
    svc._run_reconcile("kitchen")
    assert pm.patch_calls == []
    assert pm.calls and pm.calls[0]["page_id"] == "lights"


def test_patch_failure_falls_back_to_full_push(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest(page_id="lights"), patch_outcome="failed")
    svc = _service(stores, pm, device_status={"kitchen": {"overlay": {"schema": 2}}})
    svc._run_reconcile("kitchen")
    assert len(pm.patch_calls) == 1
    assert pm.calls and pm.calls[0]["page_id"] == "lights"


def test_debounce_window_tracks_capability(stores: dict[str, Any]) -> None:
    pm = StubPushManager()
    svc = _service(stores, pm, device_status={"kitchen": {"overlay": {"schema": 2}}})
    assert svc._reconcile_debounce_seconds("kitchen") == pytest.approx(0.4)
    assert svc._reconcile_debounce_seconds("other") == pytest.approx(3.0)
    stores["settings_store"].update_section(
        "app", {"touch_patch_debounce_s": 1.5, "touch_repush_debounce_s": 7}
    )
    assert svc._reconcile_debounce_seconds("kitchen") == pytest.approx(1.5)
    assert svc._reconcile_debounce_seconds("other") == pytest.approx(7.0)


# -- webhook_refresh (#242) -----------------------------------------------


def _webhook_region(spec: str) -> dict[str, Any]:
    action, _, arg = spec.partition(":")
    return {
        "x": 0,
        "y": 0,
        "w": 400,
        "h": 300,
        "depth": 1,
        "order": 0,
        "tap": {"action": action, "arg": arg},
        "swipe": None,
        "slide": None,
        "origin": "config",
        "dangling": [],
    }


def _button_service_with_map(stores: dict[str, Any], pm: StubPushManager, spec: str):
    stores["settings_store"].update_section("app", {"button_map": {"a": spec}})
    return _service(stores, pm)


def _press(svc: ButtonService) -> Any:
    return svc.handle_button(device_id="kitchen", button="a", event_id=1)


def test_webhook_refresh_fires_the_webhook_and_arms_a_reconcile(stores: dict[str, Any]) -> None:
    """The whole point of #242: the panel catches up after the receiver
    has had a chance to act on the POST."""
    pm = StubPushManager(latest=_latest())
    svc = _button_service_with_map(stores, pm, "webhook_refresh:https://example.test/book")
    fired: list[tuple[str, dict[str, Any]]] = []
    svc._fire_webhook_async = lambda url, payload: fired.append((url, payload))  # type: ignore[method-assign]
    scheduled: list[str] = []
    svc._spawn_reconcile = scheduled.append  # type: ignore[method-assign]

    result = _press(svc)

    assert [u for u, _ in fired] == ["https://example.test/book"]
    assert scheduled == ["kitchen"]
    assert result.push_result is None  # nothing pushed inside the wake


def test_plain_webhook_still_does_not_reconcile(stores: dict[str, Any]) -> None:
    """Existing `webhook:` buttons keep their fire-and-forget behaviour;
    #242 must not start repainting panels nobody asked to repaint."""
    pm = StubPushManager(latest=_latest())
    svc = _button_service_with_map(stores, pm, "webhook:https://example.test/book")
    fired: list[str] = []
    svc._fire_webhook_async = lambda url, payload: fired.append(url)  # type: ignore[method-assign]
    scheduled: list[str] = []
    svc._spawn_reconcile = scheduled.append  # type: ignore[method-assign]

    _press(svc)

    assert fired == ["https://example.test/book"]
    assert scheduled == []


def test_webhook_refresh_waits_longer_than_the_normal_debounce(stores: dict[str, Any]) -> None:
    """The delay floor is what gives the receiver time to commit. Without
    it the reconcile would read pre-action state."""
    stores["settings_store"].update_section(
        "app", {"touch_repush_debounce_s": 0, "button_webhook_refresh_delay_s": 12}
    )
    pm = StubPushManager(latest=_latest())
    svc = _service(stores, pm)
    svc._spawn_reconcile = lambda device_id: None  # type: ignore[method-assign]

    svc._schedule_reconcile("kitchen", min_delay_s=svc._webhook_refresh_delay_seconds())

    assert svc._reconcile_min_delay["kitchen"] == 12.0


def test_reconcile_delay_floor_is_a_max_not_a_replacement(stores: dict[str, Any]) -> None:
    """A burst mixing an HA tap and a webhook_refresh waits for the longer
    of the two, so the webhook's receiver still gets its grace period."""
    pm = StubPushManager(latest=_latest())
    svc = _service(stores, pm)
    svc._spawn_reconcile = lambda device_id: None  # type: ignore[method-assign]

    svc._schedule_reconcile("kitchen", min_delay_s=9.0)
    svc._schedule_reconcile("kitchen")  # HA tap, no floor
    svc._schedule_reconcile("kitchen", min_delay_s=4.0)

    assert svc._reconcile_min_delay["kitchen"] == 9.0


def test_worker_clears_the_delay_floor_when_it_drains(stores: dict[str, Any]) -> None:
    """A leaked floor would slow every later reconcile on that device."""
    stores["settings_store"].update_section("app", {"touch_repush_debounce_s": 0})
    stores["rotation_store"].upsert(
        Rotation(
            id="rot",
            name="Rot",
            device_ids=["kitchen"],
            steps=[RotationStep(page_id="morning", dwell_minutes=30)],
        )
    )
    pm = StubPushManager(latest=_latest())
    svc = _service(stores, pm)
    svc._spawn_reconcile = lambda device_id: None  # type: ignore[method-assign]

    svc._schedule_reconcile("kitchen", min_delay_s=0)
    svc._reconcile_worker("kitchen")

    assert svc._reconcile_min_delay == {}
    assert svc._reconcile_last == {}


def test_webhook_refresh_delay_default_when_unset(stores: dict[str, Any]) -> None:
    pm = StubPushManager(latest=_latest())
    svc = _service(stores, pm)
    assert svc._webhook_refresh_delay_seconds() == 5.0


def test_webhook_refresh_delay_rejects_a_bool(stores: dict[str, Any]) -> None:
    """``True`` is an int in Python; a config typo must not become a 1s delay."""
    stores["settings_store"].update_section("app", {"button_webhook_refresh_delay_s": True})
    pm = StubPushManager(latest=_latest())
    svc = _service(stores, pm)
    assert svc._webhook_refresh_delay_seconds() == 5.0
