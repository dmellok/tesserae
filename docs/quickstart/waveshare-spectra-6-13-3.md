# Quickstart: Waveshare 13.3" Spectra 6 (ESP32-S3)

Use the ESP32-S3-WROOM-2 board to drive this battery-powered panel. It sleeps between updates. This is the maintainer's everyday display.

## 01 — Flash the firmware

Plug the ESP32-S3-WROOM-2 board into your computer over USB. Hold the BOOT button while plugging in, then release.

Open the [Tesserae browser flasher](https://tesserae.ink/flash) in Chrome or Edge. Select **Waveshare 13.3" Spectra 6**, then click **Connect & flash**. It installs over USB using Web Serial; no build tools needed.

On first boot, join `Tesserae-Setup` from your phone or laptop. Enter these on the Wi-Fi setup page:

- Tesserae server URL: `http://<your-server>:8765`
- Your home Wi-Fi name and password.

Save. The board restarts onto your network.

## 02 — It pairs itself

The device contacts `/api/v1/device/discover`. Within a few seconds, it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

The panel is 1200×1600 in portrait. The editor rotates the image for you, so design for how you've mounted it.

1. **Dashboards → New**.
2. Pick a layout preset that suits the Spectra 6 six-colour palette.
3. Bind the page to the board in the device picker.
4. Hit **Push**.

When it wakes, the board downloads and draws the image, then sleeps. A full Spectra 6 refresh takes around 30 seconds.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- Choose **Every N minutes** or **Daily at a set time** for regular updates.
- Turn on **Smart sync** on an **Every N minutes** schedule to prepare each image just before the board wakes. This saves battery and idle work.

With smart sync and several hours between wakes, one LiPo battery lasts several months.

## Next steps

- [Quiet hours](../install/devices.md#quiet-hours) to skip overnight renders.
- Battery monitoring: the board reports voltage on each wake, and Tesserae shows the history on the **Device batteries** page.
- [Pair a second board](../install/devices.md#multiple-panels) to drive two Waveshare panels at once.
