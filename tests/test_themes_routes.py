"""Theme builder: list / create / edit / delete / duplicate + UserThemeStore
+ plugin loader picks up user themes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask

from app.main import REPO_ROOT, create_app
from app.state.user_themes import (
    PALETTE_TOKENS,
    UserTheme,
    UserThemeStore,
    slug_id,
    validate_palette,
)

_FULL_PALETTE = {token: "#abcdef" for token in PALETTE_TOKENS}


@pytest.fixture
def app(tmp_path: Path) -> Flask:
    a = create_app(
        testing=False,
        data_root=tmp_path,
        plugins_dir=REPO_ROOT / "plugins",
        renderers_dir=REPO_ROOT / "renderers",
        devices_dir=REPO_ROOT / "devices",
    )
    a.config["TESTING"] = True
    return a


def _sign_in(client) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


# --- UserThemeStore unit tests ---------------------------------------


def test_user_theme_store_round_trip(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    assert store.load() == []
    store.upsert(UserTheme(id="mine", name="Mine", mode="light", palette=_FULL_PALETTE.copy()))
    rows = store.load()
    assert len(rows) == 1
    assert rows[0].id == "mine"
    assert rows[0].palette == _FULL_PALETTE


def test_user_theme_delete(tmp_path: Path) -> None:
    store = UserThemeStore(tmp_path / "user.json")
    store.upsert(UserTheme(id="x", name="X", mode="light", palette=_FULL_PALETTE.copy()))
    assert store.delete("x") is True
    assert store.delete("x") is False
    assert store.load() == []


def test_validate_palette_catches_missing_tokens() -> None:
    assert validate_palette({"bg": "#fff"}) is not None
    assert validate_palette(_FULL_PALETTE) is None


def test_validate_palette_catches_bad_hex() -> None:
    palette = dict(_FULL_PALETTE)
    palette["accent"] = "not-a-colour"
    err = validate_palette(palette)
    assert err is not None and "accent" in err


def test_slug_id_normalises_names() -> None:
    assert slug_id("Hello World") == "hello_world"
    assert slug_id("---rough!!!input---") == "rough_input"
    assert slug_id("") == "theme"


# --- /themes route tests ---------------------------------------------


def test_themes_page_lists_builtin_themes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes").get_data(as_text=True)
    # The curated subset shipped with M13.
    for name in ("Paper", "Cobalt", "Embers", "Cyber"):
        assert name in body
    # All built-ins; no user themes yet.
    assert "built-in" in body


def test_create_user_theme_persists_and_shadows_builtin(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    form = {"name": "Paper Plus", "id": "", "mode": "light", **_FULL_PALETTE}
    resp = client.post("/themes/new", data=form, follow_redirects=False)
    assert resp.status_code == 302
    # Lands on disk.
    path = tmp_path / "plugins" / "themes_core" / "user.json"
    assert path.exists()
    raw = json.loads(path.read_text())
    assert raw[0]["name"] == "Paper Plus"
    # In-memory registry got the new theme without restart.
    registry = app.config["PLUGIN_REGISTRY"]
    assert registry.get_theme(slug_id("Paper Plus")) is not None


def test_create_user_theme_rejects_missing_name(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/themes/new",
        data={"name": "", "mode": "light", **_FULL_PALETTE},
        follow_redirects=True,
    )
    assert b"name is required" in resp.data.lower() or b"Theme name is required" in resp.data


def test_create_user_theme_rejects_invalid_palette(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    bad = dict(_FULL_PALETTE)
    bad["accent"] = "not-a-colour"
    resp = client.post(
        "/themes/new",
        data={"name": "Broken", "mode": "light", **bad},
        follow_redirects=True,
    )
    assert b"Invalid palette" in resp.data


def test_update_user_theme(app: Flask, tmp_path: Path) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Sandbox", "mode": "light", **_FULL_PALETTE})
    # The id is derived as 'sandbox'.
    updated = dict(_FULL_PALETTE)
    updated["accent"] = "#112233"
    client.post(
        "/themes/sandbox/update",
        data={"name": "Sandbox 2", "mode": "dark", **updated},
    )
    store = UserThemeStore(tmp_path / "plugins" / "themes_core" / "user.json")
    saved = store.get("sandbox")
    assert saved is not None
    assert saved.name == "Sandbox 2"
    assert saved.mode == "dark"
    assert saved.palette["accent"] == "#112233"
    # In-memory registry reflects the update.
    registry_theme = app.config["PLUGIN_REGISTRY"].get_theme("sandbox")
    assert registry_theme is not None
    assert registry_theme.palette["accent"] == "#112233"


def test_delete_user_theme_unshadows_builtin(app: Flask) -> None:
    """If a user theme shadows a built-in id, deleting the user theme
    re-exposes the original built-in. Important so users can experiment
    without permanently overwriting the bundled themes."""
    client = app.test_client()
    _sign_in(client)
    # Duplicate the built-in 'cobalt' (creates a new id like cobalt_copy).
    resp = client.post("/themes/cobalt/duplicate", follow_redirects=False)
    assert resp.status_code == 302
    edit_target = resp.location.split("edit=")[-1]
    # Now upsert that user theme under the SAME id as the built-in
    # (simulates a 'shadow this built-in' workflow). Use update with a
    # different name/palette.
    custom = {k: "#000000" for k in PALETTE_TOKENS}
    client.post(
        "/themes/new", data={"id": "cobalt", "name": "Cobalt mine", "mode": "light", **custom}
    )
    registry = app.config["PLUGIN_REGISTRY"]
    cobalt = registry.get_theme("cobalt")
    assert cobalt is not None and cobalt.is_user
    # Delete shadow → built-in returns.
    client.post("/themes/cobalt/delete")
    cobalt_after = registry.get_theme("cobalt")
    assert cobalt_after is not None
    assert cobalt_after.is_user is False
    assert cobalt_after.name == "Cobalt"
    # Clean up the duplicate too.
    client.post(f"/themes/{edit_target}/delete")


def test_duplicate_builtin_creates_user_theme(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/themes/embers/duplicate", follow_redirects=False)
    assert resp.status_code == 302
    # Redirect URL includes ?edit=<new_id>.
    assert "edit=" in resp.location
    new_id = resp.location.split("edit=")[-1]
    registry = app.config["PLUGIN_REGISTRY"]
    new_theme = registry.get_theme(new_id)
    assert new_theme is not None
    assert new_theme.is_user
    # Palette copied verbatim from the built-in.
    builtin = next(t for t in registry.themes.values() if t.id == "embers" and not t.is_user)
    # The freshly duplicated user theme should match the original's palette.
    # Note: if we just shadowed, builtin would be None — but we generated
    # a distinct id, so the built-in is still present.
    assert new_theme.palette == builtin.palette


def test_themes_nav_link_present(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/settings", follow_redirects=True).get_data(as_text=True)
    assert "/themes" in body
