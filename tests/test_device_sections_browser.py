"""The device page's collapsible sections in a live Chromium: every section
starts expanded, the chevron or the header collapses it, the choice survives a
reload (localStorage, per section id), and a jump to a section, a sub-group
(#lineups, the old anchors) or a headline tile opens it first. Runs against
the real page, rendered by the real app, in both UI designs and both themes."""

from __future__ import annotations

import json
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

DEV = "kitchen"
KEY = "tesserae-devsec-collapsed:"


@pytest.fixture
def base_url(tmp_path: Path) -> Iterator[str]:
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
    client = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = client.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "device_id": DEV,
                "kind": "pico_bin_client",
                "panel_w": 800,
                "panel_h": 480,
                "fw_version": "1.41.0",
            }
        ),
    )
    assert resp.status_code == 201
    token = resp.get_json()["device_token"]
    client.post(
        f"/api/v1/device/{DEV}/status",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"battery_pct": 76, "logs": {"schema": 1, "ring_bytes": 3072}}),
    )
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/settings/devices/{DEV}"
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


def _open(browser: Any, url: str, store: dict[str, str] | None = None) -> Any:
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    if store:
        # Seed localStorage before the page script runs.
        ctx.add_init_script(
            "(s => { for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); })("
            + json.dumps(store)
            + ")"
        )
    pg = ctx.new_page()
    pg.goto(url)
    return pg


def _collapsed(pg: Any, sec: str) -> bool:
    return pg.evaluate(
        "id => document.getElementById(id + '-body').hidden && "
        "document.getElementById(id).classList.contains('is-collapsed') && "
        "document.querySelector('#' + id + ' [data-devsec-toggle]')"
        ".getAttribute('aria-expanded') === 'false'",
        sec,
    )


def _expanded(pg: Any, sec: str) -> bool:
    return pg.evaluate(
        "id => !document.getElementById(id + '-body').hidden && "
        "!document.getElementById(id).classList.contains('is-collapsed') && "
        "document.querySelector('#' + id + ' [data-devsec-toggle]')"
        ".getAttribute('aria-expanded') === 'true'",
        sec,
    )


SECTIONS = ["overview", "display", "schedule", "controls", "power", "troubleshooting"]


def test_every_section_starts_expanded(browser: Any, base_url: str) -> None:
    pg = _open(browser, base_url)
    for sec in SECTIONS:
        assert _expanded(pg, sec), sec
    assert pg.locator("#display-body").is_visible()


def test_chevron_and_header_collapse_and_the_choice_is_remembered(
    browser: Any, base_url: str
) -> None:
    pg = _open(browser, base_url)
    pg.click("#schedule [data-devsec-toggle]")
    assert _collapsed(pg, "schedule")
    assert not pg.locator("#schedule-body").is_visible()
    assert pg.evaluate("k => localStorage.getItem(k)", KEY + "schedule") == "1"
    # The other sections are untouched.
    assert _expanded(pg, "display")
    assert pg.evaluate("k => localStorage.getItem(k)", KEY + "display") is None
    # Screen readers hear what the button will do.
    assert pg.locator("#schedule [data-devsec-toggle] .sr-only").inner_text() == "Expand Schedule"

    pg.reload()
    assert _collapsed(pg, "schedule")
    assert _expanded(pg, "display")

    # Clicking the header's title area opens it again.
    pg.click("#schedule .dx-section-title")
    assert _expanded(pg, "schedule")
    assert pg.evaluate("k => localStorage.getItem(k)", KEY + "schedule") is None
    pg.reload()
    assert _expanded(pg, "schedule")


def test_the_toggle_works_from_the_keyboard(browser: Any, base_url: str) -> None:
    pg = _open(browser, base_url)
    pg.focus("#power [data-devsec-toggle]")
    pg.keyboard.press("Enter")
    assert _collapsed(pg, "power")
    pg.keyboard.press("Space")
    assert _expanded(pg, "power")


def test_a_link_inside_a_header_does_not_toggle(browser: Any, base_url: str) -> None:
    pg = _open(browser, base_url)
    # The Lineups page button sits in a sub-group head, not a section head,
    # but the rule is the same: only bare header space toggles.
    pg.evaluate(
        "() => { const h = document.querySelector('#overview [data-devsec-head]');"
        " const a = document.createElement('a'); a.href = '#x'; a.id = 'probe'; a.textContent = 'x';"
        " h.appendChild(a); }"
    )
    pg.click("#probe")
    assert _expanded(pg, "overview")


def test_subnav_click_expands_a_collapsed_section(browser: Any, base_url: str) -> None:
    pg = _open(browser, base_url, {KEY + "troubleshooting": "1"})
    assert _collapsed(pg, "troubleshooting")
    pg.click('[data-subnav-link="troubleshooting"]')
    assert _expanded(pg, "troubleshooting")
    assert pg.locator("#connection").is_visible()


