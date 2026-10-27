"""Heat-pump snapshot from configured Home Assistant entities."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Return entity choices scoped to the option's Home Assistant domain."""
    domains = {
        "heating": ("climate",),
        "hot_water": ("water_heater",),
        "binary_sensor": ("binary_sensor",),
        "sensor": ("sensor",),
    }
    core = _core()
    if core is not None and name in domains:
        return core.entity_choices(domains=domains[name])
    return []


def _numeric(value: Any) -> float | None:
    if value in (None, "", "unknown", "unavailable"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _lookup(states: list[dict[str, Any]], entity_id: Any) -> dict[str, Any] | None:
    if not isinstance(entity_id, str) or not entity_id:
        return None
    return next((state for state in states if state.get("entity_id") == entity_id), None)


def _available_state(state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    value = str(state.get("state") or "").strip()
    return value if value.lower() not in ("", "unknown", "unavailable") else None


def _entity_data(states: list[dict[str, Any]], entity_id: Any, core: Any) -> dict[str, Any]:
    state = _lookup(states, entity_id)
    raw_value = _available_state(state)
    attrs = (state.get("attributes") or {}) if state else {}
    return {
        "name": core.friendly_name(state) if state else str(entity_id or ""),
        "value": _numeric(raw_value),
        "unit": str(attrs.get("unit_of_measurement") or ""),
        "available": raw_value is not None,
    }


def _yesterday_data(states: list[dict[str, Any]], entity_id: Any, core: Any) -> dict[str, Any]:
    current = _entity_data(states, entity_id, core)
    result = {**current, "value": None, "available": False, "error": None}
    if not isinstance(entity_id, str) or not entity_id:
        return result

    state = _lookup(states, entity_id)
    attrs = (state.get("attributes") or {}) if state else {}
    previous_period = _numeric(attrs.get("last_period"))
    if previous_period is not None:
        result.update(value=previous_period, available=True)
        return result

    try:
        history = core.history(entity_id, hours=48)
    except Exception as err:
        result["error"] = core.coerce_error(err)
        return result

    now = datetime.now().astimezone()
    yesterday = now.date() - timedelta(days=1)
    previous_day: list[tuple[datetime, float]] = []
    for sample in history:
        value = _numeric(sample.get("state"))
        timestamp = sample.get("last_changed") or sample.get("last_updated")
        if value is None or not isinstance(timestamp, str):
            continue
        try:
            changed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if changed_at.tzinfo is None:
            changed_at = changed_at.astimezone()
        local_time = changed_at.astimezone(now.tzinfo)
        if local_time.date() == yesterday:
            previous_day.append((local_time, value))

    if previous_day:
        result.update(value=max(previous_day, key=lambda item: item[0])[1], available=True)
    return result


def _temperature(
    states: list[dict[str, Any]],
    sensor_entity: Any,
    fallback_entity: Any,
    core: Any,
    fallback_attributes: tuple[str, ...],
) -> dict[str, Any]:
    reading = _entity_data(states, sensor_entity, core)
    if reading["available"]:
        return reading

    state = _lookup(states, fallback_entity)
    attrs = (state.get("attributes") or {}) if state else {}
    value = None
    for name in fallback_attributes:
        value = _numeric(attrs.get(name))
        if value is not None:
            break
    if value is None:
        return reading
    return {
        "name": core.friendly_name(state) if state else reading["name"],
        "value": value,
        "unit": str(attrs.get("temperature_unit") or "°C"),
        "available": True,
    }


def _mode_data(states: list[dict[str, Any]], entity_id: Any, core: Any) -> dict[str, Any]:
    state = _lookup(states, entity_id)
    return {
        "name": core.friendly_name(state) if state else str(entity_id or ""),
        "mode": _available_state(state),
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    compressor_state = _lookup(states, options.get("compressor_entity"))
    compressor_status = _available_state(compressor_state)
    normalized_status = (compressor_status or "").lower()
    if normalized_status in {"on", "true", "running", "active", "1"}:
        compressor_active = True
    elif normalized_status in {"off", "false", "idle", "inactive", "0"}:
        compressor_active = False
    else:
        compressor_active = None

    energy = {}
    for key, option in (
        ("daily_total", "daily_energy_entity"),
        ("heating", "heating_energy_entity"),
        ("hot_water", "hot_water_energy_entity"),
    ):
        entity_id = options.get(option)
        energy[key] = _entity_data(states, entity_id, core)
        energy[f"{key}_yesterday"] = _yesterday_data(states, entity_id, core)

    return {
        "place": str(options.get("label") or "Heat pump"),
        "time": datetime.now().strftime("%H:%M"),
        "compressor": {
            "active": compressor_active,
            "state": compressor_status,
            "name": (
                core.friendly_name(compressor_state)
                if compressor_state
                else str(options.get("compressor_entity") or "")
            ),
        },
        "heating_temperature": _temperature(
            states,
            options.get("flow_entity"),
            options.get("heating_entity"),
            core,
            ("current_temperature",),
        ),
        "hot_water_temperature": _temperature(
            states,
            options.get("hot_water_temperature_entity"),
            options.get("hot_water_entity"),
            core,
            ("current_temperature",),
        ),
        "power": _entity_data(states, options.get("power_entity"), core),
        "heating_mode": _mode_data(states, options.get("heating_entity"), core),
        "hot_water_mode": _mode_data(states, options.get("hot_water_entity"), core),
        "overall_spf": _entity_data(states, options.get("overall_spf_entity"), core),
        "energy": energy,
        "_fetched_at": int(time.time()),
    }
