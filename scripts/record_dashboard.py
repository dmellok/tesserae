"""Record a video of building a dashboard in Tesserae.

Seeds an "already onboarded" Tesserae (admin password set, onboarding
flag flipped, one device registered) before launching, so the recording
opens on the Dashboards list and is purely about composing, no wizard
pre-roll. Then drives Playwright through:

  1. /login
  2. /pages           → name the dashboard, hit Create
  3. /pages/<id>      → bind the registered device
                       pick a 2-columns layout
                       cell 1 → weather_now
                       cell 2 → clock_word
                       hold on the live preview as it renders

Usage::

    python scripts/record_dashboard.py --output ~/Desktop/dashboard.mp4

See ``scripts/_recording.py`` for the shared lifecycle + cursor + ffmpeg
transcode.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from _recording import REPO_ROOT, CursorDriver, add_common_cli_args, run_scenario

# ----- Per-step hold pacing -------------------------------------------
#
# Each step's hold is scaled to its visual density. The editor is the
# most info-dense screen Tesserae has, layout grid, cells, sidebar,
# preview, so it gets the longest holds.
HOLD_LOGIN = 2.5
HOLD_LIST = 3.0
HOLD_EDITOR_INTRO = 4.5
HOLD_AFTER_LAYOUT = 3.5
HOLD_CELL_PICK = 2.8
HOLD_AFTER_WIDGET = 3.0
HOLD_CUSTOM_OPEN = 2.5  # the Custom-layout details expanded
HOLD_AFTER_INSERT = 3.0  # let the new cell settle into the board
HOLD_PREVIEW = 5.5  # the payoff frame, give it room
HOLD_DONE = 1.0
BEAT_S = 0.5
READ_PAUSE_S = 1.2


# ----- Data-root prep -------------------------------------------------


def prepare_onboarded_state(data_root: Path) -> None:
    """Seed the tmp data root with an already-onboarded install: admin
    password set, onboarding flag flipped, one Inky-Impression-13.3″
    device registered. Avoids re-recording the wizard for every scenario
    that isn't actually about the wizard."""
    from app import device_loader, device_service, renderer_loader
    from app.auth import set_password
    from app.onboarding import mark_onboarded
    from app.state.settings_store import SettingsStore

    core_dir = data_root / "core"
    core_dir.mkdir(parents=True, exist_ok=True)
    device_data = data_root / "devices"
    renderer_data = data_root / "renderers"
    plugin_data = data_root / "plugins"
    for d in (device_data, renderer_data, plugin_data):
        d.mkdir(parents=True, exist_ok=True)

    settings = SettingsStore(core_dir / "settings.json")
    set_password(settings, "demo1234")
    mark_onboarded(settings)

    devices = device_loader.discover(
        REPO_ROOT / "devices",
        schema_path=REPO_ROOT / "schema" / "device.schema.json",
        data_root=device_data,
    )
    renderers = renderer_loader.discover(
        REPO_ROOT / "renderers",
        schema_path=REPO_ROOT / "schema" / "renderer.schema.json",
        data_root=renderer_data,
    )
    result = device_service.create_instance(
        devices=devices,
        renderers=renderers,
        data_root=device_data,
        instance_id="hallway",
        kind_id="pi_png_client",
        name="Hallway",
        panel_overrides={"w": 1600, "h": 1200},
    )
    if not result.ok:
        raise RuntimeError(f"could not seed device: {result.error}")


# ----- The flow -------------------------------------------------------