@pytest.mark.parametrize(
    ("anchor", "section"),
    [
        ("lineups", "schedule"),
        ("timing", "schedule"),
        ("rendering", "display"),
        ("identity", "display"),
        ("connection", "troubleshooting"),
        ("logs", "troubleshooting"),
        ("power", "power"),
        ("schedule", "schedule"),
    ],
)
def test_a_hash_link_opens_the_section_that_holds_it(
    browser: Any, base_url: str, anchor: str, section: str
) -> None:
    pg = _open(browser, base_url, {KEY + s: "1" for s in SECTIONS})
    pg.goto(f"{base_url}#{anchor}")
    pg.wait_for_function("id => !document.getElementById(id + '-body').hidden", arg=section)
    assert _expanded(pg, section)
    # And it was scrolled into view (a short page bottoms out before the
    # target reaches the top), not left where the collapsed layout put it.
    # The jump is a smooth scroll: wait for it to land rather than reading
    # the position mid-flight.
    pg.wait_for_function(
        "id => { const t = document.getElementById(id).getBoundingClientRect().top;"
        " return t >= 0 && t < 880; }",
        arg=anchor,
        timeout=5000,
    )
    # Sections the link has nothing to do with stay collapsed.
    others = [s for s in SECTIONS if s != section]
    assert all(_collapsed(pg, s) for s in others)


def test_hashchange_in_the_page_opens_a_collapsed_section(browser: Any, base_url: str) -> None:
    pg = _open(browser, base_url)
    pg.click("#schedule [data-devsec-toggle]")
    pg.evaluate("() => { location.hash = '#lineups'; }")
    pg.wait_for_function("() => !document.getElementById('schedule-body').hidden")
    assert _expanded(pg, "schedule")


def test_headline_tiles_jump_to_their_sections(browser: Any, base_url: str) -> None:
    pg = _open(browser, base_url, {KEY + s: "1" for s in SECTIONS if s != "overview"})
    tiles = {
        "Showing": ("#lineups", "schedule"),
        "Battery": ("#power", "power"),
        "Firmware": ("#connection", "troubleshooting"),
        "Next update": ("#timing", "schedule"),
    }
    for label, (href, section) in tiles.items():
        tile = pg.locator(f'#overview a.dx-qtile--link:has(.dx-qtile-label:text-is("{label}"))')
        assert tile.get_attribute("href") == href, label
        tile.click()
        pg.wait_for_function("id => !document.getElementById(id + '-body').hidden", arg=section)
        assert _expanded(pg, section), label
        # Same tile again, section collapsed by hand: the hash is already
        # current, so only the click handler can reopen it.
        pg.click(f"#{section} [data-devsec-toggle]")
        assert _collapsed(pg, section)
        pg.evaluate("() => window.scrollTo(0, 0)")
        tile.click()
        pg.wait_for_function("id => !document.getElementById(id + '-body').hidden", arg=section)
        assert _expanded(pg, section), label
        pg.click(f"#{section} [data-devsec-toggle]")


def test_saving_with_the_name_section_collapsed_opens_it(browser: Any, base_url: str) -> None:
    """The display name is required; a collapsed section would otherwise block
    Save with an error on a field nobody can see."""
    pg = _open(browser, base_url, {KEY + "display": "1"})
    assert _collapsed(pg, "display")
    pg.evaluate(
        "() => { const i = document.querySelector('input[name=\"device_name\"]'); i.value = '';"
        " i.form.requestSubmit(); }"
    )
    pg.wait_for_function("() => !document.getElementById('display-body').hidden")
    assert _expanded(pg, "display")
    assert pg.url.endswith(f"/settings/devices/{DEV}")  # nothing was submitted


@pytest.mark.parametrize("ui", ["classic", "paper"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chevron_is_drawn_in_both_designs_and_themes(
    browser: Any, base_url: str, ui: str, theme: str
) -> None:
    pg = _open(browser, base_url)
    pg.evaluate(
        "([u, t]) => { document.documentElement.dataset.ui = u;"
        " document.documentElement.dataset.theme = t; }",
        [ui, theme],
    )
    for sec in SECTIONS:
        box = pg.locator(f"#{sec} [data-devsec-toggle]").bounding_box()
        assert box and box["width"] >= 24 and box["height"] >= 24, sec
        icon = pg.locator(f"#{sec} [data-devsec-toggle] .ph-caret-down")
        assert icon.is_visible()
    # Collapsed, the header stays and the rotated chevron points right.
    pg.click("#display [data-devsec-toggle]")
    assert (
        pg.evaluate(
            "() => getComputedStyle(document.querySelector('#display [data-devsec-toggle] .ph')).transform"
        )
        != "none"
    )
    assert pg.locator("#display .dx-section-title").is_visible()
