"""Renderer timezone propagation, ``RenderRequest.timezone_id`` reaches
Chromium's ``new_context`` so widgets' client-side ``new Date()`` runs
in the user's configured zone, not the container's ``TZ`` env var.

Without this, a Docker / HA add-on deployment painted clock + calendar
widgets one hour behind during BST (the user reported it on the forum:
preview correct, rendered frame wrong, because the in-browser preview
read the user's laptop TZ while the in-container Chromium read UTC).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.push import resolve_render_timezone_id
from app.renderer import RenderRequest, _screenshot_attempt
from app.state.settings_store import SettingsStore

# -- _screenshot_attempt forwards timezone_id ---------------------------


def _stub_browser_returning_png() -> MagicMock:
    """Browser stub whose context.new_page().screenshot() returns a PNG.

    We don't care about navigation here; we patch goto + screenshot
    via the stub chain so the function reaches the new_context call and
    we can inspect the kwargs it was given."""
    browser = MagicMock()
    page = MagicMock()
    page.screenshot.return_value = b"\x89PNG"
    page.evaluate.return_value = 100
    context = MagicMock()
    context.new_page.return_value = page
    browser.new_context.return_value = context
    return browser


def test_screenshot_attempt_forwards_timezone_id() -> None:
    browser = _stub_browser_returning_png()
    req = RenderRequest(
        url="http://x/y",
        timezone_id="Europe/London",
        wait_until="load",
    )
    _screenshot_attempt(browser, req, attempt=1)
    kwargs = browser.new_context.call_args.kwargs
    assert kwargs.get("timezone_id") == "Europe/London"
    # Existing kwargs survive.
    assert kwargs["viewport"] == {"width": req.viewport_w, "height": req.viewport_h}
    assert kwargs["color_scheme"] == "light"


def test_screenshot_attempt_omits_timezone_when_none() -> None:
    """``timezone_id=None`` (the default) leaves Chromium on the
    container's TZ. We verify the kwarg isn't present so we don't
    accidentally pass ``None`` to Playwright (which raises)."""
    browser = _stub_browser_returning_png()
    req = RenderRequest(url="http://x/y", wait_until="load")
    _screenshot_attempt(browser, req, attempt=1)
    kwargs = browser.new_context.call_args.kwargs
    assert "timezone_id" not in kwargs


# -- resolve_render_timezone_id ----------------------------------------


def test_resolve_returns_none_for_system_setting(tmp_path: Path) -> None:
    """``"system"`` means "use the container's TZ". Resolver returns
    None so the caller doesn't pass anything to Playwright."""
    store = SettingsStore(tmp_path / "settings.json")
    store.update_section("app", {"timezone": "system"})
    assert resolve_render_timezone_id(store) is None


def test_resolve_returns_none_for_empty_setting(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.update_section("app", {"timezone": ""})
    assert resolve_render_timezone_id(store) is None


def test_resolve_returns_iana_zone_when_valid(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.update_section("app", {"timezone": "Europe/London"})
    assert resolve_render_timezone_id(store) == "Europe/London"


def test_resolve_returns_utc(tmp_path: Path) -> None:
    """The setting picker exposes ``UTC`` as a separate choice; the
    resolver should pass it through (Chromium accepts ``UTC``)."""
    store = SettingsStore(tmp_path / "settings.json")
    store.update_section("app", {"timezone": "UTC"})
    assert resolve_render_timezone_id(store) == "UTC"


def test_resolve_returns_none_for_unknown_zone(tmp_path: Path) -> None:
    """A bogus value mustn't reach Playwright (which would raise on
    every render). Log + fall back to ``None``."""
    store = SettingsStore(tmp_path / "settings.json")
    store.update_section("app", {"timezone": "Atlantis/Lost"})
    assert resolve_render_timezone_id(store) is None


def test_resolve_treats_missing_setting_as_system(tmp_path: Path) -> None:
    """Fresh install with no timezone setting at all should behave the
    same as ``"system"``: return None, fall through to container TZ."""
    store = SettingsStore(tmp_path / "settings.json")
    assert resolve_render_timezone_id(store) is None