async def drive_dashboard(page: Any, base_url: str, cursor: CursorDriver) -> None:
    # ---- /login ----------------------------------------------------
    # ``?next=/pages`` parks us on the Dashboards list straight after
    # signing in, the default redirect target is /send, which isn't
    # what this scenario is about.
    await page.goto(f"{base_url}/login?next=/pages")
    await page.wait_for_load_state("domcontentloaded")
    await cursor.park_centre()
    await asyncio.sleep(HOLD_LOGIN)
    await cursor.type_into(page.locator("input[name=password]"), "demo1234")
    await asyncio.sleep(BEAT_S)
    await cursor.click(page.locator("button[type=submit]").first)

    # ---- /pages (Dashboards list, empty) ---------------------------
    await page.wait_for_url(lambda url: url.rstrip("/").endswith("/pages"), timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_LIST)
    await cursor.type_into(page.locator("input[name=name]"), "Hallway")
    await asyncio.sleep(BEAT_S)
    await cursor.click(page.locator(".dashboard-create-btn"))

    # ---- /pages/<id> editor, bind the device, pick a layout -------
    await page.wait_for_url(
        lambda url: "/pages/" in url and not url.endswith("/pages"),
        timeout=10_000,
    )
    await cursor.park_centre()
    await asyncio.sleep(HOLD_EDITOR_INTRO)

    # Tick the Hallway device. The native checkbox is hidden under a
    # styled <label>, clicking the label triggers the input via the
    # browser's built-in label-for-input wiring AND lets the label's
    # own focus/hover styling animate in the recording, which clicking
    # the bare input wouldn't.
    await cursor.click(page.locator('label.device-check:has(input[value="hallway"])'))
    # data-reload-on-change reloads the editor after the check; wait
    # for the editor grid to re-appear before continuing.
    await page.wait_for_selector(".editor-grid", state="visible", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(READ_PAUSE_S)

    # Apply the 2-columns preset. The smooth scroll in ``focus_on``
    # centres the button in the viewport so the sticky topbar isn't
    # in the way, no force-click needed.
    await cursor.click(
        page.locator('button.layout-card[aria-label="Apply 2 columns"]'),
    )
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_AFTER_LAYOUT)

    # ---- Fill cell 1 with Weather, Now -----------------------------
    cells = page.locator("section.cell-card")
    cell1_id = await cells.nth(0).get_attribute("data-cell-id")
    await cursor.select_visibly(
        cells.nth(0).locator(f"#cell-{cell1_id}-plugin"),
        "weather_now",
        hold_s=HOLD_CELL_PICK,
    )
    # The plugin select has data-reload-on-change, wait for the editor
    # to re-render with the chosen widget's option fields visible.
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_AFTER_WIDGET)

    # ---- Fill cell 2 with Clock, Word ------------------------------
    cells = page.locator("section.cell-card")
    cell2_id = await cells.nth(1).get_attribute("data-cell-id")
    await cursor.select_visibly(
        cells.nth(1).locator(f"#cell-{cell2_id}-plugin"),
        "clock_word",
        hold_s=HOLD_CELL_PICK,
    )
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_AFTER_WIDGET)

    # ---- Open Custom layout, split the right cell horizontally ------
    # The "Custom layout" disclosure exposes a drag-resize / insert-cell
    # editor underneath the preset grid. We open it, hover the right
    # cell in the board to reveal its outer insert buttons, then click
    # the bottom-edge insert to split the right column into top + bottom.
    await cursor.click(page.locator("details.custom-layout > summary"))
    # The board re-uses the same cells data; wait for its DOM nodes to
    # render before reaching for them.
    await page.wait_for_selector("[data-layout-board] .le-cell", state="visible", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_CUSTOM_OPEN)

    # Cell index 1 in the board = the right column (cells are ordered
    # left-to-right, top-to-bottom). The insert buttons are hover-
    # revealed via CSS, so we hover the cell first to bring them into
    # the layout, then click the bottom edge to split it horizontally.
    right_board_cell = page.locator("[data-layout-board] .le-cell").nth(1)
    await right_board_cell.hover()
    await asyncio.sleep(0.3)
    await cursor.click(right_board_cell.locator(".le-insert--bottom"))
    # The insert posts to /pages/<id>/cells/batch in-place, no nav, so
    # wait for the board's DOM to reflect three cells before continuing.
    await page.wait_for_function(
        "() => document.querySelectorAll('[data-layout-board] .le-cell').length === 3",
        timeout=10_000,
    )
    await page.wait_for_selector(
        "section.cell-card:nth-of-type(3)", state="visible", timeout=10_000
    )
    await cursor.park_centre()
    await asyncio.sleep(HOLD_AFTER_INSERT)

    # ---- Fill the new bottom-right cell -----------------------------
    cells = page.locator("section.cell-card")
    cell3_id = await cells.nth(2).get_attribute("data-cell-id")
    await cursor.select_visibly(
        cells.nth(2).locator(f"#cell-{cell3_id}-plugin"),
        "clock_analog",
        hold_s=HOLD_CELL_PICK,
    )
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_AFTER_WIDGET)

    # ---- Hold on the live preview, the payoff frame ---------------
    # The preview card renders server-side as cells change, so by now
    # it shows the composed dashboard. Glide the cursor onto it so the
    # viewer's eye lands there too.
    preview = page.locator(".preview-card")
    if await preview.count() > 0:
        await cursor.focus_on(preview.first, hold_s=0.5)
    await asyncio.sleep(HOLD_PREVIEW)
    await asyncio.sleep(HOLD_DONE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_cli_args(parser)
    args = parser.parse_args()
    run_scenario(
        scenario_name="dashboard",
        prepare=prepare_onboarded_state,
        drive=drive_dashboard,
        args=args,
    )


if __name__ == "__main__":
    main()
