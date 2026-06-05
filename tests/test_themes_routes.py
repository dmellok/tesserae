"""Themes routes: browse page renders + reads from the registry.

M-A only covers the read-only catalogue page; M-B will add create /
update / delete tests when the user-themes store ships.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient

from app.main import REPO_ROOT, create_app


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=True,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client: FlaskClient) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_themes_index_renders_with_a_card_per_bundled_theme(app: Flask) -> None:
    """A smoke pass: the page comes up 200 and every theme id from the
    registry shows up somewhere in the HTML. Doesn't pin on layout —
    just on data flow from registry → template."""
    from app.state.theme_registry import BUNDLED_THEMES

    client = app.test_client()
    _sign_in(client)
    resp = client.get("/themes")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for theme in BUNDLED_THEMES:
        assert theme.id in body, f"theme {theme.id!r} missing from /themes output"


def test_themes_index_loads_spectra_token_stylesheet(app: Flask) -> None:
    """Browse page needs spectra-tokens.css for the data-theme cascade
    to actually paint the swatches. Pin that the link tag is in the
    head so a refactor of head_extra doesn't silently break visuals."""
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes").get_data(as_text=True)
    assert "spectra-tokens.css" in body
    assert "themes.css" in body
