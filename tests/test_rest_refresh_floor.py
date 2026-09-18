"""Repaint floor enforcement on the delivery path (#250).

``refresh_floor_s`` is how fast a panel's glass can be repainted. Its only
historical enforcement point clamped the *poll* cadence, which is a
different thing, and removing that clamp left the field declared by 41
hardware profiles and applied nowhere.

What these pin: a new frame that would land inside the floor is held
rather than dropped, the device keeps painting what it has, the next poll
is pulled in to the moment the floor expires, and nothing about the hold
loses a resend or delays a device that declared no floor.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import refresh_floor
from app.main import REPO_ROOT, create_app

# A profile with a 60 s floor, and one device kind that declares none.
FLOORED_KIND = "seeed_reterminal_e1003"
FLOORLESS_KIND = "pico_bin_client"


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _register(app: Flask, client, device_id: str, kind: str) -> str:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": device_id, "kind": kind, "panel_w": 1872, "panel_h": 1404}),
    )
    assert resp.status_code == 201
    return resp.get_json()["device_token"]


def _seed_render(
    app: Flask,
    device_id: str,
    *,
    digest: str,
    served_digest: str | None = None,
    served_ago_s: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Put a render in the live slot, optionally with the delivery-side
    snapshot the floor measures from."""
    entry: dict[str, Any] = {
        "digest": digest,
        "ext": "bin",
        "filename": f"{digest}.bin",
        "renderer_id": "esp32_gray_bin",
        "timestamp": time.time(),
        "composition_digest": f"comp-{digest}",
    }
    entry.update(extra)
    if served_digest is not None:
        entry["last_served_digest"] = served_digest
        entry["last_served_at"] = time.time() - (served_ago_s or 0.0)
    app.config["PUSH_MANAGER"]._latest_renders[device_id] = entry
    return entry


# -- the module itself ---------------------------------------------------


def test_panel_floor_reads_the_manifest_field(app: Flask) -> None:
    registry = app.config["DEVICE_REGISTRY"]
    floored = next(k for k in registry.all() if k.id == FLOORED_KIND)
    floorless = next(k for k in registry.all() if k.id == FLOORLESS_KIND)
    assert refresh_floor.panel_floor_s(floored) == 60
    assert refresh_floor.panel_floor_s(floorless) is None


def test_floor_is_unmeasurable_without_a_recorded_handover(app: Flask) -> None:
    """A device that has never been served gives nothing to time from, and
    an unmeasurable floor must not withhold a frame."""
    client = app.test_client()
    _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="aaa")
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    assert refresh_floor.hold_remaining_s(device, app.config["PUSH_MANAGER"]) is None


def test_remaining_rounds_up_and_expires(app: Flask) -> None:
    client = app.test_client()
    _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="bbb", served_digest="aaa", served_ago_s=19.4)
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    push_mgr = app.config["PUSH_MANAGER"]
    # 40.6 s left, rounded up so a caller waking on it lands past the floor.
    assert refresh_floor.hold_remaining_s(device, push_mgr) == 41
    _seed_render(app, "e1003", digest="bbb", served_digest="aaa", served_ago_s=61)
    assert refresh_floor.hold_remaining_s(device, push_mgr) is None


def test_a_backwards_clock_does_not_hold_the_panel(app: Flask) -> None:
    """An NTP step or a container restart can put the handover in the
    future; that is a reason to ignore the floor, not to sit on a frame."""
    client = app.test_client()
    _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="bbb", served_digest="aaa", served_ago_s=-3600)
    device = app.config["DEVICE_REGISTRY"].get("e1003")
    assert refresh_floor.hold_remaining_s(device, app.config["PUSH_MANAGER"]) is None


# -- the delivery path ---------------------------------------------------


def test_new_frame_inside_the_floor_is_held_not_served(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="new", served_digest="old", served_ago_s=10)

    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={"Authorization": f"Bearer {token}", "If-None-Match": '"old"'},
    )
    assert resp.status_code == 304
    # The device is told to keep what it holds, and Content-Location names
    # that frame rather than the one it is not getting yet.
    assert resp.headers["ETag"] == '"old"'
    assert "/renders/old.bin" in resp.headers["Content-Location"]


