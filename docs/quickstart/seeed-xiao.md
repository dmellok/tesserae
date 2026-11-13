# Quickstart: Seeed XIAO 7.5" ePaper Panel (TRMNL BYOS)

Flash your XIAO ESP32 board to drive its 7.5" black-and-white screen. It ships without firmware.

!!! tip "There's a native Tesserae firmware path"
    To use native Tesserae firmware, open [tesserae.ink/flash](https://tesserae.ink/flash) and follow the [unified Seeed guide](seeed-unified.md).

!!! note "Confirmed on real hardware"
    Tested on the TRMNL 7.5" OG DIY Kit with TRMNL firmware. [Open an issue](https://github.com/dmellok/tesserae/issues) if you hit a problem.

## 01 — Flash the XIAO TRMNL firmware

Plug the XIAO ESP32 into your computer over USB. Use the [TRMNL Web Flasher](https://trmnl.com/flash) and pick the **XIAO 7.5"** variant.

After flashing, open the device's Wi-Fi setup page from your phone or laptop. Enter:

- BYOS server URL: `http://<your-server>:8765`
- Your home Wi-Fi name and password.

Save. The XIAO restarts onto your network.

## 02 — It pairs itself

The device registers on its first check-in and appears under **Settings → Devices** within seconds.

## 03 — Compose a dashboard

The panel is 800×480 landscape, black and white. Tesserae turns grey shading into black-and-white dot patterns (dithering).

1. Open **Dashboards → New**.
2. Use bold text, simple icons and 1-bit illustrations with high contrast.
3. Bind the page to the device in the device picker.
4. Click **Push**.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- Turn on **Smart sync** on an **Every N minutes** schedule to match image updates to device wakes.
- Use **Every N minutes** for a fixed interval.

The screen draws quickly. Frequent updates have little effect on battery life.

## Next steps

- [Wake interval](../install/devices.md#dashboard-refresh-settings) and [quiet hours](../install/devices.md#quiet-hours).
- [Browse community widgets](https://tesserae.ink/catalog/) tuned for the 800×480 1-bit canvas.
