"""Action order, checked in a live Chromium across the admin UI.

Convention (docs/dev/ui-conventions.md): wherever a link-styled control and a
button-styled control sit together, the button is on the right of the link,
and the primary action is rightmost. Link-styled means no border and no
background (anchors, ``.ghost``, quiet and text buttons); button-styled means
a border or a fill. The DOM order has to match the visual order, so a row
that is turned around with ``row-reverse`` or ``order`` fails too.

One live server serves every page. Each page is opened in Classic and Paper,
light and dark, at desktop and phone width; every cluster of adjacent
controls is measured from rendered geometry, not from the markup.
"""

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
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Page

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

# Every route here renders in the seeded app below.
PAGES = [
    "/decks",
    "/decks/mine/edit",
    "/decks/new",
    "/pages",
    "/pages/new",
    "/pages/clock",
    "/schedules",
    "/rotations",
    "/history",
    "/events",
    "/send",
    "/stats/",
    "/themes",
    "/themes/new",
    "/schedules/new",
    "/plugins/",
    "/plugins/gtfs/",
    "/plugins/calendar_core/",
    "/plugins/todo/",
    "/_test/preview?widget=calendar_day",
    "/plugins/browse",
    "/plugins/templates/",
    "/devices/battery",
    "/settings/server",
    "/settings/system",
    "/settings/about",
    "/settings/devices",
    "/settings/devices/hallway",
    "/settings/devices/hallway/calibration",
    "/settings/plugins",
    "/settings/renderers",
    "/settings/firmware",
    "/settings/genai",
    "/settings/relay",
    "/settings/rooms",
    "/settings/companion",
    "/onboarding",
]

