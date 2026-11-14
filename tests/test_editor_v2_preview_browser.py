"""Dashboard editor v2 live preview, in a live Chromium.

Two parts:

* The pure layout geometry in static/pages/editor_v2/preview.js
  (window.EV2PreviewGeom): which cells share a line (T-junctions), how far a
  line may move, snapping, split, remove-and-merge, and the port of
  app.panel.fit_cells_to_panel, checked against the Python original.
* The preview partial itself, rendered on a bare test route so it does not
  depend on the steps page: dragging a shared line saves every cell on it
  through batch_cells, clicks and keys emit the steps <-> preview events, and
  edit mode splits and removes cells.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask, render_template_string

from app.main import REPO_ROOT, create_app
from app.panel import fit_cells_to_panel
from app.state.page_store import Cell, Page, Panel

_EXE = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
PREVIEW_JS = REPO_ROOT / "static" / "pages" / "editor_v2" / "preview.js"


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


# ---------------------------------------------------------------------------
# Geometry (no server)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pw() -> Iterator[Any]:
    """One Playwright per module: the sync API can't nest a second one."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def geom(pw: Any) -> Iterator[Any]:
    """A blank page with preview.js loaded; call ``geom(js_expr)``."""
    browser = _launch(pw)
    page = browser.new_page()
    page.set_content("<!doctype html><title>geom</title>")
    page.add_script_tag(content=PREVIEW_JS.read_text())

    def run(expr: str) -> Any:
        return page.evaluate(f"() => {{ const G = window.EV2PreviewGeom; return ({expr}); }}")

    yield run
    browser.close()


def _cells(*specs: tuple[str, int, int, int, int]) -> str:
    return json.dumps([{"id": i, "x": x, "y": y, "w": w, "h": h} for i, x, y, w, h in specs])


TEE = _cells(
    ("tall", 0, 0, 480, 480),
    ("top", 480, 0, 320, 240),
    ("bottom", 480, 240, 320, 240),
)
CROSS = _cells(
    ("tl", 0, 0, 400, 240),
    ("tr", 400, 0, 400, 240),
    ("bl", 0, 240, 400, 240),
    ("br", 400, 240, 400, 240),
)


def test_t_junction_is_one_line(geom: Any) -> None:
    edges = geom(f"G.edgeGroups({TEE})")
    vertical = [e for e in edges if e["dir"] == "v"]
    horizontal = [e for e in edges if e["dir"] == "h"]
    assert len(vertical) == 1
    assert vertical[0]["pos"] == 480
    assert vertical[0]["a"] == ["tall"]
    assert vertical[0]["b"] == ["top", "bottom"]
    assert (vertical[0]["s0"], vertical[0]["s1"]) == (0, 480)
    assert len(horizontal) == 1
    assert (horizontal[0]["a"], horizontal[0]["b"]) == (["top"], ["bottom"])
    assert (horizontal[0]["s0"], horizontal[0]["s1"]) == (480, 800)


def test_crossing_lines_split_into_independent_halves(geom: Any) -> None:
    edges = geom(f"G.edgeGroups({CROSS})")
    assert sorted((e["dir"], e["a"][0], e["b"][0]) for e in edges) == [
        ("h", "tl", "bl"),
        ("h", "tr", "br"),
        ("v", "bl", "br"),
        ("v", "tl", "tr"),
    ]


def test_status_bar_line_is_not_draggable(geom: Any) -> None:
    cells = _cells(("sb", 0, 0, 800, 48), ("l", 0, 48, 400, 432), ("r", 400, 48, 400, 432))
    edges = geom(f"G.edgeGroups({cells}, {{exclude: ['sb']}})")
    assert [(e["dir"], e["a"], e["b"]) for e in edges] == [("v", ["l"], ["r"])]


