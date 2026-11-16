"""The Test Display case in a live Chromium: one Live row, the print lineup that
won but has not landed Queued, the one whose printer is idle Waiting, and the
day bar drawing the three all-day lineups in separate lanes, on a desktop and
a phone, from the server's markup and from the page's own redraw."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, render_template

from app.lineups_view import build_view
from app.main import create_app
from tests.test_lineups_browser import _HAS_CHROMIUM, _launch, _open_displays_on_phones
from tests.test_lineups_status import DASH, DOOR_FRAME, NOW, door

pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")

DOOR = '.lu-ds[data-display="test_display"]'


def _vm(merged: bool = False) -> dict[str, Any]:
    d = door(DOOR_FRAME) if merged else door()
    return build_view([d], DASH, now=NOW, time_cycle="h12").to_dict()


@pytest.fixture
def live(tmp_path: Path) -> Iterator[str]:
    from werkzeug.serving import make_server

    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    app: Flask = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=plugins_dir)
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    app.add_url_rule(
        "/lineups-door",
        "lineups_door",
        lambda: render_template("decks.html", vm=_vm(), automation_url="#auto"),
    )
    app.add_url_rule(
        "/lineups-merged",
        "lineups_merged",
        lambda: render_template("decks.html", vm=_vm(merged=True), automation_url="#auto"),
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


_REPORT = """(sec) => {
  const rows = [...sec.querySelectorAll('.lu-row')].map((r) => ({
    id: r.dataset.lineup,
    status: r.dataset.status,
    tag: r.querySelector('.lu-tag-st').textContent.trim(),
    why: (r.querySelector('.lu-hs-why') || {}).textContent || '',
    grey: r.classList.contains('is-off'),
  }));
  const trk = sec.querySelector('.lu-today .lu-trk');
  trk.scrollIntoView({block: 'center'});
  const tb = trk.getBoundingClientRect();
  const blocks = [...trk.querySelectorAll('.lu-blk:not(.hold)')].map((b) => {
    const r = b.getBoundingClientRect();
    return {id: b.dataset.lineup, live: b.classList.contains('live'),
            cls: b.className, top: r.top, bottom: r.bottom, left: r.left, right: r.right,
            inside: r.top >= tb.top - 0.5 && r.bottom <= tb.bottom + 0.5,
            // nothing covers it (a read-only phone bar takes no presses at all)
            hit: getComputedStyle(b).pointerEvents === 'none' ||
                 document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2)
                   ?.closest('.lu-blk') === b};
  });
  return {rows, blocks, trackH: tb.height};
}"""


def _check(got: dict[str, Any]) -> None:
    rows = {r["id"]: r for r in got["rows"]}
    assert [r["status"] for r in got["rows"]].count("live") == 1, got["rows"]
    assert rows["photos_all_day"]["status"] == "live"
    assert rows["print_alpha_door"]["status"] == "queued"
    assert rows["print_alpha_door"]["tag"].lower() == "queued"
    assert rows["print_alpha_door"]["why"].startswith("lands ~")
    beta = rows["print_beta_door"]
    assert beta["status"] == "waiting" and beta["tag"].lower() == "waiting" and beta["grey"]
    assert beta["why"] == "until BETA Printer Print status is running"
    blocks = {b["id"]: b for b in got["blocks"]}
    assert set(blocks) == {"photos_all_day", "print_alpha_door", "print_beta_door"}
    assert [b["id"] for b in got["blocks"] if b["live"]] == ["photos_all_day"]
    assert "st-queued" in blocks["print_alpha_door"]["cls"]
    assert "st-waiting" in blocks["print_beta_door"]["cls"]
    # Every lane is visible: inside the track, reachable, stacked without overlap.
    ordered = sorted(blocks.values(), key=lambda b: b["top"])
    assert ordered[0]["id"] == "photos_all_day"
    for b in ordered:
        assert b["inside"] and b["hit"] and b["bottom"] - b["top"] >= 5, b
    for a, b in pairwise(ordered):
        assert a["bottom"] <= b["top"] + 0.5, (a, b)
    # Compact: thin lanes add little to the bar.
    assert got["trackH"] <= 70


@pytest.mark.parametrize(
    ("width", "mobile"), [(1440, False), (390, True)], ids=["desktop", "phone"]
)
def test_door_frame_shows_one_live_row_and_laned_day_bar(
    live: str, width: int, mobile: bool
) -> None:
    from playwright.sync_api import sync_playwright

    vm = _vm()
    with sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(
            viewport={"width": width, "height": 900},
            **({"has_touch": True, "is_mobile": True} if mobile else {}),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Phones start with every display collapsed; this one is opened.
        _open_displays_on_phones(page, ["test_display"])
        page.route(
            "**/decks/view.json*",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(vm)
            ),
        )
        # The server's markup, before the script runs.
        ctx_nojs = browser.new_context(
            viewport={"width": width, "height": 900}, java_script_enabled=False
        )
        static = ctx_nojs.new_page()
        static.goto(live + "/lineups-door", wait_until="load")
        _check(static.locator(DOOR).evaluate(_REPORT))
        ctx_nojs.close()
        # The page's own redraw from the same view-model.
        page.goto(live + "/lineups-door", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        page.evaluate("() => TesseraeLineups.render()")
        _check(page.locator(DOOR).evaluate(_REPORT))
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
        assert errors == []
        browser.close()


_STRIP = """(row) => [...row.querySelectorAll('.lu-step')].map((st) => ({
  meta: st.querySelector('.lu-step-meta').textContent.trim(),
  chip: (st.querySelector('.lu-cond') || {}).textContent || '',
  chipCls: (st.querySelector('.lu-cond') || {className: ''}).className,
  title: (st.querySelector('.lu-cond') || {title: ''}).title,
  sep: (st.querySelector('.lu-sep') || {textContent: null}).textContent,
  loop: !!st.querySelector('.lu-sep.loop'),
  arrow: !!st.querySelector('.lu-sep:not(.else):not(.loop)'),
}))"""


def _check_strip(steps: list[dict[str, Any]]) -> None:
    assert [s["meta"] for s in steps] == ["1st choice", "2nd choice", "3rd choice"]
    assert steps[0]["chip"] == "Only while Printer A Print status is running"
    assert "ok" in steps[0]["chipCls"].split()
    assert steps[0]["title"] == "Shows only while Printer A Print status is running."
    assert "no" in steps[1]["chipCls"].split()
    assert steps[2]["chip"] == "Fallback"
    assert [s["sep"] for s in steps] == ["otherwise", "otherwise", None]
    assert not any(s["loop"] or s["arrow"] for s in steps)


def test_priority_lineup_reads_as_choices_with_condition_chips(live: str) -> None:
    from playwright.sync_api import sync_playwright

    vm = _vm(merged=True)
    with sync_playwright() as p:
        browser = _launch(p)
        nojs = browser.new_context(
            viewport={"width": 1440, "height": 900}, java_script_enabled=False
        )
        static = nojs.new_page()
        static.goto(live + "/lineups-merged", wait_until="load")
        _check_strip(static.locator("#lu-row-door_frame").evaluate(_STRIP))
        nojs.close()
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # Phones start with every display collapsed; this one is opened.
        _open_displays_on_phones(page, ["test_display"])
        page.route(
            "**/decks/view.json*",
            lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(vm)
            ),
        )
        page.goto(live + "/lineups-merged", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        page.evaluate("() => TesseraeLineups.render()")
        row = page.locator("#lu-row-door_frame")
        if "is-folded" in (row.get_attribute("class") or ""):
            page.locator('[data-fold="door_frame"]').click()
        _check_strip(row.evaluate(_STRIP))
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
        assert errors == []
        browser.close()
