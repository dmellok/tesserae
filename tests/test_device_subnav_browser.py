"""Acceptance tests for the device-page sub-nav (live Chromium).

The sub-nav intercepts its own anchor clicks and smooth-scrolls instead, then
used to ALSO ``scrollIntoView`` the clicked pill so the phone strip keeps it
centred. Two scroll requests against the same scrolling box is a browser
coin-flip: Chromium drops the second one as a no-op when the element is
already in view, Firefox honours it and cancels the first, so the click did
nothing at all (#326).

The divergence is invisible to a source-level assertion and only reproduces in
the browser that loses the race, so what's pinned here is the invariant that
makes the behaviour browser-independent: one click issues exactly ONE scroll
against the document, and the pill is kept in view by scrolling the nav's own
scrollport instead.

The second half of #326: once the click scrolled, the section landed UNDER the
sticky topbar, because both the sub-nav's sticky offset and the section's
scroll-margin were fixed 16px. The topbar's nav wraps onto a second row
between roughly 900px and 1100px wide, so no fixed number works; the height is
measured into ``--t-topbar-h`` and both offsets are derived from it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.main import REPO_ROOT


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_available(), reason="Playwright Chromium not installed"
)

# The anchors device_page.html emits, in order.
_ANCHORS = [
    ("overview", "Overview"),
    ("identity", "Identity"),
    ("display", "Display"),
    ("timing", "Timing"),
    ("controls", "Controls"),
    ("rendering", "Rendering"),
    ("power", "Power"),
    ("lineups", "Lineups"),
    ("connection", "Connection"),
    ("remove", "Remove"),
]

# Record every scrollIntoView the page makes, so a second document-level
# scroll request can't sneak back in unnoticed.
_SPY_JS = """() => {
  window.__sivTargets = [];
  const real = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = function (opts) {
    window.__sivTargets.push(this.id || this.className);
    return real.call(this, opts);
  };
}"""


# True once the smooth scroll has stopped moving. Waiting on the final
# POSITION instead would pass the moment the animation happened to travel
# through it, which is every run.
_SCROLL_SETTLED = """() => {
  const y = window.scrollY;
  const stable = window.__lastY === y;
  window.__lastY = y;
  return y > 0 && stable;
}"""

# #power comes to rest 16px under the topbar, not 16px under the viewport.
_GAP_ABOVE_SECTION = """() => {
  const bar = document.querySelector('.topbar').getBoundingClientRect().height;
  return document.getElementById('power').getBoundingClientRect().top - bar;
}"""

_NAV = "document.querySelector('[data-subnav]')"
_NAV_SCROLL_LEFT = f"() => {_NAV}.scrollLeft"
_NAV_OVERFLOWS = f"() => {_NAV}.scrollWidth > {_NAV}.clientWidth"


@pytest.fixture
def page_path(tmp_path: Path) -> Path:
    """The sub-nav markup device_page.html emits, with the real stylesheet
    and the real controller inlined, as a file:// page. No server needed:
    the sub-nav reads nothing but its own links and their sections."""
    links = "".join(
        f'<a href="#{a}" class="dx-subnav-link{" is-active" if i == 0 else ""}"'
        f' data-subnav-link="{a}">{label}</a>'
        for i, (a, label) in enumerate(_ANCHORS)
    )
    sections = "".join(
        f'<section class="dx-section-card dx-devsec" id="{a}" data-devsec>{label}</section>'
        for a, label in _ANCHORS
    )
    css = (REPO_ROOT / "static" / "style" / "settings.css").read_text(encoding="utf-8")
    shell = (REPO_ROOT / "static" / "style" / "shell.css").read_text(encoding="utf-8")
    script = (REPO_ROOT / "static" / "pages" / "settings.js").read_text(encoding="utf-8")
    # components.js is what measures the topbar and publishes --t-topbar-h.
    components = (REPO_ROOT / "static" / "components.js").read_text(encoding="utf-8")
    path = tmp_path / "subnav.html"
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{shell}</style><style>{css}</style>"
        # Give the sections real height so there is somewhere to scroll to.
        # A fixed topbar height so the landing point is deterministic; the
        # real bar measures 61px on one row and 100px on two.
        "<style>.topbar { height: 61px; }"
        " .dx-devsec { height: 600px; }"
        " html, body { overflow-x: hidden; overflow-x: clip; margin: 0; }</style>"
        "</head><body>"
        # The real sticky topbar: what both offsets have to clear.
        '<header class="topbar"><a href="#">Tesserae</a></header>'
        '<div class="dx-devpage-layout">'
        f'<nav class="dx-subnav" aria-label="Sections" data-subnav>{links}</nav>'
        f'<div class="dx-devpage-main">{sections}</div>'
        "</div>"
        f"<script>{components}</script><script>{script}</script>"
        "</body></html>",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def browser() -> Iterator[object]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _open(browser, page_path: Path, width: int, height: int = 700):  # type: ignore[no-untyped-def]
    pg = browser.new_page(viewport={"width": width, "height": height})
    pg.goto(page_path.as_uri())
    pg.evaluate(_SPY_JS)
    return pg


def test_desktop_click_scrolls_the_page_once(browser, page_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The regression: the click must issue exactly one scroll against the
    document. A second one (the pill) is what Firefox cancelled the first
    with, leaving the page where it started."""
    pg = _open(browser, page_path, width=1280)
    assert pg.evaluate("() => window.scrollY") == 0

    # A section in the middle of the list, so the document has room to put it
    # exactly at the top rather than bottoming out.
    pg.click('[data-subnav-link="power"]')
    # Smooth scrolling a few thousand pixels takes Chromium about a second.
    pg.wait_for_function(_SCROLL_SETTLED)

    assert pg.evaluate(_GAP_ABOVE_SECTION) == pytest.approx(16, abs=2)
    assert pg.evaluate("() => window.__sivTargets") == ["power"]
    assert pg.evaluate(
        "() => document.querySelector('[data-subnav-link=\"power\"]').classList"
        ".contains('is-active')"
    )


