"""spotify_album_art, full-bleed album art of the current track.

Thin widget: all the OAuth + API work lives in spotify_core; we just
reach in via the registry and shape the now-playing result for the
client.
"""

from __future__ import annotations

from typing import Any

from flask import current_app


def _core() -> Any:
    plugin = current_app.config["PLUGIN_REGISTRY"].get("spotify_core")
    return plugin.server_module if plugin is not None else None


def fetch(
    options: dict[str, Any], settings: dict[str, Any], *, ctx: dict[str, Any]
) -> dict[str, Any]:
    del options, settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Spotify Core plugin to use this widget."}
    np: dict[str, Any] = core.now_playing()
    if np.get("error"):
        return {"error": np["error"]}
    if np.get("idle") or not np.get("album_art"):
        return {"idle": True}
    return {
        "album_art": np["album_art"],
        "is_playing": np.get("is_playing", False),
        "track": np.get("track", ""),
        "artist": np.get("artist", ""),
    }
