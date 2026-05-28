# Update: esp32-inky-dash-client → multi-head + always-on portal

## Why

Two related changes from the Tesserae server side:

1. **Named devices**: the server now supports multiple ESP32 panels.
   Each instance has its own MQTT topic namespace:

   ```
   tesserae/<device_id>/frame/bin   ← retained frame announcement (subscribed)
   tesserae/<device_id>/status      ← heartbeat (published, retained)
   tesserae/<device_id>/config      ← sleep_interval etc. (subscribed)
   ```

   The default kind still uses `device_id="esp32"` for back-compat, but
   the firmware currently hardcodes that prefix in three places. A
   second physical ESP32 needs a different id.

2. **Always-on settings portal**: the captive portal currently only
   runs when STA association fails. We want the same form reachable
   **after** the device is on the LAN so settings can be edited
   without erasing NVS / re-flashing.

Both share the same NVS-backed config struct, so it's natural to do
them together.

## Goal

* Replace the single `mqtt_topic` NVS field with a `device_id` field
  (1–32 chars, `[a-z0-9_-]`, starts with a letter). Derive
  `update_topic`, `config_topic`, and `status_topic` from
  `tesserae/<device_id>/...` at runtime.
* Migrate existing installs: on first boot after the firmware update,
  if NVS has the legacy `mqtt/topic` key set to something matching
  `tesserae/<X>/frame/bin`, extract `<X>` into the new `device_id`
  key and clear the old one. Anything else falls back to default
  `"esp32"`.
* Bring up an HTTP server bound to the STA interface after WiFi
  connects, serving the **same** settings form the captive portal
  uses. The form now has a `device_id` field. Submitting it writes
  NVS and triggers a software restart.
* Keep `tesserae/esp32/...` working out of the box (default device_id
  = `"esp32"`).

## Repo layout (the bits that need changing)

```
include/app_config.h                ← MQTT_DEFAULT_* macros, NVS_KEY_*
src/mqtt_config.h / mqtt_config.c   ← struct field rename + topic builders
src/mqtt_handler.c                  ← read device_id, build the three topics
src/provisioning.c                  ← form HTML: device_id field; extract STA-mode server
src/wifi_manager.c / main.c         ← call the always-on portal after STA connect
```

`grep -rn 'inky/esp32\|tesserae/esp32\|MQTT_DEFAULT_TOPIC\|mqtt_topic' src/ include/`
locates every site to revisit.

## Implementation

### 1. NVS schema — replace `topic` with `device_id`

`mqtt_config_t` in `mqtt_config.h`:

```c
typedef struct {
    char uri[160];
    char device_id[33];   /* was: char topic[96]; */
    char user[64];
    char pass[64];
} mqtt_config_t;
```

`app_config.h`:

```c
#ifndef MQTT_DEFAULT_DEVICE_ID
#define MQTT_DEFAULT_DEVICE_ID  "esp32"
#endif
#define NVS_KEY_MQTT_DEVICE_ID  "device_id"
```

Delete `MQTT_DEFAULT_TOPIC` / `MQTT_DEFAULT_CONFIG_TOPIC` /
`MQTT_DEFAULT_STATUS_TOPIC` once nothing references them.

In `mqtt_config_load`:

* Try `nvs_get_str(NVS_KEY_MQTT_DEVICE_ID)` first.
* If missing, try the legacy `nvs_get_str(NVS_KEY_MQTT_TOPIC)` and
  extract the middle segment from a value matching
  `tesserae/<X>/frame/bin` or `inky/esp32/update` — see migration
  block below.
* If still missing, fall back to `MQTT_DEFAULT_DEVICE_ID`.

Migration (run once when the legacy key is found):

```c
const char *prefix = "tesserae/";
const char *suffix = "/frame/bin";
if (strncmp(legacy_topic, prefix, strlen(prefix)) == 0) {
    const char *id_start = legacy_topic + strlen(prefix);
    const char *id_end = strstr(id_start, suffix);
    if (id_end && id_end > id_start && (id_end - id_start) <= 32) {
        memcpy(out->device_id, id_start, id_end - id_start);
        out->device_id[id_end - id_start] = '\0';
    }
}
/* anything else (including the very old "inky/esp32/update"):
   leave device_id at default "esp32" — same effective topic. */

nvs_set_str(h, NVS_KEY_MQTT_DEVICE_ID, out->device_id);
nvs_erase_key(h, NVS_KEY_MQTT_TOPIC);   /* one-shot migration */
```

`mqtt_config_save` takes `device_id` instead of `topic` and validates
the same charset client-side (`isalnum || '_' || '-'`, must start with
a letter, length 2–32). Reject empty / malformed input by returning
`ESP_ERR_INVALID_ARG` so the form can surface "bad device id" instead
of silently writing junk.

### 2. Topic builders

Centralise topic construction in `mqtt_handler.c` (one place, no
sprintf-elsewhere). Heap-allocate three buffers from the
`mqtt_config_t.device_id` on session start:

```c
static char s_update_topic[96];
static char s_config_topic[96];
static char s_status_topic[96];

static void build_topics(const char *device_id)
{
    snprintf(s_update_topic, sizeof s_update_topic,
             "tesserae/%s/frame/bin", device_id);
    snprintf(s_config_topic, sizeof s_config_topic,
             "tesserae/%s/config", device_id);
    snprintf(s_status_topic, sizeof s_status_topic,
             "tesserae/%s/status", device_id);
}
```

