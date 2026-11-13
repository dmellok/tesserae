# Quickstart: Seeed ePaper Devices (unified firmware)

Use native Tesserae firmware for supported Seeed panels. Setup takes about ten minutes.

!!! tip "Prefer TRMNL BYOS firmware?"
    To keep TRMNL firmware and use Tesserae as its server, follow:

    - [Seeed reTerminal E-Series (TRMNL BYOS)](seeed-reterminal.md)
    - [Seeed XIAO 7.5" (TRMNL BYOS)](seeed-xiao.md)

## Which device do you have?

| Device | Panel | Colour |
|---|---|---|
| Seeed reTerminal E1001 | 7.5", 800×480 | Mono B/W |
| Seeed reTerminal E1002 | 7.3", 800×480 | 6-colour Spectra 6 |
| Seeed reTerminal E1003 | 10.3", 1872×1404 | Mono + 16-level greyscale |
| Seeed reTerminal E1004 | 13.3", 1200×1600 | 6-colour Spectra 6 |
| Seeed XIAO EE02 | 13.3", 1200×1600 | 6-colour Spectra 6 |
| Seeed XIAO EE03 | 10.3", 1872×1404 | Mono + 16-level greyscale |
| Seeed XIAO 7.5" ePaper Panel (C3, integrated) | 7.5", 800×480 | Mono B/W |
| Seeed XIAO ePaper 7.5" (S3, TRMNL OG DIY Kit) | 7.5", 800×480 | Mono B/W |

!!! warning "Two different XIAO 7.5-inch products"
    These use identical images but need different firmware and updates:

    | Product | Board |
    |---|---|
    | XIAO 7.5" ePaper Panel | Built-in ESP32-C3 |
    | TRMNL 7.5" (OG) DIY Kit | XIAO ESP32-S3 Plus with a separate panel |

    Flash and register the one matching your board (each board's kind id is in [Screens & compatibility](../compatibility.md)). If you chose wrong, flash the correct firmware and reconnect. Tesserae changes the kind but keeps the device id, token and history.

## 01 — Flash the Tesserae firmware

Use Chrome, Edge or another Chromium-based browser to flash firmware over USB. Firefox and Safari don't support the required Web Serial API.

!!! warning "macOS users: install the WCH CH340 driver first"
    All four reTerminal E-series models use a WCH CH340 USB connection. macOS needs a driver before the flasher can see them.

    - Install the [WCH CH34x DriverKit driver](https://github.com/WCHSoftGroup/ch34xser_macos) (`WCHSoftGroup/ch34xser_macos`).
    - Enable it under **System Settings → General → Login Items & Extensions → Driver Extensions**. Until then, it says "activated waiting for user".
    - The port appears as `/dev/cu.wchusbserial*`.

    Linux and Windows already have the driver. XIAO boards use a different USB connection that works on macOS without it.

1. Connect the device over USB. Use a data cable and a data-capable port. Some desktop front-panel ports only supply power.
2. Open **[tesserae.ink/flash](https://tesserae.ink/flash)** in Chrome or Edge.
3. Under **Choose your panel**, select your device from **Device**. Only boards with a firmware image appear. If yours is missing, check the table above.
4. Click **Next: plug it in**. Under **Flash it over USB**, click **Connect & flash** and choose the device's serial port.
5. Wait 60-90 seconds. The screen goes blank and resets when done. Keep USB connected until the flasher confirms success.

!!! note "USB serial permissions"
    | System | Permission needed |
    |---|---|
    | macOS | Allow the browser's one-time prompt. |
    | Linux | If the port is missing, add yourself to `dialout`, then log out and back in. |
    | Windows | The driver installs automatically on first connection. |

## 02 — Connect to Wi-Fi

The **Connect it** step at [tesserae.ink/flash](https://tesserae.ink/flash) sends Wi-Fi settings and the server URL over USB. If you used it, skip the hotspot steps.

Otherwise, join the device's open Wi-Fi network, `Tesserae-Setup`:

1. Join from your phone or laptop. Stay connected even if it warns that there's no internet.
2. The setup page opens automatically. If it doesn't, open `http://192.168.4.1/`.
3. Enter:
   - Wi-Fi SSID: your home network's name.
   - Wi-Fi password: your home network's password.
   - Tesserae server URL: `http://<your-server>:8765`, the address you use to open Tesserae.
4. Save. The device restarts on your home Wi-Fi.

If your server advertises `tesserae.local` on the network, use `http://tesserae.local:8765`. Otherwise use its local IP address.

## 03 — Register the device in Tesserae

The device checks in every 30 seconds. Within a minute of restarting, it appears under **Settings → Devices → Discovered**. Tesserae detects the kind from its firmware.

1. Open **Settings → Devices → Discovered**.
2. Click **Register**. Optionally give the device a name.
3. It gets its access token on the next `/discover` check, within 30 seconds, and moves to **Devices**.

You don't need to type a token or use SSH.

## 04 — Compose a dashboard and push

In the editor:

1. Open **Dashboards → New**.
2. Choose a layout for your panel:

    | Panel | Works well with |
    |---|---|
    | E1001 and both XIAO 7.5" versions | Bold, high-contrast text |
    | E1003 | Muted photos and shaded gradients |
    | E1002, E1004 and EE02 | Weather widgets with separate colour blocks |

3. Bind the page to your device in the picker on the editor's right.
4. Click **Push**. The device downloads the image on its next check-in, draws it, then sleeps.

Set the refresh interval on the dashboard's **Schedules** card. For battery devices, turn on **Smart sync** to draw each image just before the device wakes.

## Per-device notes

| Panel | What to expect |
|---|---|
| E1002 | Six inks: black, white, yellow, red, blue, green. Refresh takes about 30 seconds; this is normal. |
| E1003 | Photos use 16 grey levels instead of 1-bit dots. Rotate to portrait, 1872 pixels tall, for lists. |
| E1004 | Same colours as E1002, with a larger screen. Refresh takes roughly 40 seconds. Seeed advertises up to 6 months per charge at one refresh daily. |
| XIAO EE02 | Same image format as E1004. Smaller, cheaper driver board without the reTerminal case. |
| XIAO EE03 | Same panel and image format as E1003. No reTerminal case or touch overlay. |
| XIAO 7.5" ePaper Panel (C3) | Same image format as E1001. No battery reporting or user buttons, only Boot and Reset. No page-turn bindings. Needs device-firmware v1.16.0 or later. |
| XIAO ePaper 7.5" (S3) | Same image format as E1001 and C3. A separate ESP32-S3 Plus board, sold as the TRMNL 7.5" (OG) DIY Kit. The [TRMNL OG quickstart](trmnl.md) also works for it. |

All four reTerminal models and XIAO EE02 report battery level. The C3 can't measure its battery because it has no connection to a voltage-measuring pin.

Tesserae treats this as unknown, not empty. It shows no battery indicator, history chart or low-battery overlay.

## What if it didn't work?

| Symptom | Likely cause + fix |
|---|---|
| Not in Discovered after 2 minutes | Check Wi-Fi settings and that the device left setup mode. From another computer on the same LAN, try `curl http://<server>:8765/healthz`. |
| Wrong kind, such as `esp32_client` instead of `seeed_reterminal_e1002` | Update firmware from tesserae.ink/flash to match the hardware catalog. |
| Stripes or garbage on screen | Check the frame size. Tesserae must be v0.64.60 or later. Find the version under Settings → Devices → your device → Troubleshooting → Diagnostics. |
| Flasher won't connect | Use Chrome or Edge; Firefox and Safari don't support Web Serial. Check the USB cable and port support data. |
| No macOS serial port for a reTerminal | Install and enable [`WCHSoftGroup/ch34xser_macos`](https://github.com/WCHSoftGroup/ch34xser_macos) under **System Settings → General → Login Items & Extensions → Driver Extensions**. |
| Setup page at `http://192.168.4.1/` won't load on the phone | Try another browser. Brave on Android can fail with `ERR_TOO_MANY_RETRIES` where Chrome on the same phone loads the page. |
| Stuck in Wi-Fi setup mode | Rejoin the setup network and enter Wi-Fi settings again. Some setup pages need an explicit Save tap. |
| Image downloads but won't draw | The battery may be too low to power a refresh. Charge over USB for an hour, then retry. |

## Next steps

- [Set up multiple panels on one server](../install/devices.md#multiple-panels).
- [Add a rotation](../install/devices.md#compose-a-dashboard).
- [Bind the front buttons](../install/buttons.md) under **Controls → Button map**. Defaults: `left → previous`, `right → next`, `refresh → re-render`. Override them there to change pages or force a refresh.
- [Browse community widgets](https://tesserae.ink/catalog/).
- [Firmware source](https://github.com/dmellok/tesserae-device-firmware).
