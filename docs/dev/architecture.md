# Architecture

Tesserae is four layers and one direction of flow. Each layer only knows
the boxes directly adjacent — so adding a new device, a new wire format,
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
│                    └─ esp32_bin  (orientation: composition)         │
│                            ▼                                        │
│                  (artifact bytes, payload, mime, topic, retain)     │
├─────────────────────────────────────────────────────────────────────┤
│  Transport      MqttTransport (one job: publish)                    │
│                            ▼                                        │
│                  Broker                                             │
├─────────────────────────────────────────────────────────────────────┤
│  Devices        Device kinds (drop-a-folder) + user instances       │
│                    ├─ pi_bin_client  (tesserae/pi_bin/status)       │
│                    ├─ pi_png_client  (tesserae/pi_png/status)       │
│                    └─ esp32_client   (status + pub/sub config)      │
└─────────────────────────────────────────────────────────────────────┘
```

A **kind** is a built-in device template; each physical panel you
register is an **instance** of a kind with its own id, topics, and
panel size. Multi-head fan-out is just "more instances".

One canonical internal orientation: **composition** (the panel's
mounted orientation). Renderers transform out of it via their declared
`orientation` field — no orientation muddle.

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
| `tesserae/+/status` | (wildcard) — any unregistered id surfaces for one-click registration in Settings → Devices | — | subscribe |

The `kind` / `panel_w` / `panel_h` / `fw_version` keys in a heartbeat
are the **discovery hint**: a client that includes them gets pre-filled
in the Discovered strip so registering it is one click.

## Tech stack

- Python 3.11+, Flask 3.x, Pydantic 2.x
- Pillow + numpy for image ops
- Playwright + Chromium for headless render
- paho-mqtt for the broker bridge
- Lit 3.x web components (no React), vanilla JS (no TypeScript)
- esbuild bundles one entry per admin page
  (`static/pages/*.js` → `static/dist/`)
- waitress as the production WSGI server

## Repo layout

```
tesserae/
  app/             Flask app, transport, push pipeline, state, scheduler
  plugins/<id>/    widget / theme / font / data / admin plugins
                   (drop-a-folder) — 47 widgets ship bundled
  renderers/<id>/  renderer plugins (drop-a-folder)
  devices/<id>/    device plugins (drop-a-folder)
  schema/          JSON Schemas for plugin/renderer/device manifests
  static/          Lit components, page entries, shared CSS, dist/, icons/
                   (vendored Phosphor 2.1.1 — woff2 only, 1.5 MB)
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

* **Widgets** export a Lit web component and (optionally) a server
  module. See [Build a widget with AI](writing-a-plugin.md) for the
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
