# Quickstart: Waveshare 4.2" B/W (ESP32)

A Waveshare 4.2 inch monochrome e-paper module driven by an ESP32 dev board. Battery-powered, 400×300 1-bpp panel; sips power because the frame is mono.

!!! note "Pending real-hardware confirmation"
    This SKU is in the catalog but hasn't been verified end-to-end by the maintainer yet. The protocol path matches the other ESP32 quickstarts and should work; please [open an issue](https://github.com/dmellok/tesserae/issues) if you hit anything unexpected.

## 01 — Flash the firmware

Plug the ESP32 dev board into your computer over USB while holding the BOOT button, then release.

There isn't a browser flasher for this SKU yet, and the firmware repo doesn't cut binary releases. Clone [tesserae-device-esp32-bw](https://github.com/dmellok/tesserae-device-esp32-bw) and follow the README to build + flash with ESP-IDF (`idf.py flash`). Track [tesserae.ink/flash](https://tesserae.ink/flash) for browser-flash support if it lands.

On first boot the board opens a Wi-Fi captive portal (SSID `tesserae-esp32-bw-XXXX`). Connect from your phone or laptop, then set:

- **Tesserae server URL**: `http://<your-server>:8765`
- **Wi-Fi SSID and password**: your home network.

Save. The board restarts onto your network.

## 02 — It pairs itself

The board calls Tesserae's `/api/setup` with its MAC and auto-provisions. Within a few seconds it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

The 4.2" B/W panel is 400×300 landscape, 1-bit-per-pixel monochrome. Server-side dithering handles the rest.

1. **Dashboards → New**.
2. Pick a layout that suits the small canvas; high-contrast text, simple icons, and 1-bit illustrations work best.
3. Bind the page to the board in the device picker.
4. Hit **Push**.

The board wakes, fetches a tiny 15 KB packed buffer, paints (a couple of seconds for a 1-bpp refresh), and sleeps.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- **Every N minutes** for status displays (next train, kitchen timer, meeting countdown).
- **Smart sync** for battery-aware wake timing.

The 1-bpp 4.2" panel is one of the fastest-painting and lowest-power options in the catalog; cadence can be aggressive without hurting battery life.

## Next steps

- [Per-device settings](../install/devices.md#per-device-settings) for the wake interval and quiet hours.
- [Build a 1-bit widget](../dev/writing-a-widget.md) tuned for monochrome panels.
