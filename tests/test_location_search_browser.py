"""The ``location_search`` picker accepts a pasted coordinate pair (#302).

The picker only ever asked Open-Meteo's name search, which has no idea what
``-37.85, 144.94`` means, so a pasted pair showed "No matches." and could not
be saved. The shortcut is client-side (no fetch), so it needs a real browser
to prove the synthetic row appears, that picking it fills the hidden storage
and the sibling Label, and that Enter inside the debounce window works too.
"""

from __future__ import annotations

import json
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

_PAIR = "-37.85079727704507, 144.93620377089385"
_STORAGE_JS = "() => document.querySelector('[data-location-storage]').value"
_LABEL_JS = "() => document.querySelector('[name=opt_label]').value"
_ROWS_JS = (
    "() => Array.from(document.querySelectorAll('[data-idx]')).map((r) => r.textContent.trim())"
)


@pytest.fixture
def page_path(tmp_path: Path) -> Path:
    """The real macro output with the real stylesheet and script inlined, as
    a file:// page. A sibling ``opt_label`` input stands in for the cell
    editor's Label field, which the picker auto-fills on select."""
    app: Flask = create_app(testing=True, data_root=tmp_path / "data")
    with app.app_context():
        template = app.jinja_env.from_string(
            "{% from '_components.html' import location_search_field %}"
            "{{ location_search_field('lf', 'opt_location', 'Location') }}"
        )
        field = template.render()
    css = (REPO_ROOT / "static" / "style" / "forms.css").read_text(encoding="utf-8")
    script = (REPO_ROOT / "static" / "components.js").read_text(encoding="utf-8")
    path = tmp_path / "location.html"
    path.write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{css}</style></head>"
        f'<body><form id="f" onsubmit="window.__submitted = true; return false;">'
        f'{field}<input type="text" name="opt_label"></form>'
        f"<script>{script}</script></body></html>",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def page(page_path: Path) -> Iterator[object]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        # Any name-search request would be a bug for a coordinate query;
        # fail loudly instead of leaking to the network.
        pg.route("**/geocoding-api.open-meteo.com/**", lambda route: route.abort())
        pg.goto(page_path.as_uri())
        pg.evaluate(
            "() => { window.__changes = 0; window.__submitted = false;"
            " document.getElementById('f')"
            ".addEventListener('change', (ev) => {"
            " if (ev.target.hasAttribute('data-location-storage')) window.__changes += 1; }); }"
        )
        yield pg
        browser.close()


def _picked(page) -> dict[str, object]:  # type: ignore[no-untyped-def]
    raw = page.evaluate(_STORAGE_JS)
    assert raw, "nothing stored in the hidden input"
    return json.loads(raw)


def test_pasted_pair_offers_a_coordinate_row_and_saves_it(page) -> None:  # type: ignore[no-untyped-def]
    page.fill("[data-location-display]", _PAIR)
    page.wait_for_selector("[data-idx]")
    assert page.evaluate(_ROWS_JS) == ["Use coordinates -37.8508, 144.9362"]

    page.click("[data-idx]")
    loc = _picked(page)
    assert loc["latitude"] == -37.85079727704507
    assert loc["longitude"] == 144.93620377089385
    assert loc["name"] == "-37.8508, 144.9362"
    assert page.evaluate(_LABEL_JS) == "-37.8508, 144.9362"
    # One committed change on the hidden storage input (the editor's autosave
    # signal); the pill names the pick once rather than repeating the
    # coordinates underneath.
    assert page.evaluate("() => window.__changes") == 1
    pill = page.evaluate("() => document.querySelector('[data-location-pill]').textContent")
    assert pill.count("-37.8508, 144.9362") == 1


def test_enter_inside_the_debounce_picks_the_pair(page) -> None:  # type: ignore[no-untyped-def]
    page.fill("[data-location-display]", _PAIR)
    page.press("[data-location-display]", "Enter")
    loc = _picked(page)
    assert loc["latitude"] == -37.85079727704507
    assert page.evaluate("() => window.__submitted") is False


def test_a_city_name_is_not_mistaken_for_coordinates(page) -> None:  # type: ignore[no-untyped-def]
    page.fill("[data-location-display]", "Paris, FR")
    page.wait_for_timeout(500)
    assert page.evaluate(_ROWS_JS) == []
    assert page.evaluate(_STORAGE_JS) == ""
