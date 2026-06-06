"""Shared infrastructure for ``record_*.py`` scenario scripts.

Each scenario (onboarding, dashboard creation, …) wants the same three
things:

* a fresh Tesserae instance against a tmpdir data root, on a free port,
  reachable over HTTP;
* a Playwright headed browser context recording video, with a custom
  in-page cursor that's actually visible (Chromium's recorder doesn't
  capture the OS cursor) and that glides along curved Bezier paths
  between targets so the motion reads as deliberate rather than
  teleporting;
* a ``.webm`` → ``.mp4`` transcode for embedding in READMEs and posts.

This module owns all three. The scenario script supplies a ``prepare``
hook (called before Tesserae starts, used by the dashboard scenario to
seed an already-onboarded state) and a ``drive`` async function (the
actual click-by-click flow).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.main import create_app  # noqa: E402

# ----- Tesserae lifecycle ---------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(port: int, *, timeout: float = 25.0) -> None:
    """Poll any open path on the server until it responds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # /setup AND /login are both open paths; whichever the scenario
        # is in, one of them returns 200 on a freshly-built instance.
        for path in ("/login", "/setup"):
            try:
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as resp:
                    if resp.status in (200, 302):
                        return
            except (URLError, ConnectionError, TimeoutError):
                continue
        time.sleep(0.2)
    raise RuntimeError(f"Tesserae didn't come up on :{port} within {timeout}s")


def _serve_in_thread(port: int, data_root: Path) -> threading.Thread:
    app = create_app(
        data_root=data_root,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )

    def _run() -> None:
        from waitress import serve

        serve(
            app,
            host="127.0.0.1",
            port=port,
            threads=4,
            ident="tesserae-record",
            log_socket_errors=False,
        )

    thread = threading.Thread(target=_run, name="tesserae-record-server", daemon=True)
    thread.start()
    return thread


# ----- Cursor injection -----------------------------------------------
#
# Playwright records via Chromium's internal pipeline, which doesn't
# include the OS cursor. We inject a DOM-level fake cursor + click ring
# into every page Playwright loads.
#
# Movement is JS-driven (requestAnimationFrame over a quadratic Bezier
# with a perpendicular control-point offset = the "parabolic" knob).
# CSS transitions are deliberately *not* used on the cursor, they would
# fight the per-frame JS updates and produce straight-line motion.

