# Tesserae

Tesserae runs dashboards on e-ink screens. You build a dashboard in your
browser, and Tesserae turns it into a picture and sends it to your screens.

It runs on your own computer or server. The screen can be anything from a
Raspberry Pi to a jailbroken Kindle; see [which screens work](compatibility.md).

The name comes from [*tessera*](https://en.wikipedia.org/wiki/Tessera), one
tile in a mosaic, because a dashboard is built from tiles. It started as a
rebuild of [inky-dash](https://github.com/dmellok/inky-dash).

!!! tip "Where to start"
    - To get it running, [install Tesserae](install/server.md), or use [Docker](install/docker.md) or the [Home Assistant App](install/home-assistant.md).
    - To connect a screen, [install a client](install/clients.md) and then [set up the device](install/devices.md).
    - The easiest hardware is the [Seeed reTerminal E-Series](hardware/seeed.md). It runs on a battery, and you flash it from your browser at [tesserae.ink/flash](https://tesserae.ink/flash).
    - Browse the [widget gallery](widgets/gallery.md) (37 built in) or the [community catalog](https://tesserae.ink/catalog/) to see what it can show.
    - Want your own widget? Start with [Build a widget with AI](dev/writing-a-widget.md).

## How it works

1. You build a dashboard in the editor. Each tile is a widget.
2. Tesserae draws it in a hidden browser (Chromium) at your screen's exact size.
3. It reduces the picture to the colours your screen can show.
4. The screen downloads the picture when it wakes up, or Tesserae pushes it to screens that stay on. The screen draws it and goes back to sleep.

Widgets, themes, fonts and device types are plugin folders. Drop one in and
Tesserae picks it up the next time it starts. The [widget contract](widgets.md)
has the details.

## Project status

Tesserae is a hobby project with one maintainer and a growing group of
contributors. Sixteen screens are confirmed working, and more are supported
but untested. The full list is in
[what's been tested](compatibility.md#whats-been-tested-on-real-hardware).

If you get it working on a new screen,
[open an issue or PR](https://github.com/dmellok/tesserae).
