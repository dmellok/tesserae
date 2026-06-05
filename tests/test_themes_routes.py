"""Themes routes: unified strip + builder view, store CRUD, extract.

M-A pinned the read-only browse over the bundled registry. M-B added
the user-themes store; M-C collapsed browse + edit into one ``/themes``
view (vertical strip + builder pane) and added the image-extract
endpoint that feeds the builder. These tests cover what the UI relies
on: the strip lists everything, navigating to ``/themes/<id>`` selects
that theme, the create / update / delete / duplicate POSTs still
work, and the extract endpoint round-trips a real upload.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image

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


def _png(rgb: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (40, 40), rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# -- unified index / show view ---------------------------------------


def test_themes_index_renders_with_strip_and_builder(app: Flask) -> None:
    """The unified view has a strip + builder area + preview pane.
    Sanity-check the structural divs so a refactor that breaks the
    three-panel skeleton is caught here."""
    from app.state.theme_registry import BUNDLED_THEMES

    client = app.test_client()
    _sign_in(client)
    resp = client.get("/themes")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Every bundled theme is reachable from the strip — anchor href is
    # /themes/<id>, so any drift in the route names trips this.
    for theme in BUNDLED_THEMES:
        assert f"/themes/{theme.id}" in body, f"strip missing link for {theme.id}"
    # Layout containers exist.
    assert 'class="themes-strip"' in body
    assert "themes-builder" in body
    assert "data-theme-preview" in body


def test_show_route_selects_theme_in_strip(app: Flask) -> None:
    """Visiting /themes/<id> marks that strip item active so the user
    sees which theme they're editing."""
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes/light").get_data(as_text=True)
    # is-active modifier sits on the strip anchor for the chosen id.
    assert 'href="/themes/light"' in body
    assert "is-active" in body


