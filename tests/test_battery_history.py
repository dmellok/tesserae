"""BatteryHistory store + phase-aware robust regression coverage."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.state.battery_history import BatteryHistory, Prediction


@pytest.fixture
def store(tmp_path: Path) -> BatteryHistory:
    return BatteryHistory(tmp_path / "battery.db")


def test_record_and_read_back(store: BatteryHistory) -> None:
    now = time.time()
    store.record("esp32", pct=87, battery_mv=4100, timestamp=now - 3600)
    store.record("esp32", pct=85, battery_mv=4080, timestamp=now)
    rows = store.recent("esp32", window_days=1)
    assert [r.pct for r in rows] == [87, 85]
    assert [r.battery_mv for r in rows] == [4100, 4080]
    # Ordered oldest first.
    assert rows[0].timestamp < rows[1].timestamp


def test_recent_window_filters_out_old_rows(store: BatteryHistory) -> None:
    now = time.time()
    store.record("esp32", pct=50, timestamp=now - 30 * 86400)
    store.record("esp32", pct=80, timestamp=now - 86400 // 2)
    rows = store.recent("esp32", window_days=7)
    assert [r.pct for r in rows] == [80]


def test_forget_drops_only_target_device(store: BatteryHistory) -> None:
    now = time.time()
    store.record("esp32", pct=80, timestamp=now)
    store.record("photopainter", pct=60, timestamp=now)
    store.forget("esp32")
    assert store.device_ids() == ["photopainter"]
    assert store.recent("esp32", window_days=7) == []


def test_predict_returns_none_when_not_enough_samples(store: BatteryHistory) -> None:
    now = time.time()
    for i in range(5):  # below MIN_SAMPLES=8
        store.record("esp32", pct=80 - i, timestamp=now - i * 3600)
    assert store.predict("esp32", window_days=7) is None


def test_predict_returns_no_projection_for_flat_battery(store: BatteryHistory) -> None:
    now = time.time()
    for i in range(20):
        store.record("esp32", pct=80, timestamp=now - i * 3600)
    pred = store.predict("esp32", window_days=7)
    assert pred is not None
    assert pred.days_to_empty is None
    assert pred.days_to_20pct is None
    assert abs(pred.slope_per_day) < 0.1


def test_predict_projects_days_to_empty_on_a_clean_drain(store: BatteryHistory) -> None:
    """Synthesise 8 samples over 6.5 days at a clean -10 %/day drain.
    Newest is at ~now (30%), oldest is at 6.5 days ago (95%). Predicts
    days_to_20pct ≈ 1 and days_to_empty ≈ 3."""
    now = time.time()
    span_days = 6.5  # oldest sample sits comfortably inside the 7-day window
    slope = -10.0
    for i in range(8):
        days_ago = span_days * (7 - i) / 7.0  # 6.5, 5.57, ..., 0.93, 0
        pct = 30 - slope * days_ago  # 30 + 10*days_ago
        store.record("esp32", pct=round(pct), timestamp=now - days_ago * 86400)
    pred = store.predict("esp32", window_days=7)
    assert pred is not None
    assert isinstance(pred, Prediction)
    assert pred.slope_per_day == pytest.approx(-10.0, abs=0.5)
    assert pred.current_pct == pytest.approx(30.0, abs=0.5)
    # current 30 → 20 in 1 day, → 0 in 3 days
    assert pred.days_to_20pct == pytest.approx(1.0, abs=0.3)
    assert pred.days_to_empty == pytest.approx(3.0, abs=0.5)
    assert pred.samples == 8


def test_predict_uses_last_discharge_segment_after_a_recharge(
    store: BatteryHistory,
) -> None:
    """Fleet reality: most battery devices get topped up periodically,
    so a 7-day window often contains a charge event mid-stream. A
    naive whole-window regression then reads as flat or rising and
    returns no projection (this was the prod complaint after v0.55.1
    shipped). The fix isolates the most recent monotonic-discharge
    segment and fits on it instead.

    Build a series with a clean -10 %/day drain in days 7..4, a
    sharp +60% charge event at day 3.5, and another clean -10 %/day
    drain in days 3..0. The whole-window regression would slope
    positive; the last-segment regression should slope at ~-10."""
    now = time.time()
    # Drain phase: 70% (day 7) → 40% (day 4)
    drain_phase_a = [(7.0, 70), (6.0, 60), (5.0, 50), (4.0, 40)]
    # Charge event: 40% → 100% (delta=+60) at day 3.5
    charge_phase = [(3.5, 100)]
    # Drain phase: 100% (day 3) → 70% (day 0)
    drain_phase_b = [(3.0, 100), (2.0, 90), (1.0, 80), (0.0, 70)]
    for days_ago, pct in drain_phase_a + charge_phase + drain_phase_b:
        store.record("esp32_topup", pct=pct, timestamp=now - days_ago * 86400)
    pred = store.predict("esp32_topup", window_days=7)
    assert pred is not None
    # Slope read from the post-charge segment, not the whole window.
    assert pred.slope_per_day < -1.0, (
        f"expected a draining slope, got {pred.slope_per_day:+.2f}/day"
    )
    # Projection lands (not None) because the segment is monotonically
    # draining.
    assert pred.days_to_20pct is not None
    assert pred.days_to_empty is not None
    # current_pct comes from the LAST sample of the full series
    # (which sits at 70%), not the segment.
    assert pred.current_pct == pytest.approx(70.0, abs=2.0)


def test_predict_falls_back_to_full_window_when_segment_too_short(
    store: BatteryHistory,
) -> None:
    """If the most recent discharge segment is shorter than
    MIN_SEGMENT_SAMPLES (e.g. a recharge happened just an hour ago),
    fall through to the full-window regression so the user still
    gets a slope reading."""
    now = time.time()
    # 8 samples over 6.5 days of clean -10 %/day drain, then a single
    # very recent charge spike that creates a too-short post-charge
    # segment (just one sample after the jump).
    for i in range(8):
        days_ago = 6.5 * (8 - i) / 8.0
        pct = 30 - (-10.0) * days_ago
        store.record("esp32_recent_charge", pct=round(pct), timestamp=now - days_ago * 86400)
    # Drop a single fresh "just charged" sample. Segment is length 1 → falls back.
    store.record("esp32_recent_charge", pct=100, timestamp=now - 60)
    pred = store.predict("esp32_recent_charge", window_days=7)
    assert pred is not None
    # Full window includes the charge spike at the end, but the
    # fallback regression on the whole series still reads negative
    # because the bulk of the data is the clean drain.
    assert pred.slope_per_day < 0


def test_predict_returns_days_to_full_on_a_sustained_charge(
    store: BatteryHistory,
) -> None:
    """Battery on the charger: percent rises monotonically over the
    most recent samples. ``days_to_full`` should project the time to
    100% from the latest charging segment's slope, not from a
    discharge slope elsewhere in the window."""
    now = time.time()
    # 12 samples over 6 days, all monotonically rising from 30 → 90%
    # (10 %/day). The segment IS the full window in this case.
    for i in range(12):
        days_ago = 6.0 * (11 - i) / 11
        pct = 30 + 10 * (6.0 - days_ago)  # rises from 30 to 90 over 6 days
        store.record("esp32_charging", pct=round(pct), timestamp=now - days_ago * 86400)
    pred = store.predict("esp32_charging", window_days=7)
    assert pred is not None
    assert pred.is_charging is True
    assert pred.charge_rate_per_day is not None
    assert pred.charge_rate_per_day > 0
    # No prior clean discharge exists, so charging must not leak its
    # positive slope into the drain-rate field.
    assert pred.slope_per_day is None
    assert pred.days_to_full is not None
    # current 90%, slope 10 %/day → 1 day to 100%
    assert pred.days_to_full == pytest.approx(1.0, abs=0.3)
    # Discharge projections must NOT be present when charging.
    assert pred.days_to_empty is None
    assert pred.days_to_20pct is None


def test_predict_no_days_to_full_when_flat(store: BatteryHistory) -> None:
    """A perfectly flat battery isn't charging; we don't show a
    "Full in ∞" indicator. The slope-magnitude gate
    (``MIN_CHARGE_PER_DAY``) catches this even though
    ``_last_charging_segment`` would otherwise return the full
    flat window."""
    now = time.time()
    for i in range(20):
        store.record("esp32_flat", pct=80, timestamp=now - i * 3600)
    pred = store.predict("esp32_flat", window_days=7)
    assert pred is not None
    assert pred.days_to_full is None


def test_predict_no_days_to_full_after_a_recent_discharge(
    store: BatteryHistory,
) -> None:
    """A device that drained yesterday and is flat today shouldn't
    show "Full in" just because the slope is technically near zero.
    The trailing samples must show sustained charging."""
    now = time.time()
    # Drain phase ending 1 day ago, then flat at 60% for the last day.
    for i in range(10):
        days_ago = 7.0 - 0.5 * i  # 7.0, 6.5, ..., 2.5
        pct = 90 - i * 3  # 90, 87, ..., 63
        store.record("esp32_mixed", pct=pct, timestamp=now - days_ago * 86400)
    for i in range(8):
        store.record("esp32_mixed", pct=60, timestamp=now - i * 3600)
    pred = store.predict("esp32_mixed", window_days=7)
    assert pred is not None
    assert pred.days_to_full is None, (
        f"flat trailing samples shouldn't project to full; got days_to_full={pred.days_to_full}"
    )


def test_predict_days_to_full_zero_when_already_at_or_above_100(
    store: BatteryHistory,
) -> None:
    """A device that's just hit 100% should render "Full now" (i.e.
    0 days), not be hidden as "not charging anymore"."""
    now = time.time()
    # Climbing 50 → 100 over 5 days.
    for i in range(10):
        days_ago = 5.0 * (9 - i) / 9
        pct = 50 + 50 * (5.0 - days_ago) / 5.0
        store.record("esp32_topup", pct=round(pct), timestamp=now - days_ago * 86400)
    pred = store.predict("esp32_topup", window_days=7)
    assert pred is not None
    assert pred.current_pct >= 100.0
    assert pred.is_charging is True
    assert pred.days_to_full == 0.0


def test_predict_separates_frequent_charge_ramp_from_last_drain(
    store: BatteryHistory,
) -> None:
    """Regression for Discussion #137's live E1004 shape.

    A long clean discharge ends at 68%, then a charge jump and nine
    frequent rising samples follow. The old heuristic fitted those
    nine samples as +475%/day and labelled that value Drain rate.
    """
    now = time.time()
    drain_start = now - 2.2 * 86400
    drain_span = 45.5 * 3600
    for i in range(500):
        fraction = i / 499
        store.record(
            "e1004",
            pct=round(76 - 8 * fraction),
            timestamp=drain_start + drain_span * fraction,
        )

    charge_start = drain_start + drain_span + 300
    for i, pct in enumerate((76, 77, 78, 79, 79, 80, 81, 81, 82)):
        store.record("e1004", pct=pct, timestamp=charge_start + i * 128)

    pred = store.predict("e1004", window_days=7)
    assert pred is not None
    assert pred.is_charging is True
    assert pred.charge_rate_per_day is not None
    assert pred.charge_rate_per_day > 100
    assert pred.slope_per_day == pytest.approx(-4.2, abs=0.8)
    assert pred.slope_per_day < 0
    assert pred.days_to_20pct is None
    assert pred.days_to_empty is None
    assert pred.days_to_full is not None


def test_predict_charge_rate_excludes_pre_charge_plateau(
    store: BatteryHistory,
) -> None:
    """A 68-69% plateau before plug-in must not dilute Full in."""
    now = time.time()
    start = now - 2 * 86400
    for i in range(16):
        store.record(
            "plateau",
            pct=80 - round(11 * i / 15),
            timestamp=start + i * 2 * 3600,
        )
    plateau_start = start + 32 * 3600
    for i in range(24):
        store.record(
            "plateau",
            pct=68 + (i % 2),
            timestamp=plateau_start + i * 15 * 60,
        )
    charge_start = plateau_start + 24 * 15 * 60
    for i, pct in enumerate((76, 77, 78, 79, 80, 81, 82)):
        store.record("plateau", pct=pct, timestamp=charge_start + i * 5 * 60)

    pred = store.predict("plateau", window_days=7)
    assert pred is not None
    assert pred.is_charging is True
    assert pred.charge_rate_per_day == pytest.approx(288.0, abs=5.0)
    assert pred.days_to_full == pytest.approx(0.0625, abs=0.01)


def test_theil_sen_ignores_single_post_unplug_relaxation_drop(
    store: BatteryHistory,
) -> None:
    """One immediate voltage correction must not steepen normal drain."""
    now = time.time()
    points = [
        (6.0, 70),
        (5.0, 60),
        (4.0, 50),
        (3.0, 40),
        (2.1, 85),  # charge event
        (2.0, 76),  # immediate unplug relaxation
        (1.5, 75),
        (1.0, 73),
        (0.5, 72),
        (0.0, 70),
    ]
    for days_ago, pct in points:
        store.record("relaxation", pct=pct, timestamp=now - days_ago * 86400)

    pred = store.predict("relaxation", window_days=7)
    assert pred is not None
    assert pred.is_charging is False
    assert pred.slope_per_day == pytest.approx(-3.0, abs=1.0)
    assert pred.slope_per_day > -6.0
    assert pred.days_to_empty is not None


def test_device_ids_returns_distinct_devices(store: BatteryHistory) -> None:
    now = time.time()
    store.record("a", pct=80, timestamp=now)
    store.record("b", pct=70, timestamp=now)
    store.record("a", pct=79, timestamp=now + 1)
    assert store.device_ids() == ["a", "b"]
