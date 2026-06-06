"""ha_energy, solar / grid / battery / house-consumption snapshot.

Reads the same ``sensor.*`` entities you've already configured for HA's
Energy panel and produces the structured shape four selectable variants
paint:

    {
      "place": str, "time": str,
      "solar_w": float,           # current solar production, W
      "grid_w": float,            # current grid import (+) or export (-)
      "battery_w": float,         # battery charge (+) or discharge (-)
      "house_w": float,           # house consumption
      "battery_soc": float|None,  # 0-100 %, if entity provided
      "solar_today_kwh": float|None,
      "flow": "solar" | "grid" | "battery" | "mixed",
      "sparkline": [float, ...]   # last 24h solar (or house if no solar)
    }

The widget batches a single ``get_states`` call to cover all four power
entities; the SoC + today's-energy entities are optional and only
queried when the user filled them in.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from flask import current_app


def _core() -> Any:
    return current_app.config["PLUGIN_REGISTRY"].get("ha_core").server_module


def choices(name: str) -> list[dict[str, str]]:
    """Entity picker for the six per-role selects. All energy/power
    sensors live under ``sensor.*`` in HA, so filter to that domain so
    the user isn't scrolling past every light and lock to find their
    inverter sensor."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("sensor",))
    return []


# -- helpers -----------------------------------------------------------


def _f(value: Any) -> float:
    """Coerce a state string to float. HA returns ``"unavailable"`` /
    ``"unknown"`` for offline entities; treat those as 0 so the widget
    keeps drawing instead of erroring."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _f_or_none(value: Any) -> float | None:
    """Same as ``_f`` but returns ``None`` for unparseable inputs, used
    for optional fields where ``0`` would be misleading (battery SoC)."""
    if value in (None, "", "unavailable", "unknown"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _state(states: list[dict[str, Any]], entity_id: str) -> dict[str, Any] | None:
    """Linear scan over a get_states() result. O(N) but the list is
    small (HA's full state dump for a typical install is a few hundred
    entries), and we do it once per render."""
    for st in states:
        if st.get("entity_id") == entity_id:
            return st
    return None


def _state_w(states: list[dict[str, Any]], entity_id: str) -> float:
    """Read the entity's ``state`` as a float interpreted as watts. If
    the entity's ``unit_of_measurement`` is kW, scale up by 1000 so the
    widget compares apples to apples regardless of which units the user
    has their power sensors reporting in."""
    if not entity_id:
        return 0.0
    st = _state(states, entity_id)
    if st is None:
        return 0.0
    value = _f(st.get("state"))
    unit = str((st.get("attributes") or {}).get("unit_of_measurement", "")).lower()
    if unit == "kw":
        value *= 1000
    return value


def _dominant_flow(solar: float, grid: float, battery: float, house: float) -> str:
    """Which contribution is currently the largest source of house
    power? Used by some variants to highlight a single primary accent."""
    contributions = {
        "solar": max(0.0, solar),
        "grid": max(0.0, grid),
        # Battery discharge is positive contribution; charging is negative.
        "battery": max(0.0, -battery) if battery < 0 else 0.0,
    }
    if not any(contributions.values()):
        return "mixed"
    return max(contributions, key=lambda k: contributions[k])


# -- entry point -------------------------------------------------------


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    try:
        states = core.get_states()
    except Exception as err:
        return {"error": core.coerce_error(err)}

    solar = _state_w(states, options.get("solar_entity") or "")
    grid = _state_w(states, options.get("grid_entity") or "")
    battery = _state_w(states, options.get("battery_entity") or "")
    house = _state_w(states, options.get("house_entity") or "")

    soc_entity = (options.get("battery_soc_entity") or "").strip()
    soc = None
    if soc_entity:
        soc_state = _state(states, soc_entity)
        if soc_state is not None:
            soc = _f_or_none(soc_state.get("state"))

    today_entity = (options.get("solar_today_entity") or "").strip()
    solar_today_kwh = None
    if today_entity:
        today_state = _state(states, today_entity)
        if today_state is not None:
            solar_today_kwh = _f_or_none(today_state.get("state"))

    # 48-hour sparkline split into yesterday + today so the client
    # can paint a comparison ghost line. Prefer the solar entity (it
    # has a clear day-shaped curve); fall back to house consumption
    # when solar's not configured.
    spark_entity = (options.get("solar_entity") or options.get("house_entity") or "").strip()
    sparkline_today: list[float] = []
    sparkline_yesterday: list[float] = []
    if spark_entity:
        try:
            hist_24 = core.history(spark_entity, hours=24)
        except Exception:
            hist_24 = []
        try:
            hist_48 = core.history(spark_entity, hours=48)
        except Exception:
            hist_48 = []
        # Today's 24h: the last 24h of samples. Yesterday's 24h: the
        # 24-48h window. We bin BEFORE splitting so both halves get
        # the same 48-slot density even if the underlying sample
        # rate varies.
        today_raw = [_f(s.get("state")) for s in hist_24]
        sparkline_today = _downsample(today_raw, slots=48)
        if hist_48:
            full_48 = [_f(s.get("state")) for s in hist_48]
            binned = _downsample(full_48, slots=96)
            sparkline_yesterday = binned[:48]
            # If today's downsample produced fewer than 48 samples
            # (sparse history), pull today's half out of the 48h bin too
            # so both lines line up under the same x-axis.
            if not sparkline_today:
                sparkline_today = binned[48:]

    flow = _dominant_flow(solar, grid, battery, house)

    return {
        "label": options.get("label", "Home"),
        "place": options.get("label", "Home"),
        "time": datetime.now().strftime("%H:%M"),
        "hour": datetime.now().hour,
        "solar_w": round(solar, 1),
        "grid_w": round(grid, 1),
        "battery_w": round(battery, 1),
        "house_w": round(house, 1),
        "battery_soc": round(soc, 1) if soc is not None else None,
        "solar_today_kwh": round(solar_today_kwh, 2) if solar_today_kwh is not None else None,
        "flow": flow,
        # Backwards-compat: `sparkline` mirrors today's series.
        "sparkline": sparkline_today,
        "sparkline_today": sparkline_today,
        "sparkline_yesterday": sparkline_yesterday,
        # Bookkeeping for the cache layer.
        "_fetched_at": int(time.time()),
    }


def _downsample(values: list[float], *, slots: int) -> list[float]:
    """Bin a variable-length history into ``slots`` equal-time slots.

    HA's history endpoint returns one sample per state change, so a noisy
    sensor over 24h might return 2000+ entries. The widget chart only
    needs ~48 points (one every 30 minutes), so we bin + average."""
    if not values or slots < 1:
        return []
    n = len(values)
    if n <= slots:
        return [round(v, 1) for v in values]
    out: list[float] = []
    step = n / slots
    for i in range(slots):
        lo = int(i * step)
        hi = int((i + 1) * step) or lo + 1
        bucket = values[lo:hi] or [0.0]
        out.append(round(sum(bucket) / len(bucket), 1))
    return out