def test_desktop_column_nav_never_scrolls_itself(browser, page_path: Path) -> None:  # type: ignore[no-untyped-def]
    """On desktop the nav is a sticky column that never overflows, so there is
    no pill to chase and nothing to scroll."""
    pg = _open(browser, page_path, width=1280)
    assert pg.evaluate(_NAV_OVERFLOWS) is False

    pg.click('[data-subnav-link="connection"]')
    pg.wait_for_timeout(700)

    assert pg.evaluate(_NAV_SCROLL_LEFT) == 0


def test_phone_strip_centres_the_pill_in_its_own_scrollport(  # type: ignore[no-untyped-def]
    browser, page_path: Path
) -> None:
    """Under 900px the nav is a horizontal strip. The active pill still has to
    come into view -- but by scrolling the strip, not the document."""
    pg = _open(browser, page_path, width=420)
    assert pg.evaluate(_NAV_OVERFLOWS) is True
    assert pg.evaluate(_NAV_SCROLL_LEFT) == 0

    pg.click('[data-subnav-link="power"]')
    pg.wait_for_function(_NAV_SCROLL_LEFT + " > 0")
    pg.wait_for_timeout(700)

    # Still exactly one document scroll, and the strip moved on its own.
    assert pg.evaluate("() => window.__sivTargets") == ["power"]
    assert pg.evaluate(_NAV_SCROLL_LEFT) > 0
    # The pill sits inside the strip's box rather than off one of its edges.
    assert pg.evaluate(
        "() => {"
        " const nav = document.querySelector('[data-subnav]');"
        " const pill = document.querySelector('[data-subnav-link=\"power\"]');"
        " const n = nav.getBoundingClientRect(), p = pill.getBoundingClientRect();"
        " return p.left >= n.left - 1 && p.right <= n.right + 1;"
        "}"
    )


def test_a_taller_topbar_pushes_the_landing_point_down(browser, page_path: Path) -> None:  # type: ignore[no-untyped-def]
    """The topbar's nav wraps onto a second row between roughly 900px and
    1100px wide, which is where #326 was reported. A section has to clear
    whatever height the bar currently is, not a number baked in at design
    time, so the offsets track the measured value."""
    pg = _open(browser, page_path, width=1280)
    one_row = pg.evaluate("() => document.querySelector('.topbar').getBoundingClientRect().height")

    # Force the bar to twice its height, the way a wrapped nav row does.
    pg.evaluate(
        "(h) => { document.querySelector('.topbar').style.height = h * 2 + 'px'; }",
        one_row,
    )
    pg.wait_for_function(
        "(h) => getComputedStyle(document.documentElement)"
        ".getPropertyValue('--t-topbar-h').trim() === Math.round(h * 2) + 'px'",
        arg=one_row,
    )

    pg.click('[data-subnav-link="power"]')
    pg.wait_for_function(_SCROLL_SETTLED)

    assert pg.evaluate(_GAP_ABOVE_SECTION) == pytest.approx(16, abs=2)
    # And the sticky column sits below the taller bar rather than under it.
    assert pg.evaluate(
        "() => document.querySelector('[data-subnav]').getBoundingClientRect().top"
    ) >= pg.evaluate("() => document.querySelector('.topbar').getBoundingClientRect().height")