def test_line_limits_keep_minimum_size_and_stop_at_cells(geom: Any) -> None:
    lo, hi = geom(
        f"(() => {{ const c = {TEE}; return G.edgeLimits(c, G.edgeGroups(c)[0], 48); }})()"
    )
    assert (lo, hi) == (48, 800 - 48)
    # A cell beyond a gap on the far side stops the near cell from growing into it.
    gap = _cells(
        ("a", 0, 0, 300, 480),
        ("b", 300, 0, 200, 240),
        ("c", 300, 240, 100, 240),
        ("d", 500, 240, 300, 240),
    )
    lo, hi = geom(
        f"(() => {{ const c = {gap}; const e = G.edgeGroups(c).find(e => e.dir === 'v' && e.pos === 300);"
        " return G.edgeLimits(c, e, 8); })()"
    )
    assert lo == 8
    assert hi == 400 - 8  # c (300..400) keeps 8 px


def test_apply_edge_moves_every_cell_on_the_line(geom: Any) -> None:
    moved = geom(
        f"(() => {{ const c = {TEE}; return G.applyEdge(c, G.edgeGroups(c)[0], 400); }})()"
    )
    assert {c["id"]: (c["x"], c["y"], c["w"], c["h"]) for c in moved} == {
        "tall": (0, 0, 400, 480),
        "top": (400, 0, 400, 240),
        "bottom": (400, 240, 400, 240),
    }


def test_snapping(geom: Any) -> None:
    def snap(raw: float, **kw: Any) -> dict[str, Any]:
        opts = {"lo": 48, "hi": 752, "r0": 0, "r1": 800, "threshold": 10, "grid": 0, **kw}
        return geom(f"G.snapPos({raw}, {json.dumps(opts)})")

    assert snap(262) == {"pos": 267, "label": "⅓"}
    assert snap(405) == {"pos": 400, "label": "½"}
    assert snap(585) == {"pos": 585, "label": None}  # between ⅔ and ¾, too far from both
    assert snap(20) == {"pos": 48, "label": None}  # clamped to the minimum size
    assert snap(130, grid=12) == {"pos": 133, "label": None}  # nearest of 12 columns
    # Snap targets are fractions of the band under a status bar, not the panel.
    assert snap(266, r0=48, r1=480, lo=0, hi=480) == {"pos": 264, "label": "½"}


def test_split_and_minimum(geom: Any) -> None:
    cell = '{"id":"c","x":100,"y":0,"w":301,"h":240}'
    assert geom(f"G.split({cell}, 'v', 8)") == {
        "updates": [{"id": "c", "x": 100, "y": 0, "w": 150, "h": 240}],
        "creates": [{"x": 250, "y": 0, "w": 151, "h": 240}],
    }
    assert geom(f"G.split({cell}, 'h', 8)") == {
        "updates": [{"id": "c", "x": 100, "y": 0, "w": 301, "h": 120}],
        "creates": [{"x": 100, "y": 120, "w": 301, "h": 120}],
    }
    assert geom(f"G.split({cell}, 'h', 121)") is None


def test_split_cuts_on_the_grid_when_snapping(geom: Any) -> None:
    # 12 columns over 800 px: lines every 66.67 px. The middle of 100..401
    # (250.5) is nearest the line at 266.67, so the cut lands there.
    cell = '{"id":"c","x":100,"y":0,"w":301,"h":240}'
    assert geom(f"G.split({cell}, 'v', 8, {{r0: 0, step: 800 / 12}})") == {
        "updates": [{"id": "c", "x": 100, "y": 0, "w": 167, "h": 240}],
        "creates": [{"x": 267, "y": 0, "w": 134, "h": 240}],
    }
    # No grid line leaves both parts big enough: fall back to the middle.
    small = '{"id":"s","x":0,"y":0,"w":60,"h":240}'
    assert geom(f"G.split({small}, 'v', 20, {{r0: 0, step: 200}})")["updates"][0]["w"] == 30


