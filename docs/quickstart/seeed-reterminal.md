# Quickstart: Seeed reTerminal E-Series

The Seeed reTerminal E Series e-paper terminals: E1001 (7.5" mono), E1002 (7.3" ACeP colour), E1003 (10.3" mono, 16-level grey), and E1004 (13.3" colour Spectra 6). All ship with Seeed's SenseCraft HMI firmware and need a re-flash with TRMNL firmware to talk to Tesserae's BYOS endpoints.

!!! note "Verified vs Pending"
    The E1003 has been confirmed end-to-end on real hardware. The E1001, E1002, and E1004 use the same flashing flow; the TRMNL firmware port for E1004 is still in progress upstream.

## 01 — Flash TRMNL firmware

The reTerminal ships with Seeed's SenseCraft HMI by default, which doesn't speak BYOS. Two flashers handle the re-flash:

- **[TRMNL Web Flasher](https://trmnl.com/flash)**: recommended. Browser-based, works for every supported device. Use this for the freshest firmware.
- **Seeed reTerminal E-Series Firmware Flasher**: an alternative Seeed-hosted channel, exclusive to E1001 / E1002 / E1003. Linked from [Seeed's wiki](https://wiki.seeedstudio.com/reterminal_e10xx_trmnl/). Use this if the upstream TRMNL release lags behind what Seeed has packaged.

Plug the device into your computer over USB while holding the BOOT button (or however the flasher prompts), then click **Install**.

After flashing, the device opens a Wi-Fi captive portal. Connect from your phone or laptop, then set:

- **BYOS server URL**: `http://<your-server>:8765`
- **Wi-Fi SSID and password**: your home network.

Save. The reTerminal restarts onto your network.

## 02 — It pairs itself

On first boot the device calls Tesserae's `/api/setup` with its MAC and auto-provisions. It appears under **Settings → Devices** within seconds. No token to type.

The flashed firmware sends a `Model` header that Tesserae maps to the panel dimensions:

| Model | Resolution | Notes |
|---|---|---|
| `reTerminal E1001` | 800×480 | 7.5" mono |
| `reTerminal E1002` | 800×480 | 7.3" ACeP 7-colour |
| `reTerminal E1003` | 1404×1872 portrait | 10.3" mono, 16-level grey |
| `reTerminal E1004` | 1200×1600 | 13.3" Spectra 6, firmware port in progress |

## 03 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. Build a layout that suits the panel. The E1003's 16-level greyscale and tall portrait aspect lend themselves to monochrome-friendly typography. The E1002's ACeP 7-colour palette and 800×480 canvas suit photo-led layouts.
3. Bind the page to the device in the device picker.
4. Hit **Push**.

The reTerminal wakes, fetches the rendered frame from Tesserae, paints, and sleeps.

## 04 — Set the refresh

The reTerminal polls Tesserae's BYOS endpoint on its own cadence (≥60 seconds; the floor is in the manifest). In the dashboard's **Schedules** card:

- **Smart sync** renders just before each wake, so the panel always gets a fresh frame without burning battery on idle renders.
- The E1003 advertises up to six months of battery life at a 1-hour wake cadence with smart sync.

## Next steps

- [Build a widget tuned for greyscale](../dev/writing-a-widget.md) for the E1003's 16-level palette.
- [Browse community widgets](../widgets/community.md).
- [Per-device settings](../install/devices.md#per-device-settings) for the wake interval and quiet hours.