CURSOR_INIT_SCRIPT = r"""
(() => {
  if (window.__tesserae_cursor_installed__) return;
  window.__tesserae_cursor_installed__ = true;

  const css = `
    #__tesserae-cursor {
      position: fixed;
      top: 0; left: 0;
      width: 32px; height: 32px;
      pointer-events: none;
      z-index: 2147483647;
      transform: translate(-9999px, -9999px) translate(-50%, -50%);
      will-change: transform;
    }
    #__tesserae-cursor .halo {
      position: absolute;
      inset: 0;
      border-radius: 50%;
      background: rgba(15, 23, 42, 0.18);
      transform: scale(1);
      transition: transform 220ms cubic-bezier(.4, 0, .2, 1),
                  background-color 220ms ease;
    }
    #__tesserae-cursor.is-focused .halo {
      transform: scale(1.2);
      background: rgba(15, 23, 42, 0.24);
    }
    #__tesserae-cursor .dot {
      position: absolute;
      top: 50%; left: 50%;
      width: 14px; height: 14px;
      margin: -7px 0 0 -7px;
      border-radius: 50%;
      background: rgba(15, 23, 42, 0.92);
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
    }
    .__tesserae-ring {
      position: fixed;
      top: 0; left: 0;
      pointer-events: none;
      z-index: 2147483646;
      border: 1.5px solid rgba(15, 23, 42, 0.40);
      border-radius: 50%;
      animation: __tesserae_ring 800ms cubic-bezier(.2, 0, 0, 1) forwards;
      will-change: width, height, opacity, margin;
    }
    @keyframes __tesserae_ring {
      0%   { width: 32px; height: 32px;
             margin: -16px 0 0 -16px; opacity: 0.40; }
      100% { width: 84px; height: 84px;
             margin: -42px 0 0 -42px; opacity: 0; }
    }
  `;

  function ensureStyle() {
    if (document.getElementById("__tesserae-style")) return;
    const style = document.createElement("style");
    style.id = "__tesserae-style";
    style.textContent = css;
    (document.head || document.documentElement).appendChild(style);
  }

  function ensureCursor() {
    ensureStyle();
    let cursor = document.getElementById("__tesserae-cursor");
    if (cursor) return cursor;
    cursor = document.createElement("div");
    cursor.id = "__tesserae-cursor";
    cursor.innerHTML = '<div class="halo"></div><div class="dot"></div>';
    (document.body || document.documentElement).appendChild(cursor);
    return cursor;
  }

  function update(x, y) {
    const c = ensureCursor();
    c.style.transform = `translate(${x}px, ${y}px) translate(-50%, -50%)`;
  }
  function setFocus(on) {
    const c = ensureCursor();
    c.classList.toggle("is-focused", !!on);
  }
  function ring(x, y) {
    const host = document.body || document.documentElement;
    if (!host) return;
    const r = document.createElement("div");
    r.className = "__tesserae-ring";
    r.style.left = x + "px";
    r.style.top  = y + "px";
    host.appendChild(r);
    setTimeout(() => r.remove(), 900);
  }

  let glideRaf = null;
  function glide(fromX, fromY, toX, toY, durationMs, arc) {
    if (glideRaf !== null) { cancelAnimationFrame(glideRaf); glideRaf = null; }
    const dx = toX - fromX, dy = toY - fromY;
    const len = Math.sqrt(dx * dx + dy * dy);
    if (len < 0.5 || durationMs <= 0) { update(toX, toY); return; }
    const side = (Math.random() < 0.5) ? 1 : -1;
    const px = -dy / len * side;
    const py =  dx / len * side;
    const offset = arc * len * (0.92 + Math.random() * 0.16);
    const cx = (fromX + toX) / 2 + px * offset;
    const cy = (fromY + toY) / 2 + py * offset;
    const start = performance.now();
    function step() {
      const elapsed = performance.now() - start;
      const t = Math.min(1, elapsed / durationMs);
      const e = 1 - (1 - t) * (1 - t);
      const omt = 1 - e;
      const x = omt * omt * fromX + 2 * omt * e * cx + e * e * toX;
      const y = omt * omt * fromY + 2 * omt * e * cy + e * e * toY;
      update(x, y);
      if (t < 1) glideRaf = requestAnimationFrame(step);
      else glideRaf = null;
    }
    glideRaf = requestAnimationFrame(step);
  }

  window.__tesserae_move  = (x, y) => update(x, y);
  window.__tesserae_glide = (fx, fy, tx, ty, dur, arc) =>
    glide(fx, fy, tx, ty, dur, arc);
  window.__tesserae_focus = (x, y) => { update(x, y); setFocus(true);  };
  window.__tesserae_blur  = ()     => { setFocus(false); };
  window.__tesserae_click = (x, y) => { update(x, y); ring(x, y); };

  ensureStyle();
  if (document.body) ensureCursor();
})();
"""


# ----- Cursor driver (Python-side wrapper) ----------------------------