def test_the_held_frame_lands_once_the_floor_expires(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="new", served_digest="old", served_ago_s=61)

    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={"Authorization": f"Bearer {token}", "If-None-Match": '"old"'},
    )
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "new"


def test_a_newer_render_during_the_hold_is_the_one_that_paints(app: Flask) -> None:
    """The frame is held, never queued, so the panel lands on the latest
    content rather than replaying what was pending when the floor started."""
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="first", served_digest="old", served_ago_s=10)
    headers = {"Authorization": f"Bearer {token}", "If-None-Match": '"old"'}
    assert client.get("/api/v1/device/e1003/frame", headers=headers).status_code == 304

    _seed_render(app, "e1003", digest="second", served_digest="old", served_ago_s=61)
    resp = client.get("/api/v1/device/e1003/frame", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "second"


def test_a_device_with_no_declared_floor_is_never_held(app: Flask) -> None:
    client = app.test_client()
    token = _register(app, client, "pico", FLOORLESS_KIND)
    _seed_render(app, "pico", digest="new", served_digest="old", served_ago_s=1)

    resp = client.get(
        "/api/v1/device/pico/frame",
        headers={"Authorization": f"Bearer {token}", "If-None-Match": '"old"'},
    )
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "new"


def test_a_client_without_a_cached_etag_is_served_immediately(app: Flask) -> None:
    """There is nothing to tell such a client to keep, and a panel that
    booted without a URL needs one more than the glass needs the floor."""
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="new", served_digest="old", served_ago_s=1)

    resp = client.get("/api/v1/device/e1003/frame", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.get_json()["render_id"] == "new"


def test_an_unchanged_poll_inside_the_floor_is_the_ordinary_304(app: Flask) -> None:
    """The floor only guards a repaint. A device polling the frame it
    already holds takes the conditional-GET path as before, which is what
    keeps Companion's pending badge honest."""
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="same", served_digest="same", served_ago_s=1)

    resp = client.get(
        "/api/v1/device/e1003/frame",
        headers={"Authorization": f"Bearer {token}", "If-None-Match": '"same"'},
    )
    assert resp.status_code == 304
    assert resp.headers["ETag"] == '"same"'
    assert app.config["PUSH_MANAGER"]._latest_renders["e1003"]["last_served_digest"] == "same"


def test_a_resend_held_by_the_floor_is_still_a_resend_after_it(app: Flask) -> None:
    """A resend (#119) bypasses the conditional GET. Held by the floor, its
    flag must survive, or the operator's Send is silently swallowed."""
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(
        app,
        "e1003",
        digest="same",
        served_digest="old",
        served_ago_s=5,
        force_refetch=True,
    )
    headers = {"Authorization": f"Bearer {token}", "If-None-Match": '"same"'}

    held = client.get("/api/v1/device/e1003/frame", headers=headers)
    assert held.status_code == 304
    assert app.config["PUSH_MANAGER"]._latest_renders["e1003"]["force_refetch"] is True

    app.config["PUSH_MANAGER"]._latest_renders["e1003"]["last_served_at"] = time.time() - 61
    after = client.get("/api/v1/device/e1003/frame", headers=headers)
    assert after.status_code == 200


# -- the poll that collects it -------------------------------------------


def test_next_poll_is_pulled_in_to_the_floor_expiry(app: Flask) -> None:
    """Otherwise a device that sleeps for an hour sits out the rest of that
    hour over a sixty-second floor."""
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="new", served_digest="old", served_ago_s=30)

    resp = client.post(
        "/api/v1/device/e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert resp.status_code == 200
    # 30 s of floor left, plus the second that lands the wake past it.
    assert resp.get_json()["next_poll_s"] == 31


def test_an_idle_device_is_not_woken_for_its_own_floor(app: Flask) -> None:
    """Nothing is pending, so the floor has nothing to come back for and
    the configured interval stands."""
    client = app.test_client()
    token = _register(app, client, "e1003", FLOORED_KIND)
    _seed_render(app, "e1003", digest="same", served_digest="same", served_ago_s=30)

    resp = client.post(
        "/api/v1/device/e1003/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({}),
    )
    assert resp.get_json()["next_poll_s"] > 31
