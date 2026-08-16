# Quickstart: Seeed ePaper Devices (unified firmware)

The recommended path for every Seeed ePaper device Tesserae supports. One firmware image, one web flasher, one Wi-Fi captive-portal flow, one Tesserae registration. About ten minutes from opening the box to the panel painting a dashboard.

Covers the reTerminal E-series (E1001 / E1002 / E1003 / E1004) plus the XIAO ePaper family (XIAO EE02 for the 13.3" Spectra 6 kit, and the XIAO 7.5" ePaper Panel which is Seeed's TRMNL OG-compatible kit).

!!! tip "Prefer TRMNL BYOS firmware?"
    If you'd rather stay on TRMNL's firmware and use Tesserae as a BYOS server, follow the older [Seeed reTerminal E-Series (TRMNL BYOS)](seeed-reterminal.md) or [Seeed XIAO 7.5" (TRMNL BYOS)](seeed-xiao.md) guide. This page is for the native Tesserae firmware path.

## Which device do you have?

| Device | Panel | Colour | Format | Firmware kind |
|---|---|---|---|---|
| Seeed reTerminal E1001 | 7.5", 800×480 | Mono B/W | Raw 1-bpp bin (48000 bytes) | `seeed_reterminal_e1001` |
| Seeed reTerminal E1002 | 7.3", 800×480 | 6-colour Spectra 6 | Raw 4-bpp bin (192000 bytes) | `seeed_reterminal_e1002` |
| Seeed reTerminal E1003 | 10.3", 1872×1404 | Mono + 16-level greyscale | Raw 4-bpp grayscale bin (1314144 bytes) | `seeed_reterminal_e1003` |
| Seeed reTerminal E1004 | 13.3", 1200×1600 | 6-colour Spectra 6 | Raw 4-bpp bin (960000 bytes) | `seeed_reterminal_e1004` |
| Seeed XIAO EE02 | 13.3", 1200×1600 | 6-colour Spectra 6 | Raw 4-bpp bin (960000 bytes) | `seeed_ee02` |
| Seeed XIAO 7.5" ePaper Panel (Seeed's TRMNL OG kit) | 7.5", 800×480 | Mono B/W | Raw 1-bpp bin (48000 bytes) | `xiao_epaper_75` |

Pick your device, then keep reading. Every device follows the same four steps.

## 01 — Flash the Tesserae firmware

Tesserae ships a **web-based firmware flasher**. Chrome / Edge / Chromium-based browsers only; Firefox and Safari don't yet expose the Web Serial API the flasher needs.

!!! warning "macOS users: install the WCH CH340 driver first"
    The reTerminal E1001 / E1002 / E1003 / E1004 flash through an on-board **WCH CH340** USB-serial bridge. macOS doesn't ship a driver for it, so without one the flasher can't see the device on the serial port list.

    - Install the [WCH CH34x DriverKit driver](https://github.com/WCHSoftGroup/ch34xser_macos) (`WCHSoftGroup/ch34xser_macos`).
    - After install, enable it under **System Settings → General → Login Items & Extensions → Driver Extensions**. It'll sit in "activated waiting for user" until you toggle it on.
    - The port then appears as `/dev/cu.wchusbserial*` and the flasher can see it.

    Linux and Windows have the driver in-tree; no action needed there. The XIAO boards use a different USB bridge that macOS handles natively.

1. Plug the device into your computer over USB. Use a real data cable, not a charge-only cable, and make sure the port supports data (front-panel USB on a desktop tower sometimes doesn't).
2. Open **[tesserae.ink/flash](https://tesserae.ink/flash)** in Chrome or Edge.
3. Select your device from the list. If your device isn't there, check the "Which device do you have?" table above; the flasher shows only devices that have a matching firmware image built.
4. Click **Connect**, pick the serial port for your device, and click **Install**.
5. Wait 60-90 seconds for the flash to complete. The panel goes blank + resets automatically at the end; leave the USB cable plugged in until the flasher confirms success.

!!! note "USB serial permissions"
    On macOS the browser prompts once; grant it. On Linux you may need to add yourself to the `dialout` group and log out / back in if the flasher can't see the serial port. On Windows the driver installs automatically the first time you plug in the device.

## 02 — Connect to Wi-Fi

After flashing, the device boots into a **soft-AP setup mode** and broadcasts an open Wi-Fi network named `tesserae-<macsuffix>` (e.g. `tesserae-a4b2c9`).

1. On your phone or laptop, join that Wi-Fi network. Your OS may complain that there's no internet on this network; ignore it and stay connected.
2. A captive portal loads automatically. If it doesn't, browse to `http://192.168.4.1/`.
3. Fill in:
   - **Wi-Fi SSID**: your home network.
   - **Wi-Fi password**: your home network's password.
   - **Tesserae server URL**: `http://<your-server>:8765` (whatever URL you'd type in a browser to reach the web UI).
4. Save. The device restarts onto your home Wi-Fi.

If your Tesserae server publishes `tesserae.local` via mDNS, you can enter `http://tesserae.local:8765`. Otherwise use its LAN IP.

## 03 — Register the device in Tesserae

The device now polls Tesserae's REST discover endpoint every 30 seconds. Within a minute of restart you'll see it in **Settings → Devices → Discovered** with the correct kind (E1001, E1002, EE02, etc.) auto-detected from what the firmware reports.

1. Open Tesserae's web UI: **Settings → Devices → Discovered**.
2. Click **Register** on the new device. Optionally rename it to something you'll recognise ("kitchen", "hallway", "bedside").
3. The device picks up its access token on the next `/discover` poll (within 30 seconds) and moves from **Discovered** to the regular **Devices** list.

No token to type by hand. No SSH. The device is now authenticated and ready to receive frames.

## 04 — Compose a dashboard and push

In the editor:

1. **Dashboards → New**.
2. Build a layout that suits the panel. The mono devices (E1001 / XIAO 7.5") want high-contrast bold typography; the greyscale E1003 handles muted photos and dithered gradients well; the Spectra 6 devices (E1002 / E1004 / EE02) suit weather widgets with dedicated colour blocks per element.
3. Bind the page to your device in the device picker on the right side of the composer.
4. Hit **Push**. The device wakes on its next poll, downloads the pre-rendered frame from Tesserae, paints, and sleeps.

Refresh cadence is set per-dashboard on the **Schedules** card. **Smart sync** is the natural fit for battery devices; it renders just before each device wake so the panel always paints a fresh frame without burning battery on idle renders.

## Per-device notes

Some panels have quirks worth knowing about.

- **E1002 (Spectra 6, 800×480):** Tesserae quantises to the panel's 6-colour palette server-side (black, white, yellow, red, blue, green). The panel's full refresh takes about 30 seconds; the Spectra process is what's slow, not Tesserae or the firmware. That's normal panel physics, not a bug.
- **E1003 (mono greyscale, 10.3"):** the panel supports 16 levels of grey via its IT8951 driver. Tesserae's greyscale renderer ships photos as real gradients rather than 1-bit dither. Portrait aspect (1872 tall) suits list-shaped dashboards.
- **E1004 (Spectra 6, 13.3"):** same colour behaviour as E1002 but at a much larger canvas. Refresh cycle is roughly 40 seconds. Seeed advertises up to 6 months of battery life at one refresh per day.
- **XIAO EE02:** frame format is byte-identical to the E1004 (both are 13.3" Spectra 6 at 1200×1600). The XIAO driver board is smaller and cheaper; useful when you want the panel without the reTerminal case.
- **Seeed XIAO 7.5" ePaper Panel:** this is Seeed's TRMNL OG kit — same panel, same board topology, sold through Seeed's channel. Frame format is byte-identical to the E1001. If you already followed the [TRMNL OG quickstart](trmnl.md), it also applies here; this unified flow is the alternative if you'd rather run the native Tesserae firmware.
- **Battery reporting:** the reTerminal E1001 / E1002 / E1003 / E1004 and the XIAO boards all report battery correctly.

## What if it didn't work?

| Symptom | Likely cause + fix |
|---|---|
| Device doesn't appear in Discovered after 2 minutes. | Check the Wi-Fi credentials were accepted (device should be off the soft-AP now). Verify the Tesserae server URL is reachable from the device's network: `curl http://<server>:8765/api/healthz` from another machine on the same LAN. |
| Discovered card says the wrong kind (e.g. `esp32_client` instead of `seeed_reterminal_e1002`). | The firmware kind string doesn't match the hardware catalog. Update to the latest firmware from tesserae.ink/flash. |
| Panel refreshes but shows garbage / stripes. | Frame size mismatch. Check that Tesserae's server version is at least v0.64.60 (the E-series manifests need to be present). Settings → Devices → your card → Debug shows the running version. |
| Web flasher won't connect. | Firefox / Safari don't support Web Serial. Use Chrome or Edge. Also make sure you're using a data-capable USB cable and port. |
| Serial-port list is empty on macOS with a reTerminal plugged in. | The WCH CH340 driver isn't installed / enabled. Install [`WCHSoftGroup/ch34xser_macos`](https://github.com/WCHSoftGroup/ch34xser_macos), then toggle it on under **System Settings → General → Login Items & Extensions → Driver Extensions**. |
| Firmware flashes but device stays in soft-AP mode forever. | Wi-Fi credentials weren't saved. Re-join the soft-AP and re-enter them; some captive portals need an explicit "Save" tap. |
| Frame downloads but panel doesn't paint. | Battery voltage may be too low for the panel refresh voltage doubler. Plug the USB cable back in and let it charge for an hour; try again. |

## Next steps

- [Set up multiple panels on one server](../install/devices.md#multiple-panels): different dashboards on different Seeed devices, all painted by the same Tesserae install.
- [Add a rotation](../install/devices.md#next-steps): cycle through several dashboards on the same panel across the day.
- [Bind the front buttons](../install/buttons.md): rotate through dashboards, jump straight to one, or force a refresh with the reTerminal's three front buttons. Default is `left → previous`, `right → next`, `refresh → re-render`; override per device from **Settings → Devices → General → Buttons**.
- [Browse community widgets](https://tesserae.ink/catalog/): one-click installs for Spotify, GitHub, OctoPrint, F1, and more.
- [Firmware source](https://github.com/dmellok/tesserae-device-firmware): read the code, file issues, contribute board support for new panels.