# Measured in the page. A control is "link" when it has neither a border nor a
# fill, "button" when it has either. A cluster is controls that sit next to each
# other (along a row, or stacked on a phone) inside one small container.
_SWEEP_JS = r"""
() => {
  // Not action rows: navigation, tab and segmented strips (one of several
  // states, not an action), menus of items, and the toast region.
  const SKIP_ANC = 'nav, [role=tablist], [role=menu], [role=listbox], .topbar, .sidebar, .tabs, '
    + '.seg, .lu-seg, .dx-segmented, .cat-seg, .preview-modes, .info-pop-wrap, .flash, '
    + '[class*="-more-menu"], .cat-sort-menu';
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return false;
      if (e.hasAttribute('hidden') || e.inert) return false;
    }
    return true;
  };
  const alpha = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return c === 'transparent' ? 0 : 1;
    const p = m[1].split(/[ ,\/]+/).filter(Boolean);
    return p.length > 3 ? parseFloat(p[3]) : 1;
  };
  const kindOf = (el) => {
    const cs = getComputedStyle(el);
    const edge = ['Top', 'Right', 'Bottom', 'Left'].some(
      (s) => parseFloat(cs['border' + s + 'Width']) > 0 && cs['border' + s + 'Style'] !== 'none'
        && alpha(cs['border' + s + 'Color']) > 0.05);
    const fill = alpha(cs.backgroundColor) > 0.05 || cs.backgroundImage !== 'none';
    return edge || fill ? 'button' : 'link';
  };
  const label = (el) => (el.getAttribute('aria-label') || el.textContent || el.title || el.className || el.tagName)
    .replace(/\s+/g, ' ').trim().slice(0, 28);
  const els = [...document.querySelectorAll(
    'a[href], button, input[type=submit], input[type=button], [role=button]')]
    .filter((el) => !el.closest(SKIP_ANC)
      && !el.matches('.info-pop, [aria-pressed], [aria-selected], [role=switch], [role=tab], [role=menuitem]')
      // headline summary tiles that link to a settings section (information, not actions)
      && !el.matches('a.dx-qtile--link')
      // a thumbnail or QR code that is a link, and corner close / dismiss icons
      && !(el.tagName === 'A' && el.querySelector('img') && !el.textContent.trim())
      && !/^(close|dismiss|hide)\b/i.test(el.getAttribute('aria-label') || '')
      && el.getAttribute('type') !== 'hidden' && vis(el))
    .filter((el, _, all) => !all.some((o) => o !== el && o.contains(el)));
  const info = els.map((el, i) => {
    const r = el.getBoundingClientRect();
    return { el, i, r, kind: kindOf(el), label: label(el),
      primary: /primary|accent|-save\b/.test(el.className) };
  });
  const depthOf = (a, b) => {
    const anc = new Set();
    for (let e = a, d = 0; e; e = e.parentElement, d++) anc.add(e);
    let d2 = 0;
    for (let e = b; e; e = e.parentElement, d2++) if (anc.has(e)) {
      let d1 = 0;
      for (let x = a; x !== e; x = x.parentElement) d1++;
      return { lca: e, up: Math.max(d1, d2) };
    }
    return null;
  };
  const near = (A, B) => {
    const a = A.r, b = B.r;
    const vo = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    const ho = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const rowGap = Math.max(a.left, b.left) - Math.min(a.right, b.right);
    const colGap = Math.max(a.top, b.top) - Math.min(a.bottom, b.bottom);
    const row = vo >= 0.5 * Math.min(a.height, b.height) && rowGap <= 40;
    const col = ho >= 0.5 * Math.min(a.width, b.width) && colGap <= 16 && vo < 0;
    if (!row && !col) return null;
    const d = depthOf(A.el, B.el);
    if (!d || d.up > 4 || d.lca === document.body) return null;
    const lr = d.lca.getBoundingClientRect();
    if (lr.height > 260) return null;
    return row ? 'row' : 'col';
  };
  const parent = info.map((_, i) => i);
  const find = (x) => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  for (let i = 0; i < info.length; i++) for (let j = i + 1; j < info.length; j++) {
    const n = near(info[i], info[j]);
    if (n) parent[find(i)] = find(j);
  }
  const groups = {};
  info.forEach((c, i) => { (groups[find(i)] ||= []).push(c); });
  const out = [];
  for (const g of Object.values(groups)) {
    if (g.length < 2) continue;
    // reading order: line by line (a wrapped row is several lines), left to right
    const byLine = (a, b) => {
      const dy = (a.r.top + a.r.bottom) / 2 - (b.r.top + b.r.bottom) / 2;
      return Math.abs(dy) > 0.5 * Math.min(a.r.height, b.r.height) ? dy : a.r.left - b.r.left;
    };
    const seen = [...g].sort(byLine);
    const kinds = new Set(seen.map((c) => c.kind));
    const problems = [];
    // 1. a link-styled control after a button-styled one
    let sawButton = false;
    for (const c of seen) {
      if (c.kind === 'button') sawButton = true;
      else if (sawButton) { problems.push('link "' + c.label + '" is after a button'); break; }
    }
    // 2. a non-primary button after the primary one
    let sawPrimary = false;
    for (const c of seen) {
      if (c.primary) sawPrimary = true;
      else if (sawPrimary && c.kind === 'button') { problems.push('button "' + c.label + '" is after the primary'); break; }
    }
    // 3. the DOM order differs from the visual order (row-reverse, order)
    const dom = [...g].sort((a, b) => (a.el.compareDocumentPosition(b.el) & 4 ? -1 : 1));
    if (dom.some((c, k) => c !== seen[k]) && (kinds.size > 1 || seen.some((c) => c.primary))) {
      problems.push('DOM order differs from visual order');
    }
    if (problems.length) out.push({
      problems,
      order: seen.map((c) => c.kind[0].toUpperCase() + ':' + c.label),
      dom: dom.map((c) => c.label),
    });
  }
  return out;
}
"""

