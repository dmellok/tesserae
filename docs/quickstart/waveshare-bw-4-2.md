# Quickstart: Waveshare 4.2" B/W (ESP32)

Connect an ESP32 board to this 4.2" black-and-white panel. It runs on a battery and uses little power.

!!! note "Pending real-hardware confirmation"
    The maintainer hasn't tested this model on hardware yet. It uses the same protocol as the other ESP32 quickstarts and should work. [Open an issue](https://github.com/dmellok/tesserae/issues) if it doesn't.

## 01 — Flash the firmware

Plug the ESP32 dev board into your computer over USB while holding the BOOT button, then release.

Build and flash [tesserae-device-esp32-bw](https://github.com/dmellok/tesserae-device-esp32-bw) with PlatformIO (`pio run -t upload`). Follow its README. There are no ready-made firmware downloads or browser flasher yet.

Check [tesserae.ink/flash](https://tesserae.ink/flash) for future browser support.

On first boot, join `Tesserae-Setup` from your phone or laptop. The password is `tesserae`. Enter these on the Wi-Fi setup page:

- MQTT broker URI: your Tesserae server's message broker address. Include credentials if needed.
- Your home Wi-Fi name and password.

Save. The board restarts onto your network.

## 02 — It pairs itself

The board sends a status message over MQTT on `tesserae/<device_id>/status`. Within a few seconds, it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

The panel is 400×300 landscape, with one bit per pixel: black or white. Tesserae converts shades to black-and-white dot patterns (dithering).

1. Open **Dashboards → New**.
2. Use high-contrast text, simple icons and 1-bit illustrations.
3. Bind the page to the board in the device picker.
4. Click **Push**.

The board downloads a 15 KB image when it wakes. Drawing takes a couple of seconds, then the board sleeps.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- Choose **Every N minutes** for a status display, such as a train departure board.
- Turn on **Smart sync** on an **Every N minutes** schedule to save battery through wake timing.

This is one of the fastest and lowest-power panels in the catalog. Frequent updates have little effect on battery life.

## Next steps

- [Wake interval](../install/devices.md#dashboard-refresh-settings) and [quiet hours](../install/devices.md#quiet-hours).
- [Build a 1-bit widget](../dev/writing-a-widget.md) tuned for monochrome panels.
