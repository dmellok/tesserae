# Quickstart guides

You have Tesserae running. Now you need a panel on the wall. Pick the hardware below; each guide walks you from "this device is in a box" to "it's painting a Tesserae dashboard" in about ten minutes.

If Tesserae is not running yet, start at [Install Tesserae](../install/server.md). Every quickstart on this page assumes the server is up and the browser editor is reachable.

## Raspberry Pi

The Pi runs a small client that subscribes to your Tesserae server and paints the panel over SPI. No firmware flashing involved.

- [Raspberry Pi + Pimoroni Inky Impression](pi-inky.md): the 4", 5.7", 7.3", 13.3", pHAT, or wHAT. Manual install path.
- [Raspberry Pi + Pimoroni Inky, automated via cloud-init](pi-inky-cloud-init.md): zero-touch flash-and-boot setup for a fresh SD card.
- [Pi Pico Plus 2 W + Pimoroni Inky 13.3"](pi-pico-inky.md): the battery-powered RP2350 path.

## Waveshare ESP32

ESP32-S3 development boards driving Waveshare e-paper panels. Battery-powered, deep-sleep between renders.

- [Waveshare 13.3" Spectra 6 (ESP32-S3)](waveshare-spectra-6-13-3.md): the maintainer's daily driver.
- [Waveshare 7.3" PhotoPainter (ESP32-S3)](waveshare-photopainter-7-3.md): the 6-colour 7.3" path.
- [Waveshare 4.2" B/W (ESP32)](waveshare-bw-4-2.md): the monochrome 1-bpp path.

## TRMNL and TRMNL-compatible

Devices that speak the TRMNL BYOS protocol. The TRMNL flagship hardware ships ready to go; Seeed and XIAO panels need a firmware flash first.

- [TRMNL OG / TRMNL X](trmnl.md): point the device's BYOS URL at your Tesserae server.
- [Seeed reTerminal E-Series (E1001 / E1002 / E1003 / E1004)](seeed-reterminal.md): flash TRMNL firmware, then pair.
- [Seeed XIAO 7.5" ePaper Panel](seeed-xiao.md): flash the XIAO TRMNL variant, then pair.

## Kindle

Jailbroken Kindles can act as a Tesserae panel via the KOReader plugin path.

- [Kindle Paperwhite + KOReader](kindle.md): the trmnl-display plugin route.

## After any quickstart

Every guide ends at the same point: a panel painting a single dashboard. From there:

- [Set up a schedule](../install/devices.md): rotate dashboards, refresh on a cadence, use smart sync to render just before each wake on battery panels.
- [Browse the widget catalog](../widgets/community.md): one-click install of community widgets for Spotify, GitHub, OctoPrint, F1, and more.
- [Build your own widget](../dev/writing-a-widget.md): the drop-a-folder plugin path.

## Don't see your hardware?

The list above is the verified-or-pending matrix. If you have a panel that isn't covered:

- Check the [hardware compatibility matrix](../compatibility.md) for the latest status.
- Read [Add hardware support](../dev/adding-hardware.md) for the data-only JSON path to a new SKU.
- Open a [discussion](https://github.com/dmellok/tesserae/discussions) if you'd like to test something not yet listed.
