"""ha_core smoke: config gating, entity_choices shaping, error coercion —
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
        assert "Plugins" in choices[0]["label"]


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
