# Prompt — Raspberry Pi + Inky panel client

> Hand this whole file to Claude Code (or whatever coding assistant)
> on a Raspberry Pi to build the on-device painter. The Pi just
> subscribes to a couple of MQTT topics, decodes the payload, and
> paints whatever Tesserae publishes.

## What you're building

A Python long-running process on a Raspberry Pi that:

1. Subscribes to a Tesserae renderer's MQTT topic
2. Decodes the payload (PNG or packed `.bin`)
3. Paints the panel via Pimoroni's `inky` (or equivalent) library
4. Publishes a heartbeat back to Tesserae every N seconds

The Pi is a passive receiver. Tesserae's renderer publishes the frame;
the Pi paints it. Nothing fancy — no local compositing, no caching.

## Hardware target

- Raspberry Pi (Zero 2 W is enough; Pi 4 is overkill, Pi 3 works)
- Pimoroni Inky Impression (any size — 4", 5.7", 7.3", 13.3" all work the same way)
- 64-bit Raspberry Pi OS (Bookworm or later) — 32-bit works but Playwright won't, irrelevant for this client

## MQTT topics (per-device — substitute your prefix)

Tesserae's renderer publishes to `tesserae/<device>/frame/<format>` and
expects a heartbeat at `tesserae/<device>/status`. The `<device>` slot
is whatever you set as the device's `topic_prefix` in Tesserae's
Settings → Devices.

Two Pi kinds ship with Tesserae — pick the one matching what you're
building:

- **`pi_bin_client`** → default prefix `pi_bin` → subscribes to
  `tesserae/pi_bin/frame/bin` (or your instance prefix).
- **`pi_png_client`** → default prefix `pi_png` → subscribes to
  `tesserae/pi_png/frame/png` (or your instance prefix).

For a custom instance (one Pi running pi_bin client in the kitchen, a
second running pi_png in the hallway) you might use prefixes like
`pi-kitchen`, `pi-hallway`, etc.

| Direction | Topic | QoS / Retained | Payload |
|---|---|---|---|
| Subscribe | `tesserae/<prefix>/frame/png` | QoS 1, **not retained** | Pi-PNG: a PNG already rotated to the panel's landscape pixel grid, plus a 1-byte `options` header (see below) |
| Subscribe | `tesserae/<prefix>/frame/bin` | QoS 1, **retained** | Pi-bin: a raw 4-bpp packed buffer ready to stream to the Inky's SPI |
| Publish | `tesserae/<prefix>/status` | QoS 1, **retained** | JSON heartbeat (see below) |

You can subscribe to just one of the two frame topics depending on
what your firmware supports. PNG is friendlier (any image library can
decode it); `.bin` is faster but locks you to a specific palette /
panel pixel layout.

### Pi-PNG payload format

The retained PNG payload is **PNG bytes + 1 byte options trailer**.
The trailer is at the end:

```
[ PNG bytes ........................ ][ options_byte ]
```

`options_byte` bits, MSB to LSB:

| Bit | Meaning |
|---|---|
| 7-6 | `rotate` (0/1/2/3 quarter-turns CW the firmware should apply on top) |
| 5-4 | `scale_mode` — 0=fit, 1=fill, 2=stretch, 3=center |
| 3-0 | `bg_color` index when letterboxing (0=white, 1=black, 2=red, 3=green, 4=blue, 5=yellow, 6=orange) |

For a v1 you can ignore the trailer entirely and just decode the PNG
bytes — the renderer already applied the right transforms server-side
when `transform_rotate_quarters` is set. The trailer is for clients
that want client-side flexibility.

### Pi-bin payload format

`.bin` is raw 4-bpp packed pixel data: two pixels per byte, palette-
indexed (Spectra 6 / Spectra 6 colour table — black, white, yellow,
red, blue, green, sometimes orange). Layout is row-major,
panel-landscape-native. Stream straight to SPI.

For Spectra 6 the palette index ↔ colour mapping (in nibble order):

| Index | Colour |
|---|---|
| 0 | Black |
| 1 | White |
| 2 | Green |
| 3 | Blue |
| 4 | Red |
| 5 | Yellow |
| 6 | Orange |

### Status / heartbeat payload

JSON, published every 30–60 s (retained). Tesserae's UI shows the
parsed fields; if you skip or rename a field, the UI falls back to
showing the raw payload, so it won't crash.

```json
{
  "state": "idle",          // "idle" | "rendering" | "sleeping"
  "ip": "192.168.1.42",     // last known LAN IP
  "rssi": -53,              // dBm
  "last_paint": "2026-05-28T07:42:11Z",  // ISO 8601
  "uptime_s": 38421,
  "version": "0.2.0",       // your firmware version
  "panel": "inky_13_3"      // optional; informational
}
```

## Code skeleton

Reference implementation in ~150 lines using `paho-mqtt` and `inky`:

```python
import json, time, signal
import paho.mqtt.client as mqtt
from inky.auto import auto

BROKER_HOST  = "192.168.1.10"      # your Tesserae broker
BROKER_PORT  = 1883
DEVICE_PREFIX = "pi"                # match Settings → Devices → topic prefix
USERNAME, PASSWORD = "tesserae", "..."  # if your broker requires auth

display = auto()                    # auto-detect the connected Inky

def on_frame_png(_client, _ud, msg):
    raw = msg.payload
    # Last byte is the options trailer (see Pi-PNG payload above);
    # decode the PNG from raw[:-1].
    png_bytes, options_byte = raw[:-1], raw[-1]
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png_bytes))
    img = img.convert("RGB").resize((display.width, display.height))
    display.set_image(img)
    display.show()

def on_connect(client, *_):
    client.subscribe(f"tesserae/{DEVICE_PREFIX}/frame/png", qos=1)

def publish_heartbeat(client):
    payload = {
        "state": "idle",
        "ip": "...",     # socket.gethostbyname(socket.gethostname())
        "uptime_s": int(time.monotonic()),
        "version": "0.2.0",
    }
    client.publish(
        f"tesserae/{DEVICE_PREFIX}/status",
        json.dumps(payload), qos=1, retain=True,
    )

client = mqtt.Client(client_id=f"{DEVICE_PREFIX}-painter")
client.username_pw_set(USERNAME, PASSWORD)
client.on_connect = on_connect
client.message_callback_add(f"tesserae/{DEVICE_PREFIX}/frame/png", on_frame_png)
client.connect(BROKER_HOST, BROKER_PORT)
client.loop_start()

# Heartbeat loop
signal.signal(signal.SIGINT, lambda *_: (client.loop_stop(), exit(0)))
while True:
    publish_heartbeat(client)
    time.sleep(45)
```

For a real install you want:

- A **systemd unit** so it restarts on crash and starts on boot
- **TLS to the broker** if the broker is on a different host than the Pi
- **A retry loop** on `inky` exceptions (the SPI driver hiccups
  occasionally on warm boots)
- A **log to journald** at INFO so you can `journalctl -fu tesserae-pi`

## What Tesserae does NOT need from you

- No HTTP server. The Pi is purely an MQTT client.
- No HA discovery. Tesserae publishes its own discovery to MQTT.
- No firmware OTA. If you want OTA, that's on you.

## Verifying

After you flash + boot the Pi:

1. In Tesserae's Settings → Devices, your device should turn green
   (heartbeat received within the last minute).
2. Open Settings → Renderers → Diagnostics and hit "Test push" — a
   synthetic image should hit your panel within a few seconds.
3. Assign a page to your device in the page editor (Target device
   dropdown) and hit Push from the Send tab.
