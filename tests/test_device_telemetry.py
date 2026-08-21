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
    ``next_sleep_s``; the parser should always pick it when the two
    agree (within the mismatch tolerance). The configured fallback
    is ignored too."""
    store = _make_store(tmp_path)
    # Both fields agree: 1_000_500 - 1_000_000 = 500s, matches
    # next_sleep_s=500. sleep_until wins as the more precise source.
    entry = store.record_heartbeat(
        "esp32_office",
        received_at=1_000_000.0,
        parsed={"sleep_until": 1_000_500.0, "next_sleep_s": 500},
        configured_sleep_s=600,
    )
    assert entry.predicted_next_wake_at == 1_000_500.0
    assert entry.last_sleep_interval_s == 500


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


def test_sleep_until_falls_back_to_next_sleep_s_when_they_disagree(tmp_path: Path) -> None:
    """Defensive sanity check: when firmware publishes both
    ``sleep_until`` and ``next_sleep_s`` and they disagree by more
    than 30 seconds, the absolute timestamp is almost certainly
    carrying clock skew (NTP not synced when computed, or a stale
    value). The relative duration is a duration and can't be wrong
    about length, so prefer it.

    Real-world repro: ESP32 firmware publishing
    ``sleep_until=<received+371>`` while ``next_sleep_s=60``, the
    server would happily trust the bad ``sleep_until`` and predict a
    wake 6 minutes out for a device actually waking in 1 minute,
    producing -307s offsets every cycle."""
    store = _make_store(tmp_path)
    t0 = 1_000_000.0
    entry = store.record_heartbeat(
        "esp32_bedside",
        received_at=t0,
        # firmware claims wake at t0+371, but also next_sleep_s=60.
        # The 371 is wrong (clock skew); the 60 is correct.
        parsed={"sleep_until": t0 + 371, "next_sleep_s": 60},
        configured_sleep_s=None,
    )
    # The bad sleep_until was rejected; we used next_sleep_s instead.
    assert entry.last_sleep_interval_s == 60
    assert entry.predicted_next_wake_at == t0 + 60


def test_sleep_until_still_trusted_when_close_to_next_sleep_s(tmp_path: Path) -> None:
    """Small disagreement (e.g. a couple of seconds between when
    ``sleep_until`` was computed and the heartbeat was actually
    published) is normal and within ``SLEEP_UNTIL_VS_NEXT_SLEEP_MISMATCH_S``.
    In that case we keep using the more-accurate absolute
    ``sleep_until`` value as documented."""
    store = _make_store(tmp_path)
    t0 = 1_000_000.0
    entry = store.record_heartbeat(
        "esp32_office",
        received_at=t0,
        # Off by 5s, well within the 30s tolerance, so trust the
        # absolute value (rounded interval should still be ~605).
        parsed={"sleep_until": t0 + 605, "next_sleep_s": 600},
        configured_sleep_s=None,
    )
    assert entry.predicted_next_wake_at == t0 + 605
    assert entry.last_sleep_interval_s == 605


# -- reprojection after a config change (#246) ----------------------------


def test_changing_the_interval_reprojects_a_configured_prediction(tmp_path) -> None:
    """A device moved from 1 minute to 1 hour looked overdue for the best
    part of an hour, because the stored prediction kept the old cadence
    until the device next woke."""
    from app.state.device_telemetry import TelemetryStore

    store = TelemetryStore(tmp_path / "t.json")
    store.record_heartbeat("panel", parsed={}, received_at=1000.0, configured_sleep_s=60)
    before = store.get("panel")
    assert before is not None and before.predicted_next_wake_at == 1060.0

    after = store.reproject("panel", 3600)
    assert after is not None
    assert after.predicted_next_wake_at == 1000.0 + 3600
    assert after.last_sleep_interval_s == 3600


def test_reprojection_leaves_a_firmware_prediction_alone(tmp_path) -> None:
    """The device's own statement about when it will wake outranks a
    server-side setting; overwriting it would undo the accuracy smart
    sync exists for."""
    from app.state.device_telemetry import TelemetryStore

    store = TelemetryStore(tmp_path / "t.json")
    store.record_heartbeat(
        "panel", parsed={"next_sleep_s": 120}, received_at=1000.0, configured_sleep_s=60
    )
    assert store.reproject("panel", 3600) is None
    entry = store.get("panel")
    assert entry is not None and entry.predicted_next_wake_at == 1120.0


def test_reprojection_resets_confidence(tmp_path) -> None:
    """Past on-time wakes say nothing about a cadence that just changed."""
    from app.state.device_telemetry import TelemetryStore

    store = TelemetryStore(tmp_path / "t.json")
    store.record_heartbeat("panel", parsed={}, received_at=1000.0, configured_sleep_s=60)
    store.record_heartbeat("panel", parsed={}, received_at=1060.0, configured_sleep_s=60)
    store.record_heartbeat("panel", parsed={}, received_at=1120.0, configured_sleep_s=60)
    assert store.get("panel").consecutive_on_time_wakes > 0

    after = store.reproject("panel", 3600)
    assert after is not None and after.consecutive_on_time_wakes == 0


def test_reprojection_is_a_noop_when_the_interval_is_unchanged(tmp_path) -> None:
    from app.state.device_telemetry import TelemetryStore

    store = TelemetryStore(tmp_path / "t.json")
    store.record_heartbeat("panel", parsed={}, received_at=1000.0, configured_sleep_s=60)
    assert store.reproject("panel", 60) is None


def test_reprojection_without_a_heartbeat_is_a_noop(tmp_path) -> None:
    from app.state.device_telemetry import TelemetryStore

    store = TelemetryStore(tmp_path / "t.json")
    assert store.reproject("never-seen", 3600) is None


def test_reprojection_rejects_a_nonsense_interval(tmp_path) -> None:
    from app.state.device_telemetry import TelemetryStore

    store = TelemetryStore(tmp_path / "t.json")
    store.record_heartbeat("panel", parsed={}, received_at=1000.0, configured_sleep_s=60)
    assert store.reproject("panel", 0) is None
