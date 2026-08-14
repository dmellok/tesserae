"""Capability support is read from what a device advertised, never from
what model it is (#225).

The rule these cover: three states, not two. "We haven't heard from it"
is a different answer from "it told us no", and collapsing them is how
the Offline Album form ended up offering every registered display and
saving a binding that could never be delivered.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.device_capability import (
    FRAME_CACHE,
    capability_support,
    capability_support_map,
    freshness_threshold_s,
    heartbeat_freshness,
)
from app.main import REPO_ROOT, create_app


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


def _device(app: Flask) -> Any:
    """A registered instance to compute states against."""
    client = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="d").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        json={
            "device_id": "kitchen",
            "kind": "pico_bin_client",
            "panel_w": 1600,
            "panel_h": 1200,
        },
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return app.config["DEVICE_REGISTRY"].get("kitchen")


def test_a_fresh_beat_advertising_the_capability_is_supported(app: Flask) -> None:
    with app.app_context():
        device = _device(app)
        status = {
            "received_at": time.time(),
            "frame_cache": {"schema": 1, "capacity_bytes": 8_000_000, "max_frames": 32},
        }
        support = capability_support(device, status, FRAME_CACHE)
    assert support["state"] == "supported"
    assert support["observed_at"] is not None
    # Nothing to explain when the answer is yes.
    assert "reason_code" not in support


def test_a_fresh_beat_without_the_capability_is_unsupported(app: Flask) -> None:
    """The one state we have evidence for. The device woke, reported, and
    said nothing about a frame cache: it has none right now."""
    with app.app_context():
        device = _device(app)
        support = capability_support(device, {"received_at": time.time()}, FRAME_CACHE)
    assert support["state"] == "unsupported"
    assert support["reason_code"] == "not_advertised"
    assert support["observed_at"] is not None


def test_a_device_that_has_never_reported_is_unknown(app: Flask) -> None:
    with app.app_context():
        device = _device(app)
        for status in (None, {}, {"received_at": "not-a-number"}):
            support = capability_support(device, status, FRAME_CACHE)
            assert support["state"] == "unknown"
            assert support["reason_code"] == "no_usable_heartbeat"
            assert support["observed_at"] is None


def test_a_stale_beat_is_unknown_not_unsupported(app: Flask) -> None:
    """Frame-cache support is current state: firmware advertises it only
    while storage is mounted, and a card can be pulled between wakes. An
    old beat is not evidence either way, but it is worth showing."""
    with app.app_context():
        device = _device(app)
        old = time.time() - freshness_threshold_s(device) - 60
        support = capability_support(device, {"received_at": old}, FRAME_CACHE)
        stale_but_advertised = capability_support(
            device,
            {"received_at": old, "frame_cache": {"schema": 1, "capacity_bytes": 1}},
            FRAME_CACHE,
        )
    assert support["state"] == "unknown"
    assert support["reason_code"] == "stale_heartbeat"
    assert support["observed_at"] is not None
    # Even a stale beat that *did* advertise it doesn't count as a yes.
    assert stale_but_advertised["state"] == "unknown"


def test_the_freshness_window_tracks_the_device_s_own_cadence(app: Flask) -> None:
    """A panel that sleeps for half an hour by design must not read as
    stale after five minutes of silence."""
    with app.app_context():
        device = _device(app)
        default_window = freshness_threshold_s(device)
        app.config["SETTINGS_STORE"].update_section(
            "devices", {"kitchen": {"sleep_interval_s": 1800}}
        )
        slow_window = freshness_threshold_s(device)
    assert slow_window == 5400
    assert slow_window > default_window
    assert default_window >= 300  # floor, so one skipped beat isn't fatal


def test_freshness_and_support_agree(app: Flask) -> None:
    with app.app_context():
        device = _device(app)
        now = time.time()
        assert heartbeat_freshness(device, {"received_at": now})[0] == "fresh"
        assert heartbeat_freshness(device, None) == ("unknown", None)
        mapped = capability_support_map(device, {"received_at": now})
    assert set(mapped) == {FRAME_CACHE}
