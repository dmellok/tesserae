# Quickstart: Waveshare 13.3" Spectra 6 (ESP32-S3)

The Waveshare 13.3" Spectra 6 e-paper board with an ESP32-S3-WROOM-2 driver. Battery-powered, deep-sleep between renders, the maintainer's primary daily driver.

## 01 — Flash the firmware

Plug the ESP32-S3-WROOM-2 board into your computer over USB. Hold the BOOT button while plugging in, then release.

Open the [Tesserae browser flasher](https://tesserae.ink/flash) in Chrome or Edge, pick **Waveshare 13.3" Spectra 6** in the device dropdown, then click **Verify & flash**. It installs the firmware over Web Serial with no toolchain.

On first boot the board opens a Wi-Fi captive portal (SSID `tesserae-esp32-XXXX`). Connect from your phone or laptop, then set:

- **Tesserae server URL**: `http://<your-server>:8765`
- **Wi-Fi SSID and password**: your home network.

Save. The board restarts onto your network.

## 02 — It pairs itself

The board calls Tesserae's `/api/setup` with its MAC and auto-provisions. Within a few seconds it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

The 13.3" Spectra 6 panel is firmware-native portrait at 1200×1600 (the firmware reads that stride regardless of how you mount the panel). The composer handles the rotation upstream, so build your layout however the panel hangs on the wall.

1. **Dashboards → New**.
2. Pick a layout preset that suits the Spectra 6 six-colour palette.
3. Bind the page to the board in the device picker.
4. Hit **Push**.

The board wakes, fetches the frame, paints it (around 30 seconds for a full Spectra 6 refresh), and goes back to deep sleep.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- **Every N minutes** or **Daily at a set time** for fixed cadences.
- **Smart sync** to render just before each wake, so the panel always shows a fresh frame without burning battery on idle renders.

Smart sync plus a multi-hour wake interval is what gets multi-month battery life on a single LiPo.

## Next steps

- [Quiet hours](../install/devices.md#per-device-settings) to skip overnight renders.
- [Battery monitoring](../install/devices.md#per-device-settings): the board reports voltage on each wake; Tesserae shows the history.
- [Pair a second board](../install/devices.md#multiple-panels) to drive two Waveshare panels at once.
