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
     "rotation": 0,
     "gamut": "mono",
     "name": "Kitchen Display",
     "fw_version": "0.1.0",
     "mac": "AA:BB:CC:DD:EE:FF"
   }
   ```

   `device_id` and `mac` are required here. The MAC is what step 4
   matches on to hand the token back, so an announce without one is
   rejected with `400` instead of landing in the Discovered strip and
   polling forever on `registered: false` (issue #226). A client that
   has no MAC to report pairs with a 6-digit code instead (path B).

   Send the real MAC, not a stand-in. `null`, `"None"` (a client's own
   null formatted into the body), `00:00:00:00:00:00` and
   `ff:ff:ff:ff:ff:ff` are all rejected the same way an absent MAC is,
   because two devices sending the same placeholder would resolve to
   one instance and the second would be handed the first one's token.
   The same rule applies to the `mac` on `/register`: a placeholder is
   dropped rather than stored, so pairing succeeds but that device
   can't auto-claim later.

   `name` is optional on both `/discover` and `/register`: a suggested
   human-readable display name for the device. On `/register` it's
   applied directly to the new instance. On `/discover` it prefills
   the Register card's "Display name" field in Settings → Devices, so
   the admin sees the suggestion and can edit or clear it before
   registering; the admin's choice always wins, and later announces
   never rename an already-registered device. Omit it freely, the
   device id is used as the display fallback.

   `rotation` is optional, one of `0`, `90`, `180`, `270`, and it
   changes how `panel_w` / `panel_h` are read (issue #200).

   Send it and you're saying: **`panel_w` / `panel_h` are my
   framebuffer, and `rotation` is the turn from that buffer to the
   dashboard.** `0` means the dashboard is composed at the dims you
   reported. `90` and `270` compose it transposed, so a client
   reporting `1200 x 1920` gets a `1920 x 1200` dashboard rotated back
   onto its `1200 x 1920` buffer before the image is written. `180`
   keeps the dims and turns the image half a turn for an upside-down
   mount. Either way the file you download is shaped like the panel you
   declared, and the server also echoes `native_w` / `native_h` back in
   the `/frame` envelope so you can assert on it.

   Omit it and the older reading applies: `panel_w` / `panel_h` are the
   dashboard canvas itself, and the server only infers the aspect from
   them (taller than wide registers as portrait). That's what every
   client written before this field existed means, so nothing changes
   for firmware that doesn't send it, including a client doing its own
   rotation after download.

   The value is read once, when the device instance is created. After
   that the panel belongs to the operator: remount it and change the
   **Rotation** dropdown in Settings → Devices. Re-sending a different
   `rotation` does nothing on its own, because a `/register` for an
   existing device id (and a `/discover` that matches a known MAC)
   returns the existing device untouched rather than rebuilding its
   panel. `/status` doesn't accept the field at all. To have a client
   redeclare its geometry from scratch, delete the device and pair it
   again.

   The dropdown's degrees are measured the same way this field is, as
   the turn from your framebuffer, so a client that declared `0` reads
   back as 0° even on a portrait-native panel.

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
   | `bwr_3` | 3-colour B/W/Red tri-colour e-ink | `bwr_3` |
   | `gray_4` | 4-level greyscale ramp (2-bit, no highlight) | `gray_4` |
   | `bwry_4` | 4-colour B/W/Red/Yellow (PicPak-class 4.2" panels) | `bwry_4` |
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

   `format` is optional and selects the wire container the server
   renders into. It only applies to kinds that offer more than one
   renderer; `circuitpython_generic` offers two:

   | Value | Renderer | Notes |
   | --- | --- | --- |
   | `png` (default) | `circuitpython_png` | Indexed PNG. Smallest download. Needs `zlib.decompress` on the client. |
   | `bmp` | `circuitpython_bmp` | Uncompressed indexed BMP. No `zlib` on the client at all. |

   Declare `"format": "bmp"` when the board can't spare a contiguous
   decode buffer. CircuitPython's `zlib.decompress` is one-shot (no
   streaming inflate), so decoding even a small indexed PNG needs the
   whole inflated image in RAM alongside the `displayio.Bitmap`; on a
   Pico W class board (~110K free SRAM) that exhausts or fragments
   memory. An uncompressed BMP sidesteps it: `adafruit_imageload` reads
   it row by row with `file.read` / `seek`, so peak RAM is the
   framebuffer plus a small row buffer. Boards with headroom (ESP32-S3
   etc.) should keep the default PNG, which is a few times smaller on the
   wire (the server writes palette-mode BMP at 8 bits per pixel). The
   value is resolved to a renderer by matching its file extension, so the
   `format` you declare is exactly the `format` field you'll get back
   from `/frame`. An unknown or absent value leaves the kind's default
   (PNG) renderer in place.

   **Switching format later.** You don't need to delete and re-create a
   device to change its format. Re-declare `format` on any later
   `/register` or `/discover` (MAC-match) call and the server moves the
   device to the matching renderer in place, then invalidates its cached
   frame so `/frame` returns `204` until the next push repaints it in the
   new format (rather than serving a stale frame in the old one). A
   `format` that's absent, unknown, or already active is a no-op.
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
     "device_id_changed": false,
     "config": { "sleep_interval_s": 300 },
     "server_time": 1687000060
   }
   ```

   **The returned `device_id` wins.** On this path the MAC is the
   identity, so if it matches an instance registered under a different
   id than the one you announced, you're handed the stored id and its
   token, and your announced id is ignored. That's what lets a re-flashed
   board whose settings were wiped re-acquire its token, but it means a
   client that keeps using its own id afterwards is talking about a
   device the server doesn't have. Adopt the returned value.

   The claim response says which case you're in two ways (v0.318.0):
   `device_id_changed` in the body, plus the same answer in headers so a
   client can branch without parsing JSON:

   ```http
   X-Tesserae-Device-Id: circuitpython_kitchen
   X-Tesserae-Device-Id-Changed: false
   ```

   Both headers are sent on every successful claim, so an absent header
   means an older server rather than "nothing changed". When the id did
   change, the body also carries `announced_device_id` (what you sent),
   and the server logs the mismatch at WARNING so it's visible from the
   operator's side too.

   To move a device to a new id, delete it in Settings → Devices and pair
   again. Announcing a new id does not rename the instance: the id is the
   operator's, and pages, schedules, and bindings hang off it.
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
     "rotation": 0,
     "gamut": "mono",
     "name": "Kitchen Display",
     "fw_version": "0.1.0",
     "mac": "AA:BB:CC:DD:EE:FF"
   }

   `gamut` follows the same rules as on `/discover` (v0.69.1). Both
   paths write the canonicalised value onto the auto-provisioned
   instance's panel block. `format` (`png` / `bmp`) and `rotation` are
   honoured here too, same rules as on `/discover`.
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

