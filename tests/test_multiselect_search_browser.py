"""A long multi-select is compact: its picks under a search box, the whole
list only while the box is in use, Enter ticking the best match (live
Chromium, real macro, stylesheet and script)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from tests.test_multiselect_reorder_browser import pytestmark  # noqa: F401  (skip without Chromium)

_OPTIONS = [
    {"value": f"t{i}", "label": name}
    for i, name in enumerate(
        [
            "Events",
            "Halloween 2010",
            "People",
            "People/Alex",
            "People/Julie",
            "Places",
            "Places/Chicago",
            "Places/Egypt",
            "julie",
            "Photo Booth",
        ]
    )
]


def _page_html(tmp_path: Path, options: list[dict[str, str]], value: list[str]) -> Path:
    app: Flask = create_app(testing=True, data_root=tmp_path / "data")
    with app.app_context():
        field = app.jinja_env.from_string(
            "{% from '_components.html' import multiselect_field %}"
            "{{ multiselect_field('mf', 'tag_id', 'Tags', value=value, options=options) }}"
        ).render(value=value, options=options)
    css = (REPO_ROOT / "static" / "style" / "forms.css").read_text(encoding="utf-8")
    script = (REPO_ROOT / "static" / "components.js").read_text(encoding="utf-8")
    path = tmp_path / "multiselect.html"
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{css}</style></head><body><button id='out'>out</button>"
        f'<form id="f">{field}</form><script>{script}</script></body></html>',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def page(tmp_path: Path) -> Iterator[object]:
    from playwright.sync_api import sync_playwright

    path = _page_html(tmp_path, _OPTIONS, ["t2"])
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        )
        pg = browser.new_page()
        pg.goto(path.as_uri())
        pg.evaluate(
            "() => { window.__changes = 0; document.getElementById('f')"
            ".addEventListener('change', () => { window.__changes += 1; }); }"
        )
        yield pg
        browser.close()


_SHOWN = """() => Array.from(document.querySelectorAll('.multiselect-opt'))
  .filter((r) => r.offsetParent !== null).map((r) => r.innerText.trim())"""
_PICKED = """() => Array.from(document.querySelectorAll('.multiselect-opt input:checked'))
  .map((i) => i.value)"""


def test_closed_it_shows_only_the_picks(page) -> None:  # type: ignore[no-untyped-def]
    assert page.evaluate(_SHOWN) == ["People"]
    assert page.get_attribute("[data-ms-filter]", "placeholder") == "Search tags…"
    assert page.get_attribute("[data-ms-filter]", "aria-expanded") == "false"


def test_typing_filters_and_enter_ticks_the_first_match(page) -> None:  # type: ignore[no-untyped-def]
    page.click("[data-ms-filter]")
    assert len(page.evaluate(_SHOWN)) == len(_OPTIONS)
    page.keyboard.type("jul")
    assert page.evaluate(_SHOWN) == ["People/Julie", "julie"]
    page.keyboard.press("Enter")
    # Ticked, box cleared for the next one, and the form never submitted.
    assert page.evaluate(_PICKED) == ["t2", "t4"]
    assert page.input_value("[data-ms-filter]") == ""
    assert page.evaluate("() => window.__changes") == 1
    # An exact match wins over an earlier partial one.
    page.keyboard.type("julie")
    page.keyboard.press("Enter")
    assert page.evaluate(_PICKED) == ["t2", "t4", "t8"]


def test_arrow_keys_walk_the_results_and_focus_leaving_closes(page) -> None:  # type: ignore[no-untyped-def]
    page.click("[data-ms-filter]")
    page.keyboard.type("places")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Space")
    assert page.evaluate(_PICKED) == ["t2", "t6"]
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("ArrowUp")
    assert page.evaluate("() => document.activeElement.matches('[data-ms-filter]')")
    page.click("#out")
    page.wait_for_function(
        "() => !document.querySelector('.multiselect').classList.contains('is-open')"
    )
    assert page.evaluate(_SHOWN) == ["People", "Places/Chicago"]
    assert page.input_value("[data-ms-filter]") == ""


def test_one_pick_has_no_reorder_handle(page) -> None:  # type: ignore[no-untyped-def]
    assert not page.locator("[data-ms-drag]").first.is_visible()


def test_a_short_list_stays_a_plain_checklist(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    path = _page_html(tmp_path, _OPTIONS[:4], ["t2"])
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        )
        pg = browser.new_page()
        pg.goto(path.as_uri())
        assert len(pg.evaluate(_SHOWN)) == 4
        assert pg.locator("[data-ms-toggle]").count() == 0
        browser.close()
