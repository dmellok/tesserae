"""A render whose pictures failed to load is retried, and an automated send
never publishes the broken frame.

Intermittent blank frames: a CDN (Google's lh3) errors some image requests
from the headless renderer, and the frame used to go out with an empty
picture. Covered here, without launching Chromium:

* the renderer re-renders once after a backoff when a composer page finished
  with failed images, and hands the failures (status / error text) back on the
  returned bytes;
* the warning names the host and the response status or failure text;
* ``PushManager.push`` keeps the display on its previous picture for an
  automated send whose images are still broken (no frame swap, no "sent" row),
  gives up holding after a few consecutive renders so a dead URL cannot stall
  a lineup, and lets a manual send through with a warning.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app import renderer as renderer_mod
from app.push import _BROKEN_IMAGE_MAX_HOLDS
from app.renderer import (
    CaptureRequest,
    ImageFailure,
    RenderedPng,
    RenderRequest,
    _capture_one,
    _log_failed_images,
    _screenshot_one,
    _watch_image_requests,
    failed_images_of,
)
from tests.test_push import _distinct_png, _make_renderer, _wired

LH3 = "https://lh3.googleusercontent.com/pw/AB123=w600-h800"
BAD = ImageFailure(url=LH3, host="lh3.googleusercontent.com", status=429, status_text="")


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(renderer_mod, "_image_retry_sleep", slept.append)
    return slept


# -- the warning ------------------------------------------------------------


def test_warning_names_host_status_and_error_text(caplog: pytest.LogCaptureFixture) -> None:
    net = {
        LH3: {"status": 429, "status_text": "Too Many Requests"},
        "https://cdn.example/b.png": {"error": "net::ERR_HTTP2_PROTOCOL_ERROR"},
    }
    settle = {
        "total": 3,
        "failed": 2,
        "failed_urls": [LH3, "https://cdn.example/b.png"],
    }
    with caplog.at_level(logging.WARNING, logger="app.renderer"):
        failures = _log_failed_images(settle, net)
    assert "2 of 3 image(s) failed to load" in caplog.text
    assert "host lh3.googleusercontent.com" in caplog.text
    assert "HTTP 429 Too Many Requests" in caplog.text
    assert "net::ERR_HTTP2_PROTOCOL_ERROR" in caplog.text
    assert [f.status for f in failures] == [429, None]
    assert failures[1].error == "net::ERR_HTTP2_PROTOCOL_ERROR"


def test_warning_says_when_the_network_left_no_trace(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.renderer"):
        failures = _log_failed_images({"total": 1, "failed": 1, "failed_urls": [LH3]}, {})
    assert "no network error seen" in caplog.text
    assert failures[0].status is None and failures[0].error is None


def test_network_watch_records_image_status_and_failure_by_query_free_url() -> None:
    handlers: dict[str, Any] = {}
    page = MagicMock()
    page.on.side_effect = lambda name, fn: handlers.__setitem__(name, fn)
    seen = _watch_image_requests(page)

    ok = MagicMock(url=LH3 + "?token=secret", status=200, status_text="OK")
    ok.request.resource_type = "image"
    handlers["response"](ok)
    bad = MagicMock(url=LH3 + "?token=secret", status=403, status_text="Forbidden")
    bad.request.resource_type = "image"
    handlers["response"](bad)
    # A later 200 does not hide the earlier error; non-images are ignored.
    ok2 = MagicMock(url=LH3, status=200, status_text="OK")
    ok2.request.resource_type = "image"
    handlers["response"](ok2)
    script = MagicMock(url="https://x/app.js", status=500, status_text="")
    script.request.resource_type = "script"
    handlers["response"](script)
    failed = MagicMock(url="https://cdn.example/c.png?a=b", failure="net::ERR_BLOCKED_BY_ORB")
    failed.resource_type = "image"
    handlers["requestfailed"](failed)

    assert seen[LH3]["status"] == 403
    assert "secret" not in str(seen)
    assert seen["https://cdn.example/c.png"]["error"] == "net::ERR_BLOCKED_BY_ORB"
    assert "https://x/app.js" not in seen


# -- the renderer's retry ---------------------------------------------------


def _png(failed: bool) -> RenderedPng:
    return RenderedPng(b"\x89PNG", (BAD,) if failed else ())


def test_failed_images_trigger_one_more_render_that_wins(
    _no_backoff: list[float],
) -> None:
    req = RenderRequest(url="http://x/compose/p", image_retry_backoff_s=1.5)
    with patch(
        "app.renderer._screenshot_attempt", side_effect=[_png(True), _png(False)]
    ) as attempt:
        out = _screenshot_one(MagicMock(), req)
    assert attempt.call_count == 2
    assert failed_images_of(out) == ()
    assert _no_backoff == [1.5]


def test_images_failing_every_time_return_the_last_frame_with_its_failures() -> None:
    req = RenderRequest(url="http://x/compose/p")
    with patch("app.renderer._screenshot_attempt", side_effect=[_png(True), _png(True)]) as attempt:
        out = _screenshot_one(MagicMock(), req)
    # One retry by default, then the frame comes back flagged, not an error.
    assert attempt.call_count == 2
    assert failed_images_of(out) == (BAD,)
    assert bytes(out) == b"\x89PNG"


def test_external_pages_and_zero_retries_do_not_re_render() -> None:
    for req in (
        RenderRequest(url="http://site/", is_composer=False),
        RenderRequest(url="http://x/compose/p", image_retries=0),
    ):
        with patch("app.renderer._screenshot_attempt", return_value=_png(True)) as attempt:
            out = _screenshot_one(MagicMock(), req)
        assert attempt.call_count == 1
        assert failed_images_of(out)


def test_timeouts_and_image_retries_are_counted_separately() -> None:
    req = RenderRequest(url="http://x/compose/p", max_attempts=2)
    seq = [PlaywrightTimeoutError("goto"), _png(True), _png(False)]
    with patch("app.renderer._screenshot_attempt", side_effect=seq) as attempt:
        out = _screenshot_one(MagicMock(), req)
    assert attempt.call_count == 3
    assert failed_images_of(out) == ()
    # Attempt numbers keep counting up for the phase log.
    assert [c.args[2] for c in attempt.call_args_list] == [1, 2, 3]


def test_capture_path_retries_the_same_way() -> None:
    creq = CaptureRequest(render=RenderRequest(url="http://x/compose/p"), script="1")
    with patch(
        "app.renderer._capture_attempt",
        side_effect=[(_png(True), None), (_png(False), [1])],
    ) as attempt:
        png, result = _capture_one(MagicMock(), creq)
    assert attempt.call_count == 2
    assert failed_images_of(png) == () and result == [1]


# -- the publish path -------------------------------------------------------


def _manager(tmp_path: Path):
    png = _distinct_png((255, 0, 0))
    renderers = [_make_renderer(tmp_path, "pi_png", "png", retain=False)]
    manager, mqtt, composition = _wired(tmp_path, png, renderers)
    return manager, mqtt, composition


def _capture(png: bytes, *, failed: bool):
    return (RenderedPng(png, (BAD,) if failed else ()), [])


def _statuses(manager: Any) -> list[tuple[str, str]]:
    return [(r.type, r.status) for r in manager._event_log.list(limit=50)]


def test_automated_send_holds_the_previous_frame_when_images_still_fail(tmp_path: Path) -> None:
    manager, mqtt, _ = _manager(tmp_path)
    good = _distinct_png((0, 255, 0))
    broken = _distinct_png((0, 0, 255))

    with patch("app.push.capture_composed", return_value=_capture(good, failed=False)):
        assert manager.push("home", source="scheduler").status == "sent"
    published = len(mqtt.published)
    latest = dict(manager._latest_renders)

    with patch("app.push.capture_composed", return_value=_capture(broken, failed=True)):
        result = manager.push("home", source="scheduler")

    assert result.status == "failed"
    assert "lh3.googleusercontent.com" in (result.error or "")
    assert "429" in (result.error or "")
    assert "previous picture" in (result.error or "")
    # No frame swap and no publish; the broken frame was not even written out.
    assert len(mqtt.published) == published
    assert manager._latest_renders == latest
    assert not any(p.stem == _digest(broken) for p in (tmp_path / "renders").glob("*.png"))
    # History records a failure, never a "sent" for the broken frame.
    sent = [r for r in manager._event_log.list(limit=50) if r.status == "sent"]
    assert len(sent) <= 2  # the good push's push + renderer rows
    assert manager._event_log.list(limit=1)[0].status == "failed"


def _digest(png: bytes) -> str:
    import hashlib

    return hashlib.sha256(png).hexdigest()[:16]


def test_next_clean_render_publishes_normally(tmp_path: Path) -> None:
    manager, mqtt, _ = _manager(tmp_path)
    with patch(
        "app.push.capture_composed",
        return_value=_capture(_distinct_png((9, 9, 9)), failed=True),
    ):
        assert manager.push("home", source="scheduler").status == "failed"
    assert not mqtt.published
    with patch(
        "app.push.capture_composed",
        return_value=_capture(_distinct_png((1, 2, 3)), failed=False),
    ):
        result = manager.push("home", source="scheduler")
    assert result.status == "sent" and result.warning is None
    assert manager._broken_image_holds == {}


def test_a_page_that_never_loads_its_image_is_published_after_a_few_holds(tmp_path: Path) -> None:
    manager, mqtt, _ = _manager(tmp_path)
    broken = _distinct_png((7, 7, 7))
    with patch("app.push.capture_composed", return_value=_capture(broken, failed=True)):
        statuses = [
            manager.push("home", source="rotation").status
            for _ in range(_BROKEN_IMAGE_MAX_HOLDS + 2)
        ]
    assert statuses[:_BROKEN_IMAGE_MAX_HOLDS] == ["failed"] * _BROKEN_IMAGE_MAX_HOLDS
    # Then it goes out as it always did (once; the repeats are unchanged).
    assert statuses[_BROKEN_IMAGE_MAX_HOLDS] == "sent"
    assert len(mqtt.published) == 1


def test_manual_send_publishes_with_a_warning(tmp_path: Path) -> None:
    manager, mqtt, _ = _manager(tmp_path)
    broken = _distinct_png((5, 5, 5))
    with patch("app.push.capture_composed", return_value=_capture(broken, failed=True)):
        result = manager.push("home", bypass_coalesce=True, force_publish=True)
    assert result.status == "sent"
    assert result.warning and "lh3.googleusercontent.com" in result.warning
    assert "429" in result.warning
    assert len(mqtt.published) == 1
    row = manager._event_log.get(result.event_id or 0)
    assert row is not None and row.extra["image_warning"] == result.warning


def test_prewarm_does_not_cache_a_broken_frame(tmp_path: Path) -> None:
    manager, _mqtt, _ = _manager(tmp_path)
    with patch(
        "app.push.capture_composed",
        return_value=_capture(_distinct_png((4, 4, 4)), failed=True),
    ):
        manager.prewarm_page("home", device_id="pi_png")
    assert not manager._precompose
