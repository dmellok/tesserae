"""Dashboard editor v2: every bundled widget's settings panel has one shape.

A dashboard with one cell per bundled widget; each row is opened at desktop
and phone width and checked:

- the cell form's children are only the known blocks (description, a
  choices note, the main options, the collapsible sections, the footer), with
  no loose text between them (#: "Checking capture status..." once sat
  between "More Dashboard settings" and "Look of this cell");
- status text lives in the footer's status slot, nowhere else;
- the footer reads: status line, then "Empty this cell" (link, left), then the
  buttons, Done (primary) last, in DOM order and on screen;
- "More <widget> settings" counts the fields it shows;
- the options are compact: each section's switches are one group, short
  fields (numbers, short selects) pair up on the two-column grid wherever
  they can, nothing overflows the row or clips a label or a short select's
  text, and on a phone every option control is a 44px target.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page, Panel

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
pytestmark = [
    pytest.mark.skipif(not _HAS_CHROMIUM, reason="Playwright Chromium not installed"),
    pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning"),
]

PAGE_ID = "every-widget"

# Direct children a cell form may have (hidden inputs and <noscript> aside),
# in the order they must come.
ALLOWED_BLOCKS = [
    "p.ev2-wsub",
    "p.ev2-note",
    "div.ev2-opts:not(.ev2-opts--tail)",
    "details.ev2-adv",
    "div.ev2-cv-foot",
]

CHECK_JS = """
(allowed) => {
  const out = [];
  const forms = [...document.querySelectorAll('.ev2-cl[data-widget] [data-ev2-cell-form]')];
  for (const form of forms) {
    const id = form.dataset.cellId;
    const plugin = form.querySelector('[data-ev2-plugin]').value;
    EV2.widgets.openRow(id);
    const bad = (msg) => out.push(`${plugin}: ${msg}`);
    const body = form.closest('.ev2-cl-b');
    if (body.hidden) bad('row did not open');

    // 1. Only known blocks, in order, no loose text.
    let rank = 0;
    for (const n of form.childNodes) {
      if (n.nodeType === Node.TEXT_NODE) {
        if (n.textContent.trim()) bad(`loose text "${n.textContent.trim()}"`);
        continue;
      }
      if (n.nodeType !== Node.ELEMENT_NODE) continue;
      if (n.matches('input[type=hidden], noscript')) continue;
      const r = allowed.findIndex((sel) => n.matches(sel));
      if (r < 0) bad(`unexpected block <${n.tagName.toLowerCase()} class="${n.className}">`);
      else if (r < rank) bad(`${allowed[r]} comes after ${allowed[rank]}`);
      else rank = r;
    }
    const blocks = [...form.children].filter((el) => !el.matches('input[type=hidden], noscript'));
    if (!blocks.length || !blocks[blocks.length - 1].matches('.ev2-cv-foot'))
      bad('footer is not the last block');

    // 2. Status text only in the footer slot.
    const foot = form.querySelector(':scope > .ev2-cv-foot');
    const slot = foot && foot.querySelector(':scope > [data-ev2-cell-status]');
    if (!slot) bad('no status slot in the footer');
    else {
      if (slot.getAttribute('role') !== 'status') bad('status slot has no role=status');
      if (slot.getAttribute('aria-live') !== 'polite') bad('status slot is not aria-live=polite');
    }
    form.querySelectorAll('[role=status], [data-capture-status], [data-ev2-status]').forEach((el) => {
      if (el !== slot) bad(`status element outside the footer slot: ${el.outerHTML.slice(0, 80)}`);
    });

    // 3. Footer order: status, link, buttons, Done last; screen agrees.
    if (foot) {
      const kids = [...foot.children].filter((el) => !el.matches('.sp'));
      if (kids[0] !== slot) bad('status slot is not first in the footer');
      const done = foot.querySelector('[data-ev2-done]');
      const empty = foot.querySelector('[data-ev2-empty]');
      if (kids[kids.length - 1] !== done) bad('Done is not last in the footer');
      if (!empty) bad('no "Empty this cell"');
      else {
        const order = kids.indexOf(empty);
        const firstButton = kids.findIndex((el) => el.matches('button:not(.ev2-link), .ev2-mv'));
        if (order > firstButton) bad('"Empty this cell" comes after a button');
        const e = empty.getBoundingClientRect();
        const d = done.getBoundingClientRect();
        if (!(e.right <= d.left + 1 || e.bottom <= d.top + 1))
          bad('"Empty this cell" is not left of (or above) Done on screen');
      }
      if (slot && slot.textContent.trim()) {
        const s = slot.getBoundingClientRect();
        const d = done.getBoundingClientRect();
        if (s.bottom > d.top + 1) bad('status line is not above the footer actions');
      }
    }

    // 4. "More settings" is never an empty disclosure, and every group's
    //    summary has one form: how many settings are changed, or "defaults".
    form.querySelectorAll('details[data-opt-more]').forEach((more) => {
      const list = more.querySelector('[data-opt-more-list]');
      const shown = [...list.children].filter((el) => el.getClientRects().length).length;
      if (!more.hidden && shown === 0) bad('More settings shown with nothing in it');
    });
    form.querySelectorAll('details[data-ev2-adv]').forEach((group) => {
      const sum = (group.querySelector('[data-ev2-adv-sum]') || {}).textContent || '';
      if (!/^([0-9]+ changed|defaults)$/.test(sum.trim())) bad(`group summary reads "${sum}"`);
    });
  }
  return {checked: forms.length, problems: out};
}
"""


# The compact layout, measured with every group open. A "section" is one
# .ev2-opts grid (the main options, "More", "Look", "Refresh, taps and
# dithering").
LAYOUT_JS = """
(phone) => {
  const out = [];
  const visible = (el) => el.getClientRects().length > 0;
  const canvas = document.createElement('canvas').getContext('2d');
  const forms = [...document.querySelectorAll('.ev2-cl[data-widget] [data-ev2-cell-form]')];
  for (const form of forms) {
    EV2.widgets.openRow(form.dataset.cellId);
    form.querySelectorAll('details.ev2-adv').forEach((d) => { if (!d.hidden) d.open = true; });
    const plugin = form.querySelector('[data-ev2-plugin]').value;
    const bad = (msg) => out.push(`${plugin}: ${msg}`);
    const fr = form.getBoundingClientRect();

    for (const opts of form.querySelectorAll('.ev2-opts')) {
      if (!visible(opts)) continue;
      const items = [...opts.children].filter(visible);
      const name = (el) => (el.querySelector('[name]') || {}).name || el.textContent.trim().slice(0, 20);

      // 1. Every switch of the section in one group.
      const switches = [...opts.querySelectorAll('.field--switch, .ev2-swi')].filter(visible);
      const groups = new Set(switches.map((s) => s.closest('[data-ev2-swg]')));
      if (groups.has(null)) bad(`a switch outside a group: ${name(switches.find((s) => !s.closest('[data-ev2-swg]')))}`);
      if (groups.size > 1) bad(`switches split over ${groups.size} groups`);

      // 2. Number boxes and short fields are half-width, and a short field
      //    alone in its row is not followed by one that could have joined it.
      const twoCols = getComputedStyle(opts).gridTemplateColumns.split(' ').length === 2;
      items.forEach((it) => {
        const num = it.querySelector('.field > input[type=number], .field-unit-wrap input[type=number]');
        const lab = (it.querySelector('label') || {}).textContent || '';
        if (num && !it.closest('.field--slider') && !it.classList.contains('ev2-oi--short') && lab.trim().length <= 22)
          bad(`number field "${lab.trim()}" spans the row`);
      });
      if (twoCols) {
        items.forEach((it, i) => {
          if (!it.classList.contains('ev2-oi--short')) return;
          const r = it.getBoundingClientRect();
          const mate = items.some((o) => o !== it && Math.abs(o.getBoundingClientRect().top - r.top) < 2 && o.classList.contains('ev2-oi--short'));
          if (mate) return;
          const skipped = [];
          for (const next of items.slice(i + 1)) {
            if (!next.classList.contains('ev2-oi--short')) {
              skipped.push(next);
              continue;
            }
            const cond = next.querySelector('[data-show-if]');
            const needs = cond ? Object.keys(JSON.parse(cond.dataset.showIf)) : [];
            const names = skipped.flatMap((el) => [...el.querySelectorAll('[name]')].map((n) => n.name.replace(/^opt_/, '')));
            if (skipped.length <= 2 && !needs.some((n) => names.includes(n)))
              bad(`"${name(it)}" sits alone though "${name(next)}" could pair with it`);
            break;
          }
        });
      }

      for (const it of items) {
        // 3. Nothing wider than the row.
        // (The "i" button's 44px touch target may reach past its glyph; the
        // glyph is what has to fit. The overrides' storage is offscreen.)
        for (const el of [it, ...it.querySelectorAll('*')]) {
          if (!visible(el) || el.matches('.info-pop, .entity-overrides-storage')) continue;
          if (el.closest('.info-pop-content, .pic-pop, [role=listbox], .location-search-results')) continue;
          const r = el.getBoundingClientRect();
          if (r.width && (r.right > fr.right + 1 || r.left < fr.left - 1)) {
            bad(`<${el.tagName.toLowerCase()} class="${el.className}"> overflows the row (${Math.round(r.left)}-${Math.round(r.right)} vs ${Math.round(fr.left)}-${Math.round(fr.right)})`);
            break;
          }
        }
        // 4. Labels inside their field; a half-width select shows its
        //    longest choice whole.
        const ir = it.getBoundingClientRect();
        it.querySelectorAll('label, .ev2-lbl').forEach((l) => {
          if (!visible(l) || l.closest('.ti-root, .multiselect, .server-colour, .days-picker')) return;
          const text = document.createRange();
          text.selectNodeContents(l);
          const parts = [...text.getClientRects()].filter((r) => r.width);
          if (parts.some((r) => r.right > ir.right + 1 || r.left < ir.left - 1))
            bad(`label "${l.textContent.trim().split(/\\s+/).slice(0, 4).join(' ')}" is clipped`);
        });
        if (it.classList.contains('ev2-oi--short')) {
          // A widget's half-width select shows every choice whole. The
          // cell's Look selects may hold a longer override: it ends in an
          // ellipsis and the title has the whole name. Their default
          // ("Use dashboard") fits.
          const room = (el) => {
            const cs = getComputedStyle(el);
            canvas.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
            return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
          };
          it.querySelectorAll('select').forEach((sel) => {
            if (!visible(sel)) return;
            const r = room(sel);
            const own = sel.name.startsWith('opt_');
            for (const o of sel.options) {
              if (canvas.measureText(o.text).width <= r + 1) continue;
              if (own || !o.value) bad(`"${o.text}" is cut off in half-width "${name(it)}"`);
              else if (getComputedStyle(sel).textOverflow !== 'ellipsis') bad(`"${o.text}" is clipped without an ellipsis`);
            }
            const cur = sel.selectedOptions[0];
            if (cur && sel.title !== cur.text) bad(`half-width "${name(it)}" has title "${sel.title}"`);
          });
          // A half-width text box shows its example whole.
          it.querySelectorAll('input[type=text][placeholder]').forEach((inp) => {
            if (!visible(inp)) return;
            const r = room(inp);
            if (canvas.measureText(inp.placeholder).width > r + 1)
              bad(`placeholder "${inp.placeholder}" is cut off in "${name(it)}"`);
          });
        }
        // Two switches to a row only when each label stays on one line,
        // clear of its switch.
        it.querySelectorAll('.ev2-swr--pair > *').forEach((cell) => {
          if (!visible(cell)) return;
          const lab = cell.querySelector('.switch-label, .ev2-swi > label');
          const sw = cell.querySelector('.switch-track, .ev2-switch');
          const text = document.createRange();
          text.selectNodeContents(lab);
          const lines = new Set([...text.getClientRects()].filter((r) => r.width).map((r) => Math.round(r.top)));
          if (lines.size > 1) bad(`paired switch "${lab.textContent.trim()}" wraps`);
          if (text.getBoundingClientRect().right > sw.getBoundingClientRect().left) bad(`paired switch "${lab.textContent.trim()}" runs into its switch`);
        });
        // 5. Phone: 44px targets.
        if (phone) {
          it.querySelectorAll('input:not([type=hidden]), select, textarea, .info-pop, .field--switch, .ev2-swi').forEach((c) => {
            if (!visible(c) || c.closest('.ev2-switch, .server-colour, .days-picker, .multiselect, .pic-pop, .info-pop-content, .ti-root, .switch')) return;
            if (c.matches('.entity-overrides-storage, [hidden]')) return;
            const h = c.getBoundingClientRect().height;
            if (h < 43.5) bad(`${c.tagName.toLowerCase()}.${c.className} "${name(c.closest('.ev2-oi, .ev2-of') || c)}" is ${Math.round(h)}px tall on a phone`);
          });
        }
      }
    }
  }
  if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push('the page scrolls sideways');
  return {checked: forms.length, problems: out};
}
"""


@pytest.fixture(scope="module")
def pw() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Flask, list[str]]]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path_factory.mktemp("cellpanel") / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    widgets = sorted(app.config["PLUGIN_REGISTRY"].widgets(), key=lambda p: p.id)
    cols, w, h = 6, 300, 240
    cells = [
        Cell(id=f"w{i:02d}", plugin=p.id, x=(i % cols) * w, y=(i // cols) * h, w=w, h=h)
        for i, p in enumerate(widgets)
    ]
    rows = (len(cells) + cols - 1) // cols
    app.config["PAGE_STORE"].save(
        Page(id=PAGE_ID, name="Every widget", panel=Panel(w=cols * w, h=rows * h), cells=cells)
    )
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", app, [p.id for p in widgets]
    finally:
        srv.shutdown()
        thread.join(timeout=10)


def _open(pw: Any, base: str, width: int) -> tuple[Any, Any, list[str]]:
    browser = pw.chromium.launch(executable_path=_EXE) if _EXE else pw.chromium.launch()
    page = browser.new_page(viewport={"width": width, "height": 900})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/pages/{PAGE_ID}", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2 && EV2.widgets && EV2.cellStatus")
    page.evaluate("() => EV2.steps && EV2.steps.open('widgets')")
    return browser, page, errors


@pytest.mark.parametrize("width", [1440, 390])
def test_every_widget_panel_has_one_shape(pw: Any, server: Any, width: int) -> None:
    base, _app, widget_ids = server
    browser, page, errors = _open(pw, base, width)
    try:
        result = page.evaluate(CHECK_JS, ALLOWED_BLOCKS)
        assert result["checked"] == len(widget_ids)
        assert result["problems"] == []
        assert errors == []
    finally:
        browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_every_widget_panel_is_compact(pw: Any, server: Any, width: int) -> None:
    base, _app, widget_ids = server
    browser, page, errors = _open(pw, base, width)
    try:
        result = page.evaluate(LAYOUT_JS, width < 640)
        assert result["checked"] == len(widget_ids)
        assert result["problems"] == []
        assert errors == []
    finally:
        browser.close()


def test_a_long_look_override_ends_in_an_ellipsis_with_its_name_in_the_title(
    pw: Any, server: Any
) -> None:
    base, _app, _ids = server
    browser, page, errors = _open(pw, base, 390)
    try:
        result = page.evaluate(
            """() => {
              EV2.widgets.openRow('w00');
              const form = document.querySelector('[data-ev2-cell-form][data-cell-id="w00"]');
              form.querySelector('details[data-ev2-adv="look"]').open = true;
              const sel = form.querySelector('select[name="theme"]');
              const blank = sel.options[0].text;
              const long = [...sel.options].sort((a, b) => b.text.length - a.text.length)[0];
              sel.value = long.value;
              EV2.widgets.refreshSums(form);
              return {blank, text: long.text, title: sel.title,
                      overflow: getComputedStyle(sel).textOverflow,
                      half: sel.getBoundingClientRect().width < form.getBoundingClientRect().width / 2};
            }"""
        )
        assert result["blank"] == "Use dashboard"
        assert result["half"]
        assert result["overflow"] == "ellipsis"
        assert result["title"] == result["text"]
        assert errors == []
    finally:
        browser.close()


def test_capture_status_lands_in_the_footer(pw: Any, server: Any) -> None:
    """ha_dashboard's capture status is filled from the preview's cell data,
    in the footer slot, and nothing about it sits between the sections."""
    base, _app, widget_ids = server
    cell_id = f"w{widget_ids.index('ha_dashboard'):02d}"
    browser, page, errors = _open(pw, base, 1440)
    try:
        page.evaluate("(id) => EV2.widgets.openRow(id)", cell_id)
        slot = page.locator(
            f'[data-ev2-cell-form][data-cell-id="{cell_id}"] [data-ev2-cell-status]'
        )
        assert slot.get_attribute("data-ev2-status-kind") == "capture"
        # No Home Assistant here: the capture reports why it failed.
        page.wait_for_function(
            """(id) => {
              const s = document.querySelector(
                `[data-ev2-cell-form][data-cell-id="${id}"] [data-ev2-cell-status]`);
              return s && s.dataset.tone === 'error' && /Home Assistant/.test(s.textContent);
            }""",
            arg=cell_id,
            timeout=20000,
        )
        assert slot.inner_text().startswith("Capture failed:")
        form_text = page.locator(f'[data-ev2-cell-form][data-cell-id="{cell_id}"]')
        assert "Checking capture status" not in form_text.inner_text()
        assert errors == []
    finally:
        browser.close()


def test_status_api_sets_and_clears(pw: Any, server: Any) -> None:
    base, _app, _ids = server
    browser, page, errors = _open(pw, base, 1440)
    try:
        page.evaluate("() => EV2.widgets.openRow('w00')")
        slot = page.locator('[data-ev2-cell-form][data-cell-id="w00"] [data-ev2-cell-status]')
        assert slot.bounding_box()["height"] <= 1  # empty: no room taken
        page.evaluate("() => EV2.cellStatus.set('w00', 'Connection failed', 'error')")
        assert slot.inner_text() == "Connection failed"
        assert slot.get_attribute("data-tone") == "error"
        assert slot.bounding_box()["height"] > 10
        page.evaluate("() => EV2.cellStatus.clear('w00')")
        assert slot.inner_text() == ""
        assert slot.get_attribute("data-tone") is None
        assert errors == []
    finally:
        browser.close()
