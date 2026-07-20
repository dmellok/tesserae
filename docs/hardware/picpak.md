# PicPak 4.2" BWRY

The **PicPak** is a battery-powered 4.2" 4-colour BWRY
(black / white / red / yellow) e-paper frame. It runs a community
firmware authored by
[@varanu5](https://github.com/varanu5/picpak-tesserae-client) that
speaks Tesserae's REST device API directly and paints frames the
`esp32_bin` renderer packs to native BWRY 2-bpp.

## Firmware

- **Repo**: [`varanu5/picpak-tesserae-client`](https://github.com/varanu5/picpak-tesserae-client)
- **Author**: [@varanu5](https://github.com/varanu5)
- **Status**: stable, ongoing stress-testing by the author.
- **Compatibility discussion**: [tesserae#61](https://github.com/dmellok/tesserae/issues/61) —
  varanu5's write-up of what works, the BWRY palette order, the
  2-bpp packing layout, and the vertical-scan flip requirement.

The firmware is not part of the Tesserae repo; it lives with its
author. The quickest way on is the browser flasher below; otherwise
build and flash from the repo per its README. Either way, pair the
device afterwards in Tesserae's Settings → Devices.

## Flashing

The **[PicPak Web Flasher](https://picpaktesserae.pages.dev)** writes the
community firmware straight from your browser over Web Serial, with no
toolchain to install. Plug the PicPak in over USB, open the flasher in a
Chromium-based browser (Chrome or Edge), pick the serial port, and flash.

Prefer to build it yourself? Follow the README in
[`varanu5/picpak-tesserae-client`](https://github.com/varanu5/picpak-tesserae-client).

## Panel specs

| Property | Value |
|---|---|
| Panel | 4.2" 4-colour BWRY e-paper |
| Resolution | 400 × 300 (landscape) |
| Gamut | `bwry_4` (black / white / red / yellow) |
| Wire format | `esp32_bin` (2-bpp packed) |
| Delivery | REST (`GET /api/v1/device/<id>/frame`) |
| Device kind | `picpak_client` |
| SKU manifest | `hardware/community/picpak_4_2.json` |

## Once paired

The PicPak appears under Settings → Devices → Discovered after the
firmware first checks in. Assign it a page like any other device.
Sleep interval is configurable per-device (30 s to 1 week) from the
device settings; the firmware reads that value on each wake so a
longer interval takes effect on the next fetch.
