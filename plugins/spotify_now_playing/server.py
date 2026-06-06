"""spotify_now_playing, current track title/artist/album + progress.

Thin widget over spotify_core: it owns the OAuth + API; we shape the
now-playing result for the client.
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
    if np.get("idle") or not np.get("ok"):
        return {"idle": True}
    return {
        "track": np.get("track", ""),
        "artist": np.get("artist", ""),
        "album": np.get("album", ""),
        "album_art": np.get("album_art"),
        "is_playing": np.get("is_playing", False),
        "progress_ms": np.get("progress_ms", 0),
        "duration_ms": np.get("duration_ms", 0),
    }
