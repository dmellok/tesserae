"""Renderer hooks the ha_dashboard widget relies on: a per-request init
script installed on the context before the first page, an ignore-TLS flag,
and a ready-condition wait plus settle on external renders.

No real Chromium: a MagicMock browser records what each path did, the same
way test_renderer_user_agent does. Both the screenshot path and the shared
capture/inspect path are covered, since they set the context up separately.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.renderer import (
    RenderRequest,
    _navigate_and_settle,
    _new_composer_page,
    _screenshot_attempt,
)

SCRIPT = "localStorage.setItem('hassTokens', '{}')"
READY = "() => document.querySelector('home-assistant') !== null"


def _browser() -> MagicMock:
    browser = MagicMock()
    browser.version = "141.0.7390.37"
    return browser


def _external(**overrides: object) -> RenderRequest:
    fields: dict[str, object] = {
        "url": "http://ha.local:8123/lovelace/0",
        "viewport_w": 800,
        "viewport_h": 536,
        "is_composer": False,
    }
    fields.update(overrides)
    return RenderRequest(**fields)  # type: ignore[arg-type]


def test_screenshot_path_installs_init_script_and_tls_flag() -> None:
    browser = _browser()
    _screenshot_attempt(browser, _external(init_script=SCRIPT, ignore_https_errors=True), 1)
    kwargs = browser.new_context.call_args.kwargs
    assert kwargs["ignore_https_errors"] is True
    context = browser.new_context.return_value
    context.add_init_script.assert_called_once_with(SCRIPT)
    # The script goes in before the page exists, or the first document misses it.
    calls = [c[0] for c in context.method_calls]
    assert calls.index("add_init_script") < calls.index("new_page")


def test_screenshot_path_leaves_defaults_untouched() -> None:
    browser = _browser()
    _screenshot_attempt(browser, _external(), 1)
    kwargs = browser.new_context.call_args.kwargs
    assert "ignore_https_errors" not in kwargs
    browser.new_context.return_value.add_init_script.assert_not_called()
    page = browser.new_context.return_value.new_page.return_value
    page.wait_for_function.assert_not_called()
    page.wait_for_timeout.assert_not_called()


def test_screenshot_path_waits_for_ready_then_settles() -> None:
    browser = _browser()
    _screenshot_attempt(browser, _external(ready_js=READY, ready_timeout_ms=1234, settle_ms=250), 1)
    page = browser.new_context.return_value.new_page.return_value
    page.wait_for_function.assert_called_once_with(READY, timeout=1234)
    page.wait_for_timeout.assert_called_once_with(250)


def test_composer_render_ignores_external_only_waits() -> None:
    browser = _browser()
    request = RenderRequest(
        url="http://127.0.0.1/compose/x", is_composer=True, ready_js=READY, settle_ms=250
    )
    _screenshot_attempt(browser, request, 1)
    page = browser.new_context.return_value.new_page.return_value
    for call in page.wait_for_function.call_args_list:
        assert call.args[0] != READY
    page.wait_for_timeout.assert_not_called()


def test_capture_path_installs_init_script_and_waits() -> None:
    browser = _browser()
    request = _external(init_script=SCRIPT, ignore_https_errors=True, ready_js=READY, settle_ms=100)
    context, page = _new_composer_page(browser, request)
    assert browser.new_context.call_args.kwargs["ignore_https_errors"] is True
    context.add_init_script.assert_called_once_with(SCRIPT)
    settle = _navigate_and_settle(page, request, 1)
    page.wait_for_function.assert_called_once_with(READY, timeout=request.ready_timeout_ms)
    page.wait_for_timeout.assert_called_once_with(100)
    assert settle["ready"] == "fired"


def test_ready_wait_timeout_is_best_effort() -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    browser = _browser()
    request = _external(ready_js=READY)
    _context, page = _new_composer_page(browser, request)
    page.wait_for_function.side_effect = PlaywrightTimeoutError("slow")
    settle = _navigate_and_settle(page, request, 1)
    assert settle["ready"] == "timeout"
    page.screenshot.assert_not_called()  # navigate only; the caller screenshots
