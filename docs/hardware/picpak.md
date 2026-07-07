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

The firmware is not part of the Tesserae repo; it lives with its
author. Grab it from the repo above, flash to a PicPak per the
firmware's README, then pair the device in Tesserae's Settings →
Devices.

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
