# Architecture

Tesserae is four layers and one direction of flow. Each layer only knows
the boxes directly adjacent, so adding a new device, a new wire format,
or a new widget is a contained change.

## The pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  Composition    Plugins  Themes  Fonts                              │
│  (browser)         │       │       │                                │
│                    └───────┼───────┘                                │
│                            ▼                                        │
│                  Panel-sized PNG (composition orientation)          │
├─────────────────────────────────────────────────────────────────────┤
│  Render         Renderers (drop-a-folder)                           │
│                    ├─ pi_png     (orientation: landscape)           │
│                    ├─ pi_bin     (orientation: composition)         │
│                    ├─ esp32_bin  (orientation: composition)         │
│                    └─ trmnl_png  (orientation: composition, 1-bit)  │
│                            ▼                                        │
│                  (artifact bytes, payload, mime, topic, retain)     │
├─────────────────────────────────────────────────────────────────────┤
│  Transport      MqttTransport ──┐    │    HTTP-pull API ──┐         │
│  (one job: publish)             ▼    │    (one job: serve) ▼        │
│                                 Broker        /api/display          │
├─────────────────────────────────────────────────────────────────────┤
│  Devices        Device kinds (drop-a-folder) + user instances       │
│                    ├─ pi_bin_client   (tesserae/pi_bin/status)      │
│                    ├─ pi_png_client   (tesserae/pi_png/status)      │
│                    ├─ esp32_client    (status + pub/sub config)     │
│                    ├─ pico_bin_client (RP2350, retained, landscape) │
│                    └─ trmnl_client    (HTTP poll, no broker needed) │
└─────────────────────────────────────────────────────────────────────┘
```

A **kind** is a built-in device template; each physical panel you
register is an **instance** of a kind with its own id, topics, and
panel size. Multi-head fan-out is just "more instances".

One canonical internal orientation: **composition** (the panel's
mounted orientation). Renderers transform out of it via their declared
`orientation` field, no orientation muddle.

Two transports run side-by-side: **MQTT push** for Pi / ESP32 clients
(the broker fans frames out to any number of subscribers) and
**HTTP pull** for TRMNL devices + KOReader-on-Kindle (the device
polls `/api/display` on a schedule and pulls the latest 1-bit PNG).
A Tesserae install can mix both, pick whichever fits each panel.

## MQTT topic scheme

Grammar: `tesserae/<device-id>/<channel>[/<format>]`

`<device-id>` is the device's topic prefix. Built-in kinds default to
`pi_bin`, `pi_png`, and `esp32`; user-registered instances use their own
id (e.g. `pi_kitchen`, `esp32_hallway`).

| Topic | Payload | Retain | Direction |
|---|---|---|---|
| `tesserae/pi_png/frame/png` | `{url, rotate, scale, bg, saturation}` | no | publish |
| `tesserae/pi_bin/frame/bin` | `{url}` | no | publish |
| `tesserae/pi_bin/status`, `tesserae/pi_png/status` | `{state, kind, panel_w, panel_h, fw_version, …}` | yes | subscribe |
| `tesserae/esp32/frame/bin` | `{url}` | yes | publish |
| `tesserae/esp32/status` | `{battery_mv, battery_pct, rssi, ip, kind, panel_w, panel_h, fw_version}` | yes | subscribe |
| `tesserae/esp32/config` | `{sleep_interval_s}` | yes | both |
| `tesserae/<id>/frame/trmnl` | `{url}`, only when `trmnl_png` is enabled for a TRMNL-style device that prefers MQTT over the HTTP pull | yes | publish |
| `tesserae/+/status` | (wildcard), any unregistered id surfaces for one-click registration in Settings → Devices | | subscribe |

The `kind` / `panel_w` / `panel_h` / `fw_version` keys in a heartbeat
are the **discovery hint**: a client that includes them gets pre-filled
in the Discovered strip so registering it is one click.

## HTTP-pull API (TRMNL / KOReader)

TRMNL-style devices don't subscribe to MQTT, they poll the Tesserae
server on a schedule. A handful of HTTP endpoints (served by
`app/trmnl_api.py`) cover the protocol:

| Route | Purpose |
|---|---|
| `GET /api/setup` | Initial pairing, client sends MAC, server returns a 5-char access token + assigned device id. |
| `GET /api/display` | Latest frame URL + next-poll interval for the calling device. Authenticated via the device's access token. |
| `POST /api/log` | Client log messages (battery state, RSSI, errors). Surfaces in the device card. |

Pairing uses a short token printed at the top of Settings → Devices →
Add device → TRMNL. The device reads its MAC from EEPROM, calls
`/api/setup`, and the server pre-fills a Discovered entry on the
admin's Devices page for one-click registration.

## Webhook push

A single bearer-token endpoint lets any external system trigger an
on-demand re-render and push:

| Route | Purpose |
|---|---|
| `POST /api/v1/push` | Re-render every device assigned to the named page and publish. Body: `{"page": "<id>"}`. Auth: `Authorization: Bearer <token>`. |

The token is generated / rotated / cleared from Settings → System →
Webhook and stored under `data/core/settings.json`. Use it from
Home Assistant automations, cron, GitHub Actions, etc. See
`docs/install/server.md` → **Webhook push** for the request shape
and rate-limit behaviour.

## Home Assistant MQTT discovery

When the broker is shared with Home Assistant, Tesserae can publish
HA's MQTT discovery messages so every device + every assigned page
shows up automatically as HA entities (a hub device per Tesserae
install, plus a button + select + image + diagnostic entities per
display). See `docs/install/home-assistant.md`. The publisher lives
in `app/ha_discovery.py`; it's opt-in via Settings → Integrations →
Home Assistant.

## Tech stack

- Python 3.11+, Flask 3.x, Pydantic 2.x
- Pillow + numpy for image ops
- Playwright + Chromium for headless render
- paho-mqtt for the broker bridge
- Vanilla JavaScript on the admin (no React, no Lit, no TypeScript)
- esbuild bundles one entry per admin page
  (`static/pages/*.js` → `static/dist/`)
- waitress as the production WSGI server

## Repo layout

```
tesserae/
  app/             Flask app, transport, push pipeline, state, scheduler
  plugins/<id>/    widget / font / data / admin plugins
                   (drop-a-folder), 58 widgets ship bundled.
                   Themes live in static/style/spectra-*.css + a
                   Python registry, not the plugin tree.
  renderers/<id>/  renderer plugins (drop-a-folder)
  devices/<id>/    device plugins (drop-a-folder)
  schema/          JSON Schemas for plugin/renderer/device manifests
  static/          Lit components, page entries, shared CSS, dist/, icons/
                   (vendored Phosphor 2.1.1, woff2 only, 1.5 MB)
  templates/       Jinja shells
  tests/           top-level tests
  data/            runtime state (gitignored)
```

## The "drop a folder" contract

A widget, renderer, or device plugin is a folder under
`plugins/<id>/`, `renderers/<id>/`, or `devices/<id>/` with a JSON
manifest the loader validates against the matching JSON Schema. The
manifest declares the plugin's id, name, settings, and any optional
hooks; the loader catches mistakes at boot rather than mid-push.

* **Widgets** export a default `render(shadow, ctx)` function (vanilla
  JS, mounted into a per-cell Shadow DOM) and (optionally) a server
  module. See [Build a widget with AI](writing-a-widget.md) for the
  full authoring walkthrough.
* **Renderers** export `transform(png_bytes, *, panel, settings) ->
  bytes` and `payload(digest, base_url, *, settings) -> dict`. The
  push pipeline composes the PNG once, then hands it to every loaded
  renderer.
* **Devices** describe a wire-level client: which renderers they
  consume, what their status / config topics look like, and a JSON
  Schema for the per-device config form the admin UI generates.

See [the widget contract](../widgets.md) for the per-plugin schema +
the full set of UI hooks (icon, theme, data plugins) widgets can use.
