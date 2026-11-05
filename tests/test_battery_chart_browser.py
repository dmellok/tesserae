"""In a live Chromium: the battery chart's tooltip reads in light and dark, in
both UI designs; wake dots show their reason on hover and from the keyboard.

Set ``BATTERY_SHOTS=/some/dir`` to also save screenshots of each combination."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(_EXE or p.chromium.executable_path).exists()
    except Exception:
        return False


def _launch(p: Any) -> Any:
    return p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()


_HAS_CHROMIUM = _chromium_available()
if not _HAS_CHROMIUM and os.environ.get("TESSERAE_REQUIRE_BROWSER_TESTS") == "1":
    raise RuntimeError(
        "Playwright Chromium is required; run playwright install --with-deps chromium"
    )
pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")


def _seed(app: Flask, data_root: Path) -> None:
    client = app.test_client()
    for device_id, kind in (
        ("hall_frame", "visionect_place_and_play_32"),
        ("attic_esp", "esp32_client"),
        ("busy_esp", "esp32_client"),
    ):
        client.post(
            "/settings/devices/add",
            data={"id": device_id, "kind": kind, "name": device_id.replace("_", " ").title()},
        )
    store = app.config["BATTERY_HISTORY"]
    log = app.config["EVENT_LOG"]
    now = time.time()

    def at(i: int) -> float:
        return now - (48 - i) * 1800  # sample i of a sample every 30 minutes

    def backdate(ts: float) -> None:
        with contextlib.closing(sqlite3.connect(data_root / "core" / "events.db")) as conn, conn:
            conn.execute(
                "UPDATE events SET timestamp = ? WHERE id = (SELECT MAX(id) FROM events)", (ts,)
            )

    # The frame sleeps through samples 10..27 (a 9.5 hour gap); the ESP never does.
    for i in range(48):
        store.record("attic_esp", pct=100 - i // 2, timestamp=at(i))  # starts at 100%
        if not 10 <= i <= 27:
            store.record("hall_frame", pct=100 - i // 2, timestamp=at(i))
    # A busy display: a check-in every 4 minutes, with a cause now and then, so
    # the chart is crowded (the screenshot of it shows dots staying apart).
    for i in range(360):
        store.record("busy_esp", pct=100 - i // 9, timestamp=now - (360 - i) * 240)
    causes = [
        ("page", {}),
        ("home_assistant", {"button": "Next"}),
        ("scheduler", {}),
        ("button", {"button": "right"}),
        ("webhook", {}),
        ("deck", {}),
    ]
    for k, i in enumerate(range(5, 360, 7)):
        source, extra = causes[k % len(causes)]
        log.record(
            type="push",
            source=source,
            target="page_a",
            status="sent",
            extra={"device_ids": ["busy_esp"], **extra},
        )
        backdate(now - (360 - i) * 240 - 20)
    for i, reason in {60: "connection error", 61: "connection error", 200: "reboot"}.items():
        log.record(
            type="device",
            source="busy_esp",
            target="rest://busy_esp/status",
            status="ok",
            extra={"parsed": {"wake_reason": reason}},
        )
        backdate(now - (360 - i) * 240 + 2)
    # Quiet hours cover the frame's sleep, so the gap reads "asleep".
    pad = 1800
    start = datetime.fromtimestamp(at(9) - pad, UTC).strftime("%H:%M")
    end = datetime.fromtimestamp(at(28) + pad, UTC).strftime("%H:%M")
    app.config["SETTINGS_STORE"].patch_section(
        "app", {"quiet_hours_enabled": True, "quiet_hours_start": start, "quiet_hours_end": end}
    )
    # Home Assistant pressed Next, and the frame connected "by server request";
    # then it lost its connection, then it rebooted. These are the rows the
    # handlers write, moved back to the time of the sample they belong to.
    log.record(
        type="push",
        source="home_assistant",
        target="hall_frame",
        status="sent",
        extra={"device_ids": ["hall_frame"], "button": "Next"},
    )
    backdate(at(32) - 60)
    for idx, reason in {32: "by server request", 38: "connection error", 44: "reboot"}.items():
        log.record(
            type="device",
            source="hall_frame",
            target="rest://hall_frame/status",
            status="ok",
            extra={"parsed": {"connect_reason": reason}},
        )
        backdate(at(idx) + 2)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    data_root = tmp_path / "data"
    app: Flask = create_app(
        testing=False,
        data_root=data_root,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True, "timezone": "UTC"})
    store.patch_section("auth", {"disabled": True})
    _seed(app, data_root)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


# The Visionect card's chart (the one seeded with wake reasons).
CANVAS = "article[data-lf-text$=' hall_frame'] canvas.battery-chart"

# Tooltip colours as RGB triples + the contrast ratio between box and text.
_TOOLTIP_JS = """() => {
  const canvas = document.querySelector(__CANVAS__);
  const tip = Chart.getChart(canvas).options.plugins.tooltip;
  const c = document.createElement('canvas').getContext('2d');
  const rgb = (v) => { c.fillStyle = '#000'; c.fillStyle = v; c.fillRect(0, 0, 1, 1);
    return Array.from(c.getImageData(0, 0, 1, 1).data).slice(0, 3); };
  const lum = ([r, g, b]) => { const f = (x) => { x /= 255;
    return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const ratio = (a, b) => { const [hi, lo] = [lum(rgb(a)), lum(rgb(b))].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05); };
  const page = getComputedStyle(canvas.closest('.dx-battery-card')).backgroundColor;
  return { body: ratio(tip.backgroundColor, tip.bodyColor),
           title: ratio(tip.backgroundColor, tip.titleColor),
           edge: ratio(tip.borderColor, page) };
}""".replace("__CANVAS__", repr(CANVAS))

# Where wake dot ``i`` of the first chart is, in page coordinates.
_DOT_JS = """(label) => {
  const canvas = document.querySelector(__CANVAS__);
  const wakes = JSON.parse(canvas.dataset.wakes);
  const i = wakes.findIndex((w) => w.label === label);
  if (i < 0) return null;
  const el = Chart.getChart(canvas).getDatasetMeta(1).data[i];
  const r = canvas.getBoundingClientRect();
  return { x: r.left + el.x, y: r.top + el.y };
}""".replace("__CANVAS__", repr(CANVAS))

# Where the first gap's band is hovered, in page coordinates.
_GAP_JS = """() => {
  const canvas = document.querySelector(__CANVAS__);
  const chart = Chart.getChart(canvas);
  const gaps = JSON.parse(canvas.dataset.gaps);
  if (!gaps.length) return null;
  const mid = (gaps[0].start_ms + gaps[0].end_ms) / 2;
  const r = canvas.getBoundingClientRect();
  const area = chart.chartArea;
  return { x: r.left + chart.scales.x.getPixelForValue(mid),
           y: r.top + (area.top + area.bottom) / 2 };
}""".replace("__CANVAS__", repr(CANVAS))

# The first chart's state for one dot: crosshair x (relative to the canvas),
# the dot's x, its current radius, and whether it is the active element.
_STATE_JS = """(label) => {
  const canvas = document.querySelector(__CANVAS__);
  const chart = Chart.getChart(canvas);
  const wakes = JSON.parse(canvas.dataset.wakes);
  const i = wakes.findIndex((w) => w.label === label);
  const el = chart.getDatasetMeta(1).data[i];
  return { cross: canvas.dataset.crosshairX === undefined ? null : Number(canvas.dataset.crosshairX),
           x: el.x, radius: el.options.radius,
           active: chart.getActiveElements().some((a) => a.datasetIndex === 1 && a.index === i) };
}""".replace("__CANVAS__", repr(CANVAS))

# Whether any dot of any chart pokes outside its canvas (clipped), plus the y axis.
_BOUNDS_JS = """() => [...document.querySelectorAll('canvas.battery-chart')].map((canvas) => {
  const chart = Chart.getChart(canvas);
  const w = canvas.getBoundingClientRect().width;
  let out = 0;
  chart.getDatasetMeta(1).data.forEach((el, i) => {
    const r = el.options.hoverRadius + el.options.borderWidth;
    if (el.y - r < 0 || el.x - r < 0 || el.x + r > w) out += 1;
  });
  return { out, dots: chart.getDatasetMeta(1).data.length, clip: chart.data.datasets[1].clip,
           min: chart.scales.y.min, max: chart.scales.y.max,
           ticks: chart.scales.y.ticks.map((t) => t.value) };
})""".replace("__CANVAS__", repr(CANVAS))

_TIP_TEXT_JS = """() => {
  const t = Chart.getChart(document.querySelector(__CANVAS__)).tooltip;
  return { opacity: t.opacity, body: t.body.flatMap((b) => b.lines), title: t.title };
}""".replace("__CANVAS__", repr(CANVAS))


def _open(page: Any, base: str, app: Flask, *, ui: str, theme: str) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": ui})
    page.goto(f"{base}/devices/battery?window=1")
    page.evaluate(
        "(t) => { const r = document.documentElement;"
        " if (t === 'dark') r.setAttribute('data-theme', 'dark');"
        " else r.removeAttribute('data-theme'); }",
        theme,
    )
    page.wait_for_selector("canvas.battery-chart")
    page.wait_for_timeout(300)  # the theme observer restyles on the next frame


@pytest.mark.parametrize("ui", ["classic", "paper"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_tooltip_is_readable_and_dots_explain_themselves(
    served: tuple[str, Flask], ui: str, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    shots = os.environ.get("BATTERY_SHOTS")
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 2400})
        _open(page, base, app, ui=ui, theme=theme)
        assert page.evaluate("document.documentElement.dataset.ui || 'classic'") == (
            "paper" if ui == "paper" else "classic"
        )

        # Readable: text on the box clears WCAG AA, and its border stands off the card.
        ratios = page.evaluate(_TOOLTIP_JS)
        assert ratios["body"] >= 4.5, ratios
        assert ratios["title"] >= 3.0, ratios
        assert ratios["edge"] >= 1.2, ratios

        # Hovering a dot names its reason.
        spot = page.evaluate(_DOT_JS, "Home Assistant: Next")
        assert spot is not None
        page.mouse.move(spot["x"], spot["y"])
        page.wait_for_timeout(150)
        tip = page.evaluate(_TIP_TEXT_JS)
        assert tip["opacity"] > 0
        assert "Home Assistant: Next" in tip["body"], tip
        # The hovered dot grows and a crosshair marks where it falls in time.
        hovered = page.evaluate(_STATE_JS, "Home Assistant: Next")
        assert hovered["active"] and hovered["cross"] is not None
        assert abs(hovered["cross"] - hovered["x"]) <= 1
        page.mouse.move(2, 2)
        page.wait_for_timeout(150)
        rest = page.evaluate(_STATE_JS, "Home Assistant: Next")
        assert not rest["active"] and rest["cross"] is None
        assert hovered["radius"] >= rest["radius"] + 2, (hovered, rest)
        page.mouse.move(spot["x"], spot["y"])
        page.wait_for_timeout(150)

        # No dot is cut off at 100%, at 0% or at the window edges.
        for chart in page.evaluate(_BOUNDS_JS):
            assert chart["out"] == 0, chart
            assert chart["clip"] is False
            assert chart["min"] < 0 and chart["max"] > 100
            assert chart["ticks"] == [0, 25, 50, 75, 100]
        if shots:
            Path(shots).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(shots) / f"batteries-{ui}-{theme}.png"), full_page=True)

        # A span with no readings says why, instead of reading as measured data.
        spot = page.evaluate(_GAP_JS)
        assert spot is not None
        page.mouse.move(spot["x"], spot["y"])
        page.wait_for_timeout(150)
        gap_tip = page.evaluate(_TIP_TEXT_JS)
        assert gap_tip["opacity"] > 0
        assert "Asleep" in gap_tip["body"][0] and "(quiet hours), no readings" in gap_tip["body"][0]
        assert page.evaluate(f"document.querySelector({CANVAS!r}).dataset.crosshairX") is not None

        if shots:
            page.screenshot(
                path=str(Path(shots) / f"batteries-{ui}-{theme}-gap.png"), full_page=True
            )

        # The key lists only the causes drawn, plus the band.
        key = page.inner_text("article[data-lf-text$=' hall_frame'] .dx-wake-legend")
        assert "Home Assistant" in key and "Reconnected" in key and "Restarted or crashed" in key
        assert "No readings" in key and "Manual push" not in key

        # And from the keyboard: focus the chart, step back one wake.
        page.mouse.move(2, 2)
        page.evaluate(f"document.querySelector({CANVAS!r}).focus()")
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(150)
        tip = page.evaluate(_TIP_TEXT_JS)
        assert tip["opacity"] > 0
        assert any("%" in line for line in tip["body"])
        before = page.evaluate(f"document.querySelector({CANVAS!r}).dataset.crosshairX")
        page.keyboard.press("ArrowLeft")
        page.wait_for_timeout(150)
        after = page.evaluate(f"document.querySelector({CANVAS!r}).dataset.crosshairX")
        assert before is not None and after is not None and float(after) < float(before)
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
        assert page.evaluate(_TIP_TEXT_JS)["opacity"] == 0
        assert page.evaluate(f"document.querySelector({CANVAS!r}).dataset.crosshairX") is None
        browser.close()


def test_visionect_card_has_prediction_tiles_in_the_browser(served: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page()
        _open(page, base, app, ui="paper", theme="light")
        labels = page.evaluate(
            """() => Object.fromEntries([...document.querySelectorAll('.dx-battery-card')].map(
                 (c) => [c.dataset.lfText.split(' ').pop(),
                         [...c.querySelectorAll('.dx-stat-label')].map((l) => l.textContent.trim())]))"""
        )
        browser.close()
    assert "Reaches 0%" in labels["hall_frame"]
    assert "Drain rate" in labels["attic_esp"]


def test_a_gap_outside_quiet_hours_reads_as_offline(served: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    app.config["SETTINGS_STORE"].patch_section("app", {"quiet_hours_enabled": False})
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 2400})
        _open(page, base, app, ui="classic", theme="light")
        spot = page.evaluate(_GAP_JS)
        page.mouse.move(spot["x"], spot["y"])
        page.wait_for_timeout(150)
        body = page.evaluate(_TIP_TEXT_JS)["body"][0]
        browser.close()
    assert body.startswith("No readings ") and body.endswith("(offline)")


ATTIC = "article[data-lf-text$=' attic_esp']"
BUSY = "article[data-lf-text$=' busy_esp']"


def _state(page: Any, card: str) -> dict[str, Any]:
    """A card's chart window, wakes, samples tile and pressed range."""
    return page.evaluate(
        """(card) => {
          const root = document.querySelector(card);
          const canvas = root.querySelector('canvas.battery-chart');
          const chart = Chart.getChart(canvas);
          const tile = (label) => [...root.querySelectorAll('.dx-stat-tile')]
            .find((t) => t.querySelector('.dx-stat-label').textContent.trim() === label);
          const pressed = [...root.querySelectorAll('.dx-battery-range button')]
            .filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.textContent);
          return { hours: Number(canvas.dataset.windowHours), series: JSON.parse(canvas.dataset.series).length,
                   samples: Number(tile('Samples').querySelector('.dx-stat-value').textContent),
                   pressed, xmin: chart.scales.x.min, xmax: chart.scales.x.max,
                   resetHidden: root.querySelector('[data-battery-zoom-reset]').hidden,
                   resetShown: getComputedStyle(root.querySelector('[data-battery-zoom-reset]')).display !== 'none' };
        }""",
        card,
    )


def test_each_chart_has_its_own_range_that_survives_a_reload(served: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 2400})
        _open(page, base, app, ui="classic", theme="light")  # page default: 1 day
        before = _state(page, ATTIC)
        busy_before = _state(page, BUSY)
        assert before["hours"] == 24 and before["pressed"] == ["24h"]

        # 12h: only this card changes, without a reload, tiles included.
        page.click(f"{ATTIC} .dx-battery-range button[data-hours='12']")
        page.wait_for_function(
            "(c) => document.querySelector(c + ' canvas').dataset.windowHours === '12'", arg=ATTIC
        )
        after = _state(page, ATTIC)
        assert after["pressed"] == ["12h"]
        assert 0 < after["series"] < before["series"] and after["samples"] == after["series"]
        assert _state(page, BUSY) == busy_before
        if os.environ.get("BATTERY_SHOTS"):
            page.screenshot(path=str(Path(os.environ["BATTERY_SHOTS"]) / "batteries-12h.png"))
        assert after["xmax"] - after["xmin"] <= 12 * 3600 * 1000 + 1

        # The choice is remembered per device: a reload opens this card on 12h.
        page.reload()
        page.wait_for_function(
            "(c) => document.querySelector(c + ' canvas').dataset.windowHours === '12'", arg=ATTIC
        )
        assert _state(page, ATTIC)["pressed"] == ["12h"]
        assert _state(page, BUSY)["hours"] == 24

        # And it switches back, key and tiles following.
        page.click(f"{ATTIC} .dx-battery-range button[data-hours='168']")
        page.wait_for_function(
            "(c) => document.querySelector(c + ' canvas').dataset.windowHours === '168'", arg=ATTIC
        )
        assert _state(page, ATTIC)["pressed"] == ["7d"]
        browser.close()


