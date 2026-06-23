# Install a client

The Tesserae server publishes frames; a **client** on the other end paints your
panel. Six reference clients live in their own repos, pick whichever matches
your hardware. Five of them subscribe to MQTT (Pi + ESP32); the sixth (TRMNL
/ KOReader-on-Kindle) polls the server over HTTP. All six use the same
device-registration flow described in [Set up a device](devices.md).

| Client | Transport | Default id | Best for |
|---|---|---|---|
| `tesserae-device-esp32-bin` | MQTT | `esp32` | Battery-powered Waveshare 13.3" Spectra 6 |
| `tesserae-device-photopainter-7.3-bin` | MQTT | `esp32` | Battery-powered Waveshare 7.3" PhotoPainter (Spectra 6) |
| `tesserae-device-esp32-bw` | MQTT | `esp32_bw` | Waveshare 4.2" B/W (400×300, 1-bpp) and other small B/W panels |
| `tesserae-device-pi-bin` | MQTT | `pi_bin` | Plugged-in Pimoroni Inky Impression (fastest path) |
| `tesserae-device-pi-png` | MQTT | `pi_png` | Any inky-supported panel (2/3/6/7 colour) |
| `tesserae-trmnl-client` | HTTP-pull | `trmnl` | TRMNL devices + KOReader-on-Kindle |

See [Screens & compatibility](../compatibility.md) for which renderer feeds each
client and what's been tested on real hardware.

## tesserae-device-esp32-bin

[:material-github: dmellok/tesserae-device-esp32-bin](https://github.com/dmellok/tesserae-device-esp32-bin)
· pairs with the `esp32_bin` renderer · default id `esp32`

Battery-powered **ESP32-S3-WROOM-2** firmware for the Waveshare 13.3" Spectra 6
panel. It deep-sleeps between wakes, subscribes to the retained
`tesserae/<device_id>/frame/bin` topic, and skips the download when the frame
hash hasn't changed. Months of battery life from a single Li-Po; the refresh
cadence is set by `sleep_interval_s` on `tesserae/<device_id>/config`. Device id
and Wi-Fi are configured through a captive portal (reachable afterward on the
LAN at `tesserae-<id>.local`).

!!! success "This is the maintainer's daily driver"
    The ESP32 + Waveshare 13.3" path runs in production daily. See
    [Screens & compatibility](../compatibility.md#whats-been-tested-on-real-hardware)
    for the full per-client real-hardware status table.

## tesserae-device-photopainter-7.3-bin

[:material-github: dmellok/tesserae-device-photopainter-7.3-bin](https://github.com/dmellok/tesserae-device-photopainter-7.3-bin)
· pairs with the `esp32_bin` renderer · default id `esp32`

Battery-powered **ESP32-S3** firmware for the **Waveshare 7.3" PhotoPainter**
(landscape-native 800×480 Spectra 6). Same MQTT wire contract and deep-sleep
pattern as the 13.3" client; the difference is the panel size and the SPI init
sequence baked for the PhotoPainter board. Pick `waveshare_photopainter_7_3` as
the panel preset when registering the device so the server-side renderer uses
the right row stride. Confirmed on real hardware.

## tesserae-device-esp32-bw

[:material-github: dmellok/tesserae-device-esp32-bw](https://github.com/dmellok/tesserae-device-esp32-bw)
· pairs with the `esp32_bw_bin` renderer · default id `esp32_bw`

ESP32 firmware for the **Waveshare 4.2" B/W** e-paper panel (400×300, 1-bpp).
Same MQTT topic shape as the 13.3" client (subscribes to
`tesserae/<device_id>/frame/bin`), but consumes the 1-bpp packed buffer the
`esp32_bw_bin` renderer produces: exactly `width × height / 8` bytes, 8 pixels
per byte, MSB = leftmost, bit-set = white. Heartbeat publishes `panel_w` /
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

## TRMNL / KOReader (HTTP-pull)

[:material-github: dmellok/tesserae-trmnl-client](https://github.com/dmellok/tesserae-trmnl-client)
· pairs with the `trmnl_png` renderer · default id `trmnl`

For TRMNL devices and Kindle e-readers running KOReader's
`trmnl-display` plugin. Instead of subscribing to MQTT, the device polls
`GET /api/display` on a schedule (the response carries the next frame
URL and the next-poll interval). No broker required, handy when you
want a panel that "just talks to the internet".

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
