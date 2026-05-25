"""Composer route shape — markup well-formedness + theme variable wiring."""

from __future__ import annotations

from flask.testing import FlaskClient


def test_test_render_route_returns_cell_markup(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=clock&size=md")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="clock"' in body
    assert 'class="cell' in body
    # The composer must inject the resolved palette as CSS custom properties
    # on the cell so plugins can use var(--theme-*) without explicit wiring.
    assert "--theme-bg:" in body
    assert "--theme-accent:" in body


def test_test_render_unknown_plugin_still_routes(client: FlaskClient) -> None:
    # /_test/render mounts whatever plugin id you give it; the client-side
    # composer surfaces the load failure rather than the server refusing.
    resp = client.get("/_test/render?plugin=does_not_exist&size=md")
    assert resp.status_code == 200
    assert 'data-plugin="does_not_exist"' in resp.get_data(as_text=True)


def test_test_render_rejects_bad_size(client: FlaskClient) -> None:
    resp = client.get("/_test/render?plugin=clock&size=enormous")
    assert resp.status_code == 400


def test_compose_route_404s_on_unknown_page(client: FlaskClient) -> None:
    resp = client.get("/compose/nonexistent")
    assert resp.status_code == 404
