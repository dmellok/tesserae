# Tesserae

**Self-hosted e-ink dashboard companion.** Compose tile-based dashboards in
the browser, render them headless, and deliver the resulting frame to one or
more panels over a small REST device API (MQTT retained topic is still
supported for existing setups). Every reference client, Raspberry Pi, ESP32,
Seeed reTerminal E-Series, TRMNL, jailbroken Kindle via KOReader, speaks the
same protocol.

Sibling rebuild of [inky-dash](https://github.com/dmellok/inky-dash), same job,
but every layer of the publishing pipeline is a **drop-a-folder** extension
point: new widget, new theme, new font, new renderer, new device kind. The
name comes from [*tessera*](https://en.wikipedia.org/wiki/Tessera), the
individual tile of a mosaic; the editor composes a dashboard out of cells.

!!! tip "New here? Start with the path that matches you"
    - **I want it running** → [Install Tesserae](install/server.md) (or via [Docker](install/docker.md) / as a [Home Assistant App](install/home-assistant.md))
    - **I have a panel to drive** → [Install a client](install/clients.md) then [Set up a device](install/devices.md)
    - **I want the ready-to-go hardware path** → the [Seeed reTerminal E-Series](hardware/seeed.md) (browser flash at [tesserae.ink/flash](https://tesserae.ink/flash), battery-powered, no assembly required)
    - **What can it show?** → [Bundled widget gallery](widgets/gallery.md) (33 ship in the install) or the [community catalog](widgets/community.md) (more, one-click install)
    - **What hardware works?** → [Screens & compatibility](compatibility.md)
    - **I want to build a widget** → [Build a widget with AI](dev/writing-a-widget.md)

## How it works

1. **Compose** a dashboard of cells in the editor; each cell is a widget plugin.
2. **Render** the dashboard headlessly with Chromium at the target panel's exact pixel size.
3. **Quantise** the frame to the panel's colour palette and pack it for the wire.
4. **Deliver** the packed frame over the REST device API (`GET /api/v1/device/<id>/frame` for battery-poll clients, `POST /api/v1/device/<id>/frame` for always-on clients). MQTT retained topics still work for existing MQTT-based setups. A small client on the panel paints the frame and sleeps.

Every layer, the bundled widgets + the community catalog, the themes, the fonts, the renderers, the
device kinds, is a drop-a-folder plugin or a dedicated app surface
discovered at boot. See [Build a widget with AI](dev/writing-a-widget.md)
for the authoring path and [the widget contract](widgets.md) for the
full spec.

## Project status

Tesserae is a self-hosted hobby project, built in the open by a solo
maintainer with a growing group of community contributors. The
composer → renderers → transport → devices pipeline, scheduler, Home
Assistant MQTT auto-discovery, webhook push, the Spectra theme system
(browse / builder / image-to-palette extraction), font picker, and
form-driven page editor are all shipping. Real-hardware coverage now
sits at **14 verified panels across Seeed, Pimoroni, Waveshare, TRMNL,
and Kindle**, driven by a client fleet that includes the
Tesserae-native firmware for the Seeed reTerminal E-Series (browser
flash, battery-powered), `esp32_bin` (13.3" Waveshare + 7.3"
PhotoPainter), `pi_bin` and `pi_png` (Pi HAT panels including the Inky
Impression family), `pico_bin` (battery-powered Pi Pico Plus 2W
driving Inky-style Spectra 6), `trmnl_png` (TRMNL BYOS devices +
jailbroken Kindle via KOReader), `circuitpython_png` (generic
CircuitPython boards), and the community-firmware `picpak_client`
(PicPak 4.2" BWRY). See
[what's tested](compatibility.md#whats-been-tested-on-real-hardware).
Testers on other displays are welcome, as are contributors:
[open an issue or PR](https://github.com/dmellok/tesserae).