# Opens every <details>, un-hides what the page hides until a state calls for it
# (confirm bars, wizard steps, the dialogs), so the markup behind those states
# is measured too. A few of the hidden controls are mutually exclusive with
# their neighbours (the wizard's "Set up another" shows only on the last step,
# where Back is hidden), so those stay hidden.
_REVEAL_JS = """
() => {
  document.querySelectorAll('details').forEach((d) => { d.open = true; });
  document.querySelectorAll('[hidden]').forEach((e) => {
    if (e.matches('script, template, style, input[type=hidden], [data-wizard-again]')) return;
    e.hidden = false;
  });
  document.querySelectorAll('dialog').forEach((d) => { if (!d.open) d.show(); });
}
"""


def _build_app(tmp_path: Path) -> Flask:
    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})
    c = app.test_client()
    for dev in ("hallway", "frame"):
        code = app.config["PAIRING_STORE"].issue(note="t").code
        resp = c.post(
            "/api/v1/device/register",
            headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
            data=json.dumps({"device_id": dev, "kind": "esp32_client"}),
        )
        assert resp.status_code == 201
    pages = app.config["PAGE_STORE"]
    pages.save(Page(id="photos", name="Google Photos", device_ids=["frame"]))
    pages.save(Page(id="clock", name="Clock", device_ids=["hallway"]))
    app.config["DECK_STORE"].upsert(
        Deck(id="mine", name="Mine", device_ids=["hallway"], pages=[DeckPage(page_id="clock")])
    )
    c.post("/plugins/disable", data={"plugin_id": "gtfs"})
    c.post(
        "/plugins/calendar_core/feeds",
        data={"name": "Home", "url": "https://example.invalid/home.ics", "colour": "#336699"},
    )
    c.post("/plugins/todo/lists", data={"name": "Chores"})
    app.config["DISCOVERY_CACHE"].record("desk_frame", b'{"kind":"esp32_client"}')
    c.post(
        "/schedules/new",
        data={
            "id": "morning",
            "name": "Morning",
            "page_id": "clock",
            "type": "interval",
            "interval_minutes": "15",
            "priority": "0",
            "enabled": "on",
        },
    )
    log = app.config["EVENT_LOG"]
    for status in ("sent", "failed", "sent"):
        log.record(
            type="push",
            source="page",
            target="clock",
            status=status,
            digest="d",
            error="boom" if status == "failed" else None,
        )
    return app


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app = _build_app(tmp_path_factory.mktemp("action-order"))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


# (design, colour scheme, viewport width)
VARIANTS = [
    ("paper", "light", 1280),
    ("classic", "light", 1280),
    ("paper", "dark", 390),
    ("classic", "light", 390),
]


def _clusters(page: Any, path: str, reveal: bool) -> list[str]:
    found = [
        f"{path} {'(all states shown) ' if reveal else ''}{'; '.join(item['problems'])}: "
        f"{' | '.join(item['order'])}"
        for item in page.evaluate(_SWEEP_JS)
    ]
    return found


