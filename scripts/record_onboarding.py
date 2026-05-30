"""Record a video of the Tesserae onboarding flow.

Drives Playwright through ``/setup`` → password → ``/onboarding/welcome``
→ broker → device → starter dashboard → telemetry → finish, captures
everything to video (``.webm`` natively, ``.mp4`` via ffmpeg if you
ask for it).

Usage::

    python scripts/record_onboarding.py --output ~/Desktop/onboarding.mp4
    python scripts/record_onboarding.py --output demo.webm

See ``scripts/_recording.py`` for the shared Tesserae lifecycle +
cursor injection + ffmpeg transcode that this scenario rides on top of.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from _recording import CursorDriver, add_common_cli_args, run_scenario

# ----- Per-page hold pacing ------------------------------------------
#
# Scaled to the information density of each wizard step so the cursor
# pauses on info-heavy pages long enough to record voice-over while
# moving briskly through the lighter ones.
HOLD_SETUP = 2.8
HOLD_WELCOME = 2.8
HOLD_BROKER = 3.8
HOLD_DEVICE = 5.5  # id, kind, panel preset, custom w/h, rotation
HOLD_DASHBOARD = 3.8
HOLD_TELEMETRY = 6.5  # full consent block, what gets sent, what doesn't
HOLD_DONE = 1.0
READ_PAUSE_S = 1.4
BEAT_S = 0.5


async def drive_onboarding(page: Any, base_url: str, cursor: CursorDriver) -> None:
    # ---- /setup -----------------------------------------------------
    await page.goto(f"{base_url}/setup")
    await page.wait_for_load_state("domcontentloaded")
    await cursor.park_centre()
    await asyncio.sleep(HOLD_SETUP)
    await cursor.type_into(page.locator("#password"), "demo1234")
    await cursor.type_into(page.locator("#password_confirm"), "demo1234")
    await asyncio.sleep(BEAT_S)
    await cursor.click(page.locator("button[type=submit]").first)

    # ---- /onboarding/welcome ---------------------------------------
    await page.wait_for_url("**/onboarding/welcome", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_WELCOME)
    await cursor.click(page.get_by_role("link", name="Get started"))

    # ---- /onboarding/broker (built-in pre-checked) -----------------
    await page.wait_for_url("**/onboarding/broker", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_BROKER)
    await cursor.click(page.get_by_role("button", name="Save & continue"))

    # ---- /onboarding/device ----------------------------------------
    await page.wait_for_url("**/onboarding/device", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_DEVICE)
    manual = page.locator("details.wizard-manual")
    if await manual.count() > 0 and not await manual.first.evaluate("el => el.open"):
        await cursor.click(manual.first.locator("summary"))
        await asyncio.sleep(BEAT_S)
    await cursor.type_into(page.locator("#dev-id"), "hallway")
    await asyncio.sleep(BEAT_S)
    await cursor.click(page.get_by_role("button", name="Add device"))
    await asyncio.sleep(READ_PAUSE_S)
    await cursor.park_centre()
    await cursor.click(page.get_by_role("link", name="Next"))

    # ---- /onboarding/dashboard -------------------------------------
    await page.wait_for_url("**/onboarding/dashboard", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_DASHBOARD)
    if await page.get_by_role("button", name="Create a starter dashboard").count() > 0:
        await cursor.click(page.get_by_role("button", name="Create a starter dashboard"))
        await page.wait_for_url("**/onboarding/dashboard", timeout=10_000)
        await cursor.park_centre()
        await asyncio.sleep(HOLD_DASHBOARD)
    await cursor.click(page.get_by_role("link", name="Next"))

    # ---- /onboarding/telemetry -------------------------------------
    await page.wait_for_url("**/onboarding/telemetry", timeout=10_000)
    await cursor.park_centre()
    await asyncio.sleep(HOLD_TELEMETRY)
    await cursor.click(page.get_by_role("button", name="Finish"))

    # ---- landed on the home page ------------------------------------
    await page.wait_for_load_state("networkidle", timeout=10_000)
    await asyncio.sleep(HOLD_DONE)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_cli_args(parser)
    args = parser.parse_args()
    run_scenario(
        scenario_name="onboarding",
        prepare=None,  # /setup itself bootstraps the install
        drive=drive_onboarding,
        args=args,
    )


if __name__ == "__main__":
    main()
