# Install a client

The Tesserae server publishes frames; a **client** on the other end paints your
panel. The unified [`tesserae-device-firmware`](https://github.com/dmellok/tesserae-device-firmware)
is a single ESP32-S3 codebase covering the whole Seeed reTerminal E-Series, the
XIAO ePaper family, and the Waveshare 13.3" Spectra 6 + PhotoPainter 7.3"
boards, flashable in the browser at [tesserae.ink/flash](https://tesserae.ink/flash)
with no toolchain. Panels outside that range use one of the standalone clients
below. There's also an HTTP-pull path for TRMNL hardware and jailbroken Kindles
that doesn't need a Tesserae-built client at all. All paths use the same
device-registration flow described in [Set up a device](devices.md).

| Client | Transport | Default id | Best for |
|---|---|---|---|
| [`tesserae-device-firmware`](https://github.com/dmellok/tesserae-device-firmware) | REST | `esp32` | Seeed reTerminal E1001-E1004, XIAO EE02, XIAO EE03, XIAO 7.5", Waveshare 13.3" Spectra 6, PhotoPainter 7.3" (browser flash at [tesserae.ink/flash](https://tesserae.ink/flash)) |
| `tesserae-device-esp32-bw` | MQTT | `esp32_bw` | Waveshare 4.2" B/W (400×300, 1-bpp) and other small B/W panels |
| [`tesserae-device-pi`](https://github.com/dmellok/tesserae-device-pi) | REST / MQTT / cloud relay | `pi_device` | Any Pimoroni Inky panel on a Raspberry Pi: Impression (4", 5.7", 7.3", 13.3"), wHAT, pHAT |
| `tesserae-device-pico-bin` | MQTT | `pico_bin` | Pimoroni Pico-driven Inky Impression (4-bpp Spectra 6) |
| [`tesserae-device-inkyframe`](https://github.com/dmellok/tesserae-device-inkyframe) | REST | `inky_frame_73` | Pimoroni Inky Frame 7.3" (Spectra 6 on a Pico 2 W, or the earlier 7-colour ACeP); setup from a phone, signed over-the-air updates |
| [`tesserae-device-badger`](https://github.com/dmellok/tesserae-device-badger) | REST | `badger_<mac>` | Pimoroni Badger 2350 (264×176, 4 greys) and Badger 2040 W (296×128, mono), as an app in the badge's own launcher |
| [`tesserae-koreader`](https://github.com/dmellok/tesserae-koreader) | REST | `koreader_client` | Jailbroken Kindles, Kobo, and other e-readers running KOReader; 16-level greyscale, or colour on a Kaleido 3 reader (Kobo Libra Colour, Clara Colour), sleeps between refreshes |
| [TRMNL stock firmware](https://github.com/usetrmnl/trmnl-firmware) or [KOReader plugin](https://github.com/koreader/koreader) | HTTP-pull (BYOS) | `trmnl` | TRMNL devices + KOReader-on-Kindle |

See [Screens & compatibility](../compatibility.md) for which renderer feeds each
client and what's been tested on real hardware.

## tesserae-device-firmware

[:material-github: dmellok/tesserae-device-firmware](https://github.com/dmellok/tesserae-device-firmware)
· pairs with the `esp32_bin` renderer · default id `esp32`

Battery-powered **ESP32-S3** firmware, one codebase across every board Tesserae
supports natively on ESP32: the whole Seeed reTerminal E-Series (E1001, E1002,
E1003, E1004), the Seeed XIAO ePaper family (EE02, EE03, XIAO 7.5"), and the
Waveshare 13.3" Spectra 6 + PhotoPainter 7.3". Speaks Tesserae's v1 REST device
API and streams panel-native `.bin` frames the firmware paints without
on-device quantise or dither.

The easiest way to install it is the browser-based flasher at
[tesserae.ink/flash](https://tesserae.ink/flash): pick your board, hit Install,
no ESP-IDF toolchain required. Wi-Fi is provisioned on first boot via a
captive portal; device id and Tesserae server URL land in the same setup step.
Releases are built and published from CI on each Tesserae tag so the flasher
always serves the latest firmware.

!!! success "This is the maintainer's daily driver"
    The Waveshare 13.3" and reTerminal E-Series paths run in production daily.
    See [Screens & compatibility](../compatibility.md#whats-been-tested-on-real-hardware)
    for the full per-client real-hardware status table.

## tesserae-device-esp32-bw

[:material-github: dmellok/tesserae-device-esp32-bw](https://github.com/dmellok/tesserae-device-esp32-bw)
· pairs with the `esp32_bw_bin` renderer · default id `esp32_bw`

ESP32 firmware for the **Waveshare 4.2" B/W** e-paper panel (400×300, 1-bpp).
Subscribes to `tesserae/<device_id>/frame/bin` and consumes the 1-bpp packed
buffer the `esp32_bw_bin` renderer produces: exactly `width × height / 8`
bytes, 8 pixels per byte, MSB = leftmost, bit-set = white. Heartbeat publishes `panel_w` /
`panel_h` so other B/W resolutions (with width a multiple of 8) auto-fill the
Discovered card — point the firmware at a different size and Tesserae picks it
up without changes.

!!! warning "Untested in the wild as of v0.46.x"
    Wire contract verified by unit tests; no real-hardware paint confirmation
    yet. If you flash it, [open an issue](https://github.com/dmellok/tesserae/issues)
    with the result either way and the table on [Screens & compatibility](../compatibility.md)
    gets updated.

## tesserae-device-pi

[:material-github: dmellok/tesserae-device-pi](https://github.com/dmellok/tesserae-device-pi)
· pairs as `pi_client`, with the `pi_bin` or `pi_png` renderer · default id `pi_device`

A Python daemon for a **Raspberry Pi** driving any Pimoroni Inky panel. It reads
the panel from the HAT's EEPROM and reports it when it pairs, and the server
picks the frame format: a **Spectra 6 Impression** (4", 7.3", 13.3") gets the
`pi_bin` frame, already quantised and packed for the panel and written straight
to it, the fastest path; **every other panel** (the 7-colour Impressions, wHAT,
pHAT) gets the `pi_png` frame, which the Pi dithers to the panel's colours.

It speaks REST (the default, no broker), MQTT, or the cloud relay for a panel
that can't reach the server. Beyond painting frames it has push updates,
offline decks and albums, the front buttons, battery and sensor telemetry,
status screens on the panel, a settings page on your network, and signed
self-updates from **Settings → Firmware**.

Install it with the one-shot script in its repo, from PyPI
(`pip install tesserae-device-pi`), or on a fresh SD card with the
[cloud-init quickstart](../quickstart/pi-inky-cloud-init.md). The
[Raspberry Pi quickstart](../quickstart/pi-inky.md) walks through it.

It replaces the two earlier Pi clients, `tesserae-device-pi-bin` and
`tesserae-device-pi-png`, whose repos are archived. Installing it on a Pi that
ran either picks up the old config and keeps the device's id and pairing.
A device paired by an old client keeps its `pi_bin_client` or `pi_png_client`
kind and keeps getting the same frames.

## tesserae-koreader (Kindle, Kobo, any KOReader e-reader)

[:material-github: dmellok/tesserae-koreader](https://github.com/dmellok/tesserae-koreader)
· pairs with the `esp32_gray_bin` renderer (`kaleido_png` on a colour reader) · default id `koreader_client`

A KOReader plugin that makes an e-reader a Tesserae panel over the REST device
protocol, the same one the ESP32 firmware speaks. Kindles need a jailbreak to
run KOReader; Kobo, PocketBook and reMarkable do not.

1. Copy `tesserae.koplugin/` from the [latest release](https://github.com/dmellok/tesserae-koreader/releases) into `koreader/plugins/` and restart KOReader.
2. In Tesserae, **Settings → Devices → Add device → REST API → Issue pairing code** to make a claim code.
3. On the reader, **Tools → Tesserae → Pair with a claim code…**, enter your server's address and the code.
4. Assign the new panel a dashboard, then **Tools → Tesserae → Show dashboard**.

The plugin reports the reader's real screen size when it pairs, so one kind
covers every model. Frames are packed at 4 bits per pixel (16 greys) and
decoded on the reader; an unchanged dashboard answers `304` and costs no
repaint. A colour reader (E Ink Kaleido 3: Kobo Libra Colour, Clara Colour)
pairs with the `kaleido3` gamut when KOReader's colour rendering is on, and
receives a colour PNG dithered to 16 levels per channel (4096 colours)
instead of the packed grey frame; with colour rendering off it pairs as a
greyscale reader. Not yet confirmed on a colour reader. Where KOReader exposes the hardware alarm (Kobo, and Kindles on a
recent KOReader build) the reader sleeps between refreshes and wakes for each
one; elsewhere it stays awake on a timer with Wi-Fi off in between. The
refresh interval is the panel's **Refresh interval** on the device card.

Tesserae Cloud speaks the same protocol: enter `https://cloud.tesserae.ink`
as the server and a claim code from **Settings › Panels**.

## tesserae-device-inkyframe (Pimoroni Inky Frame 7.3")

[:material-github: dmellok/tesserae-device-inkyframe](https://github.com/dmellok/tesserae-device-inkyframe)
· pairs with the `pico_bin` renderer · kind `pimoroni_inky_frame_73` (Spectra 6) or `pimoroni_inky_frame_73_acep`

Pico SDK firmware for the Inky Frame 7.3", the Tesserae-maintained fork of
[partridgeworks/tesserae.inkyframe](https://github.com/partridgeworks/tesserae.inkyframe).
The server packs each frame into the panel's own 4-bpp palette, so the frame
streams it straight to the glass.

1. Flash it from Chrome or Edge at
   [tesserae.ink/flash/inky-frame](https://tesserae.ink/flash/inky-frame): pick
   your Frame, connect it over USB and click **Connect & flash**. A new Frame, or
   one running MicroPython, needs BOOTSEL held on the Pico while you tap Reset
   first; a Frame already on Tesserae is rebooted into its bootloader for you.
   On a Pico 2 W it also writes the A/B partition table that over-the-air
   updates need. To build it yourself instead, use PlatformIO as the repo README
   describes.
2. Power the frame and join the `Tesserae-Setup-XXXX` network it opens (password
   `tesserae`). Enter your Wi-Fi and server address on the setup page.
3. The frame announces itself: click **Register** on it in **Settings → Devices**.

The five front buttons send button events with their letters (`a`-`e`).
Signed over-the-air updates arrive from **Settings → Firmware** once the A/B
partition table has been written over USB (the web flasher does this). Confirmed on a Pico 2 W Frame with
the Spectra 6 panel.

## tesserae-device-badger (Pimoroni Badger 2350 and 2040 W)

[:material-github: dmellok/tesserae-device-badger](https://github.com/dmellok/tesserae-device-badger)
· Badger 2350 pairs with `esp32_gray2_bin` (4 greys), Badger 2040 W with `esp32_bw_bin` (mono)

An app for the badge's stock launcher (Badgeware on the 2350, Badger OS on the
2040 W), so the badge keeps its other apps and Tesserae is one more entry in
its menu.

1. Copy the app onto the badge: on the 2350, double-tap RESET and copy the
   `tesserae` folder into `apps/`; on the 2040 W, copy it with Thonny or
   `mpremote`. The repo README has the exact files.
2. Copy `config/tesserae_config.py` beside the badge's Wi-Fi settings and set
   `SERVER_URL` to your server's IP address (these boards can't resolve
   `.local` names).
3. Launch **Tesserae** from the menu and click **Register** on the badge in
   **Settings → Devices**.

A/B/C/up/down send button events with their own keys. The config file sets the
activity light, how long the badge stays awake after a refresh, its refresh
speed and what each button sends. The 2350 is confirmed on hardware (Badgeware
1.27.0); the 2040 W has not run on hardware yet.

## TRMNL / KOReader (HTTP-pull)

[:material-github: usetrmnl/trmnl-firmware](https://github.com/usetrmnl/trmnl-firmware)
or [:material-github: koreader/koreader](https://github.com/koreader/koreader) (`trmnl-display` plugin)
· pairs with the `trmnl_png` renderer · default id `trmnl`

TRMNL devices run TRMNL's stock firmware (which speaks the BYOS protocol
Tesserae implements server-side); Kindles can use KOReader's `trmnl-display`
plugin here too, though the `tesserae-koreader` plugin above is the better
fit for an e-reader (16 greys, hardware wake, claim-code pairing). Either
way, the device polls `GET /api/display` on a schedule, the response
carries the next frame URL and the next-poll interval. No broker required,
handy when you want a panel that "just talks to the internet".

Pairing has two paths:

- **TRMNL devices (auto-provision, no admin action).** Seeed-built hardware running the TRMNL firmware sends its MAC in the `Id` header on its first `/api/setup` call; Tesserae creates a device record and mints an access token automatically. The new card appears in **Settings → Devices** within seconds. Auto-provision was wired up in 0.44.1.
- **KOReader on a Kindle (token-typed).** KOReader's `trmnl-display` plugin doesn't send a MAC, so you generate a short 5-character token by adding a device of the **TRMNL client** kind under **Settings → Devices → Add device**, paste it into the plugin config, and the next `/api/setup` exchanges it for a permanent device-id + access token.

From there the device polls `/api/display` authenticated via the token. Errors / battery / RSSI heartbeat back via `POST /api/log` and surface on the device card.

The `trmnl_png` renderer fits your composition PNG to the device's
panel size and quantises to **1-bit black/white** with the dither of
your choice (Floyd-Steinberg, Atkinson, Jarvis, Stucki, Bayer 8×8,
halftone, crosshatch, or none). Dither + contrast live on the device
card so you can tune for the specific panel (Kindle Paperwhite 2,
the TRMNL device's panel, etc.).

The device reports its real screen on every poll (`png-width` /
`png-height` on KOReader, `Width` / `Height` on native TRMNL
firmware). Tesserae records that as the panel's **native buffer** the
first time a client reports one, so a landscape dashboard mounted on
a portrait e-reader is turned 90° server-side instead of being served
at the wrong aspect and stretched by the client's scaler. Later polls
don't change it, so a second client on the same token with a different
screen gets frames shaped for the first one; if you move the token to
a different reader, set the new size on the device card's panel
settings. The **Rotation** control on the device card is the turn from
that reported buffer: 0° shows your composition as composed, 90° / 270°
lay it across the screen's long axis (a landscape design filling a
portrait Kindle), and the flipped variants add 180° for an upside-down
mount.

## Browser-based "client" (no firmware, no native client)

For old tablets, jailbroken Kindles in browser mode, kiosk PCs, or any
screen with a URL bar but no Tesserae native client, every registered
device exposes an **auto-refreshing mirror page** at:

```
http://<your-tesserae-host>:8765/mirror/<device_id>
```

It's a tiny self-contained HTML page that embeds the device's stable
preview image (`/preview/<device_id>.png`) with a
`<meta http-equiv="refresh">` so the panel keeps re-pulling on its
own. Bookmark it in Safari on an iPad and walk away. Both the
`/preview/` and `/mirror/` URLs are linked from the device card in
**Settings → Devices** so you don't need to memorise the pattern.

The default refresh cadence is the device's configured
`sleep_interval_s` setting (so it matches what an actual firmware
client would do); override with `?refresh=N` (seconds, clamped to
`[5, 86400]`). For a sideways-mounted iPad showing a portrait panel,
`?rotate=90` (or `180` / `270`) applies a CSS rotation client-side so
the content lands the right way up.

No auth required on LAN (same bypass list as `/preview/` and
`/renders/`). Equivalent in spirit to TRMNL's `/mirror` endpoint.

## After flashing / pairing

On first run, a client publishes a heartbeat on `tesserae/<device-id>/status`
(MQTT clients) or calls `/api/setup` then `/api/display` (TRMNL).
Head to [Set up a device](devices.md) to register it, calibrate the
panel orientation, and bind a dashboard.

Running more than one panel? Flash / pair each with a distinct
`device-id`, every client gets its own topics or HTTP token, panel
size, and orientation.
