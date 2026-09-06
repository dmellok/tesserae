"""The 24h rail thins dense schedules instead of truncating them (#166).

The rail drew ``marks[:48]``. A 3-minute schedule projects ~480 fires a day,
so the ticks ran out about two hours in and the lane read as "the schedule
stopped firing" -- while the ``refreshes today`` label beside it still showed
the full count, so the label and the ticks disagreed.

The issue reports this against ``_build_timeline`` in ``app/schedule_routes.py``
(a ``fires[:200]`` cap). That function has no production caller: only a test
imports it. The rail an operator actually sees is built in
``app/deck_routes.py``, where the cap is 48 -- so the truncation bites four
times sooner than the issue estimates.
"""

from __future__ import annotations

from app.deck_routes import MAX_RAIL_MARKS, _thin_marks


def _even_marks(count: int) -> list[float]:
    """``count`` fires spread evenly across the day, as percentages."""
    return [i * 100.0 / max(1, count - 1) for i in range(count)]


def test_a_sparse_schedule_is_untouched() -> None:
    """A daily or hourly schedule draws every fire, exactly as before."""
    marks = _even_marks(24)
    assert _thin_marks(marks) == marks


def test_a_rail_at_the_cap_is_untouched() -> None:
    marks = _even_marks(MAX_RAIL_MARKS)
    assert _thin_marks(marks) == marks


def test_a_dense_schedule_still_reaches_the_end_of_the_day() -> None:
    """The defect, in one assertion.

    480 fires truncated to the first 48 stopped at 9.8% of the window --
    about two hours in on a 24h rail.
    """
    thinned = _thin_marks(_even_marks(480))
    assert thinned[-1] > 99.0, f"the lane stops at {thinned[-1]:.1f}% of the day"


def test_a_dense_schedule_starts_where_the_schedule_starts() -> None:
    thinned = _thin_marks(_even_marks(480))
    assert thinned[0] == 0.0


def test_thinning_is_bounded_at_every_density() -> None:
    """The cap is what keeps the DOM small; thinning must not lift it."""
    for count in (49, 100, 480, 1440):
        assert len(_thin_marks(_even_marks(count))) <= MAX_RAIL_MARKS


def test_marks_stay_in_order_and_never_stack() -> None:
    """Two ticks on one pixel read as one tick, which understates density."""
    thinned = _thin_marks(_even_marks(1440))
    assert thinned == sorted(thinned)
    assert len(set(thinned)) == len(thinned)


def test_the_lane_is_spread_not_bunched_at_the_start() -> None:
    """Head-truncation put every tick in the first tenth of the rail.

    A thinned lane should have marks in every quarter of the day, which is
    the property that makes it readable as a cadence.
    """
    thinned = _thin_marks(_even_marks(480))
    for lo, hi in ((0.0, 25.0), (25.0, 50.0), (50.0, 75.0), (75.0, 100.1)):
        assert any(lo <= m < hi for m in thinned), f"no ticks between {lo}% and {hi}%"
