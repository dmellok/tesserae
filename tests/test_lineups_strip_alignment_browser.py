"""A NEXT / QUEUED pill on one tile must not push that tile down: the pictures of a
lineup's strip sit level in one row, whether or not the strip is the live one."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
_CSS = (Path(__file__).resolve().parents[1] / "static/style/dx-lineups.css").read_text()

_STEP = (
    '<li class="lu-step"><figure><span class="lu-slot">{pill}</span>'
    '<span class="lu-tile" style="display:block;width:80px;height:120px;background:#444"></span>'
    "</figure></li>"
)


def _tops(page, strip_class: str, pills: list[str]) -> list[float]:
    page.set_content(
        f"<style>{_CSS}</style><div class='lu'><ol class='lu-strip {strip_class}'>"
        + "".join(_STEP.format(pill=p) for p in pills)
        + "</ol></div>"
    )
    return page.evaluate(
        "[...document.querySelectorAll('.lu-tile')].map(e => e.getBoundingClientRect().top)"
    )


@pytest.fixture
def page():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(executable_path=_EXE) if _EXE else p.chromium.launch()
        except Exception as err:  # no browser here
            pytest.skip(f"no Chromium: {err}")
        yield browser.new_page()
        browser.close()


@pytest.mark.parametrize("strip_class", ["l", "l has-now"])
def test_a_pill_on_one_tile_keeps_the_row_level(page, strip_class: str) -> None:
    pill = '<span class="lu-pill p-next">NEXT 10 AM</span>'
    tops = _tops(page, strip_class, ["", pill, ""])
    assert max(tops) - min(tops) < 0.5, tops


def test_a_strip_without_a_pill_adds_no_empty_row(page) -> None:
    with_pill = _tops(page, "l", ["", '<span class="lu-pill p-next">NEXT</span>', ""])
    without = _tops(page, "l", ["", "", ""])
    assert without[0] < with_pill[0], (without, with_pill)
