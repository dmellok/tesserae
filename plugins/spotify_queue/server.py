"""spotify_queue, current track + next few items from the user's queue.

Thin widget over spotify_core: that plugin owns the OAuth + token refresh
+ /v1/me/player/queue call; we shape the result for the client.
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
    del settings, ctx
    core = _core()
    if core is None:
        return {"error": "Install the Spotify Core plugin to use this widget."}
    max_items = max(1, min(12, int(options.get("max_items") or 6)))
    show_now = bool(options.get("show_now_playing", True))

    q: dict[str, Any] = core.queue()
    if q.get("error"):
        return {"error": q["error"]}
    if q.get("idle") or not q.get("ok"):
        return {"idle": True}
    return {
        "currently_playing": q.get("currently_playing") if show_now else None,
        "queue": (q.get("queue") or [])[:max_items],
    }