def test_show_route_404s_for_unknown_id(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    assert client.get("/themes/does-not-exist").status_code == 404


def test_show_route_does_not_shadow_special_paths(app: Flask) -> None:
    """``/themes/new`` and ``/themes/user.css`` are specific routes;
    the ``<theme_id>`` catch-all would shadow them if registered
    before them. These checks guarantee Flask resolves the explicit
    routes first."""
    client = app.test_client()
    _sign_in(client)
    assert client.get("/themes/new").status_code == 200
    assert client.get("/themes/user.css").status_code == 200


def test_bundled_theme_preview_pane_targets_selected_theme(app: Flask) -> None:
    """Regression for v0.26.1: clicking a bundled theme in the strip
    used to leave the preview painted with Light because the JS
    seeded the pane from the form's (default-Light) values on every
    load, overriding the data-theme cascade. The fix gates the seed
    behind the read-only check; pin the data-theme attribute on the
    preview pane so the cascade has the right id to paint from."""
    import re

    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes/nord").get_data(as_text=True)
    pane_tag = re.search(r'<div class="theme-preview-pane"[^>]*>', body)
    assert pane_tag is not None
    assert 'data-theme="nord"' in pane_tag.group(0)


def test_bundled_theme_preview_header_shows_actual_name(app: Flask) -> None:
    """Preview header used to read "New theme" for every bundled
    selection because the seed UserTheme carried the placeholder
    name. Fixed by lifting the bundled theme's actual name in
    _render_index."""
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes/nord").get_data(as_text=True)
    # Find the preview-name <h3> and assert it says "Nord".
    assert "<h3 data-preview-name>Nord</h3>" in body


def test_bundled_theme_view_has_duplicate_to_edit_cta(app: Flask) -> None:
    """Bundled themes can't be saved over; the builder is shown for
    reference but the prominent action is "Duplicate to edit"."""
    import re

    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes/light").get_data(as_text=True)
    assert "Duplicate to edit" in body
    # The readonly marker must be on the actual <form> tag so JS
    # disables every input. Match the form opening tag specifically
    # since the inline JS below references the attribute name as a
    # string and would false-positive a substring check.
    form_tag = re.search(r"<form[^>]*class=\"theme-builder\"[^>]*>", body)
    assert form_tag is not None
    assert "data-builder-readonly" in form_tag.group(0)


def test_user_theme_view_is_editable_with_save_button(app: Flask) -> None:
    import re

    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Mine"})
    body = client.get("/themes/user-mine").get_data(as_text=True)
    assert "Update theme" in body
    # The form element must not carry the readonly marker. The JS code
    # below references the attribute name as a string, so a substring
    # check would false-positive — match the actual ``<form...>``
    # opening tag instead.
    form_tag = re.search(r"<form[^>]*>", body)
    assert form_tag is not None
    assert "data-builder-readonly" not in form_tag.group(0)
    # Delete + duplicate forms are present.
    assert "/themes/user-mine/delete" in body
    assert "/themes/user-mine/duplicate" in body


def test_index_default_selects_first_user_theme_when_present(app: Flask) -> None:
    """A user iterating on their own themes shouldn't land back on
    Light every time they revisit /themes."""
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Sunset"})
    body = client.get("/themes").get_data(as_text=True)
    # Sunset's strip item is the one marked active.
    assert 'href="/themes/user-sunset"' in body
    # Form action targets Sunset's update endpoint.
    assert "/themes/user-sunset/update" in body


def test_index_default_falls_back_to_light_when_no_user_themes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    body = client.get("/themes").get_data(as_text=True)
    # Light is the selected theme (action_url + preview both reflect it).
    assert "Duplicate to edit" in body  # only shown for bundled selections


def test_edit_alias_redirects_to_show(app: Flask) -> None:
    """Old M-B URL pattern still works as a redirect so bookmarks
    don't 404."""
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Sunset"})
    resp = client.get("/themes/user-sunset/edit")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/themes/user-sunset")


# -- user.css endpoint -------------------------------------------------


def test_user_css_endpoint_serves_text_css(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.get("/themes/user.css")
    assert resp.status_code == 200
    assert resp.mimetype == "text/css"


def test_user_css_endpoint_emits_saved_theme_blocks(app: Flask) -> None:
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


def test_create_redirects_to_show_and_persists_theme(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/themes/new",
        data={"name": "My theme", "mode": "dark", "bg": "#101010", "accent_1": "#CC3333"},
    )
    assert resp.status_code == 302
    # The route still bounces to the M-B-shaped /edit URL; that URL
    # 302s onward to /themes/<id> via edit_alias. Follow the chain.
    final = client.get(resp.headers["Location"], follow_redirects=True)
    assert final.status_code == 200
    saved = app.config["USER_THEMES_STORE"].get("user-my-theme")
    assert saved is not None
    assert saved.mode == "dark"
    assert saved.bg == "#101010"
    assert saved.accent_1 == "#CC3333"


def test_update_preserves_id_when_name_changes(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Original"})
    client.post(
        "/themes/user-original/update",
        data={"name": "Renamed", "bg": "#FAFAFA"},
    )
    saved = app.config["USER_THEMES_STORE"].get("user-original")
    assert saved is not None
    assert saved.name == "Renamed"
    assert saved.bg == "#FAFAFA"


def test_delete_removes_theme(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    client.post("/themes/new", data={"name": "Throwaway"})
    client.post("/themes/user-throwaway/delete")
    assert app.config["USER_THEMES_STORE"].get("user-throwaway") is None


def test_duplicate_bundled_theme_creates_user_copy(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/themes/light/duplicate")
    assert resp.status_code == 302
    user_themes = app.config["USER_THEMES_STORE"].list_all()
    assert len(user_themes) == 1
    assert user_themes[0].id.startswith("user-")


# -- extract-palette endpoint -----------------------------------------


def test_extract_returns_palette_for_uploaded_image(app: Flask) -> None:
    """Round-trip: upload a solid colour, get back JSON with the 20
    Spectra token keys + the detected mode."""
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/themes/extract-palette",
        data={"image": (io.BytesIO(_png((220, 60, 60))), "test.png")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert "tokens" in payload
    assert payload["tokens"]["bg"].startswith("#")
    # 20 token keys (6 accents × 2 + 8 metadata + surface trio).
    assert len(payload["tokens"]) >= 20
    assert payload["mode"] in ("light", "dark")


def test_extract_rejects_empty_upload(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post("/themes/extract-palette", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_extract_rejects_non_image_upload(app: Flask) -> None:
    client = app.test_client()
    _sign_in(client)
    resp = client.post(
        "/themes/extract-palette",
        data={"image": (io.BytesIO(b"not an image"), "test.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "decode" in resp.get_json()["error"].lower()


def test_extract_honours_explicit_mode_override(app: Flask) -> None:
    """The form's mode dropdown overrides the extractor's luminance
    heuristic when present — gives users an escape hatch when the
    auto-detection picks the wrong side of the light/dark fence."""
    client = app.test_client()
    _sign_in(client)
    # Bright image — auto-detect would say "light"; force "dark".
    resp = client.post(
        "/themes/extract-palette",
        data={
            "image": (io.BytesIO(_png((230, 230, 230))), "test.png"),
            "mode": "dark",
        },
        content_type="multipart/form-data",
    )
    payload = resp.get_json()
    # In dark mode, bg luminance < text_primary luminance (dark canvas,
    # bright text). The extractor's own bands stamp this.
    bg = payload["tokens"]["bg"]
    text = payload["tokens"]["text_primary"]
    assert _luminance(bg) < _luminance(text)


def _luminance(hex_color: str) -> float:
    s = hex_color.lstrip("#")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
