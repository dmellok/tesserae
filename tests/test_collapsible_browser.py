"""The shared collapsible-section component in a live Chromium: every section
starts expanded, the chevron or the empty header space collapses it, the
choice survives a reload (localStorage, per page and section), the keyboard
toggles it, a #hash link or a field that fails validation inside a collapsed
section opens it, and reduced motion turns the chevron's transition off. Runs
against the real pages, in both UI designs and both themes."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
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


_HAS_CHROMIUM = _chromium_available()
if not _HAS_CHROMIUM and os.environ.get("TESSERAE_REQUIRE_BROWSER_TESTS") == "1":
    raise RuntimeError(
        "Playwright Chromium is required; run playwright install --with-deps chromium"
    )
pytestmark = pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed")

STATS = "/stats/"
FEEDS = "/plugins/calendar_core/"
PREFIX = "tesserae-collapsed:"


@pytest.fixture
def site(tmp_path: Path) -> Iterator[str]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


@pytest.fixture
def browser() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        yield b
        b.close()


def _page(browser: Any, ctx: Any = None) -> Any:
    ctx = ctx or browser.new_context(viewport={"width": 1280, "height": 900})
    return ctx.new_page()


def _collapsed(pg: Any, sec: str) -> bool:
    return pg.evaluate(
        "id => document.getElementById(id + '-body').hidden"
        " && document.getElementById(id).classList.contains('is-collapsed')"
        " && document.querySelector('#' + id + ' [data-collapsible-toggle]')"
        ".getAttribute('aria-expanded') === 'false'",
        sec,
    )


def _expanded(pg: Any, sec: str) -> bool:
    return pg.evaluate(
        "id => !document.getElementById(id + '-body').hidden"
        " && !document.getElementById(id).classList.contains('is-collapsed')"
        " && document.querySelector('#' + id + ' [data-collapsible-toggle]')"
        ".getAttribute('aria-expanded') === 'true'",
        sec,
    )


def _key(path: str, sec: str) -> str:
    return PREFIX + path.rstrip("/") + "#" + sec


def test_every_section_starts_expanded(browser: Any, site: str) -> None:
    pg = _page(browser)
    pg.goto(site + STATS)
    ids = pg.evaluate("[...document.querySelectorAll('[data-collapsible]')].map(e => e.id)")
    assert len(ids) >= 4
    for sec in ids:
        assert _expanded(pg, sec), sec
    assert pg.locator("#sec-this-install-body").is_visible()


def test_chevron_collapses_and_the_choice_is_remembered_per_page(browser: Any, site: str) -> None:
    pg = _page(browser)
    pg.goto(site + STATS)
    pg.click("#sec-outcomes [data-collapsible-toggle]")
    assert _collapsed(pg, "sec-outcomes")
    assert not pg.locator("#sec-outcomes-body").is_visible()
    assert _expanded(pg, "sec-displays")
    # The accessible name follows the state.
    assert "Expand Outcomes" in pg.inner_text("#sec-outcomes [data-collapsible-toggle]")
    assert pg.evaluate("k => localStorage.getItem(k)", _key("/stats", "sec-outcomes")) == "1"

    pg.reload()
    assert _collapsed(pg, "sec-outcomes")
    assert _expanded(pg, "sec-displays")

    pg.click("#sec-outcomes [data-collapsible-toggle]")
    assert _expanded(pg, "sec-outcomes")
    assert pg.evaluate("k => localStorage.getItem(k)", _key("/stats", "sec-outcomes")) is None
    assert "Collapse Outcomes" in pg.inner_text("#sec-outcomes [data-collapsible-toggle]")


def test_clicking_empty_header_space_toggles(browser: Any, site: str) -> None:
    pg = _page(browser)
    pg.goto(site + STATS)
    box = pg.locator("#sec-this-install .dx-section-head").bounding_box()
    # Middle of the header, right of the title text: bare header space.
    pg.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2)
    assert _collapsed(pg, "sec-this-install")
    pg.mouse.click(box["x"] + box["width"] * 0.75, box["y"] + box["height"] / 2)
    assert _expanded(pg, "sec-this-install")


def test_keyboard_toggles_with_enter_and_space(browser: Any, site: str) -> None:
    pg = _page(browser)
    pg.goto(site + STATS)
    btn = pg.locator("#sec-displays [data-collapsible-toggle]")
    btn.focus()
    pg.keyboard.press("Enter")
    assert _collapsed(pg, "sec-displays")
    pg.keyboard.press("Space")
    assert _expanded(pg, "sec-displays")
    assert pg.evaluate("document.activeElement.hasAttribute('data-collapsible-toggle')")


def test_a_hash_link_opens_the_collapsed_section_and_scrolls_to_it(browser: Any, site: str) -> None:
    ctx = browser.new_context(viewport={"width": 1280, "height": 500})
    ctx.add_init_script(
        f"localStorage.setItem({_key('/stats', 'sec-where-this-data-lives')!r}, '1')"
    )
    pg = ctx.new_page()
    pg.goto(site + STATS + "#sec-where-this-data-lives")
    assert _expanded(pg, "sec-where-this-data-lives")
    pg.wait_for_timeout(300)
    top = pg.evaluate(
        "document.getElementById('sec-where-this-data-lives').getBoundingClientRect().top"
    )
    assert -5 <= top < 250, top

    # A link clicked on the page, and a hashchange, do the same.
    pg.click("#sec-outcomes [data-collapsible-toggle]")
    assert _collapsed(pg, "sec-outcomes")
    pg.evaluate("location.hash = '#sec-outcomes-body'")
    pg.wait_for_function("!document.getElementById('sec-outcomes-body').hidden")
    assert _expanded(pg, "sec-outcomes")


def test_a_field_that_fails_validation_in_a_collapsed_section_opens_it(
    browser: Any, site: str
) -> None:
    pg = _page(browser)
    pg.goto(site + FEEDS)
    pg.click("#cal-add [data-collapsible-toggle]")
    assert _collapsed(pg, "cal-add")
    pg.evaluate("document.querySelector('#cal-add form').requestSubmit()")
    pg.wait_for_function("!document.getElementById('cal-add-body').hidden")
    assert _expanded(pg, "cal-add")
    assert pg.locator("#feed-name").is_visible()


def test_the_other_card_chrome_works_the_same(browser: Any, site: str) -> None:
    pg = _page(browser)
    pg.goto(site + FEEDS)
    pg.click("#cal-feeds [data-collapsible-toggle]")
    assert _collapsed(pg, "cal-feeds")
    assert (
        pg.evaluate("k => localStorage.getItem(k)", _key("/plugins/calendar_core", "cal-feeds"))
        == "1"
    )
    # The other cards are untouched.
    assert _expanded(pg, "cal-add")


@pytest.mark.parametrize("ui", ["classic", "paper"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chevron_is_drawn_and_a_collapsed_section_is_only_its_header(
    browser: Any, site: str, ui: str, theme: str, tmp_path: Path
) -> None:
    ctx = browser.new_context(viewport={"width": 390, "height": 800})
    ctx.add_init_script(f"localStorage.setItem('tesserae-theme', '{theme}')")
    pg = ctx.new_page()
    pg.goto(site + STATS)
    pg.evaluate(
        "ui => { if (ui === 'classic') document.documentElement.removeAttribute('data-ui');"
        " else document.documentElement.setAttribute('data-ui', 'paper'); }",
        ui,
    )
    box = pg.locator("#sec-this-install [data-collapsible-toggle]").bounding_box()
    assert box and box["width"] >= 32 and box["height"] >= 32
    assert box["x"] + box["width"] <= 390 + 1, "the chevron stays inside a phone screen"
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    open_h = pg.locator("#sec-this-install").bounding_box()["height"]
    pg.click("#sec-this-install [data-collapsible-toggle]")
    shut_h = pg.locator("#sec-this-install").bounding_box()["height"]
    assert shut_h < open_h - 40 and shut_h < 170, (open_h, shut_h)


def test_reduced_motion_turns_the_chevron_transition_off(browser: Any, site: str) -> None:
    ctx = browser.new_context(reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(site + STATS)
    dur = pg.evaluate(
        "getComputedStyle(document.querySelector('[data-collapsible-toggle] .ph')).transitionDuration"
    )
    assert dur in ("0s", "0ms")
    pg2 = _page(browser)
    pg2.goto(site + STATS)
    assert (
        pg2.evaluate(
            "getComputedStyle(document.querySelector('[data-collapsible-toggle] .ph')).transitionDuration"
        )
        != "0s"
    )


def test_collapsed_sections_are_hidden_in_print_and_the_body_never_animates_layout(
    browser: Any, site: str
) -> None:
    pg = _page(browser)
    pg.goto(site + STATS)
    pg.click("#sec-outcomes [data-collapsible-toggle]")
    pg.emulate_media(media="print")
    assert not pg.locator("#sec-outcomes-body").is_visible()
    assert not pg.locator("#sec-outcomes [data-collapsible-toggle]").is_visible()
    props = pg.evaluate(
        "getComputedStyle(document.getElementById('sec-displays-body')).transitionProperty"
    )
    assert "height" not in props


def test_a_search_that_matches_inside_a_collapsed_group_opens_it(browser: Any, site: str) -> None:
    pg = _page(browser)
    pg.goto(site + "/plugins/")
    pg.click("#sec-font [data-collapsible-toggle]")
    assert _collapsed(pg, "sec-font")
    pg.fill("[data-lf-search]", "core fonts")
    pg.wait_for_function("!document.getElementById('sec-font-body').hidden")
    assert _expanded(pg, "sec-font")
    # Not remembered: the stored choice is still "collapsed" for the next visit.
    assert pg.evaluate("k => localStorage.getItem(k)", _key("/plugins", "sec-font")) == "1"


_CONTRAST_JS = """
sel => {
  const px = (css) => {
    const c = document.createElement('canvas'); c.width = c.height = 1;
    const g = c.getContext('2d', { willReadFrequently: true });
    g.clearRect(0, 0, 1, 1); g.fillStyle = css; g.fillRect(0, 0, 1, 1);
    return Array.from(g.getImageData(0, 0, 1, 1).data);
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const el = document.querySelector(sel);
  const fg = px(getComputedStyle(el).color);
  let bg = null;
  for (let n = el; n && !bg; n = n.parentElement) {
    const p = px(getComputedStyle(n).backgroundColor);
    if (p[3] === 255) bg = p;
  }
  bg = bg || [255, 255, 255, 255];
  const [a, b] = [lum(fg), lum(bg)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
"""


@pytest.mark.parametrize("ui", ["classic", "paper"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_sub_section_titles_keep_4_5_to_1_contrast(
    browser: Any, site: str, ui: str, theme: str
) -> None:
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('tesserae-theme', '{theme}')")
    pg = ctx.new_page()
    pg.goto(site + STATS)
    pg.evaluate(
        "ui => { if (ui === 'classic') document.documentElement.removeAttribute('data-ui');"
        " else document.documentElement.setAttribute('data-ui', 'paper'); }",
        ui,
    )
    assert pg.evaluate(_CONTRAST_JS, ".dx-subhead") >= 4.5
    style = pg.evaluate(
        "() => { const s = getComputedStyle(document.querySelector('.dx-subhead'));"
        " return [s.fontSize, s.fontWeight, s.textTransform]; }"
    )
    assert style[1] == "700" and style[2] == "none"
    assert style[0] == ("18px" if ui == "paper" else "15px")
    # the chevron icon is a UI component: 3:1 against the card
    assert pg.evaluate(_CONTRAST_JS, "#sec-this-install [data-collapsible-toggle]") >= 3.0