def test_remove_merges_into_a_full_edge_neighbour(geom: Any) -> None:
    # Removing "top" lets "bottom" take its space (they share a full edge).
    assert geom(f"G.removeMerge({TEE}, 'top')") == {
        "deletes": ["top"],
        "updates": [{"id": "bottom", "x": 480, "y": 0, "w": 320, "h": 480}],
    }
    # Removing "tall": the two stacked cells tile its right edge, both widen.
    assert geom(f"G.removeMerge({TEE}, 'tall')") == {
        "deletes": ["tall"],
        "updates": [
            {"id": "top", "x": 0, "y": 0, "w": 800, "h": 240},
            {"id": "bottom", "x": 0, "y": 240, "w": 800, "h": 240},
        ],
    }
    # Nothing lines up with the middle cell of a pinwheel: no removal.
    pinwheel = _cells(
        ("n", 0, 0, 500, 160),
        ("e", 500, 0, 300, 320),
        ("s", 300, 320, 500, 160),
        ("w", 0, 160, 300, 320),
        ("m", 300, 160, 200, 160),
    )
    assert geom(f"G.removeMerge({pinwheel}, 'm')") is None
    # The status bar never absorbs a removed cell.
    bar = _cells(("sb", 0, 0, 800, 48), ("only", 0, 48, 800, 216), ("low", 0, 264, 800, 216))
    assert geom(f"G.removeMerge({bar}, 'only', {{exclude: ['sb']}})") == {
        "deletes": ["only"],
        "updates": [{"id": "low", "x": 0, "y": 48, "w": 800, "h": 432}],
    }


@pytest.mark.parametrize(
    ("cells", "target", "strip"),
    [
        ([(0, 0, 480, 480), (480, 0, 320, 240), (480, 240, 320, 240)], (800, 480), None),
        ([(0, 0, 480, 480), (480, 0, 320, 240), (480, 240, 320, 240)], (1200, 825), None),
        ([(0, 0, 400, 480), (400, 0, 400, 240), (400, 240, 400, 240)], (480, 800), None),
        ([(0, 0, 333, 201), (333, 0, 467, 201), (0, 201, 800, 279)], (1872, 1404), None),
        ([(0, 0, 800, 48), (0, 48, 400, 432), (400, 48, 400, 432)], (800, 480), 0),
        (
            [(0, 0, 800, 48), (0, 48, 400, 432), (400, 48, 400, 216), (400, 264, 400, 216)],
            (480, 800),
            0,
        ),
    ],
)
def test_fit_cells_matches_python(
    geom: Any, cells: list[tuple[int, int, int, int]], target: tuple[int, int], strip: int | None
) -> None:
    expected = fit_cells_to_panel(cells, target[0], target[1], top_strip_index=strip)
    js_cells = json.dumps(
        [{"id": f"c{i}", "x": x, "y": y, "w": w, "h": h} for i, (x, y, w, h) in enumerate(cells)]
    )
    strip_id = json.dumps(f"c{strip}" if strip is not None else None)
    got = geom(f"G.fitCells({js_cells}, {target[0]}, {target[1]}, {strip_id})")
    assert [(c["x"], c["y"], c["w"], c["h"]) for c in got] == [tuple(t) for t in expected]


def test_axis_map_flags_rotated_displays(geom: Any) -> None:
    assert geom(f"G.axisMap({TEE}, 800, 480, null).rotated") is False
    assert geom(f"G.axisMap({TEE}, 1200, 825, null).rotated") is False
    assert geom(f"G.axisMap({TEE}, 480, 800, null).rotated") is True


# ---------------------------------------------------------------------------
# The pane (live server, bare test route)
# ---------------------------------------------------------------------------

PANEL_W, PANEL_H = 800, 480

