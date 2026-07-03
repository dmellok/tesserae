"""Tests for the button-row detail synthesis on the History page.

Focused on ``_button_detail`` since it's a pure function of an
``EventRow`` + a page-name lookup. The full ``history_view`` wiring
is exercised by the running app; here we lock in the summary string
so the History row stays informative for the common outcomes.
"""

from __future__ import annotations

from app.history_routes import _button_detail
from app.state.event_log import EventRow


def _row(**extra: object) -> EventRow:
    return EventRow(
        id=1,
        type="push",
        timestamp=1_700_000_000.0,
        source="button",
        target="kitchen",
        status="dispatched",
        digest=None,
        error=None,
        duration_s=0.0,
        extra=dict(extra),
    )


def test_dispatched_press_details_include_button_and_pushed_page() -> None:
    row = _row(
        button="right",
        action_spec="rotate_next",
        action_description="rotate_next -> step 1",
        pushed_page_id="afternoon",
        step_index=1,
        step_page_id="afternoon",
    )
    detail = _button_detail(row, page_names={"afternoon": "Afternoon calendar"})
    assert detail is not None
    assert "button right" in detail
    assert "rotate_next" in detail
    assert "Afternoon calendar" in detail


def test_page_shortcut_details_show_target_page_friendly_name() -> None:
    row = _row(
        button="custom",
        action_spec="page:morning",
        action_description="page -> morning",
        pushed_page_id="morning",
    )
    detail = _button_detail(row, page_names={"morning": "Morning briefing"})
    assert detail is not None
    assert "page:morning" in detail
    assert "Morning briefing" in detail


def test_webhook_details_show_spec() -> None:
    row = _row(
        button="notify",
        action_spec="webhook:https://hook.example/x",
        action_description="webhook -> https://hook.example/x",
    )
    detail = _button_detail(row, page_names={})
    assert detail is not None
    assert "webhook:https://hook.example/x" in detail


def test_deduped_press_falls_back_to_description() -> None:
    row = _row(button="right", action_description="duplicate; ignored")
    detail = _button_detail(row, page_names={})
    assert detail is not None
    assert "button right" in detail
    assert "duplicate; ignored" in detail


def test_row_without_any_button_info_returns_none() -> None:
    row = _row()
    assert _button_detail(row, page_names={}) is None
