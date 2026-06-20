"""Capture current-state screenshots for the UX backlog handoff.

Spawns a throwaway Tesserae instance with ``testing=True`` (so the
auth gate is bypassed and the data dir is a temp), pre-populates a
couple of fake devices + a discovered entry, then drives playwright
through Settings → Devices to capture the regions named in the
open UX issues (#16 / #17 / #22). Screenshots land in
``notes/design-handoffs/ux-backlog/reference/current-state/``.

Run from the repo root:

    .venv/bin/python scripts/capture_ux_screenshots.py
"""

from __future__ import annotations

import socket
import threading
import time
from wsgiref.simple_server import make_server

from playwright.sync_api import sync_playwright

from app.app_factory import REPO_ROOT, create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _populate(app):
    """Add a registered device + a discovered entry so the page
    renders content the design agent can read off."""
    devices = app.config["DEVICE_REGISTRY"]
    discovery = app.config["DISCOVERY_CACHE"]

    # One registered REST device so the per-instance card surfaces
    # (its Connection details + transport flip live there).
    client = app.test_client()
    client.post(
        "/settings/devices/add",
        data={
            "id": "lounge",
            "kind": "esp32_client",
            "name": "Lounge",
            "panel_preset": "inky_13_3",
        },
    )
    # Flip to REST so the dormant-MQTT hide / transport-flip story
    # is visible.
    client.post("/settings/devices/lounge/set-transport", data={"transport": "rest"})
    # Plus a second device so the strip has a comparison row.
    client.post(
        "/settings/devices/add",
        data={
            "id": "kitchen_pi",
            "kind": "pi_bin_client",
            "name": "Kitchen Pi",
            "panel_preset": "inky_7_3",
        },
    )

    # One MQTT-style discovered entry + one REST-style discovered
    # entry so the Discovered strip shows both transport variants
    # (issue #17 is exactly this).
    import json

    discovery.record(
        "hallway_esp",
        json.dumps({"battery_pct": 88, "rssi": -68, "ip": "192.168.50.74"}).encode(),
    )
    discovery.record(
        "garage_pico",
        json.dumps(
            {"transport": "rest", "mac": "AA:BB:CC:11:22:33", "ip": "192.168.50.91"}
        ).encode(),
    )
    _ = devices  # silence linter; iteration above already populated registrations


def main() -> None:
    out_dir = REPO_ROOT / "notes" / "design-handoffs" / "ux-backlog" / "reference" / "current-state"
    out_dir.mkdir(parents=True, exist_ok=True)

    # testing=True bypasses the auth gate + swaps in a tmp data
    # root. We deliberately don't pass plugins_dir / renderers_dir;
    # the defaults already point at the repo's bundled set.
    app = create_app(testing=True)
    _populate(app)

    port = _free_port()
    server = make_server("127.0.0.1", port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.4)  # let the WSGI server start accepting

    base = f"http://127.0.0.1:{port}"
    # Each entry is (output_filename, url, css_selector_or_None).
    # None = full-page screenshot; a selector grabs just that element's
    # bounding box (cropped) so each issue gets a focused view.
    targets = [
        ("settings-devices-full-page.png", "/settings/devices", None),
        # Issue #16: the two parallel "add device" entry points sit
        # back-to-back on the Devices tab. Capture both cards by
        # grabbing them with a CSS combinator.
        ("16-add-device-current.png", "/settings/devices", "#add-device"),
        ("16-pair-device-current.png", "/settings/devices", "#pair-device"),
        # Issue #17: the Discovered strip's first child block on the
        # Devices tab.
        ("17-discovered-strip-current.png", "/settings/devices", "#discovered-devices"),
        # Issue #22: no kind-defaults editing UI exists, so the
        # supporting shot is one of the user-instance cards showing
        # the "Instance of: <kind>" row (in Connection details). That
        # makes the asymmetry visible — instances are editable,
        # kinds aren't.
        ("22-device-card-current.png", "/settings/devices", "#device-lounge"),
    ]

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1400, "height": 1800})
            page = ctx.new_page()
            for name, path, selector in targets:
                page.goto(base + path, wait_until="domcontentloaded")
                page.wait_for_timeout(500)
                if selector is None:
                    page.screenshot(path=str(out_dir / name), full_page=True)
                else:
                    element = page.query_selector(selector)
                    if element is None:
                        print(f"skip {name}: selector matched nothing")
                        continue
                    element.screenshot(path=str(out_dir / name))
                print(f"wrote {out_dir / name}")
            browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
