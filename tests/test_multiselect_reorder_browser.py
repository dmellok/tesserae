"""Acceptance tests for prioritised multi-select reordering (live Chromium).

The reorder handles move rows with DOM insertion, and a plain ``insertBefore``
of a row that already exists blurs anything focused inside it. That failure is
invisible to a source-level assertion and to a rendered-HTML assertion: the
markup is identical either way, and the first keypress works. Only the *second*
keypress reveals it, so this has to run in a real browser.

Both the native ``moveBefore`` path and the ``insertBefore`` fallback are
covered, since browsers without ``moveBefore`` are the ones that need the
explicit focus restore.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app


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

_OPTIONS = [
    {"value": "media_player.kitchen", "label": "Kitchen"},
    {"value": "media_player.lounge", "label": "Lounge"},
    {"value": "media_player.study", "label": "Study"},
    {"value": "media_player.bedroom", "label": "Bedroom"},
]
_SELECTED = ["media_player.kitchen", "media_player.lounge", "media_player.study"]

# Read the checkbox values in DOM order, which is the order they submit in,
# marking the checked ones.
_ORDER_JS = """() => Array.from(
  document.querySelectorAll('.multiselect-opt input')
).map((i) => i.value.split('.')[1] + (i.checked ? '*' : ''))"""

_FOCUS_JS = """() => {
  const a = document.activeElement;
  if (!a) return 'none';
  return a.getAttribute('aria-label') || a.tagName + ':' + (a.value || '');
}"""


@pytest.fixture
def page_path(tmp_path: Path) -> Path:
    """The real macro output, with the real stylesheet and script inlined, as
    a file:// page. No server needed: the component is self-contained."""
    app: Flask = create_app(testing=True, data_root=tmp_path / "data")
    with app.app_context():
        template = app.jinja_env.from_string(
            "{% from '_components.html' import multiselect_field %}"
            "{{ multiselect_field('mf', 'entities', 'Players',"
            " value=value, options=options) }}"
        )
        field = template.render(value=_SELECTED, options=_OPTIONS)
    css = (REPO_ROOT / "static" / "style" / "forms.css").read_text(encoding="utf-8")
    script = (REPO_ROOT / "static" / "components.js").read_text(encoding="utf-8")
    path = tmp_path / "multiselect.html"
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{css}</style></head>"
        f'<body><form id="f">{field}</form><script>{script}</script></body></html>',
        encoding="utf-8",
    )
    return path


@pytest.fixture(params=["native", "fallback"])
def page(page_path: Path, request: pytest.FixtureRequest) -> Iterator[object]:
    """``fallback`` removes ``Element.prototype.moveBefore`` before the
    component binds, exercising the insertBefore-plus-refocus path that older
    browsers take."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        if request.param == "fallback":
            pg.add_init_script("delete Element.prototype.moveBefore;")
        pg.goto(page_path.as_uri())
        pg.evaluate(
            "() => { window.__inputs = 0;"
            " document.getElementById('f')"
            ".addEventListener('input', () => { window.__inputs += 1; }); }"
        )
        yield pg
        browser.close()


def test_arrow_keys_step_a_row_more_than_once(page) -> None:  # type: ignore[no-untyped-def]
    """The regression: the handle must stay focused across the move, or the
    second keypress lands on the page and the row never moves again."""
    page.evaluate("() => document.querySelectorAll('[data-ms-drag]')[0].focus()")
    assert page.evaluate(_ORDER_JS) == ["kitchen*", "lounge*", "study*", "bedroom"]

    page.keyboard.press("ArrowDown")
    assert page.evaluate(_ORDER_JS) == ["lounge*", "kitchen*", "study*", "bedroom"]
    assert page.evaluate(_FOCUS_JS) == "Reorder Kitchen"

    page.keyboard.press("ArrowDown")
    assert page.evaluate(_ORDER_JS) == ["lounge*", "study*", "kitchen*", "bedroom"]
    assert page.evaluate(_FOCUS_JS) == "Reorder Kitchen"

    page.keyboard.press("Home")
    assert page.evaluate(_ORDER_JS) == ["kitchen*", "lounge*", "study*", "bedroom"]

    page.keyboard.press("End")
    assert page.evaluate(_ORDER_JS) == ["lounge*", "study*", "kitchen*", "bedroom"]
    assert page.evaluate(_FOCUS_JS) == "Reorder Kitchen"

    # One commit per accepted move, so the editor marks dirty and re-previews.
    assert page.evaluate("() => window.__inputs") == 4
    assert (
        page.evaluate("() => document.querySelector('[data-ms-order-status]').textContent")
        == "Kitchen, priority 3 of 3"
    )


def test_unchecked_rows_are_not_reorderable(page) -> None:  # type: ignore[no-untyped-def]
    """Only selected rows carry priority, so an unchecked row has no handle to
    focus and the checked block is unaffected."""
    handles = page.evaluate(
        "() => Array.from(document.querySelectorAll('.multiselect-opt')).map("
        " (row) => getComputedStyle(row.querySelector('[data-ms-drag]')).display)"
    )
    assert handles == ["grid", "grid", "grid", "none"]


def test_toggling_a_row_keeps_keyboard_focus_on_it(page) -> None:  # type: ignore[no-untyped-def]
    """Ticking re-partitions the list, which moves the row the user is standing
    on. Focus has to travel with it or keyboard users lose their place."""
    page.evaluate("() => document.querySelectorAll('.multiselect-opt input')[3].focus()")

    page.keyboard.press("Space")
    assert page.evaluate(_ORDER_JS) == ["kitchen*", "lounge*", "study*", "bedroom*"]
    assert page.evaluate(_FOCUS_JS) == "INPUT:media_player.bedroom"

    page.keyboard.press("Space")
    assert page.evaluate(_ORDER_JS) == ["kitchen*", "lounge*", "study*", "bedroom"]
    assert page.evaluate(_FOCUS_JS) == "INPUT:media_player.bedroom"
