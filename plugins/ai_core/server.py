"""ai_core, shared Anthropic Claude connection + context resolver.

No widget cell of its own; sibling ai_* widgets reach in via the plugin
registry and call ``call_llm`` and ``resolve_placeholders`` so prompts
across the ai family stay consistent and the API key only lives in one
place.

Config lives in Settings -> Plugins -> AI Core: an Anthropic API key
(get one at https://console.anthropic.com/) and a default model. Each
widget can override the model in its cell options.

Placeholder DSL: prompt templates reference live state like
``{weather.now.temp_c}``, ``{todo.count}``, ``{calendar.next.title}``,
``{ha.entity.<entity_id>.state}``, and ``{time.day_of_week}``. The
resolver reads from peer cores (weather_core, calendar_core, ha_core)
+ the running clock and substitutes values. Unknown placeholders fall
back to a literal ``unknown`` so a malformed template never crashes
the widget.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from flask import current_app

USER_AGENT = "tesserae/0.1 (+ai_core)"
ANTHROPIC_VERSION = "2023-06-01"
ENDPOINT = "https://api.anthropic.com/v1/messages"
HTTP_TIMEOUT_S = 30

# Whitelist of placeholder roots the resolver recognises. Anything
# outside this set returns "unknown" rather than reaching into the
# Flask app config. Keeps the template DSL inert: no env vars, no
# arbitrary attribute access on Python objects.
_KNOWN_ROOTS = frozenset({"weather", "todo", "calendar", "ha", "time"})

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z][\w.\-]*)\}")


# ---------------------------------------------------------------- settings


def _settings() -> dict[str, Any]:
    store = current_app.config["SETTINGS_STORE"]
    section = store.get_section("plugins") or {}
    return section.get("ai_core") or {}


def api_key() -> str:
    """The Anthropic API key. Stored as ``api_key_secret`` on disk per
    the settings_store secret convention."""
    s = _settings()
    return (s.get("api_key_secret") or s.get("api_key") or "").strip()


def default_model() -> str:
    return (_settings().get("model") or "claude-haiku-4-5").strip()


# ---------------------------------------------------------------- LLM call


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 300,
    system: str | None = None,
) -> dict[str, Any]:
    """Send a single-turn prompt to Anthropic Messages API.

    Returns ``{"text": "..."}`` on success, ``{"error": "..."}`` on
    any failure (missing key, network blip, non-200, malformed
    response). Never raises so widget ``fetch()`` callers can render
    a clean error state without try/except gymnastics.
    """
    key = api_key()
    if not key:
        return {
            "error": (
                "Anthropic API key not set. Add one at Settings -> Plugins "
                "-> AI Core."
            )
        }

    body: dict[str, Any] = {
        "model": (model or default_model()),
        "max_tokens": int(max_tokens),
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            detail = json.loads(err.read().decode("utf-8")).get("error", {}).get("message", "")
        except (json.JSONDecodeError, OSError):
            detail = ""
        return {"error": f"HTTP {err.code}: {detail or err.reason}"}
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        return {"error": f"{type(err).__name__}: {err}"}
    except json.JSONDecodeError as err:
        return {"error": f"Malformed response: {err}"}

    content = payload.get("content") or []
    text_parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "".join(text_parts).strip()
    if not text:
        return {"error": "Empty response from model"}
    return {"text": text, "model": payload.get("model") or body["model"]}


# ---------------------------------------------------------------- placeholders


def _peer(plugin_id: str) -> Any:
    """Look up another plugin's loaded server module via the registry.

    Returns ``None`` if the peer isn't installed; the caller falls back
    to ``unknown`` rather than crashing a brief because the user hasn't
    set up weather_core yet.
    """
    plugin = current_app.config["PLUGIN_REGISTRY"].get(plugin_id)
    return plugin.server_module if plugin is not None else None


def _resolve_one(
    path: str,
    *,
    time_now: datetime,
    weather_snapshot: dict[str, Any] | None = None,
    todo_snapshot: dict[str, Any] | None = None,
    calendar_snapshot: dict[str, Any] | None = None,
    ha_allow: set[str] | None = None,
) -> str:
    """Resolve a single dotted placeholder path to a string value.

    Unknown roots and unreachable subpaths return the literal string
    ``unknown`` so the rendered template still reads as a sentence.
    """
    parts = path.split(".")
    if not parts or parts[0] not in _KNOWN_ROOTS:
        return "unknown"

    root = parts[0]
    if root == "time":
        return _resolve_time(parts[1:], time_now)
    if root == "weather" and weather_snapshot is not None:
        return _walk(weather_snapshot, parts[1:])
    if root == "todo" and todo_snapshot is not None:
        return _walk(todo_snapshot, parts[1:])
    if root == "calendar" and calendar_snapshot is not None:
        return _walk(calendar_snapshot, parts[1:])
    if root == "ha":
        return _resolve_ha(parts[1:], ha_allow or set())
    return "unknown"


def _walk(snapshot: dict[str, Any], rest: list[str]) -> str:
    cur: Any = snapshot
    for key in rest:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list):
            try:
                cur = cur[int(key)]
            except (ValueError, IndexError):
                return "unknown"
        else:
            return "unknown"
        if cur is None:
            return "unknown"
    if isinstance(cur, dict | list):
        return "unknown"
    return str(cur)


def _resolve_time(rest: list[str], now: datetime) -> str:
    if not rest:
        return now.isoformat()
    key = rest[0]
    if key == "hour":
        return f"{now.hour:02d}"
    if key == "minute":
        return f"{now.minute:02d}"
    if key == "day_of_week":
        return now.strftime("%A")
    if key == "day":
        return now.strftime("%d")
    if key == "month":
        return now.strftime("%B")
    if key == "year":
        return str(now.year)
    if key == "am_pm":
        return "AM" if now.hour < 12 else "PM"
    if key == "is_morning":
        return "true" if 5 <= now.hour < 12 else "false"
    if key == "is_afternoon":
        return "true" if 12 <= now.hour < 17 else "false"
    if key == "is_evening":
        return "true" if 17 <= now.hour < 22 else "false"
    if key == "is_night":
        return "true" if (now.hour >= 22 or now.hour < 5) else "false"
    if key == "iso":
        return now.isoformat()
    return "unknown"


def _resolve_ha(rest: list[str], allow: set[str]) -> str:
    """``ha.entity.<entity_id>.state`` against the live HA core.

    ``entity_id`` must appear in the cell's explicit allow list, so a
    rogue template can't read arbitrary HA state. Empty allow list ->
    every ha placeholder resolves to ``unknown``.
    """
    if len(rest) < 3 or rest[0] != "entity":
        return "unknown"
    # Recombine entity_id (may contain dots up to one: domain.object_id)
    # The wire form is ``ha.entity.sensor.living_room_temp.state`` so
    # entity_id = "sensor.living_room_temp" and tail = "state".
    if len(rest) < 4:
        return "unknown"
    entity_id = f"{rest[1]}.{rest[2]}"
    tail = rest[3]
    if entity_id not in allow:
        return "unknown"
    core = _peer("ha_core")
    if core is None or not hasattr(core, "get_state"):
        return "unknown"
    try:
        state = core.get_state(entity_id)
    except Exception:
        return "unknown"
    if not isinstance(state, dict):
        return "unknown"
    if tail == "state":
        return str(state.get("state") or "unknown")
    if tail.startswith("attr_"):
        attrs = state.get("attributes") or {}
        return str(attrs.get(tail[len("attr_") :]) or "unknown")
    return "unknown"


def _tz_now() -> datetime:
    """Render time in the server's configured timezone (Settings ->
    Server -> Timezone), falling back to UTC. Mirrors how clock + weather
    widgets pick their wall clock."""
    store = current_app.config["SETTINGS_STORE"]
    tz_name = (store.get_section("server") or {}).get("timezone") or "UTC"
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(ZoneInfo("UTC"))


def resolve_placeholders(
    template: str,
    *,
    weather: dict[str, Any] | None = None,
    todo: dict[str, Any] | None = None,
    calendar: dict[str, Any] | None = None,
    ha_allow: list[str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Substitute every ``{path}`` placeholder in ``template``.

    Returns ``(resolved_string, debug_values)`` where ``debug_values``
    maps the original placeholder paths to their resolved values; the
    widget's debug panel renders this so users can see what got sent
    to the model.

    Snapshots (``weather`` / ``todo`` / ``calendar``) are passed in by
    the caller so the widget controls the freshness and shape of the
    data; ai_core stays a pure resolver and doesn't make decisions
    about cache or fetching.
    """
    now = _tz_now()
    allow_set = set(ha_allow or [])
    debug: dict[str, str] = {}

    def _sub(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _resolve_one(
            path,
            time_now=now,
            weather_snapshot=weather,
            todo_snapshot=todo,
            calendar_snapshot=calendar,
            ha_allow=allow_set,
        )
        debug[path] = value
        return value

    return PLACEHOLDER_RE.sub(_sub, template), debug


def available_placeholders() -> list[str]:
    """Enumerated examples the editor surfaces in widget help text."""
    return [
        "weather.now.condition",
        "weather.now.temp_c",
        "weather.now.feels_like_c",
        "weather.today.high_c",
        "weather.today.low_c",
        "weather.today.precip_mm",
        "todo.count",
        "todo.urgent_count",
        "todo.top",
        "calendar.next.title",
        "calendar.next.in_minutes",
        "calendar.events_today",
        "ha.entity.<entity_id>.state",
        "time.day_of_week",
        "time.hour",
        "time.am_pm",
        "time.is_morning",
        "time.is_afternoon",
        "time.is_evening",
    ]
