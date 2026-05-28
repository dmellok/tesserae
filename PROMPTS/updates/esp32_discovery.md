# Update: esp32-inky-dash-client → discovery heartbeat fields

## Why

Tesserae now listens on `tesserae/+/status` and surfaces any
unregistered device id in Settings → Devices as a "Discovered" row
with a one-click Register button. To make that one-click flow
pre-fill the kind and panel size, this client needs to embed a few
well-known keys in its heartbeat JSON.

Apply this **after** the [esp32_multihead.md](esp32_multihead.md)
update — that one introduced the NVS `device_id` field and the
always-on settings server; this one extends the heartbeat payload
the device publishes to `tesserae/<device_id>/status`.

## Goal

Heartbeat JSON published to `tesserae/<device_id>/status` includes
these top-level keys (in addition to whatever the firmware already
sends — battery, RSSI, etc. flow through unchanged):

| Key | Type | Source |
|---|---|---|
| `kind` | string — always `"esp32_client"` | constant |
| `panel_w` | uint16 (px) | `EPD_WIDTH` from app_config.h |
| `panel_h` | uint16 (px) | `EPD_HEIGHT` from app_config.h |
| `fw_version` | string | `FW_VERSION` macro (set in platformio.ini via `build_flags = -DFW_VERSION=\"0.3.0\"`) |
| `ip` | string | STA IP from `esp_netif_get_ip_info` |

These fields are static for the device's lifetime (no need to
recompute every heartbeat), but the JSON serializer still includes
them every time — Tesserae's parser merges keys across heartbeats so
omitting any of them after the first wake would still work, but it's
simpler to always send.

## Implementation

### 1. `heartbeat.h` / `heartbeat.c` — extend the payload builder

Find the function that constructs the heartbeat JSON (probably
`heartbeat_build_payload` or inlined in `mqtt_handler.c`). Add the
new fields to the JSON object.

If you're using ESP-IDF's `cJSON`:

```c
cJSON_AddStringToObject(root, "kind", "esp32_client");
cJSON_AddNumberToObject(root, "panel_w", EPD_WIDTH);
cJSON_AddNumberToObject(root, "panel_h", EPD_HEIGHT);
cJSON_AddStringToObject(root, "fw_version", FW_VERSION);
cJSON_AddStringToObject(root, "ip", ip_str);  /* see below */
```

If you hand-formatted JSON via `snprintf`, just extend the format
string. Don't forget to bump the buffer size budget.

### 2. STA IP helper

`wifi_manager.c` already has the IP from `esp_netif_get_ip_info`.
Expose it via a small accessor:

```c
/* wifi_manager.h */
bool wifi_manager_get_sta_ip(char *out, size_t out_sz);

/* wifi_manager.c */
bool wifi_manager_get_sta_ip(char *out, size_t out_sz)
{
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (!netif) return false;
    esp_netif_ip_info_t ip;
    if (esp_netif_get_ip_info(netif, &ip) != ESP_OK) return false;
    snprintf(out, out_sz, IPSTR, IP2STR(&ip.ip));
    return true;
}
```

The heartbeat builder calls this once per wake; if it fails (e.g.
the STA interface isn't up yet — unlikely if we just published an
MQTT message, but be defensive), emit `""` or skip the key.

### 3. `FW_VERSION` macro

Add to `platformio.ini` under `[env:...]`:

```ini
build_flags =
    -DFW_VERSION=\"0.3.0\"
```

If you want the version baked from `git describe --tags --dirty`,
add a `pre:` script in PlatformIO that resolves it and exports the
macro. For v1, a hardcoded string is fine — bump it manually when
you cut a release.

### 4. Tests

The ESP32 codebase doesn't have a unit test harness today (it's all
on-device). Skip formal tests; verify by hand (see below).

## Verification

1. **Manual** — flash the firmware, watch the boot log: it should
   publish a heartbeat with the new fields. `mosquitto_sub -t
   'tesserae/+/status' -v` on the broker host confirms the JSON.
2. **End-to-end** — in Tesserae's Settings → Devices, before
   registering this device, the "Discovered devices" strip should
   appear with the right kind chip (`esp32_client`), the panel dims
   pre-filled (1200×1600 for the Waveshare 13.3), and the STA IP
   shown. Clicking Register creates the instance without prompting
   for kind / panel.
3. **Edge** — if the heartbeat fires before STA fully has an IP
   (shouldn't happen with the current wake sequence, but worth
   eyeballing), the `ip` key should be empty / absent and the rest
   of the payload still valid JSON.

## Out of scope

* No discovery payload on the legacy `inky/esp32/...` topics — only
  the new `tesserae/<device_id>/status` topic carries the structured
  heartbeat.
* No firmware-side version negotiation. If a field changes meaning
  later, Tesserae will need to handle the migration.