HARNESS = """{% extends "_base.html" %}
{% block main %}
<section id="ev2-preview" style="max-width: 900px">
  {% include "partials/page_editor_v2_preview.html" %}
</section>
<script>
  window.__events = [];
  ["ev2:cell-select", "ev2:layout-changed"].forEach((name) =>
    document.addEventListener(name, (e) => window.__events.push([name, e.detail])));
</script>
{% endblock %}"""


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    app: Flask = create_app(
        testing=False,
        data_root=tmp_path / "data",
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    store = app.config["PAGE_STORE"]
    store.save(
        Page(
            id="tee",
            name="Tee",
            panel=Panel(w=PANEL_W, h=PANEL_H),
            cells=[
                Cell(id="tall", x=0, y=0, w=480, h=480),
                Cell(id="top", x=480, y=0, w=320, h=240),
                Cell(id="bottom", x=480, y=240, w=320, h=240),
            ],
        )
    )

    from app import device_service

    for iid, orientation in (("desk", "landscape"), ("hall", "portrait")):
        device_service.create_instance(
            devices=app.config["DEVICE_REGISTRY"],
            renderers=app.config["RENDERER_REGISTRY"],
            data_root=app.config["DEVICE_DATA_ROOT"],
            instance_id=iid,
            kind_id="esp32_client",
            name=iid.title(),
            orientation=orientation,
        )
    store.save(
        Page(
            id="two",
            name="Two",
            panel=Panel(w=PANEL_W, h=PANEL_H),
            device_ids=["desk", "hall"],
            cells=[
                Cell(id="a", x=0, y=0, w=400, h=480),
                Cell(id="b", x=400, y=0, w=400, h=240),
                Cell(id="c", x=400, y=240, w=400, h=240),
            ],
        )
    )

    from app.page_routes import _editor_context

    def harness(page_id: str) -> str:
        return render_template_string(HARNESS, **_editor_context(store.get(page_id)))

    app.add_url_rule("/_test/ev2-preview/<page_id>", "ev2_preview_harness", harness)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", app
    finally:
        srv.shutdown()
        thread.join(timeout=10)


def _geometry(app: Flask, page_id: str) -> dict[str, tuple[int, int, int, int]]:
    page = app.config["PAGE_STORE"].get(page_id)
    return {c.id: (c.x, c.y, c.w, c.h) for c in page.cells}


def _open(p: Any, base: str, page_id: str, **ctx: Any) -> tuple[Any, Any, list[str]]:
    browser = _launch(p)
    page = browser.new_page(viewport={"width": 1200, "height": 900}, **ctx)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/_test/ev2-preview/{page_id}", wait_until="domcontentloaded")
    page.wait_for_function("() => window.EV2Preview && document.querySelector('.ev2pv-edge')")
    return browser, page, errors


def _events(page: Any, name: str) -> list[Any]:
    return page.evaluate(
        f"() => window.__events.filter(e => e[0] === {json.dumps(name)}).map(e => e[1])"
    )


def test_dragging_a_shared_line_moves_every_cell_on_it(server: tuple[str, Flask], pw: Any) -> None:
    base, app = server
    browser, page, errors = _open(pw, base, "tee")
    line = page.locator(".ev2pv-edge--v")
    assert line.count() == 1
    lb = line.bounding_box()
    ob = page.locator(".ev2pv-overlay").bounding_box()
    assert lb and ob
    x0 = lb["x"] + lb["width"] / 2
    y = lb["y"] + lb["height"] * 0.25  # away from the T-junction in the middle
    target = ob["x"] + ob["width"] * 400 / PANEL_W  # ½: snaps exactly
    with page.expect_response(lambda r: r.url.endswith("/cells/batch")):
        page.mouse.move(x0, y)
        page.mouse.down()
        for k in range(1, 11):
            page.mouse.move(x0 + (target - x0 + 3) * k / 10, y)
        page.wait_for_function(
            "() => document.querySelector('.ev2pv-edge.is-active .ev2pv-px').textContent === '400 px · ½'"
        )
        page.mouse.up()
    page.wait_for_function("() => window.__events.some(e => e[0] === 'ev2:layout-changed')")
    changed = _events(page, "ev2:layout-changed")[0]["cells"]
    assert {c["id"]: c["w"] for c in changed} == {"tall": 400, "top": 400, "bottom": 400}
    assert not errors
    browser.close()
    assert _geometry(app, "tee") == {
        "tall": (0, 0, 400, 480),
        "top": (400, 0, 400, 240),
        "bottom": (400, 240, 400, 240),
    }


def test_click_selects_and_focus_outlines(server: tuple[str, Flask], pw: Any) -> None:
    base, _app = server
    browser, page, errors = _open(pw, base, "tee")
    page.locator('.ev2pv-cell[data-cell-id="top"]').click()
    assert _events(page, "ev2:cell-select") == [{"cellId": "top"}]
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('ev2:cell-focus', {detail: {cellId: 'bottom'}}))"
    )
    assert page.evaluate(
        "() => [...document.querySelectorAll('.ev2pv-cell.is-selected')].map(b => b.dataset.cellId)"
    ) == ["bottom"]
    # Keyboard: cells are buttons; Enter on a focused cell selects it too.
    page.locator('.ev2pv-cell[data-cell-id="tall"]').focus()
    page.keyboard.press("Enter")
    assert _events(page, "ev2:cell-select")[-1] == {"cellId": "tall"}
    assert not errors
    browser.close()


