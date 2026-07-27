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

## MQTT ↔ REST capability mapping

If you're coming from a working MQTT setup, this table shows the REST
equivalent of every MQTT capability Tesserae uses.

| Capability | MQTT (current) | REST (v0.52+) | Notes / trade-offs |
|---|---|---|---|
| **Frame delivery** | Server publishes retained message to `tesserae/<device_id>/frame/<ext>` with payload `{url}`. Device subscribes; broker pushes message on subscribe (retained) and on each new render. | Server caches latest frame per device. Device GETs `/api/v1/device/<id>/frame` → 200 + `{url, format, panel_w, panel_h, render_id}` + `ETag` header. | MQTT is sub-second push; REST is bounded by the device's poll interval. Battery devices wake on a cycle anyway, so for the e-ink target the difference is theoretical. |
| **Retain semantics** | Broker stores last retained message indefinitely. Fresh subscriber gets it on connect, retain flag = 1 on the inbound. | Server stores latest render per device in `data/core/latest_renders.json`. Any GET returns it. ETag-driven `If-None-Match` returns 304 if unchanged, so a sleeping client saves a fetch + paint cycle when nothing changed. | REST is actually richer: the 304 path saves panel-refresh time (~10s on Spectra 6) AND battery. MQTT retain doesn't tell the client "this is the same as last time." |
| **Status heartbeat** | Device publishes JSON to `tesserae/<device_id>/status`. Server subscribes (per-device + wildcard for discovery). | Device POSTs JSON to `/api/v1/device/<id>/status`. | Parsed via the same `device.parse_status` hook in both transports; merged into the same `DEVICE_STATUS` cache. Settings → Devices freshness dot looks identical. |
| **Config push (server → device)** | Server publishes retained JSON to `tesserae/<device_id>/config` when user saves on Settings → Devices. Device subscribes. | Server's response to `POST /status` includes the current `config` block and `next_poll_s`. Firmware applies on each wake. | MQTT propagates config in real time; REST propagates on the device's next wake. For sleep intervals measured in minutes / hours, both feel equivalent. One round-trip per wake instead of two. |
| **Discovery (unknown device)** | Server wildcard-subscribes to `tesserae/+/status`. Any heartbeat from an unregistered device id appears in the Discovered strip. | Firmware POSTs `/api/v1/device/discover` (unauthenticated). Entry added to the same `DiscoveryCache` that MQTT feeds. Shows up in the same Discovered strip. | MQTT discovery is incidental (any client publishing status is discovered). REST requires an explicit announce call. Either way ends up in the same admin UI. |
| **Device → server auth** | Broker-level: username + password set in broker config, used by every client. One shared credential. | Per-device bearer token in the `Authorization: Bearer <token>` header (or `X-Tesserae-Token` fallback). Tokens are unique per device, revocable per device. | REST is a meaningful security improvement: revoking one device's access doesn't affect the others. |
| **Server → device auth** | Same broker creds; topic ACLs are not enforced by amqtt's default config. | Server-side token validation checks the URL device id matches the token's owner. Cross-device access returns 403. | REST has real per-device isolation at the auth layer; MQTT's bundled-broker setup gives effectively shared credentials. |
| **First-boot pairing** | User flashes broker host + port + username + password into firmware (captive portal, build-time config, etc.). | User generates a 6-digit pairing code on Settings → Devices → Pair new device, flashes that into firmware. Firmware POSTs `/api/v1/device/register` once, persists the device token, wipes the pairing code. | REST replaces "type 4 broker fields" with "type 1 pairing code." Friendlier UX, single rotation point if compromised. |
| **Idempotent registration** | N/A (no register step). | POST `/register` with an already-registered `device_id` returns 200 + `reused_existing: true` + the existing token. A firmware retry after a network glitch is safe. | MQTT doesn't have this problem because there's no pairing step. |
| **Brute-force protection** | None at the broker level (amqtt) for credential guessing. The shared credential makes guessing one device's worth of access trivial anyway. | `POST /register` is rate-limited (10 failed attempts / 60s / IP) with `Retry-After` on 429. Successful registers release the bucket. | A real win for REST. The 6-digit code's 20 bits of entropy is meaningful only with rate limiting; without it, a brute force takes minutes. |
| **Transport lifecycle** | Persistent TCP connection. Keepalive packets. Reconnect logic on broker drop. | Stateless HTTP. No connection to maintain. | REST is simpler in firmware: no connection state, no keepalive, no reconnect path. Each wake makes one or two requests and exits. |
| **Server lifecycle / broker** | Needs a broker process (bundled amqtt or external Mosquitto). Broker has its own restart / health / log story. | Just the Tesserae server itself. | One fewer service to operate. The bundled amqtt stays in tree for users who want MQTT but is no longer auto-enabled on fresh installs. |
| **HA Assistant integration** | Tesserae publishes HA MQTT discovery configs to `homeassistant/+/config`. HA picks them up via its own MQTT subscription. | Not implemented for REST in v0.52. HA discovery still requires an MQTT broker. | Phase 2+ work. For now, HA integration users keep MQTT enabled or use Tesserae's HA App with Mosquitto. |
| **Multicast / wildcard fanout** | One published message reaches every subscriber; broker handles fanout. | Each subscriber GETs independently. Each request hits the server. | Doesn't matter for the typical e-ink fleet (1–10 panels). Would matter at hundreds. |
| **Cross-device observability** | One MQTT explorer subscribed to `tesserae/#` sees the entire fleet's traffic. | One admin session sees device status via Settings → Devices. No equivalent "tee" of all device traffic. | Operationally MQTT is easier to debug live. REST is easier to debug from logs (every request is in the access log). |
| **Manual firmware diagnostics** | Firmware can publish to arbitrary topics; admin reads via any MQTT client. | Firmware POSTs `/api/v1/device/<id>/log` → server's EventLog. Visible on Settings → Events. | REST integrates with the EventLog instead of needing an external client. |
| **TLS** | Mosquitto can do TLS with certificate management. amqtt's TLS story is less battle-tested. | HTTP only in v1. HTTPS via reverse proxy (Caddy / NGINX) works since firmware just sees the URL. | Equivalent in practice if both rely on a reverse-proxy frontend. |
| **Latency from compose → panel** | Sub-second (publish + subscriber wake). | Bounded by `next_poll_s`. Default 60s for a Pi client, 15min for a battery client. | MQTT wins when "instant" matters (dev preview / manual Send). REST wins everywhere battery is the priority. |
| **Setup / install cost (new user)** | Install Mosquitto, edit `mosquitto.conf`, open `:1883`, generate creds, paste into Tesserae + every client. ~10 minutes for a first-time installer. | Run Tesserae. Plug the device in; it shows up in the Discovered strip; click Register. No service to install, no code typed into firmware. < 1 minute. | The headline reason REST is the v0.52+ default. |
| **Migration** | N/A. | Per-device flip: Settings → Devices → Switch to REST (or to MQTT). Token persists across flips so flipping back is one click. | A heterogeneous fleet is fine — some devices MQTT, some REST. |

