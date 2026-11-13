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
import re
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
    ("m5stack", "M5Stack"),
    ("lilygo", "LILYGO"),
    ("fraimic", "Fraimic"),
    ("paperlesspaper", "paperlesspaper"),
    ("pimoroni", "Pimoroni"),
    ("remarkable", "reMarkable"),
    ("trmnl", "TRMNL"),
    ("waveshare", "Waveshare"),
    ("xteink", "Xteink"),
    ("amazon", "Amazon Kindle"),
    ("kobo", "Kobo"),
    ("gooddisplay", "Good Display"),
    ("elecrow", "Elecrow"),
    ("epdiy", "epdiy"),
    ("community", "Community"),
    ("zectrix", "ZecTrix"),
]

# Affiliate disclosure, rendered under the SKU-table intro.
#
# Some Seeed product links carry an affiliate parameter, and the Pimoroni Inky
# Frame and Inky Impression links are Shopify Collabs affiliate links, so the wording has to be
# visible next to the links rather than buried in a footer. Every tagged link
# pays a commission. Only the Seeed tag is known to apply a discount at
# checkout (confirmed on a real checkout, not just the product page, which
# shows the undiscounted price), so the discount is claimed for Seeed alone.
AFFILIATE_DISCLOSURE = (
    '!!! info "Some links are affiliate links"\n'
    "    Seeed product links and the Pimoroni Inky Frame and Inky Impression "
    "links carry a Tesserae affiliate tag. Buying through them pays a small "
    "commission to the project. Seeed links also give a discount at checkout.\n\n"
    "    Tags do not affect which products are listed or their tested / not-tested "
    "status. Other links are plain product links.\n\n"
    "    Fraimic links are plain links, with a discount code instead: "
    "**TESSERAE10** takes 10% off at fraimic.com. Fraimic supplied a Large "
    "Canvas to Tesserae free of charge, and Tesserae earns a commission on "
    "sales made with the code."
)

# Vendor id -> URL. Displayed as the section link so a reader can jump
# straight to the vendor's own catalog.
VENDOR_URL: dict[str, str] = {
    "seeed": "https://www.seeedstudio.com/",
    "m5stack": "https://m5stack.com/",
    "fraimic": "https://fraimic.com/",
    "paperlesspaper": "https://paperlesspaper.de/en",
    "pimoroni": "https://shop.pimoroni.com/",
    "remarkable": "https://remarkable.com/",
    "trmnl": "https://usetrmnl.com/",
    "waveshare": "https://www.waveshare.com/",
    "xteink": "https://www.xteink.com/",
    "amazon": "https://www.amazon.com/kindle",
    "kobo": "https://www.kobo.com/ereaders",
    "gooddisplay": "https://www.good-display.com/",
    "epdiy": "https://github.com/vroland/epdiy",
}

