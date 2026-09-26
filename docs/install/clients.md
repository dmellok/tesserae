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
| `tesserae-device-pi-bin` | MQTT | `pi_bin` | Plugged-in Pimoroni Inky Impression (fastest path) |
| `tesserae-device-pi-png` | REST / MQTT | `pi_png` | Any inky-supported panel (2/3/6/7 colour) |
| `tesserae-device-pico-bin` | MQTT | `pico_bin` | Pimoroni Pico-driven Inky Impression (4-bpp Spectra 6) |
| [`tesserae-koreader`](https://github.com/dmellok/tesserae-koreader) | REST | `koreader_client` | Jailbroken Kindles, Kobo, and other e-readers running KOReader; 16-level greyscale, sleeps between refreshes |
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

## tesserae-device-pi-bin

[:material-github: dmellok/tesserae-device-pi-bin](https://github.com/dmellok/tesserae-device-pi-bin)
· pairs with the `pi_bin` renderer · default id `pi_bin`

A Raspberry-Pi-side Python daemon. It subscribes to
`tesserae/<device_id>/frame/bin` and writes the server's already-packed 4-bpp
buffer straight into the [`inky`](https://github.com/pimoroni/inky) library's
internal `_buf`, no PIL on the Pi paint path. This is the **fastest** path on a
Pimoroni Inky Impression (Spectra 6 / Waveshare E6, any of the four sizes,
auto-detected via the HAT EEPROM). The trade-off is a private-API dependency:
the `inky` version is pinned exactly.

## tesserae-device-pi-png

[:material-github: dmellok/tesserae-device-pi-png](https://github.com/dmellok/tesserae-device-pi-png)
· pairs with the `pi_png` renderer · default id `pi_png`

The same Pi-side daemon shape, but it subscribes to
`tesserae/<device_id>/frame/png` and hands incoming PNGs to `inky`'s high-level
`set_image()`. That makes it work on **every panel the inky library supports** -
pHAT, wHAT, Impression 4"/5.7"/7.3"/13.3", in 2/3/6/7 colour. Quantising on the
Pi every frame makes it the slower of the two Pi paths, but it stays
wire-compatible with the inky-dash v3/v4 listener protocol.

## tesserae-koreader (Kindle, Kobo, any KOReader e-reader)

[:material-github: dmellok/tesserae-koreader](https://github.com/dmellok/tesserae-koreader)
· pairs with the `esp32_gray_bin` renderer · default id `koreader_client`

A KOReader plugin that makes an e-reader a Tesserae panel over the REST device
protocol, the same one the ESP32 firmware speaks. Kindles need a jailbreak to
run KOReader; Kobo, PocketBook and reMarkable do not.

1. Copy `tesserae.koplugin/` from the [latest release](https://github.com/dmellok/tesserae-koreader/releases) into `koreader/plugins/` and restart KOReader.
2. In Tesserae, **Settings → Devices → Add device → Pair with a code** to make a claim code.
3. On the reader, **Tools → Tesserae → Pair with a claim code…**, enter your server's address and the code.
4. Assign the new panel a dashboard, then **Tools → Tesserae → Show dashboard**.

The plugin reports the reader's real screen size when it pairs, so one kind
covers every model. Frames are packed at 4 bits per pixel (16 greys) and
decoded on the reader; an unchanged dashboard answers `304` and costs no
repaint. Where KOReader exposes the hardware alarm (Kobo, and Kindles on a
recent KOReader build) the reader sleeps between refreshes and wakes for each
one; elsewhere it stays awake on a timer with Wi-Fi off in between. The
refresh interval is the panel's **Refresh interval** on the device card.

Tesserae Cloud speaks the same protocol: enter `https://cloud.tesserae.ink`
as the server and a claim code from **Settings › Panels**.

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
- **KOReader on a Kindle (token-typed).** KOReader's `trmnl-display` plugin doesn't send a MAC, so you generate a short 5-character token via **Settings → Devices → Add device → TRMNL**, paste it into the plugin config, and the next `/api/setup` exchanges it for a permanent device-id + access token.

From there the device polls `/api/display` authenticated via the token. Errors / battery / RSSI heartbeat back via `POST /api/log` and surface on the device card.

The `trmnl_png` renderer fits your composition PNG to the device's
panel size and quantises to **1-bit black/white** with the dither of
your choice (Floyd-Steinberg, Atkinson, Jarvis, Stucki, Bayer 8×8,
halftone, crosshatch, or none). Dither + contrast live on the device
card so you can tune for the specific panel (Kindle Paperwhite 2,
the TRMNL device's panel, etc.).

The device reports its real screen on every poll (`png-width` /
`png-height` on KOReader, `Width` / `Height` on native TRMNL
firmware). Tesserae persists that as the panel's **native buffer** the
first time it hears from a client, so a landscape dashboard mounted on
a portrait e-reader is turned 90° server-side instead of being served
at the wrong aspect and stretched by the client's scaler. The
**Rotation** control on the device card is the turn from that reported
buffer: 0° shows your composition as composed, 90° / 270° lay it
across the screen's long axis (a landscape design filling a portrait
Kindle), and the flipped variants add 180° for an upside-down mount.

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
