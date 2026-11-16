"""A widget whose Picture choice depends on the Printer picked above it, for
the disabled-choices tests (server and browser)."""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN_ID = "pics"
REASON = (
    "Not available for Printer A: Home Assistant gets no sliced files from it. "
    "Availability depends on the printer. Save and reopen to refresh."
)

SERVER_PY = f"""
REASON = {REASON!r}


def choices(name, options=None):
    if name == "printer":
        return [{{"value": "alpha", "label": "Printer A"}}, {{"value": "beta", "label": "BETA"}}]
    if name == "method":
        printer = (options or {{}}).get("printer") or "alpha"
        rows = [
            {{"value": "toolpath", "label": "Toolpaths", "image": "/plugins/pics/static/toolpath.svg"}},
            {{"value": "preview", "label": "Line art from the preview",
              "image": "/plugins/pics/static/preview.svg"}},
            {{"value": "ai", "label": "AI engraving", "image": "/plugins/pics/static/ai.svg"}},
        ]
        if printer == "alpha":
            rows[0].update(disabled=True, reason=REASON)
        return rows
    return []
"""

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240">'
    '<rect width="320" height="240" fill="{bg}"/>'
    '<path d="M40 200 L160 40 L280 200 Z" fill="none" stroke="#fff" stroke-width="6"/></svg>'
)


def write_plugin(plugins_dir: Path) -> None:
    d = plugins_dir / PLUGIN_ID
    (d / "static").mkdir(parents=True)
    manifest = {
        "tesserae_compat": "1.x",
        "name": "Pics",
        "version": "0.0.1",
        "kind": "widget",
        "supports": {"sizes": ["sm", "md", "lg"]},
        "cell_options": [
            {
                "name": "printer",
                "type": "select",
                "label": "Printer",
                "default": "",
                "choices_from": "printer",
            },
            {
                "name": "method",
                "type": "select",
                "label": "Picture",
                "default": "toolpath",
                "choices_from": "method",
                "help": "Availability depends on the printer. Save and reopen to refresh.",
            },
            {
                "name": "size",
                "type": "select",
                "label": "Size",
                "default": "s",
                "choices": [
                    {"value": "s", "label": "Small"},
                    {
                        "value": "l",
                        "label": "Large",
                        "disabled": True,
                        "reason": "Too big for this display.",
                    },
                ],
            },
        ],
    }
    (d / "plugin.json").write_text(json.dumps(manifest))
    (d / "client.js").write_text("export default function () {}\n")
    (d / "server.py").write_text(SERVER_PY)
    for name, bg in (("toolpath", "#1f4e8c"), ("preview", "#2d6a4f"), ("ai", "#7b2d8c")):
        (d / "static" / f"{name}.svg").write_text(_SVG.format(bg=bg))
