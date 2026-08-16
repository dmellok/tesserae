# Quickstart: Seeed reTerminal E-Series (TRMNL BYOS)

The Seeed reTerminal E Series e-paper terminals: E1001 (7.5" mono), E1002 (7.3" Spectra 6), E1003 (10.3" mono, 16-level grey), and E1004 (13.3" Spectra 6). All ship with Seeed's SenseCraft HMI firmware and need a re-flash to talk to Tesserae.

!!! tip "There's a native Tesserae firmware path"
    This page covers the **TRMNL BYOS** flashing path. There's also a native Tesserae firmware for the reTerminal E-series with a browser-based flasher at [tesserae.ink/flash](https://tesserae.ink/flash); see the [unified Seeed guide](seeed-unified.md) for that flow. Pick whichever fits your setup.

    Stay on this page if you want to run TRMNL's firmware and use Tesserae as the BYOS server, or if the unified firmware doesn't yet cover your specific board revision.

!!! note "Verified vs Pending"
    All four E-Series boards are confirmed on real hardware running the native Tesserae firmware (the [unified Seeed guide](seeed-unified.md)). On this page's TRMNL BYOS path, the E1003 has been confirmed end-to-end; the E1001, E1002, and E1004 use the same flashing flow, and the TRMNL firmware port for the E1004 is still in progress upstream.

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

### Per-model references

- **E1002**: the panel uses an ACeP 7-colour gamut, which is more restrictive than the Spectra 6 family. For practical layout patterns on this gamut, see the [colour-for-calendars discussion on r/trmnl](https://www.reddit.com/r/trmnl/comments/1ucr8b2/color_for_calendars_is_here/).
- **E1004**: TRMNL firmware support is in development upstream. Original bring-up by [@limengdu](https://github.com/limengdu) landed in [usetrmnl/trmnl-firmware#410](https://github.com/usetrmnl/trmnl-firmware/pull/410) (now closed). The active PR is [usetrmnl/trmnl-firmware#445](https://github.com/usetrmnl/trmnl-firmware/pull/445) by [@oetiker](https://github.com/oetiker), which rebases that work onto `main` with build hardening and adds onboard SHT4x temperature / humidity reporting through the existing `SENSORS` header. Verified on real E1004 hardware.

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
- [Browse community widgets](https://tesserae.ink/catalog/).
- [Per-device settings](../install/devices.md#per-device-settings) for the wake interval and quiet hours.