def test_dragging_across_a_chart_zooms_and_reset_undoes_it(served: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 2400})
        _open(page, base, app, ui="paper", theme="dark")
        whole = _state(page, ATTIC)
        assert whole["resetHidden"] is True and whole["resetShown"] is False  # truly not displayed
        box = page.evaluate(
            """(card) => { const c = document.querySelector(card + ' canvas');
              const a = Chart.getChart(c).chartArea; const r = c.getBoundingClientRect();
              return { x0: r.left + a.left + (a.right - a.left) * 0.3,
                       x1: r.left + a.left + (a.right - a.left) * 0.6,
                       y: r.top + (a.top + a.bottom) / 2 }; }""",
            ATTIC,
        )
        page.mouse.move(box["x0"], box["y"])
        page.mouse.down()
        page.mouse.move((box["x0"] + box["x1"]) / 2, box["y"], steps=4)
        page.mouse.move(box["x1"], box["y"], steps=4)
        page.mouse.up()
        zoomed = _state(page, ATTIC)
        assert zoomed["resetHidden"] is False and zoomed["resetShown"] is True
        if os.environ.get("BATTERY_SHOTS"):
            page.screenshot(path=str(Path(os.environ["BATTERY_SHOTS"]) / "batteries-zoomed.png"))
        assert (zoomed["xmax"] - zoomed["xmin"]) < 0.4 * (whole["xmax"] - whole["xmin"])
        # Dots outside the zoomed span are not drawn.
        shown = page.evaluate(
            "(c) => Chart.getChart(document.querySelector(c + ' canvas')).data.datasets[1].data.length",
            ATTIC,
        )
        assert shown < whole["series"]
        page.click(f"{ATTIC} [data-battery-zoom-reset]")
        reset = _state(page, ATTIC)
        assert reset["resetHidden"] is True and reset["xmin"] == whole["xmin"]
        browser.close()


def test_clear_history_is_a_small_quiet_link_in_the_cards_top_right(
    served: tuple[str, Flask],
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = served
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1100, "height": 2400})
        _open(page, base, app, ui="classic", theme="light")
        geo = page.evaluate(
            """(card) => { const root = document.querySelector(card);
              const link = root.querySelector('.dx-battery-clear button').getBoundingClientRect();
              const r = root.getBoundingClientRect();
              const chart = root.querySelector('.dx-battery-chartbox').getBoundingClientRect();
              return { right: r.right - link.right, top: link.top - r.top,
                       chartTop: chart.top - r.top, w: link.width, h: link.height }; }""",
            ATTIC,
        )
        browser.close()
    assert geo["top"] < geo["chartTop"]  # in the header, above the chart
    assert geo["right"] < 40  # against the right edge
    assert geo["h"] < 24 and geo["w"] < 160  # text-sized, not a big button
