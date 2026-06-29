# Quickstart: Pi Pico Plus 2 W + Pimoroni Inky 13.3"

A Pimoroni Pico Plus 2 W (RP2350) driving a Pimoroni Inky Impression 13.3" panel. Battery-powered, deep-sleep between renders, runs for months on a small LiPo.

!!! note "Different from the Pi path"
    The Pi quickstart uses a Linux Raspberry Pi running Python. This quickstart uses a Pi *Pico*, which is a microcontroller running C firmware. Different boards, different setup; same Tesserae server on the other side.

## 01 — Flash the Pico

Plug the Pico Plus 2 W into your computer over USB while holding the BOOTSEL button. It mounts as a USB drive named `RP2350`.

Download the latest UF2 from the [tesserae-device-pico-bin releases](https://github.com/dmellok/tesserae-device-pico-bin/releases) and drag it onto the mounted drive. The Pico reboots and unmounts within a second.

On first boot it opens a Wi-Fi captive portal (SSID `tesserae-pico-XXXX`). Connect to it from your phone or laptop, then set:

- **Tesserae server URL**: `http://<your-server>:8765`
- **Wi-Fi SSID and password**: your home network.

Save and the Pico restarts onto your network.

## 02 — It pairs itself

The Pico calls Tesserae's `/api/setup` endpoint with its MAC and auto-provisions. Within a few seconds it appears under **Settings → Devices → Discovered**. Click **Register** to confirm the pairing.

## 03 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. The Pico drives a 13.3" Spectra 6 at 1600×1200 landscape; build a layout that suits the panel's six-colour palette.
3. Bind the page to the Pico in the device picker on the right.
4. Hit **Push**.

The Pico wakes, fetches the frame, paints it, and goes back to deep sleep.

## 04 — Set the refresh

Open the dashboard's **Schedules** card and pick:

- **Every N minutes** if you want a regular cadence.
- **Daily at a set time** for once-a-day refreshes.
- **Smart sync** to render *just before* each wake, so the panel always paints the freshest possible frame without burning battery on idle renders.

Smart sync is what gets the multi-month battery life. The Pico's wake cycle stays short and the server only renders when it's actually about to be needed.

## Next steps

- [Tune the wake interval](../install/devices.md#per-device-settings) for the battery vs freshness tradeoff you want.
- [Quiet hours](../install/devices.md#per-device-settings) to skip renders overnight.
- [Build your own widget](../dev/writing-a-widget.md) for the panel.
