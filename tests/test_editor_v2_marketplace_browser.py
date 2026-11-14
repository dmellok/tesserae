"""Dashboard editor v2 with the catalog's marketplace widgets.

The manifests under tests/fixtures/marketplace/ are copies of the catalog
widgets' plugin.json (manifest only, no code), installed the way the app
finds them: ``<data_root>/marketplace/<id>/plugin.json``. The bundled ids
the catalog also ships (news_front_pages, news_illustrated) are left out of
the bundled dir so the catalog's copies are the ones that load.

Each widget's row passes the same checks as the bundled ones
(test_editor_v2_cell_panel_browser), plus what only their manifests
exercise: a half-width select whose choice is wide ("Most comments"), a
short label with an "i" button that would wrap, a long name with a family
tag, and a Replace between widgets that share option names but not choices.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.page_store import Cell, Page, Panel
from tests.test_editor_v2_cell_panel_browser import (
    _EXE,
    ALLOWED_BLOCKS,
    CHECK_JS,
    LAYOUT_JS,
    pytestmark,  # noqa: F401  (the same skip-without-Chromium marks)
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "marketplace"
MARKET_IDS = sorted(p.name for p in FIXTURES.iterdir() if (p / "plugin.json").exists())
PAGE_ID = "marketplace"


@pytest.fixture(scope="module")
def pw() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, Flask]]:
    from werkzeug.serving import make_server

    root = tmp_path_factory.mktemp("marketplace")
    bundled = root / "bundled"
    bundled.mkdir()
    for plugin in (REPO_ROOT / "plugins").iterdir():
        if plugin.name not in MARKET_IDS:
            (bundled / plugin.name).symlink_to(plugin)
    data = root / "data"
    for mid in MARKET_IDS:
        shutil.copytree(FIXTURES / mid, data / "marketplace" / mid)
    app: Flask = create_app(
        testing=False,
        data_root=data,
        plugins_dir=bundled,
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    app.config["TESTING"] = True
    settings = app.config["SETTINGS_STORE"]
    settings.patch_section("app", {"onboarded": True})
    settings.patch_section("auth", {"disabled": True})
    registry = app.config["PLUGIN_REGISTRY"]
    for mid in MARKET_IDS:
        plugin = registry.get(mid)
        assert plugin is not None and Path(plugin.path).parent.name == "marketplace", mid
    cols, w, h = 4, 300, 240
    cells = [
        Cell(id=f"m{i}", plugin=mid, x=(i % cols) * w, y=(i // cols) * h, w=w, h=h)
        for i, mid in enumerate(MARKET_IDS)
    ]
    cells.append(Cell(id="clock", plugin="clock_word", x=900, y=240, w=300, h=240))
    app.config["PAGE_STORE"].save(
        Page(id=PAGE_ID, name="Marketplace", panel=Panel(w=cols * w, h=2 * h), cells=cells)
    )
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}", app
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
def test_every_marketplace_panel_has_one_shape_and_is_compact(
    pw: Any, server: Any, width: int
) -> None:
    base, _app = server
    browser, page, errors = _open(pw, base, width)
    try:
        shape = page.evaluate(CHECK_JS, ALLOWED_BLOCKS)
        layout = page.evaluate(LAYOUT_JS, width < 640)
        assert shape["checked"] == len(MARKET_IDS) + 1
        assert shape["problems"] == []
        assert layout["problems"] == []
        assert errors == []
    finally:
        browser.close()


def test_a_long_widget_name_keeps_its_family_tag(pw: Any, server: Any) -> None:
    """With the tag inside the truncated name, "Google Shared Album" lost
    its "Picture" tag at 1440. In a row too narrow for both, the name ends
    in an ellipsis and the tag stays whole after it."""
    base, _app = server
    browser, page, errors = _open(pw, base, 1440)
    try:
        cell = f"m{MARKET_IDS.index('picture_google_album')}"
        got = page.evaluate(
            """(id) => {
              const row = document.querySelector(`[data-ev2-cell-form][data-cell-id="${id}"]`).closest('.ev2-cl');
              row.style.width = '240px';
              const b = row.querySelector('.ev2-cl-n b');
              const nm = b.querySelector('.nm'), tag = b.querySelector('.ev2-tag');
              const br = b.getBoundingClientRect(), t = tag.getBoundingClientRect();
              return {name: nm.textContent.trim(), cut: nm.scrollWidth > nm.clientWidth,
                      tagInside: t.width > 20 && t.right <= br.right + 1 && t.left >= nm.getBoundingClientRect().right - 1,
                      tagWhole: tag.scrollWidth <= tag.clientWidth + 1};
            }""",
            cell,
        )
        assert got["name"] == "Google Shared Album"
        assert got["cut"]
        assert got["tagInside"] and got["tagWhole"]
        assert errors == []
    finally:
        browser.close()


def test_replace_keeps_only_choices_the_new_widget_lists(server: Any) -> None:
    """Google's ``orientation`` lists Split view; Civitai's doesn't. A
    replace keeps the shared options the new widget can show (scale "fit")
    and gives the rest their defaults rather than a value no choice names."""
    _base, app = server
    store = app.config["PAGE_STORE"]
    store.save(
        Page(
            id="swap",
            name="Swap",
            panel=Panel(w=800, h=480),
            cells=[
                Cell(
                    id="a",
                    plugin="picture_google_album",
                    x=0,
                    y=0,
                    w=800,
                    h=480,
                    options={"orientation": "splitview", "scale": "fit", "mode": "random"},
                )
            ],
        )
    )
    client = app.test_client()
    resp = client.post(
        "/pages/swap/cells/a",
        data={"plugin": "picture_civitai"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
    )
    assert resp.status_code < 400
    cell = store.get("swap").cells[0]
    assert cell.plugin == "picture_civitai"
    assert cell.options["orientation"] == "auto"
    assert cell.options["scale"] == "fit"
    # Back to Google: Civitai's "auto" isn't one of Google's orientations.
    client.post(
        "/pages/swap/cells/a",
        data={"plugin": "picture_google_album"},
        headers={"X-Requested-With": "fetch", "Accept": "application/json"},
    )
    cell = store.get("swap").cells[0]
    assert cell.options["orientation"] == "any"
    assert cell.options["scale"] == "fit"