## How REST works end-to-end

1. **Server hosts the endpoints**. Tesserae's HTTP server (waitress
   in prod) serves `/api/v1/device/<id>/...` routes for frame fetch,
   status heartbeats, config polls, and first-boot registration.
2. **Firmware polls on its wake cycle**. A battery-powered client
   wakes, fetches the latest frame URL, paints it, posts a heartbeat,
   then deep-sleeps. One round trip per wake.
3. **No broker credentials to thread.** Instead of typing broker host
   + port + username + password into firmware, the device announces
   itself to Tesserae on first boot, you click **Register** in the
   admin UI, and Tesserae hands the device its per-device access
   token automatically. The token is stored in flash; the user never
   sees it.

## Adding a new REST device

The default path is zero-typed-credentials, you just need to be at
the admin UI when the device boots.

1. Flash your firmware in REST mode and point it at the Tesserae
   server URL (e.g. `http://tesserae.local:8765`) via whatever
   first-boot mechanism the firmware uses (captive portal,
   build-time config, serial). No code or token needed at this step.
2. Power the device on. On first boot it POSTs
   `/api/v1/device/discover` with its MAC address and identity.
3. **Settings → Devices**. The device shows up under the **Discovered**
   strip with the announced kind + panel dims.
4. Click **Register**. Optionally rename / pick a different panel
   preset. Tesserae creates the instance, mints a per-device access
   token, and primes its MAC.
5. The firmware retries `/discover` on its next wake (default 30 s),
   sees the MAC now matches, and receives the token in the
   response. From here it polls `/api/v1/device/<id>/frame` like any
   other device. The token persists in flash; you never see it
   again.

The same Discovered strip surfaces MQTT-discovered devices, TRMNL
self-provisioning announces, and REST `/discover` polls; the only
difference is the transport column.

### Pre-pairing with a 6-digit code (optional)

For environments where you can't be at the admin UI when the device
first boots, sealed appliances, BLE-provisioned devices, devices
shipped on a kiosk image, you can pre-mint a token via a one-shot
pairing code instead.

1. **Settings → Devices → Pair new device**. Click **Issue pairing
   code**. A six-digit code appears (10-minute TTL).
2. Type the code into the firmware's setup form alongside the
   server URL.
3. First boot, the firmware POSTs `/api/v1/device/register` with
   `X-Pairing-Code: <code>` instead of `/discover`. Tesserae creates
   the instance and returns the token in the same response, no
   admin click required.

Either path yields the same end state: a registered device with a
per-device bearer token. Pick whichever matches your bring-up flow.

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
  "server_time": 1700000100
}
```

One round trip per wake. The firmware doesn't need a separate config
poll.

### `POST /api/v1/device/register` (first boot, optional)

Used by the pre-pairing flow above. The default first-boot path is
`/discover` + admin Register click; only reach for this endpoint
when you need to pre-mint a token. Headers + body:

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

### `POST /api/v1/device/discover` (first boot)

The default first-boot endpoint. Firmware announces itself here so
the device shows up under the Discovered strip in Settings →
Devices; admin clicks **Register** and Tesserae returns the token
on the firmware's next poll.

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
