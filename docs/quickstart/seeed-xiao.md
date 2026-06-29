# Quickstart: Seeed XIAO 7.5" ePaper Panel

The Seeed XIAO ePaper Panel: an XIAO ESP32 board paired with a 7.5 inch monochrome e-paper panel. Ships unflashed; the XIAO variant of the TRMNL firmware turns it into a BYOS client that Tesserae can paint.

!!! note "Pending real-hardware confirmation"
    This SKU is in the catalog but hasn't been verified end-to-end by the maintainer yet. The protocol path matches the reTerminal flow and should work; please [open an issue](https://github.com/dmellok/tesserae/issues) if you hit anything unexpected.

## 01 — Flash the XIAO TRMNL firmware

Plug the XIAO ESP32 into your computer over USB. Use the [TRMNL Web Flasher](https://trmnl.com/flash) and pick the **XIAO 7.5"** variant.

After flashing, the device opens a Wi-Fi captive portal. Connect from your phone or laptop, then set:

- **BYOS server URL**: `http://<your-server>:8765`
- **Wi-Fi SSID and password**: your home network.

Save. The XIAO restarts onto your network.

## 02 — It pairs itself

The XIAO firmware sends its MAC in the `Id` header (uppercase, colon-separated). Tesserae matches case-insensitively and auto-provisions on first poll. The device appears under **Settings → Devices** within seconds.

## 03 — Compose a dashboard

The XIAO 7.5" panel is 800×480 landscape mono. Server-side dithering handles the greyscale-to-1-bit conversion.

1. **Dashboards → New**.
2. Pick a layout that suits high-contrast monochrome: bold type, simple icons, 1-bit illustrations.
3. Bind the page to the device in the device picker.
4. Hit **Push**.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- **Smart sync** for battery-aware wake timing.
- **Every N minutes** if you want a fixed cadence.

The 7.5" mono panel paints quickly; cadence can be reasonably aggressive without dragging on battery life.

## Next steps

- [Per-device settings](../install/devices.md#per-device-settings) for the wake interval and quiet hours.
- [Browse community widgets](../widgets/community.md) tuned for the 800×480 1-bit canvas.
