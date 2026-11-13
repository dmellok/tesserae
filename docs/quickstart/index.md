# Quickstart guides

Pick your screen below. Each guide takes about ten minutes, from unpacking to displaying a dashboard.

First, [install Tesserae](../install/server.md). Your server must be running and the browser editor reachable.

## Raspberry Pi

A Raspberry Pi runs a small client that draws pictures on a connected screen.
The screen connects over SPI. Only the Pico needs firmware flashed.

- [Raspberry Pi + Pimoroni Inky Impression](pi-inky.md): install manually. The guide lists supported sizes.
- [Raspberry Pi + Pimoroni Inky, automated via cloud-init](pi-inky-cloud-init.md): automatic setup on first boot with a fresh SD card.
- [Pi Pico Plus 2 W + Pimoroni Inky 13.3"](pi-pico-inky.md): battery-powered, using the RP2350 chip.

## Seeed ePaper (unified firmware)

Seeed reTerminal E-series and XIAO ePaper devices share one firmware image.
Flash it from your browser at [tesserae.ink/flash](https://tesserae.ink/flash).
They connect directly to Tesserae, without a TRMNL account or BYOS proxy.

- [Seeed ePaper devices (unified firmware)](seeed-unified.md): recommended setup. The guide lists supported models.

## Waveshare ESP32

ESP32 and ESP32-S3 boards drive Waveshare screens. They run on batteries and sleep between updates.

- [Waveshare 13.3" Spectra 6 (ESP32-S3)](waveshare-spectra-6-13-3.md): the maintainer's daily driver.
- [Waveshare 7.3" PhotoPainter (ESP32-S3)](waveshare-photopainter-7-3.md): 6-colour screen.
- [Waveshare 4.2" B/W (ESP32)](waveshare-bw-4-2.md): black-and-white screen, 1 bit per pixel.

## TRMNL and TRMNL-compatible

TRMNL's bring-your-own-server (BYOS) protocol connects these devices to Tesserae. TRMNL hardware comes ready to use it.

- [TRMNL OG / TRMNL X](trmnl.md): point the device's BYOS URL at your Tesserae server.
- [Seeed reTerminal E-Series (TRMNL BYOS)](seeed-reterminal.md): use TRMNL firmware. Most users should follow the [unified firmware guide](seeed-unified.md).
- [Seeed XIAO 7.5" (TRMNL BYOS)](seeed-xiao.md): use TRMNL firmware.

## Bloomin8 Canvas

- [Bloomin8 Canvas](bloomin8.md): set Schedule Pull to use Tesserae. Built from Bloomin8's spec; not yet tested on hardware.

## Fraimic Canvas

- [Fraimic Canvas](fraimic.md): add the frame by its address. Tesserae sets its wake schedule and sends pictures when it wakes. Picture format checked on a 31.5" Large Canvas.

## Visionect signs

- [Visionect signs](visionect.md): replace the Visionect Software Suite with Tesserae. Pictures reach the sign over its own protocol on TCP 11113. Tested on a Place & Play 32.

## Kindle

Jailbroken Kindles can act as a Tesserae panel through KOReader.

- [Kindle + KOReader](kindle.md): the Tesserae KOReader plugin, paired with a claim code.

## After any quickstart

Once your screen shows a dashboard:

- [Set up a schedule](../install/devices.md#compose-a-dashboard): rotate dashboards and choose how often they refresh. Smart sync draws pictures just before battery-powered screens wake.
- [Browse the widget catalog](https://tesserae.ink/catalog/): install community widgets with one click.
- [Build your own widget](../dev/writing-a-widget.md): add it as a plugin folder.

## Don't see your hardware?

Some guides are tested on hardware; others are awaiting testing. If your screen isn't listed:

- Check the [hardware compatibility matrix](../compatibility.md) for the latest status.
- Read [Add hardware support](../dev/adding-hardware.md) to add a model with a JSON file.
- Open a [discussion](https://github.com/dmellok/tesserae/discussions) if you'd like to test something not yet listed.
