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

## Client guarantees

The protocol is designed for thin clients. Your firmware
**does not need**:

- A real-time clock. The server renders the frame with the device's
  local time baked in; the `/status` response also carries
  `local_time` for any client-side display or logging needs.
- An IANA timezone database or DST rule engine. The server already
  knows its timezone (set during onboarding) and returns local time
  / offset / DST flag in every `/status` response without you having
  to tell it anything. A heartbeat `tz` field exists as an *override*
  for the rare case where the device knows its zone better than the
  server (mobile / geo-aware firmware, multi-zone install), but the
  common case doesn't need it.
- An NTP client. The same `local_time` + `tz_offset_seconds`
  response fields are sufficient to set or align an external RTC
  if you have one.
- A schedule resolver. The server tells you exactly how long to
  sleep via `next_poll_s`. You don't compute "wake at 8am local" or
  "quiet hours"; the server returns the resolved seconds.
- Locale-aware date / time formatting. The server resolves
  everything time-related and hands you strings.

Future protocol changes get tested against this principle: if a
new feature would force the client to compute something time-,
locale-, or schedule-related, the server should do it instead.

What only the client knows (and must report):

- Battery (mV or %), RSSI, IP, MAC, firmware version
- Last paint timing / errors (`/log`)
- (Edge case) The device's IANA tz, *if and only if* the device
  knows its zone better than the server does. Most installs don't.

## Discovery and pairing

Every device gets a per-device bearer token (32+ random bytes,
constant-time compared). Tokens live in the instance manifest
server-side; clients store them in persistent flash / NVS / secrets.

A user never has to type a token by hand. The default flow is the
zero-touch one below (path A): firmware announces itself, the admin
clicks **Register** in the UI once, Tesserae returns the token on
the firmware's next discover poll. The pairing-code path (B) is a
fallback for environments where the admin can't be at the UI when
the device boots; the MAC auto-claim path (C) covers a re-flashed
device that needs to re-acquire its existing token.

!!! note "`kind` is a server-side plugin id"
    The `kind` field in every discover / register payload must match
    a folder under `devices/` on the server (or a community device
    kind installed via the marketplace). The kind plugin tells the
    server which renderer to compose for, what status fields the
    device reports, and what config knobs the admin UI exposes.

    Generic CircuitPython firmwares register as
    `kind: "circuitpython_generic"` (ships in the default install). A
    board-specific firmware can ship its own kind alongside (e.g.
    `circuitpython_pico_w_inky_73`); the server tolerates both
    in parallel. Adding a new kind is documented in
    [Adding hardware support](adding-hardware.md).

### A. Zero-touch discovery + admin approval

The default first-boot path. Firmware doesn't need a token, a code,
or any user-supplied credential at flash time, just the Tesserae
server URL.

1. Firmware boots, has no token. POSTs identity to
   `/api/v1/device/discover` (no auth):
   ```json
   {
     "device_id": "circuitpython_kitchen",
     "kind": "circuitpython_generic",
     "panel_w": 800,
     "panel_h": 480,
     "gamut": "mono",
     "fw_version": "0.1.0",
     "mac": "AA:BB:CC:DD:EE:FF"
   }
   ```

   `gamut` is optional; when supplied, it's persisted onto the
   auto-provisioned instance's panel block so the generic
   `circuitpython_generic` kind can serve every panel shape without a
   per-SKU manifest add (issue #41). Accepted values:

   | Value | Meaning | Persisted as |
   | --- | --- | --- |
   | `waveshare_e6` | Spectra 6, `.bin` packer target | `waveshare_e6` |
   | `spectra_6` | Spectra 6, semantic alias | `waveshare_e6` |
   | `inky_7colour` | Pimoroni Inky ACeP, `.bin` packer target | `inky_7colour` |
   | `acep_7colour` | ACeP, semantic alias | `inky_7colour` |
   | `mono` | 1-bit B/W | `mono` |
   | `rgb24` | Full 24-bit colour (LCD-hybrid) | `rgb24` |
   | `rgb16` | 16-bit colour (RGB565) | `rgb16` |

   Anything else falls back to `waveshare_e6` at persistence time so a
   corrupt payload can't strand the device with a nonsense panel.

   ??? note "Why do `waveshare_e6` and `inky_7colour` carry manufacturer
       names?"

       Same ink chemistry, different byte layouts on the wire.
       Waveshare's firmware and Pimoroni's `inky` library both target
       Spectra 6 / ACeP panels, but they pack the palette nibbles
       differently, Waveshare reserves nibbles 0x4 and 0x7 (so blue
       remaps to 0x5 and green to 0x6), Pimoroni uses the UC8159's
       native palette order verbatim. "Spectra 6" alone doesn't tell
       Tesserae's `.bin` packer which byte format to emit, so the
       canonical gamut value has to say "Spectra 6 done Waveshare-
       style" (`waveshare_e6`) or "ACeP done Inky-style"
       (`inky_7colour`). The chemistry-only aliases (`spectra_6`,
       `acep_7colour`) exist so a client can declare "I'm a Spectra 6
       panel" without knowing which packing scheme its driver expects;
       the alias resolves to the canonical form at persistence time.
       Renaming the canonical values to something chemistry-neutral
       (e.g. `spectra_6_pack_waveshare`) would need a migration for
       every existing config, so the awkward names stay for backwards
       compat and the aliases carry the semantic intent.
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

### B. 6-digit pairing code (fallback)

For environments where the admin can't be at the UI when the device
boots, sealed appliances, BLE provisioning, QR pre-print, kiosk
mode. The admin pre-mints a code, types it into the firmware setup
form, and the firmware self-registers without a follow-up admin
click.

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
     "gamut": "mono",
     "name": "Kitchen Display",
     "fw_version": "0.1.0",
     "mac": "AA:BB:CC:DD:EE:FF"
   }

   `gamut` follows the same rules as on `/discover` (v0.69.1). Both
   paths write the canonicalised value onto the auto-provisioned
   instance's panel block.
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

