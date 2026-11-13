# REST transport

Choose how your screens receive images:

- REST is the default for new installs in v0.52+. Screens check Tesserae over HTTP. No message broker needed.
- MQTT sends updates through a message broker, such as the built-in amqtt or a separate Mosquitto server. Screens receive them without waiting for a poll.

Both transports stay supported. Existing MQTT installs continue to work unchanged.

## When to pick REST

Use REST when:

- You don't have a broker and don't want to run one.
- Your battery-powered screen can wait for its next check-in, typically every 10-60 minutes.
- You're behind NAT and don't want to expose a broker port.

Use MQTT when:

- You already run Mosquitto, as many Home Assistant users do.
- You want updates in under a second.
- Your Pi or other client stays on and benefits from immediate updates.

You can use REST and MQTT devices on the same server. Tesserae only publishes MQTT messages for MQTT devices.

## MQTT ↔ REST capability mapping

| Capability | MQTT | REST |
|---|---|---|
| Frame delivery | Retained `{url}` on `tesserae/<device_id>/frame/<ext>`. Sent on subscription and each new render. | `/api/v1/device/<id>/frame` returns 200, `{url, format, panel_w, panel_h, render_id}` and an `ETag`. Delivery waits for the device's next poll. |
| Retain semantics | Broker keeps the last message indefinitely. New subscribers receive it with retain flag 1, even if unchanged. | `If-None-Match` returns 304 for an unchanged image. This skips downloading and drawing, saving battery and about 10s on Spectra 6. |
| Status heartbeat | JSON on `tesserae/<device_id>/status`. | JSON to `/api/v1/device/<id>/status`. Both update the same status indicator under Settings → Devices. |
| Config push (server → device) | Saving on Settings → Devices sends retained JSON to `tesserae/<device_id>/config` immediately. | `POST /status` returns `config` and `next_poll_s`. Settings apply on the next wake, in one exchange rather than two. |
| Discovery | Status on `tesserae/+/status` discovers unknown devices automatically. | Firmware calls `/api/v1/device/discover` without authentication. Both appear in the same Discovered strip. |
| Device → server auth | All clients share the broker username and password. | Each device has its own revocable token: `Authorization: Bearer <token>` or `X-Tesserae-Token`. Revoking one leaves others connected. |
| Server → device auth | amqtt doesn't enforce topic access rules by default. | The token must match the URL's device id. Cross-device access returns 403. |
| First-boot pairing | Enter broker host, port, username and password in firmware. | Call `/api/v1/device/discover`, click Register, then receive the token on the next `/discover` poll. Alternatively, use a 6-digit code from Settings → Devices → Add device → REST API → Issue pairing code. Call `/api/v1/device/register`, save the token and erase the code. |
| Registration retries | No registration step. | Repeating `/register` for an existing `device_id` returns 200, `reused_existing: true` and the existing token. Network retries are safe. |
| Brute-force protection | amqtt doesn't limit password guesses. Credentials are shared. | `POST /register` allows 10 failed attempts per 60s per IP. A 429 response includes `Retry-After`; successful registrations clear the count. A 6-digit code has 20 bits of entropy and needs this limit to resist guesses. |
| Connection | Persistent TCP, with keepalive and reconnection. | Stateless HTTP. Each wake makes one or two requests; no connection state to maintain. |
| Broker | Separate amqtt or Mosquitto process to monitor. | Only Tesserae is needed. Built-in amqtt remains available but isn't enabled automatically on fresh installs. |
| Home Assistant | Discovery messages on `homeassistant/+/config`. | HA discovery still needs MQTT; REST didn't implement it in v0.52. Keep a broker enabled, including with the HA App. |
| Sending to many screens | One message reaches every subscriber. | Each screen makes its own request. The difference is small for 1 to 10 panels, but matters at hundreds. |
| Debugging all devices | An MQTT explorer on `tesserae/#` sees all device traffic. | Settings → Devices shows status, without a live copy of all traffic. Every request appears in the access log. |
| Firmware logs | Read arbitrary diagnostic topics with an MQTT client. | `/api/v1/device/<id>/log` forwards logs to the EventLog under Settings → Events. |
| TLS encryption | Mosquitto supports certificates; amqtt's TLS is less tested. | HTTP only in v1. Use an HTTPS reverse proxy such as Caddy or NGINX. Both can use a proxy. |
| Update delay | Under a second for an awake subscriber. | Controlled by `next_poll_s`: default 60s for Pi, 15min for battery clients. MQTT suits previews and manual Send; REST suits battery use. |
| Setup | Install Mosquitto, edit `mosquitto.conf`, open `:1883` and enter credentials on every client. About 10 minutes for a first install. | Start Tesserae, connect the device and click Register. Under 1 minute, with no extra service or firmware code to type. |
| Migration | No migration step. | Switch each device under Settings → Devices → Switch to REST or Switch to MQTT. The token persists, so switching back takes one click. Mixed setups work. |

## How REST works end-to-end

1. Tesserae serves the `/api/v1/device/<id>/...` routes for device communication.
2. A battery-powered device wakes and downloads the image. It draws it, sends status, then sleeps. One exchange per wake.
3. On first boot, click **Register** to pair the device. Tesserae sends its access token automatically, and the device saves it in flash. You don't need broker credentials or a visible token.

## Adding a new REST device

Have **Settings → Devices** open when the device starts. You won't need to type a code or token.

1. Flash REST firmware. Set the server URL, such as `http://tesserae.local:8765`, using the firmware's setup method.
2. Start the device. It sends its MAC address and identity to `/api/v1/device/discover`.
3. Find it under **Settings → Devices → Discovered**, with its reported kind and panel size.
4. Click **Register**. Optionally rename it or change the panel preset. Tesserae creates the device and its token, matched to its MAC address.
5. On the next wake (default 30 s), firmware calls `/discover` again and receives its token. It saves the token in flash and starts polling `/api/v1/device/<id>/frame`. You don't need to copy the token.

