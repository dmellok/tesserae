"""The Lineups page template (templates/decks.html + _lineups.html) drawn from
the view-model's mockup sample (app.lineups_view.sample_view_model: Wednesday
18:12, a connected Visionect frame in sync and a sleeping kitchen display with
a queued step). Everything here is the server-rendered markup a browser shows
without JavaScript."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, cast

import pytest
from flask import Flask, render_template

from app.lineups_view import (
    SAMPLE_NOW,
    build_view,
    sample_inputs,
    sample_view_model,
    update_in_label,
)
from app.main import REPO_ROOT, create_app

# Whitespace-normalised, so rules read the same however Prettier wraps them.
CSS = re.sub(r"\s+", " ", (REPO_ROOT / "static" / "style" / "dx-lineups.css").read_text())


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
    )
    a.config["TESTING"] = True
    return a


def _render(app: Flask, vm: dict[str, Any] | None, **ctx: Any) -> str:
    with app.test_request_context("/decks"):
        if vm is None:
            return render_template("decks.html", automation_url="#auto", **ctx)
        return render_template("decks.html", vm=vm, automation_url="#auto", **ctx)


@pytest.fixture
def page(app: Flask) -> str:
    return _render(app, sample_view_model().to_dict())


def _vm_display(display_id: str) -> dict[str, Any]:
    return next(d for d in sample_view_model().to_dict()["displays"] if d["id"] == display_id)


def _next_line(d: dict[str, Any]) -> str:
    nl = d["next_line"]
    return (
        str(nl["before"])
        + (f" <b>{nl['at']}</b>" if nl["at"] else "")
        + (f" {nl['after']}" if nl["after"] else "")
    )


def _vm_lineup(lineup_id: str) -> dict[str, Any]:
    vm = sample_view_model().to_dict()
    for d in vm["displays"]:
        for lu in d["lineups"]:
            if lu["id"] == lineup_id:
                return cast(dict[str, Any], lu)
    raise KeyError(lineup_id)


def _section(body: str, display_id: str) -> str:
    m = re.search(
        rf'<section class="lu-ds[^"]*" data-display="{display_id}".*?</section>', body, re.S
    )
    assert m, display_id
    return m.group(0)


def _row(body: str, lineup_id: str) -> str:
    m = re.search(rf'<li class="lu-row[^"]*" id="lu-row-{lineup_id}".*?</div>\s*</li>', body, re.S)
    assert m, lineup_id
    return m.group(0)


def _row_open_tag(body: str, lineup_id: str) -> str:
    m = re.search(rf'<li class="(lu-row[^"]*)" id="lu-row-{lineup_id}"', body)
    assert m, lineup_id
    return m.group(1)


# -- states -------------------------------------------------------------------


def test_live_later_today_and_not_today_rows(page: str) -> None:
    assert _row_open_tag(page, "dinner").split()[:2] == ["lu-row", "is-live"]
    assert _row_open_tag(page, "night").split()[:2] == ["lu-row", "is-later"]
    for lid in ("breakfast", "weekend", "monday"):
        assert "is-off" in _row_open_tag(page, lid)
    dinner = _row(page, "dinner")
    assert '<span class="lu-tag-st live"><i class="lu-dot pulse"></i>Live</span>' in dinner
    prog = _vm_lineup("dinner")["progress"]
    assert f"ends in <b>{prog['ends_in_label']}</b>" in dinner
    assert 'role="progressbar"' in dinner and f'aria-valuenow="{prog["window_pct"]}"' in dinner
    night = _row(page, "night")
    assert '<span class="lu-tag-st later">Later today</span>' in night
    n = _vm_lineup("night")
    assert f"in <b>{n['progress']['starts_in_label']}</b>" in night
    assert f">{n['window']['label']}<" in night
    # Not today: the tag names the days it runs, the state says when next.
    for lid in ("breakfast", "weekend", "monday"):
        lu = _vm_lineup(lid)
        assert f'<span class="lu-tag-st off">{lu["status_label"]}</span>' in _row(page, lid)
        assert f"next {lu['next_run']['day_label']}" in _row(page, lid)


def test_rows_keep_the_builders_order(page: str) -> None:
    frame = _section(page, "frame")
    ids = re.findall(r'id="lu-row-([a-z]+)"', frame)
    assert ids == ["dinner", "night", "breakfast", "weekend", "monday"]


def test_day_tiles_and_facts(page: str) -> None:
    dinner = _row(page, "dinner")
    tiles = re.findall(
        r'<button type="button" class="lu-dt([^"]*)" data-toggle-day="dinner:(\d)" aria-pressed="(\w+)"',
        dinner,
    )
    assert [(i, p) for _, i, p in tiles] == [
        ("0", "true"), ("1", "true"), ("2", "true"),
        ("3", "false"), ("4", "false"), ("5", "false"), ("6", "false"),
    ]  # fmt: skip
    assert "is-today" in tiles[2][0] and "is-today" not in tiles[0][0]
    assert 'title="6 dashboards"' in dinner
    assert 'title="Advances every 30 min"' in dinner and "<span>30m</span>" in dinner
    assert 'class="lu-im opt" title="Full cycle 3h"' in dinner
    night = _row(page, "night")
    assert "1 dashboard: Night sky, pushed once, then held" in night
    # A window that runs into Quiet hours carries the moon chip.
    for lid in ("dinner", "night", "breakfast", "weekend", "monday", "kitchen"):
        w = _vm_lineup(lid)["window"]
        row = _row(page, lid)
        assert ('class="lu-im warn"' in row) == w["overlaps_pause"], lid
        if w["overlaps_pause"]:
            assert html.escape(w["pause_title"], quote=True) in row or w["pause_title"] in row
    assert _vm_lineup("monday")["window"]["overlaps_pause"]


def test_display_header(page: str) -> None:
    frame = _section(page, "frame")
    hdr = frame[: frame.index("</header>")]
    assert "<h2>Visionect frame</h2>" in hdr
    fd = _vm_display("frame")
    for tag in (
        fd["link"]["label"],
        fd["size_label"],
        fd["pause"]["label"],
        str(fd["refreshes_today"]),
    ):
        assert f"<span>{tag}</span>" in hdr
    assert '<p class="lu-show sync"' in hdr and "<b>Family</b>" in hdr
    assert _next_line(fd) in hdr and fd["next_line"]["before"] == "AI News next at"
    # The now thumbnail sits in a fixed slot in the display's own shape.
    assert re.search(
        r'<figure class="lu-now-thumb" title="On screen: Family"><span class="lu-thumb p" style="--ar:1440/2560',
        hdr,
    )
    # The fold chevron is the header's last control, tied to the body.
    assert 'data-collapse="frame" aria-expanded="true" aria-controls="lu-dsb-frame"' in hdr
    assert 'id="lu-dsb-frame"' in frame


def test_day_bar_is_prerendered(page: str) -> None:
    frame = _section(page, "frame")
    bar = re.search(
        r'<div class="lu-trk tall flagged".*?</div>\s*(?=<div class="lu-scale)', frame, re.S
    )
    assert bar
    b = bar.group(0)
    assert 'aria-label="Wed: Breakfast 06:30, Dinner (Mon–Wed) 17:00, Night 22:00"' in b
    # The live window wears the accent class; holds are rods; a push is a sliver.
    assert re.search(
        r'class="lu-blk live" style="left:70\.8333%;width:12\.5%" data-lineup="dinner"', b
    )
    assert b.count('class="lu-blk hold"') == 4  # night, breakfast, dinner, night
    assert re.search(r'class="lu-blk push" [^>]*data-lineup="night"', b)
    # Every non-hold block is a drag/keyboard target with resize handles.
    assert b.count('data-edit tabindex="0"') == 3
    assert b.count('<i class="lu-h" data-h="l"') == 2  # the push has no handles
    # The next advance tick on the live block, Quiet hours with the moon, now.
    # Every advance today is a titled split line, the next one worded as such;
    # earlier ones are gone.
    ticks = re.findall(r'<i class="lu-tick[^"]*"[^>]*></i>', b)
    assert ticks == [
        '<i class="lu-tick next" style="left:50.0%" data-tip="Next refresh 18:30" aria-hidden="true"></i>',
        '<i class="lu-tick" style="left:66.6667%" data-tip="Refresh 19:00" aria-hidden="true"></i>',
        '<i class="lu-tick" style="left:83.3333%" data-tip="Refresh 19:30" aria-hidden="true"></i>',
    ]
    assert (
        b.count('<i class="lu-pz"') == 2
        and html.escape(_vm_display("frame")["day"]["pause"][0]["title"]) in b
    )
    assert '<i class="lu-nowm" style="left:75.8333%" title="Now 18:12">' in b
    assert '<span class="lu-blk-side"' in b  # Night's label beside its push
    # Today needs no "back to today" label; the calendar button opens the picker.
    assert "lu-dp-label" not in frame
    assert (
        'data-dpick="frame" aria-haspopup="dialog" aria-expanded="false" aria-label="Pick a day; showing Wed · today"'
        in frame
    )


def _scale(frame: str) -> str:
    m = re.search(r'<div class="lu-scale[^"]*" aria-hidden="true">.*?</div>', frame, re.S)
    assert m
    return m.group(0)


def test_day_bar_scale_shows_block_starts_past_two_blocks(app: Flask) -> None:
    # Wed: Breakfast, Dinner and the Night push are three blocks, so the
    # scale names each start under its block (holds are not blocks).
    wed = _scale(_section(_render(app, sample_view_model().to_dict()), "frame"))
    assert wed == (
        '<div class="lu-scale st" aria-hidden="true">'
        '<span style="--x:27.0833%;--n:5">06:30</span>'
        '<span style="--x:70.8333%;--n:5">17:00</span>'
        '<span style="--x:91.6667%;--n:5">22:00</span>'
        "</div>"
    )
    # Two blocks (Thu) or one (the kitchen) keep the plain axis.
    thu = _render(app, sample_view_model(selected_day=3).to_dict())
    for frame in (_section(thu, "frame"), _section(thu, "kitchen")):
        assert _scale(frame) == (
            '<div class="lu-scale" aria-hidden="true"><span style="left:0.0%">00</span>'
            '<span style="left:25.0%">06</span><span style="left:50.0%">12</span>'
            '<span style="left:75.0%">18</span><span style="left:100.0%">24</span></div>'
        )


def test_block_start_tiers_match_the_css() -> None:
    # Every layout tier has the container query that applies it, and only it.
    from app.lineups_view import SCALE_TIERS

    t = SCALE_TIERS
    assert f"@container lu-scale (width < {t[1]}ch) {{ .lu .lu-scale.st .h0 {{" in CSS
    for i in range(1, len(t) - 1):
        q = f"@container lu-scale ({t[i]}ch <= width < {t[i + 1]}ch) {{ .lu .lu-scale.st .h{i} {{"
        assert q in CSS
    assert f"@container lu-scale (width >= {t[-1]}ch) {{ .lu .lu-scale.st .h{len(t) - 1} {{" in CSS
    assert CSS.count("@container lu-scale") == len(t)
    js = (REPO_ROOT / "static" / "lineups_render.js").read_text()
    assert f"const SCALE_TIERS = [{', '.join(map(str, t))}];" in js


def test_another_day_shows_back_to_today(app: Flask) -> None:
    body = _render(app, sample_view_model(selected_day=5).to_dict())
    frame = _section(body, "frame")
    assert re.search(
        r'<button type="button" class="lu-dp-label" data-day="2" data-display="frame"[^>]*>Sat · in 3 days',
        frame,
    )
    # A planned day has neutral blocks, no live accent, no now mark.
    bar = frame[frame.index('<div class="lu-trk tall"') : frame.index('<div class="lu-scale')]
    assert "lu-blk plan" in bar and " live" not in bar and "lu-nowm" not in bar
    assert "lu-nflag" not in bar and "flagged" not in bar
    # Every advance of another day is a faint line; none is "next".
    assert bar.count('<i class="lu-tick" ') == 4 and "lu-tick next" not in bar


def test_tick_titles_follow_the_clock_style(app: Flask) -> None:
    """A tick's hover tooltip is the advance time in the resolved clock style
    (data-tip: the page shows it instantly, never as a slow native title)."""
    for cycle, nxt, later in (
        ("h23", "Next refresh 18:30", "Refresh 19:00"),
        ("h12", "Next refresh 6:30\u202fPM", "Refresh 7\u202fPM"),
    ):
        out = _render(app, sample_view_model(time_cycle=cycle).to_dict())
        titles = re.findall(r'<i class="lu-tick[^"]*"[^>]*data-tip="([^"]+)"', out)
        assert nxt in titles and later in titles, (cycle, titles[:4])
        assert all(t.startswith(("Next refresh ", "Refresh ")) for t in titles)


def test_next_tick_flag_is_prerendered(page: str) -> None:
    """Over the next advance sits an "Update in ..." flag, once, on the live
    day bar only; the sample is 18 minutes from the 18:30 advance."""
    frame = _section(page, "frame")
    flags = re.findall(r'<span class="lu-nflag"[^>]*>[^<]*</span>', frame)
    assert flags == [
        '<span class="lu-nflag" style="--bl:70.8333;--bw:12.5;--tp:50.0" data-in="1080">Update in 18 min</span>'
    ]
    assert 'class="lu-trk tall flagged"' in frame
    # One flag per display that has a next advance (both do here), none on
    # another day's bar (see test_another_day_shows_back_to_today).
    assert page.count('<span class="lu-nflag"') == 2


@pytest.mark.parametrize(
    ("minutes", "text"),
    [
        (0, "Update in under a minute"),
        (1, "Update in 1 min"),
        (5, "Update in 5 min"),
        (59, "Update in 59 min"),
        (60, "Update in 1 h"),
        (65, "Update in 1 h 5 min"),
        (121, "Update in 2 h 1 min"),
        (1439, "Update in 23 h 59 min"),
        (1440, ""),
        (5000, ""),
    ],
)
def test_update_in_label(minutes: int, text: str) -> None:
    assert update_in_label(minutes) == text


def test_next_tick_reports_how_long_it_is_away() -> None:
    vm = sample_view_model().to_dict()
    frame = next(d for d in vm["displays"] if d["id"] == "frame")
    ticks = [t for s in frame["day"]["segments"] for t in s["ticks"]]
    (nxt,) = [t for t in ticks if t["next"]]
    assert (nxt["in_min"], nxt["in_sec"], nxt["in_label"]) == (18, 1080, "Update in 18 min")
    # Only the next one carries it; the schema stays at version 1.
    assert all(t["in_min"] is None and t["in_label"] == "" for t in ticks if not t["next"])
    assert vm["schema_version"] == 1


def test_queued_step_on_a_sleeping_display(page: str) -> None:
    kitchen = _section(page, "kitchen")
    hdr = kitchen[: kitchen.index("</header>")]
    assert '<p class="lu-show queued"' in hdr and "<b>Bus times</b>" in hdr
    assert _next_line(_vm_display("kitchen")) in hdr and "when it wakes" in hdr
    assert "<span>Wakes at 18:18</span>" in hdr
    row = _row(page, "kitchen")
    steps = re.findall(r'<li class="lu-step([^"]*)" data-step="(\d)">.*?</li>', row, re.S)
    assert [(c.strip(), i) for c, i in steps] == [
        ("is-waiting", "0"), ("is-next", "1"), ("", "2"), ("is-now", "3"),
    ]  # fmt: skip
    pills = {st["pill"]["kind"]: st["pill"] for st in _vm_lineup("kitchen")["steps"] if st["pill"]}
    for kind in ("queued", "next"):
        pill = pills[kind]
        assert (
            f'<span class="lu-pill p-{kind}" title="{html.escape(pill["title"])}">{html.escape(pill["label"])}</span>'
            in row
        )
    assert pills["queued"]["label"].startswith("Queued")
    assert re.search(
        r'<span class="lu-pill p-now" title="On screen now"><svg[^>]*>.*?</svg>Now</span>', row
    )
    # Folded, the live row still says what is scheduled and what is on screen.
    lands = _vm_lineup("kitchen")["live_now"]["lands"]
    assert f'<span class="lu-hn q" title="Scheduled, queued: lands {lands}">Weather</span>' in row
    assert '<span class="lu-hn dim" title="Still on screen">Bus times on screen</span>' in row


def test_steps_strip_arrows_and_loop(page: str) -> None:
    dinner = _row(page, "dinner")
    assert 'class="lu-strip has-now p"' in dinner
    assert dinner.count('<span class="lu-sep" aria-hidden="true">') == 5
    assert '<span class="lu-sep loop" title="Loops back to Newspaper">' in dinner
    assert dinner.count('title="Shows only while its conditions pass"') == 2
    assert ">3/6<" in dinner
    assert 'class="lu-strip p"' in _row(page, "night")  # not live: no NOW slot
    assert '<span class="lu-step-meta">only</span>' in _row(page, "night")
    assert 'class="lu-strip has-now l"' in _row(page, "kitchen")


def test_multi_item_steps_stack_with_a_count(page: str) -> None:
    dinner = _row(page, "dinner")
    assert '<span class="lu-tile stacked">' in dinner
    assert re.search(
        r'<button type="button" class="lu-count" data-peek="dinner:1" aria-haspopup="dialog" aria-expanded="false" title="12 items in this step. Show them">×12</button>',
        dinner,
    )
    assert 'data-peek="dinner:4"' in dinner and ">×20<" in dinner
    assert ">×8<" in _row(page, "kitchen")
    assert dinner.count("lu-tile stacked") == 2
    assert dinner.count('class="lu-count"') == 2


def test_push_now_works_without_javascript(page: str) -> None:
    dinner = _row(page, "dinner")
    form = re.search(
        r'<form method="post" action="/decks/dinner/push-step" class="lu-push-form">(.*?)</form>',
        dinner,
        re.S,
    )
    assert form
    assert '<input type="hidden" name="index" value="0">' in form.group(1)
    assert '<input type="hidden" name="page_id" value="newspaper">' in form.group(1)
    assert 'aria-label="Push Newspaper to Visionect frame now"' in form.group(1)
    # Edit / toggle / delete stay plain links and forms.
    assert '<a href="/decks/dinner/edit">Edit</a>' in dinner
    assert 'action="/decks/dinner/toggle"' in dinner and ">Disable<" in dinner
    assert 'data-confirm="Delete Dinner (Mon–Wed)?"' in dinner


# -- folding ---------------------------------------------------------------------


def _fold_states(body: str) -> dict[str, str]:
    pat = r'aria-expanded="(true|false)" aria-controls="lu-body-[^"]+" aria-label="[^"]*" data-fold="([^"]+)"'
    return {lid: st for st, lid in re.findall(pat, body)}


def test_folded_by_default_when_two_displays_are_busy(page: str) -> None:
    assert 'data-fold-default="1"' in page
    states = _fold_states(page)
    assert states == {
        "dinner": "true", "night": "false", "breakfast": "false",
        "weekend": "false", "monday": "false", "kitchen": "true",
    }  # fmt: skip
    for lid, st in states.items():
        assert ("is-folded" in _row_open_tag(page, lid)) == (st == "false")
        assert f'id="lu-body-{lid}"' in page


def test_one_busy_display_keeps_later_rows_open(app: Flask) -> None:
    displays, dashboards = sample_inputs()
    vm = build_view(displays[:1], dashboards, now=SAMPLE_NOW, tz_name="UTC").to_dict()
    body = _render(app, vm)
    assert 'data-fold-default="0"' in body
    states = _fold_states(body)
    assert states["dinner"] == "true" and states["night"] == "true"
    assert states["breakfast"] == "false"  # not today still folds


# -- page, no-JS, accent ------------------------------------------------------------


def test_page_shell_and_progressive_enhancement(page: str) -> None:
    assert '<main class="page lu-page">' in page
    # The toolbar filters what is drawn, so it waits for the script.
    assert re.search(r'<div class="lu-tb" role="search" data-lu-toolbar hidden>', page)
    assert '<option value="frame">Visionect frame</option>' in page
    assert '<button type="button" data-view="cards" aria-pressed="true">Cards</button>' in page
    assert ">Fold all<" in page and ">6 lineups<" in page
    # The view-model rides along for the script, intact.
    m = re.search(r'<script type="application/json" data-lu-vm>(.*?)</script>', page, re.S)
    assert m
    assert (
        json.loads(html.unescape(m.group(1)) if "&#34;" in m.group(1) else m.group(1))
        == sample_view_model().to_dict()
    )
    for js in ("lineups_api.js", "lineups_render.js", "lineups.js"):
        assert f"/static/{js}" in page
    assert "lineup_fold.js" not in page
    assert "/static/style/dx-lineups.css" in page
    assert 'data-view-url="/decks/view.json"' in page and 'data-edits-url="/decks/edits"' in page
    # The wizard and the schedule forms are still on the page.
    assert "data-open-timed-wizard" in page and 'id="timed-wizard"' in page
    assert 'id="schedule-form-card"' in page


def test_empty_state_without_lineups(app: Flask) -> None:
    vm = build_view([], {}, now=SAMPLE_NOW, tz_name="UTC").to_dict()
    body = _render(app, vm)
    assert "Nothing on your displays yet" in body
    assert "data-lu-toolbar" not in body
    assert "lu-ds" not in body.split('class="lu-empty"')[0].split("data-lu-displays")[1]
    # A view-model that failed to build says so, with a retry, never a blank page.
    with app.test_request_context("/decks?day=3"):
        failed = render_template("decks.html", lineups_view=None, automation_url="#")
    assert "data-lu-load-error" in failed and "could not be loaded" in failed
    assert '<a href="/decks?day=3">Try again</a>' in failed
    assert "Nothing on your displays yet" not in failed
    assert 'id="timed-wizard"' in failed  # the rest of the page still works


def test_names_are_escaped(app: Flask) -> None:
    vm = sample_view_model().to_dict()
    vm["displays"][0]["lineups"][0]["name"] = '<img src=x onerror="alert(1)">'
    body = _render(app, vm)
    assert '<img src=x onerror="alert(1)">' not in body.split("data-lu-vm")[0]
    assert "&lt;img src=x onerror=" in body


def test_accent_comes_from_the_theme_tokens() -> None:
    # Every live/accent colour reads --lu-acc, which is the app's --t-accent
    # (server colour aware); no hard-coded greens or reds stand in for it.
    assert re.search(r"--lu-acc: var\(--t-accent\);", CSS)
    assert re.search(r"--lu-acc-fg: var\(--t-accent-fg\);", CSS)
    for rule in (
        r"\.lu \.lu-tag-st\.live \{ background: var\(--lu-acc\); color: var\(--lu-acc-fg\);",
        r"\.lu \.lu-blk\.live \{ background: var\(--lu-acc\); color: var\(--lu-acc-fg\);",
        r"\.lu \.lu-pill\.p-now \{ background: var\(--lu-acc\); color: var\(--lu-acc-fg\);",
        r"\.lu \.lu-step\.is-now \.lu-thumb \{ border: 2px solid var\(--lu-acc\)",
        r"\.lu \.lu-sdot \{[^}]*background: var\(--lu-acc\)",
    ):
        assert re.search(rule, CSS), rule
    for literal in ("#8dbf7f", "#b8352a", "#3d7a3a", "#2dd4bf"):
        assert literal not in CSS.lower()
    # Semantic colours stay their own: amber for pause/queued, red for errors.
    assert re.search(r"\.lu \.lu-pz \{[^}]*var\(--t-warn\)", CSS)
    assert re.search(
        r"\.lu \.lu-step\.is-waiting \.lu-thumb \{ outline: 2px dashed var\(--t-warn\)", CSS
    )
    assert re.search(r"\.lu \.lu-show\.queued \.lu-sdot \{ background: var\(--t-warn\)", CSS)
    assert re.search(r"\.lu \.lu-render-error \{[^}]*var\(--t-danger\)", CSS)
    # Warm accents swap Quiet hours to an ink cross-hatch and queued dots to rings.
    assert ".lu.acc-warm .lu-pz" in CSS and ".lu.acc-warm .lu-show.queued .lu-sdot" in CSS
    # Paper dark's default red under white is nudged past 4.5:1.
    assert re.search(
        r':root\[data-ui="paper"\]\[data-theme="dark"\]:not\(\[data-server-colour\]\) \.lu \{ --lu-acc: #[cC]8463[aA]; \}',
        CSS,
    )


def _contrast(a: str, b: str) -> float:
    def lum(h: str) -> float:
        cs = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        cs = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in cs]
        return 0.2126 * cs[0] + 0.7152 * cs[1] + 0.0722 * cs[2]

    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def test_paper_dark_default_accent_reaches_4_5() -> None:
    tokens = (REPO_ROOT / "static/style/paper-tokens.css").read_text()
    dark = tokens.split(':root[data-ui="paper"][data-theme="dark"]', 1)[1]
    shipped = re.search(r"--t-accent:\s*(#[0-9a-fA-F]{6})", dark)
    foreground = re.search(r"--t-accent-fg:\s*(#[0-9a-fA-F]{6})", dark)
    adjusted = re.search(
        r':root\[data-ui="paper"\]\[data-theme="dark"\]:not\(\[data-server-colour\]\) \.lu \{ --lu-acc: (#[0-9a-fA-F]{6});',
        CSS,
    )
    assert shipped and foreground and adjusted
    assert _contrast(shipped[1], foreground[1]) < 4.5
    assert _contrast(adjusted[1], foreground[1]) >= 4.5


def test_old_lineups_page_code_is_gone() -> None:
    assert not (REPO_ROOT / "static" / "lineup_fold.js").exists()
    old = (REPO_ROOT / "static" / "style" / "dx-decks.css").read_text()
    assert ".dk-row" not in old and ".dk-group" not in old
    assert ".dxe-thumb" in old  # the lineup editor keeps its styles
    # Thumbnails fit inside the display's shape instead of cropping.
    assert re.search(r"\.lu \.lu-thumb img \{[^}]*object-fit: contain", CSS)


def test_unknown_glass_says_so(app: Flask) -> None:
    vm = sample_view_model().to_dict()
    k = vm["displays"][1]
    k["on_screen"], k["on_screen_known"] = None, False
    k["show_note"] = ", waiting for check-in"
    k["placeholder"] = _note("none", "No image yet", "", "No image is known for this display yet.")
    hdr = _section(_render(app, vm), "kitchen").split("</header>")[0]
    assert "No image yet" in hdr and "lu-thumb-ph" not in hdr and ">?<" not in hdr
    assert '<p class="lu-show unknown"' in hdr
    assert '<b>unknown</b><span class="k">, waiting for check-in</span>' in hdr


def _note(state: str, label: str, sub: str, title: str) -> dict[str, str]:
    return {"state": state, "label": label, "sub": sub, "title": title}


def _kitchen_header(app: Flask, **fields: Any) -> str:
    vm = sample_view_model().to_dict()
    k = vm["displays"][1]
    k["on_screen"], k["on_screen_known"] = None, False
    k["queued_badge"] = None
    k["placeholder"] = _note("none", "No image yet", "", "No image is known for this display yet.")
    k.update(fields)
    return _section(_render(app, vm), "kitchen").split("</header>")[0]


def test_frame_without_a_dashboard_is_drawn_as_a_thumb_and_named(app: Flask) -> None:
    frame = {
        "label": "a frame sent by hand",
        "thumb_url": "/compose/thumbs/v1/sent/abc123.webp",
        "since": "17:58",
        "title": "A frame sent by hand, on screen since 17:58",
    }
    hdr = _kitchen_header(app, frame=frame, show_title=frame["title"])
    assert '<img src="/compose/thumbs/v1/sent/abc123.webp"' in hdr
    assert 'title="A frame sent by hand, on screen since 17:58"' in hdr
    assert 'data-gamut="' in hdr and "lu-thumb-ph" not in hdr
    assert '<p class="lu-show frame"' in hdr
    assert '<b>a frame sent by hand</b><span class="k">since 17:58</span>' in hdr
    assert "unknown" not in hdr and "waiting" not in hdr
    # The image is gone: the label still holds, with the placeholder thumb.
    gone = _kitchen_header(
        app,
        frame={**frame, "thumb_url": ""},
        placeholder=_note("unavailable", "Image unavailable", "", "The image file is gone."),
    )
    assert "<img" not in gone and "Image unavailable" in gone and "a frame sent by hand" in gone
    assert ">?<" not in gone


def test_nothing_sent_is_honest(app: Flask) -> None:
    hdr = _kitchen_header(
        app,
        nothing_sent=True,
        placeholder=_note("nothing", "Nothing sent yet", "", "Nothing has been sent yet."),
    )
    assert "<b>Nothing sent yet</b>" in hdr and "unknown</b>" not in hdr
    assert 'title="Nothing has been sent yet."' in hdr and ">?<" not in hdr


def test_queued_placeholder_and_badge_markup(app: Flask) -> None:
    sentence = "A new frame is queued. The frame picks it up at its next check-in ~3:48 PM."
    hdr = _kitchen_header(
        app,
        placeholder=_note(
            "queued", "Queued", "Frame picks it up at its next check-in ~3:48 PM", sentence
        ),
    )
    assert "note-queued" in hdr and f'aria-label="{sentence}"' in hdr
    assert "<b>Queued</b><small>Frame picks it up at its next check-in ~3:48 PM</small>" in hdr
    assert ">?<" not in hdr
    frame = {
        "label": "a frame sent by hand",
        "thumb_url": "/compose/thumbs/v1/sent/abc123.webp",
        "since": "17:58",
        "title": "A frame sent by hand, on screen since 17:58",
    }
    badged = _kitchen_header(
        app, frame=frame, placeholder=None, queued_badge=_note("queued", "Queued", "", sentence)
    )
    assert '<img src="/compose/thumbs/v1/sent/abc123.webp"' in badged
    assert f'<span class="lu-qbadge" title="{sentence}"' in badged


def test_checked_in_display_is_not_waiting(app: Flask) -> None:
    hdr = _kitchen_header(app, show_note=", checked in 18:10")
    assert '<b>unknown</b><span class="k">, checked in 18:10</span>' in hdr
    assert "waiting for check-in" not in hdr


def test_route_variable_is_accepted(app: Flask) -> None:
    with app.test_request_context("/decks"):
        body = render_template(
            "decks.html", lineups_view=sample_view_model().to_dict(), automation_url="#"
        )
    assert 'id="lu-row-dinner"' in body


def test_status_and_delivery_notes_are_accessible_in_server_markup(app: Flask) -> None:
    vm = sample_view_model().to_dict()
    frame, _kitchen = vm["displays"]
    frame["show_suffix"] = "from a manual push · resumes 18:30"
    frame["status_title"] = "3 min behind · poll ≈18:18"
    frame["lineups"][0]["warnings"] = ["These dashboards are on different displays"]
    markup = _render(app, vm)
    assert '<span class="lu-sr-only">Synced</span>' in markup
    assert '<span class="lu-sr-only">Waiting to land</span>' in markup
    assert "from a manual push · resumes 18:30" in markup
    assert "3 min behind · poll ≈18:18" in markup
    assert 'class="lu-row-warning"' in markup


def test_orphans_distinguish_deleted_and_unassigned_displays(app: Flask) -> None:
    import copy

    vm = sample_view_model().to_dict()
    orphan = copy.deepcopy(vm["displays"][0]["lineups"][0])
    orphan["id"] = "orphan"
    orphan["display_id"] = ""
    removed = copy.deepcopy(orphan)
    removed["id"] = "removed"
    removed["display_removed"] = True
    vm["unassigned"] = [orphan, removed]
    markup = _render(app, vm)
    unassigned = markup[markup.index("lu-ds--orphans") :]
    assert "<h2>Not on a display yet</h2>" in unassigned
    assert "<h2>Display removed</h2>" in unassigned
    assert "data-push=" not in unassigned
    assert ">Push now<" not in unassigned


def test_browser_ci_installs_and_requires_chromium() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text()
    assert "playwright install --with-deps chromium" in workflow
    assert 'TESSERAE_REQUIRE_BROWSER_TESTS: "1"' in workflow
    assert "pytest -n 3 -q tests/test_lineups_browser.py" in workflow


def test_mobile_sheet_has_old_viewport_units_fallback() -> None:
    assert CSS.index("max-height: min(75vh, 560px)") < CSS.index("max-height: min(75dvh, 560px)")


def test_day_bar_blocks_have_interactive_or_read_only_roles(page: str) -> None:
    blocks = re.findall(r'<div class="lu-blk[^>]*>', page)
    assert blocks
    for block in blocks:
        assert ('role="button"' if "data-edit" in block else 'role="group"') in block
        assert "aria-label=" in block


def test_display_header_has_a_settings_gear(page: str) -> None:
    gears = re.findall(r'<a class="lu-gear" href="([^"]+)"[^>]*aria-label="([^"]+)"', page)
    assert ("/settings/devices/frame", "Visionect frame settings") in gears
    assert ("/settings/devices/kitchen", "Kitchen display settings") in gears


def test_deleted_dashboard_step_is_flagged_in_markup_and_js(app: Flask) -> None:
    from app.lineups_view import DashboardInfo, build_view, sample_inputs

    displays, dashboards = sample_inputs()
    dashboards["art"] = DashboardInfo(id="art", name="art", missing=True)
    vm = build_view(displays, dashboards, now=SAMPLE_NOW, tz_name="UTC")
    row = _row(_render(app, vm.to_dict()), "dinner")
    tip = "This dashboard was deleted; remove it from the lineup"
    assert re.search(rf'<li class="lu-step[^"]*is-missing" data-step="\d" title="{tip}">', row)
    assert row.count("is-missing") == 1
    assert '<span class="lu-step-name">Missing dashboard</span>' in row
    assert 'class="lu-thumb-ph" aria-hidden="true">!</span>' in row
    assert "+1 missing" in row and "5 dashboards" in row
    # No Push now for the deleted one, so five forms for six steps.
    assert row.count('class="lu-push-form"') == 5
    js = (REPO_ROOT / "static" / "lineups_render.js").read_text()
    for token in (
        tip,
        '" is-missing"',
        "+${l.missing_count} missing",
        "!s.missing",
        '.includes("missing")',
    ):
        assert token in js, token
