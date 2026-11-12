from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

PLUGIN_DIR = Path(__file__).resolve().parents[1]
SERVER_PATH = PLUGIN_DIR / "server.py"


def _load_server() -> Any:
    spec = importlib.util.spec_from_file_location("ha_heat_pump_server_under_test", SERVER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load_server()


def entity(entity_id: str, state: str, **attributes: object) -> dict[str, Any]:
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


class FakeCore:
    def __init__(
        self,
        states: list[dict[str, Any]] | None = None,
        history: dict[str, list[dict[str, Any]]] | None = None,
        *,
        states_error: Exception | None = None,
        history_error: Exception | None = None,
    ):
        self.states = states or []
        self.history_samples = history or {}
        self.states_error = states_error
        self.history_error = history_error
        self.history_calls: list[str] = []

    def get_states(self) -> list[dict[str, Any]]:
        if self.states_error:
            raise self.states_error
        return self.states

    def entity_choices(self, domains: tuple[str, ...]) -> list[dict[str, str]]:
        return [{"value": domains[0] + ".example", "label": domains[0]}]

    def friendly_name(self, state: dict[str, Any]) -> str:
        return str(state.get("attributes", {}).get("friendly_name") or state["entity_id"])

    def history(self, entity_id: str, *, hours: int) -> list[dict[str, Any]]:
        assert hours == 48
        self.history_calls.append(entity_id)
        if self.history_error:
            raise self.history_error
        return self.history_samples.get(entity_id, [])

    @staticmethod
    def coerce_error(error: Exception) -> str:
        return str(error)


def _use_core(core: FakeCore) -> SimpleNamespace:
    app = SimpleNamespace(
        config={"PLUGIN_REGISTRY": {"ha_core": SimpleNamespace(server_module=core)}}
    )
    return app


def test_fetch_maps_generic_entities_and_period_counters() -> None:
    core = FakeCore(
        [
            entity("binary_sensor.pump", "on", friendly_name="Compressor"),
            entity("sensor.flow", "35.5", unit_of_measurement="°C"),
            entity("sensor.tank", "48", unit_of_measurement="°C"),
            entity("sensor.power", "1.4", unit_of_measurement="kW"),
            entity("sensor.total", "18.7", unit_of_measurement="kWh", last_period="15.3"),
            entity("sensor.heating", "12.4", unit_of_measurement="kWh", last_period="14.1"),
            entity("sensor.water", "3.2", unit_of_measurement="kWh", last_period="2.1"),
            entity("climate.heating", "heat", current_temperature="34"),
            entity("water_heater.tank", "eco", current_temperature="47"),
        ]
    )
    zone = ZoneInfo("Europe/Berlin")
    app = _use_core(core)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is zone
            return cls(2026, 9, 28, 20, 5, tzinfo=tz)

    with (
        patch.object(server, "current_app", app),
        patch.object(server, "datetime", FixedDateTime),
        patch.object(server, "app_timezone", return_value=zone),
    ):
        result = server.fetch(
            {
                "compressor_entity": "binary_sensor.pump",
                "flow_entity": "sensor.flow",
                "hot_water_temperature_entity": "sensor.tank",
                "power_entity": "sensor.power",
                "heating_entity": "climate.heating",
                "hot_water_entity": "water_heater.tank",
                "daily_energy_entity": "sensor.total",
                "heating_energy_entity": "sensor.heating",
                "hot_water_energy_entity": "sensor.water",
            },
            {},
            ctx={},
        )

    assert result["time"] == "20:05"
    assert result["compressor"]["active"] is True
    assert result["compressor"]["name"] == "Compressor"
    assert result["heating_temperature"]["value"] == 35.5
    assert result["hot_water_temperature"]["value"] == 48
    assert result["heating_mode"]["mode"] == "heat"
    assert result["hot_water_mode"]["mode"] == "eco"
    assert result["power"] == {
        "name": "sensor.power",
        "value": 1.4,
        "unit": "kW",
        "available": True,
    }
    assert result["energy"]["daily_total_yesterday"]["value"] == 15.3
    assert result["energy"]["heating_yesterday"]["value"] == 14.1
    assert result["energy"]["hot_water_yesterday"]["value"] == 2.1
    assert core.history_calls == []


def test_unavailable_and_non_numeric_states_are_not_readings() -> None:
    core = FakeCore(
        [
            entity("binary_sensor.pump", "unavailable"),
            entity("sensor.unknown", "unknown", unit_of_measurement="°C"),
            entity("sensor.invalid", "not-a-number", unit_of_measurement="kWh"),
            entity("sensor.infinite", "NaN", unit_of_measurement="kW"),
            entity("climate.heating", "unavailable"),
        ]
    )
    with patch.object(server, "current_app", _use_core(core)):
        result = server.fetch(
            {
                "compressor_entity": "binary_sensor.pump",
                "flow_entity": "sensor.unknown",
                "power_entity": "sensor.infinite",
                "daily_energy_entity": "sensor.invalid",
                "heating_entity": "climate.heating",
                "show_yesterday": False,
            },
            {},
            ctx={},
        )

    assert result["compressor"]["active"] is None
    assert result["compressor"]["state"] is None
    assert result["heating_temperature"]["available"] is False
    assert result["heating_temperature"]["value"] is None
    assert result["power"]["available"] is False
    assert result["power"]["value"] is None
    assert result["energy"]["daily_total"]["available"] is False
    assert result["energy"]["daily_total"]["value"] is None
    assert result["heating_mode"]["mode"] is None
    assert core.history_calls == []


def test_missing_entities_return_unavailable_values() -> None:
    core = FakeCore()
    with patch.object(server, "current_app", _use_core(core)):
        result = server.fetch(
            {
                "compressor_entity": "binary_sensor.missing",
                "flow_entity": "sensor.missing",
                "daily_energy_entity": "sensor.missing",
            },
            {},
            ctx={},
        )

    assert result["compressor"]["active"] is None
    assert result["heating_temperature"]["available"] is False
    assert result["energy"]["daily_total"]["available"] is False
    assert result["energy"]["daily_total_yesterday"]["available"] is False
    assert core.history_calls == ["sensor.missing"]


def test_state_fetch_errors_are_reported() -> None:
    core = FakeCore(states_error=ConnectionError("Home Assistant is unreachable"))
    with patch.object(server, "current_app", _use_core(core)):
        result = server.fetch({}, {}, ctx={})

    assert result == {"error": "Home Assistant is unreachable"}


def test_history_errors_are_reported_without_losing_current_reading() -> None:
    core = FakeCore(
        [entity("sensor.energy", "8.5", unit_of_measurement="kWh")],
        history_error=TimeoutError("history timed out"),
    )
    with patch.object(server, "current_app", _use_core(core)):
        result = server.fetch({"daily_energy_entity": "sensor.energy"}, {}, ctx={})

    assert result["energy"]["daily_total"]["value"] == 8.5
    assert result["energy"]["daily_total_yesterday"]["value"] is None
    assert result["energy"]["daily_total_yesterday"]["error"] == "history timed out"
    assert core.history_calls == ["sensor.energy"]


def test_disabling_yesterday_skips_history_requests() -> None:
    core = FakeCore(
        [
            entity("sensor.total", "18.7", unit_of_measurement="kWh"),
            entity("sensor.heating", "12.4", unit_of_measurement="kWh"),
            entity("sensor.water", "3.2", unit_of_measurement="kWh"),
        ]
    )
    with patch.object(server, "current_app", _use_core(core)):
        result = server.fetch(
            {
                "daily_energy_entity": "sensor.total",
                "heating_energy_entity": "sensor.heating",
                "hot_water_energy_entity": "sensor.water",
                "show_yesterday": False,
            },
            {},
            ctx={},
        )

    assert result["energy"]["daily_total"]["value"] == 18.7
    assert result["energy"]["daily_total_yesterday"]["available"] is False
    assert result["energy"]["heating_yesterday"]["available"] is False
    assert result["energy"]["hot_water_yesterday"]["available"] is False
    assert core.history_calls == []


def test_history_yesterday_boundary_uses_application_timezone() -> None:
    zone = ZoneInfo("Europe/Berlin")
    core = FakeCore(
        [entity("sensor.energy", "8.5", unit_of_measurement="kWh")],
        history={
            "sensor.energy": [
                {"state": "5.2", "last_changed": "2026-09-27T21:30:00+00:00"},
                {"state": "99", "last_changed": "2026-09-27T22:30:00+00:00"},
            ]
        },
    )

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            assert tz is zone
            return cls(2026, 9, 28, 0, 45, tzinfo=tz)

    with (
        patch.object(server, "current_app", _use_core(core)),
        patch.object(server, "datetime", FixedDateTime),
        patch.object(server, "app_timezone", return_value=zone),
    ):
        result = server.fetch({"daily_energy_entity": "sensor.energy"}, {}, ctx={})

    assert result["time"] == "00:45"
    assert result["energy"]["daily_total_yesterday"]["value"] == 5.2
    assert core.history_calls == ["sensor.energy"]


def test_entity_choices_are_domain_scoped() -> None:
    core = FakeCore()
    with patch.object(server, "current_app", _use_core(core)):
        assert server.choices("heating")[0]["value"] == "climate.example"
        assert server.choices("hot_water")[0]["value"] == "water_heater.example"
        assert server.choices("binary_sensor")[0]["value"] == "binary_sensor.example"
        assert server.choices("unknown") == []


def test_manifest_and_locales_include_widget_defaults_and_common_modes() -> None:
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    english = json.loads((PLUGIN_DIR / "strings" / "en.json").read_text(encoding="utf-8"))
    german = json.loads((PLUGIN_DIR / "strings" / "de.json").read_text(encoding="utf-8"))
    options = {option["name"]: option for option in manifest["cell_options"]}

    assert manifest["supports"]["sizes"] == ["xs", "sm", "md", "lg"]
    for name in (
        "compressor_entity",
        "flow_entity",
        "hot_water_temperature_entity",
        "power_entity",
        "heating_entity",
        "hot_water_entity",
        "daily_energy_entity",
        "heating_energy_entity",
        "hot_water_energy_entity",
        "overall_spf_entity",
    ):
        assert options[name]["default"] == ""

    assert options["show_yesterday"]["default"] is True
    assert options["show_temperature_gauge"]["default"] is False
    assert options["show_overall_spf"]["default"] is True
    assert options["show_overall_spf"]["advanced"] is True
    for translations in (english, german):
        assert translations["not_available"]
        for mode in ("heat_pump", "performance", "electric", "high_demand"):
            assert translations[f"mode_{mode}"]
