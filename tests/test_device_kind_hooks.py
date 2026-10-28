"""``device_service`` kind hooks: a device kind with rules of its own registers
callbacks once, and the settings routes, the Home Assistant config setter and
the add / register / remove flows call them for every device without naming
the kind."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import device_service
from app.main import REPO_ROOT, create_app

KIND = "circuitpython_generic"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[Any, ...]]]:
    """A hook set for ``circuitpython_generic`` devices (and nothing else) that
    records what it was asked, and refuses a ``note`` of ``"refused"`` or a
    ``sleep_interval_s`` of 777."""
    monkeypatch.setattr(device_service, "_KIND_HOOKS", {})
    log: list[tuple[Any, ...]] = []

    def conflict(
        _devices: Any, _store: Any, device: Any, values: dict[str, Any], exclude: str
    ) -> Any:
        log.append(("conflict", device.id, dict(values), exclude))
        refused = values.get("note") == "refused" or str(values.get("sleep_interval_s")) == "777"
        return "that value is refused" if refused else None

    def saved(_store: Any, device: Any, values: dict[str, Any], previous: dict[str, Any]) -> None:
        log.append(("saved", device.id, dict(values), dict(previous)))

    def added(_store: Any, device: Any) -> None:
        log.append(("added", device.id))

    def discovered(parsed: dict[str, Any]) -> dict[str, Any]:
        announced = parsed.get("sleep_interval_s")
        return {"sleep_interval_s": announced} if announced else {}

    def removed(device: Any) -> None:
        log.append(("removed", device.id))

    device_service.register_kind_hooks(
        "test",
        device_service.KindHooks(
            applies_to=lambda d: (
                getattr(d, "kind_of", None) == KIND or getattr(d, "id", "") == KIND
            ),
            config_conflict=conflict,
            config_saved=saved,
            device_added=added,
            discovered_config=discovered,
            device_removed=removed,
        ),
    )
    yield log


def _client(app: Flask) -> Any:
    client = app.test_client()
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    return client


def test_without_hooks_a_save_is_a_plain_store(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(device_service, "_KIND_HOOKS", {})
    client = _client(app)
    client.post("/settings/devices/add", data={"kind": KIND, "id": "kitchen", "name": "Kitchen"})
    device = app.config["DEVICE_REGISTRY"].get("kitchen")
    store = app.config["SETTINGS_STORE"]
    fields = [{"name": "note"}]
    assert (
        device_service.save_device_config(
            store, app.config["DEVICE_REGISTRY"], device, {"note": "x"}, fields
        )
        is None
    )
    assert store.get_section("devices")["kitchen"]["note"] == "x"
    assert device_service.discovered_config(device, {"note": "x"}) == {}


def test_a_kinds_hooks_veto_store_and_follow_a_save(
    app: Flask, calls: list[tuple[Any, ...]]
) -> None:
    client = _client(app)
    client.post("/settings/devices/add", data={"kind": KIND, "id": "kitchen", "name": "Kitchen"})
    client.post("/settings/devices/add", data={"kind": "esp32_client", "id": "attic", "name": "A"})
    registry = app.config["DEVICE_REGISTRY"]
    store = app.config["SETTINGS_STORE"]
    kitchen, attic = registry.get("kitchen"), registry.get("attic")
    assert ("added", "kitchen") in calls and ("added", "attic") not in calls
    fields = [{"name": "note"}]

    calls.clear()
    store.update_item("devices", "kitchen", {"note": "before"})
    assert (
        device_service.save_device_config(store, registry, kitchen, {"note": "after"}, fields)
        is None
    )
    assert [c[0] for c in calls] == ["conflict", "saved"]
    assert calls[0][3] == "kitchen"  # the device being saved is excluded from the check
    assert calls[1][2:] == ({"note": "after"}, {"note": "before"})
    assert store.get_section("devices")["kitchen"]["note"] == "after"

    # A refusal stores nothing and runs no after-the-fact hook.
    calls.clear()
    problem = device_service.save_device_config(
        store, registry, kitchen, {"note": "refused"}, fields
    )
    assert problem == "that value is refused"
    assert store.get_section("devices")["kitchen"]["note"] == "after"
    assert [c[0] for c in calls] == ["conflict"]

    # Another kind never reaches the hooks.
    calls.clear()
    assert (
        device_service.save_device_config(store, registry, attic, {"note": "refused"}, fields)
        is None
    )
    assert calls == []

    # The early check works on a kind, before any device exists.
    assert device_service.config_conflict(registry, store, registry.get(KIND), {"note": "refused"})


def test_the_save_routes_and_the_card_honour_a_refusal(
    app: Flask, calls: list[tuple[Any, ...]]
) -> None:
    client = _client(app)
    client.post("/settings/devices/add", data={"kind": KIND, "id": "kitchen", "name": "Kitchen"})
    store = app.config["SETTINGS_STORE"]
    # The generic per-device settings route.
    resp = client.post(
        "/settings/device-kitchen", data={"sleep_interval_s": "777"}, follow_redirects=True
    )
    assert "that value is refused" in resp.get_data(as_text=True)
    assert store.get_section("devices").get("kitchen", {}).get("sleep_interval_s") != 777
    # The device card's combined save.
    resp = client.post(
        "/settings/devices/kitchen/save", data={"sleep_interval_s": "777"}, follow_redirects=True
    )
    assert "that value is refused" in resp.get_data(as_text=True)
    assert store.get_section("devices").get("kitchen", {}).get("sleep_interval_s") != 777


def test_a_discovered_device_gets_the_values_its_payload_announces(
    app: Flask, calls: list[tuple[Any, ...]]
) -> None:
    client = _client(app)
    app.config["DISCOVERY_CACHE"].record(
        "cp_desk", f'{{"kind":"{KIND}","sleep_interval_s":600}}'.encode()
    )
    resp = client.post(
        "/settings/devices/discovery/cp_desk/register", data={"id": "desk", "name": "Desk"}
    )
    assert resp.status_code == 302
    assert app.config["SETTINGS_STORE"].get_section("devices")["desk"]["sleep_interval_s"] == 600
    assert ("saved", "desk", {"sleep_interval_s": 600}, {}) in calls
    # The added hook fires for a device registered from the discovery strip,
    # after the announced config is stored, as for one added by hand.
    assert ("added", "desk") in calls
    assert calls.index(("saved", "desk", {"sleep_interval_s": 600}, {})) < calls.index(
        ("added", "desk")
    )

    # Announced values the kind's rules refuse: nothing is created.
    app.config["DISCOVERY_CACHE"].record(
        "cp_shed", f'{{"kind":"{KIND}","sleep_interval_s":777}}'.encode()
    )
    resp = client.post(
        "/settings/devices/discovery/cp_shed/register",
        data={"id": "shed", "name": "Shed"},
        follow_redirects=True,
    )
    assert "Device not registered" in resp.get_data(as_text=True)
    assert app.config["DEVICE_REGISTRY"].get("shed") is None
    assert ("added", "shed") not in calls


def test_removing_a_device_tells_its_kinds_hooks(app: Flask, calls: list[tuple[Any, ...]]) -> None:
    client = _client(app)
    client.post("/settings/devices/add", data={"kind": KIND, "id": "kitchen", "name": "Kitchen"})
    device_service.delete_instance(
        devices=app.config["DEVICE_REGISTRY"],
        renderers=app.config["RENDERER_REGISTRY"],
        instance_id="kitchen",
    )
    assert ("removed", "kitchen") in calls


def test_a_failing_after_hook_does_not_undo_the_save(
    app: Flask, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(device_service, "_KIND_HOOKS", {})

    def boom(*_a: Any) -> None:
        raise RuntimeError("hook bug")

    device_service.register_kind_hooks(
        "boom",
        device_service.KindHooks(
            applies_to=lambda _d: True, config_saved=boom, device_removed=boom
        ),
    )
    client = _client(app)
    client.post("/settings/devices/add", data={"kind": KIND, "id": "kitchen", "name": "Kitchen"})
    registry, store = app.config["DEVICE_REGISTRY"], app.config["SETTINGS_STORE"]
    assert (
        device_service.save_device_config(
            store, registry, registry.get("kitchen"), {"note": "x"}, [{"name": "note"}]
        )
        is None
    )
    assert store.get_section("devices")["kitchen"]["note"] == "x"
