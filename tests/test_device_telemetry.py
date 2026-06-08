"""Per-device telemetry store: heartbeat → prediction + confidence math.

Covers the smart-sync step 1 contract (issue #10): the store reads
optional firmware-published fields, falls back to a configured sleep
interval, ramps a confidence counter, and persists everything across
restarts so a server bounce doesn't reset trust.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.state.device_telemetry import (
    CONFIDENCE_TRUSTED_AT,
    ON_TIME_TOLERANCE_S,
    TelemetryStore,
)


def _make_store(tmp_path: Path) -> TelemetryStore:
    return TelemetryStore(tmp_path / "core" / "device_telemetry.json")


def test_record_with_configured_sleep_only_uses_fallback(tmp_path: Path) -> None:
    """Firmware doesn't publish sleep_until / next_sleep_s; the
    configured interval feeds the prediction."""
    store = _make_store(tmp_path)
    entry = store.record_heartbeat(
        "esp32_bedside",
        received_at=1_000_000.0,
        parsed={"battery_pct": 72},
        configured_sleep_s=600,
    )
    assert entry.last_heartbeat_at == 1_000_000.0
    assert entry.last_sleep_interval_s == 600
    assert entry.predicted_next_wake_at == 1_000_600.0
    assert entry.consecutive_on_time_wakes == 0  # first beat, no prior prediction


def test_record_prefers_sleep_until_over_next_sleep_s(tmp_path: Path) -> None:
    """``sleep_until`` (absolute wake time) is more accurate than
    ``next_sleep_s``; the parser should always pick it when both come
    in. The configured fallback is ignored too."""
    store = _make_store(tmp_path)
    entry = store.record_heartbeat(
        "esp32_office",
        received_at=1_000_000.0,
        parsed={"sleep_until": 1_001_234.0, "next_sleep_s": 500},
        configured_sleep_s=600,
    )
    assert entry.predicted_next_wake_at == 1_001_234.0
    # Derived interval should reflect the wake-time math, not the
    # less-accurate published next_sleep_s or the fallback.
    assert entry.last_sleep_interval_s == 1234


def test_record_uses_next_sleep_s_when_no_sleep_until(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    entry = store.record_heartbeat(
        "esp32_office",
        received_at=1_000_000.0,
        parsed={"next_sleep_s": 450},
        configured_sleep_s=600,
    )
    assert entry.last_sleep_interval_s == 450
    assert entry.predicted_next_wake_at == 1_000_450.0


def test_record_with_no_signal_drops_prediction_keeps_prior_interval(
    tmp_path: Path,
) -> None:
    """A heartbeat that carries no sleep info AND no configured
    fallback leaves the device unpredictable. Past interval is kept
    for display continuity but the wake-time prediction is dropped."""
    store = _make_store(tmp_path)
    # Seed with a prior interval.
    store.record_heartbeat(
        "esp32_office",
        received_at=1_000_000.0,
        parsed={"next_sleep_s": 300},
        configured_sleep_s=None,
    )
    # Now a partial heartbeat with no sleep info.
    entry = store.record_heartbeat(
        "esp32_office",
        received_at=1_000_300.0,
        parsed={"battery_pct": 65},
        configured_sleep_s=None,
    )
    assert entry.last_sleep_interval_s == 300  # carried over for the UI
    assert entry.predicted_next_wake_at is None  # but no live prediction


def test_confidence_ramps_on_consecutive_on_time_wakes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    t0 = 1_000_000.0
    store.record_heartbeat(
        "esp32_office",
        received_at=t0,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    # CONFIDENCE_TRUSTED_AT consecutive on-time wakes flip the device
    # to trusted. Each wake arrives exactly on the predicted moment.
    for n in range(1, CONFIDENCE_TRUSTED_AT + 1):
        entry = store.record_heartbeat(
            "esp32_office",
            received_at=t0 + n * 600,
            parsed={"next_sleep_s": 600},
            configured_sleep_s=None,
        )
        assert entry.consecutive_on_time_wakes == n
        assert entry.last_wake_offset_s == 0
    assert entry.is_trusted is True


def test_late_wake_within_tolerance_still_counts(tmp_path: Path) -> None:
    """An on-time wake is +/- ON_TIME_TOLERANCE_S of the prediction.
    A small jitter (e.g. WiFi reconnect taking a few seconds) shouldn't
    reset trust."""
    store = _make_store(tmp_path)
    t0 = 1_000_000.0
    store.record_heartbeat(
        "x",
        received_at=t0,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    # Wake +30s late, well within the 60s tolerance.
    entry = store.record_heartbeat(
        "x",
        received_at=t0 + 600 + 30,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    assert entry.consecutive_on_time_wakes == 1
    assert entry.last_wake_offset_s == 30


def test_missed_wake_resets_confidence(tmp_path: Path) -> None:
    """An out-of-tolerance wake (a forced wake, a sleep miscount,
    a clock skew) flushes trust so the scheduler stops JIT-ing."""
    store = _make_store(tmp_path)
    t0 = 1_000_000.0
    store.record_heartbeat(
        "x",
        received_at=t0,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    # Build up some confidence first.
    for n in range(1, 3):
        store.record_heartbeat(
            "x",
            received_at=t0 + n * 600,
            parsed={"next_sleep_s": 600},
            configured_sleep_s=None,
        )
    # Now wake way late (offset > tolerance) → reset.
    entry = store.record_heartbeat(
        "x",
        received_at=t0 + 3 * 600 + ON_TIME_TOLERANCE_S + 30,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    assert entry.consecutive_on_time_wakes == 0
    assert entry.last_wake_offset_s is not None
    assert entry.last_wake_offset_s > ON_TIME_TOLERANCE_S


def test_persistence_round_trip_keeps_confidence(tmp_path: Path) -> None:
    """A server restart shouldn't reset trust, otherwise smart-sync
    would have to warm up after every code update / HA add-on bump."""
    path = tmp_path / "core" / "device_telemetry.json"
    store_a = TelemetryStore(path)
    t0 = 1_000_000.0
    store_a.record_heartbeat(
        "esp32_office",
        received_at=t0,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    for n in range(1, CONFIDENCE_TRUSTED_AT + 1):
        store_a.record_heartbeat(
            "esp32_office",
            received_at=t0 + n * 600,
            parsed={"next_sleep_s": 600},
            configured_sleep_s=None,
        )

    # New store reading the same persisted file.
    store_b = TelemetryStore(path)
    entry = store_b.get("esp32_office")
    assert entry is not None
    assert entry.is_trusted
    assert entry.consecutive_on_time_wakes == CONFIDENCE_TRUSTED_AT


def test_forget_removes_entry(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.record_heartbeat(
        "gone",
        received_at=1.0,
        parsed={},
        configured_sleep_s=300,
    )
    assert store.get("gone") is not None
    store.forget("gone")
    assert store.get("gone") is None
    # And the persisted file no longer carries it.
    data = json.loads((tmp_path / "core" / "device_telemetry.json").read_text())
    assert "gone" not in data["devices"]


def test_get_returns_independent_copy(tmp_path: Path) -> None:
    """The store hands out copies, so a caller mutating the returned
    entry doesn't corrupt the in-memory cache."""
    store = _make_store(tmp_path)
    store.record_heartbeat(
        "x",
        received_at=1.0,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    snap = store.get("x")
    assert snap is not None
    snap.consecutive_on_time_wakes = 999  # mutate the copy
    fresh = store.get("x")
    assert fresh is not None
    assert fresh.consecutive_on_time_wakes == 0  # original untouched


def test_double_heartbeat_within_debounce_does_not_reset_confidence(tmp_path: Path) -> None:
    """Issue surfaced by real-world ESP32 testing: some firmwares send
    a heartbeat on connect AND a second heartbeat just before deep
    sleep. Without debouncing, the second one looks like a wake that
    arrived ~0s after the first, with offset ~= -sleep_cycle, and
    confidence resets to 0 every cycle. The store now treats beats
    within WAKE_DEBOUNCE_S as the same wake."""
    store = _make_store(tmp_path)
    t0 = 1_000_000.0
    # Set up trust across three real wakes 600s apart.
    store.record_heartbeat(
        "esp", received_at=t0, parsed={"next_sleep_s": 600}, configured_sleep_s=None
    )
    for n in range(1, CONFIDENCE_TRUSTED_AT + 1):
        store.record_heartbeat(
            "esp",
            received_at=t0 + n * 600,
            parsed={"next_sleep_s": 600},
            configured_sleep_s=None,
        )
    entry = store.get("esp")
    assert entry is not None and entry.is_trusted

    # A second heartbeat 2 seconds later (firmware's "going to sleep"
    # beat) must NOT reset confidence.
    after_debounce = store.record_heartbeat(
        "esp",
        received_at=t0 + CONFIDENCE_TRUSTED_AT * 600 + 2,
        parsed={"next_sleep_s": 600},
        configured_sleep_s=None,
    )
    assert after_debounce.is_trusted
    assert after_debounce.consecutive_on_time_wakes == CONFIDENCE_TRUSTED_AT
