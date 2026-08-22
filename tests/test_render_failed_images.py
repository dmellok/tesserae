"""A render that finishes with broken images must say so (issue #255).

The image wait resolves on ``error`` as well as ``load``, which is right (a
dead CDN must not hold a render hostage) but meant a page full of
broken-image glyphs completed silently: the frame reached the panel, the
server logged a success, and the only evidence was on the glass. A reporter
whose Home Assistant album art stopped rendering had nothing in the log to
send.

Two halves are covered:

* the browser-side collector, against real Chromium, including that it strips
  the query string (an HA ``entity_picture`` carries its auth token there);
* the Python reporter that turns that into a warning.
"""

from __future__ import annotations

import logging

import pytest

from app.renderer import _IMAGE_WAIT_JS, _log_failed_images

playwright = pytest.importorskip("playwright.sync_api")


# -- the reporter ---------------------------------------------------------


def test_a_failure_is_logged_with_its_url(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.renderer"):
        _log_failed_images(
            {
                "total": 3,
                "awaited": 2,
                "timed_out": False,
                "failed": 1,
                "failed_urls": ["http://ha.local:8123/api/media_player_proxy/media_player.x"],
            }
        )
    assert "1 of 3 image(s) failed" in caplog.text
    assert "media_player_proxy" in caplog.text


def test_a_clean_render_says_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.renderer"):
        _log_failed_images(
            {"total": 2, "awaited": 1, "timed_out": False, "failed": 0, "failed_urls": []}
        )
        _log_failed_images({"total": 0, "awaited": 0, "timed_out": False})
        _log_failed_images({"error": "Execution context was destroyed"})
        _log_failed_images(None)
    assert caplog.text == ""


# -- the browser-side collector -------------------------------------------


def _evaluate(html: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            return page.evaluate(_IMAGE_WAIT_JS)
        finally:
            browser.close()


def test_a_broken_image_is_counted_and_named() -> None:
    result = _evaluate('<img src="http://127.0.0.1:9/definitely-not-there.png" alt="">')
    assert result["failed"] == 1
    assert result["total"] == 1
    assert any("definitely-not-there.png" in u for u in result["failed_urls"])


def test_a_query_string_is_stripped_so_a_token_is_not_logged() -> None:
    """HA hands out album art as
    ``/api/media_player_proxy/media_player.x?token=<secret>``. The whole point
    of the report is that it goes in a log file."""
    result = _evaluate(
        '<img src="http://127.0.0.1:9/api/media_player_proxy/media_player.x'
        '?token=SUPERSECRETVALUE" alt="">'
    )
    assert result["failed"] == 1
    joined = " ".join(result["failed_urls"])
    assert "SUPERSECRETVALUE" not in joined
    assert "token" not in joined
    assert "media_player_proxy/media_player.x" in joined


def test_a_working_image_is_not_reported() -> None:
    # A 1x1 gif as a data URI always loads, so this isolates the failure path
    # from network flakiness.
    ok = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    result = _evaluate(f'<img src="{ok}" alt="">')
    assert result["failed"] == 0
    assert result["failed_urls"] == []


def test_images_inside_a_shadow_root_are_covered() -> None:
    """Every widget renders into its own shadow tree, so a collector that only
    walked the light DOM would miss the case this exists for."""
    result = _evaluate(
        """
        <div id="host"></div>
        <script>
          const root = document.getElementById('host').attachShadow({mode: 'open'});
          root.innerHTML = '<img src="http://127.0.0.1:9/in-shadow.png" alt="">';
        </script>
        """
    )
    assert result["failed"] == 1
    assert any("in-shadow.png" in u for u in result["failed_urls"])
