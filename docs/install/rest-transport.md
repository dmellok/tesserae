# REST transport

Tesserae ships two delivery transports for getting frames out to your
panels:

- **REST** (default for new installs in v0.52+). Devices poll Tesserae
  over HTTP. No broker required. Simpler setup, lower-overhead for new
  users.
- **MQTT** (the original). Devices subscribe to a broker. Lower wake-
  cycle latency (no polling interval), but you need a broker (the
  bundled amqtt or an external Mosquitto).

Both transports stay supported. Existing MQTT installs continue to work
unchanged; this page is for new installs going REST-first, and for
existing MQTT users who want to convert a device to REST.

## When to pick REST

Pick REST when:

- You don't already run an MQTT broker
- You don't want to install Mosquitto or use the bundled amqtt
- You're battery-powered + happy with the firmware-chosen poll interval
  (typically 10-60 minutes for an e-ink panel)
- You're behind NAT and don't want to expose a broker port

Pick MQTT when:

- You already run Mosquitto (Home Assistant users typically do)
- You want sub-second push from compose → panel
- You have always-on clients (Pi-side) that benefit from event-driven
  updates instead of polling

You can mix: some devices on REST, some on MQTT. Tesserae's push
pipeline reads `Device.transport` per instance and skips MQTT publish
for REST devices automatically.

## How REST works end-to-end

1. **Server hosts the endpoints**. Tesserae's HTTP server (waitress
   in prod) serves `/api/v1/device/<id>/...` routes for frame fetch,
   status heartbeats, config polls, and first-boot pairing.
2. **Firmware polls on its wake cycle**. A battery-powered client
   wakes, fetches the latest frame URL, paints it, posts a heartbeat,
   then deep-sleeps. One round trip per wake.
3. **Pairing replaces broker creds**. Instead of typing broker host +
   port + username + password into firmware, the user generates a
   6-digit pairing code in Tesserae's admin UI and pastes it into the
   firmware once on first boot. The firmware POSTs the code, gets a
   permanent device token back, persists the token in flash, and
   forgets the pairing code.

## Pairing a new REST device (UI flow)

1. **Settings → Devices → Pair new device**.
2. Click **Issue pairing code**. A six-digit code appears (10-minute
   TTL). Copy it.
