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
| `tesserae/<id>/frame/trmnl` | `{url}`, only when `trmnl_png` is enabled for a TRMNL-style device that prefers MQTT over the HTTP pull | no | publish |
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
| `GET /api/setup` | Initial pairing, client sends its MAC in the `Id` header, server returns an access token (`api_key`) + `friendly_id`. |
| `GET /api/display` | Latest frame URL + next-poll interval for the calling device. Authenticated via the device's access token. |
| `POST /api/log` | Client log messages (battery state, RSSI, errors). Surfaces in the device card. |

A device that sends its MAC to `/api/setup` self-provisions: the server
creates a TRMNL instance keyed by that MAC, mints a high-entropy access
token, and the device appears in Settings → Devices with no admin click.
Callers that send no MAC (KOReader on a Kindle) keep the token they
brought, and land as a Discovered entry for one-click registration.

## Webhook push

A single bearer-token endpoint lets any external system trigger an
on-demand re-render and push:

| Route | Purpose |
|---|---|
| `POST /api/v1/push` | Re-render every device assigned to the named page and publish. Body: `{"page_id": "<id>"}`. Auth: `Authorization: Bearer <token>`. |

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
in `app/ha_discovery.py`; it's opt-in via Settings → Server →
Network & integrations.

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

The admin shell and canvas editor chrome use `Tesserae UI …` font families
so dashboard content can load the original families independently. Both
shells preload the active design's common fonts before their stylesheets.
UI faces use `font-display: optional`: if a font is late, visible text
keeps its system fallback for that page view; later views can use the
cached font. Bundled `fonts_core` WOFF assets are cached for one day,
with revalidation in dev mode. Icons reserve their glyph width, and Paper
pages start at their final position without a page-wide entry slide.

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
* **Services** (`kind: "service"`) are non-placeable data sources: a
  plugin folder with a server module exporting only `fetch(options,
  settings, *, ctx)` and no `render`. A service exposes a whole external
  API (Open-Meteo, Home Assistant, Spotify, a generic REST endpoint) for
  a canvas `code` / `data` element to pull from, but never shows up in
  the widget picker. Probed with empty options it returns a
  self-describing map of the scopes it offers, so the MCP agent can
  explore the API (`GET /api/mcp/services`) before choosing a scope to
  source. Because it has no placeable UI, its manifest may leave
  `supports.sizes` empty.

See [the widget contract](../widgets.md) for the per-plugin schema +
the full set of UI hooks (icon, theme, data plugins) widgets can use.