# Vendor id -> intro paragraph. Rendered between the vendor heading and
# the SKU table. Optional; a vendor with no intro just gets its heading
# straight into the table. Used to surface firmware / flasher /
# ready-to-go framing where a vendor's SKUs share one delivery path.
VENDOR_INTRO: dict[str, str] = {
    "seeed": (
        "The reTerminal E-Series and XIAO ePaper family run the "
        "[Tesserae-native firmware](https://github.com/dmellok/tesserae-device-firmware). "
        "Flash in one click at [tesserae.ink/flash](https://tesserae.ink/flash) "
        "using Chrome or Edge with Web Serial; no build tools are needed. "
        "They run on batteries and need no assembly.\n\n"
        'The XIAO 7.5" and TRMNL 7.5" OG DIY Kit also run the TRMNL BYOS '
        "firmware if you prefer stock."
    ),
    "m5stack": (
        "Runs the "
        "[Tesserae-native firmware](https://github.com/dmellok/tesserae-device-firmware)."
        "\n\n- PaperS3: the ESP32-S3 drives the parallel panel bus directly, without a controller chip. "
        "Its firmware waveform table produces 16 greys; tuning requires a build-time edit instead of a vendor LUT. "
        "Partial refresh, tap echo and on-device touch widgets are not supported yet.\n"
        '- Original M5Paper: the same 4.7" glass, with an IT8951 controller on the ESP32-D0WDQ6-V3. '
        "It uses the reTerminal E1003's greyscale driver unchanged. "
        "Both M5Paper SKUs use the same 259200-byte frame."
    ),
    "pimoroni": (
        "- Inky Impressions: use a Raspberry Pi with "
        "[tesserae-device-pi](https://github.com/dmellok/tesserae-device-pi).\n"
        "- Inky Frame: has its own Pico and runs "
        "[tesserae-device-inkyframe](https://github.com/dmellok/tesserae-device-inkyframe). "
        "Flash in your browser at "
        "[tesserae.ink/flash/inky-frame](https://tesserae.ink/flash/inky-frame) "
        "using Chrome or Edge with WebUSB; no build tools are needed.\n"
        "- Badgers: run "
        "[tesserae-device-badger](https://github.com/dmellok/tesserae-device-badger) "
        "as an app in their launcher."
    ),
    "fraimic": (
        "Framed Spectra 6 colour e-paper canvases on battery, driven over the "
        "frame's own local REST API: no Fraimic account, app, cloud or firmware "
        "change. Tesserae sets the frame's wake schedule, uploads the latest "
        "picture at each wake and puts it back to sleep, with wakes as close as "
        "five minutes apart; see the [Fraimic quickstart](quickstart/fraimic.md). "
        "The Large Canvas is confirmed on the bench, and the Standard Canvas by "
        "a community report.\n\n"
        "The code **TESSERAE10** takes 10% off either canvas at "
        "[fraimic.com](https://fraimic.com/). Fraimic supplied the Large Canvas "
        "free of charge, and Tesserae earns a commission on sales made with the "
        "code."
    ),
    "paperlesspaper": (
        "Open-hardware picture frames with ESP32-C6 and NiMH batteries. "
        "Install [Tesserae-native firmware](https://github.com/dmellok/tesserae-device-firmware) "
        "instead of the vendor's cloud firmware at "
        "[tesserae.ink/flash](https://tesserae.ink/flash).\n\n"
        "Flashing overwrites the vendor cloud certificate. Back up its region first to keep a copy. "
        "paperlesspaper support can restore cloud access if you want to return to the vendor service.\n\n"
        "OpenPaper 7 and OpenPaper L are confirmed on hardware."
    ),
    "remarkable": (
        "Use the "
        "[tesserae.remarkable](https://github.com/partridgeworks/tesserae.remarkable) "
        "client, an AppLoad app inside the tablet's interface. "
        "Developer Mode and the XOVI/AppLoad runtime are required.\n\n"
        "It uses the v1 REST device API to draw server-dithered frames through the tablet's display system. "
        "It deep-sleeps between polls and wakes on an RTC alarm.\n\n"
        "reMarkable 2 is confirmed on hardware. reMarkable 1 and Paper Pro builds are not yet confirmed."
    ),
    "amazon": (
        "Jailbreak the Kindle before installing KOReader. The "
        "[Tesserae KOReader plugin](https://github.com/dmellok/tesserae-koreader) "
        "pairs with a claim code and downloads 16-level greyscale frames at the server's chosen interval. "
        "It sleeps between downloads on KOReader builds that expose the hardware alarm.\n\n"
        "Any Kindle running KOReader works; the plugin reports its actual screen size when pairing. "
        "The listed models have a store entry and known resolution."
    ),
    "kobo": (
        "Kobo readers run KOReader without a jailbreak. Use the same "
        "[Tesserae KOReader plugin](https://github.com/dmellok/tesserae-koreader) "
        "with native hardware wake, so the reader sleeps between refreshes without extra setup."
    ),
    "xteink": (
        "These e-readers run "
        "[CrossInk](https://github.com/uxjulia/CrossInk) firmware with "
        "Tesserae support. The dashboard appears as the sleep screen when the device enters sleep, without a timer. "
        "The radio never wakes it on its own.\n\n"
        "Panels are mounted portrait. Frames are composed at portrait dimensions, then "
        "`esp32_bw_bin` packs them at the firmware's native landscape stride."
    ),
    "community": (
        "These devices use community firmware that speaks Tesserae's device API. "
        "Each SKU links to its firmware repo, where its author is credited."
    ),
}