The Discovered strip also lists MQTT and TRMNL devices alongside REST `/discover` devices. The transport column tells them apart.

### Pre-pairing with a 6-digit code (optional)

Use a one-time pairing code if you can't open the admin UI during first boot. This also suits sealed appliances or devices set up through Bluetooth.

1. Open **Settings → Devices → Add device → REST API → Issue pairing code**. The six-digit code expires after 10 minutes.
2. Enter it in the firmware's setup form with the server URL.
3. On first boot, firmware calls `/api/v1/device/register` with `X-Pairing-Code: <code>` instead of `/discover`. Tesserae creates the device and returns its token. No admin click needed.

## REST API reference

Use the [Client protocol spec](../dev/client-protocol.md) when building a client. It tracks `main` and defines the full API and device loop.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/device/<id>/frame` | Latest frame URL + format + panel dims; ETag-driven `304` when unchanged. |
| `POST /api/v1/device/<id>/status` | Heartbeat; response carries `config`, `next_poll_s`, `server_time`, and local-time fields. |
| `POST /api/v1/device/discover` | First-boot announce (unauthenticated); lands in the Discovered strip. |
| `POST /api/v1/device/register` | Pre-pairing with a 6-digit code (optional; idempotent on retry). |
| `POST /api/v1/device/<id>/log` | Forward a client log line to Settings → Events. |

These routes are under `/api/v1/device/`. Registered devices authenticate with `Authorization: Bearer <token>` or `X-Tesserae-Token`. The second header supports HTTP libraries that can't use `Authorization`.

## Switching a device between transports

Open the device card under **Settings → Devices**. At the bottom, click **Switch to MQTT** or **Switch to REST**.

Switching keeps the device id, panel settings and per-clone renderer settings.

- REST to MQTT removes `transport: "rest"` from the manifest. The next render sends status, config and frame messages over MQTT. Tesserae keeps the access token for switching back.
- MQTT to REST creates a token if needed and shows it once. Copy it into the firmware.

This only changes the server. It doesn't reconfigure the device, so the option appears only for clients that support both transports.

Boards running `tesserae-device-firmware` use REST only, as do the KOReader plugin and PicPak. This includes reTerminal, XIAO, M5Stack and Xteink. Their cards have no **Switch to MQTT**.

If an older version let you switch one of these, Tesserae returns it to REST at startup.

## Synchronized wake (optional)

Normally, devices sleep from their last check-in, so screens on the same interval drift apart. **Synchronized wake** makes them update together at clock times.

Open the device card → **Schedule** → *Advanced: synchronized wake* → **Wake on the clock**.

| Setting | When the device wakes |
|---|---|
| **On the clock** | Keep the interval, with wakes at `anchor + multiples of it` in local time. A 15-minute interval anchored at `00:00` wakes at :00 / :15 / :30 / :45. |
| **At set times only** | Ignore the interval and use listed times, such as `07:00, 12:30, 19:00`. This saves battery for pages that change a few times daily. |

Use **Apply to all** to copy the setting to every REST device. Wakes during quiet hours are skipped.

After one or two cycles, Tesserae learns the delay from waking to drawing. It adjusts wake times so images appear together. Firmware that supports `wake_at` can be more precise, but no firmware update is required.

## Firmware

To port existing firmware to REST, build against the
[Client protocol spec](../dev/client-protocol.md).

## Security notes

- A 6-digit code has 20 bits of entropy, about 1 million values. Guesses are limited to 10 failures per IP per minute and appear in logs. This is acceptable on a private LAN; public access needs extra protection, such as reverse-proxy login, Tailscale or a VPN.
- Device tokens have 20 alphanumeric characters, about 120 bits of entropy. They are suitable for a LAN, but need extra protection on the public internet.
- Discovery is unauthenticated because new devices have no token. It shares registration's rate limit to prevent spam.
- The server uses HTTP only in v1. For encryption, use an HTTPS reverse proxy such as Caddy, NGINX Proxy Manager or Cloudflare Tunnel.

## Remote access over the internet (Cloudflare Tunnel)

A Cloudflare Tunnel lets a remote screen reach your home server without forwarding router ports. Configure these settings:

1. Turn on **Settings → Server → Network & integrations → Allow REST clients on public networks**. It's off by default. Devices then receive short-lived signed image URLs; unsigned requests to `/renders/` are refused.

   With it off, images are limited to your LAN or an authenticated session. A remote device won't be able to download them.

2. If using Cloudflare Access, exclude the device paths below. Access requires interactive login, such as SSO or an email code, which a device can't complete. Add a **Bypass** policy or exclude these paths from your Access policy:

   | Path | Why it's safe to exclude from Access |
   |---|---|
   | `/api/v1/device/*` | Requires the per-device bearer token; cross-device access is refused with 403. |
   | `/renders/*` | Served through the short-lived signed URLs from step 1; unsigned requests are refused. |

   Everything else, including the admin UI, stays behind Access.

Pair the device on your LAN before taking it elsewhere. Use the 6-digit code method above. This avoids exposing `/register` publicly, even though it is rate-limited.

## Migrating an MQTT install to REST

To move a device to REST, use the per-device toggle described above.

To move the whole setup:

1. Set **Settings → Server → Network & integrations → Default transport for new devices** to REST. The device wizard then uses REST by default.
2. Switch existing devices one at a time. Each switch shows a bearer token that you must add to the firmware.
3. Once all devices use REST, stop the broker. For the built-in broker, uncheck **Settings → Server → MQTT broker → Built-in broker**. Stop an external Mosquitto service yourself.