def test_arrow_keys_move_a_line_and_save(server: tuple[str, Flask], pw: Any) -> None:
    base, app = server
    browser, page, errors = _open(pw, base, "tee")
    page.locator(".ev2pv-edge--h").focus()
    with page.expect_response(lambda r: r.url.endswith("/cells/batch")):
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Shift+ArrowDown")
    page.wait_for_function("() => window.__events.some(e => e[0] === 'ev2:layout-changed')")
    assert page.evaluate("() => document.activeElement.classList.contains('ev2pv-edge')")
    assert not errors
    browser.close()
    geometry = _geometry(app, "tee")
    # One step is 1% of the 480 px height (5 px); Shift moves ten steps.
    assert geometry["top"] == (480, 0, 320, 295)
    assert geometry["bottom"] == (480, 295, 320, 185)
    assert geometry["tall"] == (0, 0, 480, 480)


def test_edit_mode_splits_and_removes(server: tuple[str, Flask], pw: Any) -> None:
    base, app = server
    browser, page, errors = _open(pw, base, "tee")
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('ev2:edit-mode', {detail: {on: true}}))"
    )
    page.locator('.ev2pv-cell[data-cell-id="top"]').click()
    assert _events(page, "ev2:cell-select") == []  # edit mode selects for the tools instead
    with page.expect_response(lambda r: r.url.endswith("/cells/batch")):
        page.locator('[data-ev2pv-tool="split-v"]').click()
    page.wait_for_function("() => document.querySelectorAll('.ev2pv-cell').length === 4")
    geometry = _geometry(app, "tee")
    assert geometry["top"] == (480, 0, 160, 240)
    new_id = next(k for k in geometry if k not in ("tall", "top", "bottom"))
    assert geometry[new_id] == (640, 0, 160, 240)

    page.keyboard.press("Escape")  # focus is on the Split button; Esc drops the selection
    assert page.locator(".ev2pv-tools-bar").count() == 0
    page.locator(f'.ev2pv-cell[data-cell-id="{new_id}"]').click()
    with page.expect_response(lambda r: r.url.endswith("/cells/batch")):
        page.locator('[data-ev2pv-tool="remove"]').click()
    page.wait_for_function("() => document.querySelectorAll('.ev2pv-cell').length === 3")
    assert _geometry(app, "tee")["top"] == (480, 0, 320, 240)
    assert len(_events(page, "ev2:layout-changed")) == 2
    assert not errors
    browser.close()


def test_one_tab_per_shape_and_no_resizing_on_a_turned_display(
    server: tuple[str, Flask], pw: Any
) -> None:
    base, _app = server
    browser, page, errors = _open(pw, base, "two")
    tabs = page.locator("[data-ev2pv-tab]")
    assert tabs.count() == 2
    assert page.locator(".ev2pv-group:not([hidden]) .ev2pv-edge").count() == 2
    page.evaluate("() => window.EV2Preview.setTab('480x800')")
    visible = page.locator(".ev2pv-group:not([hidden])")
    assert visible.get_attribute("data-group-key") == "480x800"
    assert visible.locator(".ev2pv-edge").count() == 0
    assert "turned the other way" in page.locator("[data-ev2pv-hint]").inner_text()
    # Cells stay clickable there, drawn where the composer draws them.
    visible.locator('.ev2pv-cell[data-cell-id="b"]').click()
    assert _events(page, "ev2:cell-select") == [{"cellId": "b"}]
    assert not errors
    browser.close()
