"""ai_brief, LLM-written 1-3 sentence dashboard summary.

Builds a context snapshot (current weather via Open-Meteo, today's
calendar via calendar_core, time via the server clock, allow-listed
Home Assistant entities via ha_core), substitutes those into the
user's prompt template, sends to Anthropic Claude via ai_core, and
caches the response for ``refresh_minutes``.

The widget is intentionally lazy: if a placeholder's data source isn't
configured (no lat/lon, no calendar feeds, no allow-listed entities),
the placeholder resolves to ``unknown`` and the brief still renders.
That way a brand-new install with just an API key produces something
sensible.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import current_app

WEATHER_CACHE_TTL_S = 600
HTTP_TIMEOUT_S = 15
USER_AGENT = "tesserae/0.1 (+ai_brief)"


def _http_json(url: str) -> dict[str, Any] | None:
    """Bounded GET that returns parsed JSON or None on any failure.

    Inlined rather than imported from Tesserae's app.plugin_http so the
    widget tarball stays self-contained at the import layer (matches
    the convention every other community widget follows).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- helpers


def _peer(plugin_id: str) -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get(plugin_id)
    return plugin.server_module if plugin is not None else None


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_entity_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not raw:
        return []
    return [tok.strip() for tok in str(raw).replace("\n", ",").split(",") if tok.strip()]


# ---------------------------------------------------------------- weather


_WEATHER_CODES = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "violent rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


def _fetch_weather(
    lat: float, lon: float, units: str, data_dir: Path
) -> dict[str, Any] | None:
    """Return a small weather snapshot or None if lat/lon unset."""
    if lat == 0.0 and lon == 0.0:
        return None
    cache_path = data_dir / f"wx_{lat:.3f}_{lon:.3f}_{units}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < WEATHER_CACHE_TTL_S:
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    temp_unit = "fahrenheit" if units == "imperial" else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,weather_code,apparent_temperature,relative_humidity_2m"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        f"&temperature_unit={temp_unit}"
        "&forecast_days=1&timezone=auto"
    )
    payload = _http_json(url)
    if not isinstance(payload, dict):
        return None

    current = payload.get("current") or {}
    daily = payload.get("daily") or {}

    def _first(arr: object) -> Any:
        if isinstance(arr, list) and arr:
            return arr[0]
        return None

    code = current.get("weather_code")
    snapshot = {
        "now": {
            "condition": _WEATHER_CODES.get(int(code) if isinstance(code, int | float) else -1, "unknown"),
            "temp_c": _round(current.get("temperature_2m")),
            "feels_like_c": _round(current.get("apparent_temperature")),
            "humidity_pct": _round(current.get("relative_humidity_2m")),
        },
        "today": {
            "high_c": _round(_first(daily.get("temperature_2m_max"))),
            "low_c": _round(_first(daily.get("temperature_2m_min"))),
            "precip_pct": _round(_first(daily.get("precipitation_probability_max"))),
        },
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(snapshot), encoding="utf-8")
    return snapshot


def _round(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{round(float(value))}"
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- calendar


def _fetch_calendar() -> dict[str, Any] | None:
    """Build {events_today, next: {title, in_minutes}} via calendar_core."""
    core = _peer("calendar_core")
    if core is None or not hasattr(core, "load_events"):
        return None
    now = datetime.now(UTC)
    window_end = now + timedelta(hours=24)
    try:
        events = core.load_events(now, window_end)
    except Exception:
        return None
    if not isinstance(events, list):
        return None

    today_count = 0
    next_event: dict[str, Any] | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        start_raw = ev.get("start") or ev.get("start_iso")
        if not start_raw:
            continue
        try:
            start_dt = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=UTC)
        if start_dt.date() == now.date():
            today_count += 1
        if start_dt > now and next_event is None:
            mins = max(0, int((start_dt - now).total_seconds() // 60))
            next_event = {"title": str(ev.get("title") or "untitled"), "in_minutes": str(mins)}
    return {
        "events_today": str(today_count),
        "next": next_event or {"title": "nothing scheduled", "in_minutes": "0"},
    }


# ---------------------------------------------------------------- fetch


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings
    template = str(options.get("prompt_template") or "").strip()
    if not template:
        return {"error": "Prompt template is empty."}

    ai_core = _peer("ai_core")
    if ai_core is None:
        return {"error": "AI Core plugin not installed."}

    # Server-level home location (Settings → Server → Location) is the
    # fallback when the cell's lat/lon fields are empty. Saves the user
    # from re-typing the same coordinates on every weather-aware widget.
    # Tesserae >= 0.46.7 injects ctx["home_lat"] / ctx["home_lon"];
    # older hosts return 0.0 from the default and the widget renders
    # a "configure location" state.
    lat = float(options.get("latitude") or ctx.get("home_lat") or 0.0)
    lon = float(options.get("longitude") or ctx.get("home_lon") or 0.0)
    units = str(options.get("units") or "metric")
    include_calendar = options.get("include_calendar", True) is not False
    ha_allow = _parse_entity_list(options.get("ha_entities"))
    refresh_minutes = max(5, int(options.get("refresh_minutes") or 60))
    max_tokens = max(50, int(options.get("max_tokens") or 200))
    model_override = (str(options.get("model_override") or "").strip()) or None
    header_label = (str(options.get("header_label") or "BRIEF").strip()) or "BRIEF"

    data_dir = Path(ctx["data_dir"])
    data_dir.mkdir(parents=True, exist_ok=True)

    weather_snapshot = _fetch_weather(lat, lon, units, data_dir)
    calendar_snapshot = _fetch_calendar() if include_calendar else None

    resolved_prompt, debug_values = ai_core.resolve_placeholders(
        template,
        weather=weather_snapshot,
        calendar=calendar_snapshot,
        ha_allow=ha_allow,
    )

    cache_key = hashlib.sha256(
        f"{resolved_prompt}|{model_override or ''}|{max_tokens}".encode()
    ).hexdigest()[:16]
    cache_path = data_dir / f"brief_{cache_key}.json"
    refresh_seconds = refresh_minutes * 60
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < refresh_seconds:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            cached["from_cache"] = True
            return cached
        except (json.JSONDecodeError, OSError):
            pass

    result = ai_core.call_llm(
        resolved_prompt,
        model=model_override,
        max_tokens=max_tokens,
    )
    if result.get("error"):
        return {
            "error": result["error"],
            "header_label": header_label,
            "resolved_prompt": resolved_prompt,
            "debug_values": debug_values,
        }

    response = {
        "brief": result.get("text", ""),
        "model": result.get("model", ""),
        "header_label": header_label,
        "generated_at": _utcnow_iso(),
        "resolved_prompt": resolved_prompt,
        "debug_values": debug_values,
        "from_cache": False,
    }
    with contextlib.suppress(OSError):
        cache_path.write_text(json.dumps(response), encoding="utf-8")
    return response
