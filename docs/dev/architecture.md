# Architecture

Tesserae has four layers. Data flows in one direction, and each layer only
talks to its neighbours. Adding widgets, formats, or devices only affects a
small part of the code.

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
│                    ├─ pi_png       (orientation: landscape)         │
│                    ├─ pi_bin       (orientation: composition)       │
│                    ├─ esp32_bin    (orientation: composition)       │
│                    ├─ esp32_bw_bin (orientation: composition, 1-bit)│
│                    ├─ pico_bin     (orientation: composition)       │
│                    ├─ trmnl_png    (orientation: composition, 1-bit)│
│                    ├─ trmnl_png_gray16 (composition, 16-grey PNG)   │
│                    └─ kaleido_png  (composition, Kaleido 3 RGB PNG) │
│                            ▼                                        │
│                  (artifact bytes, payload, mime, topic, retain)     │
├─────────────────────────────────────────────────────────────────────┤
│  Transport      MqttTransport ──┐    │    HTTP-pull API ──┐         │
│  (one job: publish)             ▼    │    (one job: serve) ▼        │
│                                 Broker        /api/display          │
├─────────────────────────────────────────────────────────────────────┤
│  Devices        Device kinds (drop-a-folder) + user instances       │
│                    ├─ pi_bin_client    (tesserae/pi_bin/status)     │
│                    ├─ pi_png_client    (tesserae/pi_png/status)     │
│                    ├─ esp32_client     (status + pub/sub config)    │
│                    ├─ esp32_bw_client  (1-bpp pre-packed, MQTT)     │
│                    ├─ pico_bin_client  (RP2350, retained, landscape)│
│                    └─ trmnl_client     (HTTP poll, no broker needed)│
└─────────────────────────────────────────────────────────────────────┘
```

The diagram shows a subset of each layer; `renderers/` and `devices/` hold
the full sets.

A kind is a built-in device template. Each registered panel is an instance
with its own id, topics, and size. Add an instance for each panel.

Composition is the panel's mounted orientation. Renderers convert it using their
`orientation` field.

Frames travel in two ways. An install can use both:

- MQTT push: a broker sends frames to any number of Pi / ESP32 subscribers.
- HTTP pull: TRMNL devices and KOReader on Kindle poll `/api/display`
  on a schedule for the latest 1-bit PNG.

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
| `tesserae/<id>/frame/trmnl` | `{url}`, only when `trmnl_png` is enabled for a TRMNL-style device that prefers MQTT over the HTTP pull | no | publish |
| `tesserae/+/status` | (wildcard), any unregistered id surfaces for one-click registration in Settings → Devices | | subscribe |

Heartbeats with `kind` / `panel_w` / `panel_h` / `fw_version` pre-fill
the Discovered strip for one-click registration.

## HTTP-pull API (TRMNL / KOReader)

TRMNL-style devices poll these endpoints on a schedule, without MQTT.
The handlers live in `app/trmnl_api.py`:

| Route | Purpose |
|---|---|
| `GET /api/setup` | Initial pairing, client sends its MAC in the `Id` header, server returns an access token (`api_key`) + `friendly_id`. |
| `GET /api/display` | Latest frame URL + next-poll interval for the calling device. Authenticated via the device's access token. |
| `POST /api/log` | Client log messages (battery state, RSSI, errors). Surfaces in the device card. |

Send a MAC address to `/api/setup` to register automatically.
The server creates a TRMNL instance keyed by that MAC and a high-entropy
access token. The device appears in Settings → Devices without an admin click.

Clients without a MAC, such as KOReader on Kindle, keep their existing token.
They appear in Discovered for one-click registration.

## Webhook push

External systems can request a fresh render and push using a bearer token:

| Route | Purpose |
|---|---|
| `POST /api/v1/push` | Re-render every device assigned to the named page and publish. Body: `{"page_id": "<id>"}`. Auth: `Authorization: Bearer <token>`. |

Generate, rotate, or clear the token in Settings → System → Webhook.
It is stored in `data/core/settings.json`. Use it from Home Assistant
automations, cron, or GitHub Actions. See
`docs/install/server.md` → **Webhook push** for the request shape
and rate-limit behaviour.

## Home Assistant MQTT discovery

Share a broker with Home Assistant to discover every device and assigned
page as HA entities. Each Tesserae install gets a hub device.
Each display gets button, select, image, and diagnostic entities.

See `docs/install/home-assistant.md`. The publisher is `app/ha_discovery.py`.
Enable it in Settings → Server → Network & integrations; it is opt-in.

## Tech stack

- Python 3.11+, Flask 3.x, Pydantic 2.x
- Pillow + numpy for image ops
- Playwright + Chromium for headless render
- paho-mqtt for the broker bridge
- Vanilla JavaScript on the admin (no React, no Lit, no TypeScript)
- No front-end build step: `static/` ships as-is (`package.json` only
  carries lint / format tooling)
- waitress as the production WSGI server

## UI font loading

The admin UI and canvas editor use `Tesserae UI …` font families.
Dashboard content can load the original families separately.
Both UIs preload the active design's common fonts before their stylesheets.

UI fonts use `font-display: optional`. Late fonts leave visible text in the
system fallback for that view. Later views can use the cached font.

Bundled `fonts_core` WOFF assets are cached for one day and revalidated
in dev mode. Icons reserve their glyph width.
Paper pages start in their final position without a page-wide slide.

## Repo layout

```
tesserae/
  app/             Flask app, transport, push pipeline, state, scheduler
  plugins/<id>/    widget / font / data / admin plugins
                   (drop-a-folder), 37 widgets ship bundled.
                   Themes live in static/style/spectra-*.css + a
                   Python registry, not the plugin tree.
  renderers/<id>/  renderer plugins (drop-a-folder)
  devices/<id>/    device plugins (drop-a-folder)
  schema/          JSON Schemas for plugin/renderer/device manifests
  static/          admin JS modules, page entries, shared CSS, vendor/, icons/
                   (vendored Phosphor icon font)
  templates/       Jinja shells
  tests/           top-level tests
  data/            runtime state (gitignored)
```

## The "drop a folder" contract

A widget, renderer, or device plugin is a folder under
`plugins/<id>/`, `renderers/<id>/`, or `devices/<id>/` with a JSON
manifest checked against the matching JSON Schema at startup.
It declares the plugin's id, name, settings, and optional hooks.

* Widgets export a default `render(shadow, ctx)` function (vanilla
  JS, mounted into a per-cell Shadow DOM) and (optionally) a server
  module. See [Build a widget with AI](writing-a-widget.md) for the
  full authoring walkthrough.
* Renderers export `transform(png_bytes, *, panel, settings) ->
  bytes` and `payload(digest, base_url, *, settings) -> dict`. The
  push pipeline composes the PNG once, then hands it to every loaded
  renderer.
* Devices describe a wire-level client: which renderers they
  consume, what their status / config topics look like, and a JSON
  Schema for the per-device config form the admin UI generates.
* Services (`kind: "service"`) are data sources you cannot place as widgets: a
  plugin folder with a server module exporting only `fetch(options,
  settings, *, ctx)` and no `render`. Canvas `code` / `data` elements can
  read its external API. Examples include Open-Meteo, Home Assistant,
  Spotify, and generic REST endpoints.

  Empty options return a map of available scopes. An MCP agent can explore
  them with `GET /api/mcp/services` before choosing one.
  Services never appear in the widget picker and may leave `supports.sizes` empty.

See [the widget contract](../widgets.md) for the per-plugin schema +
the full set of UI hooks (icon, theme, data plugins) widgets can use.
