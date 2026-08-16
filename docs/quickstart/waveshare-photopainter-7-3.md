# Quickstart: Waveshare 7.3" PhotoPainter (ESP32-S3)

The Waveshare 7.3" PhotoPainter, an ESP32-S3 with a 6-colour Spectra 6 panel and an integrated battery housing. Battery-powered, deep-sleep between renders.

## 01 — Flash the firmware

Open the PhotoPainter's case, plug the ESP32-S3 into your computer over USB while holding the BOOT button, then release.

Open the [Tesserae browser flasher](https://tesserae.ink/flash) in Chrome or Edge, pick **Waveshare 7.3" PhotoPainter** in the device dropdown, then click **Verify & flash**. It installs the firmware over Web Serial with no toolchain.

On first boot the PhotoPainter opens a Wi-Fi captive portal (SSID `tesserae-photopainter-XXXX`). Connect from your phone or laptop, then set:

- **Tesserae server URL**: `http://<your-server>:8765`
- **Wi-Fi SSID and password**: your home network.

Save. The PhotoPainter restarts onto your network.

## 02 — It pairs itself

The PhotoPainter calls Tesserae's `/api/setup` with its MAC and auto-provisions. Within a few seconds it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

The 7.3" PhotoPainter panel is 800×480 landscape, Spectra 6 six-colour.

1. **Dashboards → New**.
2. Pick a layout that suits the smaller canvas. The PhotoPainter shines as a photo frame or compact ambient surface.
3. Bind the page to the device in the device picker.
4. Hit **Push**.

A full Spectra 6 refresh takes around 20 to 30 seconds; the PhotoPainter wakes, fetches, paints, sleeps.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- **Daily at a set time** is the natural fit for a photo frame, one frame per day.
- **Smart sync** keeps the PhotoPainter painting a fresh frame without burning battery on idle wakes.

Closed-case battery life with a daily cadence is several months on the integrated cell.

## Next steps

- [Picture widgets](https://tesserae.ink/catalog/): the catalog has Immich, Unsplash, and Paperlesspaper Art widgets that fit the PhotoPainter form factor.
- [Quiet hours](../install/devices.md#per-device-settings) overnight.
