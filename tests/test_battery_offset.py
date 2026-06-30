"""Per-device battery-display offset helper."""

from __future__ import annotations

from app.battery_offset import apply_to_mv, apply_to_pct, get_offset


def test_get_offset_defaults_to_zero_when_block_missing() -> None:
    assert get_offset({}) == (0, 0)
    assert get_offset({"name": "Attic"}) == (0, 0)


def test_get_offset_returns_configured_values() -> None:
    manifest = {"battery_offset": {"mv": 350, "pct": -5}}
    assert get_offset(manifest) == (350, -5)


def test_get_offset_tolerates_unparseable_block() -> None:
    """A malformed block (e.g. someone hand-edited the manifest) must
    not crash the read path; it should fall through to (0, 0)."""
    assert get_offset({"battery_offset": "garbage"}) == (0, 0)
    assert get_offset({"battery_offset": {"mv": "not a number"}}) == (0, 0)


def test_apply_to_mv_adds_offset_and_clamps_floor() -> None:
    assert apply_to_mv(3850, 350) == 4200
    assert apply_to_mv(3500, -200) == 3300
    # Below-zero never makes sense for a battery voltage; clamp to 0
    # so the displayed value floors gracefully.
    assert apply_to_mv(100, -500) == 0
    # None stays None: a device that doesn't report mV shouldn't get
    # a synthetic 0 from the offset path.
    assert apply_to_mv(None, 500) is None


def test_apply_to_pct_uses_mv_offset_when_present() -> None:
    """The voltage path is the principled fix: if the user calibrated
    the mV offset and we have a raw mV reading, derive the corrected
    percent from the LiPo curve rather than scaling the firmware's
    pct (which itself derived from a wrong voltage)."""
    # Reported 3.85 V (= ~60% on the LiPo curve) with a +350 mV offset
    # → corrected voltage is 4.20 V → 100%.
    result = apply_to_pct(60, mv_offset=350, pct_offset=0, raw_mv=3850)
    assert result == 100


def test_apply_to_pct_uses_pct_offset_when_no_mv_offset() -> None:
    """Falls back to a direct percent shift when the user only set
    the pct offset (band-aid path: 'UI says 85, voltmeter says 100,
    add 15')."""
    result = apply_to_pct(85, mv_offset=0, pct_offset=15)
    assert result == 100


def test_apply_to_pct_layers_pct_offset_on_top_of_mv_correction() -> None:
    """Both offsets together: the mV path produces the base, then the
    pct offset adds on top. Useful when the LiPo curve doesn't match
    the user's cell chemistry exactly."""
    result = apply_to_pct(60, mv_offset=350, pct_offset=-5, raw_mv=3850)
    assert result == 95  # mV bump to 100, then -5


def test_apply_to_pct_clamps_to_0_100() -> None:
    assert apply_to_pct(95, mv_offset=0, pct_offset=20) == 100
    assert apply_to_pct(10, mv_offset=0, pct_offset=-50) == 0


def test_apply_to_pct_returns_none_when_no_raw_data() -> None:
    """A device that reports neither pct nor mV has nothing to offset;
    None stays None so the dashboard skips the card rather than
    drawing a synthetic 0."""
    assert apply_to_pct(None, mv_offset=350, pct_offset=0, raw_mv=None) is None
