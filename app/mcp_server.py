"""Tesserae MCP server — the stdio bridge an AI agent connects to.

This is a thin client over Tesserae's ``/api/mcp/*`` HTTP surface (:mod:`app.mcp_api`,
which must be running and have the ``mcp`` experiment enabled). It exposes tools an
agent uses to build **freeform (canvas) dashboards**: discover widgets and devices,
create + edit a canvas, **render a preview image** to check its own work, and push
to a panel.

Config via environment:
- ``TESSERAE_URL``        base URL of a running Tesserae (default http://127.0.0.1:8765)
- ``TESSERAE_MCP_TOKEN``  the MCP token (optional when the agent runs on the same
                          machine, since loopback is trusted)

Run it as ``tesserae-mcp`` (console script) or ``python -m app.mcp_server``. The
``mcp`` package is an optional dependency: ``pip install "tesserae[mcp]"``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_BASE = os.environ.get("TESSERAE_URL", "http://127.0.0.1:8765").rstrip("/")
_TOKEN = os.environ.get("TESSERAE_MCP_TOKEN", "").strip()

# The canvas-document shape, embedded in the set_canvas tool description so the
# agent knows exactly what to write.
_DOC_SHAPE = """A canvas document is JSON:
{
  "w": int, "h": int,                 # artboard size in px (match the target panel)
  "theme": str, "style": str,         # appearance ids from list_widgets().appearance
  "font": str, "bg": str,             # optional font id and background colour override
  "els": [ <element>, ... ]           # painted in list order: first = back, last = front
}
Each element has a unique "id" and a box "x","y","w","h" (px, top-left origin), plus
optional "opacity" (0-100) and "rotate" (degrees). By "kind":
- widget:  {"kind":"widget","widget":"<key>","fragment":"full","options":{...}}
           <key> from list_widgets(); "fragment" from that widget's fragments (or "full");
           "options" per get_widget_options(<key>).
- text:    {"kind":"text","text":"...","color":"<css or var(--accent-1)>","size":<px, 0=auto>,"align":"left|center|right"}
- rect:    {"kind":"rect","color":"...","fill":true,"stroke":<px>,"radius":<px>}
- ellipse: {"kind":"ellipse","color":"...","fill":true,"stroke":<px>}
- line:    {"kind":"line","color":"...","stroke":<px>}
- icon:    {"kind":"icon","icon":"ph-<name>","color":"...","weight":"thin|light|regular|bold|fill|duotone"}
"""


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, bytes, str]:
    """Call the Tesserae MCP API. Returns (status, raw_bytes, content_type)."""
    url = f"{_BASE}/api/mcp{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if _TOKEN:
        req.add_header("Authorization", f"Bearer {_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "") if exc.headers else ""
    except urllib.error.URLError as exc:
        detail = getattr(exc, "reason", exc)
        raise RuntimeError(
            f"Cannot reach Tesserae at {_BASE} ({detail}). Is it running, and is the "
            f"'mcp' experiment enabled in Settings → System → MCP?"
        ) from exc


def _json(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call the API expecting JSON. Non-2xx bodies are returned as-is (so the agent
    sees 422 validation details) rather than raised."""
    status, raw, _ = _request(method, path, body)
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {"error": raw.decode("utf-8", "replace")}
    if status >= 400 and isinstance(parsed, dict):
        parsed.setdefault("_status", status)
    return parsed


def build_server() -> Any:
    """Construct the FastMCP server with all tools registered.

    Tools are registered via ``add_tool`` (not the ``@tool`` decorator) so the
    optional, untyped ``mcp`` SDK doesn't strip the type info off our functions.
    """
    from mcp.server.fastmcp import FastMCP, Image

    def list_widgets() -> Any:
        """List every widget that can be placed on a canvas (with its fragments) and
        the available theme/style/font appearance options."""
        return _json("GET", "/catalog")

    def get_widget_options(widget: str) -> Any:
        """Get the configurable options for one widget, so you can fill an element's
        "options" correctly (e.g. a weather widget's location)."""
        return _json("GET", f"/widgets/{widget}/options")

    def list_devices() -> Any:
        """List registered display devices (id, name, and panel width/height in px).
        Match a canvas's w/h to the target panel for a pixel-faithful result."""
        return _json("GET", "/devices")

    def list_pages() -> Any:
        """List existing canvas (freeform) dashboards."""
        return _json("GET", "/pages")

    def create_canvas_page(name: str, w: int = 800, h: int = 480) -> Any:
        """Create a new, empty canvas dashboard and return its id. Then set_canvas()
        to lay it out. Size it to your target panel (see list_devices)."""
        return _json("POST", "/pages", {"name": name, "w": w, "h": h})

    def get_canvas(page_id: str) -> Any:
        """Get the full canvas document (size, appearance, and every element) for a page."""
        return _json("GET", f"/pages/{page_id}/canvas")

    def set_canvas(page_id: str, canvas: dict[str, Any]) -> Any:
        return _json("PUT", f"/pages/{page_id}/canvas", canvas)

    def render_preview(page_id: str) -> Any:
        """Render the canvas to a PNG at its authored size and return the image, so you
        can visually check the layout and iterate. This is your feedback loop: place →
        render_preview → adjust → set_canvas → render_preview again."""
        status, raw, ctype = _request("GET", f"/pages/{page_id}/preview.png")
        if status >= 400 or not ctype.startswith("image/"):
            raise RuntimeError(
                f"preview failed (HTTP {status}): {raw.decode('utf-8', 'replace')[:300]}"
            )
        return Image(data=raw, format="png")

    def push_to_device(page_id: str, device_ids: list[str]) -> Any:
        """Render the canvas and push it to the given devices (ids from list_devices).
        device_ids is required — pushing is always explicit."""
        return _json("POST", f"/pages/{page_id}/push", {"device_ids": device_ids})

    mcp = FastMCP("tesserae")
    for fn in (
        list_widgets,
        get_widget_options,
        list_devices,
        list_pages,
        create_canvas_page,
        get_canvas,
        render_preview,
        push_to_device,
    ):
        mcp.add_tool(fn)
    mcp.add_tool(
        set_canvas,
        description=(
            "Replace a canvas dashboard's document. Returns the saved document, or an "
            'error with field-level "details" (HTTP 422) if the document is invalid, so '
            "you can correct it and retry. After setting, call render_preview() to see "
            "the result.\n\n" + _DOC_SHAPE
        ),
    )
    return mcp


def main() -> None:
    try:
        server = build_server()
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] == "mcp":
            raise SystemExit(
                "The MCP SDK isn't installed. Install it with:\n\n"
                '    pip install "tesserae[mcp]"\n'
            ) from exc
        raise
    server.run()


if __name__ == "__main__":
    main()
