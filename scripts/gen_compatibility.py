#!/usr/bin/env python3
"""Generate docs/compatibility.md from the live manifests + panel presets.

Pulls panel sizes from ``app.panel.PANEL_PRESET_CHOICES``, renderer details
from ``renderers/<id>/renderer.json``, device kinds (protocols) from
``devices/<id>/device.json``, vendor SKUs from ``hardware/<vendor>/*.json``,
and the maintainer's real-world test status from the hand-maintained
``docs/_data/tested.json``.

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
HARDWARE_DIR = REPO_ROOT / "hardware"
TESTED = REPO_ROOT / "docs" / "_data" / "tested.json"
OUT = REPO_ROOT / "docs" / "compatibility.md"

# Per-vendor label + ordering. Seeed is listed first per Kayden's
# commitment to the Seeed ecosystem team ("Seeed section at the top of
# the supported devices lineup"). Others alphabetical after.
VENDOR_ORDER: list[tuple[str, str]] = [
    ("seeed", "Seeed Studio"),
    ("pimoroni", "Pimoroni"),
    ("trmnl", "TRMNL"),
    ("waveshare", "Waveshare"),
]

# Vendor id -> URL. Displayed as the section link so a reader can jump
# straight to the vendor's own catalog.
VENDOR_URL: dict[str, str] = {
    "seeed": "https://www.seeedstudio.com/",
    "pimoroni": "https://shop.pimoroni.com/",
    "trmnl": "https://usetrmnl.com/",
    "waveshare": "https://www.waveshare.com/",
}

# Vendor id -> intro paragraph. Rendered between the vendor heading and
# the SKU table. Optional; a vendor with no intro just gets its heading
# straight into the table. Used to surface firmware / flasher /
# ready-to-go framing where a vendor's SKUs share one delivery path.
VENDOR_INTRO: dict[str, str] = {
    "seeed": (
        "The reTerminal E-Series and XIAO ePaper family run the "
        "[Tesserae-native firmware](https://github.com/dmellok/tesserae-device-firmware); "
        "flash from the browser in one click at "
        "[tesserae.ink/flash](https://tesserae.ink/flash) (Chrome / Edge, "
        "Web Serial, no toolchain). Battery-powered, no assembly required. "
        'The XIAO 7.5" and TRMNL 7.5" OG DIY Kit also run the TRMNL BYOS '
        "firmware path if you'd rather stay on stock."
    ),
}

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
        # Labels are ``"Panel name, WxH"`` (e.g. ``Inky Impression 13.3",
        # 1600x1200``). rsplit on the last comma keeps the panel name
        # intact even when it contains parenthesised suffixes with hyphens
        # (``ESP32-S3``) or additional commas. Fixes the pre-v0.64.63
        # bug where ``partition('-')`` split on the hyphen inside
        # ``(ESP32-S3)`` and stranded ``S3), 800x480`` in the resolution
        # column.
        head, _, tail = label.rpartition(", ")
        if head and tail:
            panel, res = head, tail
        else:
            panel, res = label, ""
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


def _hardware_by_vendor() -> dict[str, list[dict]]:
    """Walk ``hardware/<vendor>/*.json`` and bucket entries by their
    parent directory name. Each entry keeps its manifest verbatim so
    the section renderer can pull any field it wants."""
    out: dict[str, list[dict]] = {}
    if not HARDWARE_DIR.exists():
        return out
    for vendor_dir in sorted(HARDWARE_DIR.iterdir()):
        if not vendor_dir.is_dir():
            continue
        entries: list[dict] = []
        for path in sorted(vendor_dir.glob("*.json")):
            if path.name.startswith((".", "_")):
                continue
            data = _load_json(path)
            if data:
                entries.append(data)
        if entries:
            out[vendor_dir.name] = entries
    return out


def _hardware_sections() -> list[str]:
    """Per-vendor tables of hardware-catalog SKUs. Seeed is listed
    first (per the Seeed ecosystem commitment), the rest alphabetical.
    Any vendor directory not in ``VENDOR_ORDER`` still renders at the
    end so a newly-added vendor shows up without a code change."""
    by_vendor = _hardware_by_vendor()
    if not by_vendor:
        return []

    ordered: list[tuple[str, str]] = list(VENDOR_ORDER)
    listed = {v_id for v_id, _ in ordered}
    for v_id in by_vendor:
        if v_id not in listed:
            # Trailing new vendors: display-cased name as the label.
            ordered.append((v_id, v_id.replace("_", " ").title()))

    out: list[str] = []
    out.append("## Hardware SKUs")
    out.append("")
    out.append(
        "Per-vendor catalog of specific device / panel SKUs Tesserae ships "
        "manifests for. Each entry maps to a device kind (protocol) + "
        "renderer combination that Tesserae picks automatically once the "
        "device pairs. Click a product name for the vendor's own page."
    )
    out.append("")
    for vendor_id, vendor_label in ordered:
        entries = by_vendor.get(vendor_id)
        if not entries:
            continue
        vendor_url = VENDOR_URL.get(vendor_id)
        heading = f"[{vendor_label}]({vendor_url})" if vendor_url else vendor_label
        out.append(f"### {heading}")
        out.append("")
        intro = VENDOR_INTRO.get(vendor_id)
        if intro:
            out.append(intro)
            out.append("")
        out.append("| SKU | Panel | Gamut | Protocol / Renderer | Kind id |")
        out.append("|---|---|---|---|---|")
        for entry in entries:
            name = entry.get("name", "")
            url = entry.get("url", "")
            sku_cell = f"[{name}]({url})" if url else name
            panel = entry.get("panel") or {}
            w = panel.get("w", "?")
            h = panel.get("h", "?")
            orient = panel.get("orientation") or ""
            dims = f"{w}×{h}"
            if orient and orient not in ("landscape",):
                dims = f"{dims} {orient}"
            gamut = panel.get("gamut", "-") or "-"
            protocol = entry.get("protocol", "-")
            # Manifest ``renderers`` override wins over the protocol's
            # own renderer list. Only inherit note is shown so a reader
            # can see when a SKU is choosing its own renderer.
            renderers = entry.get("renderers")
            if isinstance(renderers, list) and renderers:
                rend_cell = ", ".join(f"`{r}`" for r in renderers)
                proto_cell = f"`{protocol}` <br> {rend_cell}"
            else:
                proto_cell = f"`{protocol}` (inherit)"
            kind_id = entry.get("id", "-")
            rows_cells = [sku_cell, dims, f"`{gamut}`", proto_cell, f"`{kind_id}`"]
            out.append("| " + " | ".join(str(c) for c in rows_cells) + " |")
        out.append("")
    return out


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
        "Tesserae renders a dashboard headlessly and delivers the frame to a small "
        "client that paints your panel. Delivery is either MQTT (retained frame "
        "topic, always-on clients wake and receive) or REST (battery-poll clients "
        "hit `/api/v1/device/<id>/frame`). What panel you can drive comes down to "
        "which **hardware SKU** you have, which **client / protocol** talks to it, "
        "and which **renderer** produces the exact byte format the panel needs."
    )
    out.append("")
    out.append(
        "The **Hardware SKUs** section below is the fastest way to find your "
        "device. Every SKU listed there ships as a data-only manifest under "
        "`hardware/<vendor>/<sku>.json` and shows up automatically in Tesserae's "
        "Settings → Devices → Add Device kind picker with the right dims, gamut, "
        "and renderer wired up."
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
    out.extend(_hardware_sections())
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
