"""BatteryHistory store + linear regression coverage."""

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


def test_device_ids_returns_distinct_devices(store: BatteryHistory) -> None:
    now = time.time()
    store.record("a", pct=80, timestamp=now)
    store.record("b", pct=70, timestamp=now)
    store.record("a", pct=79, timestamp=now + 1)
    assert store.device_ids() == ["a", "b"]
