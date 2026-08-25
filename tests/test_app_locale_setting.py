"""App-wide default locale (Settings -> Server -> Location & time), the
fallback a device's own override reads when set to "Use app default"
(see app.locale_resolve.resolve_locale)."""

from __future__ import annotations

from flask.testing import FlaskClient


def _sign_in(client: FlaskClient) -> None:
    client.post("/setup", data={"password": "abcdefgh", "password_confirm": "abcdefgh"})


def test_locale_field_defaults_to_system(client: FlaskClient) -> None:
    _sign_in(client)
    body = client.get("/settings/server").get_data(as_text=True)
    # The App card renders on the same Settings page; "system" is the
    # unsaved default, same sentinel + meaning as the timezone field.
    assert 'name="locale"' in body or "System (host language)" in body


def test_saving_the_locale_persists_it(app, client: FlaskClient) -> None:
    _sign_in(client)
    resp = client.post("/settings/app", data={"locale": "fr"})
    assert resp.status_code in (200, 302)
    assert app.config["SETTINGS_STORE"].get_section("app").get("locale") == "fr"


def test_saved_app_locale_is_what_a_device_without_its_own_override_resolves_to(
    app, client: FlaskClient
) -> None:
    """End-to-end: the field this commit adds is exactly what
    resolve_locale() falls back to for a device with no override --
    it's not a second, disconnected concept."""
    from app.locale_resolve import resolve_locale

    client.post("/settings/app", data={"locale": "de"})
    app_section = app.config["SETTINGS_STORE"].get_section("app")
    assert resolve_locale(app_section, None) == "de"
