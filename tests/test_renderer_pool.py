"""BrowserPool routing + lifecycle.

Real Chromium isn't launched here — that would be ~2 s of cold-start
per case and pull Playwright into the lightweight test loop. We verify
the contract:

  * ``render_to_png(req, pool=pool)`` routes the request to ``pool.render``
  * ``render_to_png(req)`` (no pool) still goes through ``sync_playwright``
    + ``chromium.launch`` (verified by patching those out)
  * ``BrowserPool.stop()`` joins the worker thread without spawning
    Chromium when no render was ever requested
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.renderer import BrowserPool, RenderRequest, render_to_png


def test_render_routes_to_pool_when_supplied() -> None:
    fake = MagicMock()
    fake.render.return_value = b"\x89PNG"
    req = RenderRequest(url="http://x/y")

    out = render_to_png(req, pool=fake)

    assert out == b"\x89PNG"
    fake.render.assert_called_once_with(req)


def test_render_falls_back_to_cold_path_without_pool() -> None:
    """Without a pool, render_to_png reaches for sync_playwright + Chromium
    launch — the pre-pool behaviour. Patching at the module boundary so
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
