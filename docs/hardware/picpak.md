# PicPak 4.2" BWRY

PicPak is a battery-powered e-paper frame. It runs community firmware by
[@varanu5](https://github.com/varanu5/picpak-tesserae-client) and connects directly to Tesserae.

## Firmware

The [`varanu5/picpak-tesserae-client`](https://github.com/varanu5/picpak-tesserae-client) firmware is maintained separately by
[@varanu5](https://github.com/varanu5). The firmware is stable, and the author continues to stress-test it.

[Compatibility notes](https://github.com/dmellok/tesserae/issues/61) cover what works, BWRY palette order, 2-bpp packing and the required vertical-scan flip.

With Online features on, Settings → Devices and the Firmware page check for newer releases through api.tesserae.ink.
An "update available" badge links to the release. Install updates with the web flasher below; over-the-air updates aren't supported.

## Flashing

The [PicPak Web Flasher](https://picpaktesserae.pages.dev) is the quickest way to install the firmware.
It uses Web Serial, your browser's serial connection. No build tools are needed.

1. Plug the PicPak in over USB.
2. Open the flasher in Chrome or Edge.
3. Pick the serial port and flash.

Then pair the device in Tesserae's Settings → Devices.

To build it yourself, follow the README in
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

After its first check-in, the PicPak appears under Settings → Devices → Discovered. Assign it a page.

Set its sleep interval in device settings, from 30 s to 1 week.
The firmware reads this on each wake, so a longer interval takes effect on the next fetch.
