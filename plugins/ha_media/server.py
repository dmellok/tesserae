"""ha_media — now-playing tile for a Home Assistant media_player entity.

Thin widget over ha_core. Reads a single ``media_player.*`` entity and
shapes its state + attributes for the four visual directions in
client.js:

    {
      "name": str,                 # friendly name
      "state": "playing" | "paused" | "idle" | "off" | "unavailable",
      "title": str,                # media_title
      "artist": str,               # media_artist
      "album": str,                # media_album_name
      "art_url": str | None,       # resolved absolute URL to album art
      "source": str,               # selected source / app name
      "volume_pct": float | None,  # 0-100, from volume_level
      "media_position": float | None,
      "media_duration": float | None,
      "position_pct": float | None,  # 0-100 if both pos + dur are present
    }

HA stores album art at ``attributes.entity_picture`` as a relative path
(e.g. ``/api/media_player_proxy/media_player.living_room?token=...``).
We resolve it to an absolute URL against the configured base_url so the
admin preview iframe (and the headless renderer) can fetch it directly.
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("ha_core")
    return plugin.server_module if plugin is not None else None


def choices(name: str) -> list[dict[str, str]]:
    """Entity dropdown for the editor, filtered to media_player entities."""
    core = _core()
    if name == "entity" and core is not None:
        return core.entity_choices(domains=("media_player",))
    return []


def _f_or_none(value: Any) -> float | None:
    """Float-or-None coercion. HA serves "unavailable"/"unknown" for
    offline entities; treat them as None so the client can pick a rest
    state instead of rendering "0:00 / 0:00"."""
    if value in (None, "", "unavailable", "unknown"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str(value: Any) -> str:
    """Stringify, but blank out unavailable/unknown placeholders so the
    UI doesn't render literal "unavailable" in track titles."""
    if value in (None, "unavailable", "unknown"):
        return ""
    return str(value)


def _resolve_art(core: Any, raw: str) -> str | None:
    """Make ``entity_picture`` consumable by the browser.

    HA returns the picture as a site-relative path (with auth token
    baked in as a query param), so we just prepend our configured
    base_url. Absolute URLs (some integrations return Spotify CDN
    links etc.) pass through unchanged."""
    if not raw:
        return None
    if raw.startswith(("http://", "https://")):
        return raw
    base = core.base_url() if core is not None else ""
    if not base:
        return None
    # ``base`` is already trailing-slash-stripped; ``raw`` should start
    # with '/', but guard for integrations that omit the leading slash.
    sep = "" if raw.startswith("/") else "/"
    return f"{base}{sep}{raw}"


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Home Assistant Core plugin to use this widget."}

    entity_id = (options.get("entity_id") or "").strip()
    if not entity_id:
        return {"error": "Set a media_player entity in the cell options."}

    try:
        st = core.get_state(entity_id)
    except Exception as err:
        return {"error": core.coerce_error(err)}

    if not st:
        return {"error": f"Entity {entity_id} not found."}

    attrs = st.get("attributes") or {}
    state = str(st.get("state") or "").lower()
    # HA's media_player domain reports state as one of:
    #   playing / paused / idle / off / standby / unavailable / unknown
    # We collapse standby + unknown into "off" so the client only has to
    # branch on four buckets.
    if state in ("unknown", ""):
        state = "off"
    if state == "standby":
        state = "off"

    position = _f_or_none(attrs.get("media_position"))
    duration = _f_or_none(attrs.get("media_duration"))
    pct: float | None = None
    if position is not None and duration and duration > 0:
        pct = max(0.0, min(100.0, (position / duration) * 100.0))

    volume = _f_or_none(attrs.get("volume_level"))
    volume_pct = None if volume is None else max(0.0, min(100.0, volume * 100.0))

    return {
        "entity_id": entity_id,
        "name": core.friendly_name(st),
        "state": state,
        "title": _str(attrs.get("media_title")),
        "artist": _str(attrs.get("media_artist")),
        "album": _str(attrs.get("media_album_name")),
        "art_url": _resolve_art(core, str(attrs.get("entity_picture") or "")),
        "source": _str(attrs.get("source") or attrs.get("app_name")),
        "volume_pct": round(volume_pct, 1) if volume_pct is not None else None,
        "media_position": position,
        "media_duration": duration,
        "position_pct": round(pct, 1) if pct is not None else None,
    }