class CursorDriver:
    """Per-page helper that drives the injected cursor.

    Encapsulates the ``hover then act`` pattern used everywhere in the
    scenario scripts: glide the cursor onto a locator's centre along a
    curved path, raise the focus halo, hold briefly so the viewer reads
    "cursor landed", then perform the click / type / etc.

    ``last_pos`` is tracked across navigations on the Python side so the
    cursor restores at the previous click position immediately after
    every ``wait_for_url``, avoids the blink-off-during-navigation
    that the JS-side closure can't fix on its own."""

    # Default pacing; scenarios can monkey-patch on the instance.
    TYPE_DELAY_MS = 75
    GLIDE_BASE_MS = 450
    GLIDE_PER_PX_MS = 0.85
    GLIDE_ARC = 0.30
    FOCUS_HOLD_S = 0.5

    def __init__(self, page: Any, viewport: dict[str, int]) -> None:
        self.page = page
        self.last_pos: list[float] = [
            viewport["width"] / 2,
            viewport["height"] / 2,
        ]

    async def park_centre(self, viewport: dict[str, int] | None = None) -> None:
        """Park the cursor at the centre of the viewport on the current
        document. Use after navigation instead of restoring at the last
        clicked position, the previous click's coordinates often map
        onto nav links / buttons in the new document, which reads as
        the cursor hovering something it's about to click.

        Suppresses errors because the page may still be mid-transition
        between documents (``networkidle`` is a heuristic). If the
        evaluate fails, the cursor lazily reappears on the next move
        call from a ``focus_on`` / ``glide`` anyway."""
        with contextlib.suppress(Exception):
            await self.page.evaluate(
                """({ w, h }) => {
                  const cx = (w ?? window.innerWidth) / 2;
                  const cy = (h ?? window.innerHeight) / 2;
                  window.__tesserae_move(cx, cy);
                }""",
                {
                    "w": (viewport or {}).get("width"),
                    "h": (viewport or {}).get("height"),
                },
            )
        # Update last_pos so the next glide starts from the parked
        # position rather than the previous page's stale coords.
        self.last_pos[0] = (
            await self.page.evaluate("() => window.innerWidth / 2") or self.last_pos[0]
        )
        self.last_pos[1] = (
            await self.page.evaluate("() => window.innerHeight / 2") or self.last_pos[1]
        )

    async def focus_on(self, locator: Any, *, hold_s: float | None = None) -> tuple[float, float]:
        if hold_s is None:
            hold_s = self.FOCUS_HOLD_S
        # Smooth scroll instead of Playwright's instant
        # ``scroll_into_view_if_needed`` snap. ``block: center`` parks
        # the target in the middle of the viewport, well away from the
        # sticky topbar that intercepts clicks at the top edge.
        scrolled = await locator.evaluate(
            """el => {
              const before = window.scrollY;
              const rect = el.getBoundingClientRect();
              const inView =
                rect.top >= 60 && rect.bottom <= window.innerHeight - 20;
              if (inView) return false;
              el.scrollIntoView({behavior: 'smooth', block: 'center'});
              return true;
            }"""
        )
        if scrolled:
            # Smooth-scroll completes in ~400-600ms depending on
            # distance. Wait for the scroll to settle before reading
            # the bounding box so the cursor lands on the right pixel.
            await asyncio.sleep(0.55)
        box = await locator.bounding_box()
        if box is None:
            return (0.0, 0.0)
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        fx, fy = self.last_pos[0], self.last_pos[1]
        dx, dy = cx - fx, cy - fy
        distance = (dx * dx + dy * dy) ** 0.5
        duration_ms = int(self.GLIDE_BASE_MS + distance * self.GLIDE_PER_PX_MS)
        await self.page.evaluate(
            "([fx, fy, tx, ty, dur, arc]) => window.__tesserae_glide(fx, fy, tx, ty, dur, arc)",
            [fx, fy, cx, cy, duration_ms, self.GLIDE_ARC],
        )
        self.last_pos[0], self.last_pos[1] = cx, cy
        await asyncio.sleep(duration_ms / 1000)
        await self.page.evaluate("([x, y]) => window.__tesserae_focus(x, y)", [cx, cy])
        await asyncio.sleep(hold_s)
        return (cx, cy)

    async def click(
        self,
        locator: Any,
        *,
        hold_s: float | None = None,
        force: bool = False,
    ) -> None:
        """``force=True`` skips Playwright's "is this element clickable
        right now" intercept check, useful for labels whose child
        spans steal the pointer-events, or buttons partly under a
        sticky topbar that scroll_into_view_if_needed can't unstick."""
        cx, cy = await self.focus_on(locator, hold_s=hold_s)
        if cx or cy:
            await self.page.evaluate("([x, y]) => window.__tesserae_click(x, y)", [cx, cy])
        await locator.click(force=force)
        # The click may trigger a navigation that tears down the JS
        # context before the blur evaluate lands. That's a cosmetic
        # cleanup (drop the focus halo), don't let it fail the run.
        with contextlib.suppress(Exception):
            await self.page.evaluate("() => window.__tesserae_blur()")

    async def type_into(self, locator: Any, text: str, *, hold_s: float | None = None) -> None:
        await self.focus_on(locator, hold_s=hold_s)
        await locator.type(text, delay=self.TYPE_DELAY_MS)
        with contextlib.suppress(Exception):
            await self.page.evaluate("() => window.__tesserae_blur()")

    async def select_value(
        self,
        locator: Any,
        value: str,
        *,
        hold_s: float | None = None,
    ) -> None:
        """Set a ``<select>``'s value programmatically (Playwright's
        synthetic mouse on a native select doesn't render a visible
        dropdown in the recording). We still glide the cursor onto the
        control and ring it so the viewer sees an action happened."""
        cx, cy = await self.focus_on(locator, hold_s=hold_s)
        if cx or cy:
            await self.page.evaluate("([x, y]) => window.__tesserae_click(x, y)", [cx, cy])
        await locator.select_option(value)
        with contextlib.suppress(Exception):
            await self.page.evaluate("() => window.__tesserae_blur()")

    async def select_visibly(
        self,
        locator: Any,
        value: str,
        *,
        hold_s: float | None = None,
        listbox_rows: int = 10,
        option_hold_s: float = 0.45,
    ) -> None:
        """Pick a ``<select>`` value with a visible dropdown.

        Native ``<select>`` opens an OS-level dropdown that Chromium's
        recording pipeline doesn't capture. We work around it by
        temporarily promoting the select to ``size=N``, which renders
        as an in-document listbox the recorder *does* see, then
        gliding the cursor onto the target ``<option>`` and clicking
        it. The option click fires a real ``change`` event, which any
        ``data-reload-on-change`` handler on the page will pick up
        exactly as if the user had used the native dropdown.

        ``listbox_rows`` caps the listbox's visible height so a select
        with 50 widgets doesn't push the whole page down, the option
        scrolls into view inside the listbox before the click."""
        # Glide onto the control + ring it (so the recording reads as
        # "click the select").
        cx, cy = await self.focus_on(locator, hold_s=hold_s)
        if cx or cy:
            await self.page.evaluate("([x, y]) => window.__tesserae_click(x, y)", [cx, cy])

        # Promote the select into a listbox. ``size`` makes the options
        # render as a sibling block inside the document, captured by
        # the recording pipeline. We also nudge ``z-index`` up so the
        # listbox sits above subsequent layout cards instead of being
        # painted under them.
        await locator.evaluate(
            """(el, n) => {
              el.dataset.__priorSize = el.size || 1;
              el.size = Math.min(n, el.options.length);
              el.style.position = el.style.position || 'relative';
              el.style.zIndex = '50';
            }""",
            listbox_rows,
        )
        await asyncio.sleep(0.35)  # let the listbox lay out

        # Find the target option and scroll it into the listbox's
        # visible band before we look up its on-page coords.
        option = locator.locator(f'option[value="{value}"]').first
        await option.evaluate("el => el.scrollIntoView({block: 'nearest'})")
        await asyncio.sleep(0.2)

        box = await option.bounding_box()
        if box is not None:
            ox = box["x"] + box["width"] / 2
            oy = box["y"] + box["height"] / 2
            fx, fy = self.last_pos[0], self.last_pos[1]
            dx, dy = ox - fx, oy - fy
            distance = (dx * dx + dy * dy) ** 0.5
            duration_ms = int(self.GLIDE_BASE_MS + distance * self.GLIDE_PER_PX_MS)
            await self.page.evaluate(
                "([fx, fy, tx, ty, dur, arc]) => window.__tesserae_glide(fx, fy, tx, ty, dur, arc)",
                [fx, fy, ox, oy, duration_ms, self.GLIDE_ARC],
            )
            self.last_pos[0], self.last_pos[1] = ox, oy
            await asyncio.sleep(duration_ms / 1000)
            await self.page.evaluate("([x, y]) => window.__tesserae_focus(x, y)", [ox, oy])
            await asyncio.sleep(option_hold_s)
            await self.page.evaluate("([x, y]) => window.__tesserae_click(x, y)", [ox, oy])

        # Click the option for real, this fires ``change`` and any
        # ``data-reload-on-change`` handler swaps the cell's contents.
        # The reload tears down the JS context, so don't bother
        # restoring ``size``; the new document gets a fresh select.
        await option.click(force=True)
        with contextlib.suppress(Exception):
            await self.page.evaluate("() => window.__tesserae_blur()")


