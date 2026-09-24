"""BrowserPool routing + lifecycle.

Real Chromium isn't launched here, that would be ~2 s of cold-start
per case and pull Playwright into the lightweight test loop. We verify
the contract:

  * ``render_to_png(req, pool=pool)`` routes the request to ``pool.render``
  * ``render_to_png(req)`` (no pool) still goes through ``sync_playwright``
    + ``chromium.launch`` (verified by patching those out)
  * ``BrowserPool.stop()`` joins the worker thread without spawning
    Chromium when no render was ever requested
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.renderer import _FONT_WAIT_JS, BrowserPool, RenderRequest, render_to_png


def test_render_routes_to_pool_when_supplied() -> None:
    fake = MagicMock()
    fake.render.return_value = b"\x89PNG"
    req = RenderRequest(url="http://x/y")

    out = render_to_png(req, pool=fake)

    assert out == b"\x89PNG"
    fake.render.assert_called_once_with(req)


def test_render_falls_back_to_cold_path_without_pool() -> None:
    """Without a pool, render_to_png reaches for sync_playwright + Chromium
    launch, the pre-pool behaviour. Patching at the module boundary so
    no real browser is spawned."""
    req = RenderRequest(url="http://x/y")
    with (
        patch("app.renderer.sync_playwright") as pw,
        patch("app.renderer._screenshot_one", return_value=b"\x89PNG") as shoot,
    ):
        cm = pw.return_value
        cm.__enter__.return_value.chromium.launch.return_value = MagicMock()

        out = render_to_png(req)

    assert out == b"\x89PNG"
    shoot.assert_called_once()


def test_pool_stop_without_render_does_not_spawn_chromium() -> None:
    """Starting + stopping the pool without ever calling render() should
    NOT launch Chromium. The worker thread starts, sits idle, then exits
    on the sentinel."""
    pool = BrowserPool()
    with patch("app.renderer.sync_playwright") as pw:
        # Tighten the no-chromium contract: even if start() runs the worker
        # creates a Playwright instance but must not call chromium.launch.
        cm = pw.return_value
        cm.start.return_value = MagicMock()

        pool.start()
        pool.stop(timeout=5.0)

    assert pool._thread is None or not pool._thread.is_alive()
    # Worker created a sync_playwright instance but never called launch.
    cm.start.return_value.chromium.launch.assert_not_called()


def test_double_stop_is_a_noop() -> None:
    pool = BrowserPool()
    pool.stop()  # never started
    pool.stop()  # idempotent


def test_render_after_stop_raises() -> None:
    pool = BrowserPool()
    pool.stop()
    req = RenderRequest(url="http://x/y")
    try:
        pool.render(req)
    except RuntimeError as exc:
        assert "stopped" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on render after stop")


def test_stuck_worker_is_replaced_and_queued_tasks_still_render() -> None:
    """A Playwright call that never returns used to hold the only worker
    for good, so every later render timed out at the pool deadline until a
    restart. Once a caller times out while the worker is past that task's
    deadline, the pool swaps in a fresh worker and moves the queue over."""
    release = threading.Event()
    calls: list[str] = []

    def shoot(_browser: object, req: RenderRequest) -> bytes:
        calls.append(req.url)
        if req.url == "http://x/hang":
            release.wait(10)
        return b"\x89PNG"

    pool = BrowserPool()
    with (
        patch("app.renderer.sync_playwright"),
        patch("app.renderer._screenshot_one", side_effect=shoot),
    ):
        try:
            pool.start()
            stuck = pool._worker
            assert stuck is not None
            with pytest.raises(TimeoutError, match="no result within"):
                pool._submit(RenderRequest(url="http://x/hang"), 0.2)
            assert stuck.retired.is_set()

            assert pool._submit(RenderRequest(url="http://x/ok"), 5.0) == b"\x89PNG"
            assert pool._worker is not stuck
        finally:
            release.set()
            pool.stop(timeout=5.0)
    stuck_thread = stuck.thread
    assert stuck_thread is not None
    stuck_thread.join(5.0)
    assert not stuck_thread.is_alive()
    assert calls == ["http://x/hang", "http://x/ok"]


def test_task_cancelled_by_its_caller_is_skipped() -> None:
    """A caller that gave up cancels its task; the worker must not render it
    later for nobody, which kept a backlog from ever draining."""
    gate = threading.Event()
    calls: list[str] = []

    def shoot(_browser: object, req: RenderRequest) -> bytes:
        calls.append(req.url)
        if req.url == "http://x/slow":
            gate.wait(10)
        return b"\x89PNG"

    pool = BrowserPool()
    with (
        patch("app.renderer.sync_playwright"),
        patch("app.renderer._screenshot_one", side_effect=shoot),
    ):
        try:
            slow = threading.Thread(
                target=pool._submit, args=(RenderRequest(url="http://x/slow"), 5.0)
            )
            slow.start()
            while not calls:
                time.sleep(0.01)
            # Queued behind a task still inside its deadline: a backlog,
            # so the caller times out without replacing the worker.
            with pytest.raises(TimeoutError):
                pool._submit(RenderRequest(url="http://x/abandoned"), 0.2)
            first = pool._worker
            gate.set()
            slow.join(5.0)
            assert pool._submit(RenderRequest(url="http://x/next"), 5.0) == b"\x89PNG"
            assert pool._worker is first
        finally:
            gate.set()
            pool.stop(timeout=5.0)
    assert calls == ["http://x/slow", "http://x/next"]


def test_font_wait_is_capped() -> None:
    assert "setTimeout" in _FONT_WAIT_JS