3. Flash your firmware in REST mode (the firmware needs to be built
   with REST support; the [firmware prompts](#firmware) below describe
   what's needed in each codebase).
4. On the firmware's setup form (captive portal, build-time config,
   serial, depends on the platform), paste:
     - The Tesserae server URL (e.g. `http://tesserae.local:8765`)
     - The pairing code
5. First boot, the firmware POSTs `/api/v1/device/register` with the
   code. Tesserae creates a device instance with `transport: "rest"`
   and returns a per-device access token. The firmware persists the
   token; subsequent wakes use it as a bearer token.
6. The device shows up in Settings → Devices alongside any MQTT
   devices. Its meta block shows `Transport: REST` + the first few
   characters of its token.

## REST API reference

All endpoints under `/api/v1/device/`. Auth is per-device bearer token
(`Authorization: Bearer <token>` or `X-Tesserae-Token: <token>` for
firmware HTTP libs that hate Authorization headers).

### `GET /api/v1/device/<id>/frame`

Returns the latest rendered frame URL + format + panel dims for this
device.

| Status | Meaning |
|---|---|
| 200 | JSON `{url, format, panel_w, panel_h, render_id, renderer_id}`. `ETag` header carries the render digest. |
| 304 | `If-None-Match` matches current ETag; skip fetch + paint. |
| 204 | No frame has been rendered yet for this device. |
| 401 | Missing or invalid bearer token. |
| 403 | Token valid but for a different device than the URL claims. |

Use the ETag-driven 304 to save battery: a freshly-woken device whose
composition hasn't changed gets a 304 in <100 ms instead of pulling
the .bin and triggering a 10-second Spectra 6 refresh.

### `POST /api/v1/device/<id>/status`

Heartbeat. Body JSON:

```json
{
  "battery_mv": 3850,
  "battery_pct": 72,
  "rssi": -64,
  "ip": "10.0.0.42",
  "sleep_until": 1700000300.5,
  "next_sleep_s": 600,
  "fw_version": "0.1.0"
}
```

Response piggybacks the current per-device config AND the next poll
cadence:

```json
{
  "status": 200,
  "config": { "sleep_interval_s": 900 },
  "next_poll_s": 900,
  "server_time": 1700000100.5
}
```

One round trip per wake. The firmware doesn't need a separate config
poll.

### `POST /api/v1/device/register` (first boot)

Pairing flow. Headers + body:

```
X-Pairing-Code: 123456
Content-Type: application/json

{"device_id": "bedroom_pico", "kind": "pico_bin_client",
 "panel_w": 1600, "panel_h": 1200, "fw_version": "0.1.0",
 "mac": "aabbccddeeff"}
```

Response (201):

```json
{
  "status": 201,
  "device_token": "abc1...XYZ",
  "server_time": 1700000000,
  "config": { "sleep_interval_s": 900 },
  "reused_existing": false
}
```

If `device_id` already exists (firmware retry case), the response is
`200` + `reused_existing: true` + the existing token rather than
failing.

Rate-limited per client IP (10 failed attempts / 60s window). Successful
registrations release the bucket; an attacker can't grind one IP
without the rate limiter biting.

### `POST /api/v1/device/discover` (optional, first boot before pairing)

Firmware can announce itself BEFORE getting a pairing code. Useful for
"flash firmware, see if it appears in admin UI, generate code there,
flash code back to firmware" workflows.

Body:

```json
{"device_id": "fresh_pico", "kind": "pico_bin_client",
 "panel_w": 1600, "panel_h": 1200, "fw_version": "0.1.0",
 "mac": "aabbccddeeff"}
```

Response (200): `{"status": 200, "discovered": true, "next_step": "..."}`.

The entry shows up in the Discovered strip on Settings → Devices
alongside MQTT-discovered devices.

### `POST /api/v1/device/<id>/log` (optional, client diagnostics)

```json
{"level": "warn", "msg": "panel busy timeout", "extra": {...}}
```

Appended to the Tesserae EventLog so firmware logs surface on the
Events page alongside server-side events.

## Switching a device between transports

Settings → Devices → click the device card → **Switch to MQTT** (or
**Switch to REST**) at the bottom. The device's id / panel settings /
per-clone renderer settings stay; only the transport flips.

- **REST → MQTT**: drops the `transport: "rest"` flag from the manifest.
  The next render publishes to the device's status / config / frame
  topics over MQTT. The access token is kept in case the user flips
  back to REST later.
- **MQTT → REST**: sets the flag, mints a per-device access token if
  one doesn't exist, shows the token in a one-shot reveal so you can
  copy it into firmware.

## Firmware

The Tesserae repo's `notes/prompts/` directory has self-contained
prompts for Claude Code (or any AI coding assistant) for porting
existing firmware to REST:

- `notes/prompts/rest-firmware-pi.md` — Raspberry Pi clients
  (paho-mqtt → requests)
- `notes/prompts/rest-firmware-esp32-idf.md` — ESP-IDF firmware
  (esp-mqtt → esp_http_client; covers tesserae-device-esp32-bin,
  tesserae-device-esp32-bw, tesserae-device-photopainter-7.3-bin)
- `notes/prompts/rest-firmware-pico-sdk.md` — Pico SDK firmware
  (REST-only path for new RP2350 builds)

Each prompt is self-contained: drop it into a fresh Claude Code
session in the firmware repo and it has everything needed (API
reference, library suggestions, constraints, acceptance criteria).

## Security notes

- **6-digit codes have 20 bits of entropy** (~1M values). The rate
  limiter caps brute force at 10 failed attempts per IP per minute,
  so cracking a random code requires patience and is detectable in
  the logs. For LAN-only homelabs this is acceptable; if you expose
  Tesserae to the public internet, layer additional access control
  (reverse-proxy auth, Tailscale, VPN) on top.
- **Per-device tokens are 20-character alphanumeric** (~120 bits of
  entropy). Sufficient for direct bearer-token auth on a LAN; not
  designed for public internet exposure on their own.
- **The discovery endpoint is unauthenticated** (firmware has no
  token yet). It shares the register endpoint's rate limiter so an
  attacker can't spam the Discovered strip.
- **HTTP only in v1**. No TLS support yet; the server runs HTTP on
  the LAN. If you need encryption, stack a reverse proxy
  (Caddy, NGINX Proxy Manager, Cloudflare Tunnel) and have firmware
  point at the HTTPS-fronted endpoint.

## Migrating an MQTT install to REST

You don't need to. Existing MQTT installs keep working unchanged; the
REST transport sits alongside MQTT. If you want to flip a device,
use the per-device toggle described above.

If you want to flip the entire install:

1. Settings → Server → App → Default transport = REST. New devices
   added via the wizard default to REST.
2. For existing MQTT devices, use the per-device toggle to move them
   one at a time. Each switch reveals the new bearer token for that
   device which needs to be flashed into firmware.
3. Once every device is on REST, you can stop the broker (Settings →
   Server → Broker → uncheck "Built-in broker"; for external Mosquitto,
   stop the service yourself).
