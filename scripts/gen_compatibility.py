#!/usr/bin/env python3
"""Generate docs/compatibility.md from the live manifests + panel presets.

Pulls panel sizes from ``app.panel.PANEL_PRESET_CHOICES``, renderer details
from ``renderers/<id>/renderer.json``, device kinds from
``devices/<id>/device.json``, and the maintainer's real-world test status
from the hand-maintained ``docs/_data/tested.json``.

Run from the repo root:  ``python scripts/gen_compatibility.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.panel import PANEL_PRESET_CHOICES  # noqa: E402

RENDERERS_DIR = REPO_ROOT / "renderers"
DEVICES_DIR = REPO_ROOT / "devices"
TESTED = REPO_ROOT / "docs" / "_data" / "tested.json"
OUT = REPO_ROOT / "docs" / "compatibility.md"

# Reference client repos, keyed by renderer id. A renderer can pair
# with multiple firmware/client repos (e.g. ``esp32_bin`` serves both
# the 13.3" Waveshare client and the 7.3" PhotoPainter client), each
# entry is a list of ``(label, url)`` tuples.
CLIENT_REPOS: dict[str, list[tuple[str, str]]] = {
    "pi_png": [
        ("tesserae-device-pi-png", "https://github.com/dmellok/tesserae-device-pi-png"),
    ],
    "pi_bin": [
        ("tesserae-device-pi-bin", "https://github.com/dmellok/tesserae-device-pi-bin"),
    ],
    "esp32_bin": [
        (
            'tesserae-device-esp32-bin (13.3" Waveshare)',
            "https://github.com/dmellok/tesserae-device-esp32-bin",
        ),
        (
            'tesserae-device-photopainter-7.3-bin (7.3" PhotoPainter)',
            "https://github.com/dmellok/tesserae-device-photopainter-7.3-bin",
        ),
    ],
    "trmnl_png": [
        ("tesserae-trmnl-client", "https://github.com/dmellok/tesserae-trmnl-client"),
    ],
}


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _manifests(directory: Path) -> list[dict]:
    out: list[dict] = []
    if not directory.exists():
        return out
    for child in sorted(directory.iterdir()):
        name = "device.json" if (child / "device.json").exists() else "renderer.json"
        manifest = child / name
        if child.is_dir() and manifest.exists():
            data = _load_json(manifest)
            data["_id"] = child.name
            out.append(data)
    return out


def _panel_table() -> list[str]:
    rows = ["| Preset | Native resolution | Panel |", "|---|---|---|"]
    for choice in PANEL_PRESET_CHOICES:
        if choice["value"] == "custom":
            continue
        label = choice["label"]
        # label is e.g. 'Inky Impression 13.3", 1600x1200'; split on the dash.
        panel, _, res = label.partition("-")
        rows.append(f"| `{choice['value']}` | {res.strip()} | {panel.strip()} |")
    rows.append(
        "| `custom` | any (set width + height) | anything the inky / Waveshare path drives |"
    )
    return rows


def _renderer_table() -> list[str]:
    rows = ["| Renderer | Output | Target client(s) | What it's for |", "|---|---|---|---|"]
    for m in _manifests(RENDERERS_DIR):
        rid = m["_id"]
        ext = m.get("extension", "")
        repos = CLIENT_REPOS.get(rid) or []
        client = "<br>".join(f"[{label}]({url})" for label, url in repos) if repos else "-"
        desc = m.get("description", "").strip().replace("\n", " ")
        # Keep the table cell short, first sentence only.
        first = desc.split(". ")[0].rstrip(".") + "." if desc else ""
        rows.append(f"| `{rid}` | `.{ext}` | {client} | {first} |")
    return rows


def _device_table() -> list[str]:
    rows = ["| Device kind | Default panel | Renderers | What it is |", "|---|---|---|---|"]
    for m in _manifests(DEVICES_DIR):
        did = m["_id"]
        panel = m.get("panel", {})
        dims = f"{panel.get('w', '?')}×{panel.get('h', '?')}" if panel else "-"
        renderers = ", ".join(f"`{r}`" for r in m.get("renderers", [])) or "-"
        desc = m.get("description", "").strip().replace("\n", " ")
        first = desc.split(". ")[0].rstrip(".") + "." if desc else ""
        rows.append(f"| `{did}` | {dims} | {renderers} | {first} |")
    return rows


def _tested_table() -> list[str]:
    data = _load_json(TESTED).get("renderers", {})
    rows = [
        "| Renderer | Hardware | Status | Notes |",
        "|---|---|---|---|",
    ]
    icon = {"Tested": ":material-check-circle:", "Partial": ":material-progress-helper:"}
    for m in _manifests(RENDERERS_DIR):
        rid = m["_id"]
        entry = data.get(rid, {})
        status = entry.get("status", "Not yet tested")
        badge = f"{icon.get(status, ':material-circle-outline:')} {status}"
        hw = entry.get("hardware", "-")
        notes = entry.get("notes", "") or "-"
        rows.append(f"| `{rid}` | {hw} | {badge} | {notes} |")
    return rows


def render() -> str:
    out: list[str] = []
    out.append("<!-- AUTO-GENERATED by scripts/gen_compatibility.py, do not edit by hand.")
    out.append("     The test matrix is sourced from docs/_data/tested.json, edit that. -->")
    out.append("")
    out.append("# Screens & compatibility")
    out.append("")
    out.append(
        "Tesserae renders a dashboard headlessly and pushes the frame over MQTT; a "
        "small client on the other end paints your panel. What panel you can drive "
        "comes down to which **client** you flash and which **renderer** feeds it."
    )
    out.append("")
    out.append("## Panel presets")
    out.append("")
    out.append(
        "Built-in sizes (Settings → Panel). Pick `custom` for anything not listed, "
        "the dimensions are all that matter to the renderer."
    )
    out.append("")
    out.extend(_panel_table())
    out.append("")
    out.append("## Renderers")
    out.append("")
    out.append(
        "A renderer turns the composition PNG into the exact bytes a client wants. "
        "Each ships as a drop-a-folder plugin under `renderers/<id>/`."
    )
    out.append("")
    out.extend(_renderer_table())
    out.append("")
    out.append("## Device kinds")
    out.append("")
    out.append(
        "The bundled client kinds Tesserae knows how to talk to. A flashed client "
        "announces itself on `tesserae/<device-id>/status` and shows up under "
        "Settings → Devices → Discovered. See "
        "[Install a client](install/clients.md) and [Set up a device](install/devices.md)."
    )
    out.append("")
    out.extend(_device_table())
    out.append("")
    out.append("## What's been tested on real hardware")
    out.append("")
    out.append(
        "Honest status from the maintainer's own bench. Untested doesn't mean "
        "broken, it means nobody's confirmed it yet. Got one working? "
        "[Open a PR or issue](https://github.com/dmellok/tesserae/issues) and "
        "we'll mark it."
    )
    out.append("")
    out.extend(_tested_table())
    out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
