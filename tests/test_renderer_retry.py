"""Renderer retry contract, ``_screenshot_one`` wraps the per-attempt
render and retries on Playwright ``TimeoutError`` only.

Behaviour we lock down here:

* First attempt succeeds → ``_screenshot_attempt`` called once, no retries.
* First attempt times out, second succeeds → called twice; result wins.
* All ``max_attempts`` time out → the last TimeoutError surfaces.
* Non-timeout Playwright errors (e.g. invalid URL) do NOT retry, they
  surface immediately so we don't burn the deadline on something that
  won't recover.

No real Chromium is launched. We patch ``_screenshot_attempt`` and call
``_screenshot_one`` directly with a sentinel browser handle.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.renderer import RenderRequest, _screenshot_one


def test_success_on_first_attempt_does_not_retry() -> None:
    req = RenderRequest(url="http://x/y", max_attempts=3)
    browser = MagicMock()
    with patch("app.renderer._screenshot_attempt", return_value=b"\x89PNG") as attempt:
        out = _screenshot_one(browser, req)
    assert out == b"\x89PNG"
    assert attempt.call_count == 1
    # 1-indexed attempt counter starts at 1.
    assert attempt.call_args.args[2] == 1


def test_timeout_on_first_attempt_retries_and_succeeds() -> None:
    req = RenderRequest(url="http://x/y", max_attempts=3)
    browser = MagicMock()
    with patch(
        "app.renderer._screenshot_attempt",
        side_effect=[PlaywrightTimeoutError("Page.goto: Timeout 15000ms exceeded"), b"\x89PNG"],
    ) as attempt:
        out = _screenshot_one(browser, req)
    assert out == b"\x89PNG"
    assert attempt.call_count == 2
    # Successful retry runs as attempt #2.
    assert attempt.call_args.args[2] == 2


def test_all_attempts_timeout_raises_last_error() -> None:
    req = RenderRequest(url="http://x/y", max_attempts=3)
    browser = MagicMock()
    err1 = PlaywrightTimeoutError("Page.goto: Timeout 15000ms exceeded (try 1)")
    err2 = PlaywrightTimeoutError("Page.goto: Timeout 15000ms exceeded (try 2)")
    err3 = PlaywrightTimeoutError("Page.goto: Timeout 15000ms exceeded (try 3)")
    with (
        patch(
            "app.renderer._screenshot_attempt",
            side_effect=[err1, err2, err3],
        ) as attempt,
        pytest.raises(PlaywrightTimeoutError) as excinfo,
    ):
        _screenshot_one(browser, req)
    assert attempt.call_count == 3
    # The error that bubbles out is the last one, useful for log triage.
    assert "(try 3)" in str(excinfo.value)


def test_non_timeout_error_does_not_retry() -> None:
    """A non-timeout PlaywrightError (e.g. invalid URL, frame detached,
    browser crashed) should NOT trigger the retry path, those are
    failures retries won't fix, and we'd rather surface them fast."""
    req = RenderRequest(url="http://x/y", max_attempts=3)
    browser = MagicMock()
    with (
        patch(
            "app.renderer._screenshot_attempt",
            side_effect=PlaywrightError("net::ERR_NAME_NOT_RESOLVED"),
        ) as attempt,
        pytest.raises(PlaywrightError),
    ):
        _screenshot_one(browser, req)
    assert attempt.call_count == 1


def test_max_attempts_one_disables_retry() -> None:
    """``max_attempts=1`` is the explicit opt-out, single shot, raise
    on the first timeout."""
    req = RenderRequest(url="http://x/y", max_attempts=1)
    browser = MagicMock()
    with (
        patch(
            "app.renderer._screenshot_attempt",
            side_effect=PlaywrightTimeoutError("Page.goto: Timeout"),
        ) as attempt,
        pytest.raises(PlaywrightTimeoutError),
    ):
        _screenshot_one(browser, req)
    assert attempt.call_count == 1
