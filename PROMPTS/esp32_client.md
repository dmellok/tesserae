# Prompt — ESP32 + e-paper client

> Hand this whole file to Claude Code (or whatever coding assistant)
> when wiring up an ESP32 + e-paper frame. The ESP32's job is to wake
> up, fetch the latest frame from MQTT, paint the panel, and sleep
> again — minimising power so the thing runs for months on a
> Li-Po.

## What you're building

ESP32 firmware (ESP-IDF or Arduino — your choice; this prompt assumes
**Arduino + PubSubClient + GxEPD2** because that's the most common
hobbyist stack):

1. Wake from deep sleep
2. Connect to Wi-Fi
3. Connect to MQTT
4. Publish a heartbeat (battery, RSSI, IP, last-paint, wake-reason)
5. Subscribe to the frame topic (retained, so the message arrives immediately)
6. Decode the payload into the panel's pixel buffer
7. Paint the panel
8. Subscribe to the config topic (retained) — read sleep_interval_s
9. Deep-sleep for that interval
10. Repeat

A full wake-paint-sleep cycle should take ~15–25 seconds with a fast
panel (e.g. Waveshare E6 7.5″), most of which is the paint + Wi-Fi
handshake.

## Hardware target

- **MCU**: any ESP32 (ESP32-S3 is what most current LiPo dev boards
  use — LilyGo T5, Inkplate, FireBeetle 2 E-Paper). Vanilla ESP32
  works fine; ESP8266 doesn't have the RAM for the framebuffer.
- **Panel**: Waveshare E6 (Spectra 6) 7.5″ or smaller, OR any
  GxEPD2-supported panel. The Waveshare E6 7.5″ is 800×480 at 4 bpp
  → 192 KB framebuffer — fits in PSRAM comfortably.
- **Battery**: a Li-Po with a charge IC (TP4056 etc.). 1000–2000 mAh
  gets months of battery at 30-minute wake intervals.

## MQTT topics

The `<prefix>` is whatever you set as the device's `topic_prefix` in
Tesserae's Settings → Devices. Defaults to `esp32` for the built-in
`esp32_client` device. For a custom instance: `esp32-kitchen`, etc.

| Direction | Topic | QoS / Retained | Payload |
|---|---|---|---|
| Subscribe | `tesserae/<prefix>/frame/bin` | QoS 1, **retained** | 4-bpp packed pixel buffer + small header (see below) |
| Subscribe | `tesserae/<prefix>/config` | QoS 1, **retained** | JSON: `{ "sleep_interval_s": 1800 }` |
| Publish | `tesserae/<prefix>/status` | QoS 1, **retained** | JSON heartbeat (see below) |

The frame topic is **retained**. That's the magic: as soon as your
ESP32 subscribes after deep-sleep wake, the broker delivers the most
recent frame within ~100 ms. No round-trip to ask for it.

## Frame payload (`.bin`)

Layout: a tiny header followed by row-major 4-bpp packed pixels.

```
[ 4-byte magic: 'TSR1' ]
[ 2-byte width  (LE) ]
[ 2-byte height (LE) ]
[ 1-byte palette_id ]   // 0 = Spectra 6 (default)
[ 1-byte reserved ]
[ width * height / 2  bytes of 4-bpp pixel data, row-major ]
```

> If the renderer ships without the header (some early versions did
> raw pixels only), peek at the first 4 bytes — if they're `'TSR1'`
> use the header; otherwise assume defaults from your device config
> (you know your panel dims).

### Spectra 6 palette (palette_id 0)

| Index | Colour | Approx sRGB |
|---|---|---|
| 0 | Black | `#000000` |
| 1 | White | `#ffffff` |
| 2 | Green | `#3aaa3a` |
| 3 | Blue | `#1c4eaf` |
| 4 | Red | `#c43a2a` |
| 5 | Yellow | `#e8c41a` |
| 6 | Orange | `#d77a25` |

Indices > 6 should be treated as white.

### Decoding into the panel

Two pixels per byte: high nibble = left pixel, low nibble = right
pixel. Iterate row-major, look up the palette colour, push into the
GxEPD2 buffer.

```cpp
for (int y = 0; y < height; y++) {
  for (int x = 0; x < width; x += 2) {
    uint8_t byte = pixels[(y * width + x) / 2];
    uint8_t left  = (byte >> 4) & 0x0F;
    uint8_t right = byte & 0x0F;
    display.drawPixel(x,     y, palette[left]);
    display.drawPixel(x + 1, y, palette[right]);
  }
}
display.display(false);  // partial=false → full refresh
```

## Heartbeat payload

JSON, published on wake (before painting if you want fast UI feedback,
or after if you want to record paint duration). Retained.

```json
{
  "state": "painting",          // "painting" | "sleeping" | "boot"
  "wake_reason": "timer",       // "timer" | "wifi_fail" | "boot"
  "battery_pct": 78,            // 0–100 (read from ADC)
  "battery_v": 3.92,            // raw voltage
  "rssi": -64,                  // dBm
  "ip": "192.168.1.71",
  "uptime_awake_s": 12,         // since last boot
  "version": "0.3.1",
  "panel": "waveshare_e6_7_5"
}
```

## Config payload (read on wake)

The broker pushes the retained config straight away. Read it once,
apply, sleep.

```json
{
  "sleep_interval_s": 1800
}
```

If the message hasn't arrived within 2 s of subscribing, fall back to
your stored / default value (`900` is reasonable).

## Power budget reference

For a single wake-paint-sleep cycle on an ESP32-S3 + Waveshare E6
7.5″ at 1800 s intervals:

| Phase | Duration | Current | Energy |
|---|---|---|---|
| Wake + Wi-Fi connect | ~3 s | 200 mA | 0.6 J |
| MQTT handshake + frame | ~2 s | 100 mA | 0.2 J |
| Panel paint | ~12 s | 80 mA | 0.95 J |
| Heartbeat + sleep | ~1 s | 80 mA | 0.08 J |
| Deep sleep (1800 s) | 1800 s | 20 μA | 0.13 J |
| **Total per cycle** | ~1818 s | — | **~2 J** |

A 2000 mAh Li-Po stores ~26.6 kJ → ~13,000 cycles → ~270 days at 30-min
intervals before recharge. Bumping intervals to 1 hour roughly doubles
the battery life. Going every 5 min cuts it to ~70 days.

## Code skeleton (Arduino + ESP32-S3 + GxEPD2)

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <GxEPD2_BW.h>     // or _3C / _4C / _7C depending on your panel
#include <ArduinoJson.h>

const char* WIFI_SSID = "...";
const char* WIFI_PASS = "...";
const char* MQTT_HOST = "192.168.1.10";
const int   MQTT_PORT = 1883;
const char* MQTT_USER = "tesserae";
const char* MQTT_PASS = "...";
const char* DEVICE_PREFIX = "esp32";    // match Settings → Devices

WiFiClient   wifi;
PubSubClient mqtt(wifi);

RTC_DATA_ATTR uint32_t boot_count = 0;

uint8_t framebuf[800 * 480 / 2];  // 4-bpp, panel-sized

void onFrame(char* topic, byte* payload, unsigned int length) {
  // Validate magic. If absent, treat the whole payload as raw pixels
  // (some renderer versions ship without the header).
  uint8_t* pixels = payload;
  size_t   pxlen  = length;
  if (length >= 10 && payload[0]=='T' && payload[1]=='S' && payload[2]=='R' && payload[3]=='1') {
    uint16_t w = payload[4] | (payload[5] << 8);
    uint16_t h = payload[6] | (payload[7] << 8);
    pixels = payload + 10;
    pxlen  = length  - 10;
    // (validate w/h match your panel)
  }
  memcpy(framebuf, pixels, min((size_t)sizeof(framebuf), pxlen));
  // ... then iterate framebuf and push into GxEPD2 (see Decoding above).
}

uint32_t sleep_interval_s = 1800;

void onConfig(char* topic, byte* payload, unsigned int length) {
  StaticJsonDocument<128> doc;
  if (!deserializeJson(doc, payload, length)) {
    sleep_interval_s = doc["sleep_interval_s"] | 1800;
  }
}

void publishHeartbeat() {
  StaticJsonDocument<256> doc;
  doc["state"]       = "painting";
  doc["wake_reason"] = "timer";
  doc["battery_pct"] = readBatteryPct();
  doc["rssi"]        = WiFi.RSSI();
  doc["ip"]          = WiFi.localIP().toString();
  doc["version"]     = "0.3.1";
  char buf[256];
  size_t n = serializeJson(doc, buf);
  String topic = String("tesserae/") + DEVICE_PREFIX + "/status";
  mqtt.publish(topic.c_str(), (uint8_t*)buf, n, true);
}

void setup() {
  boot_count++;
  Serial.begin(115200);

  // Wi-Fi
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(100);

  // MQTT
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback([](char* t, byte* p, unsigned int l){
    String topic = t;
    if (topic.endsWith("/frame/bin"))     onFrame(t, p, l);
    else if (topic.endsWith("/config"))   onConfig(t, p, l);
  });
  mqtt.connect("esp32-painter", MQTT_USER, MQTT_PASS);
  String frameTopic  = String("tesserae/") + DEVICE_PREFIX + "/frame/bin";
  String configTopic = String("tesserae/") + DEVICE_PREFIX + "/config";
  mqtt.subscribe(frameTopic.c_str(),  1);
  mqtt.subscribe(configTopic.c_str(), 1);

  publishHeartbeat();

  // Wait up to 2s for retained frame + config to arrive
  unsigned long deadline = millis() + 2000;
  while (millis() < deadline) mqtt.loop();

  // ... display already painted by onFrame ...

  mqtt.disconnect();
  WiFi.disconnect(true);

  // Deep sleep
  esp_sleep_enable_timer_wakeup((uint64_t)sleep_interval_s * 1000000ULL);
  esp_deep_sleep_start();
}

void loop() {}  // unreached — we deep-sleep at end of setup()
```

## Things that will bite you

- **Retained config not arriving**: if you publish a config-topic msg
  from Tesserae but the ESP32 never wakes, the broker keeps it for
  next wake (that's the point of retain). But if the broker dies and
  loses its retained state, your ESP32 will use the fallback. Set a
  sane fallback (1800 s).
- **Wi-Fi handshake taking forever**: WPA3 + 802.11ax can take
  3–4 s. If you can, use a WPA2-only SSID for the ESP32 or pin a
  static IP to skip DHCP.
- **Battery measurement on dev boards**: the ADC pin and divider
  ratio varies by board. LilyGo, FireBeetle, M5Paper, Inkplate all
  differ. Check your board's wiring.
- **Watchdog on long paint**: panels >7″ take 10+ s. Disable the
  task watchdog during paint or feed it from a high-priority task.

## Verifying

1. Flash, plug in USB, watch serial — first boot should log
   `Heartbeat published` and `Frame received: 192000 bytes`.
2. In Tesserae's Settings → Devices, your device should turn green
   within a minute.
3. Open Settings → Renderers → Diagnostics → "Test push (synthetic
   image)". A coloured test pattern should appear on the panel on
   the ESP32's next wake.
4. Set `sleep_interval_s` short (60) for testing; bump it back up
   once you've confirmed the loop works.
