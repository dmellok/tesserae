"""A dashboard made before any panel is registered asks what it's for, and is
designed at that size instead of the server's default panel."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app.panel import parse_design_size


def _sign_in(client: FlaskClient) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_parse_design_size() -> None:
    assert parse_design_size("800x480") == (800, 480)
    assert parse_design_size(" 480X800 ") == (480, 800)
    assert parse_design_size("20x800") is None
    assert parse_design_size("big") is None
    assert parse_design_size(None) is None


def test_create_form_asks_for_a_size_with_no_panel(client: FlaskClient) -> None:
    _sign_in(client)
    html = client.get("/pages").get_data(as_text=True)
    assert 'name="design_size"' in html
    assert 'value="480x800"' in html


def test_grid_dashboard_keeps_the_chosen_size(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    resp = client.post("/pages/new", data={"name": "Hall", "design_size": "480x800"})
    assert resp.status_code in (302, 303)
    page = next(p for p in app.config["PAGE_STORE"].list() if p.name == "Hall")
    assert page.panel is not None
    assert (page.panel.w, page.panel.h) == (480, 800)


def test_grid_dashboard_without_a_size_follows_the_server_panel(
    app: Flask, client: FlaskClient
) -> None:
    _sign_in(client)
    client.post("/pages/new", data={"name": "Plain"})
    page = next(p for p in app.config["PAGE_STORE"].list() if p.name == "Plain")
    assert page.panel is None


def test_canvas_dashboard_gets_the_chosen_artboard(app: Flask, client: FlaskClient) -> None:
    from app import experiments

    with app.app_context():
        if not experiments.is_enabled("composer"):
            return
    _sign_in(client)
    client.post(
        "/pages/new", data={"name": "Board", "layout_kind": "canvas", "design_size": "400x300"}
    )
    page = next(p for p in app.config["PAGE_STORE"].list() if p.name == "Board")
    assert page.canvas is not None
    assert (page.canvas.w, page.canvas.h) == (400, 300)