@pytest.mark.parametrize(("design", "scheme", "width"), VARIANTS)
def test_every_page_puts_links_left_of_buttons(
    live: tuple[str, Flask], design: str, scheme: str, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    base, app = live
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": design})
    found: list[str] = []
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            ctx = browser.new_context(
                viewport={"width": width, "height": 900},
                color_scheme=scheme,
            )
            page = ctx.new_page()
            for path in PAGES:
                page.goto(f"{base}{path}", wait_until="load")
                page.wait_for_timeout(150)
                found += _clusters(page, path, reveal=False)
                page.evaluate(_REVEAL_JS)
                found += _clusters(page, path, reveal=True)
            ctx.close()
        finally:
            browser.close()
    assert not found, f"{design}/{scheme}/{width}px, link/button order broken:\n" + "\n".join(
        sorted(set(found))
    )


@pytest.mark.parametrize("width", [1280, 390])
def test_bind_prompt_buttons_read_the_same_way_they_tab(
    live: tuple[str, Flask], width: int
) -> None:
    """Cancel first, the primary last, on a phone too (the dialog used to move
    Cancel to the bottom with CSS `order`, which left Tab going the other way)."""
    from playwright.sync_api import sync_playwright

    base, app = live
    app.config["SETTINGS_STORE"].patch_section("app", {"ui_design": "paper"})
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
            page.goto(f"{base}/decks/mine/edit", wait_until="load")
            page.locator("#dxe-avail .dxe-chip--other", has_text="Google Photos").click()
            foot = page.locator("dialog.bind-dialog .wizard-footer button")
            foot.first.wait_for()
            labels = [b.inner_text().strip() for b in foot.all()]
            assert labels[0] == "Cancel" and labels[-1].startswith("Bind to ") and len(labels) == 3
            page.wait_for_timeout(500)  # the dialog eases in; measure it at rest
            boxes = page.evaluate(
                """() => [...document.querySelectorAll('dialog.bind-dialog .wizard-footer button')]
                    .map((b) => { const r = b.getBoundingClientRect(); return [r.left, r.top]; })"""
            )
            axis = 1 if width < 480 else 0  # stacked on a phone, in a row otherwise
            along = [b[axis] for b in boxes]
            assert along == sorted(along), boxes
            assert page.evaluate(_SWEEP_JS) == []
        finally:
            browser.close()


def _catalog_app(tmp_path: Path) -> Flask:
    from unittest.mock import MagicMock

    from app.marketplace import CatalogEntry, InstalledRecord, Marketplace
    from app.updater import Updater

    app = _build_app(tmp_path)
    entry = CatalogEntry(
        id="sample",
        name="Sample",
        description="A sample widget.",
        icon=None,
        author_name="t",
        author_github=None,
        tags=[],
        kind="widget",
        tesserae_compat="0.x",
        official=False,
        screenshot_sizes=[],
        extra_screenshot_count=0,
        folders=None,
        release_version="2.0.0",
        release_tarball_url="https://example.invalid/sample.tar.gz",
        release_sha256="0" * 64,
        source="https://example.invalid/sample",
    )
    mkt = MagicMock(spec=Marketplace)
    mkt.index_url.return_value = "https://example.invalid/widgets.json"
    mkt.screenshots_base.return_value = "https://example.invalid/screenshots/"
    mkt.plugins_dir.return_value = tmp_path / "plugins"
    mkt.installed.return_value = {
        "sample": InstalledRecord(
            catalog_id="sample",
            folders=["sample"],
            version="1.0.0",
            sha256="0" * 64,
            source=None,
            installed_at="2026-01-01T00:00:00Z",
        )
    }
    mkt.fetch_index.return_value = [entry]
    app.config["MARKETPLACE"] = mkt
    app.config["UPDATER"] = MagicMock(spec=Updater)
    return app


def test_catalog_sheet_ends_with_its_primary_action(tmp_path: Path) -> None:
    """An installed widget with an update: Source, Uninstall, then Update."""
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server

    app = _catalog_app(tmp_path)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = _launch(p)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                page.goto(
                    f"http://127.0.0.1:{server.server_port}/plugins/browse", wait_until="load"
                )
                page.locator(".cat-row").first.click()
                acts = page.locator(".cat-sheet-actions > *")
                acts.first.wait_for()
                labels = [a.inner_text().strip() for a in acts.all()]
                assert labels == ["Source", "Uninstall", "Update to v2.0.0"], labels
                assert page.evaluate(_SWEEP_JS) == []
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=10)


def test_agent_follow_banners_list_the_way_out_before_the_action() -> None:
    """The two banners that offer to open the editor (the Send page's and the
    editor's own) build their buttons from strings; Stay here / Dismiss come
    first and the accented action is last."""
    root = Path(__file__).resolve().parents[1] / "static"
    follow = (root / "agent-follow.js").read_text(encoding="utf-8")
    assert follow.index("data-stay") < follow.index("af-btn accent")
    assert follow.index("data-dismiss>Dismiss") < follow.index("af-btn accent")
    editor = (root / "panels" / "editor.js").read_text(encoding="utf-8")
    assert editor.index('class="ag-move-b" data-stay') < editor.index("ag-move-b accent")
