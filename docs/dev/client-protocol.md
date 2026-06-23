# Client protocol

Authoritative spec for anyone building a new Tesserae client (battery
microcontroller, CircuitPython device, Pi, kiosk PC, anything that
can paint pixels from an HTTP / MQTT feed). Maps the full surface a
client touches: registration, auth, frame fetch, heartbeat, config,
and frame formats. Matches the current `main` branch; cross-links to
implementation files for ground-truth lookups.

If you're picking a path:

- **Battery / embedded** (CircuitPython, MicroPython, custom MCU
  firmware): see [REST transport](../install/rest-transport.md) for
  the broker-less variant, or use MQTT if your stack already speaks
  it. The MAC-match auto-claim flow under
  [Discovery & pairing](#discovery-and-pairing) keeps zero-touch
  re-pairing working after firmware updates.
- **Always-on (Pi, kiosk)**: MQTT is simpler. The retained
  `tesserae/<id>/frame/<fmt>` topic delivers a JSON envelope with a
  download URL whenever a new frame ships.

## Architecture in 30 seconds

The server renders dashboards to immutable, content-addressed
artefacts (PNG, packed `.bin`, TRMNL 1-bpp PNG). When a new frame is
ready, the server either:

1. **MQTT push**: publishes a JSON envelope with the frame URL to
   `tesserae/<device_id>/frame/<fmt>` (retained). The client wakes /
   reacts, fetches the URL, paints, sleeps. Fan-out across multiple
   panels is one publish per renderer.
2. **REST pull**: the client polls
   `GET /api/v1/device/<id>/frame`. The server responds with the
   same JSON envelope (or 304 with an `ETag`, or 204 if no frame
   exists yet). Works without a broker, which matters for
   stripped-down CircuitPython setups, demo Codespaces, etc.

The two paths share the same envelope shape, same auth, same frame
artefacts. Picking one isn't a permanent commitment.

## Discovery and pairing

Every device gets a per-device bearer token (32+ random bytes,
constant-time compared). Tokens live in the instance manifest
server-side; clients store them in persistent flash / NVS / secrets.
There are three ways to acquire one.

### A. Zero-touch discovery + admin approval

For first-time setup when the device doesn't yet have a token.

1. Firmware boots, has no token. POSTs identity to
   `/api/v1/device/discover` (no auth):
   ```json
   {
     "device_id": "circuitpython_kitchen",
     "kind": "circuitpython_generic",
     "panel_w": 800,
     "panel_h": 480,
     "fw_version": "0.1.0",
     "mac": "AA:BB:CC:DD:EE:FF"
   }
   ```
2. Server caches the announcement. Returns:
   ```json
   {
     "status": 200,
     "registered": false,
     "discovered": true,
     "retry_after_s": 30,
     "next_step": "Open Settings -> Devices and click Register on this device's card"
   }
   ```
3. The device appears under the "Discovered" strip in Settings →
   Devices. Admin clicks **Register**, optionally renames / picks the
   panel preset.
4. Firmware retries the POST every `retry_after_s`. Once the
   admin-side register has happened and the MAC matches, the server
   returns a token:
   ```json
   {
     "status": 200,
     "registered": true,
     "device_token": "eyJ0eXAiOi…",
     "device_id": "circuitpython_kitchen",
     "config": { "sleep_interval_s": 300 },
     "server_time": 1687000060
   }
   ```
5. Firmware saves the token, drops into the steady-state loop.

### B. 6-digit pairing code

For environments where the admin can read a code into the device
ahead of time (BLE provisioning, QR pre-print, kiosk mode).

1. Admin generates a code in Settings → Devices → "Pair new device"
   (server route: `POST /api/v1/device/admin/pairing/issue`,
   15-minute expiry, single-use).
2. Device POSTs `/api/v1/device/register` with the code in the
   `X-Pairing-Code` header and a manifest:
   ```http
   POST /api/v1/device/register HTTP/1.1
   X-Pairing-Code: 482917
   Content-Type: application/json

   {
     "device_id": "circuitpython_kitchen",
     "kind": "circuitpython_generic",
     "panel_w": 800,
     "panel_h": 480,
     "name": "Kitchen Display",
     "fw_version": "0.1.0",
     "mac": "AA:BB:CC:DD:EE:FF"
   }
   ```
3. Server validates, creates the instance, returns a token:
   ```json
   {
     "status": 201,
     "device_token": "eyJ0eXAiOi…",
     "server_time": 1687000060,
     "config": { "sleep_interval_s": 300 },
     "reused_existing": false
   }
   ```

If the `device_id` already exists, the response is idempotent
(`status: 200`, `reused_existing: true`) with the **existing** token
returned. This keeps re-pairing safe across firmware re-flashes.

### C. MAC auto-claim after re-flash

If a previously-registered device boots with a fresh filesystem (lost
its stored token), it can re-acquire by hitting `/discover` with its
MAC. The MAC-match path skips the admin-approval step and hands back
the existing token immediately. Same endpoint as path A, just
short-circuits when the MAC is recognised.

### Rate limiting

`POST /api/v1/device/register` and `POST /api/v1/device/discover` are
rate-limited per source IP: 10 failed attempts per 60 seconds.
Successful registers release the bucket (so an attacker has to burn a
fresh pairing code per attempt), failed registers and all discovers
consume.

## Authentication

Every authenticated REST call carries the device token in one of two
equivalent headers:

```http
Authorization: Bearer eyJ0eXAiOi…
```

or:

```http
X-Tesserae-Token: eyJ0eXAiOi…
```

The token grants access only to `/api/v1/device/<own_id>/*` paths.
Hitting another device's endpoint with a valid-but-wrong token
returns `403`. Missing or malformed token returns `401`.

Frame downloads (`/renders/<file>`, `/preview/<id>.png`) do **not**
require auth. They're on the LAN-bypass list because the URLs are
already random + opaque (SHA-256 digests for `/renders/`) and the
server's primary auth boundary is the admin UI, not the asset CDN.
This makes the painting loop trivial: hit the URL, get the bytes.

## REST endpoints

CORS is enabled on every device-facing route
(`Access-Control-Allow-Origin: *`); auth is the boundary, not origin.

### `GET /api/v1/device/<id>/frame`

Fetch metadata for the current frame.

**Headers**:

| Header | Required | Notes |
|---|---|---|
| `Authorization: Bearer <token>` or `X-Tesserae-Token: <token>` | yes | Either works |
| `If-None-Match: "<digest>"` | optional | Echo the `ETag` from the last successful fetch; server returns `304` if unchanged |

**Responses**:

`200 OK` — new frame is available. Example for a `.bin` frame
(packed-palette format, native ESP32 / Pico clients):
```json
{
  "url": "http://192.168.1.50:8765/renders/abc123def456.bin",
  "format": "bin",
  "panel_w": 1200,
  "panel_h": 1600,
  "render_id": "abc123def456",
  "renderer_id": "esp32_bin__kitchen"
}
```

Example for a `.png` frame (pi_png renderer, ships extra hints for
client-side fit):
```json
{
  "url": "http://192.168.1.50:8765/renders/abc123def456.png",
  "format": "png",
  "panel_w": 1200,
  "panel_h": 1600,
  "render_id": "abc123def456",
  "renderer_id": "pi_png__kitchen",
  "rotate": 0,
  "scale": "fit",
  "bg": "white",
  "saturation": 0.5
}
```

Headers: `ETag: "<digest>"`, `Cache-Control: no-cache`.

**Field-by-field breakdown:**

| Field | Type | Always present | Source | Meaning |
|---|---|---|---|---|
| `url` | string | yes | rendered artefact path | Absolute URL of the frame to download. Same-origin as the server (request scheme + host), no auth required to fetch. Filename is `<render_id>.<format>`. |
| `format` | string | yes | file extension | `"bin"`, `"png"`, etc. Tells the client which decoder to use. Matches the topic suffix on the MQTT side (`frame/<format>`). |
| `panel_w` | int | yes | device manifest | Panel width in pixels (composer orientation; usually landscape for `.bin` clients, can be portrait for `.png`). Sanity-check this matches your display before painting. |
| `panel_h` | int | yes | device manifest | Panel height in pixels, same caveats as `panel_w`. |
| `render_id` | string | yes | SHA-256 truncated to 16 hex chars | Content-addressed digest of the artefact. Stable across identical renders, so two consecutive `/frame` calls returning the same digest mean nothing changed. Use as the value for `If-None-Match` on your next request. Also serves as the `ETag` header. |
| `renderer_id` | string | yes | `<renderer_kind>__<device_id>` | Which renderer produced this frame. Useful for log lines; not needed for paint. |
| `rotate` | int (0–3) | **PNG only** | per-device setting | Number of 90° clockwise quarter-turns the client should apply *after* decoding the PNG. Already-baked into `.bin` output server-side, so it's not sent for bin. |
| `scale` | string | **PNG only** | per-device setting | One of `"fit"` (letterbox), `"fill"` (crop to cover), `"stretch"` (no aspect preservation), `"blur"` (letterbox with a blurred cover behind), `"center"` (1:1 paste). Hint for client-side fit when source dims don't match panel dims. |
| `bg` | string | **PNG only** | per-device setting | Letterbox background colour (e.g. `"white"`, `"black"`) when `scale: "fit"`. Ignored for other `scale` modes. |
| `saturation` | float | **PNG only** | per-device setting | 0.0–1.5+ saturation multiplier the client should apply during quantization. The pi_png default is `0.5` (de-saturate for muted e-ink look); pi_bin / esp32_bin bake this server-side at the kind's default (1.4 / 1.0). |

**Things to know:**

- The `.bin` renderers only ship `{url, format, panel_w, panel_h, render_id, renderer_id}`. All geometry / colour transforms are done server-side before packing, so the client just unpacks nibbles and writes to SPI.
- The `.png` renderer ships the four extra hints (`rotate`, `scale`, `bg`, `saturation`) because the PNG is in composition orientation; the client decides how to land it on the actual panel.
- Other renderers may ship their own extras in the future. The contract is: anything not listed in the "always present" set above is renderer-specific, and **clients should ignore unknown fields** so new renderers don't break older firmware. Note that things like the TRMNL renderer's `dither` and `contrast` knobs are *device-side settings* (configured per instance in Settings → Devices, applied server-side before encoding) rather than envelope fields, the wire payload stays minimal.

`304 Not Modified` — `If-None-Match` matches current digest. No body,
just `ETag: "<digest>"`. Re-paint the previously-cached frame.

`204 No Content` — server has no frame for this device yet (e.g.
brand-new device, no dashboard bound). Body:
```json
{ "status": 204, "error": "no frame rendered yet for this device" }
```
Sleep and retry; admin needs to bind a dashboard.

### `POST /api/v1/device/<id>/status`

Heartbeat. Send after every paint cycle (or on a fixed interval if
always-on).

**Headers**: same auth as `/frame`.

**Body** (JSON, all fields optional, device-kind-specific):
```json
{
  "battery_mv": 3850,
  "battery_pct": 45,
  "rssi": -72,
  "ip": "192.168.1.100",
  "sleep_until": 1687000000.5,
  "next_sleep_s": 3600
}
```

The server merges with the previous heartbeat so partial payloads
preserve last-known fields. If `battery_mv` is sent but `battery_pct`
isn't, the server derives `battery_pct` from a linear LiPo curve
(3300 mV = 0 %, 4200 mV = 100 %). `sleep_until` / `next_sleep_s`
feed the JIT scheduler (so smart-sync can render *just before*
wake instead of on a fixed cadence).

**Response** (`200 OK`):
```json
{
  "status": 200,
  "config": { "sleep_interval_s": 300 },
  "next_poll_s": 300,
  "server_time": 1687000060.123
}
```

- `config`: current device config, schema declared in
  `devices/<kind>/device.json`. Apply server-side as the source of
  truth; don't trust local cache after a server-initiated change.
- `next_poll_s`: how long the firmware should sleep before the next
  status POST. Resolves in priority order: device-instance settings →
  kind-schema default → fallback 60.
- `server_time`: Unix epoch float. Useful for RTC sync on devices
  without a battery-backed clock.

### `POST /api/v1/device/<id>/log` (optional)

Forward a client log line into the server's Events tab. Useful for
remote debugging without a serial cable.

**Body** (JSON, every field optional):
```json
{ "level": "error", "msg": "SPI write timeout" }
```

**Response** (`200 OK`):
```json
{ "status": 200, "bytes": 127 }
```

Entries surface in Settings → Events with `type: device`,
`source: <device_id>`, `target: client_log`. No retention guarantees;
the Events store caps at 500 device rows.

### `GET /renders/<filename>` (no auth)

Download a frame artefact. Filenames are SHA-256 digests with the
format extension appended (`abc123def456.bin`,
`abc123def456.png`). Content-type set by extension.

**Query**: `?w=<width>` returns a downscaled thumbnail (cached
server-side; only applies to image formats). Height is auto-capped at
`8 × width` to prevent DoS via crafted aspect ratios.

### `GET /preview/<id>.png` (no auth)

Stable URL for the latest pre-renderer composition PNG of a given
device. Useful for HA generic_camera entities, image embeds in
dashboards, or "what does this device currently look like" tooling.
Headers: `Cache-Control: no-store, max-age=0`, so consumers always
get the latest. Returns `404` if no frame has rendered yet.

## MQTT topics

All topics are namespaced under `tesserae/<device_id>/`.

| Topic | Direction | Payload | Retain | QoS | Trigger |
|---|---|---|---|---|---|
| `tesserae/<id>/frame/bin` | server → device | JSON envelope, see below | **yes** | 1 | New `.bin` frame published (pico_bin, esp32, pi_bin renderers) |
| `tesserae/<id>/frame/png` | server → device | JSON envelope | no | 1 | New `.png` frame (pi_png renderer) |
| `tesserae/<id>/frame/trmnl` | server → device | JSON envelope | no | 1 | New TRMNL 1-bpp PNG (trmnl renderer) |
| `tesserae/<id>/status` | device → server | JSON, see [heartbeat](#post-apiv1deviceidstatus) | typically yes | 1 | Device after paint / interval |
| `tesserae/<id>/config` | server → device | JSON, kind's `config_schema` | **yes** | 1 | Admin updates device config |

**Frame envelope** (one example, the PNG variant):
```json
{
  "url": "http://192.168.1.50:8765/renders/abc123def456.png",
  "rotate": 3,
  "scale": "fit",
  "bg": "white",
  "saturation": 0.5
}
```

The minimum is `{ "url": "<frame URL>" }`. Anything extra is
renderer-hinted and safe to ignore if your firmware doesn't act on
it. Subscribe to `+/frame/<your_format>` if you want all devices, or
`<your_id>/frame/+` if you want every format for one device (rare).

Devices that publish their `status` with `retain: true` get free
last-will + reconnect semantics: a newly-attached subscriber sees the
most recent heartbeat without waiting for the next one.

## Frame formats

### `.bin` — 4-bpp packed binary

Used by `pi_bin`, `esp32_bin`, `pico_bin` renderers. Exactly
`(width × height) / 2` bytes, no header, no padding.

- Scanline order: row 0 first, left-to-right within each row.
- High nibble = even column index (0, 2, 4, …), low nibble = odd.
- 1200 × 1600 panel → 960 000 bytes.
- Nibble values 0–15 are palette indices. Two standard palettes:
  - **waveshare_e6**: 6-color Spectra 6 (default; Waveshare + ESP32).
  - **inky_7colour**: Pimoroni Inky 7-color (matches the `inky`
    library's `pal` array, so you can `display.set_pixel(x, y,
    nibble)` directly).

The renderer applies rotation, letterboxing, underscan, and dithering
server-side, so the firmware just unpacks nibbles and pushes to the
controller.

Reference: [`renderers/esp32_bin/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/esp32_bin/renderer.py),
[`renderers/pi_bin/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/pi_bin/renderer.py),
[`app/quantizer.py`](https://github.com/dmellok/tesserae/blob/main/app/quantizer.py).

### `.png` — standard PNG

Used by `pi_png` and `trmnl` renderers. Just decode with whatever
library your platform provides (`adafruit_imageload`, Pillow,
`stb_image`, etc.) and blit. The envelope's `rotate` / `scale` / `bg`
fields are hints for client-side fit; if your display library handles
that natively, ignore them.

The TRMNL variant is dithered to 1-bit B/W (every pixel either 0 or
255) server-side; the dither algorithm + contrast curve are device
settings (Settings → Devices → trmnl_client) rather than envelope
fields, so the wire payload is just `{"url": "..."}` like the other
bin/png variants.

Reference: [`renderers/pi_png/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/pi_png/renderer.py),
[`renderers/trmnl/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/trmnl/renderer.py).

## Configuration push

Each device kind declares a `config_schema` in
`devices/<kind>/device.json`. Admins see those fields rendered as a
form in Settings → Devices. When they save:

1. Server validates via the kind's `validate_config(payload) ->
   (bool, str|None)` Python hook. Out-of-range values get a UI error
   before they hit the wire.
2. Server writes the validated config to its settings store.
3. If the device kind declares a `config_topic`, server publishes the
   config (retained, QoS 1) so the next time the device wakes it sees
   the new values without waiting for the next status round-trip.
4. Devices on REST-only transport (no MQTT) pick up the change on the
   next `/status` POST via the `config` field in the response.

Standard sleep config:
```json
{ "sleep_interval_s": 300 }
```
Validate client-side too. The ESP32 reference bounds are
`30 ≤ sleep_interval_s ≤ 604800` (7 days). A bad value sneaking
through here means a flat battery.

## Reference implementations

In-tree clients you can read as worked examples:

- [`devices/pico_bin_client/`](https://github.com/dmellok/tesserae/tree/main/devices/pico_bin_client)
  — RP2350 Pico Plus 2 firmware (C++), wake / fetch / paint / sleep
  over retained MQTT, `.bin` format, Pimoroni Spectra 6 panel. The
  closest in-tree analog for a CircuitPython port.
- [`devices/esp32_client/`](https://github.com/dmellok/tesserae/tree/main/devices/esp32_client)
  — battle-tested ESP32 firmware, same shape as pico_bin but with
  more transports and field history.
- [`devices/pi_bin_client/`](https://github.com/dmellok/tesserae/tree/main/devices/pi_bin_client)
  and [`devices/pi_png_client/`](https://github.com/dmellok/tesserae/tree/main/devices/pi_png_client)
  — Python clients for always-on Pi, simpler because they don't deep
  sleep.
- [`devices/trmnl_client/`](https://github.com/dmellok/tesserae/tree/main/devices/trmnl_client)
  — HTTP-pull only; no MQTT, no bearer auth (TRMNL's own protocol).
  Useful if you want to see what the broker-less variant looks like
  at the wire.

## Steady-state loop sketch

A minimum-viable client, ignoring boot / pairing / retry:

```
while True:
    frame = GET /api/v1/device/<id>/frame  (with If-None-Match: <last_etag>)
    if status == 304:
        sleep next_poll_s  # nothing to paint
        continue
    if status == 204:
        sleep 60  # admin hasn't bound a dashboard yet
        continue
    bytes = GET frame.url  (no auth)
    paint(bytes, format=frame.format)
    last_etag = frame.render_id
    status = POST /api/v1/device/<id>/status  {battery_pct, rssi, ip}
    apply_config(status.config)  # if changed
    sleep status.next_poll_s
```

That's the whole loop. Everything else (smart-sync, dithering hints,
TLS, mDNS) is opt-in polish.

## Questions, edge cases, suggestions

This protocol surface is the one place where third-party hardware
support gets to be one codebase instead of N. If you're building a
client and the spec leaves anything ambiguous, please open a thread
under [GitHub Discussions](https://github.com/dmellok/tesserae/discussions)
and we'll firm it up here.
