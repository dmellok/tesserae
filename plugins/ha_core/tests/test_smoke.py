"""ha_core smoke: config gating, entity_choices shaping, error coercion -
no network (get_states is monkeypatched)."""

from __future__ import annotations

import io
import urllib.error

from flask import Flask

_STATES = [
    {"entity_id": "sensor.lounge_temp", "state": "21.4", "attributes": {"friendly_name": "Lounge"}},
    {"entity_id": "climate.hallway", "state": "heat", "attributes": {"friendly_name": "Hallway"}},
    {"entity_id": "light.desk", "state": "on", "attributes": {}},
]


def _core(app: Flask):
    return app.config["PLUGIN_REGISTRY"].get("ha_core").server_module


def test_unconfigured_choices_guide_the_user(app: Flask) -> None:
    core = _core(app)
    with app.app_context():
        assert not core.is_configured()
        choices = core.entity_choices()
        assert len(choices) == 1
        assert choices[0]["value"] == ""
        # Points at where the setting actually lives: the Settings area is
        # labelled Widgets, so "Plugins" would send people somewhere that
        # isn't in the nav.
        assert "Settings → Widgets" in choices[0]["label"]


def test_entity_choices_lists_sorts_and_filters(app: Flask, monkeypatch) -> None:
    core = _core(app)
    with app.app_context():
        store = app.config["SETTINGS_STORE"]
        store.update_section("plugins", {"ha_core": {"base_url": "http://ha", "token_secret": "t"}})
        monkeypatch.setattr(core, "get_states", lambda: _STATES)

        assert core.is_configured()
        all_choices = core.entity_choices()
        values = [c["value"] for c in all_choices]
        assert values == ["climate.hallway", "light.desk", "sensor.lounge_temp"]
        # friendly_name folded into the label; bare id when no friendly_name.
        labels = {c["value"]: c["label"] for c in all_choices}
        assert labels["sensor.lounge_temp"] == "Lounge (sensor.lounge_temp)"
        assert labels["light.desk"] == "light.desk"

        climate_only = core.entity_choices(domains=("climate",))
        assert [c["value"] for c in climate_only] == ["climate.hallway"]


def test_entity_choices_survive_unreachable_ha(app: Flask, monkeypatch) -> None:
    core = _core(app)
    with app.app_context():
        app.config["SETTINGS_STORE"].update_section(
            "plugins", {"ha_core": {"base_url": "http://ha", "token_secret": "t"}}
        )

        def boom() -> list:
            raise urllib.error.URLError("down")

        monkeypatch.setattr(core, "get_states", boom)
        choices = core.entity_choices()
        assert len(choices) == 1
        assert "unreachable" in choices[0]["label"].lower()


def test_coerce_error_is_friendly(app: Flask) -> None:
    core = _core(app)
    err = urllib.error.HTTPError("http://ha/api", 401, "Unauthorized", {}, io.BytesIO(b""))  # type: ignore[arg-type]
    try:
        msg = core.coerce_error(err)
    finally:
        err.close()
    assert "401" in msg
    assert "token" in msg


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _configure(app: Flask) -> None:
    app.config["SETTINGS_STORE"].update_section(
        "plugins", {"ha_core": {"base_url": "http://ha", "token_secret": "t"}}
    )


def test_call_service_omits_return_response(app: Flask, monkeypatch) -> None:
    """A fire-and-forget service call (light.turn_on) must NOT ask for
    return_response, which HA 400s for services that don't support it
    (the touch ha_failed regression)."""
    core = _core(app)
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = req.data
        return _FakeResp(b"[]")

    with app.app_context():
        _configure(app)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        core.call_service(
            "light", "turn_on", data={"entity_id": ["light.a"], "brightness_pct": 100}
        )

    assert seen["method"] == "POST"
    assert seen["url"] == "http://ha/api/services/light/turn_on"
    assert "return_response" not in str(seen["url"])
    assert b"brightness_pct" in seen["body"]


def test_call_service_with_response_keeps_return_response(app: Flask, monkeypatch) -> None:
    core = _core(app)
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):
        seen["url"] = req.full_url
        return _FakeResp(b"{}")

    with app.app_context():
        _configure(app)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        core.call_service_with_response("todo", "get_items", data={"entity_id": "todo.x"})

    assert "return_response" in str(seen["url"])


def test_render_template_posts_to_template_endpoint(app: Flask, monkeypatch) -> None:
    """render_template POSTs the template to /api/template and returns the
    rendered text verbatim (used by the HA device picker)."""
    core = _core(app)
    seen: dict[str, object] = {}

    def fake_urlopen(req, timeout=0, context=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = req.data
        return _FakeResp(b'[{"device_id": "abc"}]')

    with app.app_context():
        _configure(app)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        out = core.render_template("{{ integration_entities('opendisplay') }}")

    assert out == '[{"device_id": "abc"}]'
    assert seen["method"] == "POST"
    assert seen["url"] == "http://ha/api/template"
    assert b"integration_entities" in seen["body"]


class _TrackedBody(io.BytesIO):
    """Stands in for the socket-backed body of an HTTP error response."""


def _http_error(url: str, code: int, body: _TrackedBody) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "Unauthorized", {}, body)  # type: ignore[arg-type]


def test_http_error_response_is_closed(app: Flask, monkeypatch) -> None:
    """A rejected request must close the error response it raises. Leaving it
    open held one socket in CLOSE_WAIT per poll until the process ran out of
    file descriptors (#444)."""
    core = _core(app)
    core._reset_auth_cooldown()
    bodies: list[_TrackedBody] = []

    def fake_urlopen(req, timeout=0, context=None):
        body = _TrackedBody(b"404: Not Found")
        bodies.append(body)
        raise _http_error(req.full_url, 404, body)

    with app.app_context():
        _configure(app)
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        try:
            core.get_state("sensor.gone")
        except urllib.error.HTTPError as err:
            # Checked while the error is still referenced: dropping the last
            # reference closes the body too, which hid the leak whenever the
            # error got caught somewhere that kept it alive.
            assert bodies and all(b.closed for b in bodies)
            assert err.code == 404
            assert "not found" in core.coerce_error(err)
        else:
            raise AssertionError("expected HTTPError")


def test_rejected_token_backs_off_until_settings_change(app: Flask, monkeypatch) -> None:
    """After a 401, the same URL + token pair is not sent to HA again for a
    while (each try is an invalid-login entry in HA's log, #444). A new
    token gets through at once."""
    core = _core(app)
    core._reset_auth_cooldown()
    calls: list[str] = []

    def fake_urlopen(req, timeout=0, context=None):
        calls.append(req.headers.get("Authorization", ""))
        if req.headers.get("Authorization") == "Bearer t":
            raise _http_error(req.full_url, 401, _TrackedBody(b""))
        return _FakeResp(b"[]")

    try:
        with app.app_context():
            _configure(app)
            monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
            for _ in range(3):
                try:
                    core.request_json("/api/states")
                except urllib.error.HTTPError as err:
                    assert err.code == 401
                    assert "token" in core.coerce_error(err)
                else:
                    raise AssertionError("expected HTTPError")
            assert len(calls) == 1

            app.config["SETTINGS_STORE"].update_section(
                "plugins", {"ha_core": {"base_url": "http://ha", "token_secret": "fresh"}}
            )
            assert core.request_json("/api/states") == []
            assert len(calls) == 2
    finally:
        core._reset_auth_cooldown()
