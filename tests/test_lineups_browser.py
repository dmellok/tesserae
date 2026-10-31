"""The Lineups page in a live Chromium, drawn from the view-model's mockup
sample: folding (remembered across a reload), the day picker, day tiles and
bar drags as unsaved edits with Save/Undo, Push now, the items popover, and
no page-level sideways scroll from a phone to a 2736px screen. The JSON
endpoints are answered by the browser (page.route), so this tests the page,
not app/deck_routes.py."""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, render_template, request

from app.lineups_view import sample_view_model
from app.main import create_app

# PLAYWRIGHT_CHROMIUM_EXECUTABLE points at another Chromium (a headless shell
# from a different Playwright release) when the bundled one is missing.
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

PAGE = "/lineups-sample"


def _sample_vm(cycle: str = "h23") -> dict[str, Any]:
    vm = sample_view_model(time_cycle=cycle).to_dict()
    for display in vm["displays"]:
        for lineup in display["lineups"]:
            for step in lineup["steps"]:
                step["composition_digest"] = "abc123def4567890"
    return vm


_PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="
_FRAME = {
    "label": "a frame sent by hand",
    "thumb_url": "",
    "since": "17:58",
    "title": "A frame sent by hand, on screen since 17:58",
}


_QUEUED = "A new frame is queued. The frame picks it up at its next check-in ~3:48 PM."


def _note(state: str, label: str, sub: str = "", title: str = "") -> dict[str, str]:
    return {"state": state, "label": label, "sub": sub, "title": title or label}


def _header_variant(display: dict[str, Any], kind: str) -> None:
    """The kitchen's header with no dashboard on screen, as the view-model
    draws a frame sent by hand, a never-rendered display or a heard one."""
    if not kind:
        return
    display["on_screen"], display["on_screen_known"] = None, False
    display["queued_badge"] = None
    display["placeholder"] = _note("none", "No image yet")
    if kind in ("frame", "badge"):
        display["frame"] = {**_FRAME, "thumb_url": _PIXEL}
        display["show_title"] = _FRAME["title"]
        display["placeholder"] = None
        if kind == "badge":
            display["queued_badge"] = _note("queued", "Queued", "", _QUEUED)
    elif kind == "frame-gone":
        display["frame"] = dict(_FRAME)
        display["placeholder"] = _note("unavailable", "Image unavailable")
    elif kind == "nothing":
        display["nothing_sent"] = True
        display["placeholder"] = _note("nothing", "Nothing sent yet")
    elif kind == "queued":
        display["placeholder"] = _note(
            "queued", "Queued", "Frame picks it up at its next check-in ~3:48 PM", _QUEUED
        )
    elif kind == "delivering":
        display["placeholder"] = _note("delivering", "Delivering")
    elif kind == "heard":
        display["show_note"] = ", checked in 18:10"
    else:
        display["show_note"] = ", waiting for check-in"


@pytest.fixture
def live(tmp_path: Path) -> Iterator[str]:
    from werkzeug.serving import make_server

    plugins_dir = Path(__file__).resolve().parents[1] / "plugins"
    app: Flask = create_app(testing=False, data_root=tmp_path / "data", plugins_dir=plugins_dir)
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True})
    store.patch_section("auth", {"disabled": True})

    def sample() -> str:
        vm = _sample_vm(request.args.get("cycle", "h23"))
        _header_variant(vm["displays"][1], request.args.get("hdr", ""))
        for d in vm["displays"]:
            for lu in d["lineups"]:
                lu["actions"]["play_url"] = f"/decks/{lu['id']}/step"
                lu["actions"]["fire_url"] = f"/decks/{lu['id']}/push"
        return render_template("decks.html", vm=vm, automation_url="#auto")

    app.add_url_rule(PAGE, "lineups_sample", sample)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=10)


class _Api:
    """Answers the page's JSON calls and remembers what was sent."""

    def __init__(self, cycle: str = "h23") -> None:
        self.posts: list[tuple[str, Any]] = []
        self.vm = _sample_vm(cycle)
        self.push_message: str | None = None

    def install(self, page: Any) -> None:
        def handle(route: Any) -> None:
            req = route.request
            if req.method == "GET" and "/decks/view.json" in req.url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(self.vm))
                return
            if req.method == "POST" and re.search(r"/decks/(edits|[^/]+/push-step)$", req.url):
                self.posts.append((req.url, json.loads(req.post_data or "null")))
                reply: dict[str, Any] = (
                    {"ok": True, "lands": "lands now"} if "push-step" in req.url else {"ok": True}
                )
                if "push-step" in req.url and self.push_message:
                    reply["message"] = self.push_message
                    reply["fresh"] = True
                route.fulfill(status=200, content_type="application/json", body=json.dumps(reply))
                return
            route.continue_()

        page.route(re.compile(r".*/decks/.*"), handle)
        # The live-events stream is not under test; keep the page quiet.
        page.route(re.compile(r".*/events/stream.*"), lambda r: r.fulfill(status=204, body=""))


def _open(
    p: Any,
    base: str,
    *,
    width: int = 1440,
    js: bool = True,
    scheme: str = "dark",
    cycle: str = "h23",
    before_load: Any = None,
    hdr: str = "",
) -> tuple[Any, Any, _Api]:
    browser = _launch(p)
    ctx = browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme, java_script_enabled=js
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page._lu_errors = errors
    api = _Api(cycle)
    api.install(page)
    if before_load:
        before_load(page)
    page.goto(base + PAGE + f"?cycle={cycle}&hdr={hdr}", wait_until="load")
    if js:
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
    return browser, page, api


# -- the client draws what the server drew ----------------------------------------