Call `build_topics(cfg.device_id)` once in `mqtt_handler_start()` and
wire the three pointers into the existing `mqtt_ctx_t`. Drop the
`update_topic = cfg_nvs.topic` line — that field is gone.

### 3. Captive portal form — add device_id

`provisioning.c`, in `k_form_html`, replace the topic block with:

```html
<label>Device id</label>
<input name="device_id" maxlength="32" pattern="[a-z][a-z0-9_-]{1,31}"
       autocomplete="off" placeholder="esp32">
<small>Topics: <code>tesserae/&lt;id&gt;/frame/bin</code> etc. Default
<code>esp32</code> matches the built-in Tesserae kind.</small>
```

In the POST handler (`h_save`), read `device_id` instead of
`mqtt_topic` and pass it to `mqtt_config_save`. Bump the read budget
(`MAX_FORM_BYTES` or equivalent) — `device_id` is shorter than the old
topic, so total may shrink, but verify.

### 4. Always-on settings server

Today, `provisioning.c` only runs when STA connect fails. Refactor so
the same HTTP form is reachable from the LAN whenever STA is up:

* Extract the HTTP-handler registration (routes for `/`, `/save`) from
  the captive-portal entry point into a `settings_server_start(void)`
  function. The captive-portal code calls it after the SoftAP + DNS
  hijack are up; the new always-on path calls it from `main()`
  immediately after `wifi_manager_connect_sta()` succeeds.
* The HTTP server binds to all interfaces (it does already via
  `httpd_start` defaults), so the same routes answer on the STA IP.
* Add a tiny `<h2>Status</h2>` block at the top of the form that
  shows the current `device_id`, MQTT URI, and (if STA is up) the
  local IP, so the user knows where they landed.
* Make the form pre-populate input `value=""` attributes from the
  currently-loaded config — currently `k_form_html` is a static blob
  with placeholders only. Switch to building the HTML at request time
  via a small `httpd_resp_sendstr` sequence (or `asprintf`), so each
  GET reflects live NVS values.
* Advertise the page via mDNS: register
  `_http._tcp.local` with the hostname `tesserae-<device_id>.local`
  so the user can navigate without knowing the IP. Use ESP-IDF's
  `mdns_init` + `mdns_hostname_set` + `mdns_service_add`. Free /
  re-register if the device_id changes via the form.
* Don't deep-sleep while the settings server is up. Today the wake
  path is "subscribe → wait for retained frame → paint → sleep".
  Leave that wake cycle alone for the painting path, but if the
  device booted via a "long press" or "manual reset" indicator
  (RTC slow-memory flag), keep the settings server up for
  `PROVISION_PORTAL_TIMEOUT_S` instead of sleeping. The user
  triggers settings mode by hitting the reset button twice in quick
  succession (debounced via an RTC counter incremented on every cold
  boot and zeroed after `PROVISION_PORTAL_TIMEOUT_S / 2`). Spec out
  this trigger in the README — the alternative (always-on web server
  even while supposedly sleeping) burns way too much power.
* Authentication: skip it for v1. The user is on their own LAN.
  Document this caveat in the README.

### 5. README + secrets.example.h

* Update `secrets.example.h`: remove `MQTT_DEFAULT_TOPIC`,
  `MQTT_DEFAULT_CONFIG_TOPIC`, `MQTT_DEFAULT_STATUS_TOPIC`. Add
  `MQTT_DEFAULT_DEVICE_ID` so devs can compile-time set it.
* README: document the captive portal's new `device_id` field, the
  always-on settings server (URL: `http://tesserae-<id>.local/`),
  the double-tap-reset trigger, and the back-compat migration from
  the legacy `mqtt/topic` NVS key.

## Verification

1. **Legacy upgrade** — flash the new firmware onto a board that has
   the old NVS layout (`mqtt/topic = "tesserae/esp32/frame/bin"` or
   `"inky/esp32/update"`). On first boot, watch for the migration log
   line; subsequent topic subscriptions go to
   `tesserae/esp32/frame/bin`. The retained heartbeat appears on
   `tesserae/esp32/status` with no behaviour change.
2. **Captive portal** — fresh-flashed board (empty NVS) boots into
   SoftAP. Connect, set `device_id=esp32_hallway`, save. Reboots,
   joins WiFi, subscribes to `tesserae/esp32_hallway/frame/bin`,
   heartbeats on `tesserae/esp32_hallway/status`. Confirm with
   `mosquitto_sub -v -t 'tesserae/#'`.
3. **Always-on portal** — connected board on the LAN. Visit
   `http://tesserae-esp32_hallway.local/` (or its IP). Form loads with
   current values pre-filled. Change `device_id`, save. Board reboots
   and the next session uses the new prefix.
4. **Tesserae UI** — in Settings → Devices → Add device, register
   `esp32_hallway` with kind `esp32_client`. Bind a page to it and
   push. The board paints. Status badge goes green.
5. **Validation** — POST a bad device_id (`""`, `"Has-Caps"`,
   `"a"`, `"01-starts-with-digit"`, 33-char string). The server
   should refuse all of them and re-render the form with an error
   line.

## Out of scope

* No TLS for the portal — LAN-only, intended for trusted networks.
* No OTA. Firmware updates still happen over USB / esptool.
* No firmware-side opinion on which physical devices exist on the
  broker; only the Tesserae server knows that. The portal is a dumb
  text field by design.
