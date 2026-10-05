"""Server name and colour (#350): Settings › Server › This server marks every
admin page with a stripe, a name chip, a tab-title prefix and a tinted tab
icon, and leaves the admin untouched while neither is set."""

from __future__ import annotations

from flask import Flask
from flask.testing import FlaskClient

from app import server_identity


def _sign_in(client: FlaskClient) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def _set(app: Flask, **values: str) -> None:
    app.config["SETTINGS_STORE"].patch_section("app", values)


def test_normalise_colour() -> None:
    assert server_identity.normalise_colour("Yellow") == "yellow"
    assert server_identity.normalise_colour("#ABC") == "#aabbcc"
    assert server_identity.normalise_colour("6b4fa0") == "#6b4fa0"
    assert server_identity.normalise_colour("purple") == ""
    assert server_identity.normalise_colour("url(x)") == ""
    assert server_identity.normalise_colour(None) == ""


def test_resolve_is_none_until_something_is_set() -> None:
    assert server_identity.resolve({}) is None
    assert server_identity.resolve({"instance_name": "  ", "server_colour": "nope"}) is None
    named = server_identity.resolve({"instance_name": "  dev   box "})
    assert named == {"name": "dev box", "colour": None}


def test_presets_carry_a_dark_step_and_readable_text() -> None:
    yellow = server_identity.resolve({"server_colour": "yellow"})
    assert yellow is not None and yellow["colour"] is not None
    assert yellow["colour"]["light"] == "#C9971C"
    assert yellow["colour"]["dark"] == "#D9B45A"
    assert yellow["colour"]["light_fg"] == "#1C1B19"
    red = server_identity.resolve({"server_colour": "red"})
    assert red is not None and red["colour"] is not None
    assert red["colour"]["light_fg"] == "#FFFDF8"
    custom = server_identity.resolve({"server_colour": "#6b4fa0"})
    assert custom is not None and custom["colour"] is not None
    assert custom["colour"]["light"] == custom["colour"]["dark"] == "#6b4fa0"
    assert custom["colour"]["favicon"].startswith("data:image/svg+xml,")


def test_unset_leaves_the_admin_alone(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    html = client.get("/history").get_data(as_text=True)
    assert "data-server-colour" not in html
    assert 'class="server-strip"' not in html
    assert 'class="server-chip"' not in html
    assert "brand/icon.svg" in html


def test_name_and_colour_mark_the_page(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    _set(app, instance_name="dev", server_colour="blue")
    html = client.get("/history").get_data(as_text=True)
    assert 'data-server-colour="blue"' in html
    assert 'class="server-strip"' in html
    assert '<span class="server-chip" title="This server: dev">dev</span>' in html
    assert "<title>dev · " in html
    assert "--server-colour: #2B4E9B;" in html
    assert "--server-colour: #8FA8E0;" in html
    assert 'rel="icon" type="image/svg+xml" href="data:image/svg+xml,' in html
    assert '<meta name="theme-color" content="#2B4E9B"' in html


def test_name_alone_shows_the_chip_without_a_stripe(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    _set(app, instance_name="prod")
    html = client.get("/history").get_data(as_text=True)
    assert 'class="server-chip"' in html and "<title>prod · " in html
    assert 'class="server-strip"' not in html
    assert "data-server-colour" not in html


def test_name_is_escaped(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    _set(app, instance_name="<b>x</b>")
    html = client.get("/history").get_data(as_text=True)
    assert "<b>x</b>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_settings_page_offers_the_picker(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    html = client.get("/settings/server").get_data(as_text=True)
    assert "This server" in html
    assert 'name="instance_name"' in html
    for preset in ("red", "yellow", "green", "blue", "black", "custom"):
        assert f'name="server_colour" value="{preset}"' in html
    assert 'name="server_colour__custom"' in html


def test_saving_a_preset_and_a_custom_colour(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    store = app.config["SETTINGS_STORE"]
    client.post("/settings/app", data={"instance_name": "test", "server_colour": "green"})
    assert store.get_section("app")["server_colour"] == "green"
    assert store.get_section("app")["instance_name"] == "test"
    client.post(
        "/settings/app",
        data={"server_colour": "custom", "server_colour__custom": "#AA3366"},
    )
    assert store.get_section("app")["server_colour"] == "#aa3366"
    client.post("/settings/app", data={"server_colour": "custom", "server_colour__custom": "red;"})
    assert store.get_section("app")["server_colour"] == ""


def test_full_screen_editor_is_marked_too(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    _set(app, instance_name="dev", server_colour="yellow")
    cid = client.get("/pages/canvas/").location.rsplit("/", 1)[1]
    html = client.get(f"/pages/canvas/c/{cid}").get_data(as_text=True)
    assert 'class="server-strip"' in html
    assert "<title>dev · Canvas editor" in html


def test_panel_renders_are_never_marked(app: Flask, client: FlaskClient) -> None:
    _sign_in(client)
    _set(app, instance_name="dev", server_colour="yellow")
    html = client.get("/_test/render?plugin=clock&size=md").get_data(as_text=True)
    assert "server-strip" not in html
    assert "server-chip" not in html


def test_companion_api_reports_the_server_name(app: Flask) -> None:
    _set(app, instance_name="kitchen")
    from app import companion_api

    with app.app_context():
        assert companion_api._instance_name() == "kitchen"
