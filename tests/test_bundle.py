"""Export and import of dashboards and lineups (#541)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from flask import Flask

from app import bundle, page_assets
from app.state.deck_model import Deck, DeckPage
from app.state.page_store import Cell, Page
from app.state.panel_store import CanvasLayout, CodeSource, Element

LINEUPS_URL = "/decks"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _sign_in(client: Any) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _stores(app: Flask) -> dict[str, Any]:
    return {
        "page_store": app.config["PAGE_STORE"],
        "deck_store": app.config["DECK_STORE"],
        "registry": app.config["PLUGIN_REGISTRY"],
    }


def _grid(page_id: str = "aaaaaaaaaaaa", **fields: Any) -> Page:
    return Page(
        id=page_id,
        name=fields.pop("name", "Kitchen"),
        device_ids=["panel_one"],
        status_bar_enabled=True,
        status_bar_cell_id="bar",
        cells=[
            Cell(id="bar", plugin="tesserae_status", x=0, y=0, w=800, h=48),
            Cell(
                id="c1",
                plugin="rest_service",
                x=0,
                y=48,
                w=800,
                h=432,
                options={"url": "https://example.test/api?key=s3cret", "title": "Orders"},
                on_tap="page:bbbbbbbbbbbb",
            ),
        ],
        **fields,
    )


def _canvas(app: Flask, page_id: str = "bbbbbbbbbbbb") -> Page:
    asset = page_assets.save_bytes(Path(app.config["DATA_ROOT"]), page_id, PNG, "image/png")
    return Page(
        id=page_id,
        name="Hall",
        layout_kind="canvas",
        canvas=CanvasLayout(
            w=800,
            h=480,
            bg_image=str(asset["url"]),
            els=[
                Element(
                    id="e1",
                    kind="code",
                    x=0,
                    y=0,
                    w=100,
                    h=100,
                    sources=[
                        CodeSource(
                            name="orders",
                            url="https://example.test/orders",
                            headers={"Authorization": "Bearer s3cret"},
                        )
                    ],
                )
            ],
        ),
    )


def _export(app: Flask, pages: list[str], decks: list[str] | None = None) -> dict[str, Any]:
    doc, _notes = bundle.build_bundle(
        page_ids=pages,
        deck_ids=decks or [],
        data_root=Path(app.config["DATA_ROOT"]),
        version="test",
        **_stores(app),
    )
    return json.loads(json.dumps(doc))


def _apply(app: Flask, doc: dict[str, Any], **replace: set[str]) -> bundle.ImportReport:
    return bundle.apply_bundle(
        bundle.read_bundle(json.dumps(doc).encode()),
        replace_pages=replace.get("pages", set()),
        replace_lineups=replace.get("lineups", set()),
        data_root=Path(app.config["DATA_ROOT"]),
        **_stores(app),
    )


def test_export_leaves_out_bindings_and_secrets(app: Flask) -> None:
    store = app.config["PAGE_STORE"]
    store.save(_grid())
    store.save(_canvas(app))
    doc, notes = bundle.build_bundle(
        page_ids=["aaaaaaaaaaaa", "bbbbbbbbbbbb"],
        deck_ids=[],
        data_root=Path(app.config["DATA_ROOT"]),
        **_stores(app),
    )
    text = json.dumps(doc)
    assert "s3cret" not in text
    assert "panel_one" not in text and "device_ids" not in text
    assert doc["tesserae_bundle"] == bundle.BUNDLE_VERSION
    assert {"rest_service", "tesserae_status"} <= set(doc["requires"])
    grid, canvas = doc["dashboards"]
    assert grid["page"]["cells"][1]["options"] == {"url": "", "title": "Orders"}
    assert canvas["page"]["canvas"]["els"][0]["sources"][0]["headers"] == {}
    assert len(canvas["assets"]) == 1
    assert any("url" in n for n in notes) and any("headers" in n for n in notes)
    # The stored dashboards are untouched.
    assert "s3cret" in store.get("aaaaaaaaaaaa").cells[1].options["url"]


def test_import_into_an_empty_server_keeps_ids_and_pictures(app: Flask, tmp_path: Path) -> None:
    store = app.config["PAGE_STORE"]
    store.save(_grid())
    store.save(_canvas(app))
    doc = _export(app, ["aaaaaaaaaaaa", "bbbbbbbbbbbb"])
    root = Path(app.config["DATA_ROOT"])
    asset = next(iter(doc["dashboards"][1]["assets"]))
    for page_id in ("aaaaaaaaaaaa", "bbbbbbbbbbbb"):
        store.delete(page_id)
    page_assets.delete_all(root, "bbbbbbbbbbbb")

    report = _apply(app, doc)
    assert sorted(report.created_pages) == ["Hall", "Kitchen"] and not report.replaced_pages
    grid = store.get("aaaaaaaaaaaa")
    assert grid.device_ids == [] and grid.status_bar_cell_id == "bar"
    assert grid.cells[1].on_tap == "page:bbbbbbbbbbbb"
    assert (page_assets.assets_dir(root, "bbbbbbbbbbbb") / asset).read_bytes() == PNG


def test_a_copy_gets_new_ids_and_every_reference_follows(app: Flask) -> None:
    store = app.config["PAGE_STORE"]
    store.save(_grid())
    store.save(_canvas(app))
    app.config["DECK_STORE"].upsert(
        Deck(
            id="evening",
            name="Evening",
            device_ids=["panel_one"],
            pages=[DeckPage(page_id="aaaaaaaaaaaa"), DeckPage(page_id="bbbbbbbbbbbb")],
            home_page_id="bbbbbbbbbbbb",
        )
    )
    client = app.test_client()
    _sign_in(client)
    assert "/bundles/export?lineup=evening" in client.get(LINEUPS_URL).get_data(as_text=True)
    doc = json.loads(client.get("/bundles/export?lineup=evening").get_data())
    assert [d["page"]["id"] for d in doc["dashboards"]] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    assert "device_ids" not in doc["lineups"][0]

    report = _apply(app, doc)
    new_grid, new_canvas = report.page_ids["aaaaaaaaaaaa"], report.page_ids["bbbbbbbbbbbb"]
    assert new_grid != "aaaaaaaaaaaa" and new_canvas != "bbbbbbbbbbbb"
    grid = store.get(new_grid)
    assert grid.name == "Kitchen (imported)"
    assert grid.cells[1].on_tap == f"page:{new_canvas}"
    # New cell ids, and the status bar still points at its own cell.
    assert grid.status_bar_cell_id == grid.cells[0].id != "bar"
    canvas = store.get(new_canvas)
    assert canvas.canvas.bg_image.startswith(f"/page-assets/{new_canvas}/")
    root = Path(app.config["DATA_ROOT"])
    assert (root / "core" / "page_assets" / new_canvas).is_dir()
    deck = app.config["DECK_STORE"].get(report.lineup_ids["evening"])
    assert deck.id == "evening_imported" and deck.device_ids == []
    assert [p.page_id for p in deck.pages] == [new_grid, new_canvas]
    assert deck.home_page_id == new_canvas
    # The originals are as they were.
    assert store.get("aaaaaaaaaaaa").device_ids == ["panel_one"]
    assert app.config["DECK_STORE"].get("evening").device_ids == ["panel_one"]


def test_replace_keeps_the_targets_displays(app: Flask) -> None:
    store = app.config["PAGE_STORE"]
    store.save(_grid(name="Kitchen v2", gap=12))
    store.save(_canvas(app))
    app.config["DECK_STORE"].upsert(
        Deck(id="evening", name="Evening v2", pages=[DeckPage(page_id="aaaaaaaaaaaa")])
    )
    doc = _export(app, [], ["evening"])
    # "Production": the same ids, older content, bound to its own displays.
    store.save(_grid())
    live = store.get("aaaaaaaaaaaa").model_copy(update={"device_ids": ["prod_panel"], "gap": 0})
    store.save(live)
    app.config["DECK_STORE"].upsert(
        Deck(
            id="evening",
            name="Evening",
            device_ids=["prod_panel"],
            pages=[DeckPage(page_id="aaaaaaaaaaaa")],
        )
    )

    report = _apply(app, doc, pages={"aaaaaaaaaaaa"}, lineups={"evening"})
    assert report.replaced_pages == ["Kitchen v2"] and report.replaced_lineups == ["Evening v2"]
    page = store.get("aaaaaaaaaaaa")
    assert (page.name, page.gap, page.device_ids) == ("Kitchen v2", 12, ["prod_panel"])
    assert page.cells[1].id == "c1"
    deck = app.config["DECK_STORE"].get("evening")
    assert (deck.name, deck.device_ids) == ("Evening v2", ["prod_panel"])
    assert len(store.list()) == 2


def test_missing_widgets_import_and_are_reported(app: Flask) -> None:
    doc = _export_one_with_plugin(app, "not_installed_here")
    plan = bundle.describe(bundle.read_bundle(json.dumps(doc).encode()), **_stores(app))
    assert plan["dashboards"][0]["missing"] == ["not_installed_here"]
    report = _apply(app, doc)
    assert report.missing == ["not_installed_here"] and report.created_pages == ["Odd"]


def _export_one_with_plugin(app: Flask, plugin: str) -> dict[str, Any]:
    store = app.config["PAGE_STORE"]
    store.save(
        Page(
            id="cccccccccccc",
            name="Odd",
            cells=[Cell(id="c", plugin=plugin, x=0, y=0, w=10, h=10)],
        )
    )
    doc = _export(app, ["cccccccccccc"])
    store.delete("cccccccccccc")
    return doc


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"not json", "not JSON"),
        (b'{"dashboards": []}', "not a Tesserae export"),
        (b'{"tesserae_bundle": 99, "dashboards": [{"page": {"id": "a"}}]}', "newer"),
        (b'{"tesserae_bundle": 1, "dashboards": [], "lineups": []}', "no dashboards"),
        (b'{"tesserae_bundle": 1, "dashboards": [{"page": {}}]}', "no id"),
    ],
)
def test_unreadable_files_say_why(raw: bytes, why: str) -> None:
    with pytest.raises(bundle.BundleError, match=why):
        bundle.read_bundle(raw)


def test_a_bad_dashboard_or_picture_imports_nothing(app: Flask) -> None:
    store = app.config["PAGE_STORE"]
    good = {"id": "dddddddddddd", "name": "Good", "cells": []}
    bad = {"id": "eeeeeeeeeeee", "name": "Bad", "cells": [{"id": "c", "x": -5}]}
    doc = {"tesserae_bundle": 1, "dashboards": [{"page": good}, {"page": bad}]}
    with pytest.raises(bundle.BundleError, match="Bad"):
        _apply(app, doc)
    assert store.get("dddddddddddd") is None

    for name in ("../../evil.png", ".hidden.png", "script.html"):
        doc = {
            "tesserae_bundle": 1,
            "dashboards": [{"page": good, "assets": {name: "AAAA"}}],
        }
        with pytest.raises(bundle.BundleError, match="picture"):
            _apply(app, doc)
    doc = {"tesserae_bundle": 1, "dashboards": [{"page": {**good, "id": "../../x"}}]}
    with pytest.raises(bundle.BundleError, match="id"):
        _apply(app, doc)
    assert store.list() == []


def test_a_lineup_pointing_nowhere_is_skipped(app: Flask) -> None:
    doc = {
        "tesserae_bundle": 1,
        "dashboards": [{"page": {"id": "dddddddddddd", "name": "Good", "cells": []}}],
        "lineups": [{"id": "lost", "name": "Lost", "pages": [{"page_id": "nowhere"}]}],
    }
    report = _apply(app, doc)
    assert report.created_pages == ["Good"] and not report.created_lineups
    assert report.skipped and "Lost" in report.skipped[0]
    assert app.config["DECK_STORE"].get("lost") is None


def test_export_then_import_through_the_pages(app: Flask) -> None:
    store = app.config["PAGE_STORE"]
    store.save(_grid())
    store.save(_canvas(app))
    client = app.test_client()
    _sign_in(client)

    listing = client.get("/pages").get_data(as_text=True)
    assert "/bundles/import" in listing and "/bundles/export?page=aaaaaaaaaaaa" in listing

    response = client.get("/bundles/export?page=aaaaaaaaaaaa")
    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == 'attachment; filename="tesserae-kitchen.json"'
    exported = response.get_data()
    assert json.loads(exported)["left_out"]

    assert client.get("/bundles/import").status_code == 200
    preview = client.post(
        "/bundles/import",
        data={"file": (io.BytesIO(exported), "kitchen.json")},
        content_type="multipart/form-data",
    )
    body = preview.get_data(as_text=True)
    assert preview.status_code == 200 and "Replace “Kitchen”" in body and "Left out" in body
    token = body.split('name="token" value="')[1].split('"')[0]
    pending = Path(app.config["DATA_ROOT"]) / "core" / "bundle_imports" / f"{token}.json"
    assert pending.is_file()

    # Add as a copy: nothing is named for replacement.
    done = client.post("/bundles/import/apply", data={"token": token})
    assert done.status_code == 302 and not pending.exists()
    assert sorted(p.name for p in store.list()) == ["Hall", "Kitchen", "Kitchen (imported)"]
    # The token is spent, and a made-up one reads nothing off disk.
    for spent in (token, "../../settings"):
        again = client.post("/bundles/import/apply", data={"token": spent})
        assert again.status_code == 302 and len(store.list()) == 3

    refused = client.post(
        "/bundles/import",
        data={"file": (io.BytesIO(b"nope"), "x.json")},
        content_type="multipart/form-data",
    )
    assert refused.status_code == 302
    assert client.get("/bundles/export?page=missing").status_code == 302


def test_duplicate_keeps_the_status_bar_pointing_at_the_copy(app: Flask) -> None:
    store = app.config["PAGE_STORE"]
    store.save(_grid())
    client = app.test_client()
    _sign_in(client)
    client.post("/pages/aaaaaaaaaaaa/duplicate")
    copy = next(p for p in store.list() if p.id != "aaaaaaaaaaaa")
    assert copy.status_bar_cell_id == copy.cells[0].id != "bar"