class _Shape(HTMLParser):
    """Element tree as (tag, classes, attributes that matter, text)."""

    KEEP = (
        "id",
        "role",
        "title",
        "hidden",
        "tabindex",
        "type",
        "action",
        "method",
        "value",
        "name",
        "disabled",
        "style",
        "src",
        "href",
        "loading",
        "decoding",
        "alt",
    )

    def __init__(self) -> None:
        super().__init__()
        self.out: list[tuple[Any, ...]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("style"):
            # Python writes 0.0 where JS writes 0; compare the same CSS value.
            a["style"] = re.sub(r"(\d+)\.0+(?=[;%/ ]|$)", r"\1", a["style"] or "")
        keep = tuple(
            sorted(
                (k, v or "")
                for k, v in a.items()
                if k in self.KEEP or k.startswith("data-") or k.startswith("aria-")
            )
        )
        self.out.append((tag, tuple(sorted((a.get("class") or "").split())), keep))

    def handle_data(self, data: str) -> None:
        t = " ".join(data.split())
        if t:
            self.out.append(("#text", t))


def _shape(markup: str) -> list[tuple[Any, ...]]:
    s = _Shape()
    s.feed(markup)
    return s.out


@pytest.mark.parametrize("cycle", ["h23", "h12"])
def test_client_render_matches_the_server_markup(live: str, cycle: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, nojs, _ = _open(p, live, js=False, cycle=cycle)
        server = nojs.eval_on_selector_all(".lu-ds", "els => els.map(e => e.outerHTML)")
        browser.close()
        browser, page, _ = _open(p, live, cycle=cycle)
        client = page.evaluate(
            """() => {
              const vm = JSON.parse(document.querySelector('[data-lu-vm]').textContent);
              const ui = { view: 'cards', folded: (l) => l.folded, collapsed: () => false, pendingBar: () => '' };
              return vm.displays.map((d) => TesseraeLineupsRender.display(vm, d, ui));
            }"""
        )
        browser.close()
    assert len(server) == len(client) == 2
    for s, c in zip(server, client, strict=True):
        assert _shape(s) == _shape(c)
    if cycle == "h12":
        assert "\u202fPM" in "".join(server)  # the 12-hour labels really drew


@pytest.mark.parametrize(
    "hdr",
    ["frame", "frame-gone", "nothing", "heard", "waiting", "queued", "badge", "delivering"],
)
def test_header_without_a_dashboard_matches_both_renderers(live: str, hdr: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, nojs, _ = _open(p, live, js=False, hdr=hdr)
        server = nojs.eval_on_selector_all(".lu-ds-hdr", "els => els.map(e => e.outerHTML)")
        browser.close()
        browser, page, _ = _open(p, live, hdr=hdr)
        client = page.evaluate(
            """() => {
              const vm = JSON.parse(document.querySelector('[data-lu-vm]').textContent);
              const ui = { view: 'cards', folded: (l) => l.folded, collapsed: () => false, pendingBar: () => '' };
              return vm.displays.map((d) => TesseraeLineupsRender.display(vm, d, ui));
            }"""
        )
        text = page.locator("#display-kitchen .lu-ds-hdr").inner_text()
        thumb = page.locator("#display-kitchen .lu-now-thumb")
        title = thumb.get_attribute("title")
        has_img = thumb.locator("img").count()
        gamut = thumb.locator(".lu-thumb[data-gamut]").count()
        badge = thumb.locator(".lu-qbadge").count()
        thumb_text = thumb.inner_text()
        browser.close()
    assert len(server) == 2
    assert _shape(server[1]) == _shape(re.search(r"<header.*?</header>", client[1], re.S).group(0))
    assert "?" not in thumb_text
    if hdr == "badge":
        assert has_img == 1 and badge == 1 and title == _QUEUED
        assert "a frame sent by hand" in text
    elif hdr.startswith("frame"):
        assert "a frame sent by hand" in text and "unknown" not in text
        assert title == _FRAME["title"] and badge == 0
        # A real thumb carries the display-color treatment; the placeholder has none.
        assert (has_img, gamut) == ((1, 1) if hdr == "frame" else (0, 0))
    elif hdr == "nothing":
        assert "Nothing sent yet" in text and title == "Nothing sent yet"
    elif hdr == "queued":
        assert "Queued" in text and "next check-in ~3:48 PM" in text and title == _QUEUED
    elif hdr == "delivering":
        assert "Delivering" in text
    elif hdr == "heard":
        assert "checked in 18:10" in text and "waiting" not in text
    else:
        assert "waiting for check-in" in text


def test_client_clock_matches_the_server_clock(live: str) -> None:
    """lineups_render.js ``clock`` and app/lineups_view.py ``clock`` agree
    on every minute of the day, both clock styles, start and end."""
    from playwright.sync_api import sync_playwright

    from app.lineups_view import clock

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        for cycle in ("h12", "h23"):
            client = page.evaluate(
                """(cycle) => {
                  const R = TesseraeLineupsRender;
                  R.setTimeCycle(cycle);
                  const out = [];
                  for (let m = -60; m <= 1500; m++) out.push([R.clock(m), R.clock(m, true)]);
                  return out;
                }""",
                cycle,
            )
            server = [
                [clock(m, cycle=cycle), clock(m, end=True, cycle=cycle)] for m in range(-60, 1501)
            ]
            assert client == server
        browser.close()


_H23 = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?!\u202f[AP]M)")


def test_12_hour_page_draws_no_24_hour_times(live: str) -> None:
    from playwright.sync_api import sync_playwright

    grab = """() => {
      const out = [];
      document.querySelectorAll('.lu [title], .lu [aria-label]').forEach((e) => {
        out.push(e.getAttribute('title') || '', e.getAttribute('aria-label') || '');
      });
      document.querySelectorAll('.lu-win, .lu-scale span, .lu-tnext-l > span:first-child, .lu-ds')
        .forEach((e) => out.push(e.textContent));
      return out.filter(Boolean);
    }"""
    with sync_playwright() as p:
        browser, page, _ = _open(p, live, cycle="h12")
        cards = page.evaluate(grab)
        _timeline(page)
        timeline = page.evaluate(grab)
        browser.close()
    texts = cards + timeline
    assert [t for t in texts if _H23.search(t)] == []
    joined = "\n".join(texts)
    for want in ("Now 6:12\u202fPM", "Next advance ", "12\u202fAM", "\u202fAM–"):
        assert want in joined, want
    assert any(t.startswith("next ") and t.endswith("M") for t in timeline)


_START_BOXES = """() => [...document.querySelectorAll('.lu-ds')].map((ds) => {
  const sc = ds.querySelector('.lu-today .lu-scale'), bar = sc.getBoundingClientRect();
  const labels = [...sc.querySelectorAll('span')].filter((s) => getComputedStyle(s).display !== 'none')
    .map((s) => { const r = document.createRange(); r.selectNodeContents(s);
      const b = r.getBoundingClientRect(), stem = getComputedStyle(s, '::before');
      return {text: s.textContent, l: b.left, r: b.right, x: parseFloat(s.style.getPropertyValue('--x')),
        stem: s.getBoundingClientRect().left + parseFloat(stem.left)}; });
  return {st: sc.classList.contains('st'), l: bar.left, r: bar.right, labels};
})"""


@pytest.mark.parametrize("cycle", ["h23", "h12"])
@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("js", [True, False])
def test_block_start_labels_never_overlap(live: str, width: int, js: bool, cycle: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live, width=width, js=js, cycle=cycle)
        boxes = page.evaluate(_START_BOXES)
        # Wed has three blocks on the frame (starts), one on the kitchen (axis).
        assert [b["st"] for b in boxes] == [True, False]
        expected = (
            ["6:30\u202fAM", "5\u202fPM", "10\u202fPM"]
            if cycle == "h12"
            else ["06:30", "17:00", "22:00"]
        )
        assert [x["text"] for x in boxes[0]["labels"]] == expected
        if js:
            # A dense day: ten blocks, some a few minutes apart, one at 23:50.
            starts = [0, 10, 20, 360, 375, 390, 720, 735, 1080, 1430]
            day = api.vm["displays"][0]["day"]
            seg = next(s for s in day["segments"] if s["kind"] == "window")
            day["segments"] = [
                {**seg, "start_min": m, "end_min": m + 5 * (i + 1), "live": False}
                for i, m in enumerate(starts)
            ]
            page.evaluate("() => TesseraeLineups.refresh()")
            page.wait_for_function(
                "() => document.querySelectorAll('.lu-scale.st span').length === 10"
            )
            boxes = page.evaluate(_START_BOXES)
        browser.close()
    for b in boxes:
        shown = sorted(b["labels"], key=lambda x: x["l"])
        for x in shown:
            assert b["l"] - 0.5 <= x["l"] and x["r"] <= b["r"] + 0.5, x  # on the bar
            if b["st"]:
                # The stem marks the block's left edge; the time starts there
                # unless it had to slide left to end at the bar's end.
                assert abs(x["stem"] - (b["l"] + x["x"] / 100 * (b["r"] - b["l"]))) < 1
                assert abs(x["l"] - x["stem"]) < 1 or abs(x["r"] - b["r"]) < 1
        for x, y in itertools.pairwise(shown):
            assert x["r"] < y["l"], (x, y)
    if js:
        assert 2 <= len(boxes[0]["labels"]) < 10


# -- folding ---------------------------------------------------------------------


def test_rows_and_displays_fold_and_remember(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        dinner = page.locator("#lu-row-dinner")
        assert "is-folded" not in dinner.get_attribute("class")
        # A click anywhere on the row head folds it; the chevron says so.
        page.locator("#lu-row-dinner .lu-row-name").click()
        assert "is-folded" in dinner.get_attribute("class")
        assert page.locator('[data-fold="dinner"]').get_attribute("aria-expanded") == "false"
        assert not page.locator("#lu-body-dinner").is_visible()
        # Folded, the live row still names what is on screen.
        assert page.locator("#lu-row-dinner .lu-hs-now").inner_text().strip() == "Family"
        # The keyboard unfolds a later-today row.
        page.locator('[data-fold="night"]').focus()
        page.keyboard.press("Enter")
        assert "is-folded" not in page.locator("#lu-row-night").get_attribute("class")
        # Collapsing a display keeps its header block, hides the rest.
        page.locator('[data-collapse="kitchen"]').click()
        sec = page.locator('.lu-ds[data-display="kitchen"]')
        assert "is-collapsed" in sec.get_attribute("class")
        assert sec.locator(".lu-ds-hdr h2").is_visible()
        assert sec.locator(".lu-today").count() == 0
        assert not sec.locator(".lu-ds-body").is_visible()
        assert page.locator('[data-collapse="kitchen"]').evaluate(
            "b => b === document.activeElement"
        )
        page.reload(wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        assert "is-folded" in page.locator("#lu-row-dinner").get_attribute("class")
        assert "is-folded" not in page.locator("#lu-row-night").get_attribute("class")
        assert "is-collapsed" in page.locator('.lu-ds[data-display="kitchen"]').get_attribute(
            "class"
        )
        # Fold all, then Unfold all.
        fa = page.locator("[data-fold-all]")
        assert fa.inner_text().strip() == "Fold all"
        fa.click()
        assert page.locator(".lu-ds.is-collapsed").count() == 2
        assert fa.inner_text().strip() == "Unfold all"
        fa.click()
        assert page.locator(".lu-ds.is-collapsed").count() == 0
        assert page.locator(".lu-row.is-folded").count() == 0
        assert page._lu_errors == []
        browser.close()


def test_storage_that_throws_still_folds(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _Api().install(page)
        page.goto(live + PAGE, wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        page.locator('[data-fold="dinner"]').click()
        assert "is-folded" in page.locator("#lu-row-dinner").get_attribute("class")
        assert not [e for e in errors if "lineups" in e.lower() or "blocked" in e]
        browser.close()


# -- day picker -------------------------------------------------------------------


def test_day_picker(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        btn = page.locator('[data-dpick="frame"]')
        btn.click()
        pop = page.locator(".lu-dp-pop")
        assert pop.is_visible() and btn.get_attribute("aria-expanded") == "true"
        assert page.evaluate("() => document.activeElement.dataset.day") == "2"
        page.keyboard.press("ArrowRight")
        assert page.evaluate("() => document.activeElement.dataset.day") == "3"
        page.keyboard.press("Escape")
        assert pop.count() == 0
        btn.click()
        page.locator('.lu-dp-day[data-day="5"]').click()
        frame = page.locator('.lu-ds[data-display="frame"]')
        assert (
            frame.locator(".lu-trk")
            .get_attribute("aria-label")
            .startswith("Sat: Morning (weekend) 08:00")
        )
        assert frame.locator(".lu-dp-label").inner_text().strip() == "Sat · in 3 days"
        assert frame.locator(".lu-blk.plan").count() >= 1 and frame.locator(".lu-nowm").count() == 0
        # The other display keeps its own day.
        assert (
            page.locator('.lu-ds[data-display="kitchen"] .lu-trk')
            .get_attribute("aria-label")
            .startswith("Wed:")
        )
        frame.locator(".lu-dp-label").click()
        assert frame.locator(".lu-trk").get_attribute("aria-label").startswith("Wed:")
        assert frame.locator(".lu-nowm").count() == 1
        browser.close()


# -- edits: day tiles, drags, Save / Undo -------------------------------------------


def _drag_by_minutes(page: Any, lineup: str, minutes: float, handle: str | None = None) -> None:
    sel = f'.lu-ds[data-display="frame"] .lu-blk[data-edit][data-lineup="{lineup}"]'
    page.locator(sel).scroll_into_view_if_needed()
    trk = page.locator('.lu-ds[data-display="frame"] .lu-trk').bounding_box()
    box = page.locator(sel).bounding_box()
    x = box["x"] + (3 if handle == "l" else box["width"] - 3 if handle == "r" else box["width"] / 2)
    y = box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + trk["width"] * minutes / 1440, y + 4, steps=6)
    assert page.locator(".lu-drag-tip").count() == 1
    page.mouse.up()


def test_bar_drag_snaps_and_saves_as_one_batch(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        win = page.locator("#lu-row-dinner .lu-win")
        assert win.inner_text() == "17:00–20:00"
        _drag_by_minutes(page, "dinner", -37)  # snaps to 30
        assert win.inner_text() == "16:30–19:30"
        assert page.locator("#lu-row-dinner .lu-edot").count() == 1
        pending = page.locator('.lu-ds[data-display="frame"] .lu-pending')
        assert "1 unsaved change" in pending.inner_text()
        assert api.posts == []  # nothing leaves the page before Save
        pending.locator("[data-undo]").click()
        assert win.inner_text() == "17:00–20:00"
        assert page.locator("#lu-row-dinner .lu-edot").count() == 0
        # Resize the end by its handle, then add a day on another lineup.
        _drag_by_minutes(page, "dinner", 52, handle="r")  # snaps to 45
        assert win.inner_text() == "17:00–20:45"
        page.locator('[data-toggle-day="night:2"]').click()  # Night off on Wednesdays
        assert page.locator('[data-toggle-day="night:2"]').get_attribute("aria-pressed") == "false"
        assert "2 unsaved changes" in pending.inner_text()
        pending.locator("[data-save]").click()
        page.wait_for_function(
            "() => !document.querySelector('.lu-ds[data-display=\"frame\"] .lu-pend-count')"
        )
        assert len(api.posts) == 1
        url, body = api.posts[0]
        assert url.endswith("/decks/edits")
        edits = {e["id"]: e for e in body["edits"]}
        assert edits["dinner"] == {
            "id": "dinner",
            "start": "17:00",
            "end": "20:45",
            "days": [0, 1, 2],
        }
        assert edits["night"] == {
            "id": "night",
            "start": "22:00",
            "end": None,
            "days": [0, 1, 3, 4, 5, 6],
        }
        assert (
            "2 changes saved."
            in page.locator('.lu-ds[data-display="frame"] .lu-pending').inner_text()
        )
        assert page._lu_errors == []
        browser.close()


def test_keyboard_moves_and_resizes_a_window(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        sel = '.lu-ds[data-display="frame"] .lu-blk[data-edit][data-lineup="dinner"]'
        page.locator(sel).focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#lu-row-dinner .lu-win").inner_text() == "17:15–20:15"
        assert page.evaluate("() => document.activeElement.dataset.lineup") == "dinner"
        page.keyboard.press("Shift+ArrowLeft")
        assert page.locator("#lu-row-dinner .lu-win").inner_text() == "17:15–20:00"
        page.keyboard.press("Alt+Shift+ArrowLeft")
        assert page.locator("#lu-row-dinner .lu-win").inner_text() == "17:00–20:00"
        # Back where it started: nothing is pending.
        assert page.locator('.lu-ds[data-display="frame"] .lu-pend-count').count() == 0
        browser.close()


def test_day_tiles_refuse_a_clash_and_the_last_day(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        # Breakfast on Monday would overlap Morning (Mon).
        page.locator('[data-toggle-day="breakfast:0"]').click()
        msg = page.locator('.lu-ds[data-display="frame"] .lu-pend-msg').inner_text()
        assert "Mon clashes with Morning (Mon) (06:00–09:00)" in msg
        assert (
            page.locator('[data-toggle-day="breakfast:0"]').get_attribute("aria-pressed") == "false"
        )
        page.locator('[data-toggle-day="monday:0"]').click()
        assert (
            "needs at least one day"
            in page.locator('.lu-ds[data-display="frame"] .lu-pend-msg').inner_text()
        )
        # A typed window: Enter applies, the clash check runs first.
        page.locator('[data-win-edit="night"]').click()
        inp = page.locator(".lu-win-edit input")
        inp.fill("21:00")
        inp.press("Enter")
        assert page.locator("#lu-row-night .lu-win").inner_text() == "at 21:00"
        browser.close()


# -- push now, items popover ---------------------------------------------------------


def test_push_now_and_the_items_popover(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        tile = page.locator('#lu-row-dinner .lu-step[data-step="0"] .lu-tile')
        tile.hover()
        page.locator('[data-push="dinner:0"]').click()
        page.wait_for_function(
            "() => document.querySelector('#lu-row-dinner .lu-step[data-step=\"0\"] .lu-tile').classList.contains('pushed')"
        )
        assert tile.locator(".lu-pushed-msg").inner_text() == "Pushed · lands now"
        url, body = api.posts[-1]
        assert url.endswith("/decks/dinner/push-step")
        assert body == {"index": 0, "display_id": "frame", "composition_digest": "abc123def4567890"}
        # ×12 opens a grid of the step's items; Escape closes and returns focus.
        count = page.locator('[data-peek="dinner:1"]')
        count.click()
        peek = page.locator(".lu-peek")
        assert peek.get_attribute("role") == "dialog"
        assert peek.locator(".lu-pk-item").count() == 12
        assert count.get_attribute("aria-expanded") == "true"
        assert peek.locator("[data-push]").count() == 0
        assert len(api.posts) == 1
        page.keyboard.press("Escape")
        assert peek.count() == 0
        assert page.evaluate("() => document.activeElement.dataset.peek") == "dinner:1"
        browser.close()


@pytest.mark.parametrize("width", [360, 1024, 1440, 2736])
def test_no_page_level_sideways_scroll(live: str, width: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for scheme in ("dark", "light"):
            browser, page, _ = _open(p, live, width=width, scheme=scheme)
            over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            assert over <= 0, (width, scheme, over)
            # The step strip scrolls itself instead.
            if width <= 1024:
                sc = page.locator("#lu-row-dinner .lu-strip-scroll")
                assert sc.evaluate("e => e.scrollWidth > e.clientWidth")
            # Content stays capped and left-aligned on a wide screen.
            if width == 2736:
                box = page.locator(".lu").bounding_box()
                assert box["width"] <= 1480 + 1
            browser.close()


# -- phones and tablets ----------------------------------------------------------------

DEVICES = [
    "iPhone 13",
    "iPhone 15",
    "Pixel 7",
    "iPad Mini",
    "iPhone 13 landscape",
    "Pixel 7 landscape",
]

# Every visible control a finger can reach, with its box. Bar blocks are
# measured by height only: their width is their duration (a single push
# gets a widened hit area in CSS).
_SMALL_TARGETS = """() => {
  const out = [];
  document.querySelectorAll('.lu button, .lu a, .lu summary, .lu input, .lu select, .lu [tabindex="0"]').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.closest('[hidden]')) return;
    const det = el.closest('details');
    if (det && !det.open && !el.matches('summary')) return;  // inside a closed menu
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.pointerEvents === 'none') return;
    if (el.matches('.lu-strip-scroll, .lu-lanes-scroll')) return;  // scroll regions, not buttons
    const blk = el.matches('.lu-blk');
    if (r.height < 44 || (!blk && r.width < 44)) {
      out.push(`${el.className.baseVal ?? el.className} ${el.dataset.toggleDay || el.textContent.trim().slice(0, 16)} ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
  });
  return out;
}"""


def _open_device(p: Any, base: str, device: str, scheme: str = "dark") -> tuple[Any, Any, _Api]:
    browser = _launch(p)
    ctx = browser.new_context(**p.devices[device], color_scheme=scheme)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page._lu_errors = errors
    api = _Api()
    api.install(page)
    page.goto(base + PAGE, wait_until="load")
    page.wait_for_function(
        "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
    )
    return browser, page, api


@pytest.mark.parametrize("device", DEVICES)
def test_touch_devices_fit_and_every_target_is_44px(live: str, device: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open_device(p, live, device)
        assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
        assert page.evaluate(_SMALL_TARGETS) == []
        # Open a folded row and the menus too: their controls count as well.
        page.locator('[data-fold="night"]').tap()
        page.locator("#lu-row-dinner .lu-more summary").tap()
        page.wait_for_timeout(450)  # the menu's opening animation (morph.js)
        assert page.evaluate(_SMALL_TARGETS) == []
        page.locator("#lu-row-dinner .lu-more summary").tap()
        page.locator('[data-view="timeline"]').tap()
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
        assert page.evaluate(_SMALL_TARGETS) == []
        # Inputs are 16px so iOS does not zoom on focus.
        sizes = page.eval_on_selector_all(
            ".lu input, .lu select", "els => els.map(e => parseFloat(getComputedStyle(e).fontSize))"
        )
        assert sizes and min(sizes) >= 16
        assert page._lu_errors == []
        browser.close()


@pytest.mark.parametrize("device", ["iPhone 13", "Pixel 7 landscape"])
def test_phone_bar_is_read_only_and_edits_use_inputs(live: str, device: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open_device(p, live, device)
        assert "bar-ro" in page.locator("[data-lu]").get_attribute("class")
        assert page.locator(".lu-blk[data-edit]").count() == 0
        assert (
            page.locator(".lu-blk").first.evaluate("e => getComputedStyle(e).touchAction") == "auto"
        )
        assert (
            page.locator(".lu-strip-scroll").first.evaluate("e => getComputedStyle(e).touchAction")
            == "pan-x pan-y"
        )
        # The window editor is a native 15-minute time input.
        page.locator('[data-win-edit="dinner"]').tap()
        start = page.locator(".lu-win-edit input").first
        assert start.get_attribute("type") == "time" and start.get_attribute("step") == "900"
        start.fill("16:00")
        start.press("Enter")
        assert page.locator("#lu-row-dinner .lu-win").inner_text() == "16:00–20:00"
        # The Save bar sticks to the bottom of the screen.
        bar = page.locator('.lu-ds[data-display="frame"] .lu-pending')
        assert bar.evaluate("e => getComputedStyle(e).position") == "sticky"
        page.locator('[data-toggle-day="dinner:3"]').tap()
        assert page.locator('[data-toggle-day="dinner:3"]').get_attribute("aria-pressed") == "true"
        assert "1 unsaved change" in bar.inner_text()  # one lineup, window and days
        browser.close()


def test_tablet_drags_the_bar_with_touch(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open_device(p, live, "iPad Mini")
        assert "bar-ro" not in page.locator("[data-lu]").get_attribute("class")
        sel = '.lu-ds[data-display="frame"] .lu-blk[data-edit][data-lineup="dinner"]'
        assert page.locator(sel).evaluate("e => getComputedStyle(e).touchAction") == "none"
        # A touch pointer drag (pointer events, not mouse events).
        page.evaluate(
            """(sel) => {
              const blk = document.querySelector(sel), trk = blk.closest('.lu-trk');
              const r = blk.getBoundingClientRect(), W = trk.clientWidth;
              const x = r.left + r.width / 2, y = r.top + r.height / 2;
              const ev = (type, cx, target) => target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 7, pointerType: 'touch', isPrimary: true, button: 0, clientX: cx, clientY: y }));
              ev('pointerdown', x, blk);
              for (let i = 1; i <= 5; i++) ev('pointermove', x + (W * 60 / 1440) * i / 5, document);
              ev('pointerup', x + W * 60 / 1440, document);
            }""",
            sel,
        )
        assert page.locator("#lu-row-dinner .lu-win").inner_text() == "18:00–21:00"
        browser.close()


def test_touch_push_now_reveals_then_pushes(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open_device(p, live, "iPhone 13")
        btn = page.locator('[data-push="dinner:0"]')
        assert btn.evaluate("e => getComputedStyle(e).opacity") == "1"  # always shown on touch
        btn.scroll_into_view_if_needed()
        btn.tap()
        tile = page.locator('#lu-row-dinner .lu-step[data-step="0"] .lu-tile')
        assert "reveal" in tile.get_attribute("class") and api.posts == []
        btn.tap()
        page.wait_for_function(
            "() => document.querySelector('#lu-row-dinner .lu-step[data-step=\"0\"] .lu-tile').classList.contains('pushed')"
        )
        assert api.posts[-1][1] == {
            "index": 0,
            "display_id": "frame",
            "composition_digest": "abc123def4567890",
        }
        browser.close()


@pytest.mark.parametrize("device", ["iPhone 13", "iPhone 13 landscape"])
def test_popovers_are_bottom_sheets_on_phones(live: str, device: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open_device(p, live, device)
        vw, vh = page.evaluate("() => [innerWidth, innerHeight]")

        def on_screen(sel: str) -> None:
            r = page.locator(sel).bounding_box()
            assert r["x"] >= 0 and r["x"] + r["width"] <= vw + 0.5
            assert r["y"] >= 0 and abs(r["y"] + r["height"] - vh) <= 1  # docked to the bottom

        page.locator('[data-dpick="frame"]').tap()
        on_screen(".lu-dp-pop")
        assert (
            page.evaluate("() => getComputedStyle(document.documentElement).overflow") == "hidden"
        )
        assert page.locator("[data-dp-close]").is_visible()
        page.locator('.lu-dp-day[data-day="4"]').tap()
        assert (
            page.locator(".lu-dp-pop").count() == 0
            and page.locator("[data-sheet-scrim]").count() == 0
        )
        assert (
            page.evaluate("() => getComputedStyle(document.documentElement).overflow") != "hidden"
        )
        page.locator('[data-dpick="frame"]').tap()
        page.locator("[data-dp-close]").tap()
        assert page.locator(".lu-dp-pop").count() == 0
        # The items sheet closes from a tap on the scrim.
        page.locator('[data-peek="dinner:1"]').scroll_into_view_if_needed()
        page.locator('[data-peek="dinner:1"]').tap()
        on_screen(".lu-peek")
        assert page.locator(".lu-peek [data-peek-close]").is_visible()
        page.mouse.click(vw / 2, 10)  # the scrim covers the page above the sheet
        assert page.locator(".lu-peek").count() == 0
        assert (
            page.evaluate("() => getComputedStyle(document.documentElement).overflow") != "hidden"
        )
        browser.close()


def test_paper_live_row_has_an_accent_edge(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, scheme="light")
        # Resolve the edge and the surface behind the row to sRGB and compare.
        ratio = page.evaluate(
            """() => {
              const rgb = (css) => { const c = document.createElement('canvas').getContext('2d'); c.fillStyle = css; c.fillRect(0, 0, 1, 1); return [...c.getImageData(0, 0, 1, 1).data].slice(0, 3).map(v => v / 255); };
              const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
              const lum = (v) => 0.2126 * lin(v[0]) + 0.7152 * lin(v[1]) + 0.0722 * lin(v[2]);
              const row = document.querySelector('#lu-row-dinner');
              const edge = getComputedStyle(row).boxShadow.match(/^(.*?\\)) 0px 0px 0px 1px inset/)[1];
              const bg = getComputedStyle(row.closest('.lu-sched')).backgroundColor;
              const a = lum(rgb(edge)), b = lum(rgb(bg));
              return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
            }"""
        )
        assert ratio >= 3, ratio
        assert (
            page.locator("#lu-row-night").evaluate("e => getComputedStyle(e).boxShadow") == "none"
        )
        browser.close()


# -- against the real routes -------------------------------------------------------


@pytest.fixture
def real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Flask]]:
    """The real /decks page and JSON endpoints (app/deck_routes.py) over a
    registered display with a few lineups."""
    import datetime as dt

    from werkzeug.serving import make_server

    import app.deck_routes as deck_routes
    from app.main import REPO_ROOT
    from app.state.deck_model import Deck, DeckPage
    from app.state.page_store import Page

    class _Midday(dt.datetime):
        # The view folds lineups that are not running or due later today, and
        # lazy thumbnails inside a folded row never load. Pin the page's wall
        # clock to a weekday midday, between the two windows below, so these
        # tests do not depend on the hour they run.
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
            return cls(2026, 10, 28, 12, 0, tzinfo=tz)

    monkeypatch.setattr(deck_routes, "datetime", _Midday)

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    app.config["TESTING"] = True
    store = app.config["SETTINGS_STORE"]
    store.patch_section("app", {"onboarded": True, "locale": "en-GB"})  # 24-hour labels
    store.patch_section("auth", {"disabled": True})
    for pid in ("news", "art", "sky"):
        app.config["PAGE_STORE"].save(Page(id=pid, name=pid.title(), device_ids=["panel"]))
    c = app.test_client()
    code = app.config["PAIRING_STORE"].issue(note="test").code
    resp = c.post(
        "/api/v1/device/register",
        headers={"X-Pairing-Code": code, "Content-Type": "application/json"},
        data=json.dumps({"device_id": "panel", "kind": "esp32_client"}),
    )
    assert resp.status_code == 201

    def cycle(id_: str, name: str, start: str, end: str, days: list[int]) -> Deck:
        return Deck(
            id=id_, name=name, device_ids=["panel"],
            pages=[DeckPage(page_id="news"), DeckPage(page_id="art")],
            advance="timer", advance_interval_minutes=30,
            advance_anchor=start, advance_end_at=end, advance_days_of_week=days,
        )  # fmt: skip

    decks = app.config["DECK_STORE"]
    decks.upsert(cycle("morning", "Morning", "06:00", "09:00", [0, 1, 2, 3, 4]))
    decks.upsert(cycle("evening", "Evening", "17:00", "20:00", [0, 1, 2, 3, 4]))
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", app
    finally:
        server.shutdown()
        thread.join(timeout=10)


def test_deleted_dashboard_step_is_flagged_server_and_client_side(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    from app.state.deck_model import Deck, DeckPage

    base, app = real
    app.config["DECK_STORE"].upsert(
        Deck(
            id="gone", name="Gone", device_ids=["panel"],
            pages=[DeckPage(page_id="news"), DeckPage(page_id="f68e0c3b23bd")],
            advance="timer", advance_interval_minutes=30,
            advance_anchor="10:00", advance_end_at="11:00", advance_days_of_week=[0, 1, 2, 3, 4, 5, 6],
        )
    )  # fmt: skip
    tip = "This dashboard was deleted; remove it from the lineup"
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route(re.compile(r".*/events/stream.*"), lambda r: r.fulfill(status=204, body=""))
        served = page.request.get(base + "/decks").text()
        assert 'is-missing" data-step="1"' in served and "+1 missing" in served
        assert "f68e0c3b23bd" not in re.sub(r"<script.*?</script>", "", served, flags=re.S)

        def check() -> None:
            tile = page.locator("#lu-row-gone .lu-step.is-missing")
            assert tile.count() == 1
            assert tile.get_attribute("title") == tip
            assert tile.locator(".lu-step-name").inner_text() == "Missing dashboard"
            assert tile.locator(".lu-thumb-ph").inner_text() == "!"
            assert tile.locator(".lu-push-form").count() == 0
            assert "1 dashboard" in page.locator("#lu-row-gone .lu-im").first.get_attribute("title")
            assert "+1 missing" in page.locator("#lu-row-gone .lu-im.warn").inner_text()
            assert "f68e0c3b23bd" not in page.locator("#lu-row-gone").inner_text()

        page.goto(base + "/decks", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        check()
        with page.expect_response(lambda r: "/decks/view.json" in r.url):
            page.evaluate("() => window.TesseraeLineups.refresh()")
        check()
        assert errors == []
        browser.close()


def test_editor_shows_deleted_step_and_remove_persists(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    from app.state.deck_model import Deck, DeckPage

    base, app = real
    app.config["DECK_STORE"].upsert(
        Deck(
            id="gone", name="Gone", device_ids=["panel"],
            pages=[DeckPage(page_id="f68e0c3b23bd", dwell_minutes=9), DeckPage(page_id="news")],
            advance="timer", advance_interval_minutes=30,
        )
    )  # fmt: skip
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/decks/gone/edit", wait_until="load")
        cards = page.locator("#dxe-rail .dxe-pcard")
        assert cards.count() == 2
        first = cards.nth(0)
        assert "is-missing" in first.get_attribute("class")
        assert "Missing dashboard (deleted)" in first.inner_text()
        assert "f68e0c3b23bd" in first.locator(".dxe-missing-id").inner_text()
        assert page.locator("#dxe-pages-field").input_value() == "f68e0c3b23bd,news"
        # Unchanged, it is kept; Remove drops just that step and survives a save.
        first.locator(".dxe-remove-missing").click()
        assert page.locator("#dxe-rail .dxe-pcard").count() == 1
        assert page.locator("#dxe-pages-field").input_value() == "news"
        assert page.locator("#dxe-avail .dxe-chip", has_text="Missing").count() == 0
        page.locator("#deck-form").evaluate("f => f.requestSubmit()")
        page.wait_for_url(re.compile(r".*/decks.*"))
        assert [p_.page_id for p_ in app.config["DECK_STORE"].get("gone").pages] == ["news"]
        assert errors == []
        browser.close()


def test_real_routes_draw_refresh_and_save(real: tuple[str, Flask]) -> None:
    from playwright.sync_api import sync_playwright

    base, app = real
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route(re.compile(r".*/events/stream.*"), lambda r: r.fulfill(status=204, body=""))
        page.goto(base + "/decks", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        assert (
            page.locator("#lu-row-morning").count() == 1
            and page.locator("#lu-row-evening").count() == 1
        )
        # The live refresh reads the real view.json and keeps the page as it was.
        page.locator('[data-fold="evening"]').click()
        folded = "is-folded" in page.locator("#lu-row-evening").get_attribute("class")
        with page.expect_response(lambda r: "/decks/view.json" in r.url) as refreshed:
            page.evaluate("() => window.TesseraeLineups.refresh()")
        assert refreshed.value.status == 200
        assert ("is-folded" in page.locator("#lu-row-evening").get_attribute("class")) == folded
        # Move Evening by 15 minutes from the keyboard, add Saturday, save.
        page.locator('[data-toggle-day="evening:5"]').click()
        assert page.locator('[data-toggle-day="evening:5"]').get_attribute("aria-pressed") == "true"
        page.locator("#lu-row-evening .lu-win").click()
        inp = page.locator(".lu-win-edit input").first
        inp.fill("17:15")
        inp.press("Enter")
        assert page.locator("#lu-row-evening .lu-win").inner_text() == "17:15–20:00"
        with page.expect_response(lambda r: r.url.endswith("/decks/edits")) as resp:
            page.locator("[data-save]").click()
        assert resp.value.status == 200, resp.value.text()
        page.wait_for_function("() => !document.querySelector('.lu-pend-count')")
        deck = app.config["DECK_STORE"].get("evening")
        assert deck.advance_anchor == "17:15"
        assert sorted(deck.advance_days_of_week) == [0, 1, 2, 3, 4, 5]
        # A clash is refused before it is queued; nothing is sent.
        page.locator("#lu-row-morning .lu-win").click()
        inp = page.locator(".lu-win-edit input")
        inp.nth(0).fill("16:00")
        inp.nth(1).fill("18:00")
        inp.nth(1).press("Enter")
        assert "Overlaps Evening" in page.locator(".lu-win-err").inner_text()
        assert errors == []
        browser.close()


# -- Timeline: the week grid --------------------------------------------------------


def _timeline(page: Any) -> None:
    page.locator('[data-view="timeline"]').click()
    page.wait_for_selector(".lu-tlg-row")


def _open_day(page: Any, display: str, day: int) -> Any:
    lab = page.locator(f'.lu-ds[data-display="{display}"] [data-tl-row="{day}"]')
    lab.click()
    page.wait_for_timeout(350)  # the row's height transition, then the layout pass
    return page.locator(f'.lu-ds[data-display="{display}"] .lu-tlg-row[data-day="{day}"]')


def _row(page: Any, display: str, day: int) -> Any:
    return page.locator(f'.lu-ds[data-display="{display}"] .lu-tlg-row[data-day="{day}"]')


_DENSE_TICKS = """() => {
  // An advance every minute on the kitchen's all-day window, the first after
  // now marked next, then drawn again.
  const vm = TesseraeLineups.vm;
  const d = vm.displays.find((x) => x.id === 'kitchen');
  for (const day of [d.day, d.week[vm.now.weekday]]) {
    const s = day.segments.find((x) => x.kind === 'window');
    s.live = true;
    s.ticks = [];
    for (let m = s.start_min + 1; m < s.end_min; m++)
      s.ticks.push({minute: m, pct: Math.round((m - s.start_min) / (s.end_min - s.start_min) * 1e6) / 1e4,
                    next: m === vm.now.minute + 1});
  }
  TesseraeLineups.render();
}"""

_TICK_REPORT = """(blk) => {
  const ticks = [...blk.querySelectorAll('.lu-tick')];
  const shown = ticks.filter((t) => getComputedStyle(t).display !== 'none');
  const xs = shown.map((t) => t.getBoundingClientRect().left);
  const lab = blk.querySelector('.lu-blk-l');
  const r = blk.getBoundingClientRect();
  const hit = shown.map((t) => { const b = t.getBoundingClientRect();
    return document.elementFromPoint(b.left + 0.5, r.top + 3).closest('.lu-blk') === blk; });
  return {
    total: ticks.length,
    shown: shown.length,
    next: shown.filter((t) => t.classList.contains('next')).length,
    titled: shown.filter((t) => t.dataset.tip && !t.title).length,
    hidden: shown.filter((t) => t.getAttribute('aria-hidden') === 'true').length,
    gaps: xs.slice(1).map((x, i) => x - xs[i]),
    labelRight: lab && !lab.classList.contains('gone') ? lab.getBoundingClientRect().right : null,
    firstX: xs[0],
    width: r.width,
    pointer: [...new Set(ticks.map((t) => getComputedStyle(t).pointerEvents))],
    hit,
  };
}"""


def test_future_ticks_are_thinned_and_click_through(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.evaluate(_DENSE_TICKS)
        blk = page.locator('.lu-ds[data-display="kitchen"] .lu-today .lu-blk.live')
        blk.scroll_into_view_if_needed()
        rep = blk.evaluate(_TICK_REPORT)
        # Every minute after now is drawn, but only ~one line per 6px shows;
        # the next one always does. Each carries its tooltip text (never a slow
        # native title) and is aria-hidden.
        assert rep["total"] > 200 and 10 < rep["shown"] < rep["total"] / 2, rep
        assert rep["next"] == 1 and rep["titled"] == rep["shown"], rep
        assert rep["hidden"] == rep["shown"], rep
        assert min(rep["gaps"]) >= 5.5, rep
        assert rep["pointer"] == ["auto"], rep
        assert all(rep["hit"]), rep  # a tick sits inside the block, never over its handles
        # A resize re-thins the bar for its new width.
        page.set_viewport_size({"width": 700, "height": 900})
        page.wait_for_timeout(200)
        blk.scroll_into_view_if_needed()
        narrow = blk.evaluate(_TICK_REPORT)
        assert narrow["width"] != rep["width"] and min(narrow["gaps"]) >= 5.5, narrow
        # The timeline's closed rows carry the same lines, thinned likewise,
        # and none under a block's label.
        _timeline(page)
        tlb = _row(page, "kitchen", 2).locator(".lu-blk.live")
        tlb.scroll_into_view_if_needed()
        tl = tlb.evaluate(_TICK_REPORT)
        assert tl["next"] == 1 and min(tl["gaps"]) >= 5.5 and tl["pointer"] == ["auto"], tl
        assert tl["labelRight"] is None or tl["firstX"] >= tl["labelRight"], tl
        assert page._lu_errors == []
        browser.close()


_FRAME_DINNER = '.lu-ds[data-display="frame"] .lu-blk[data-edit][data-lineup="dinner"]'


def _tick_xs(blk: Any) -> list[dict[str, Any]]:
    return blk.evaluate(
        """(b) => [...b.querySelectorAll('.lu-tick')].filter((t) => getComputedStyle(t).display !== 'none')
          .map((t) => { const r = t.getBoundingClientRect(), v = getComputedStyle(t, '::before');
            return {x: r.left + r.width / 2, w: r.width, tip: t.dataset.tip, title: t.title, next: t.classList.contains('next'),
                    line: parseFloat(v.width), op: parseFloat(v.opacity), hidden: t.getAttribute('aria-hidden')}; })"""
    )


@pytest.mark.parametrize(
    ("cycle", "want"), [("h23", "Next refresh 18:30"), ("h12", "Next refresh 6:30\u202fPM")]
)
def test_tick_hover_zone_carries_the_refresh_time(live: str, cycle: str, want: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, cycle=cycle)
        blk = page.locator(_FRAME_DINNER)
        blk.scroll_into_view_if_needed()
        (nxt,) = _tick_xs(blk)  # the narrow bar thins the later ones away
        # A 9px hover zone around the line; the next one is the same split
        # line as the rest, only 2px and stronger.
        assert nxt["tip"] == want and nxt["title"] == "", nxt
        assert nxt["w"] == 9 and nxt["hidden"] == "true", nxt
        assert nxt["line"] == 2 and 0.5 < nxt["op"] < 1, nxt
        box = blk.bounding_box()
        y = box["y"] + box["height"] / 2
        for dx in (-3, 0, 3):  # the pointer is over the tick either side of the line
            got = page.evaluate(
                "([x, y]) => document.elementFromPoint(x, y).dataset.tip",
                [nxt["x"] + dx, y],
            )
            assert got == want, (dx, got)
        # Later advances read "Refresh <time>", every one that is drawn.
        page.evaluate(_DENSE_TICKS)
        kit = page.locator('.lu-ds[data-display="kitchen"] .lu-today .lu-blk.live')
        kit.scroll_into_view_if_needed()
        bad = kit.evaluate(
            """(b) => {
              const R = TesseraeLineupsRender, vm = TesseraeLineups.vm;
              const s = vm.displays.find((x) => x.id === 'kitchen').day.segments.find((x) => x.kind === 'window');
              return [...b.querySelectorAll('.lu-tick')].filter((t) => getComputedStyle(t).display !== 'none')
                .map((t) => (t.classList.contains('next') ? 'Next refresh ' : 'Refresh ')
                  + R.clock(Math.round(s.start_min + parseFloat(t.style.left) / 100 * (s.end_min - s.start_min))))
                .filter((want, i) => want !== [...b.querySelectorAll('.lu-tick')]
                  .filter((t) => getComputedStyle(t).display !== 'none')[i].dataset.tip);
            }"""
        )
        assert bad == []
        assert page._lu_errors == []
        browser.close()


def test_ticks_do_not_steal_clicks_drags_or_handles(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        win = page.locator("#lu-row-dinner .lu-win")
        blk = page.locator(_FRAME_DINNER)
        blk.scroll_into_view_if_needed()
        box = blk.bounding_box()
        y = box["y"] + box["height"] / 2
        tick = _tick_xs(blk)[0]
        # The resize handles win over any tick in their 9px edge zones.
        for edge, kind in ((box["x"] + 3, "l"), (box["x"] + box["width"] - 3, "r")):
            got = page.evaluate(
                "([x, y]) => { const e = document.elementFromPoint(x, y); return e.closest('.lu-h') && e.closest('.lu-h').dataset.h; }",
                [edge, y],
            )
            assert got == kind, (edge, got)
        # A press that starts on a tick drags the block like any other press.
        page.mouse.move(tick["x"], y)
        page.mouse.down()
        page.mouse.move(
            tick["x"] + page.locator(".lu-ds .lu-trk").first.bounding_box()["width"] * 37 / 1440,
            y + 4,
            steps=6,
        )
        assert page.locator(".lu-drag-tip").count() == 1
        page.mouse.up()
        assert win.inner_text() == "17:30–20:30"  # +37 min snaps to 30
        page.locator('.lu-ds[data-display="frame"] .lu-pending [data-undo]').click()
        assert win.inner_text() == "17:00–20:00"
        # A plain click on a tick reaches the block and opens its lineup.
        page.mouse.click(tick["x"], y)
        page.wait_for_function(
            "() => document.activeElement && document.activeElement.closest('#lu-row-dinner') && document.activeElement.classList.contains('lu-fold')"
        )
        assert page._lu_errors == []
        browser.close()


# The frame's live dinner block moved to the bar's far end, its next advance
# near the right edge (the resize handles own the last 9px).
_NEXT_AT_RIGHT_EDGE = """() => {
  const vm = TesseraeLineups.vm;
  const d = vm.displays.find((x) => x.id === 'frame');
  const s = d.day.segments.find((x) => x.kind === 'window' && x.live);
  d.day.segments = d.day.segments.filter((x) => x === s || x.kind === 'window');
  s.left_pct = 80;
  s.width_pct = 20;
  s.ticks.forEach((t) => { if (t.next) t.pct = 75; });
  TesseraeLineups.render();
}"""

_FRAME_BAR = '.lu-ds[data-display="frame"] .lu-today .lu-trk'
_RECTS = """(trk) => {
  const r = (e) => { const b = e.getBoundingClientRect(); return {l: b.left, r: b.right, t: b.top, b: b.bottom}; };
  const tip = document.querySelector('.lu-tip');
  const flag = trk.querySelector('.lu-nflag');
  const now = trk.querySelector('.lu-nowm');
  const nb = now && now.getBoundingClientRect();
  return {
    trk: r(trk),
    tip: tip && !tip.hidden ? {...r(tip), text: tip.textContent} : null,
    flag: flag && getComputedStyle(flag).display !== 'none' ? {...r(flag), text: flag.textContent} : null,
    knob: nb ? {l: nb.left + nb.width / 2 - 4, r: nb.left + nb.width / 2 + 4, t: nb.top - 3, b: nb.top + 5} : null,
    vw: innerWidth,
  };
}"""


def _hover_tick(page: Any, blk: Any) -> dict[str, Any]:
    page.wait_for_timeout(120)  # a scroll event hides the tooltip a frame later
    tick = _tick_xs(blk)[0]
    box = blk.bounding_box()
    page.mouse.move(2, 2)
    page.mouse.move(tick["x"], box["y"] + box["height"] / 2)
    return tick


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_tick_hover_grows_the_line_and_shows_the_tooltip_at_once(live: str, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, scheme=scheme, cycle="h12")
        blk = page.locator(_FRAME_DINNER)
        blk.scroll_into_view_if_needed()
        line = """(t) => { const b = getComputedStyle(t, '::before'), a = getComputedStyle(t, '::after');
          const r = t.getBoundingClientRect();
          return {sx: new DOMMatrix(b.transform).a, op: parseFloat(b.opacity), fill: parseFloat(a.opacity), x: r.left, w: r.width}; }"""
        nxt = blk.locator(".lu-tick.next")
        rest = nxt.evaluate(line)
        assert rest["sx"] == 1 and rest["fill"] == 0, rest
        assert page.evaluate(_RECTS, blk.locator("xpath=..").element_handle())["tip"] is None
        _hover_tick(page, blk)
        # The tooltip is there within 100 ms, not after the native title's second.
        page.wait_for_selector(".lu-tip:not([hidden])", timeout=100)
        got = page.evaluate(_RECTS, blk.locator("xpath=..").element_handle())
        assert got["tip"]["text"] == "Next refresh 6:30\u202fPM", got
        assert got["tip"]["b"] <= got["trk"]["t"], got  # above the bar (and its flag)
        assert got["tip"]["l"] >= got["trk"]["l"] and got["tip"]["r"] <= got["trk"]["r"], got
        assert got["flag"] and got["tip"]["b"] <= got["flag"]["t"], got
        # The line grows: 2px to 4px wide, brighter, its gap filled; nothing moves.
        page.wait_for_timeout(250)
        hot = nxt.evaluate(line)
        assert hot["sx"] == 2 and hot["op"] > rest["op"] and hot["fill"] > 0.9, (rest, hot)
        assert (hot["x"], hot["w"]) == (rest["x"], rest["w"]), (rest, hot)
        # A later line grows too, and shows its own wording.
        page.evaluate(_DENSE_TICKS)
        kit = page.locator('.lu-ds[data-display="kitchen"] .lu-today .lu-blk.live')
        kit.scroll_into_view_if_needed()
        page.wait_for_timeout(120)
        later = kit.locator(".lu-tick:not(.next):not(.thin)").nth(3)
        box = later.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_selector(".lu-tip:not([hidden])", timeout=100)
        assert page.locator(".lu-tip").inner_text().startswith("Refresh ")
        page.wait_for_timeout(250)
        assert later.evaluate(line)["sx"] == 3
        # Leaving hides it; so does a press (a drag starts), and a redraw.
        page.mouse.move(2, 2)
        assert page.locator(".lu-tip").is_hidden()
        assert page._lu_errors == []
        browser.close()


def test_tick_hover_respects_reduced_motion(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        blk = page.locator(_FRAME_DINNER)
        blk.scroll_into_view_if_needed()
        got = blk.locator(".lu-tick.next").evaluate(
            "(t) => getComputedStyle(t, '::before').transitionDuration"
        )
        assert got != "0s"
        page.emulate_media(reduced_motion="reduce")
        got = blk.locator(".lu-tick.next").evaluate(
            "(t) => [getComputedStyle(t, '::before').transitionDuration, getComputedStyle(t, '::after').transitionDuration]"
        )
        assert got == ["0s", "0s"]
        browser.close()


@pytest.mark.parametrize("width", [1440, 390])
def test_next_tick_flag_sits_above_the_line_clear_of_the_now_marker(live: str, width: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, width=width, cycle="h12")
        trk = page.locator(_FRAME_BAR)
        trk.scroll_into_view_if_needed()
        # One flag per bar with a next advance, on that advance only.
        assert page.locator(".lu-nflag").count() == 2
        assert trk.locator(".lu-nflag").count() == 1
        got = page.evaluate(_RECTS, trk.element_handle())
        flag, knob, bar = got["flag"], got["knob"], got["trk"]
        # 18 minutes at load (the sample's clock is frozen; the page's runs on).
        assert re.fullmatch(r"Update in 1[78] min", flag["text"]), got
        assert flag["b"] <= bar["t"] and flag["t"] >= 0, got  # above the bar, on screen
        assert flag["l"] >= bar["l"] - 0.5 and flag["r"] <= bar["r"] + 0.5, got
        # The now marker (18 minutes before the line) never sits under it.
        overlap = (
            flag["l"] < knob["r"]
            and flag["r"] > knob["l"]
            and flag["t"] < knob["b"]
            and flag["b"] > knob["t"]
        )
        assert not overlap, got
        # The arrow points at the line.
        tick = _tick_xs(page.locator(_FRAME_BAR + " .lu-blk.live"))[0]
        arrow = page.evaluate(
            "() => { const f = document.querySelector('.lu-today .lu-nflag'); return f.getBoundingClientRect().left + parseFloat(f.style.getPropertyValue('--ax')); }"
        )
        assert abs(arrow - tick["x"]) < 1.5, (arrow, tick)
        # It never takes a click or a hover.
        assert (
            page.locator(".lu-nflag").first.evaluate("(f) => getComputedStyle(f).pointerEvents")
            == "none"
        )
        assert page._lu_errors == []
        browser.close()


def test_next_tick_flag_stays_inside_the_bar_at_its_right_end(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, cycle="h12")
        page.evaluate(_NEXT_AT_RIGHT_EDGE)
        trk = page.locator(_FRAME_BAR)
        trk.scroll_into_view_if_needed()
        blk = page.locator(_FRAME_DINNER.replace('[data-edit][data-lineup="dinner"]', ".live"))
        _hover_tick(page, blk)
        page.wait_for_selector(".lu-tip:not([hidden])", timeout=100)
        got = page.evaluate(_RECTS, trk.element_handle())
        bar, flag, tip = got["trk"], got["flag"], got["tip"]
        assert flag["r"] <= bar["r"] + 0.5 and flag["l"] >= bar["l"], got
        assert tip["r"] <= bar["r"] + 0.5 and tip["l"] >= bar["l"] and tip["r"] <= got["vw"], got
        # The arrow still points at the line, now near the flag's right end.
        tick = _tick_xs(blk)[0]
        arrow = page.evaluate(
            "() => { const f = document.querySelector('.lu-today .lu-nflag'); return f.getBoundingClientRect().left + parseFloat(f.style.getPropertyValue('--ax')); }"
        )
        assert abs(arrow - tick["x"]) < 1.5 and arrow > flag["l"] + (flag["r"] - flag["l"]) / 2, (
            arrow,
            tick,
            got,
        )
        # The tooltip's x-range is not inside the clipped block: it lives in <body>.
        assert page.evaluate(
            "() => document.querySelector('.lu-tip').parentElement === document.body"
        )
        browser.close()


def test_next_tick_flag_wording_and_visibility(live: str) -> None:
    from playwright.sync_api import sync_playwright

    set_in = """(sec) => {
      const vm = TesseraeLineups.vm;
      const d = vm.displays.find((x) => x.id === 'frame');
      const s = d.day.segments.find((x) => x.kind === 'window' && x.live);
      s.ticks.forEach((t) => { if (t.next) { t.in_sec = sec; t.in_min = Math.floor(sec / 60); } });
      TesseraeLineups.render();
      const f = document.querySelector('.lu-ds[data-display="frame"] .lu-today .lu-nflag');
      return f ? f.textContent : null;
    }"""
    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        for sec, want in (
            (20, "Update in under a minute"),
            (60, "Update in 1 min"),
            (300, "Update in 5 min"),
            (3900, "Update in 1 h 5 min"),
            (7200, "Update in 2 h"),
            (86399, "Update in 23 h 59 min"),
            (86400, None),
        ):
            assert page.evaluate(set_in, sec) == want, sec
        # The server and the page word every minute count alike.
        from app.lineups_view import update_in_label

        client = page.evaluate(
            "() => Array.from({length: 1500}, (_, m) => TesseraeLineupsRender.updateInLabel(m))"
        )
        assert client == [update_in_label(m) for m in range(1500)]
        # Neither another day's bar nor the timeline draws one.
        _timeline(page)
        assert page.locator(".lu-nflag").count() == 0
        assert page._lu_errors == []
        browser.close()


def test_next_tick_flag_counts_down_without_a_redraw(live: str) -> None:
    from datetime import datetime

    from playwright.sync_api import sync_playwright

    t0 = datetime(2026, 10, 28, 18, 12, 0)

    with sync_playwright() as p:

        def fake_clock(page: Any) -> None:
            page.clock.install(time=t0)
            # Keep the 60 s re-read from handing back the same 18 minutes.
            page.route(re.compile(r".*/decks/view\.json.*"), lambda r: r.abort())

        browser, page, _ = _open(p, live, before_load=fake_clock)
        page.clock.pause_at(datetime(2026, 10, 28, 18, 12, 5))
        # Redraw with 18.5 minutes to go (so no minute boundary is close), and
        # keep the element to prove later that only its text changes.
        page.evaluate(
            """() => {
              const d = TesseraeLineups.vm.displays.find((x) => x.id === 'frame');
              const s = d.day.segments.find((x) => x.kind === 'window' && x.live);
              s.ticks.forEach((t) => { if (t.next) { t.in_sec = 1110; t.in_min = 18; } });
              TesseraeLineups.render();
            }"""
        )
        page.clock.run_for(50)  # the layout pass stamps when the advance is due
        flag = page.locator(_FRAME_BAR + " .lu-nflag")
        assert flag.inner_text() == "Update in 18 min"
        page.evaluate("() => { window.__same = document.querySelector('.lu-today .lu-nflag'); }")
        # The page's 30 s pass moves the text on, a minute at a time.
        page.clock.run_for(125_000)
        assert flag.inner_text() == "Update in 16 min"
        page.clock.run_for(600_000)
        assert flag.inner_text() == "Update in 6 min"
        assert page.evaluate(
            "() => window.__same === document.querySelector('.lu-today .lu-nflag')"
        )
        # Once the advance has passed the flag hides until the next re-read.
        page.clock.run_for(420_000)
        assert flag.is_hidden()
        assert page._lu_errors == []
        browser.close()


def test_timeline_rows_fold_and_open(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        assert page.locator('.lu-ds[data-display="frame"] .lu-tlg-row').count() == 7
        assert page.locator(".lu-tlg-row.is-open").count() == 0  # all folded
        assert page.locator(".lu-ln, .lu-lanes").count() == 0  # no lanes section
        today = page.locator('.lu-ds[data-display="frame"] .lu-tlg-row.is-today')
        assert today.get_attribute("data-day") == "2"
        lab = page.locator('.lu-ds[data-display="frame"] [data-tl-row="2"]')
        assert lab.get_attribute("aria-expanded") == "false"
        folded_h = today.bounding_box()["height"]
        row = _open_day(page, "frame", 2)
        assert (
            "is-open" in row.get_attribute("class") and lab.get_attribute("aria-expanded") == "true"
        )
        assert row.bounding_box()["height"] > folded_h + 30
        # Every block in the row opened, not just the clicked one.
        blocks = row.locator(".lu-blk[tabindex]")
        assert blocks.count() >= 2
        assert blocks.evaluate_all(
            "bs => bs.every((b) => b.classList.contains('open') && b.getAttribute('aria-expanded') === 'true')"
        )
        # Today's live block carries the next advance; the now mark stays.
        assert row.locator(".lu-nowm").count() == 1
        assert row.locator(".lu-blk.live .lu-tnext-l").text_content().startswith("next ")
        # Esc folds the row holding focus; Enter on a block opens it again.
        lab.focus()
        page.keyboard.press("Escape")
        assert lab.get_attribute("aria-expanded") == "false"
        row.locator(".lu-blk.live").focus()
        page.keyboard.press("Enter")
        assert lab.get_attribute("aria-expanded") == "true"
        # A click on a block opens its row; several rows can be open.
        _row(page, "frame", 1).locator(".lu-blk[tabindex]").first.click()
        assert page.locator('.lu-ds[data-display="frame"] .lu-tlg-row.is-open').count() == 2
        # Week labels open their days without a second calendar control.
        assert page.locator('[data-dpick="frame"]').count() == 0
        page.locator('.lu-ds[data-display="frame"] [data-tl-row="5"]').click()
        sat = _row(page, "frame", 5)
        assert "is-open" in sat.get_attribute("class")
        assert page._lu_errors == []
        browser.close()


def test_timeline_fold_all_per_display_is_remembered(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        btn = page.locator('[data-tl-all="frame"]')
        assert btn.inner_text().strip() == "Unfold all"
        assert btn.locator("span").get_attribute("aria-live") == "polite"
        btn.click()
        page.wait_for_timeout(300)
        assert page.locator('.lu-ds[data-display="frame"] .lu-tlg-row.is-open').count() == 7
        assert page.locator('.lu-ds[data-display="kitchen"] .lu-tlg-row.is-open').count() == 0
        assert btn.inner_text().strip() == "Fold all"
        page.reload(wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        assert (
            page.locator('.lu-ds[data-display="frame"] .lu-tlg-row.is-open').count() == 7
        )  # view and rows kept
        page.locator('[data-tl-all="frame"]').click()
        assert page.locator('.lu-ds[data-display="frame"] .lu-tlg-row.is-open').count() == 0
        browser.close()


def test_timeline_open_row_shows_every_block_inside_its_edges(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        for display, day in (("frame", 0), ("frame", 2), ("kitchen", 2)):
            row = _open_day(page, display, day)
            report = row.evaluate(
                """(row) => [...row.querySelectorAll('.lu-blk.open')].map((b) => {
                    const br = b.getBoundingClientRect();
                    const shown = [...b.querySelectorAll('.lu-wb-th')].filter((t) => !t.hidden);
                    const more = b.querySelector('.lu-wb-more');
                    return {
                      name: b.dataset.lineup,
                      shown: shown.length,
                      more: more ? more.textContent : '',
                      gaps: shown.map((t) => { const r = t.getBoundingClientRect(); return [r.left - br.left, br.right - r.right]; }),
                      dividers: [...b.querySelectorAll('.lu-tick, .lu-ln-tick')].filter((t) => t.checkVisibility()).length,
                    };
                })"""
            )
            assert report, (display, day)
            for blk in report:
                assert blk["shown"] or blk["more"], blk  # every block shows its steps
                assert blk["dividers"] == 0, blk
                for left, right in blk["gaps"]:
                    assert left >= 9.5 and right >= 9.5, blk
        more = page.locator(".lu-blk.open .lu-wb-more").first
        assert re.match(r"\+\d+$", more.inner_text())
        assert re.search(r"All steps: .+ at \d\d:\d\d", more.get_attribute("title"))
        # A single push is a dot on the rod.
        dot = _row(page, "frame", 2).locator(".lu-blk.push").bounding_box()
        assert abs(dot["width"] - 10) < 1 and abs(dot["height"] - 10) < 1
        browser.close()


def test_timeline_hold_arrows_and_labels(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        folded = _row(page, "frame", 2)
        assert folded.locator(".lu-harrow").count() >= 2
        assert folded.locator(".lu-wb-hold").evaluate_all(
            "ls => ls.every((l) => getComputedStyle(l).display === 'none')"
        )
        _open_day(page, "frame", 2)
        boxes = page.evaluate(
            """() => [...document.querySelectorAll('.lu-ds[data-display="frame"] .lu-tlg-row.is-open .lu-wb-hold')]
                .filter((l) => getComputedStyle(l).display !== 'none').map((l) => {
                  const rod = l.closest('.lu-trk').querySelector('.lu-blk.hold').getBoundingClientRect();
                  const r = l.getBoundingClientRect();
                  return { text: l.textContent, gap: rod.top - r.bottom };
              })"""
        )
        assert boxes
        assert any("from Tue" in b["text"] for b in boxes)  # carried over midnight
        for b in boxes:
            assert b["gap"] >= 2, b  # above the rod, never on it
        # Ahead of now the arrow is dashed.
        assert (
            page.locator(
                '.lu-ds[data-display="frame"] .lu-tlg-row[data-day="3"] .lu-harrow.dash'
            ).count()
            >= 1
        )
        browser.close()


def test_timeline_drag_ghosts_other_days_and_shares_pending_edits(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        _timeline(page)
        row = _row(page, "frame", 0)
        blk = row.locator('.lu-blk[data-edit][data-lineup="dinner"]')
        blk.scroll_into_view_if_needed()
        trk = row.locator(".lu-trk").bounding_box()
        box = blk.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + trk["width"] * 52 / 1440, y + 3, steps=6)  # snaps to 45 min
        # The window belongs to the lineup: its other days (Tue, Wed) ghost it.
        assert page.locator('.lu-ds[data-display="frame"] .lu-ghost').count() == 2
        assert "applies to Mon, Tue, Wed" in page.locator(".lu-drag-tip").inner_text()
        page.mouse.up()
        assert page.locator(".lu-ghost").count() == 0
        assert "is-open" not in row.get_attribute("class")  # a drag never toggles the row
        pending = page.locator('.lu-ds[data-display="frame"] .lu-pending')
        assert "1 unsaved change" in pending.inner_text()
        # Every day of the lineup shows the change and the unsaved dot.
        assert (
            page.locator(
                '.lu-ds[data-display="frame"] .lu-blk[data-lineup="dinner"] .lu-edot'
            ).count()
            == 3
        )
        # The same pending edit shows in the Cards view, and saves from either.
        page.locator('[data-view="cards"]').click()
        assert page.locator("#lu-row-dinner .lu-win").inner_text() == "17:45–20:45"
        page.locator('.lu-ds[data-display="frame"] [data-save]').click()
        page.wait_for_function("() => !document.querySelector('.lu-pend-count')")
        assert api.posts[-1][1]["edits"] == [
            {"id": "dinner", "start": "17:45", "end": "20:45", "days": [0, 1, 2]}
        ]
        browser.close()


def test_timeline_press_that_travels_never_toggles(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        row = _row(page, "frame", 4)
        trk = row.locator(".lu-trk").bounding_box()
        x, y = trk["x"] + trk["width"] * 0.6, trk["y"] + trk["height"] / 2  # on a hold, not a block
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 12, y, steps=3)
        page.mouse.up()
        assert "is-open" not in row.get_attribute("class")
        page.mouse.click(x, y)
        assert "is-open" in row.get_attribute("class")
        browser.close()


def test_timeline_is_read_only_on_narrow_cards(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, width=600)
        _timeline(page)
        assert page.locator(".lu-tlg-trk .lu-blk[data-edit]").count() == 0
        assert page.locator(".lu-tlg-trk .lu-blk[tabindex]").count() > 0  # still opens rows
        browser.close()


@pytest.mark.parametrize("width", [360, 1024, 1440, 2736])
def test_timeline_has_no_page_level_sideways_scroll(live: str, width: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, width=width)
        _timeline(page)
        _open_day(page, "frame", 2)
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
        if width <= 1024:
            sc = page.locator('[data-tl-scroll="frame"]')
            assert sc.evaluate("e => e.scrollWidth > e.clientWidth")  # the grid scrolls itself
        browser.close()


@pytest.mark.parametrize("width", [320, 360, 390, 430])
def test_timeline_day_labels_keep_their_inset_on_phones(live: str, width: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(
            viewport={"width": width, "height": 800}, has_touch=True, is_mobile=True
        )
        page = ctx.new_page()
        _Api().install(page)
        page.goto(live + PAGE, wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        page.locator('[data-view="timeline"]').tap()
        sc = page.locator('[data-tl-scroll="frame"]')
        for scroll in (0, 400):  # pinned while the grid scrolls
            sc.evaluate(f"e => {{ e.scrollLeft = {scroll}; }}")
            labels = page.evaluate(
                """() => [...document.querySelectorAll('.lu-ds[data-display="frame"] .lu-tlg-lab')].map((e) => {
                    const c = e.closest('.lu-ds-card').getBoundingClientRect(), r = e.getBoundingClientRect();
                    const range = document.createRange(); range.selectNodeContents(e);
                    const t = range.getBoundingClientRect();
                    return { text: e.textContent, left: t.left - c.left, right: r.right - t.right, clipped: e.scrollWidth > e.clientWidth + 1 };
                })"""
            )
            assert len(labels) == 7
            for lab in labels:
                assert lab["left"] >= 12, (scroll, lab)
                assert lab["right"] >= 4, (scroll, lab)
                assert not lab["clipped"], lab
        cell = page.locator('.lu-ds[data-display="frame"] .lu-tlg-lab').nth(1)
        assert cell.evaluate("e => getComputedStyle(e).position") == "sticky"
        assert cell.evaluate("e => getComputedStyle(e).backgroundColor") not in (
            "rgba(0, 0, 0, 0)",
            "transparent",
        )
        assert cell.evaluate("e => getComputedStyle(e).boxShadow") != "none"  # its right edge
        assert page.evaluate("() => document.documentElement.scrollWidth - innerWidth") <= 0
        browser.close()


def test_filters_and_count(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        assert page.locator("[data-lu-toolbar]").is_visible()
        page.locator("[data-lu-q]").fill("breakfast")
        assert page.locator(".lu-row").count() == 1
        assert page.locator("[data-lu-count]").inner_text() == "1 of 6 lineups"
        page.locator("[data-lu-q]").fill("")
        page.select_option('[data-lu-filter="state"]', "live")
        assert sorted(
            page.eval_on_selector_all(".lu-row", "rs => rs.map(r => r.dataset.lineup)")
        ) == ["dinner", "kitchen"]
        browser.close()


# -- audit regressions ---------------------------------------------------------


def _shared(page: Any, api: _Api) -> None:
    # One lineup targeted at two displays. Keep the second display's other
    # windows out of the way so the same edit is valid on both.
    import copy

    other = copy.deepcopy(api.vm["displays"][0]["lineups"][0])
    assert other["id"] == "dinner"
    other["display_id"] = "kitchen"
    api.vm["displays"][1]["lineups"] = [other]
    api.vm["displays"][1]["week"] = copy.deepcopy(api.vm["displays"][0]["week"])
    api.vm["displays"][1]["day"] = api.vm["displays"][1]["week"][api.vm["selected_day"]]
    page.evaluate("() => TesseraeLineups.refresh()")
    page.wait_for_selector("#lu-row-dinner--kitchen")


def test_shared_lineup_actions_and_edits_use_their_display(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        _shared(page, api)
        other = page.locator('.lu-ds[data-display="kitchen"]')
        other.locator(".lu-tile").first.hover()
        other.locator('[data-push="dinner:0"]').click()
        page.wait_for_function("() => document.querySelector('#lu-row-dinner--kitchen .pushed')")
        assert api.posts[-1][1] == {
            "index": 0,
            "display_id": "kitchen",
            "composition_digest": "abc123def4567890",
        }
        other.locator('[data-toggle-day="dinner:0"]').click()
        assert page.locator('[data-toggle-day="dinner:0"][aria-pressed="false"]').count() == 2
        assert page.evaluate("() => Object.keys(TesseraeLineups.state.pending).sort()") == [
            "frame",
            "kitchen",
        ]
        assert other.locator('[data-toggle-day="dinner:0"]').evaluate(
            "e => e === document.activeElement"
        )
        other.locator('[data-win-edit="dinner"]').click()
        page.locator(".lu-win-edit input").first.fill("17:15")
        page.locator(".lu-win-edit input").first.press("Enter")
        assert page.locator('[data-win-edit="dinner"]').all_text_contents() == ["17:15–20:00"] * 2
        block = other.locator('.lu-today .lu-blk[data-edit][data-lineup="dinner"]')
        block.focus()
        page.keyboard.press("ArrowRight")
        assert other.locator('.lu-today .lu-blk[data-edit][data-lineup="dinner"]').evaluate(
            "e => e === document.activeElement"
        )
        assert page.locator('[data-win-edit="dinner"]').all_text_contents() == ["17:30–20:15"] * 2
        other.locator('[data-undo="kitchen"]').click()
        assert page.locator("[data-save]").count() == 0
        assert page.locator('[data-win-edit="dinner"]').all_text_contents() == ["17:00–20:00"] * 2
        other.locator('[data-peek="dinner:1"]').click()
        assert other.locator(".lu-peek").count() == 1
        page.keyboard.press("Escape")
        browser.close()


def test_refresh_survives_removed_editor_and_defers_busy_requests(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        page.locator('[data-win-edit="night"]').click()
        api.vm["displays"][0]["name"] = "Refreshed frame"
        page.evaluate("() => TesseraeLineups.refresh()")
        # A view switch redraws and removes the editor without Apply/Escape.
        page.locator('[data-view="timeline"]').click()
        page.wait_for_function(
            "() => document.querySelector('#display-frame h2').textContent === 'Refreshed frame'"
        )
        browser.close()


def test_refresh_follows_today_and_visibility(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        api.vm["now"]["weekday"] = 3
        api.vm["selected_day"] = 3
        with page.expect_request(lambda r: "/decks/view.json" in r.url) as request:
            page.evaluate("() => TesseraeLineups.refresh()")
        assert "?day=" not in request.value.url
        with page.expect_response(lambda r: "/decks/view.json" in r.url):
            page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        page.goto(live + PAGE + "?day=1", wait_until="load")
        with page.expect_request(lambda r: "/decks/view.json" in r.url) as request:
            page.evaluate("() => TesseraeLineups.refresh()")
        assert "?day=1" in request.value.url
        browser.close()


@pytest.mark.parametrize("status", [409, 500])
def test_save_errors_preserve_edits_and_show_reason(live: str, status: int) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.route(
            "**/decks/edits",
            lambda r: r.fulfill(
                status=status,
                content_type="application/json",
                body=json.dumps(
                    {
                        "ok": False,
                        "id": "night",
                        "error": "Schedule changed" if status == 409 else "Try again",
                    }
                ),
            ),
        )
        page.locator('[data-toggle-day="night:0"]').click()
        page.locator('[data-save="frame"]').click()
        page.wait_for_selector(".lu-row-err")
        assert "Not saved:" in page.locator(".lu-pend-msg").inner_text()
        assert page.locator('[data-save="frame"]').is_visible()
        assert page.evaluate("() => !!TesseraeLineups.state.pending.frame.night")
        browser.close()


def test_edits_during_save_and_stale_refresh_are_preserved(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.evaluate("""() => {
          window.finishRefresh = null; window.finishSave = null;
          TesseraeLineupsAPI.view = () => new Promise(r => window.finishRefresh = r);
          TesseraeLineupsAPI.saveEdits = () => new Promise(r => window.finishSave = r);
          window.oldVM = JSON.parse(JSON.stringify(TesseraeLineups.vm));
          TesseraeLineups.refresh();
        }""")
        page.locator('[data-toggle-day="night:0"]').click()
        page.locator('[data-save="frame"]').click()
        page.locator('[data-toggle-day="night:1"]').click()
        page.evaluate("() => window.finishSave({ok: true})")
        page.wait_for_function("() => !TesseraeLineups.state.saving.frame")
        assert page.evaluate(
            "() => TesseraeLineups.state.pending.frame.night.days.slice(0, 2)"
        ) == [False, False]
        assert (
            page.evaluate(
                "() => TesseraeLineups.vm.displays[0].lineups.find(l => l.id === 'night').days[0]"
            )
            is False
        )
        page.evaluate("() => window.finishRefresh(window.oldVM)")
        page.wait_for_timeout(100)
        assert (
            page.evaluate(
                "() => TesseraeLineups.vm.displays[0].lineups.find(l => l.id === 'night').days[0]"
            )
            is False
        )
        browser.close()


@pytest.mark.parametrize("width", [390, 1440])
@pytest.mark.parametrize("kind", ["day", "peek"])
def test_dialog_traps_focus_isolates_page_and_restores_opener(
    live: str, width: int, kind: str
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, width=width)
        opener = page.locator('[data-dpick="frame"]' if kind == "day" else '[data-peek="dinner:1"]')
        opener.click()
        pop = page.locator(".lu-dp-pop" if kind == "day" else ".lu-peek")
        assert pop.get_attribute("aria-modal") == "true"
        assert page.locator("[data-lu-q]").evaluate("e => !!e.closest('[inert]')")
        buttons = pop.locator("button:visible")
        buttons.last.focus()
        page.keyboard.press("Tab")
        assert buttons.first.evaluate("e => e === document.activeElement")
        page.keyboard.press("Shift+Tab")
        assert buttons.last.evaluate("e => e === document.activeElement")
        page.keyboard.press("Escape")
        assert opener.evaluate("e => e === document.activeElement")
        assert not page.locator("[data-lu-q]").evaluate("e => !!e.closest('[inert]')")
        browser.close()


def test_highlight_stays_unfolded_after_refresh(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.goto(live + PAGE + "?hl=night", wait_until="load")
        page.wait_for_timeout(3100)
        page.evaluate("() => TesseraeLineups.render()")
        assert "is-folded" not in page.locator("#lu-row-night").get_attribute("class")
        browser.close()


def test_initial_strip_scrolls_to_now(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, width=390)
        page.wait_for_function(
            "() => document.querySelector('#lu-row-dinner .lu-strip-scroll').scrollLeft > 0"
        )
        strip = page.locator("#lu-row-dinner .lu-strip-scroll")
        strip.evaluate("e => e.scrollLeft = 15")
        page.evaluate("() => TesseraeLineups.render()")
        assert strip.evaluate("e => e.scrollLeft") == 15
        browser.close()


def test_pinch_zoom_does_not_act_like_the_keyboard(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, width=390)
        page.evaluate("""() => {
          Object.defineProperty(visualViewport, 'height', {value: innerHeight - 250, configurable: true});
          visualViewport.dispatchEvent(new Event('resize'));
        }""")
        assert not page.locator("[data-lu]").evaluate("e => e.classList.contains('kb-open')")
        page.locator("[data-lu-q]").focus()
        page.evaluate("() => visualViewport.dispatchEvent(new Event('resize'))")
        assert page.locator("[data-lu]").evaluate("e => e.classList.contains('kb-open')")
        browser.close()


def test_timeline_redraw_restores_focus_in_the_same_display_and_day(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        _shared(page, api)
        _timeline(page)
        block = page.locator(
            '.lu-ds[data-display="kitchen"] .lu-tlg-row[data-day="1"] .lu-blk[data-lineup="dinner"][data-edit]'
        )
        block.focus()
        page.keyboard.press("ArrowRight")
        assert block.evaluate("e => e === document.activeElement")
        browser.close()


@pytest.mark.parametrize("view", ["cards", "timeline"])
def test_pending_markup_matches_direct_renderer(live: str, view: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.locator('[data-toggle-day="night:0"]').click()
        if view == "timeline":
            _timeline(page)
        expected, drawn = page.evaluate("""() => {
          const R = TesseraeLineupsRender, st = TesseraeLineups.state, vm = TesseraeLineups.vm;
          const ui = {view: st.view, seen: new Set(), folded: l => l.folded, collapsed: () => false,
            pendingBar: d => R.pendingBar(d, Object.keys(st.pending[d.id] || {}).length)};
          const expected = vm.displays.map(d => R.display(vm, R.withPending(d, st.pending[d.id], vm.now.weekday, vm.now.minute), ui));
          TesseraeLineups.render();
          return [expected, [...document.querySelectorAll('.lu-ds')].map(d => d.outerHTML)];
        }""")
        assert len(expected) == len(drawn) == 2
        for e, d in zip(expected, drawn, strict=True):
            assert _shape(e) == _shape(d)
        browser.close()


def test_orphans_notes_and_icons_match_both_renderers(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        import copy

        orphan = copy.deepcopy(api.vm["displays"][0]["lineups"][0])
        orphan.update(id="removed", display_removed=True, display_id="")
        orphan["warnings"] = ["cannot show here"]
        api.vm["unassigned"] = [orphan]
        api.vm["displays"][0]["show_suffix"] = "from a manual push · resumes 18:30"
        api.vm["displays"][0]["status_title"] = "3 min behind · poll ≈18:18"
        page.evaluate("() => TesseraeLineups.refresh()")
        page.wait_for_selector("#lu-row-removed")
        assert page.locator(".lu-ds--orphans h2").inner_text() == "Display removed"
        assert page.locator(".lu-ds--orphans [data-push]").count() == 0
        assert (
            "cannot show here" in page.locator("#lu-row-removed .lu-row-warning").first.inner_text()
        )
        assert "from a manual push" in page.locator("#display-frame .lu-show").inner_text()
        assert "3 min behind" in page.locator("#display-frame .lu-sdot").get_attribute("title")
        assert page.locator(".lu-show .lu-sr-only").all_text_contents() == [
            "Synced",
            "Waiting to land",
        ]
        _timeline(page)
        assert page.locator("[data-tl-all] svg path").count() == 2
        browser.close()


def test_reverting_to_original_during_save_keeps_the_revert_pending(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.evaluate(
            "() => { TesseraeLineupsAPI.saveEdits = () => new Promise(r => window.finishSave = r); }"
        )
        page.locator('[data-toggle-day="night:0"]').click()
        page.locator('[data-save="frame"]').click()
        page.locator('[data-toggle-day="night:0"]').click()
        page.evaluate("() => window.finishSave({ok:true})")
        page.wait_for_function("() => !TesseraeLineups.state.saving.frame")
        assert page.evaluate("() => TesseraeLineups.state.pending.frame.night.days[0]") is True
        assert page.locator('[data-toggle-day="night:0"]').get_attribute("aria-pressed") == "true"
        browser.close()


def test_push_now_shows_fresh_render_message(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, api = _open(p, live)
        api.push_message = "Sent a fresh render, the earlier picture is no longer stored"
        tile = page.locator('#lu-row-dinner .lu-step[data-step="0"] .lu-tile')
        tile.hover()
        page.locator('[data-push="dinner:0"]').click()
        page.wait_for_function("() => document.querySelector('#lu-row-dinner .pushed')")
        assert tile.locator(".lu-pushed-msg").inner_text() == api.push_message
        assert api.posts[-1][1]["composition_digest"] == "abc123def4567890"
        browser.close()


def test_refresh_keeps_decoded_thumbnail_nodes_and_urls(real: tuple[str, Flask]) -> None:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    base, app = real
    renders = Path(app.config["DATA_ROOT"]) / "core" / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    digest = "a" * 64
    Image.new("RGB", (1440, 2560), "white").save(renders / f"{digest}.png")
    app.config["EVENT_LOG"].record(
        type="push", source="page", target="news", status="sent", digest=digest
    )
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base + "/decks", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        page.wait_for_function(
            "() => [...document.querySelectorAll('.lu-thumb img')].some(x => x.complete && x.naturalWidth)"
        )
        page.evaluate(
            "window.savedThumbs = [...document.querySelectorAll('.lu-thumb img')]; window.savedSrcs = savedThumbs.map(x => x.src)"
        )
        with page.expect_response(lambda r: "/decks/view.json" in r.url):
            page.evaluate("window.TesseraeLineups.refresh()")
        page.wait_for_timeout(150)
        assert page.evaluate("savedThumbs.every((x, i) => x.isConnected && x.src === savedSrcs[i])")
        assert page.locator("iframe").count() == 0
        assert page.locator('.lu-thumb img:not([decoding="async"])').count() == 0
        browser.close()


def test_cold_thumbnail_swaps_initial_for_background_snapshot(
    real: tuple[str, Flask], monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    base, _app = real
    queued: list[dict[str, Any]] = []
    monkeypatch.setattr("app.preview_cache.submit", lambda **kw: queued.append(kw))
    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(base + "/decks", wait_until="load")
        page.wait_for_function(
            "() => document.querySelector('[data-lu]').classList.contains('is-enhanced')"
        )
        assert page.locator(".lu-thumb img").count() == 0
        assert page.locator(".lu-thumb-ph").count() > 0
        assert page.locator("iframe").count() == 0
        assert queued
        for task in queued:
            path = task["cache_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (800, 500), "blue").save(path, "PNG")
        # No manual refresh: the bounded follow-up sees the ready snapshot.
        page.wait_for_function(
            "() => [...document.querySelectorAll('.lu-thumb img')].some(x => x.complete && x.naturalWidth)",
            timeout=10000,
        )
        assert page.locator("iframe").count() == 0
        assert page.locator('.lu-thumb img:not([loading="lazy"])').count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()


def test_preview_filters_follow_display_color_and_preserve_quantised_media(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        result = page.evaluate("""() => {
          const media = (selector, quantised = false) => {
            const box = document.querySelector(selector);
            const img = document.createElement('img');
            if (quantised) img.dataset.deviceQuantised = '';
            box.appendChild(img);
            return getComputedStyle(img).filter;
          };
          return {
            grey: media('#display-frame .lu-thumb'),
            color: media('#display-kitchen .lu-thumb'),
            quantised: media('#display-frame .lu-thumb', true),
            monoDither: (() => {
              const img = document.createElement('img');
              img.dataset.gamut = 'mono-dither';
              document.body.appendChild(img);
              return getComputedStyle(img).filter;
            })(),
          };
        }""")
        assert result == {
            "grey": "grayscale(1)",
            "color": "none",
            "quantised": "none",
            "monoDither": "grayscale(1)",
        }
        # Detached gallery popovers and timeline holds/steps use the same display,
        # even when a dashboard/lineup is shared with a color panel.
        markup = page.evaluate("""() => {
          const vm = JSON.parse(document.querySelector('[data-lu-vm]').textContent);
          return vm.displays.map(d => {
            const lu = d.lineups.find(l => l.steps.some(s => s.item_count > 1));
            const step = lu.steps.find(s => s.item_count > 1);
            return {gamut: d.gamut,
              peek: TesseraeLineupsRender.peek(d, lu, step),
              timeline: TesseraeLineupsRender.timeline(vm, d, {tlOpen: () => true})};
          });
        }""")
        for display in markup:
            assert f'data-gamut="{display["gamut"]}"' in display["peek"]
            assert f'data-gamut="{display["gamut"]}"' in display["timeline"]
        assert page._lu_errors == []
        browser.close()


def test_mono_filter_thresholds_pixels(live: str) -> None:
    import io

    from PIL import Image
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.evaluate("""() => {
          const img = document.createElement('img');
          img.id = 'mono-test';
          img.dataset.gamut = 'mono';
          img.width = 20;
          img.height = 20;
          img.style.cssText = 'position:fixed;top:0;left:0;z-index:9999';
          img.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><path fill="red" d="M0 0h10v20H0z"/><path fill="white" d="M10 0h10v20H10z"/></svg>';
          document.body.appendChild(img);
        }""")
        img = page.locator("#mono-test")
        page.wait_for_function("document.querySelector('#mono-test').complete")
        pixels = Image.open(io.BytesIO(img.screenshot())).convert("RGB")
        assert pixels.getpixel((5, 10)) == (0, 0, 0)
        assert pixels.getpixel((15, 10)) == (255, 255, 255)
        browser.close()
        browser.close()


def test_timeline_block_thumbs_share_one_gap(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for width, ui in ((1440, "paper"), (1440, "classic"), (390, "paper")):
            browser, page, _ = _open(p, live, width=width)
            if ui == "classic":
                page.evaluate("() => document.documentElement.removeAttribute('data-ui')")
            _timeline(page)
            gaps: list[float] = []
            for display, day in (("frame", 0), ("frame", 2), ("kitchen", 2)):
                row = _open_day(page, display, day)
                report = row.evaluate(
                    """(row) => [...row.querySelectorAll('.lu-blk.open')].map((b) => {
                        const br = b.getBoundingClientRect();
                        const els = [...b.querySelectorAll('.lu-wb-th, .lu-wb-more')]
                          .filter((t) => !t.hidden)
                          .map((t) => t.getBoundingClientRect())
                          .sort((a, c) => a.left - c.left);
                        return {
                          name: b.dataset.lineup,
                          first: els.length ? els[0].left - br.left - b.clientLeft : null,
                          gaps: els.slice(1).map((r, i) => r.left - els[i].right),
                        };
                    })"""
                )
                for blk in report:
                    if blk["first"] is not None:
                        assert abs(blk["first"] - 10) < 1, (width, ui, blk)  # left-aligned
                    gaps += blk["gaps"]
            assert len(gaps) >= 4, (width, ui, gaps)
            assert max(gaps) - min(gaps) <= 1, (width, ui, gaps)  # one gap everywhere
            assert page._lu_errors == []
            browser.close()
        browser.close()


_INJECT_STEPS = """([steps, total, width]) => {
  // Today's window on the frame, refreshed at the given (dashboard, name,
  // minutes after its start, flag) steps; flag is 'current' or 'next'.
  const vm = TesseraeLineups.vm;
  const d = vm.displays.find((x) => x.id === 'frame');
  const s = d.week[vm.now.weekday].segments.find((x) => x.kind === 'window');
  s.points = [];
  s.steps = steps.map(([id, name, off, flag], i) => ({
    index: i, dashboard_id: id, name, thumb_url: '', aspect: '16/10',
    at_min: s.start_min + off, at_label: '', current: flag === 'current', next: flag === 'next',
  }));
  s.steps_total = total || s.steps.length;
  if (width) { s.left_pct = 2; s.width_pct = width; }
  window._lu_inj = s.lineup_id;
  TesseraeLineups.render();
  return vm.now.weekday;
}"""

_BLOCK_REPORT = """(row) => {
  const b = row.querySelector(`.lu-blk.open[data-lineup="${window._lu_inj}"]`);
  const br = b.getBoundingClientRect();
  const rep = b.querySelector('.lu-wb-rep');
  const th = [...b.querySelectorAll('.lu-wb-th')].filter((t) => !t.hidden);
  const more = b.querySelector('.lu-wb-more');
  const vis = rep && !rep.hidden && getComputedStyle(rep).display !== 'none';
  const flag = b.querySelector('.lu-tnext-l');
  return {
    thumbs: th.length,
    roles: th.map((t) => t.dataset.role),
    titles: th.map((t) => t.title),
    aria: th.map((t) => t.getAttribute('aria-label')),
    rep: vis ? rep.textContent : null,
    repTitle: rep ? rep.title : null,
    more: more ? more.textContent : null,
    flag: flag ? flag.textContent : null,
    first: th.length ? th[0].getBoundingClientRect().left - br.left - b.clientLeft : null,
    gap: vis ? rep.getBoundingClientRect().left - th[0].getBoundingClientRect().right : null,
    inside: vis ? br.right - rep.getBoundingClientRect().right : null,
  };
}"""


def _block_with(
    page: Any, steps: list[list[Any]], total: int = 0, width: int = 70
) -> dict[str, Any]:
    day = page.evaluate(_INJECT_STEPS, [steps, total, width])
    row = _row(page, "frame", day)
    if "is-open" in (row.get_attribute("class") or ""):
        page.wait_for_timeout(350)  # the redraw's layout pass
    else:
        row = _open_day(page, "frame", day)
    report: dict[str, Any] = row.evaluate(_BLOCK_REPORT)
    return report


def _quotes(*offsets: int) -> list[list[Any]]:
    return [["q", "Quotes", m] for m in offsets]


def test_timeline_single_dashboard_block_collapses_to_one_thumb_and_badge(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for width, scheme in ((1440, "dark"), (1440, "light"), (390, "dark")):
            browser, page, _ = _open(p, live, width=width, scheme=scheme)
            _timeline(page)
            r = _block_with(page, _quotes(0, 30, 60, 90, 120))
            assert r["thumbs"] == 1 and r["more"] is None, r
            assert r["rep"] is not None and r["rep"].startswith("\u00d75"), r
            title = r["titles"][0]
            assert title.startswith("Quotes, updates at "), r
            assert title.count(",") == 5 and "+" not in title, r  # five times, no overflow
            assert r["aria"] == r["titles"] and r["repTitle"] == title, r
            assert abs(r["first"] - 10) < 1, r
            if r["rep"]:
                assert abs(r["gap"] - 6) < 1 and r["inside"] >= 9, (
                    r
                )  # --lu-wb-gap, inside the block
            # A block too narrow for the badge keeps the one thumb and its tooltip.
            r = _block_with(page, _quotes(0, 30, 60), width=8)
            assert r["thumbs"] == 1 and r["rep"] is None, r
            assert r["titles"][0].startswith("Quotes, updates at "), r
            assert page._lu_errors == []
            browser.close()


def test_timeline_collapsed_tooltip_lists_six_times_then_plus_more(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live, cycle="h12")
        _timeline(page)
        r = _block_with(page, _quotes(*range(0, 40, 5)))
        title = r["titles"][0]
        assert title.startswith("Quotes, updates at ") and title.endswith(", +2 more"), title
        assert len(re.findall(r"[AP]M", title)) == 6, title  # the app's 12-hour clock
        assert r["rep"] and r["rep"].startswith("\u00d78"), r
        # steps_total counts refreshes past the view-model's 60-step cap.
        r = _block_with(page, _quotes(*range(0, 40, 5)), total=70)
        assert r["rep"].startswith("\u00d770") and r["titles"][0].endswith(", +64 more"), r
        browser.close()


def test_timeline_block_with_one_refresh_or_two_dashboards_keeps_its_thumbs(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        r = _block_with(page, _quotes(0))
        assert r["thumbs"] == 1 and r["rep"] is None, r  # "\u00d71" is never shown
        assert re.fullmatch(r"Quotes at .+", r["titles"][0]), r
        mixed = [["a", "Alpha", 0], ["b", "Beta", 30], ["a", "Alpha", 60], ["b", "Beta", 90]]
        r = _block_with(page, mixed)
        assert r["thumbs"] == 4 and r["rep"] is None, r  # the A,B,A,B rotation stays visible
        assert [t.split(" at ")[0] for t in r["titles"]] == ["Alpha", "Beta", "Alpha", "Beta"], r
        browser.close()


def test_timeline_collapsed_block_keeps_now_and_next_marks(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        _timeline(page)
        steps = [["q", "Quotes", 0, "current"], ["q", "Quotes", 30, "next"], ["q", "Quotes", 60]]
        r = _block_with(page, steps)
        assert r["thumbs"] == 1 and r["roles"] == ["now"], r
        assert r["rep"].startswith("\u00d73"), r
        assert r["flag"] and r["flag"].startswith("next "), r  # the "next HH:MM" flag stays
        r = _block_with(page, [["q", "Quotes", 0, "next"], ["q", "Quotes", 30]])
        assert r["roles"] == ["next"] and r["rep"].startswith("\u00d72"), r
        browser.close()


# -- Today | All ---------------------------------------------------------------

_SCOPE_KEY = "tesserae.lineups.scope"
_FRAME_TODAY = ["dinner", "night"]  # Wed: live, later today; breakfast already ran


def _rows(page: Any) -> list[str]:
    return page.eval_on_selector_all(
        ".lu-row", "rs => rs.filter(r => r.offsetParent !== null).map(r => r.dataset.lineup)"
    )


def test_scope_default_markup_is_all_and_unfiltered(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, nojs, _ = _open(p, live, js=False)
        assert nojs.locator("[data-lu-boot-scope]").count() == 0
        assert len(_rows(nojs)) == 6
        browser.close()
        browser, page, _ = _open(p, live)
        assert page.locator('[data-scope="all"]').get_attribute("aria-pressed") == "true"
        assert page.locator('[data-scope="today"]').get_attribute("aria-pressed") == "false"
        assert len(_rows(page)) == 6
        browser.close()


def test_scope_today_hides_rows_not_running_on_the_day_and_remembers(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.locator('[data-scope="today"]').click()
        assert sorted(_rows(page)) == sorted([*_FRAME_TODAY, "kitchen"])
        assert page.locator('[data-scope="today"]').get_attribute("aria-pressed") == "true"
        assert page.locator("[data-lu-count]").inner_text() == "3 of 6 lineups"
        assert page.evaluate(f"() => localStorage.getItem('{_SCOPE_KEY}')") == "today"
        # Remembered across a reload; the first-paint attribute hands over to the script.
        page.reload(wait_until="load")
        page.wait_for_selector(".lu.is-enhanced")
        assert sorted(_rows(page)) == sorted([*_FRAME_TODAY, "kitchen"])
        assert page.locator("[data-lu-boot-scope]").count() == 0
        page.locator('[data-scope="all"]').click()
        assert len(_rows(page)) == 6
        assert page.evaluate(f"() => localStorage.getItem('{_SCOPE_KEY}')") == "all"
        browser.close()


def test_scope_today_hides_a_row_whose_run_today_is_finished(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        frame = '.lu-ds[data-display="frame"] '
        # Breakfast runs on Wednesdays but already finished: shown on All, not on Today.
        assert page.locator(f"{frame}#lu-row-breakfast").count() == 1
        page.locator('[data-scope="today"]').click()
        assert page.locator(f"{frame}#lu-row-breakfast").count() == 0
        assert "breakfast" not in _rows(page)
        # Another day the picker moves to keeps the "runs on that day" rule (Thursday).
        page.locator(f"{frame}[data-dpick]").click()
        page.locator(".lu-dp-day").nth(3).click()
        thursday = page.eval_on_selector_all(
            f"{frame}.lu-row", "rs => rs.map(r => r.dataset.lineup)"
        )
        assert "breakfast" in thursday
        page.locator('[data-scope="all"]').click()
        assert page.locator(f"{frame}#lu-row-breakfast").count() == 1
        browser.close()


def test_scope_today_paints_filtered_before_the_script_runs(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.evaluate(f"() => localStorage.setItem('{_SCOPE_KEY}', 'today')")
        page.route(re.compile(r".*/static/lineups(_render|_api)?\.js.*"), lambda r: r.abort())
        page.goto(live + PAGE, wait_until="load")
        assert page.locator("[data-lu-boot-scope=today]").count() == 1
        assert sorted(_rows(page)) == sorted([*_FRAME_TODAY, "kitchen"])
        browser.close()


def test_scope_follows_the_day_picker_and_survives_redraws(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.locator('[data-scope="today"]').click()
        page.locator("#lu-row-dinner .lu-fold").click()  # fold state must survive
        folded = page.locator("#lu-row-dinner").get_attribute("class")
        assert "is-folded" in folded
        page.evaluate("() => TesseraeLineups.refresh()")
        page.wait_for_timeout(300)
        assert sorted(_rows(page)) == sorted([*_FRAME_TODAY, "kitchen"])
        assert "is-folded" in page.locator("#lu-row-dinner").get_attribute("class")
        # Pick Saturday on the frame display: the weekend lineup runs, monday and dinner do not.
        page.locator('.lu-ds[data-display="frame"] [data-dpick]').click()
        page.locator(".lu-dp-day").nth(5).click()
        frame = page.eval_on_selector_all(
            '.lu-ds[data-display="frame"] .lu-row',
            "rs => rs.map(r => r.dataset.lineup)",
        )
        assert sorted(frame) == ["night", "weekend"]
        browser.close()


def test_scope_empty_state_offers_show_all(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.locator('[data-scope="today"]').click()
        page.evaluate(
            """() => {
              const d = TesseraeLineups.vm.displays[0];
              d.lineups.forEach((l) => { l.days = l.days.map(() => false); l.status = 'not_today'; });
              TesseraeLineups.render();
            }"""
        )
        empty = page.locator('.lu-ds[data-display="frame"] .lu-scope-empty')
        assert "Nothing scheduled for today" in empty.inner_text()
        assert page.locator('.lu-ds[data-display="frame"] .lu-row').count() == 0
        empty.get_by_role("button", name="Show all").click()
        assert page.locator('.lu-ds[data-display="frame"] .lu-row').count() == 5
        assert page.locator('[data-scope="all"]').evaluate("e => e === document.activeElement")
        browser.close()


def test_scope_toggle_is_keyboard_operable_and_hides_in_timeline(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page, _ = _open(p, live)
        page.locator('[data-scope="today"]').focus()
        page.keyboard.press("Enter")
        assert page.locator('[data-scope="today"]').get_attribute("aria-pressed") == "true"
        page.locator('[data-scope="all"]').focus()
        page.keyboard.press("Space")
        assert page.locator('[data-scope="all"]').get_attribute("aria-pressed") == "true"
        page.locator('[data-scope="today"]').click()
        page.locator('[data-view="timeline"]').click()
        assert not page.locator("[data-lu-scope-seg]").is_visible()
        assert page.locator("[data-lu-count]").inner_text() == "6 lineups"
        page.locator('[data-view="cards"]').click()
        assert page.locator("[data-lu-scope-seg]").is_visible()
        assert len(_rows(page)) == 3
        browser.close()


def test_scope_works_when_storage_is_blocked(live: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.add_init_script(
            "Object.defineProperty(window, 'localStorage', {get(){throw new Error('blocked')}})"
        )
        page = ctx.new_page()
        _Api().install(page)
        page.goto(live + PAGE, wait_until="load")
        page.wait_for_selector(".lu.is-enhanced")
        page.locator('[data-scope="today"]').click()
        assert len(_rows(page)) == 3
        browser.close()
