# Seeed ePaper devices

The **Seeed reTerminal E-Series** is the ready-to-go hardware path for
Tesserae: pre-assembled, battery-powered, no toolchain required. All
four reTerminal SKUs plus the XIAO ePaper family run one shared
[Tesserae-native firmware](https://github.com/dmellok/tesserae-device-firmware),
flashable from Chrome or Edge in a couple of clicks at
[tesserae.ink/flash](https://tesserae.ink/flash).

!!! info "Group photo landing shortly"
    A dedicated shot of all six Seeed SKUs painting Tesserae dashboards
    side by side is coming here. Until then, individual devices are
    visible on the [home page's rack shot](../index.md).

## Why the reTerminal E-Series

- **Browser flash.** No ESP-IDF toolchain, no `esptool.py`, no
  serial voodoo. Plug in over USB-C, hit the flasher at
  [tesserae.ink/flash](https://tesserae.ink/flash), pick your model,
  paint.
- **Battery-powered.** All four E-Series SKUs ship with a battery in
  the case. Deep-sleep between renders; days to weeks of runtime
  depending on refresh cadence.
- **No assembly.** Bare panels want a controller board, a battery
  wire-up, a case. The reTerminal E-Series arrives as a finished
  device.
- **Four sizes.** 7.5" mono, 7.3" Spectra 6 colour, 10.3" mono with
  16-level greyscale, 13.3" Spectra 6 colour. Same firmware, same
  wire format family, same Tesserae onboarding.

## SKUs

| Model | Panel | Colour | Resolution |
|---|---|---|---|
| [reTerminal E1001](https://www.seeedstudio.com/reTerminal-E1001-p-6534.html) | 7.5" | Mono | 800×480 |
| [reTerminal E1002](https://www.seeedstudio.com/reTerminal-E1002-p-6533.html) | 7.3" | Spectra 6 (6-colour) | 800×480 |
| [reTerminal E1003](https://www.seeedstudio.com/reTerminal-E1003-p-6731.html) | 10.3" | Mono, 16-level grey | 1872×1404 |
| [reTerminal E1004](https://www.seeedstudio.com/reTerminal-E1004-p-6692.html) | 13.3" | Spectra 6 (6-colour) | 1200×1600 |

The wider [Seeed XIAO ePaper](https://www.seeedstudio.com/category/Boards/XIAO-Series.html)
family is supported via the same unified firmware:

| Model | Panel | Colour | Resolution |
|---|---|---|---|
| [XIAO ePaper EE02](https://www.seeedstudio.com/XIAO-ePaper-DIY-Kit-EE02-for-13-3-Spectratm-6-E-Ink.html) | 13.3" | Spectra 6 (6-colour) | 1200×1600 |
| [XIAO 7.5" ePaper Panel](https://www.seeedstudio.com/XIAO-7-5-ePaper-Panel-p-6416.html) (C3, integrated) | 7.5" | Mono | 800×480 |
| [TRMNL 7.5" (OG) DIY Kit](https://www.seeedstudio.com/TRMNL-7-5-Inch-OG-DIY-Kit-p-6481.html) (XIAO ESP32-S3 Plus) | 7.5" | Mono | 800×480 |

The last two are different products around the same glass and are not
interchangeable at the firmware level, so they register as separate
kinds (`xiao_epaper_panel_75_c3` and `xiao_epaper_75`). The integrated
C3 panel reports no battery level; the hardware has no divider to an
ADC pin, so Tesserae shows no battery for it rather than showing it as
flat.

The XIAO SKUs share panel families with the reTerminal E1004 (EE02) and
E1001 (both 7.5" boards), so the wire format is proven; extending
coverage to the rest of the XIAO ePaper range is on the roadmap.

## Getting started

1. Install Tesserae with the [server guide](../install/server.md) or
   [Docker](../install/docker.md).
2. Plug the reTerminal into a computer running Chrome or Edge.
3. Open [tesserae.ink/flash](https://tesserae.ink/flash), pick your
   model, hit **Install**.
4. Follow the [unified Seeed quickstart](../quickstart/seeed-unified.md)
   for onboarding, Wi-Fi setup, and pairing with your Tesserae server.

## Front buttons

The reTerminal E-Series ships with three front buttons that Tesserae
can bind to any server-side action: rotate through the dashboards on
this panel, jump straight to a specific one, force a fresh render,
fire a webhook. Configure the mapping per device from **Settings →
Devices → General → Buttons**. Defaults to
`left → rotate_prev`, `right → rotate_next`, `refresh → refresh`;
see [Physical buttons](../install/buttons.md) for the full action
list and common patterns.

## Firmware source

The firmware is open source and lives at
[github.com/dmellok/tesserae-device-firmware](https://github.com/dmellok/tesserae-device-firmware).
Built with ESP-IDF, one board header per Seeed SKU, releases wired via
GitHub Actions on each Tesserae tag. Bugs and hardware notes welcome as
issues or PRs on that repo.

## Also on the site

- Full [compatibility matrix](../compatibility.md), all vendors, every
  panel Tesserae supports.
- [Unified Seeed quickstart](../quickstart/seeed-unified.md), covering
  every SKU listed above.
- [Older TRMNL BYOS path for Seeed](../quickstart/seeed-reterminal.md),
  for users who prefer to stay on TRMNL's stock firmware.