Two routes additionally accept the token as a `?k=<token>` query
parameter: [`frame.bmp`](#get-apiv1deviceidframebmp) and
[`frames.json`](#get-apiv1deviceidframesjson). Both exist for clients
that fetch a URL from a handler they don't control and so cannot set
headers at all. Everything else is header-only on purpose.

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
| `panel_w` | int | yes | device manifest | Logical panel width in pixels: the composition orientation the dashboard is rendered at, which is what Settings calls the logical panel width. Usually landscape for `.bin` clients, can be portrait for `.png`. Not necessarily your framebuffer; see `native_w` below. |
| `panel_h` | int | yes | device manifest | Logical panel height in pixels, same caveats as `panel_w`. |
| `native_w` | int | no | device manifest | Framebuffer width in pixels, present when the device declared one (a `rotation` at registration, or a hardware manifest's `native_w` / `native_h` block). This is the shape the downloaded file actually has, so it's the one to assert against your display buffer. |
| `native_h` | int | no | device manifest | Framebuffer height in pixels, same rules as `native_w`. |
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
`?button=<name>&button_event_id=<uint>` to the frame request on a
button-driven wake (the
name matches the device's `button_map`; conventionally `left`,
`right`, `refresh`). The server dispatches the mapped action
synchronously before selecting the frame, so the returned artefact
already reflects the new state on this same wake:

```
GET /api/v1/device/kitchen/frame?button=right&button_event_id=42
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

`button_event_id` is the same monotonic counter reported in `/status`.
Retries reuse the same value. The legacy query name `event` is accepted as a
fallback for firmware through v1.5.0, but new clients should use the canonical
name.

The `rotation` block is omitted when the device isn't bound to any
rotation. `manual_override: true` means a button press is currently
suppressing the time-based scheduler; the scheduler resumes at
`override_until` (server-side; the firmware doesn't need to check
this itself). A `fetch_latest` action bypasses a matching
`If-None-Match` for that response and returns the latest existing
artefact with `200`, without triggering a render or push. On a
`refresh` action the firmware should drop its
cached `ETag` before making the request so the server always returns
`200` with a full frame rather than `304`.

#### Touch wakes

Touch-capable devices report a raw stroke on the frame request instead
of a button name. This is transport-agnostic and firmware-agnostic: the
client stays dumb — it sends start point, end point, and duration, and
the server does all gesture classification (tap vs directional swipe)
and hit-testing against the frame's touch region map. Any client that
can read a touch coordinate and make an HTTP request can implement it
(ESP-IDF, CircuitPython, a Pi, a browser shim); nothing about the region
geometry lives on the device.

```
GET /api/v1/device/kitchen/frame?touch_x0=512&touch_y0=300&touch_x1=512&touch_y1=180&touch_ms=240&touch_digest=abc123def456
```

| Param | Required | Notes |
|---|---|---|
| `touch_x0`, `touch_y0` | yes | Stroke start, in the served frame's pixel space (as downloaded, before any device-side rotation/mirror) |
| `touch_x1`, `touch_y1` | optional | Stroke end; omit (or repeat the start) for a plain tap |
| `touch_ms` | optional | Stroke duration in milliseconds |
| `touch_digest` | yes | The frame digest currently displayed (your `ETag`, quotes optional). A stroke against a frame the server has since replaced is dropped, never dispatched against the wrong content |
| `touch_event_id` | optional | Monotonic wake-event counter, shares the button counter; used for retry dedup |

The resolved action dispatches synchronously before the frame lookup
(same contract as button wakes), so a `page:` / rotate action's
repaint comes back on this same response. A stale digest, a stroke on
a non-interactive area, or a frame with no touch regions all degrade
to a plain frame poll; the wake is never an error.

Continuously powered clients that poll `/frame` on a timer can send
the stroke out-of-band instead via `POST /api/v1/device/<id>/tap`
with body `{"x0": …, "y0": …, "x1"?: …, "y1"?: …, "duration_ms"?: …,
"digest": "<etag>", "event_id"?: …}`. The response is always `200`
with `{"outcome": "dispatched" | "noop" | "webhook_dispatched" |
"stale" | "no_frame" | "no_target" | "deduped" | "error", "gesture":
"tap" | "swipe_up" | …, "action_spec": …, "description": …,
"rotation"?: {…}}`; in every non-dispatched case the client's correct
move is the same, re-poll `/frame` and carry on.

Touch regions are declared in dashboard markup (`data-on-tap` /
`data-on-swipe` / `data-on-slide` attributes on cells, widget markup,
or code-element DOM) and extracted server-side at render time; the
firmware never needs region geometry. Slider regions map the stroke's
end point to an absolute 0-100 value along an axis (vertical fills
upward) and the server substitutes it into the region's action, so a
press-drag-lift sets a light level in one stroke; the firmware still
just reports raw coordinates. Home Assistant service-call actions run
synchronously on the server and re-push the current page, so the frame
returned on the same wake reflects the new state. The `outcome` values
on `POST /tap` grow `ha_dispatched` / `ha_failed` / `blocked`
accordingly.

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

Heartbeat. Send once per wake (or on a fixed interval if always-on).
Either side of the paint works; see [when to send
it](#when-to-send-the-heartbeat) below.

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
preserve last-known fields.

#### Recognised fields

Unknown fields are tolerated: they ride along in the parsed body and
render as plain key/value rows on the device card, so sending extra
costs nothing. These are the ones that actually drive something. None
of them are device-kind-specific on the server; a kind's `parse_status`
only decides type coercion, the consumers below key off the names.

| Field | Type | What it drives |
| --- | --- | --- |
| `battery_pct` | number, 0-100 | Battery tile, battery history, HA sensor, `tesserae_status` widget |
| `battery_mv` | int, mV | Same. Sent alone, the server derives `battery_pct` from a linear LiPo curve (3300 mV = 0 %, 4200 mV = 100 %) |
| `rssi` | int, negative dBm | Signal tile, HA `signal_strength` sensor. **Not** `rssi_dbm`, which is the Companion app API's response field name |
| `ip` | string | Firmware tile, HA IP address sensor |
| `mac` / `model` | string | Device card |
| `fw_version` | string | Device card; also the version the OTA rollout view compares against |
| `temperature_c` | number, °C | Environment tile, `tesserae_status` widget |
| `humidity_pct` | number, 0-100 | Same |
| `sleep_until` | number, epoch seconds | Smart-sync wake prediction |
| `next_sleep_s` | int | Same, for firmware that can't compute an absolute wake time |
| `tz` | IANA name | Server resolves `local_time` / `tz_offset_seconds` / `dst_active` back to you |
| `button` / `button_event_id` | string / uint | Button dispatch and dedup, see below |
| `deck_page_id` | string | The deck page actually on glass, see [deck cache sync](#deck-cache-sync-on-device-frame-cache) |
| `panel_w` / `panel_h` | int | Reported panel geometry |

`voltage`, `uptime`, `uptime_s` and `last_paint` are recognised only in
the sense that they're marked volatile, so a change in one of them
alone doesn't write an event-log row on every beat. Nothing else
interprets them.

The capability blocks (`ota`, `deck`, `collection`, `overlay`, `proto`)
are nested objects rather than scalar fields, and they're sticky: the
server carries the last advertised value forward across beats that omit
it, so you only need to send them once per boot.

#### When to send the heartbeat

Nothing in the protocol requires the post-paint order, and posting
`/status` **before** `/frame` is a reasonable choice: the response
carries `config`, `next_poll_s`, `server_time` / `local_time` and the
`rotation` block, so you apply fresh config and sync your clock before
the paint rather than after.

Two beats per wake (one on connect, one just before deep sleep) is
supported, but mind the window: heartbeats arriving within **10 seconds**
of each other are treated as the same wake event, and only then is the
second one kept out of the smart-sync confidence counter. An e-ink paint
often takes 15 to 30 seconds, so a pair straddling the paint falls
outside that window, the second beat reads as a *new* wake, its offset
against the standing prediction lands at roughly minus the whole sleep
interval, and confidence resets on every cycle. Such a device never
reaches trusted. If you can't keep both beats inside 10 seconds, send
**one**, just before sleep, carrying `next_sleep_s`. That gets you the
correct prediction anchor and the freshest readings in a single request;
the cost is that `config` from the response applies on your next wake
rather than the current one.

What reordering does *not* do is make a device-reported value current
in the image you paint. `/frame` doesn't render on demand, it hands
back the most recently rendered artefact for the device. The frame you
paint on wake *N* was composed before wake *N*, from the heartbeat you
sent on wake *N-1*, so a battery percentage baked into the image is one
cycle old whichever side of the paint you report it from. To narrow
that gap:

- Publish `sleep_until` or `next_sleep_s` so smart sync JIT-renders
  roughly `smart_sync_lead_s` before the predicted wake, rather than on
  a fixed cadence unrelated to when the device is actually awake.
- For genuinely live values, use the [hybrid render](#overlay-specs-hybrid-render-mode)
  path: a device advertising the `overlay` capability gets an
  `overlay_values` document on its `/status` response, computed *after*
  that heartbeat is ingested, so a slot reflects the reading it just
  posted. The device composites it over the frame locally.
- Otherwise, draw device-local values (battery, signal) on-device from
  what you already hold in RAM, rather than expecting them in the
  server-composed frame.

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
treats an id equal to the last processed one as a duplicate (a
*lower* id is taken as a counter restart after a power cycle and
still dispatches). A firmware without a monotonic counter can omit
`button_event_id` and fall back to the server's time-window
debounce (default 3 seconds, overridable via
`settings.app.button_debounce_s`).

This status-body form is also the **only** button path for a
relay-paired panel (its frame poll terminates at the relay, so
frame query parameters never reach the server). Over the relay,
repeat `button` + `button_event_id` unchanged on every status post
of the same wake, and treat the event id as required; see "Buttons
over the relay" in `docs/relay/contract.md`.

**Response** (`200 OK`):
```json
{
  "status": 200,
  "config": { "sleep_interval_s": 300 },
  "next_poll_s": 300,
  "server_time": 1687000060,
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
  status POST. Read it as *"new content is probably available then"*.
  The device's configured interval (device-instance settings →
  kind-schema default → fallback 60) is the ceiling; within it, a
  projected content change pulls the value earlier so you land on the
  new frame rather than on an arbitrary point of a fixed grid. See
  [Scheduling and polling](#scheduling-and-polling).
- `server_time`: Unix epoch **integer** (UTC, whole seconds). Useful
  for RTC sync on devices without a battery-backed clock. Sent as an
  integer, not a float: a MicroPython / CircuitPython client parses a
  JSON float into a single-precision float32, whose resolution near the
  current epoch is ~128s, so a float would round to the nearest ~2
  minutes. An integer literal parses exactly on a longint-capable client.
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

### `GET /api/v1/device/<id>/frame.bmp`

The current frame as an uncompressed indexed BMP, bytes in the body. A
single-request alternative to the `/frame` envelope, for clients that
cannot walk the JSON-then-fetch hop: an e-reader pulling a dashboard as
its sleep screen typically does so from a declarative download handler
that makes exactly one request and follows no redirects.

**Response** (`200 OK`): `Content-Type: image/bmp`, the complete image,
sized to the device's panel dimensions and rotation. An `ETag` is sent
but no conditional request is required.

BMP is served regardless of the device's configured frame format, so
adding this route to an existing device does not change what its normal
client receives; the same composition is simply re-transformed into a
second, decoder-free container. The body is capped at 1 MB, which an
800x480 4-bit BMP (~192 KB) clears comfortably; a panel large enough to
exceed it gets a `500` naming the measured size rather than a body its
client would fail to download.

**This route never answers `2xx` without a complete body.** Clients of
this shape stream the response to a temporary file and rename it over the
live sleep screen on any `2xx`, so a `204`, or a `200` with an empty
body, would replace a working screen with a zero-byte file. Accordingly:

| Situation | Status |
| --- | --- |
| No frame rendered yet | `404` (**not** `204`, unlike `/frame`) |
| Missing or bad token | `401` |
| Token belongs to another device | `403` |
| Conversion failed | `5xx` |

**Auth** is the usual device token, and this route additionally accepts
it as `?k=<token>`, because the download handlers this exists for own the
request and cannot attach headers. Headers still win when both are
present. The query form is opt-in per route rather than a global change:
a token in a query string lands in access logs, proxy logs and browser
history in a way a header does not, so it is confined to the routes with
no alternative. Plain `http://` on the LAN is fine; nothing here
redirects, so an `https` → `http` hop never arises.

### `GET /api/v1/device/<id>/frames.json`

A small list of pullable frames, for an on-device picker. Lets a reader
fetch a dashboard on demand without a computer in the loop.

**Response** (`200 OK`):
```json
{
  "items": [
    {
      "id": "current",
      "title": "Current dashboard",
      "subtitle": "Download as sleep screen",
      "url": "http://<server>/api/v1/device/<id>/frame.bmp?k=<token>"
    }
  ]
}
```

`items` is always a JSON array; clients cast it to one, so an object
would silently yield an empty picker. Each `url` is absolute and directly
downloadable under the same one-request, no-redirect rule as above, and
carries the token as a query parameter so the on-device downloader can
use it as-is. Display strings are ASCII: the e-ink UI fonts these land on
have no guaranteed coverage for arrows or symbols, and a missing glyph is
a tofu box on a screen with no way to report it.

Same auth as `/frame.bmp`, query parameter included. The list is
available before any frame exists; it describes what can be pulled, and
the download itself `404`s until there is something to serve.

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

## Deck cache sync (on-device frame cache)

> Being generalized: [Frame-cache collections](frame-cache.md) is the
> producer-neutral form of this cache (Decks + offline photo albums share one
> content-addressed frame cache). The deck-cache capability and endpoints below
> stay unchanged for compatibility. Discussion #177.

Decks are small navigable graphs of pre-rendered pages (Settings →
Decks). By default the server does all the navigation: a button/touch
event arrives, the server resolves the graph and serves the target
frame on the same wake. A client with local storage (an SD card slot,
typically) can opt into caching the deck's frames and navigating
locally instead: wake, read card, paint, radio off. Everything below is
optional; clients that never advertise the capability see no change on
any endpoint.

### Capability

Advertise in the `/register` and every `/status` body, **only while the
storage is actually present and usable**:

```json
{ "deck_cache": { "schema": 1, "capacity_bytes": 7900000 } }
```

The capability is treated as current-state per heartbeat, not sticky: a
beat that omits it (card pulled, mount failed) withdraws it, and the
server stops offering deck syncs until it reappears.

### Sync flow

1. When a deck is bound to the device and the capability was advertised
   on that beat, the `/status` response carries the deck's current
   version: `"deck": { "version": "<16-hex>" }`.
2. Version differs from the one you cached → `GET
   /api/v1/device/<id>/deck` (Bearer token). `200` returns the
   manifest; `204` means no deck is bound (drop local nav state):

   ```json
   {
     "status": 200,
     "deck_id": "kitchen_deck",
     "version": "a1b2c3d4e5f60718",
     "entry_page_id": "overview",
     "pages": [
       {
         "page_id": "overview",
         "digest": "0f1e2d3c4b5a6978",
         "bytes": 96000,
         "ttl_s": 900,
         "links": [
           { "button": "right", "zone": null, "target_page_id": "calendar" },
           { "button": null,
             "zone": { "x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5 },
             "target_page_id": "weather" }
         ]
       }
     ]
   }
   ```

   Zones are normalised 0..1 rects; scale by your panel dims to
   hit-test. The manifest warms (renders) cold pages on demand, so the
   first call after a deck edit can take a few seconds.

   Additive manifest fields (firmware should tolerate their absence
   and ignore unknown fields):

   * Link entries may carry ``"swipe": "left"|"right"|"up"|"down"``
     instead of ``button``/``zone``: classify the stroke direction and
     hit-test it against these for local nav. Defaults follow paging
     convention (swipe left pulls the NEXT page in; swipe right goes
     back), with explicit authored swipes taking precedence.
   * Pages may carry ``"cache": false`` when the device's advertised
     ``capacity_bytes`` can't fit the whole deck: don't store that
     frame (its links remain valid; navigating to it falls back to a
     network fetch). Cache priority is ring-distance from the home
     card, so home and its neighbours always fit first.
   * A top-level ``"home": {"page_id": ..., "timeout_s": N}`` block
     means: after ``timeout_s`` with no button press or tap, navigate
     back to the home page locally (paint from cache, report as
     usual). Absent = never return automatically. The server enforces
     the same rule for devices that navigate server-side.

   Where a page's graph is silent, the manifest synthesizes defaults so
   a deck authored without an explicit graph still navigates locally:
   `left` / `right` button links to the previous / next page in deck
   order (wrapping), and on touch panels left-half / right-half tap
   zones to prev / next, the zones only when the page has no explicit
   zones and no markup touch regions of its own (a default zone must
   never swallow a page's real tap targets). Explicit graph links
   always win over defaults.

   Cache hygiene note: page frame digests are content-addressed, so a
   page whose rendered bytes don't change keeps its digest across
   background refreshes and the deck version stays stable. Pages that
   embed volatile content (clocks, "last updated" stamps) produce a
   new digest on every re-render and will re-sync on every refresh
   cadence; keep such elements off deck pages you want to cache well.
3. Diff digests against your cache, fetch only what's missing via `GET
   /api/v1/device/<id>/deck/frame/<digest>` (raw frame bytes, identical
   format to `/frame` for your kind; `ETag` + immutable cache headers).
   `404` means your manifest is stale: re-fetch it. Delete cached files
   the manifest no longer references.
4. Verify before painting: exact `bytes` length AND digest. The digest
   is the first 16 hex chars of `sha256(frame bytes)`.

### Local navigation and reporting

When a button/touch wake matches a link on your current page and the
target frame is cached, verified, and younger than its `ttl_s`: paint
it locally and **do not** send the event to the server. Instead report
what you painted on your next contact: `deck_page_id` in the `/status`
body, or `?deck_page_id=` on a same-wake `/frame` poll. The server
updates its nav position from the report, so its UI and any later
server-side navigation resume from the page actually on glass.

Every other case (no link match, frame missing/stale/corrupt, version
mismatch, no card) falls back to today's behaviour: send the event and
fetch `/frame`. Any event the server receives is handled server-side as
normal; the handoff works because a locally-handled event is simply
never sent.

`ttl_s` expiry on the page currently displayed does not force a network
wake by itself; scheduled wakes handle refresh. It only disqualifies a
cached frame from being served for a navigation.

## Overlay specs (hybrid render mode)

Touch boards with fast partial refresh (IT8951-class first) can apply
server-declared primitives over the served frame locally, giving
sub-second tap feedback without a network round trip. The server frame
stays the source of truth; the overlay is cosmetic and optimistic.

Advertise `"overlay": {"schema": 1, "max_targets": 32}` in
register/status bodies. The capability is sticky (a firmware property,
unlike `deck_cache`) and persists across server restarts; it gates the
values document, the `/status` piggybacks, and (schema 2) patch
documents. `max_targets` (additive, firmware v1.9+) declares the
device's target buffer for forward compatibility with the protocol-v2
interaction manifest, which inherits the same budget discipline.

**Removed in v0.196** (protocol v2, `docs/protocol-v2-touch.md`): the
schema-1 overlay spec endpoint
`GET /api/v1/device/<id>/frame/overlay/<frame_digest>` no longer exists.
Firmware probing it receives a plain `404`, which this contract has
always defined as "feature off for this frame" — v1 clients degrade
gracefully to dispatch-without-echo. Tap-echo targets and slot geometry
return as the protocol-v2 interaction manifest. Until then the
coordinate conventions below still apply to everything that remains
(values, atlases, patch documents): all coordinates are in the wire
framebuffer's pixel space; the server performs every transform
(rotation, flip, scaling, underscan) and the firmware applies rects
verbatim.

### Value slots and glyph atlases

Widgets opt into live value text by annotating an element with
`data-overlay-key="ha:<entity_id>"` (optional `data-overlay-suffix`,
e.g. a degree sign). At render time the slot's box, alignment, font
size, and weight are extracted alongside the touch regions into the
composition sidecar.

**Delivery note (v0.196):** slot geometry and atlas references used to
ship inside the schema-1 overlay spec, which has been removed; they
return as `text` regions in the protocol-v2 interaction manifest. The
values document below and the atlas store keep working unchanged and
are what the manifest will reference.

Atlases are rasterized server-side through the same browser and fonts
as the composition (Inter, at the slot's exact size and weight), so
blitted text is pixel-consistent with the baked-in render. The strip is
packed at exactly `max(glyph.x + glyph.w)` pixels wide, 4bpp gray, high
nibble = left pixel; fetch by digest with immutable caching at
`GET /api/v1/device/<id>/frame/overlay/atlas/<digest>`. The glyph
charset covers numeric values (`0-9 . , : - + % ° C F` and space); a
character outside it renders as a mean-width blank on the device.

### Values document

`GET /api/v1/device/<id>/frame/data?digest=<frame_digest>` (Bearer):

```json
{"seq": 1753305600123, "values": {"ha:sensor.temp": "21.4°"}}
```

`seq` is wall time in **milliseconds** (int64). Prior to v0.195 it was
seconds, which made two value changes inside one second dedup to a
single repaint under newest-wins.

Values are pre-formatted display strings (state + declared suffix,
clipped to 47 chars); the firmware applies zero formatting. Poll only
while awake in the touch-linger window (1-2 s cadence). The same
document also arrives as `overlay_values` on `/status` responses for
any device whose STICKY capability includes `overlay` or `proto >= 2`
(v0.199: a beat that omits the capability, or a pure-v2 firmware that
only sends `proto`, keeps the envelopes flowing), so slots refresh on
every normal wake for free; both sources are interchangeable, newest
`seq` wins, equal `seq` = no repaint. `404` = no values for this frame
(unknown digest, no slots, or Home Assistant not configured); treat as
values-off. An entity that is `unknown` / `unavailable` is simply
absent from `values`, and the firmware keeps showing whatever the
baked-in render had.

Slot keys accept an optional attribute path
(`data-overlay-key="ha:light.desk:attributes.brightness"`), resolved
against the entity's full state object. A slot may also declare
`data-overlay-map='{"on": "1", "off": "0"}'` to rewrite raw values into
strings the numeric glyph charset can draw; unmapped values fall back
to the raw string. Slots inside code-element sandboxes are collected
too: the sandbox posts them out alongside its touch regions and the
compose page mirrors them for the extraction walker, so a full-bleed
code dashboard gets the same live values a widget does.

### Patch documents (schema 2)

Boards that advertise `"overlay": {"schema": 2, ...}` also receive
**post-action frame patches**: after a touch action changes external
state (a Home Assistant service call), the server re-renders the page
headless, diffs the new render against the frame the device is
showing, and stages the changed rects. The device's frame digest
never changes (its `/frame` poll keeps 304ing); the patches ARE the
repaint. A burst of taps coalesces server-side
(`app.touch_patch_debounce_s`, default 0.4 s after the last action)
into one document.

The same mechanism also carries **periodic small changes** (v0.195):
when a scheduled or push-triggered re-render of the page a schema-2
REST device is showing differs only a little (a header clock tick),
the server stages patches on the current digest instead of minting a
new frame. The digest therefore stays stable for long stretches — no
full flash per clock tick, and a tap fired mid-render can't be
invalidated by digest churn. Big changes (page edits, renderer setting
changes, cross-page pushes, diffs past the caps) still mint a new
digest and arrive as a normal full frame; the firmware needs no new
behaviour for either case. Diffing runs in composition space (before
per-renderer dithering), so error-diffusion dither noise never inflates
a patch; the blob content is still byte-exact with the new artifact.

The document arrives under `patches` on `GET /frame/data` (poll at
1-2 s cadence during the touch-linger window) and as `overlay_patches`
on `/status` responses:

```json
{
  "schema": 2,
  "frame_digest": "<the frame the device is showing>",
  "seq": 1753430000123,
  "format": "fb-rect",
  "url": "/api/v1/device/<id>/frame/patch/<blob_digest>",
  "bytes": 34816,
  "rects": [
    {"x": 128, "y": 640, "w": 300, "h": 90, "offset": 0, "len": 13500}
  ]
}
```

Contract:

* **Anchoring.** Apply only when `frame_digest` equals the digest of
  the frame currently on glass. The server already refuses to hand out
  a document for any other digest, so a mismatch means the client
  raced a frame change: drop the document and do a normal frame poll.
* **`format: "fb-rect"`.** The blob is per-rect row data in the exact
  packing of the frame file itself (for the E1003's 4bpp gray:
  `w * 4 / 8` bytes per row, high nibble = left pixel). `x` and `w`
  always land on byte boundaries, so each rect is a straight row-wise
  memcpy into the stored framebuffer at `(x, y)`, then a partial
  refresh of that rect. Pixels are never interpreted.
* **`seq`, newest-wins.** Strictly increasing per device (survives
  server restarts). A document with `seq` <= the last applied one is a
  no-op; a newer one replaces any pending document outright. Rects in
  one document may overlap previously patched areas; painting them
  again is correct.
* **Tap-echo supersede.** A patch rect overlapping an active tap-echo
  inversion clears it (the patch carries the truth the echo was
  predicting). Patch partial-refreshes count toward the same ghosting
  hygiene counter as echo inverts: full-quality repaint after ~8.
* **Caps.** At most 12 rects and 256 KB of blob per document; the
  server falls back to a normal full frame beyond that, so a client
  never needs to handle more.
* **Blob fetch.** Content-addressed, immutable caching. A `404` on the
  blob means the document was superseded mid-fetch: drop it and poll
  `/frame/data` again.

Devices that only advertise schema 1 (or none) still converge: the
server re-pushes the page asynchronously ~3 s after the last action
(`app.touch_repush_debounce_s`), and the next `/frame` poll or MQTT
publish delivers a normal full frame.

## Protocol v2 (device-owned touch)

Advertise `"proto": {"v": 2}` in register/status bodies (sticky,
persisted). A v2 device owns hit testing and immediate feedback; the
full client contract, including the manifest schema, byte layouts, tier
model, and reconnect rules, is the v2 firmware specification
(`docs/protocol-v2-touch.md` and its implementation prompt). Server
surfaces:

- `GET /api/v1/device/<id>/frame/manifest?digest=<frame>` — the
  interaction manifest: wire-space region rects with stable ids,
  gesture + tier + feedback declarations, and device-rendered text
  regions with glyph atlases. Action payloads never leave the server.
  Guarantees (v0.199): EVERY `/frame` `200` for a proto-2 device
  carries `"manifest": {"digest", "url"}`, including re-renders that
  mint a new frame digest — a non-interactive dashboard gets a valid
  EMPTY manifest (`regions: []`, `text: []`), never a manifest-less
  `200` (which a v2 client reads as "v1 server"). A pixel-only
  re-render keeps `manifest.digest` identical, so the device
  re-anchors its held manifest with no re-fetch. A render whose
  region extraction raced to empty can never demote an interactive
  page (v0.201): an empty extraction is refused as a sidecar
  overwrite for an unchanged composition, and an empty manifest
  rebuild for a page whose cached manifest had regions serves that
  cached manifest re-anchored instead. The endpoint also
  answers for a just-superseded frame digest for a ~60 s grace window
  (a device mid-linger on the old digest is not orphaned); the same
  grace applies to `/frame/data`, and any KNOWN digest on
  `/frame/data` answers `200` (an empty `values` document when nothing
  is live or staged) so a device never latches data-off from a poll
  that raced a re-render — `404` = genuinely unknown digest on both.
  When a frame carries more regions than the device's advertised
  budget, the trim keeps navigation first, then sliders, then taps,
  then swipes (document order within each class) and logs the dropped
  region ids by name; raising the advertised `max_targets` (the server
  honours up to 64) is the headroom fix.
- `POST /api/v1/device/<id>/tap` with
  `{"region_id", "gesture", "value"?, "digest", "event_id"}` — the v2
  action report. The server re-mints the id from the frame's own
  sidecar, applies the same dedup/stale/provenance guards as
  coordinate strokes, and dispatches. Staleness is anchored to LAYOUT,
  not pixels (v0.201): a report against a superseded frame digest
  still dispatches when that frame's region set matches the live one
  (the server keeps a ~10-generation digest lineage per device), so a
  tap can never lose a race against a pixel-only re-render. `stale`
  now means the layout genuinely changed or the digest is too old to
  resolve. Wire outcomes (v0.200): `ok`
  for anything that dispatched (or legitimately resolved to nothing to
  do), `stale` / `deduped` / `ha_failed` as the firmware already
  names them, and specific diagnostics for the rest
  (`no_action_for_region`, `action_error`, `provenance_blocked`,
  `no_frame`, `resolver_exception`) — always HTTP 200, since the
  device's correct move on every non-ok outcome is the same (log,
  re-poll). Coordinate bodies remain the v1 path on the same endpoint
  with the v1 outcome names.
- `GET /api/v1/device/<id>/stream` — Server-Sent Events: `values` /
  `patches` / `sync` envelopes (identical payloads to the `/status`
  piggybacks and `/frame/data`), comment keepalive every 25 s. An
  optimisation over the 1 s linger poll, never a correctness
  requirement; reverse proxies must not buffer the route.
- `GET /api/v1/device/<id>/bundle` (+ `/bundle/frame/<digest>`) — the
  state bundle: warmed deck pages as digest-addressed frame states plus
  a navigation links table, content-hash versioned; the `sync` event
  repeats the bundle digest. `tile` states are contract-reserved and
  not yet produced.

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

### `.bin` — packed binary

Used by `pi_bin`, `esp32_bin`, `pico_bin` renderers. Bit width and
byte layout depend on the panel's gamut, so a client should key its
unpack path off the `gamut` field it receives in `/api/v1/device/config`
(or its `discover` payload).

#### 4-bpp layout (6 and 7 colour panels)

Applies to `waveshare_e6` and `inky_7colour`. Exactly
`(width × height) / 2` bytes, no header, no padding.

- Scanline order: row 0 first, left-to-right within each row.
- High nibble = even column index (0, 2, 4, …), low nibble = odd.
- 1200 × 1600 panel → 960 000 bytes.
- Nibble values 0–15 are palette indices. Two standard palettes:
  - **waveshare_e6**: 6-colour Spectra 6 (default; Waveshare + ESP32).
    Firmware reserves nibbles `0x4` and `0x7` (blue remaps to `0x5`,
    green to `0x6`); output never uses those reserved values.
  - **inky_7colour**: Pimoroni Inky 7-colour (matches the `inky`
    library's `pal` array, so you can `display.set_pixel(x, y,
    nibble)` directly).

#### 2-bpp layout (4 colour BWRY panels)

Applies to `bwry_4` (PicPak-class 4.2" panels, v0.69.4). Exactly
`(width × height) / 4` bytes, no header, no padding. Goes straight
to the SPI stream on the C3-class controllers these panels ship with.

- Scanline order: row 0 first, left-to-right within each row.
- Four pixels per byte, MSB = leftmost pixel: bits 7:6 = col 0,
  5:4 = col 1, 3:2 = col 2, 1:0 = col 3.
- Palette (values match the PicPak's on-panel palette register):
  `0x0=black`, `0x1=white`, `0x2=yellow`, `0x3=red`.
- 400 × 300 panel → 30 000 bytes.

The renderer applies rotation, letterboxing, underscan, and dithering
server-side, so the firmware just unpacks and pushes to the
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

### CircuitPython indexed `.png` / `.bmp`

The `circuitpython_generic` kind serves the composition already
quantised to the panel's exact palette, as either an indexed PNG
(`circuitpython_png`) or an uncompressed indexed BMP
(`circuitpython_bmp`), selected by the `format` field on `discover` /
`register`. Both mount straight into a `displayio.Bitmap` via
`adafruit_imageload` with no on-device quantise, dither, or nibble
unpack, the device paints what arrives.

Pick BMP on memory-constrained boards. CircuitPython's
`zlib.decompress` is one-shot, so decoding an indexed PNG needs the
whole inflated image in a contiguous buffer alongside the bitmap; on a
Pico W class board that exhausts or fragments SRAM. The BMP has no
`zlib` in the path, `adafruit_imageload` reads it row by row with
`file.read` / `seek`, so peak RAM is the framebuffer plus a small row
buffer. Trade-off: the server writes palette-mode BMP at 8 bits per
pixel, so it's a few times larger on the wire than the PNG. The
constraint on these boards is the decode buffer, not download size.

The BMP is always uncompressed `BI_RGB`, bottom-up (Pillow's default),
which is the form `adafruit_imageload` decodes; it rejects RLE-packed
BMP.

Reference: [`renderers/circuitpython_png/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/circuitpython_png/renderer.py),
[`renderers/circuitpython_bmp/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/circuitpython_bmp/renderer.py).

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

### Always on (mains-powered panels)

The 30-second floor under `sleep_interval_s` exists to stop a typo
flattening a battery. A panel on a wall socket doesn't need it, and
deep-sleeping at all costs it responsiveness: a Send, a schedule fire or
a touch can't reach a sleeping device, so it lands whenever the panel
next happens to wake.

Firmware opts a board into the faster path by advertising a capability in
its register / heartbeat body:

```json
{ "can_stay_awake": true }
```

This is a statement about *power*, not about the SoC: advertise it when
the board is running from a supply that can sustain continuous Wi-Fi, and
withhold it (or send `false`) when it isn't, e.g. when the battery gauge
says the panel is running unplugged. It's sticky server-side, so a beat
that omits it keeps the last known answer.

Only devices that advertise it are offered the setting, which then rides
the same config block as everything else:

```json
{ "always_on": true, "awake_poll_s": 15 }
```

`always_on` is always present in the config block, defaulting to `false`,
so firmware never has to handle its absence. When it's true:

- Skip deep sleep. Stay connected and poll `/frame` on `awake_poll_s`
  (bounds `5 ≤ awake_poll_s ≤ 300`).
- `next_poll_s` is derived from `awake_poll_s` rather than
  `sleep_interval_s`, and the 30-second content-poll floor no longer
  applies, so a server that knows content is changing can hand back
  intervals in the single-digit seconds. Honouring `next_poll_s`
  continues to be the right behaviour.
- Most polls answer `304`. Re-check cheaply and only repaint the panel
  on a `200`; an e-paper refresh is the expensive part, not the request.
- Smart sync stops holding fires for the device. There's no wake to time
  a render against when the panel is reachable continuously, so bound
  schedules fire on their own cadence and the frame is waiting on the
  next poll.

`sleep_interval_s` keeps its meaning as the deep-sleep cadence and is
what the device falls back to if `always_on` goes false again, so leave
it set to something sensible.

## Scheduling and polling

A schedule or rotation cannot *wake* a device; it only changes what
frame is waiting the next time the device polls. It can, however, pull
the next poll earlier.

- `next_poll_s` is the device's configured `sleep_interval_s`
  (device-instance setting → kind-schema default → 60s) **capped
  against the next projected content change**. If a bound schedule or
  rotation step is due in 2 minutes and the configured interval is 15,
  you're told to come back in about 2 minutes, so you see the change on
  the wake it happens rather than up to 15 minutes later.
- The configured interval remains the ceiling. It's never extended,
  because manual Send, webhooks, Home Assistant events and data-change
  refreshes have no schedule to project, and a device sleeping past its
  configured interval would go blind to all of them.
- Only `scheduled` and `conditional` projections count. An `estimated`
  one is the engine's own guess at an unanchored cadence, so it's left
  alone rather than spending a wake on a maybe.
- **A widget can also declare its own next change.** Schedules and
  rotation steps are the only things the projection can see, so anything
  whose displayed state turns over on its own clock is invisible to it: a
  meeting ending at 15:00, a bin collection at dawn, a countdown hitting
  zero. A widget's `fetch()` may return `next_change_at` (ISO 8601 or a
  unix timestamp) meaning "what I just produced becomes wrong then". The
  soonest such value across the page feeds `next_poll_s` alongside the
  schedule projection, under the same ceiling, floor and margin. Nothing
  changes for a widget that doesn't return it.
- **The frame is re-rendered when that moment has passed.** Waking at the
  right instant would otherwise achieve nothing, because `/frame` returns
  the last rendered artefact: a panel told to come back at 15:00:20 would
  collect the frame composed at 14:45 and keep showing the meeting that
  just ended. So when a declared change is in the past and the stored
  frame predates it, the poll re-renders before serving. The hint is
  consumed on the way out, so this is at most one synchronous render per
  declared change, not per poll.
- The returned value carries a small margin past the projected instant,
  because the server renders *at* that instant and a browser compose
  plus quantize isn't free. Polling at exactly the projected time would
  race the render and collect the previous frame.
- A schedule is a server-side push. When it fires, the server renders
  the target page and stores the result in that device's single
  latest-frame slot (`data/core/latest_renders.json`, latest-wins).
  The device sees it on its next `/frame` poll: `200` + a new ETag,
  otherwise `304`.
- Nothing wakes a sleeping device. A deep-sleeping REST panel is
  unreachable between polls, so a change is only ever seen on the
  device's own next wake.

**Offline devices get only the last iteration.** The latest-frame slot
holds one render per device with latest-wins coalescing; there is no
queue of missed iterations. If an hourly schedule fires 24 times while a
device is offline, each render supersedes the previous one in the slot,
so on reconnect the next `/frame` returns just the current frame, not a
backlog. This is deliberate: an e-ink panel only ever wants the newest
frame.

**Smart sync (optional)** times the render to land *just before* the
device wakes instead of on a fixed server cadence. It relies on the
wake-prediction fields the device publishes in `/status`:

- `sleep_until` (absolute unix wake timestamp) is the most accurate; it
  bypasses clock-skew math on the server.
- `next_sleep_s` (seconds until the device wakes) is the fallback; the
  server adds it to the receipt time.
- Publish neither and the server predicts from the configured
  `sleep_interval_s`.

Publishing is only worth it when your wake timing differs from that
configured interval. A client that sleeps for exactly the `next_poll_s`
it was handed gains nothing by echoing the value back, since the
fallback already predicts from the same number. A client that wakes on
its own schedule (a local time-table, irregular gaps, clock-aligned
wakes) should publish, because there the server's fixed-interval
assumption is simply wrong and no amount of observation will fix it.

A device becomes *trusted* after three consecutive wakes landing within
±60s of prediction, at which point the scheduler JIT-renders
`smart_sync_lead_s` (default 10s) before the predicted wake. Publishing
`sleep_until` is the fastest, most reliable way to earn trust. Smart
sync only improves render freshness; it never wakes the device, and it
doesn't feed `next_poll_s` (that's the projection above, a separate
mechanism).

### Sizing `sleep_interval_s`

Treat it as a **backstop, not a cadence**. Because `next_poll_s` is
pulled forward to bound schedules and rotation steps, a generous
interval no longer means a stale panel: set it well above your schedule
interval and the schedule drives the wakes, roughly one per content
change rather than a fixed grid that's mostly `304`.

Size it against what *can't* be projected: manual Send, webhooks, Home
Assistant events, data-change refreshes. The interval is their only
cover, so the question it answers is "how long am I willing to wait for
a manual Send to appear". A dashboard with no schedule or rotation bound
to it has nothing to project, and sleeps the full interval every time.

| Setup | Reasonable interval | Why |
| --- | --- | --- |
| Hourly schedule, battery panel | 4-6 h | The projection wakes you on each fire; the interval only bounds manual Send |
| 5-minute rotation | 30-60 min | Same, and the rotation steps dominate the wake count anyway |
| No schedule or rotation | The staleness you'll accept | Nothing to project, so this *is* the refresh cadence |

This inverts the advice that stood before v0.320.0, which was to set
`sleep_interval_s` to the refresh cadence you wanted and accept the
`304`s.

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
    status = POST /api/v1/device/<id>/status  {battery_pct, rssi, ip}
    apply_config(status.config)  # if changed
    set_clock(status.server_time)  # if no battery-backed RTC

    frame = GET /api/v1/device/<id>/frame  (with If-None-Match: <last_etag>)
    if frame.code == 200:
        bytes = GET frame.url  (no auth)
        paint(bytes, format=frame.format)
        last_etag = frame.render_id
    # 304: nothing new to paint. 204: admin hasn't bound a dashboard yet.

    sleep status.next_poll_s
```

That's the whole loop. Everything else (smart-sync, dithering hints,
TLS, mDNS) is opt-in polish.

The heartbeat leads here so config and clock are current before the
paint, and so last-seen still ticks on a wake that 304s with nothing to
paint. Posting it after the paint instead is equally valid; see [when
to send the heartbeat](#when-to-send-the-heartbeat).

## Questions, edge cases, suggestions

This protocol surface is the one place where third-party hardware
support gets to be one codebase instead of N. If you're building a
client and the spec leaves anything ambiguous, please open a thread
under [GitHub Discussions](https://github.com/dmellok/tesserae/discussions)
and we'll firm it up here.
