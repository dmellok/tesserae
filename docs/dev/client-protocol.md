# Client protocol

Use this spec to build a client that displays frames over HTTP or
MQTT. It covers battery boards, CircuitPython devices, Pis and kiosk
PCs, and matches the current `main` branch.

- For battery or embedded firmware (CircuitPython, MicroPython, custom
  MCUs), see [REST transport](../install/rest-transport.md) to work
  without a broker. Use MQTT if your stack already supports it.
  [Discovery & pairing](#discovery-and-pairing) lets devices re-pair
  after firmware updates by matching their MAC address.
- For an always-on Pi or kiosk, MQTT is simpler. The
  `tesserae/<id>/frame/<fmt>` topic delivers a JSON envelope with a
  download URL whenever a new frame ships.

## Architecture in 30 seconds

The server renders dashboards as PNG, packed `.bin`, or TRMNL 1-bpp
PNG files. Each file has a name based on its contents and never changes.
The client gets the frame through one of two paths:

1. MQTT push: the server publishes a JSON envelope with the frame URL to
   `tesserae/<device_id>/frame/<fmt>` (retained for the MCU `.bin`
   renderers, `esp32_*bin` and `pico_bin`; see [MQTT topics](#mqtt-topics)).
   The client fetches the URL, paints, then sleeps. Each renderer
   publishes once, even when several panels use it.
2. REST pull: the client polls
   `GET /api/v1/device/<id>/frame`. The server responds with the
   same JSON envelope (or 304 with an `ETag`, or 204 if no frame
   exists yet). No broker is needed, including on CircuitPython or
   demo Codespaces.

The two paths share the same envelope shape, auth, and frame artefacts.
You can switch between them later.

## Client guarantees

Your firmware **does not need**:

- A real-time clock. The server renders local time into the frame.
  `/status` also returns `local_time` for display or logging.
- An IANA timezone database or DST rule engine. The server uses the
  timezone set during onboarding. Every `/status` response includes
  local time, UTC offset and a daylight-saving flag. The heartbeat
  `tz` override is for devices that know their zone better, such as
  mobile devices or installs across timezones.
- An NTP client. The same `local_time` + `tz_offset_seconds`
  response fields are sufficient to set or align an external RTC
  if you have one.
- A schedule resolver. `next_poll_s` gives the sleep duration in
  seconds, with local schedules and quiet hours already resolved.
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

Every device gets a per-device bearer token (20 random alphanumeric
characters, about 119 bits, constant-time compared). Tokens live in
the instance manifest server-side; clients store them in persistent
flash / NVS / secrets.

Users never type tokens. There are three pairing paths:

- A (default): firmware announces itself. The admin clicks Register,
  and the next discover poll returns the token.
- B: the admin issues a pairing code before the device boots.
- C: a re-flashed device recovers its token by MAC address.

!!! note "`kind` is a server-side plugin id"
    The `kind` field in every discover / register payload must match
    a folder under `devices/` on the server. The kind plugin tells the
    server which renderer to compose for, what status fields the
    device reports, and what config knobs the admin UI exposes.

    Generic CircuitPython firmware uses
    `kind: "circuitpython_generic"`, included by default. Board-specific
    firmware can add a kind such as `circuitpython_pico_w_inky_73`.
    Both can run together. See
    [Adding hardware support](adding-hardware.md).

### A. Zero-touch discovery + admin approval

The default first-boot path. Firmware doesn't need a token, a code,
or any user-supplied credential at flash time, only the Tesserae
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

   `device_id` and `mac` are required here. Step 4 matches the MAC to
   return the token. A missing MAC returns `400`; it cannot enter
   discovery and keep polling with `registered: false`.

   A client without a MAC pairs with a 6-digit code (path B).

   `device_id` must match `^[a-z][a-z0-9_-]{1,31}$`: 2–32 characters,
   lowercase, starting with a letter, with `_` and `-` allowed after
   the first character. Ids that fail the pattern are rejected
   everywhere a device id is accepted (announce, register, MQTT status
   topics). Clients deriving a default id from hardware identifiers
   (board name plus MAC) should keep the result under the length cap
   and sanitize the leading character, since some board names start
   with a digit.

   Send the real MAC, not a stand-in. `null`, `"None"` (a stringified
   null), `00:00:00:00:00:00` and `ff:ff:ff:ff:ff:ff` are rejected like
   a missing MAC. Shared placeholders would give two devices the same
   identity and expose the first device's token to the second.
   The same rule applies to the `mac` on `/register`: a placeholder is
   dropped rather than stored, so pairing succeeds but that device
   can't auto-claim later.

   `name` is an optional display name on `/discover` and `/register`:

   - On `/register`, it names the new instance.
   - On `/discover`, it fills the Register card's "Display name" field
     in Settings → Devices. The admin can edit or clear it before
     registering. Later announcements never rename a registered device.

   Without a name, the device id is the display fallback.

   `rotation` is optional, one of `0`, `90`, `180`, `270`, and it
   changes how `panel_w` / `panel_h` are read.

   With this field, `panel_w` / `panel_h` describe your framebuffer.
   `rotation` is the turn from that buffer to the dashboard:

   - `0`: compose at the reported dimensions.
   - `90` or `270`: compose with width and height swapped. A
     `1200 x 1920` buffer gets a `1920 x 1200` dashboard, rotated back
     onto the `1200 x 1920` buffer before writing.
   - `180`: keep the dimensions and turn the image halfway for an
     upside-down mount.

   The downloaded file matches the declared panel shape.
   `native_w` / `native_h` in the `/frame` envelope let you check it
   against your buffer.

   Without it, `panel_w` / `panel_h` describe the dashboard canvas.
   Taller-than-wide dimensions register as portrait. This also supports
   clients that rotate the image after download.

   The server reads the value once, when creating the device.
   Later mount changes use the Rotation dropdown in Settings → Devices.
   Re-sending `rotation` has no effect. `/register` for an existing id
   and `/discover` for a known MAC return the existing device unchanged.
   `/status` doesn't accept the field at all.

   To have a client redeclare its geometry from scratch, delete the
   device and pair it again.

   The dropdown also measures the turn from the framebuffer.
   A client declaring `0` reads back as 0°, even on a portrait-native panel.

   `gamut` is optional and saved in the new instance's panel settings.
   This lets `circuitpython_generic` serve different panels without
   a separate manifest for each model. Accepted values:

   | Value | Meaning | Persisted as |
   | --- | --- | --- |
   | `waveshare_e6` | Spectra 6, `.bin` packer target | `waveshare_e6` |
   | `spectra_6` | Spectra 6, semantic alias | `waveshare_e6` |
   | `inky_7colour` | Pimoroni Inky ACeP, `.bin` packer target | `inky_7colour` |
   | `acep_7colour` | ACeP, semantic alias | `inky_7colour` |
   | `mono` | 1-bit B/W | `mono` |
   | `bwr_3` | 3-colour B/W/Red tri-colour e-ink | `bwr_3` |
   | `gray_4` | 4-level greyscale ramp (2-bit, no highlight) | `gray_4` |
   | `gray_8` | 8-level greyscale ramp, sent as a 4-bpp frame using the nibbles 0, 2, 4, 6, 9, 11, 13, 15 | `gray_8` |
   | `gray_16` | 16-level greyscale ramp (4-bpp) | `gray_16`, or the kind's `gray_8` when the kind declares it |
   | `bwry_4` | 4-colour B/W/Red/Yellow (PicPak-class 4.2" panels) | `bwry_4` |
   | `rgb24` | Full 24-bit colour (LCD-hybrid) | `rgb24` |
   | `rgb16` | 16-bit colour (RGB565) | `rgb16` |
   | `kaleido3` | E Ink Kaleido 3 colour e-reader (Kobo Libra Colour, Clara Colour): 24-bit RGB with 16 levels per channel, 4096 colours | `kaleido3` |

   Unknown values fall back to `waveshare_e6` when saved, so a corrupt
   payload cannot leave the panel unusable.

   `kaleido3` also selects the renderer. Kinds can dedicate a renderer
   to a gamut with `"gamuts": ["kaleido3"]` in the manifest, as
   `kaleido_png` does on `koreader_client`.

   A dedicated gamut overrides the requested `format`. Its `/frame`
   descriptor says `format: "png"`: a full-resolution, 24-bit RGB PNG
   dithered to 16 Kaleido levels per channel (0, 17, ..., 255).

   A later `/register` can switch an existing device onto that renderer
   or off it when leaving its dedicated gamut. This supports turning
   KOReader colour rendering off. Other gamut changes leave the panel
   settings untouched.

   ??? note "Why do `waveshare_e6` and `inky_7colour` carry manufacturer
       names?"

       Waveshare firmware and Pimoroni's `inky` library pack Spectra 6 /
       ACeP pixels differently. Waveshare reserves nibbles 0x4 and 0x7:
       blue maps to 0x5 and green to 0x6. Pimoroni uses the UC8159's
       native palette order.

       The `.bin` packer needs the byte layout: `waveshare_e6` or
       `inky_7colour`. The aliases `spectra_6` and `acep_7colour` resolve
       to these values when saved. Existing names stay for compatibility;
       renaming one to `spectra_6_pack_waveshare`, for example, would
       require migrating every existing config.

   `format` is optional and selects the wire container the server
   renders into. It only applies to kinds that offer more than one
   renderer; `circuitpython_generic` offers two:

   | Value | Renderer | Notes |
   | --- | --- | --- |
   | `png` (default) | `circuitpython_png` | Indexed PNG. Smallest download. Needs `zlib.decompress` on the client. |
   | `bmp` | `circuitpython_bmp` | Uncompressed indexed BMP. No `zlib` on the client at all. |

   Declare `"format": "bmp"` when the board can't spare a contiguous
   decode buffer. CircuitPython's `zlib.decompress` cannot stream:
   the full decoded image sits in RAM beside the `displayio.Bitmap`.
   This exhausts or fragments a Pico W's roughly 110K of free SRAM.

   For BMP, `adafruit_imageload` uses `file.read` / `seek` to read rows.
   Peak RAM is the framebuffer plus a small row buffer.
   Boards with headroom (ESP32-S3
   etc.) should keep the default PNG, which is still smaller on the wire
   (the BMP is packed at 1 bit per pixel for mono and 4 for every other
   gamut, uncompressed).

   The renderer is selected by file extension. The declared `format`
   matches the `format` returned by `/frame`.
   Unknown or absent values keep the default PNG renderer.

   To switch formats, keep the existing device. Re-declare `format` on any later
   `/register` or `/discover` (MAC-match) call and the server moves the
   device to the matching renderer in place, then invalidates its cached
   frame so `/frame` returns `204` until the next push repaints it in the
   new format. A
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

   The returned `device_id` wins. The server identifies the device by
   MAC and returns its stored id and token, ignoring the announced id.
   This lets a re-flashed board recover its token after losing settings.

   Continuing with the announced id may address a device the server
   doesn't have. Adopt the returned value.

   `device_id_changed` reports the mismatch in the body. The headers
   give the same answer without JSON parsing:

   ```http
   X-Tesserae-Device-Id: circuitpython_kitchen
   X-Tesserae-Device-Id-Changed: false
   ```

   Both headers appear on every successful claim. Missing headers mean
   an older server. On a mismatch, `announced_device_id` holds the sent
   id, and the server logs a WARNING for the operator.

   To move a device to a new id, delete it in Settings → Devices and pair
   again. Announcing a new id does not rename the instance: the id is the
   operator's, and pages, schedules, and bindings hang off it.
5. Firmware saves the token, drops into the steady-state loop.

### B. 6-digit pairing code (fallback)

Use this when the admin cannot approve discovery at boot, such as a
sealed appliance, BLE setup, pre-printed QR code or kiosk. The admin
issues a code and enters it in firmware setup. Firmware then registers
without another admin click.

1. Admin generates a code in Settings → Devices → Add device → REST API →
   **Issue pairing code** (server route: `POST /api/v1/device/admin/pairing/issue`,
   10-minute expiry, single-use).
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
   ```

   `gamut` follows the same rules as on `/discover`. Both
   paths write the canonicalised value onto the auto-provisioned
   instance's panel block. `format` (`png` / `bmp`) and `rotation` are
   honoured here too, same rules as on `/discover`.
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
returned.

### C. MAC auto-claim after re-flash

After losing its token, a registered device can call `/discover` with
its MAC. A known MAC returns the existing token immediately, without
admin approval.

### Rate limiting

`POST /api/v1/device/register` and `POST /api/v1/device/discover` are
rate-limited per source IP: 10 failed attempts per 60 seconds.
Successful registrations don't count against the limit. Failed registrations and
all discover calls consume attempts. An attacker needs a fresh pairing
code for each successful registration.

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
[`frames.json`](#get-apiv1deviceidframesjson). These serve download
handlers that cannot set headers. Other authenticated routes accept
header tokens only.

Frame downloads (`/renders/<file>`, `/preview/<id>.png`) do **not**
require auth. They are on the LAN-bypass list (no auth from LAN clients)
because their URLs are random and opaque (SHA-256 digests for `/renders/`). The admin UI is
the server's main authentication boundary.

"LAN" here means the standard private ranges (RFC 1918, `fc00::/7`,
link-local, loopback) plus any networks the operator lists under
Settings → System → Authentication → Trusted networks or in
`TESSERAE_TRUSTED_NETWORKS`. A home network on a globally routable
IPv6 prefix from the ISP is outside the fixed ranges, so a device
there gets `403` on `/renders/` until that prefix is listed (or it
fetches the signed URL handed out by `/frame`).

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

`200 OK`: new frame is available. Example for a `.bin` frame
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

`Content-Location` matches the body's `url` and also appears on
`304 Not Modified`, which has no body. On `304`, a client without a
cached URL can fetch the image from this header. It need not save the
URL alongside its `ETag` across power cycles.

RFC 7231 §3.1.4.2 permits `Content-Location` on 304. The URL can also
be built from `render_id`: `<server-base>/renders/<render_id>.<format>`.
Saving `(server_base, render_id, format)` lets a client rebuild it
without the header.

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
- Other renderers may ship their own extras in the future. The contract is: anything not listed in the "always present" set above is renderer-specific, and **clients should ignore unknown fields** so new renderers don't break older firmware. The TRMNL renderer's `dither` and `contrast` knobs are *device-side settings* (configured per instance in Settings → Devices, applied server-side before encoding), not envelope fields.

Physical button wakes: devices with hardware buttons add
`?button=<name>&button_event_id=<uint>` to the frame request on a
button-driven wake. The name matches the device's `button_map`,
usually `left`, `right`, `refresh`. The server runs the mapped action
before selecting the frame, so the response reflects it on this wake:

```
GET /api/v1/device/kitchen/frame?button=right&button_event_id=42
```

A client can add `button_key=<key>` for a physical button's own name
(or `"button_key"` in the status body). For example,
`?button=right&button_key=c&button_event_id=42` identifies an Inky
Impression's C button.

The server checks the key in `button_map` before the name.
An unmapped key falls back to the name.

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

Dashboard picks: a device with its own menu can show any dashboard
bound to it by adding `?page=<dashboard id>` instead of `?button=`. The
ids come from [`frames.json`](#get-apiv1deviceidframesjson). This runs
the same `page:<id>` action as a mapped button.

The response serves that dashboard rendered for this device.
The rotation or lineup position stays unchanged, so the next timer wake
resumes the schedule. An unbound or archived dashboard is refused;
the response serves the current frame.

`button_event_id` is optional here. Without it, repeats of the same pick
within the debounce window are treated as retries, and a different pick
is not.

```
GET /api/v1/device/kitchen/frame?page=morning_briefing
```

A dashboard that has not rendered recently is rendered on the spot, so
the first pick can take a few seconds.

#### Touch wakes

Touch devices send a stroke's start point, end point and duration on
the frame request. The server classifies taps and directional swipes,
then checks which touch region they hit. The client only needs touch
coordinates and HTTP; it stores no region geometry.

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
| `touch_space` | optional | `fb` says the coordinates are framebuffer pixels (the shape of the downloaded `.bin`, `native_w` x `native_h`) instead of canvas pixels. The server turns them onto the canvas through the same rotate / flip / scale / underscan it drew the frame with, so touch follows whatever orientation the device page sets without the firmware knowing it. Omit it to send canvas pixels as before. Since 0.510.0; an older server ignores it and reads the points as canvas pixels |

The resolved action dispatches synchronously before the frame lookup
(same contract as button wakes), so a `page:` / rotate action's
repaint comes back on this same response. A stale digest, a stroke on
a non-interactive area, or a frame with no touch regions all degrade
to a plain frame poll; the wake is never an error.

For a fire-and-forget `webhook:` action, the receiver decides what
appears next. A wake carrying `If-None-Match` answers `304` for the
held frame, even if the same page has a newer render. This avoids
showing an unrelated render before the receiver's push.

A device still awake collects that push within `touch_linger_s`.
A device that sleeps gets it on its next timer wake.

The status `config` block includes settings declared by the hardware kind:

- `touch_enabled`: boolean.
- `touch_linger_s`: 0-60.
- `touch_wake`: either `"tap"` (scan through deep sleep; any touch
  wakes the device) or `"gesture"` (double tap or swipe wakes it,
  using about a sixth of the current).

A client that does not know `touch_wake` ignores it; a server that
does not send it leaves the client's mode alone.

Continuously powered clients that poll `/frame` on a timer can send
the stroke out-of-band instead via `POST /api/v1/device/<id>/tap`
with body `{"x0": …, "y0": …, "x1"?: …, "y1"?: …, "duration_ms"?: …,
"digest": "<etag>", "event_id"?: …}`. A well-formed request always answers `200`
with `{"outcome": "dispatched" | "noop" | "webhook_dispatched" |
"fetched" | "stale" | "no_frame" | "no_target" | "deduped" | "error", "gesture":
"tap" | "swipe_up" | …, "action_spec": …, "description": …,
"rotation"?: {…}}`; in every non-dispatched case the client's correct
move is the same: re-poll `/frame` and carry on.

Touch regions use `data-on-tap` / `data-on-swipe` / `data-on-slide`
attributes on cells, widgets or code-element DOM. The server extracts
them at render time; firmware stores no region geometry.

Sliders map the stroke's end point to an absolute 0-100 value along
an axis (vertical fills upward). The server puts this value into the
action, so one press-drag-lift can set a light level.

Home Assistant service calls run synchronously and re-push the current
page. The same wake's frame shows the new state. The `outcome` values
on `POST /tap` include `ha_dispatched` / `ha_failed` / `blocked`.

`304 Not Modified`: `If-None-Match` matches current digest. No body,
just `ETag: "<digest>"` and `Content-Location: <absolute URL>`. Re-paint
the previously-cached frame, or re-fetch the URL if your device didn't
retain the image bytes across a power cycle.

`204 No Content`: server has no frame for this device yet (e.g.
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

Unknown fields appear as plain key/value rows on the device card.
The fields below drive server behaviour, regardless of device kind.
A kind's `parse_status` only converts types; the server uses field names.

| Field | Type | What it drives |
| --- | --- | --- |
| `battery_pct` | number, 0-100 | Battery tile, battery history, HA sensor, `tesserae_status` widget |
| `battery_mv` | int, mV | Same. Sent alone, the server derives `battery_pct` from a linear LiPo curve (3300 mV = 0 %, 4200 mV = 100 %) |
| `power_plugged` | bool | Words the battery tile ("plugged in" / "on battery"). A UPS on a mains device, such as a PiSugar on a Pi, sends it so 100 % on the charger reads differently from a panel running down |
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

`voltage`, `uptime`, `uptime_s` and `last_paint` are marked volatile.
Changes to these alone create no event-log row. No other server
behaviour uses them.

Capability blocks (`ota`, `overlay`, `proto`, `logs`) are nested
objects. Their last reported values persist across heartbeats that
omit them, so you only need to send them once per boot.

Storage capabilities (`deck_cache`, `frame_cache`) describe the current
state on each heartbeat. See
[deck cache sync](#deck-cache-sync-on-device-frame-cache).

#### Log upload and failure reports

Firmware that can upload its log (see [`/log`](#post-apiv1deviceidlog-optional))
advertises it on every beat:

```json
"logs": {"schema": 1, "ring_bytes": 3072}
```

`schema` is the protocol version (1). `ring_bytes` is informational:
the log's size in RTC memory, which survives deep sleep.
Only devices advertising `schema >= 1` get log requests; the server
remembers this capability across restarts.

After a detected failure, the firmware adds a `diag` report to the next
beat, and to every beat after it until one status POST carrying it
returns 2xx:

```json
"diag": {
  "id": 1234,
  "paint_error": "refresh_timeout",
  "reset": "brownout",
  "at": 1790000000
}
```

- `id`: uint32, required. Increments per latched failure (random start
  at power-on). The server dedups on it, so a re-sent report is logged
  once. A report without an `id` is ignored.
- `paint_error` (optional): `init_failed` (controller never answered
  after recovery), `ready_timeout` (controller busy / HRDY line stuck) or
  `refresh_timeout` (refresh never reported done). Absent when the
  failure is only a reset.
- `reset` (optional): why this boot started, when abnormal: `brownout`,
  `panic` or `watchdog`. Absent on power-on, deep-sleep wake and
  software reset.
- `at`: epoch of the latch, `0` when the clock was unknown.

Each new report writes one error row to Settings › Events (for example
`paint failed: refresh_timeout` or `reset: brownout`), and the device page
shows the latest as "Last detected failure".

#### When to send the heartbeat

You can post `/status` before `/frame`. Its response supplies
`config`, `next_poll_s`, `server_time` / `local_time` and the
`rotation` block before the paint.

Two heartbeats per wake are supported, on connect and before deep sleep.
Only heartbeats within 10 seconds count as one wake for smart sync.

An e-ink paint often takes 15 to 30 seconds. Heartbeats on either side
then count as separate wakes.

The second's prediction offset is roughly minus the whole sleep interval.
This resets confidence and prevents trust.

If you can't keep both beats inside 10 seconds, send
**one**, just before sleep, carrying `next_sleep_s`. This gives smart
sync the correct prediction anchor and the latest readings.
The returned `config` then applies on the next wake.

`/frame` doesn't render on demand; it hands back the most recently
rendered frame. Wake *N* paints a frame made before wake *N*, using
the heartbeat from wake *N-1*. A battery percentage in that frame is
one cycle old, regardless of heartbeat order.

To narrow the gap:

- Publish `sleep_until` or `next_sleep_s` so smart sync renders roughly
  `smart_sync_lead_s` before the predicted wake.
- For live values, use the [hybrid render](#overlay-specs-hybrid-render-mode)
  path. A device advertising `overlay` gets `overlay_values` in its
  `/status` response, computed after that heartbeat arrives. The device
  draws these current readings over the frame locally.
- Otherwise, draw device-local values (battery, signal) on-device from
  what you already hold in RAM, rather than expecting them in the
  server-composed frame.

The optional `tz` override accepts an IANA name, such as `Europe/Berlin`.
Most clients can use the server's configured timezone.
Invalid names are ignored; local-time fields are returned either way.

Button events can also go in the status body, even when the device
skips `/frame` or posts `/status` after it. The server uses
`button_event_id` to avoid dispatching the same event twice:

```json
{
  "battery_mv": 3850,
  "button": "right",
  "button_event_id": 42
}
```

`button` is the button name. `button_event_id` is a per-device uint
counter that increases and persists across deep sleep, for example in NVS.

Retries send the same id. The server handles ids as follows:

- Equal to the last processed id: duplicate.
- Lower: counter restarted after a power cycle; dispatch the event.
- `0`: counter restarted; never an id duplicate. Repeats are caught by
  the time-window debounce. Clients can start at `0` on each boot.

Firmware without a counter can omit `button_event_id` and use the
time-window debounce. Its default is 3 seconds, set by
`settings.app.button_debounce_s`.

This status-body form is also the **only** button path for a
relay-paired panel (its frame poll terminates at the relay, so
frame query parameters never reach the server). Over the relay,
repeat `button` + `button_event_id` unchanged on every status post
of the same wake, and always send the event id; see "Buttons
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
- `server_time`: Unix epoch integer (UTC, whole seconds), for RTC sync.
  MicroPython / CircuitPython parses a JSON float as float32. Near the
  current epoch, its roughly 128s resolution rounds to about 2 minutes.
  An integer parses exactly on a longint-capable client.
- `local_time`: ISO 8601 string with offset suffix, resolved for the
  device's effective timezone (precedence below). Clients without an
  RTC just use this directly.
- `tz`: IANA name actually used. The server tries its configured
  `settings.app.timezone`, then the host's detected timezone, then `UTC`.
  A valid heartbeat `tz` overrides these. If `tz` is invalid, such as
  `"Berlin"` without a continent, the response names the zone used instead.
- `tz_offset_seconds`: integer offset from UTC in seconds (positive
  east of UTC). Lets RTC-equipped clients cache the rule and derive
  local time on intermediate wakes between heartbeats.
- `dst_active`: true if daylight saving was in effect at the moment
  the response was assembled. Informational; combined with the
  offset it lets a smarter client predict the next DST transition.
- `wake_at` (optional): Unix epoch integer for the instant encoded by
  `next_poll_s`. Sent only with synchronized wake enabled in Settings →
  Devices → Schedule tab. Sleeping `next_poll_s` still lands close.
  A client that honours it should sleep until `wake_at - now` computed
  at the moment it actually enters deep sleep. This avoids adding the
  frame-fetch and panel-refresh time to the sleep delay. Treat it as a
  one-shot value, like `next_poll_s`: never persist it.
- `logs` (optional): `{"upload": true}` requests an upload on this wake,
    after status and before Wi-Fi disconnects; see [`/log`](#post-apiv1deviceidlog-optional).
    Absent means don't. Only sent to devices advertising `logs.schema >= 1`,
    when either condition holds:
    - The operator requested a number of collection wakes on the device
      page. Each response requesting an upload spends one wake.
    - This status carried a new `diag` `id`, and "Collect a device's log
      after a failed paint" is enabled (the default).

    Top-level, never inside `config`: don't persist it.

The local-time fields are always present in the response regardless
of whether the heartbeat sent `tz`. A pre-existing client that doesn't
know about them just ignores the extra keys.

### `POST /api/v1/device/<id>/log` (optional)

Two uses, told apart by the content type. Same bearer token as
`/status`.

**A single log line** (`Content-Type: application/json`, or anything
other than `text/plain`): forwarded into the server's Events tab.
Useful for remote debugging without a serial cable.

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

**A log batch** (`Content-Type: text/plain; charset=utf-8`): the
device's own log, uploaded on a wake whose `/status` response carried
`logs.upload` (see [log upload and failure
reports](#log-upload-and-failure-reports)). UTF-8 text with `\n` line
endings: ESP-IDF formatted lines with ANSI colour codes stripped. Each
wake's section starts with a header line:

```text
# tesserae-log v1 fw=<ver> boot=<n> reset=<reason> wake=<cause> epoch=<t>
```

(`epoch=0` when the clock is unknown). A batch normally holds the unsent
tail of the previous wake, from the RTC ring, followed by the current
wake up to the upload, so a paint that failed with the radio off arrives
on the next wake.

- At most 64 KB; a larger body is `413` with the usual error body. Trim
  from the front to fit and write a `# ... N bytes dropped` line.
- Redact before a line is captured: replace the query string of any
  `http://` / `https://` URL with `?<redacted>` and any `Bearer <token>`
  with `Bearer <redacted>`. Never log Wi-Fi passphrases, device tokens
  or relay keys.
- Upload after the status and before Wi-Fi goes down. Treat any 2xx as
  stored. A failed upload is not retried that wake; unsent ring bytes
  stay eligible for the next request.

**Response** (`200 OK`):
```json
{ "status": 200, "bytes": 9412, "stored": true }
```

Tesserae Cloud may answer `204` instead. The self-hosted server stores
the batch under `data/core/device_logs/<device_id>/`, keeps the newest 20
uploads up to 1 MB per device, and writes one Events row per upload
(`log uploaded, N lines`). The device page's Logs section lists them.

### `GET /api/v1/device/<id>/frame.bmp`

Returns the current frame as an uncompressed indexed BMP in one request.
Use this instead of the `/frame` envelope when the downloader cannot
fetch JSON and then an image. E-reader sleep-screen handlers often
allow one request with no redirects.

**Response** (`200 OK`): `Content-Type: image/bmp`, the complete image,
sized to the device's panel dimensions and rotation. An `ETag` is sent
but no conditional request is required.

This route always serves BMP without changing the device's configured
frame format. It converts the same composition to an uncompressed image.
The body is capped at 1 MB; an 800x480 4-bit BMP is about 192 KB.

Oversized images return `500` with the measured size.

**This route never answers `2xx` without a complete body.** These
clients replace the sleep-screen file on any `2xx`. A `204` or empty
`200` would overwrite it with a zero-byte file.

| Situation | Status |
| --- | --- |
| No frame rendered yet | `404` (**not** `204`, unlike `/frame`) |
| Missing or bad token | `401` |
| Token belongs to another device | `403` |
| Conversion failed | `5xx` |

Auth uses the usual device token. `?k=<token>` also works for download
handlers that cannot set headers. Headers still win when both are present.

Query tokens appear in access logs, proxy logs and browser history.
Only routes that need this fallback accept them. Plain `http://` works
on the LAN, with no redirects or `https` → `http` hop.

### `GET /api/v1/device/<id>/frames.json`

Returns a frame list for an on-device dashboard picker.

**Response** (`200 OK`):
```json
{
  "items": [
    {
      "id": "current",
      "title": "Current dashboard",
      "subtitle": "Download as sleep screen",
      "url": "http://<server>/api/v1/device/<id>/frame.bmp?button=refresh&button_event_id=<epoch>&k=<token>"
    },
    {
      "id": "page:morning_briefing",
      "title": "Morning briefing",
      "subtitle": "Show on this device",
      "url": "http://<server>/api/v1/device/<id>/frame.bmp?page=morning_briefing&k=<token>"
    }
  ]
}
```

After `current`, the list includes bound, unarchived dashboards in
dashboard-list order, rendered at this device's size. After `page:`,
the `id` contains the dashboard id. Clients using `/frame` pass this
as [`?page=`](#get-apiv1deviceidframe).

Downloading an item also shows that dashboard on the device.

`items` is always a JSON array; an object would produce an empty picker.
Each `url` is absolute, includes the token query parameter, and needs
one request with no redirect.

Display strings are ASCII. E-ink UI fonts may lack arrows or symbols;
a missing glyph shows as a box.

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

> [Frame-cache collections](frame-cache.md) lets decks and offline photo
> albums share one content-addressed cache. The deck-cache capability
> and endpoints below stay unchanged for compatibility.

Decks are linked, pre-rendered pages in Settings → Decks. Normally,
the server handles a button or touch event and serves the target frame
on the same wake.

With local storage, such as an SD card, a client can cache frames and
navigate with its radio off. This is optional. Clients that never
advertise the capability see no endpoint changes.

### Capability

Advertise in the `/register` and every `/status` body, **only while the
storage is actually present and usable**:

```json
{ "deck_cache": { "schema": 1, "capacity_bytes": 7900000 } }
```

Omitting the capability withdraws it for that heartbeat, for example
after card removal or a mount failure. The server stops offering deck
syncs until the capability returns.

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

   Zones are rectangles with coordinates from 0 to 1. Scale them by
   panel dimensions to hit-test. The first call after a deck edit can
   take a few seconds while uncached pages render.

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

   Without authored links, the manifest adds defaults:

   - `left` / `right` buttons go to the previous / next page in deck
     order, wrapping at the ends.
   - On touch panels, left-half / right-half taps do the same, but only
     when the page has no explicit zones or markup touch regions.

   A default zone must never swallow a page's real tap targets.
   Explicit graph links always win over defaults.

   Unchanged frame bytes keep the same digest and deck version across
   refreshes. Clocks and "last updated" stamps change the digest on each
   render and force another sync. Keep them off pages you want to cache.
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

Every other case (no link match, missing/stale/corrupt frame, version
mismatch, no card) sends the event and fetches `/frame`.
The server handles every event it receives.

`ttl_s` expiry on the page currently displayed does not force a network
wake by itself; scheduled wakes handle refresh. It only disqualifies a
cached frame from being served for a navigation.

## Overlay specs (hybrid render mode)

Touch boards with fast partial refresh, such as IT8951 boards, can
draw server-defined shapes over the frame. This gives sub-second tap
feedback without a network round trip. The overlay is cosmetic;
the server frame remains authoritative.

Advertise `"overlay": {"schema": 1, "max_targets": 32}` in
register/status bodies. This capability persists across heartbeats and
server restarts, unlike `deck_cache`. It enables values documents,
values on `/status`, and schema-2 patches.

`max_targets` (additive, firmware v1.9+) declares the device's target-buffer capacity. The
protocol-v2 interaction manifest uses the same limit.

The protocol-v2 spec is `docs/protocol-v2-touch.md`. The schema-1 endpoint
`GET /api/v1/device/<id>/frame/overlay/<frame_digest>` no longer exists.
It returns `404`, meaning "feature off for this frame". V1 clients
fall back to dispatch-without-echo. The protocol-v2 interaction manifest
carries tap-echo targets and slot geometry.

Values, atlases and patches all use the downloaded framebuffer's pixel
coordinates. The server applies rotation, flip, scaling and underscan.
Firmware uses the rectangles as given.

### Value slots and glyph atlases

Widgets opt into live value text by annotating an element with
`data-overlay-key="ha:<entity_id>"` (optional `data-overlay-suffix`,
e.g. a degree sign). At render time the slot's box, alignment, font
size, and weight are extracted alongside the touch regions into the
composition sidecar.

Slot geometry and atlas references are `text` regions in the
protocol-v2 interaction manifest. It references the values document
below and the atlas store.

An atlas is a strip of rendered glyphs. The server uses the composition's
browser and fonts (Inter, at the slot's exact size and weight) so the
text matches the frame.

The strip is exactly `max(glyph.x + glyph.w)` pixels wide, 4bpp gray,
high nibble = left pixel. Fetch by digest with immutable caching at
`GET /api/v1/device/<id>/frame/overlay/atlas/<digest>`. The glyph
charset covers numeric values (`0-9 . , : - + % ° C F` and space); a
character outside it renders as a mean-width blank on the device.

### Values document

`GET /api/v1/device/<id>/frame/data?digest=<frame_digest>` (Bearer):

```json
{"seq": 1753305600123, "values": {"ha:sensor.temp": "21.4°"}}
```

`seq` is wall time in milliseconds (int64).

Values are pre-formatted display strings (state + declared suffix,
clipped to 47 chars); the firmware applies zero formatting. Poll only
while awake in the touch-linger window (1-2 s cadence). The same
document also arrives as `overlay_values` on `/status` responses for
any device whose saved capability includes `overlay` or `proto >= 2`.
Omitting the capability on a heartbeat does not stop delivery.
Firmware sending only `proto` also gets values.

The two sources are interchangeable: newest `seq` wins; equal `seq`
means no repaint. `404` means no values for this frame: unknown digest,
no slots, or no Home Assistant configuration. Treat it as values-off.

An entity that is `unknown` / `unavailable` is absent from `values`.
Firmware keeps showing its value from the rendered frame.

Slot keys accept an optional attribute path
(`data-overlay-key="ha:light.desk:attributes.brightness"`), resolved
against the entity's full state object. A slot may also declare
`data-overlay-map='{"on": "1", "off": "0"}'` to rewrite raw values into
strings the numeric glyph charset can draw; unmapped values use the
raw string. Code-element sandboxes also export slots with touch regions,
so full-bleed code dashboards get the same live values as widgets.

### Patch documents (schema 2)

Boards advertising `"overlay": {"schema": 2, ...}` receive patches
after touch actions change external state, such as a Home Assistant
service call. The server re-renders the page, compares it with the
displayed frame, and prepares the changed rectangles.

The digest stays unchanged; `/frame` keeps returning 304.
The patches repaint the frame. A burst of taps becomes one document
after `app.touch_patch_debounce_s` (default 0.4 s after the last action).

Small scheduled or push-triggered changes also use patches on a
schema-2 REST device's current page. A clock tick, for example, keeps
the digest stable and avoids a full flash or an invalidated tap.

Page edits, renderer-setting changes, cross-page pushes and changes
past the caps still produce a new digest and full frame.
Firmware needs no new behaviour for either case.

The server compares compositions before dithering, so dither noise
does not inflate patches. Patch bytes still match the new frame exactly.

A deep-sleep device has no framebuffer in RAM on a timer wake and
cannot apply a patch from status. The server keeps the full render
behind each patch. On the next `GET /frame`:

- If the device never fetched the patch, return 200 with the full frame.
- If it fetched the patch during linger or SSE, keep returning 304.

Firmware needs no new behaviour. To disable patches per device, set
"Update delivery" to "Always full refresh" on its Settings card.

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

* Anchoring. Apply only when `frame_digest` equals the digest of
  the frame currently on glass. The server already refuses to hand out
  a document for any other digest, so a mismatch means the client
  raced a frame change: drop the document and do a normal frame poll.
* `format: "fb-rect"`. The blob is per-rect row data in the exact
  packing of the frame file itself (for the E1003's 4bpp gray:
  `w * 4 / 8` bytes per row, high nibble = left pixel). `x` and `w`
  always land on byte boundaries, so each rect is a straight row-wise
  memcpy into the stored framebuffer at `(x, y)`, then a partial
  refresh of that rect. Pixels are never interpreted.
* `seq`, newest-wins. Strictly increasing per device (survives
  server restarts). A document with `seq` <= the last applied one is a
  no-op; a newer one replaces any pending document outright. Rects in
  one document may overlap previously patched areas; painting them
  again is correct.
* Tap-echo supersede. A patch rect overlapping an active tap-echo
  inversion clears it (the patch carries the truth the echo was
  predicting). Patch partial-refreshes count toward the same ghosting
  hygiene counter as echo inverts: full-quality repaint after ~8.
* Caps. At most 12 rects and 256 KB of blob per document; the
  server falls back to a normal full frame beyond that, so a client
  never needs to handle more.
* Blob fetch. Content-addressed, immutable caching. A `404` on the
  blob means the document was superseded mid-fetch: drop it and poll
  `/frame/data` again.

Devices that only advertise schema 1 (or none) still converge: the
server re-pushes the page asynchronously ~3 s after the last action
(`app.touch_repush_debounce_s`), and the next `/frame` poll or MQTT
publish delivers a normal full frame.

### Stale frame signal

Changes past the caps, page edits, renderer-setting changes and external
`POST /api/v1/push` calls produce a new digest. During touch linger,
the device is not polling `/frame`, so `/frame/data` reports the change:

```json
{"seq": 1753430000123, "values": {}, "stale": true, "frame_digest": "<newer>"}
```

Contract:

* Meaning. The server's live frame for this device is no longer the
  one named in the poll's `?digest=`. Run a normal `/frame` poll; the
  digest really did change, so a conditional GET answers `200` without
  dropping the stored ETag.
* Both fields or neither. A bare `"stale": true` with no
  `frame_digest` is malformed, and a client that acted on one would
  refetch on every poll of the window. Compare the digest against the
  frame on glass and ignore a match.
* Precedence. A stale response never carries `patches`: the full
  frame supersedes them.
* Locally-cached frames are never stale. A device painting a deck
  page or a collection frame from its own cache navigated there itself,
  so the live slot moving underneath is not reported against it.
* Deep sleep vs always-on. A deep-sleep device should fetch and
  paint before its window closes (that is the whole point: the
  alternative is waiting out `sleep_interval_s`). An always-on device
  should bring its next scheduled poll forward instead and let the
  panel's own refresh floor bound the repaint.

Devices predating the field ignore it and converge on their next poll
as before.

## Protocol v2 (device-owned touch)

Advertise `"proto": {"v": 2}` in register/status bodies (sticky,
persisted). A v2 device handles hit testing and immediate feedback.
`docs/protocol-v2-touch.md` defines the manifest, byte layouts, tiers
and reconnect rules. Server routes:

- `GET /api/v1/device/<id>/frame/manifest?digest=<frame>`: the
    interaction manifest. It describes regions in framebuffer coordinates,
    with stable ids, gestures, tiers and feedback. Text regions include
    glyph atlases. Action payloads never leave the server.

    Every `/frame` `200` for a proto-2 device
    carries `"manifest": {"digest", "url"}`, including re-renders that
    mint a new frame digest. A non-interactive dashboard gets a valid
    empty manifest (`regions: []`, `text: []`), never a manifest-less
    `200` (which a v2 client reads as "v1 server"). A pixel-only
    re-render keeps `manifest.digest` identical, so the device
    re-anchors its held manifest with no re-fetch.

    For an unchanged composition, empty region extraction cannot
    overwrite the sidecar. If a manifest rebuild is empty but the cached
    manifest has regions, the server re-anchors and serves the cached one.

    The endpoint accepts a just-replaced frame digest for about 60 s.
    The same grace period applies to `/frame/data`.
    For any known digest, `/frame/data` returns `200`, with empty `values`
    when nothing is live or staged. A render race cannot disable values.

    `404` means an unknown digest on `/frame/manifest`.
    On `/frame/data`, it means an unknown digest with no live render;
    otherwise the response is `200` with the stale signal above.

    Over-budget manifests keep navigation, then sliders, taps and swipes,
    in document order within each class. Dropped region ids are logged.
    Increase advertised `max_targets` if needed; the server honours up to 64.

- `POST /api/v1/device/<id>/tap` with
    `{"region_id", "gesture", "value"?, "digest", "event_id"}`: the v2
    action report. The server recreates the id from the frame's sidecar
    and applies the coordinate-stroke guards against duplicates, stale
    reports and invalid provenance before dispatch.

    Staleness is anchored to layout. A report against an older digest
    still dispatches when its region set matches the live frame.
    The server keeps about 10 digest generations per device.

    A pixel-only render cannot invalidate a tap. `stale` means the layout
    changed or the digest is too old to resolve. Outcomes are:

    - `ok`: dispatched, or correctly resolved to no action.
    - `stale` / `deduped` / `ha_failed`: the firmware's named failures.
    - Other diagnostics: `no_action_for_region`, `action_error`,
      `provenance_blocked`, `no_frame`, `resolver_exception`.

    All return HTTP 200. For every non-ok outcome, log and re-poll.
    Coordinate bodies use the v1 path and outcome names on this endpoint.

- `GET /api/v1/device/<id>/stream`: Server-Sent Events: `values` /
    `patches` / `sync` envelopes (identical payloads to the `/status`
    piggybacks and `/frame/data`), comment keepalive every 5 s. An
    optimisation over the 1 s linger poll, never a correctness
    requirement; reverse proxies must not buffer the route.

- `GET /api/v1/device/<id>/bundle` (+ `/bundle/frame/<digest>`): the
    state bundle: warmed deck pages as digest-addressed frame states plus
    a navigation links table, content-hash versioned; the `sync` event
    repeats the bundle digest. `tile` states are contract-reserved and
    not yet produced.

Touch v3, where the firmware draws and hit-tests typed primitives
itself, adds three more routes (same Bearer auth):

- `GET /api/v1/device/<id>/frame/spec`: the spec for the device's
  current frame, anchored to a layout digest that stays stable across
  data-only redraws. An empty `primitives` list is valid (a
  non-interactive dashboard). `?layout=<digest>` is advisory: the
  endpoint always answers at once with the current spec. `404` when no
  frame has rendered.
- `GET /api/v1/device/<id>/atlas/<digest>`: the 4bpp gray glyph strip
  for a touch-v3 atlas digest, content-addressed and immutable; `404`
  for an unknown digest.
- `POST /api/v1/device/<id>/interact` with
  `{"primitive_id", "interaction", "value"?, "event_id"?}`: the report
  for a primitive the firmware hit-tested locally (`interaction`
  defaults to `tap`). The server resolves the primitive's action and
  dispatches it; the answer is always `200` with an `outcome`.

## MQTT topics

All topics are namespaced under `tesserae/<device_id>/`.

| Topic | Direction | Payload | Retain | QoS | Trigger |
|---|---|---|---|---|---|
| `tesserae/<id>/frame/bin` | server → device | JSON envelope, see below | **yes** (`pi_bin`: no) | 1 | New `.bin` frame published (pico_bin, esp32, pi_bin renderers) |
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

Publishing `status` with `retain: true` supports last-will and
reconnect behaviour: new subscribers receive the latest heartbeat
without waiting for another.

## Frame formats

### `.bin` — packed binary

Used by `pi_bin`, `esp32_bin`, `pico_bin` renderers. Bit width and
byte layout depend on the panel's gamut, so a client should key its
unpack path off the `gamut` it declared in its `discover` / `register`
payload.

#### 4-bpp layout (6 and 7 colour panels)

Applies to `waveshare_e6` and `inky_7colour`. Exactly
`(width × height) / 2` bytes, no header, no padding.

- Scanline order: row 0 first, left-to-right within each row.
- High nibble = even column index (0, 2, 4, …), low nibble = odd.
- 1200 × 1600 panel → 960 000 bytes.
- Nibble values 0–15 are palette indices. Two standard palettes:
  - waveshare_e6: 6-colour Spectra 6 (default; Waveshare + ESP32).
    Firmware reserves nibbles `0x4` and `0x7` (blue remaps to `0x5`,
    green to `0x6`); output never uses those reserved values.
  - inky_7colour: Pimoroni Inky 7-colour (matches the `inky`
    library's `pal` array, so you can `display.set_pixel(x, y,
    nibble)` directly).

#### 2-bpp layout (4 colour BWRY panels)

Applies to `bwry_4` (PicPak-class 4.2" panels). Exactly
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

Used by `pi_png` and `trmnl` renderers. Decode and display with a PNG
library, such as `adafruit_imageload`, Pillow or `stb_image`.
The envelope's `rotate` / `scale` / `bg`
fields are hints for client-side fit; if your display library handles
that natively, ignore them.

All three TRMNL variants use `/api/display` and the same
`{"url": "..."}` payload. Set dithering and contrast in Settings →
Devices → General. These settings are absent from the envelope:

- `trmnl_png`: dithered to 1-bit B/W (every pixel either 0 or 255).
  The default on `trmnl_client`; right for TRMNL OG panels on firmware
  before 1.6.0 and any client that can only blit 1bpp.
- `trmnl_png_gray4`: dithered to 4 grey levels, saved as a true 2-bit
  greyscale PNG (IHDR bit depth 2, colour type 0; samples 0..3). TRMNL
  firmware 1.6.0 and later decodes it and paints 4 shades on the 7.5"
  (OG) class of panels; older firmware cannot decode it. The XIAO 7.5"
  TRMNL kind (`seeed_xiao_75`) moves onto it and off it by the
  `FW-Version` header; elsewhere it is a device-card pick.
- `trmnl_png_gray16`: dithered to 16 grey levels in an 8-bit greyscale PNG
  (mode `L`, at most 16 distinct values). For 8-bit-grey framebuffers,
  such as KOReader on a Kobo Clara HD.

    The `trmnl_x` kind uses this renderer by default with a true 4-bit
    PNG (`png_bit_depth: 4` in `protocol_config`; samples 0..15), as TRMNL
    documents for the X. Decode uses the same `RenderImage` / lodepng /
    `stb_image` path; a 1-bit-only client should stay on `trmnl_png`.
    Pick it per device with the Renderer dropdown on the device card.

Each of the three is dedicated to one gamut (`mono`, `gray_4`, `gray_16`),
so the panel's reported gamut, its preview and the MCP device list follow
the renderer picked.

Reference: [`renderers/pi_png/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/pi_png/renderer.py),
[`renderers/trmnl_png/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/trmnl_png/renderer.py),
[`renderers/trmnl_png_gray4/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/trmnl_png_gray4/renderer.py),
[`renderers/trmnl_png_gray16/renderer.py`](https://github.com/dmellok/tesserae/blob/main/renderers/trmnl_png_gray16/renderer.py).

### CircuitPython indexed `.png` / `.bmp`

The `circuitpython_generic` kind reduces colours to the panel's palette
before serving indexed PNG (`circuitpython_png`) or uncompressed BMP
(`circuitpython_bmp`). Choose with `format` on `discover` / `register`.
Both load into a `displayio.Bitmap` through `adafruit_imageload`.
The device needs no colour reduction, dithering or nibble unpacking.

Pick BMP on memory-constrained boards. `zlib.decompress` needs a full
PNG decode buffer beside the bitmap, exhausting or fragmenting Pico W
SRAM. BMP avoids `zlib`: `adafruit_imageload` reads rows with
`file.read` / `seek`, using only the framebuffer and a small row buffer.
BMP downloads are larger, but decoding uses less memory.

BMP output is uncompressed `BI_RGB`, bottom-up, at 1 bit per pixel for
`mono` and 4 bits for every other gamut. `adafruit_imageload` accepts
this format and rejects RLE-packed BMP.

Every frame has the gamut's full palette in a fixed order.
For `bwr_3`, it is always black, white, red, with `biClrUsed` set to 3.
Bit depth and colour table stay constant for that gamut.

A client can allocate its `displayio.Bitmap` from the first frame's
header and reuse it for later frames.
Even a tri-colour page without red remains a 4-bpp, 3-colour file.
The table has exactly `biClrUsed` entries, so `adafruit_imageload`
allocates a 2-bit bitmap for 3 colours: half the RAM of 16 values.

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

The 30-second `sleep_interval_s` floor protects batteries from a typo.
Mains-powered panels can stay connected for faster updates.
A sleeping device cannot receive Send, schedule or touch updates until
it wakes.

Firmware opts a board into the faster path by advertising a capability in
its register / heartbeat body:

```json
{ "can_stay_awake": true }
```

Advertise this when the power supply can sustain continuous Wi-Fi.
Withhold it or send `false` when unplugged and running on battery.
Omitting it on a heartbeat preserves the last reported value.

Only devices advertising it get the setting in their config block:

```json
{ "always_on": true, "awake_poll_s": 15 }
```

`always_on` is always present and defaults to `false`. When true:

- Skip deep sleep. Stay connected and poll `/frame` on `awake_poll_s`
  (bounds `5 ≤ awake_poll_s ≤ 300`).
- `next_poll_s` comes from `awake_poll_s`, replacing `sleep_interval_s`.
  The 30-second floor no longer applies, so content changes can shorten
  polls to single-digit seconds. Continue honouring `next_poll_s`.
- Most polls answer `304`. Re-check cheaply and only repaint the panel
  on a `200`; an e-paper refresh is the expensive part, not the request.
- Smart sync stops delaying scheduled renders for the device.
  Bound schedules run at their own times; the next poll gets the frame.

`sleep_interval_s` remains the deep-sleep interval used when `always_on`
becomes false. Keep a suitable value set.

## Scheduling and polling

A schedule or rotation changes the waiting frame and can bring the
next poll forward. It cannot wake a sleeping device.

- `next_poll_s` caps the configured wake interval at the next predicted
    content change. A schedule due in 2 minutes shortens a 15-minute
    interval to about 2 minutes, plus a 70 s margin.

    The scheduler checks every 30 s and renders each due item in turn.
    The margin avoids fetching the previous frame before rendering finishes.
    A just-passed target awaiting the next tick counts as due now;
    an early device polls again after the margin instead of its full interval.

- The interval is chosen in this order:

    1. Always-on polling interval, for a device that never sleeps.
    2. `sleep_interval_s` on the displayed dashboard.
    3. Device-instance setting.
    4. Kind-schema default.
    5. 60 s.

    A dashboard can therefore sleep longer for slow-changing content.
    A daily agenda on a five-minute panel would otherwise wake about
    288 times a day and receive 287 `304`s.

    Dashboard values are clamped to the kind's `min`/`max` and apply
    only to sleeping devices. Set them in Dashboards → Wake or over MCP
    (`PATCH /api/mcp/pages/<id>/canvas`).

- The configured interval remains the ceiling. Manual Send, webhooks,
    Home Assistant events and data-change refreshes cannot be predicted.
    Sleeping longer would delay all of them.

- Only `scheduled` and `conditional` predictions count.
    An `estimated` time is an unanchored guess and does not cause a wake.

- Widgets can report changes the schedule cannot see, such as a
    meeting ending at 15:00, a dawn bin collection or a countdown ending.
    A widget's `fetch()` may return `next_change_at` (ISO 8601 or Unix
    timestamp): when its current output becomes wrong.

    The soonest value on the page feeds `next_poll_s` alongside schedules,
    under the same ceiling and floor. It adds a 20 s render margin,
    with no scheduler tick to wait for. Widgets omitting it behave as before.

- `/frame` normally serves the last render. A poll at 15:00:20 could
    otherwise fetch a 14:45 frame with a meeting that has ended.
    If a declared change has passed and the frame predates it,
    the poll re-renders before serving.

    The hint is then consumed: at most one synchronous render per declared
    change, never per poll.

- Margins allow time for browser rendering and colour reduction.
    Polling at the exact change time could fetch the previous frame.

- A schedule is a server-side push. When it fires, the server renders
    the target page and stores the result in that device's single
    latest-frame slot (`data/core/latest_renders.json`, latest-wins).
    The device sees it on its next `/frame` poll: `200` + a new ETag,
    otherwise `304`.

- A deep-sleeping REST panel is unreachable between polls.
    It sees changes on its own next wake.

Offline devices get the latest frame, with no queue of missed updates.
If an hourly schedule fires 24 times offline, each frame replaces the
previous one. The next `/frame` returns only the current frame.

Optional smart sync renders just before the device wakes.
It uses these `/status` fields:

- `sleep_until` (absolute unix wake timestamp) is the most accurate; it
  bypasses clock-skew math on the server.
- `next_sleep_s` (seconds until the device wakes) is the fallback; the
  server adds it to the receipt time.
- Publish neither and the server predicts from the `next_poll_s` it
  handed out in the `/status` response, falling back to the configured
  `sleep_interval_s` until it has issued one.

Report wake timing when it differs from `next_poll_s`.
Echoing the same value adds nothing, since the server already uses it.
The first wake has no `next_poll_s` to echo yet.

A client that wakes on its own
schedule (a local time-table, irregular gaps, clock-aligned wakes) should
publish, because there the server's assumption is wrong and no
amount of observation will fix it.

A device becomes trusted after three consecutive wakes within ±60s
of prediction. The scheduler then renders `smart_sync_lead_s`
(default 10s) before the predicted wake. Publishing `sleep_until`
is the fastest, most reliable way to earn trust.

Smart sync improves frame freshness. It never wakes the device or
sets `next_poll_s`; content predictions set that separately.

### Synchronized wake (wake alignment)

Off by default. Enable per device or fleet-wide in Settings → Devices →
Schedule tab. The server adjusts `next_poll_s` to align wakes with
clock times. Devices sharing the same grid paint together without
a cross-device protocol.

- On the clock: `sleep_interval_s` stays the period, wakes land on
  `anchor + k × interval` in the server's local time (anchor `HH:MM`,
  default midnight). Projected content changes still pull the poll
  earlier, same as above.
- At set times: the device wakes only at a listed set of `HH:MM`
  times. This is the one case where `next_poll_s` may exceed the
  configured interval, and nothing pulls it earlier.

Grid times within quiet hours are skipped. The server learns the
device's wake-to-check-in delay and shifts its wake that much earlier.
This aligns the paint with the grid time.

No client change is required. The value is recomputed at each check-in,
so errors do not accumulate. Using `wake_at` from `/status` to compute
the delay at deep-sleep entry improves alignment further.

### Sizing `sleep_interval_s`

Use it to limit delays for changes without a predicted time.
`next_poll_s` already brings wakes forward for schedules and rotation
steps. A generous interval allows roughly one wake per content change,
avoiding a fixed grid of mostly `304` responses.

Choose the longest acceptable wait for manual Send, webhooks, Home
Assistant events and data-change refreshes. Without a bound schedule
or rotation, the device sleeps the full interval every time.

| Setup | Reasonable interval | Why |
| --- | --- | --- |
| Hourly schedule, battery panel | 4-6 h | The projection wakes you on each fire; the interval only bounds manual Send |
| 5-minute rotation | 30-60 min | Same, and the rotation steps dominate the wake count anyway |
| No schedule or rotation | The staleness you'll accept | Nothing to project, so this *is* the refresh cadence |

Setting `sleep_interval_s` to the desired refresh interval usually
causes unnecessary `304` responses.

## Reference implementations

In-tree clients you can read as worked examples:

- [`devices/pico_bin_client/`](https://github.com/dmellok/tesserae/tree/main/devices/pico_bin_client)
  RP2350 Pico Plus 2 firmware (C++), wake / fetch / paint / sleep
  over retained MQTT, `.bin` format, Pimoroni Spectra 6 panel. The
  closest in-tree analog for a CircuitPython port.
- [`devices/esp32_client/`](https://github.com/dmellok/tesserae/tree/main/devices/esp32_client)
  ESP32 firmware, same shape as pico_bin but with
  more transports and field history.
- [`devices/pi_bin_client/`](https://github.com/dmellok/tesserae/tree/main/devices/pi_bin_client)
  and [`devices/pi_png_client/`](https://github.com/dmellok/tesserae/tree/main/devices/pi_png_client)
  Python clients for always-on Pi, simpler because they don't deep
  sleep.
- [`devices/trmnl_client/`](https://github.com/dmellok/tesserae/tree/main/devices/trmnl_client)
  HTTP-pull only; no MQTT, no bearer auth (TRMNL's own protocol).
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

Smart-sync, dithering hints, TLS, and mDNS are optional.

The heartbeat updates config, clock and last-seen even when no paint
is needed. Posting after paint is also valid; see [when
to send the heartbeat](#when-to-send-the-heartbeat).

## Questions, edge cases, suggestions

If a rule is unclear, open a thread under
[GitHub Discussions](https://github.com/dmellok/tesserae/discussions).
