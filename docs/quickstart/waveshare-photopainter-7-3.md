# Quickstart: Waveshare 7.3" PhotoPainter (ESP32-S3)

The PhotoPainter uses an ESP32-S3 board and a six-colour Spectra 6 panel. It runs on a battery in its built-in housing and sleeps between updates.

## 01 — Flash the firmware

Open the case. Hold BOOT while connecting the ESP32-S3 to your computer over USB, then release.

Open the [Tesserae browser flasher](https://tesserae.ink/flash) in Chrome or Edge. Select **Waveshare 7.3" PhotoPainter**, then click **Connect & flash**. It installs over USB using Web Serial; no build tools needed.

On first boot, join `Tesserae-Setup` from your phone or laptop. Enter these on the Wi-Fi setup page:

- Tesserae server URL: `http://<your-server>:8765`
- Your home Wi-Fi name and password.

Save. The PhotoPainter restarts onto your network.

## 02 — It pairs itself

The device contacts `/api/v1/device/discover`. Within a few seconds, it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

The 7.3" PhotoPainter panel is 800×480 landscape, Spectra 6 six-colour.

1. Open **Dashboards → New**.
2. Choose a layout for a photo frame or compact display.
3. Bind the page to the device in the device picker.
4. Click **Push**.

A full refresh takes around 20 to 30 seconds. The PhotoPainter downloads and draws the image when it wakes, then sleeps.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- Choose **Daily at a set time** to show one photo per day.
- Turn on **Smart sync** on an **Every N minutes** schedule to get fresh images without wasting battery on idle wakes.

With the case closed and one daily update, the built-in battery lasts several months.

## Next steps

- [Picture widgets](https://tesserae.ink/catalog/) for the PhotoPainter include Immich, Unsplash and Paperlesspaper Art.
- [Quiet hours](../install/devices.md#quiet-hours) overnight.
