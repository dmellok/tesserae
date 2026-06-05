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
    # User-themes CSS endpoint is linked too so a freshly-saved theme
    # paints without a hard refresh path through static/.
    assert "/themes/user.css" in body


# -- user CSS endpoint -------------------------------------------------


def test_user_css_endpoint_serves_text_css(app: Flask) -> None:
    """Empty-store case: still 200 + text/css mimetype so the link tag
    in compose.html never produces a console error / 404. Stylesheet
    body is just a placeholder comment."""
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/themes/user.css")
    assert resp.status_code == 200
    assert resp.mimetype == "text/css"
    body = resp.get_data(as_text=True)
    assert "No user themes" in body


def test_user_css_endpoint_emits_saved_theme_blocks(app: Flask) -> None:
    """A round-trip through create + GET ensures the route sees the
    same store the user just saved into."""
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/themes/new",
        data={"name": "Sunset", "mode": "light", "bg": "#FFE6CC"},
    )
    body = client.get("/themes/user.css").get_data(as_text=True)
    assert '[data-theme="user-sunset"]' in body
    assert "--bg: #FFE6CC;" in body


# -- create / update / delete / duplicate ------------------------------


def test_create_redirects_to_edit_and_persists_theme(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/themes/new",
        data={
            "name": "My theme",
            "mode": "dark",
            "bg": "#101010",
            "accent_1": "#CC3333",
        },
    )
    assert resp.status_code == 302
    assert "/themes/user-my-theme/edit" in resp.headers["Location"]
    # The saved record carries the form values, not the dataclass
    # defaults — sanity check the route's UserTheme construction.
    saved = app.config["USER_THEMES_STORE"].get("user-my-theme")
    assert saved is not None
    assert saved.mode == "dark"
    assert saved.bg == "#101010"
    assert saved.accent_1 == "#CC3333"


def test_create_rejects_empty_name(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/themes/new", data={"name": "   "})
    assert resp.status_code == 302
    # No theme was persisted — the flash + bounce is the only
    # behaviour, but we can verify the store stays empty.
    assert app.config["USER_THEMES_STORE"].list_all() == []


def test_update_preserves_id_when_name_changes(app: Flask) -> None:
    """Renaming a theme can't change its id, or every page bound to it
    would lose its theme reference."""
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Original"})
    resp = client.post(
        "/themes/user-original/update",
        data={"name": "Renamed", "bg": "#FAFAFA"},
    )
    assert resp.status_code == 302
    saved = app.config["USER_THEMES_STORE"].get("user-original")
    assert saved is not None
    assert saved.name == "Renamed"
    assert saved.bg == "#FAFAFA"


def test_delete_removes_theme(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Throwaway"})
    resp = client.post("/themes/user-throwaway/delete")
    assert resp.status_code == 302
    assert app.config["USER_THEMES_STORE"].get("user-throwaway") is None


def test_duplicate_bundled_theme_creates_user_copy(app: Flask) -> None:
    """A user clicking Duplicate on the Light card should land in the
    builder for a fresh user theme prepopulated from Light. The new
    theme's id is ``user-…``, not ``user-light`` (collision-safe)."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/themes/light/duplicate")
    assert resp.status_code == 302
    location = resp.headers["Location"]
    assert location.startswith("/themes/user-")
    # The store now holds exactly one user theme — the new copy.
    user_themes = app.config["USER_THEMES_STORE"].list_all()
    assert len(user_themes) == 1
    assert user_themes[0].id.startswith("user-")


def test_duplicate_user_theme_clones_fields(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post(
        "/themes/new",
        data={"name": "Sunset", "bg": "#FFE6CC", "accent_1": "#CC4400"},
    )
    client.post("/themes/user-sunset/duplicate")
    user_themes = app.config["USER_THEMES_STORE"].list_all()
    assert len(user_themes) == 2
    copy = next(t for t in user_themes if t.id != "user-sunset")
    assert copy.bg == "#FFE6CC"
    assert copy.accent_1 == "#CC4400"
    assert "copy" in copy.name.lower()


def test_duplicate_increments_id_when_existing_copy_exists(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Sunset"})
    client.post("/themes/user-sunset/duplicate")
    client.post("/themes/user-sunset/duplicate")
    ids = sorted(t.id for t in app.config["USER_THEMES_STORE"].list_all())
    # The store's unique_id_for picks ``user-<slug>-2`` then ``-3`` so
    # three saves yield three distinct ids.
    assert len(ids) == 3
    assert "user-sunset" in ids


# -- browse page integration ------------------------------------------


def test_browse_page_lists_user_themes_alongside_bundled(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Mine"})
    body = client.get("/themes").get_data(as_text=True)
    assert "user-mine" in body
    # Each user-theme card carries Edit + Delete affordances; pin the
    # delete form so a layout refactor can't silently remove the action.
    assert "/themes/user-mine/delete" in body
    assert "/themes/user-mine/edit" in body


def test_browse_page_bundled_cards_get_duplicate_action(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes").get_data(as_text=True)
    assert "/themes/light/duplicate" in body


# -- builder template -------------------------------------------------


def test_builder_form_renders_with_seed_theme(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes/new").get_data(as_text=True)
    # Every dataclass token field has a matching <input name="..."> so
    # the form POST round-trips cleanly.
    for token in (
        "bg",
        "surface",
        "text_primary",
        "accent_1",
        "accent_1_soft",
        "accent_6_soft",
        "on_accent",
    ):
        assert f'name="{token}"' in body, f"builder is missing {token} input"


def test_builder_edit_404s_for_unknown_id(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/themes/user-does-not-exist/edit")
    assert resp.status_code == 404
