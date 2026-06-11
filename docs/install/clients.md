# Install a client

The Tesserae server publishes frames; a **client** on the other end paints your
panel. Four reference clients live in their own repos, pick whichever matches
your hardware. Three of them subscribe to MQTT (Pi + ESP32); the fourth (TRMNL
/ KOReader-on-Kindle) polls the server over HTTP. All four use the same
device-registration flow described in [Set up a device](devices.md).

| Client | Transport | Default id | Best for |
|---|---|---|---|
| `tesserae-esp32-bin-client` | MQTT | `esp32` | Battery-powered Waveshare 13.3" Spectra 6 |
| `tesserae-pi-bin-client` | MQTT | `pi_bin` | Plugged-in Pimoroni Inky Impression (fastest path) |
| `tesserae-pi-png-client` | MQTT | `pi_png` | Any inky-supported panel (2/3/6/7 colour) |
| `tesserae-trmnl-client` | HTTP-pull | `trmnl` | TRMNL devices + KOReader-on-Kindle |

See [Screens & compatibility](../compatibility.md) for which renderer feeds each
client and what's been tested on real hardware.

## tesserae-esp32-bin-client

[:material-github: dmellok/tesserae-esp32-bin-client](https://github.com/dmellok/tesserae-esp32-bin-client)
· pairs with the `esp32_bin` renderer · default id `esp32`

Battery-powered **ESP32-S3-WROOM-2** firmware for the Waveshare 13.3" Spectra 6
panel. It deep-sleeps between wakes, subscribes to the retained
`tesserae/<device_id>/frame/bin` topic, and skips the download when the frame
hash hasn't changed. Months of battery life from a single Li-Po; the refresh
cadence is set by `sleep_interval_s` on `tesserae/<device_id>/config`. Device id
and Wi-Fi are configured through a captive portal (reachable afterward on the
LAN at `tesserae-<id>.local`).

!!! success "This is the maintainer's daily driver"
    The ESP32 + Waveshare 13.3" path runs in production daily. All three
    clients are [confirmed on real hardware](../compatibility.md#whats-been-tested-on-real-hardware).

## tesserae-pi-bin-client

[:material-github: dmellok/tesserae-pi-bin-client](https://github.com/dmellok/tesserae-pi-bin-client)
· pairs with the `pi_bin` renderer · default id `pi_bin`

A Raspberry-Pi-side Python daemon. It subscribes to
`tesserae/<device_id>/frame/bin` and writes the server's already-packed 4-bpp
buffer straight into the [`inky`](https://github.com/pimoroni/inky) library's
internal `_buf`, no PIL on the Pi paint path. This is the **fastest** path on a
Pimoroni Inky Impression (Spectra 6 / Waveshare E6, any of the four sizes,
auto-detected via the HAT EEPROM). The trade-off is a private-API dependency:
the `inky` version is pinned exactly.

## tesserae-pi-png-client

[:material-github: dmellok/tesserae-pi-png-client](https://github.com/dmellok/tesserae-pi-png-client)
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

## After flashing / pairing

On first run, a client publishes a heartbeat on `tesserae/<device-id>/status`
(MQTT clients) or calls `/api/setup` then `/api/display` (TRMNL).
Head to [Set up a device](devices.md) to register it, calibrate the
panel orientation, and bind a dashboard.

Running more than one panel? Flash / pair each with a distinct
`device-id`, every client gets its own topics or HTTP token, panel
size, and orientation.
