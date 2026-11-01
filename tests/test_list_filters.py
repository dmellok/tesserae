"""The shared list helper (static/list-filter.js): every filtered list
carries its root, search box and sort hooks, and the Paper sidebar folds to
an icon rail with its state applied before paint."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from flask import Flask


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _seed(app: Flask, client) -> None:
    client.post(
        "/settings/devices/add", data={"id": "lounge", "kind": "esp32_client", "name": "Lounge"}
    )
    from app.state.page_store import Page

    app.config["PAGE_STORE"].save(Page(id="home", name="Home", device_ids=["lounge"]))
    log = app.config["EVENT_LOG"]
    log.record(type="push", source="page", target="home", status="sent", duration_s=1.2)
    log.record(type="push", source="page", target="home", status="error", error="timeout")
    app.config["BATTERY_HISTORY"].record("lounge", pct=40, timestamp=time.time() - 3600)
    app.config["BATTERY_HISTORY"].record("lounge", pct=38, timestamp=time.time())


def test_base_loads_the_list_helper(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    html = client.get("/pages").get_data(as_text=True)
    assert "/static/list-filter.js" in html
    helper = Path(app.static_folder or "static") / "list-filter.js"
    src = helper.read_text(encoding="utf-8")
    for hook in (
        "data-lf-search",
        "data-lf-filter",
        "data-lf-sort-key",
        "aria-sort",
        "data-lf-tail",
        "localStorage",
    ):
        assert hook in src


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/pages", "dashboards"),
        ("/settings/devices", "devices"),
        ("/history", "history"),
        ("/events", "events"),
        ("/plugins/", "widgets"),
        ("/themes", "themes"),
        ("/settings/firmware", "firmware"),
        ("/devices/battery", "batteries"),
    ],
)
def test_filtered_lists_carry_the_toolbar_hooks(app: Flask, path: str, key: str) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(
        Deck(id="d1", name="Mornings", pages=[DeckPage(page_id="home")])
    )
    resp = client.get(path)
    assert resp.status_code == 200, path
    html = resp.get_data(as_text=True)
    assert f'data-lf="{key}"' in html
    assert "data-lf-search" in html
    assert "data-lf-item" in html
    assert "data-lf-count" in html or key == "dashboards"
    assert "data-lf-sort" in html


def test_tables_have_sortable_heads_with_values(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    devices = client.get("/settings/devices").get_data(as_text=True)
    for k in ("name", "kind", "link", "panel", "battery", "firmware", "seen"):
        assert f'data-lf-sort-key="{k}"' in devices
    assert 'data-sort-name="Lounge"' in devices and 'data-sort-panel="' in devices
    firmware = client.get("/settings/firmware").get_data(as_text=True)
    assert '<th data-lf-sort-key="name">Device</th>' in firmware
    assert "data-sort-installed=" in firmware
    history = client.get("/history").get_data(as_text=True)
    assert 'data-lf-outcome="failed"' in history and 'data-lf-outcome="ok"' in history
    assert 'value="dur:desc" data-type="num"' in history
    assert "data-sort-time=" in history


def test_paper_sidebar_folds_to_a_rail(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    app.config["SETTINGS_STORE"].update_section("app", {"ui_design": "paper"})
    html = client.get("/pages").get_data(as_text=True)
    assert 'data-ui="paper"' in html
    # The toggle, labelled for its action and wired to the nav.
    assert 'class="sidebar-toggle" data-sidebar-toggle aria-controls="primary-nav"' in html
    assert 'aria-expanded="true" aria-label="Collapse sidebar"' in html
    # The saved state goes onto <html> in the head, before the stylesheets.
    head = html[: html.index("</head>")]
    assert "localStorage.getItem('tesserae-sidebar') === 'collapsed'" in head
    assert "classList.add('sidebar-collapsed')" in head
    assert head.index("tesserae-sidebar") < head.index("style/base.css")
    css = (Path(app.static_folder or "static") / "style" / "paper.css").read_text(encoding="utf-8")
    assert ':root[data-ui="paper"].sidebar-collapsed { --p-sidebar-w: 64px; }' in css
    assert "prefers-reduced-motion" in css
    assert "/static/sidebar-rail.js" in html
    rail = (Path(app.static_folder or "static") / "sidebar-rail.js").read_text(encoding="utf-8")
    assert "tesserae-sidebar" in rail and "aria-expanded" in rail and "Expand sidebar" in rail


def test_lineups_page_has_its_own_toolbar(app: Flask) -> None:
    """The Lineups page filters its own view-model (static/lineups.js) rather
    than the shared list helper: search, display/kind/state filters, order,
    and a count."""
    client = app.test_client()
    _sign_in(client)
    _seed(app, client)
    from app.state.deck_model import Deck, DeckPage

    app.config["DECK_STORE"].upsert(
        Deck(id="d1", name="Mornings", pages=[DeckPage(page_id="home")])
    )
    html = client.get("/decks").get_data(as_text=True)
    for hook in (
        "data-lu-toolbar",
        "data-lu-q",
        'data-lu-filter="kind"',
        'data-lu-filter="state"',
        'data-lu-filter="sort"',
        "data-lu-count",
    ):
        assert hook in html
    assert "Mornings" in html