# ----- Orchestrator ---------------------------------------------------


PrepareFn = Callable[[Path], None]
DriveFn = Callable[[Any, str, CursorDriver], Awaitable[None]]


async def _record_with(
    port: int,
    video_dir: Path,
    viewport: dict[str, int],
    drive: DriveFn,
) -> None:
    from playwright.async_api import async_playwright

    base_url = f"http://127.0.0.1:{port}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(
            viewport=viewport,
            record_video_dir=str(video_dir),
            record_video_size=viewport,
        )
        await ctx.add_init_script(CURSOR_INIT_SCRIPT)
        page = await ctx.new_page()
        driver = CursorDriver(page, viewport)
        await drive(page, base_url, driver)
        await ctx.close()
        await browser.close()


def _need_ffmpeg(msg_for: str) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError(
            f"ffmpeg not found on PATH, install it (`brew install ffmpeg`) or "
            f"output a raw .webm instead of {msg_for}."
        )


def _transcode_to_mp4(src: Path, dst: Path) -> None:
    _need_ffmpeg(".mp4")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        check=True,
    )


def _transcode_to_gif(
    src: Path,
    dst: Path,
    *,
    fps: int = 12,
    width: int = 800,
) -> None:
    """Two-pass GIF transcode via ffmpeg's palettegen / paletteuse.

    Why two passes: a single-pass GIF uses ffmpeg's default 256-colour
    web-safe palette which butchers UI gradients and rings. Generating
    a *content-specific* palette in pass 1 and applying it in pass 2
    with a tiny dither produces a noticeably cleaner result at roughly
    half the file size.

    ``fps`` 12 is a sweet spot for screen recordings, smooth enough
    for cursor motion without inflating the file. ``width`` 800 keeps
    the README embed legible while staying well under the ~25 MB
    GitHub embed cap on a one-minute recording."""
    _need_ffmpeg(".gif")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as palette:
        palette_path = Path(palette.name)
    try:
        # Pass 1, generate the palette.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-vf",
                f"fps={fps},scale={width}:-1:flags=lanczos,palettegen=stats_mode=diff",
                str(palette_path),
            ],
            check=True,
        )
        # Pass 2, apply it with a light Bayer dither.
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(src),
                "-i",
                str(palette_path),
                "-filter_complex",
                (
                    f"fps={fps},scale={width}:-1:flags=lanczos[x];"
                    "[x][1:v]paletteuse=dither=bayer:bayer_scale=5"
                ),
                "-loop",
                "0",
                str(dst),
            ],
            check=True,
        )
    finally:
        palette_path.unlink(missing_ok=True)


