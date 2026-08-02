"""External renders must not advertise HeadlessChrome (#178).

Akamai/Cloudflare-class bot protection blocks the default headless UA on
sight, so ``_screenshot_attempt`` sets a de-headlessed UA on the context
for external URLs while composer self-renders keep Chromium's default.
No real Chromium: a MagicMock browser records the context kwargs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.renderer import RenderRequest, _screenshot_attempt, external_user_agent


def test_external_user_agent_embeds_real_engine_version() -> None:
    browser = MagicMock()
    browser.version = "141.0.7390.37"
    ua = external_user_agent(browser)
    assert ua.startswith("Mozilla/5.0")
    assert "Chrome/141.0.7390.37" in ua
    assert "Headless" not in ua


def test_external_user_agent_survives_a_missing_browser() -> None:
    ua = external_user_agent(None)
    assert ua.startswith("Mozilla/5.0")
    assert "Chrome/" in ua
    assert "Headless" not in ua


def _attempt(is_composer: bool) -> MagicMock:
    browser = MagicMock()
    browser.version = "141.0.7390.37"
    request = RenderRequest(
        url="https://example.com" if not is_composer else "http://127.0.0.1/compose/x",
        viewport_w=800,
        viewport_h=480,
        is_composer=is_composer,
    )
    _screenshot_attempt(browser, request, 1)
    return browser


def test_external_render_context_hides_the_headless_marker() -> None:
    browser = _attempt(is_composer=False)
    kwargs = browser.new_context.call_args.kwargs
    ua = kwargs.get("user_agent", "")
    assert ua.startswith("Mozilla/5.0")
    assert "Chrome/141.0.7390.37" in ua
    assert "Headless" not in ua


def test_composer_render_keeps_the_default_user_agent() -> None:
    browser = _attempt(is_composer=True)
    kwargs = browser.new_context.call_args.kwargs
    assert "user_agent" not in kwargs
