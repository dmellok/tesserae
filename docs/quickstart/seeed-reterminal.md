# Quickstart: Seeed reTerminal E-Series (TRMNL BYOS)

Flash TRMNL firmware to connect your reTerminal to Tesserae. Its factory-installed SenseCraft HMI firmware can't use Tesserae as a server (BYOS).

!!! tip "There's a native Tesserae firmware path"
    You can also use native Tesserae firmware through [tesserae.ink/flash](https://tesserae.ink/flash). Follow the [unified Seeed guide](seeed-unified.md).

    Use this page for TRMNL firmware, or if native firmware doesn't cover your board revision.

!!! note "Verified vs Pending"
    The E1003 is confirmed working through TRMNL BYOS. The other three models use the same flashing steps.

    All four are tested with native Tesserae firmware; see the [unified Seeed guide](seeed-unified.md). Use native firmware if TRMNL's flasher doesn't list the E1004.

## 01 — Flash TRMNL firmware

Use one of these flashers:

- Use [TRMNL Web Flasher](https://trmnl.com/flash) for the latest firmware on supported devices.
- Seeed's reTerminal E-Series Firmware Flasher supports E1001, E1002 and E1003 only.

    Find it on [Seeed's wiki](https://wiki.seeedstudio.com/reterminal_e10xx_trmnl/). Use it if Seeed has a newer packaged release.

Hold BOOT while connecting the device to your computer over USB, or follow the flasher's prompt. Click **Install**.

After flashing, open the device's Wi-Fi setup page from your phone or laptop. Enter:

- BYOS server URL: `http://<your-server>:8765`
- Your home Wi-Fi name and password.

Save. The reTerminal restarts onto your network.

## 02 — It pairs itself

The device registers automatically through `/api/setup` using its MAC address. It appears under **Settings → Devices** within seconds. You don't need to type a token.

The reTerminal starts at 800×480. Its first `/api/display` check supplies the real size in `Width` and `Height`. Tesserae only uses `Model` to choose sizes for TRMNL's own boards.

| Model | Resolution | Notes |
|---|---|---|
| reTerminal E1001 | 800×480 | 7.5" mono |
| reTerminal E1002 | 800×480 | 7.3" Spectra 6 |
| reTerminal E1003 | 1872×1404 | 10.3" mono, 16-level grey |
| reTerminal E1004 | 1200×1600 | 13.3" Spectra 6 |

### Per-model references

- For E1002 colour layouts, see the [colour-for-calendars discussion on r/trmnl](https://www.reddit.com/r/trmnl/comments/1ucr8b2/color_for_calendars_is_here/). Its six Spectra 6 inks match the E1004; the screen is smaller.
- E1004 TRMNL support: [#771](https://github.com/usetrmnl/trmnl-firmware/pull/771), with earlier work by [@limengdu](https://github.com/limengdu) ([#410](https://github.com/usetrmnl/trmnl-firmware/pull/410)) and [@oetiker](https://github.com/oetiker) ([#445](https://github.com/usetrmnl/trmnl-firmware/pull/445)). Native firmware is tested in full colour; see the [unified Seeed guide](seeed-unified.md).

## 03 — Compose a dashboard

In the editor:

1. Open **Dashboards → New**.
2. Choose a layout:
    - E1003: greyscale text.
    - E1002: photos on its 800×480 colour screen.
3. Bind the page to the device in the device picker.
4. Click **Push**.

The reTerminal downloads and draws the image when it wakes, then sleeps.

## 04 — Set the refresh

The reTerminal checks for images at least 60 seconds apart. Open the dashboard's **Schedules** card:

- Turn on **Smart sync** on an **Every N minutes** schedule to draw each image just before the device wakes.
- Seeed rates E1003 and E1004 battery life at up to six months. The E1004 figure assumes one refresh daily. More frequent wakes use more battery.

## Next steps

- [Build a widget tuned for greyscale](../dev/writing-a-widget.md) for the E1003's 16-level palette.
- [Browse community widgets](https://tesserae.ink/catalog/).
- [Wake interval](../install/devices.md#dashboard-refresh-settings) and [quiet hours](../install/devices.md#quiet-hours).