# Reference client repos, keyed by renderer id. A renderer can pair
# with multiple firmware/client repos (e.g. ``esp32_bin`` serves both
# the 13.3" Waveshare client and the 7.3" PhotoPainter client), each
# entry is a list of ``(label, url)`` tuples.
CLIENT_REPOS: dict[str, list[tuple[str, str]]] = {
    "pi_png": [
        ("tesserae-device-pi", "https://github.com/dmellok/tesserae-device-pi"),
    ],
    "pi_bin": [
        ("tesserae-device-pi", "https://github.com/dmellok/tesserae-device-pi"),
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


# A period followed by a space ends the first sentence, unless it is the
# one closing "e.g." or "i.e.".
_SENTENCE_END = re.compile(r"(?<!e\.g)(?<!i\.e)\.\s")


def _first_sentence(desc: str) -> str:
    """The first sentence of a manifest description, to keep a table cell short."""
    if not desc:
        return ""
    end = _SENTENCE_END.search(desc)
    first = desc[: end.start()] if end else desc
    return first.rstrip(".") + "."


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
        first = _first_sentence(desc)
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
        first = _first_sentence(desc)
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
        "Tesserae chooses each model's protocol and renderer when it pairs. "
        "Click a product name for the vendor's page."
    )
    out.append("")
    out.append(AFFILIATE_DISCLOSURE)
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
            gamut_cell = f"`{gamut}`"
            depths = entry.get("colour_depths")
            if isinstance(depths, dict) and len(depths) > 1:
                # Boards whose firmware paints more than one depth: list them,
                # the panel's default first.
                others = [g for g in depths if g != gamut]
                gamut_cell = " / ".join(f"`{g}`" for g in [gamut, *others])
            rows_cells = [sku_cell, dims, gamut_cell, proto_cell, f"`{kind_id}`"]
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
        "Tesserae draws dashboards in a hidden browser. "
        "It sends pictures to client software, which drives your screen. "
        "Your hardware model, client protocol and renderer must agree on the picture's byte format.\n\n"
        "- MQTT: always-on clients wake and receive frames from a retained topic.\n"
        "- REST: battery-poll clients request `/api/v1/device/<id>/frame`."
    )
    out.append("")
    out.append(
        "Find your device under Hardware SKUs below. Its data-only definition in "
        "`hardware/<vendor>/<sku>.json` adds it to Settings → Devices → Add Device "
        "with dimensions, colour range and renderer already set."
    )
    out.append("")
    out.append("## Panel presets")
    out.append("")
    out.append(
        "Choose a built-in size from Panel size in Settings → Devices → Add device. "
        "Use `custom` for unlisted screens; the renderer only needs the dimensions."
    )
    out.append("")
    out.extend(_panel_table())
    out.append("")
    out.append("## Renderers")
    out.append("")
    out.append(
        "A renderer converts the dashboard PNG to the client's byte format. "
        "Each is a plugin folder under `renderers/<id>/`."
    )
    out.append("")
    out.extend(_renderer_table())
    out.append("")
    out.append("## Device kinds")
    out.append("")
    out.append(
        "Supported clients appear under Settings → Devices → Discovered after flashing. "
        "They announce themselves over MQTT on `tesserae/<device-id>/status`, "
        "or over REST with `POST /api/v1/device/discover`. See "
        "[Install a client](install/clients.md) and [Set up a device](install/devices.md)."
    )
    out.append("")
    out.extend(_device_table())
    out.append("")
    out.extend(_hardware_sections())
    out.append("## What's been tested on real hardware")
    out.append("")
    out.append(
        "Results from the maintainer's hardware. Untested means unconfirmed, not broken. "
        "If yours works, "
        "[open a PR or issue](https://github.com/dmellok/tesserae/issues) and "
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
