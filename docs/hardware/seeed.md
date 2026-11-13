# Seeed ePaper devices

The reTerminal E-Series and XIAO ePaper devices share
[Tesserae-native firmware](https://github.com/dmellok/tesserae-device-firmware),
which you can flash from Chrome or Edge at
[tesserae.ink/flash](https://tesserae.ink/flash).

## Why the reTerminal E-Series

- Flash over USB-C at [tesserae.ink/flash](https://tesserae.ink/flash).
  You don't need ESP-IDF or `esptool.py`. No manual serial setup is needed.
- All four models include a battery and sleep between updates.
  Seeed rates the E1003 and E1004 for up to six months.
  The E1004 rating assumes one refresh per day; more frequent updates drain the battery faster.
- No assembly is needed. All four sizes use the same setup process.

## SKUs

| Model | Panel | Colour | Resolution |
|---|---|---|---|
| [reTerminal E1001](https://www.seeedstudio.com/reTerminal-E1001-p-6534.html) | 7.5" | Mono | 800×480 |
| [reTerminal E1002](https://www.seeedstudio.com/reTerminal-E1002-p-6533.html) | 7.3" | Spectra 6 (6-colour) | 800×480 |
| [reTerminal E1003](https://www.seeedstudio.com/reTerminal-E1003-p-6731.html) | 10.3" | Mono, 16-level grey | 1872×1404 |
| [reTerminal E1004](https://www.seeedstudio.com/reTerminal-E1004-p-6692.html) | 13.3" | Spectra 6 (6-colour) | 1200×1600 |

The [reTerminal Sticky](https://www.seeedstudio.com/reTerminal-Sticky-p-6861.html)
(480×800 portrait, 4-level grey, touch) uses the same firmware as kind `seeed_reterminal_sticky`.

Supported [Seeed XIAO ePaper](https://www.seeedstudio.com/category/Boards/XIAO-Series.html) devices:

| Model | Panel | Colour | Resolution |
|---|---|---|---|
| [XIAO ePaper EE02](https://www.seeedstudio.com/XIAO-ePaper-DIY-Kit-EE02-for-13-3-Spectratm-6-E-Ink.html) | 13.3" | Spectra 6 (6-colour) | 1200×1600 |
| [XIAO ePaper EE03](https://www.seeedstudio.com/XIAO-ePaper-DIY-Kit-EE03-for-10-3-Monochrome-ePaper-Display.html) | 10.3" | Mono, 16-level grey | 1872×1404 |
| [XIAO ePaper EE04](https://www.seeedstudio.com/XIAO-ePaper-Display-Board-EE04-p-6560.html) (7.5" mono or 7.3" Spectra 6) | 7.5" / 7.3" | Mono / Spectra 6 | 800×480 |
| [XIAO ePaper EE05](https://wiki.seeedstudio.com/epaper_ee05/) + [2.13" quadruple-colour panel](https://www.seeedstudio.com/2-13-Quadruple-Color-ePaper-Display-with-122x250-Pixels-p-5779.html) | 2.13" | BWRY (4-colour) | 250×122 |
| [XIAO 7.5" ePaper Panel](https://www.seeedstudio.com/XIAO-7-5-ePaper-Panel-p-6416.html) (C3, integrated) | 7.5" | Mono | 800×480 |
| [TRMNL 7.5" (OG) DIY Kit](https://www.seeedstudio.com/TRMNL-7-5-Inch-OG-DIY-Kit-p-6481.html) (XIAO ESP32-S3 Plus) | 7.5" | Mono | 800×480 |

The last two products use the same screen but need different firmware:

| Product | Device kind |
|---|---|
| XIAO 7.5" ePaper Panel (C3) | `xiao_epaper_panel_75_c3` |
| TRMNL 7.5" (OG) DIY Kit | `xiao_epaper_75` |

The C3 panel cannot measure battery level, so Tesserae leaves it blank.

Coverage for the remaining XIAO ePaper range is on the roadmap.

## Getting started

1. Install Tesserae with the [server guide](../install/server.md) or
   [Docker](../install/docker.md).
2. Plug the reTerminal into a computer running Chrome or Edge.
3. Open [tesserae.ink/flash](https://tesserae.ink/flash), pick your
   model, hit **Connect & flash**.
4. Follow the [unified Seeed quickstart](../quickstart/seeed-unified.md)
   to set up Wi-Fi and pair the device with Tesserae.

## Front buttons

The three front buttons can run any server action.
Use them to rotate this screen's dashboards or jump to a specific one.
They can also draw a fresh picture or call a webhook.

Set each device's **Button map** under **Controls**.
Defaults: `left → rotate_prev`, `right → rotate_next`, `refresh → refresh`.

See [Physical buttons](../install/buttons.md) for all actions and examples.

## Firmware source

The firmware is open source and lives at
[github.com/dmellok/tesserae-device-firmware](https://github.com/dmellok/tesserae-device-firmware).
It is built with ESP-IDF. GitHub Actions publishes releases on each Tesserae tag.
Report bugs and hardware notes as issues or PRs there.

## Also on the site

- [Compatibility matrix](../compatibility.md): all supported screens.
- [Unified Seeed quickstart](../quickstart/seeed-unified.md): setup for every model above.
- [TRMNL BYOS path for Seeed](../quickstart/seeed-reterminal.md): keep TRMNL's stock firmware.
