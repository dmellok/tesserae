# Tesserae

**Self-hosted e-ink dashboard companion.** Compose tile-based dashboards in
the browser, render them headless, and push the resulting frame to one or more
panels, Raspberry Pi and ESP32 over MQTT, TRMNL hardware and Kindle (via
KOReader) over HTTP-pull.

Sibling rebuild of [inky-dash](https://github.com/dmellok/inky-dash), same job,
but every layer of the publishing pipeline is a **drop-a-folder** extension
point: new widget, new theme, new font, new renderer, new device kind. The
name comes from [*tessera*](https://en.wikipedia.org/wiki/Tessera), the
individual tile of a mosaic; the editor composes a dashboard out of cells.

!!! tip "New here? Start with the path that matches you"
    - **I want it running** → [Install Tesserae](install/server.md) (or via [Docker](install/docker.md) / as a [Home Assistant Add-on](install/home-assistant.md))
    - **I have a panel to drive** → [Install a client](install/clients.md) then [Set up a device](install/devices.md)
    - **What can it show?** → [Widget gallery](widgets/gallery.md) (58 widgets)
    - **What hardware works?** → [Screens & compatibility](compatibility.md)
    - **I want to build a widget** → [Build a widget with AI](dev/writing-a-plugin.md)

## How it works

1. **Compose** a dashboard of cells in the editor; each cell is a widget plugin.
2. **Render** the dashboard headlessly with Chromium at the target panel's exact pixel size.
3. **Quantise** the frame to the panel's colour palette and pack it for the wire.
4. **Publish**, over MQTT (Pi, ESP32) or HTTP-pull (TRMNL, Kindle). A small client on the panel paints it and sleeps.

Every layer, the 58 widgets, the themes, the fonts, the renderers, the
device kinds, is a drop-a-folder plugin or a dedicated app surface
discovered at boot. See [Build a widget with AI](dev/writing-a-plugin.md)
for the authoring path and [the widget contract](widgets.md) for the
full spec.

## Project status

Tesserae is a self-hosted hobby project, built in the open by a solo
maintainer. The composer → renderers → transport → devices pipeline,
scheduler, Home Assistant MQTT auto-discovery, webhook push, the
Spectra theme system (browse / builder / image-to-palette extraction),
font picker, and form-driven page editor are all shipping. Four
reference clients (ESP32, Pi `.bin`, Pi PNG, TRMNL / KOReader) run on
real hardware, see
[what's tested](compatibility.md#whats-been-tested-on-real-hardware).
The panel matrix is still small, so testers on other displays are
very welcome, as are contributors:
[open an issue or PR](https://github.com/dmellok/tesserae).
