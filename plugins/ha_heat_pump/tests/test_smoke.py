from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

import server  # noqa: E402


def entity(entity_id: str, state: str, **attributes: object) -> dict:
    return {"entity_id": entity_id, "state": state, "attributes": attributes}


class FakeCore:
    def __init__(self, states: list[dict], history: dict | None = None):
        self.states = states
        self.history_samples = history or {}
        self.history_calls: list[str] = []

    def get_states(self) -> list[dict]:
        return self.states

    def entity_choices(self, domains: tuple[str, ...]) -> list[dict[str, str]]:
        return [{"value": domains[0] + ".example", "label": domains[0]}]

    def friendly_name(self, state: dict) -> str:
        return str(state.get("attributes", {}).get("friendly_name") or state["entity_id"])

    def history(self, entity_id: str, *, hours: int) -> list[dict]:
        assert hours == 48
        self.history_calls.append(entity_id)
        return self.history_samples.get(entity_id, [])

    @staticmethod
    def coerce_error(error: Exception) -> str:
        return str(error)


class HeatPumpSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        yesterday = datetime.now().astimezone().date() - timedelta(days=1)
        core = FakeCore(
            [
                entity("binary_sensor.compressor", "on", friendly_name="Compressor"),
                entity("sensor.flow", "35.5", unit_of_measurement="°C"),
                entity("sensor.tank", "48", unit_of_measurement="°C"),
                entity("sensor.power", "1.4", unit_of_measurement="kW"),
                entity("sensor.total", "18.7", unit_of_measurement="kWh", last_period="15.3"),
                entity("sensor.heating", "12.4", unit_of_measurement="kWh", last_period="14.1"),
                entity("sensor.hot_water", "3.2", unit_of_measurement="kWh"),
                entity("climate.heat", "heat", current_temperature="34", temperature="40"),
                entity("water_heater.tank", "eco", current_temperature="47"),
            ],
            history={
                "sensor.hot_water": [
                    {
                        "state": "2.1",
                        "last_changed": datetime.combine(yesterday, datetime.max.time())
                        .astimezone()
                        .isoformat(),
                    }
                ]
            },
        )
        self.app = SimpleNamespace(
            config={"PLUGIN_REGISTRY": {"ha_core": SimpleNamespace(server_module=core)}}
        )
        self.patch = patch.object(server, "current_app", self.app)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.core = core

    def test_fetch_maps_status_temperatures_power_and_period_counters(self) -> None:
        result = server.fetch(
            {
                "compressor_entity": "binary_sensor.compressor",
                "flow_entity": "sensor.flow",
                "hot_water_temperature_entity": "sensor.tank",
                "power_entity": "sensor.power",
                "daily_energy_entity": "sensor.total",
                "heating_energy_entity": "sensor.heating",
                "hot_water_energy_entity": "sensor.hot_water",
            },
            {},
            ctx={},
        )

        self.assertTrue(result["compressor"]["active"])
        self.assertEqual(result["heating_temperature"]["value"], 35.5)
        self.assertEqual(result["hot_water_temperature"]["value"], 48)
        self.assertEqual(result["power"]["unit"], "kW")
        self.assertEqual(result["energy"]["daily_total_yesterday"]["value"], 15.3)
        self.assertEqual(result["energy"]["heating_yesterday"]["value"], 14.1)
        self.assertEqual(result["energy"]["hot_water_yesterday"]["value"], 2.1)

    def test_temperature_fallbacks_and_missing_values(self) -> None:
        result = server.fetch(
            {"heating_entity": "climate.heat", "hot_water_entity": "water_heater.tank"},
            {},
            ctx={},
        )
        self.assertEqual(result["heating_temperature"]["value"], 34)
        self.assertEqual(result["hot_water_temperature"]["value"], 47)
        self.assertIsNone(result["power"]["value"])
        self.assertIsNone(result["compressor"]["active"])

    def test_configured_vitocal_entities_are_mapped(self) -> None:
        self.core.states.extend(
            [
                entity("climate.e3_vitocal_17_heizung", "auto"),
                entity("water_heater.e3_vitocal_17_warmwasser", "on"),
                entity("sensor.e3_vitocal_17_vorlauftemperatur", "39.8", unit_of_measurement="°C"),
                entity(
                    "sensor.e3_vitocal_17_ww_speichertemperatur", "50", unit_of_measurement="°C"
                ),
                entity("sensor.e3_vitocal_17_saisonale_arbeitszahl", "4.3"),
                entity(
                    "sensor.keller_e3_vitocal_17_strom_taglich",
                    "20.1",
                    unit_of_measurement="kWh",
                    last_period="21.1",
                ),
                entity(
                    "sensor.keller_e3_vitocal_17_vitocal_heizung_taglich",
                    "17.6",
                    unit_of_measurement="kWh",
                    last_period="18.1",
                ),
                entity(
                    "sensor.keller_e3_vitocal_17_vitocal_warmwasser_taglich",
                    "2.5",
                    unit_of_measurement="kWh",
                    last_period="3.0",
                ),
                entity("binary_sensor.e3_vitocal_17_kompressor", "off"),
            ]
        )
        result = server.fetch(
            {
                "heating_entity": "climate.e3_vitocal_17_heizung",
                "hot_water_entity": "water_heater.e3_vitocal_17_warmwasser",
                "compressor_entity": "binary_sensor.e3_vitocal_17_kompressor",
                "flow_entity": "sensor.e3_vitocal_17_vorlauftemperatur",
                "hot_water_temperature_entity": "sensor.e3_vitocal_17_ww_speichertemperatur",
                "daily_energy_entity": "sensor.keller_e3_vitocal_17_strom_taglich",
                "heating_energy_entity": "sensor.keller_e3_vitocal_17_vitocal_heizung_taglich",
                "hot_water_energy_entity": "sensor.keller_e3_vitocal_17_vitocal_warmwasser_taglich",
                "overall_spf_entity": "sensor.e3_vitocal_17_saisonale_arbeitszahl",
            },
            {},
            ctx={},
        )

        self.assertFalse(result["compressor"]["active"])
        self.assertEqual(result["heating_mode"]["mode"], "auto")
        self.assertEqual(result["hot_water_mode"]["mode"], "on")
        self.assertEqual(result["heating_temperature"]["value"], 39.8)
        self.assertEqual(result["hot_water_temperature"]["value"], 50)
        self.assertEqual(result["overall_spf"]["value"], 4.3)
        self.assertEqual(result["energy"]["daily_total_yesterday"]["value"], 21.1)
        self.assertEqual(result["energy"]["heating_yesterday"]["value"], 18.1)
        self.assertEqual(result["energy"]["hot_water_yesterday"]["value"], 3)

    def test_history_is_used_only_when_last_period_is_missing(self) -> None:
        result = server.fetch({"daily_energy_entity": "sensor.total"}, {}, ctx={})
        self.assertEqual(result["energy"]["daily_total_yesterday"]["value"], 15.3)
        self.assertNotIn("sensor.total", self.core.history_calls)

    def test_entity_choices_are_domain_scoped(self) -> None:
        self.assertEqual(server.choices("heating")[0]["value"], "climate.example")
        self.assertEqual(server.choices("hot_water")[0]["value"], "water_heater.example")
        self.assertEqual(server.choices("binary_sensor")[0]["value"], "binary_sensor.example")
        self.assertEqual(server.choices("unknown"), [])

    def test_manifest_and_renderer_declare_requested_sizes_and_archetype(self) -> None:
        manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
        client = (PLUGIN_DIR / "client.js").read_text(encoding="utf-8")
        self.assertEqual(manifest["name"], "HA Heat Pump")
        self.assertEqual(manifest["supports"]["sizes"], ["xs", "sm", "md", "lg"])
        options = {option["name"]: option["default"] for option in manifest["cell_options"]}
        self.assertEqual(options["flow_entity"], "sensor.e3_vitocal_17_vorlauftemperatur")
        self.assertNotIn("return_entity", options)
        self.assertEqual(options["heating_entity"], "climate.e3_vitocal_17_heizung")
        self.assertEqual(options["hot_water_entity"], "water_heater.e3_vitocal_17_warmwasser")
        self.assertEqual(
            options["overall_spf_entity"], "sensor.e3_vitocal_17_saisonale_arbeitszahl"
        )
        self.assertIn("ctx?.cell?.size", client)
        self.assertIn('class="w-body status-body hp-body"', client)
        self.assertIn("status-hero hp-hero", client)
        self.assertIn(".hp-md .hp-hero-spf, .hp-lg .hp-hero-spf { display: block; }", client)
        self.assertIn(
            ".hp-hero-spf-value { color: var(--text-primary); font-size: var(--fs-jumbo);", client
        )
        self.assertIn(".hp-md .hp-daily-total { display: none; }", client)
        self.assertIn('class="hp-hero-spf"', client)
        self.assertIn(".hp-md .hp-reading-mode { display: none; }", client)
        self.assertIn(".hp-md .hp-state { font-size: var(--fs-lead); }", client)
        self.assertIn(".hp-md .hp-hero-spf-value { font-size: var(--fs-lead); }", client)
        self.assertIn(".hp-md .hp-hero { min-height:", client)
        self.assertIn(".hp-hero-lockup { min-width: max-content; flex: 0 0 auto; }", client)
        self.assertIn(".hp-state { overflow: visible;", client)
        self.assertIn(".hp-reading-label, .hp-energy-label { color: var(--text-muted);", client)
        self.assertIn('class="hp-reading-mode"', client)
        self.assertIn("aria-label=", client)
        self.assertIn(".hp-md .hp-yesterday { display: none; }", client)
        self.assertIn('modeIndicator(heatingMode.mode, t("heating_mode"', client)
        self.assertIn('modeIndicator(hotWaterMode.mode, t("hot_water_mode"', client)
        self.assertIn("status-grid hp-readings", client)
        self.assertIn(".hp-lg .hp-current-power { display: flex; }", client)
        self.assertIn(".hp-lg .hp-energy-details", client)
        self.assertIn(
            ".hp-md .hp-reading-top { align-items: flex-start; flex-direction: column; }", client
        )
        self.assertIn('class="hp-reading-caption"', client)
        self.assertIn(".hp-md .hp-energy-details,", client)
        self.assertNotIn("client.css", client)
        self.assertNotIn("border:", client)
        self.assertNotRegex(client, r"#[0-9a-fA-F]{3,8}\b")


if __name__ == "__main__":
    unittest.main()