Headers: `ETag: "<digest>"`, `Content-Location: <absolute URL of the frame>`, `Cache-Control: no-cache`.

`Content-Location` mirrors the `url` field in the JSON body, and (unlike the body) is also returned on `304 Not Modified` responses. A client that boots without a cached URL (typical on non-e-ink panels that don't retain state through a power cycle) can read the header on `304` and re-fetch the image without having to have persisted the URL alongside its `ETag`. RFC 7231 §3.1.4.2 explicitly permits `Content-Location` on 304 for this purpose. The URL is also deterministic from the `render_id`: `<server-base>/renders/<render_id>.<format>`, so a client that persists `(server_base, render_id, format)` can reconstruct locally and skip reading the header entirely.

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

**Physical button wakes** — devices with hardware buttons add
`?button=<name>` to the frame request on a button-driven wake (the
name matches the device's `button_map`; conventionally `left`,
`right`, `refresh`). The server dispatches the mapped action
synchronously before selecting the frame, so the returned artefact
already reflects the new state on this same wake:

```
GET /api/v1/device/kitchen/frame?button=right
```

When bound to a rotation, the response also carries a `rotation`
block describing where the device is now:

```json
{
  "url": "...",
  "render_id": "...",
  "rotation": {
    "rotation_id": "kitchen_rotation",
    "step_index": 2,
    "step_page_id": "afternoon_calendar",
    "step_count": 4,
    "manual_override": true,
    "override_until": "2026-07-04T00:00:00+11:00"
  }
}
```

The `rotation` block is omitted when the device isn't bound to any
rotation. `manual_override: true` means a button press is currently
suppressing the time-based scheduler; the scheduler resumes at
`override_until` (server-side; the firmware doesn't need to check
this itself). On a `refresh` action the firmware should drop its
cached `ETag` before making the request so the server always returns
`200` with a full frame rather than `304`.

`304 Not Modified` — `If-None-Match` matches current digest. No body,
just `ETag: "<digest>"` and `Content-Location: <absolute URL>`. Re-paint
the previously-cached frame, or re-fetch the URL if your device didn't
retain the image bytes across a power cycle.

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

An optional `tz` field accepts an IANA name (e.g. `Europe/Berlin`)
when the device knows its zone better than the server (mobile
firmware, geo-aware setups, multi-zone installs). The common case
doesn't need to send it: the server already resolves into its
own configured timezone (set during onboarding) and returns the
local-time fields described below regardless. Invalid or
unrecognised names fall through silently.

**Button events** also flow through the status body on a button
wake, so a device that skips `/frame` (or hits it and then hits
`/status` seconds later) still gets the action dispatched. The
same event on both endpoints is deduped server-side against
`button_event_id`:

```json
{
  "battery_mv": 3850,
  "button": "right",
  "button_event_id": 42
}
```

`button` is the button name; `button_event_id` is a monotonically
increasing uint the firmware maintains per device (persist across
deep sleep, e.g. in NVS). Retries send the same id, and the server
treats any incoming id `<= last processed` as a duplicate. A
firmware without a monotonic counter can omit `button_event_id` and
fall back to the server's time-window debounce (default 3 seconds,
overridable via `settings.app.button_debounce_s`).

**Response** (`200 OK`):
```json
{
  "status": 200,
  "config": { "sleep_interval_s": 300 },
  "next_poll_s": 300,
  "server_time": 1687000060.123,
  "local_time": "2026-06-22T19:00:00+02:00",
  "tz": "Europe/Berlin",
  "tz_offset_seconds": 7200,
  "dst_active": true,
  "rotation": {
    "rotation_id": "kitchen_rotation",
    "step_index": 2,
    "step_page_id": "afternoon_calendar",
    "step_count": 4,
    "manual_override": true,
    "override_until": "2026-07-04T00:00:00+11:00"
  }
}
```

`rotation` is only present when the device is bound to a rotation;
its shape is identical to the block returned by `/frame` (see
above).

- `config`: current device config, schema declared in
  `devices/<kind>/device.json`. Apply server-side as the source of
  truth; don't trust local cache after a server-initiated change.
- `next_poll_s`: how long the firmware should sleep before the next
  status POST. Resolves in priority order: device-instance settings →
  kind-schema default → fallback 60.
- `server_time`: Unix epoch float (UTC). Useful for RTC sync on
  devices without a battery-backed clock.
- `local_time`: ISO 8601 string with offset suffix, resolved for the
  device's effective timezone (precedence below). Clients without an
  RTC just use this directly.
- `tz`: IANA name the server actually used to resolve. Resolution
  precedence: server's configured `settings.app.timezone` (set
  during onboarding, the common case), then the host's auto-detected
  TZ, then `UTC` as the last-resort. A heartbeat-sent `tz` overrides
  all of the above when present and valid; if a sent `tz` is
  garbled (e.g. `"Berlin"` without the continent prefix), the
  response echoes whichever zone was actually used so the client
  can detect its guess failed.
- `tz_offset_seconds`: integer offset from UTC in seconds (positive
  east of UTC). Lets RTC-equipped clients cache the rule and derive
  local time on intermediate wakes between heartbeats.
- `dst_active`: true if daylight saving was in effect at the moment
  the response was assembled. Informational; combined with the
  offset it lets a smarter client predict the next DST transition.

The local-time fields are always present in the response regardless
of whether the heartbeat sent `tz`. A pre-existing client that doesn't
know about them just ignores the extra keys; pay-for-what-you-use.

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
