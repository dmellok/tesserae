"""Open-Meteo service (kind: service).

A non-placeable data source that exposes the Open-Meteo API to a code element.
Open-Meteo needs no API key, which makes it the cleanest reference for the
``service`` plugin kind: the agent lists it via list_services(), probes it with
empty options to discover the scopes, then requests a scope with a variable
list. fetch() returns the raw Open-Meteo JSON for the chosen scope, so a code
element can read whatever fields it needs off ctx.data.<name>.

There is no render side (kind "service"); ``fetch`` is the whole plugin.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from app.plugin_http import fetch_json

# Endpoint + a sensible default variable set per scope. The agent can override
# ``variables`` to pull anything Open-Meteo offers for that endpoint.
_SCOPES: dict[str, dict[str, Any]] = {
    "current": {
        "url": "https://api.open-meteo.com/v1/forecast",
        "param": "current",
        "default": "temperature_2m,relative_humidity_2m,apparent_temperature,"
        "is_day,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        "desc": "Current conditions at the coordinate.",
    },
    "hourly": {
        "url": "https://api.open-meteo.com/v1/forecast",
        "param": "hourly",
        "default": "temperature_2m,precipitation_probability,weather_code,wind_speed_10m",
        "desc": "Hourly forecast series (honours forecast_days).",
    },
    "daily": {
        "url": "https://api.open-meteo.com/v1/forecast",
        "param": "daily",
        "default": "weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_sum,sunrise,sunset,uv_index_max",
        "desc": "Daily forecast series (honours forecast_days).",
    },
    "air_quality": {
        "url": "https://air-quality-api.open-meteo.com/v1/air-quality",
        "param": "hourly",
        "default": "pm10,pm2_5,carbon_monoxide,ozone,uv_index,european_aqi",
        "desc": "Air quality (hourly).",
    },
    "marine": {
        "url": "https://marine-api.open-meteo.com/v1/marine",
        "param": "hourly",
        "default": "wave_height,wave_direction,wave_period,sea_surface_temperature",
        "desc": "Marine / wave conditions (hourly).",
    },
}


def _discovery() -> dict[str, Any]:
    """Self-describing map returned when no scope is set, so the agent can
    explore the API before choosing what to fetch."""
    return {
        "service": "open-meteo",
        "auth": "none",
        "scopes": {
            name: {"description": spec["desc"], "default_variables": spec["default"].split(",")}
            for name, spec in _SCOPES.items()
        },
        "usage": "Set options.scope to one of the scopes, options.latitude/longitude "
        "(or leave 0 to use the app home location), and optionally options.variables "
        "(comma-separated) to override the defaults.",
    }


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    scope = str(options.get("scope") or "").strip()
    if not scope:
        return _discovery()
    spec = _SCOPES.get(scope)
    if spec is None:
        return {"error": f"unknown scope {scope!r}; valid: {', '.join(_SCOPES)}"}

    try:
        lat = float(options.get("latitude") or 0) or float(ctx.get("home_lat") or 0)
        lon = float(options.get("longitude") or 0) or float(ctx.get("home_lon") or 0)
    except (TypeError, ValueError):
        lat = lon = 0.0
    if not lat and not lon:
        return {"error": "no latitude/longitude (set them, or configure the app home location)"}

    variables = str(options.get("variables") or "").strip() or spec["default"]
    params: dict[str, Any] = {
        "latitude": lat,
        "longitude": lon,
        spec["param"]: variables,
        "timezone": str(options.get("timezone") or "auto"),
    }
    if scope in ("hourly", "daily"):
        try:
            params["forecast_days"] = max(1, min(16, int(options.get("forecast_days") or 3)))
        except (TypeError, ValueError):
            params["forecast_days"] = 3
    url = f"{spec['url']}?{urlencode(params)}"
    try:
        return dict(fetch_json(url, timeout=8.0, retries=1))
    except Exception as err:
        return {"error": f"{type(err).__name__}: {err}", "scope": scope}