def add_common_cli_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        required=True,
        help="Output file path. Suffix decides format: .mp4 transcodes "
        "to H.264, .gif via two-pass palettegen, .webm keeps the raw "
        "Playwright recording.",
    )
    parser.add_argument(
        "--mp4",
        action="store_true",
        help="Force transcode to H.264 mp4 via ffmpeg.",
    )
    parser.add_argument(
        "--gif",
        action="store_true",
        help="Force transcode to GIF via two-pass palettegen.",
    )
    parser.add_argument(
        "--gif-fps",
        type=int,
        default=12,
        help="Frames per second for GIF output (default: 12).",
    )
    parser.add_argument(
        "--gif-width",
        type=int,
        default=800,
        help="Width in pixels for GIF output, height auto-scales (default: 800).",
    )
    parser.add_argument("--viewport-width", type=int, default=1280)
    parser.add_argument("--viewport-height", type=int, default=1024)


def run_scenario(
    *,
    scenario_name: str,
    prepare: PrepareFn | None,
    drive: DriveFn,
    args: argparse.Namespace,
) -> None:
    """Spin up Tesserae against a tmpdir data root, optionally seed it
    via ``prepare``, then run ``drive`` against a recording Playwright
    context. ffmpeg-transcode the result if requested."""
    output = Path(args.output).expanduser().resolve()
    suffix = output.suffix.lower()
    want_gif = args.gif or suffix == ".gif"
    want_mp4 = args.mp4 or suffix == ".mp4"
    if want_gif and want_mp4:
        raise RuntimeError("pick one of --gif / --mp4, not both")
    viewport = {"width": args.viewport_width, "height": args.viewport_height}

    with tempfile.TemporaryDirectory(prefix=f"tesserae-record-{scenario_name}-") as td:
        td_path = Path(td)
        data_root = td_path / "data"
        video_dir = td_path / "video"
        video_dir.mkdir()

        if prepare is not None:
            print(f"[record] preparing data root for {scenario_name!r}")
            prepare(data_root)

        port = _free_port()
        print(f"[record] spinning up Tesserae on http://127.0.0.1:{port}")
        _serve_in_thread(port, data_root)
        _wait_ready(port)
        print("[record] ready, driving the scenario")

        asyncio.run(_record_with(port, video_dir, viewport, drive))
        print("[record] scenario complete, flushing video")

        webms = sorted(video_dir.glob("*.webm"))
        if not webms:
            raise RuntimeError(
                "Playwright didn't write a .webm, check the chromium "
                "browser is installed (`playwright install chromium`)."
            )
        webm = webms[0]

        output.parent.mkdir(parents=True, exist_ok=True)
        if want_gif:
            print(f"[record] transcoding to gif → {output}")
            _transcode_to_gif(webm, output, fps=args.gif_fps, width=args.gif_width)
        elif want_mp4:
            print(f"[record] transcoding to mp4 → {output}")
            _transcode_to_mp4(webm, output)
        else:
            shutil.copy(webm, output)
        print(f"[record] saved {output}")
