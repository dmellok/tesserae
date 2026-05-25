# Tesserae

E-ink dashboard companion. Compose tile-based dashboards in the browser, render
headless, push the resulting frame to one or more devices (Pi listener, ESP32
client) over MQTT.

Sibling rebuild of [inky-dash](https://github.com/dmellok/inky-dash). Same job,
but the publishing pipeline is a first-class extension point — new device, new
renderer, new format = drop a folder.

The name: a [*tessera*](https://en.wikipedia.org/wiki/Tessera) is the
individual tile of a mosaic; the editor composes a dashboard out of cells.

## Status

In active development. Tracked by milestone:

| # | Milestone | Status |
|---|---|---|
| 1 | Scaffold + plugin loader + composer + clock widget + smoke test | done |
| 2 | Renderer loader + `pi_png` + `MqttTransport` + push pipeline | done |
| 3 | `/settings` page generated from manifests + auth gate | next |
| 4 | `pi_bin` + `esp32_bin` renderers | — |
| 5 | Device layer (`pi_listener` + `esp32_client`) | — |
| 6 | Scheduler | — |
| 7 | Send page | — |
| 8 | Push history (event log) | — |
| 9 | Home Assistant MQTT discovery | — |
| 10 | Port remaining widgets | — |

## Architecture in one diagram

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
│  Devices        Devices (drop-a-folder)                             │
│                    ├─ pi_listener  (subscribes tesserae/pi/status)  │
│                    └─ esp32_client (subs status, pub/sub config)    │
└─────────────────────────────────────────────────────────────────────┘
```

Four layers, one direction of flow. Each layer only knows the boxes directly
adjacent. **One canonical internal orientation: composition** (panel's mounted
orientation). Renderers transform out of it via their declared `orientation`
field — no orientation muddle.

## MQTT topic scheme

Grammar: `tesserae/<device-type>/<channel>[/<format>]`

| Topic | Payload | Retain | Direction |
|---|---|---|---|
| `tesserae/pi/frame/png` | `{url, rotate, scale, bg, saturation}` | no | publish |
| `tesserae/pi/frame/bin` | `{url}` | no | publish |
| `tesserae/pi/status` | `{...heartbeat...}` | yes | subscribe |
| `tesserae/esp32/frame/bin` | `{url}` | yes | publish |
| `tesserae/esp32/status` | `{battery_mv, battery_pct, rssi, ip}` | yes | subscribe |
| `tesserae/esp32/config` | `{sleep_interval_s}` | yes | both |

## Running locally

The app factory, plugin loader, renderer loader, and push pipeline are in
place. There's no admin UI yet, but you can:

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main
```

Then open <http://127.0.0.1:8000/_test/render?plugin=clock&size=md> to see a
single clock cell rendered through the composer.

To run the test suite (loader contracts + composer markup + transport +
push pipeline + per-widget/renderer smoke):

```sh
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app/
```

The renderer + transport + push pipeline are exercised end-to-end against
mocked broker and patched Playwright — no live broker or Chromium is
required for tests.

## Tech stack

- Python 3.11+, Flask 3.x, Pydantic 2.x
- Pillow + numpy for image ops
- Playwright + Chromium for headless render
- paho-mqtt for the broker bridge
- Lit 3.x web components (no React), vanilla JS (no TypeScript)
- esbuild bundles one entry per admin page (`static/pages/*.js` → `static/dist/`)

## Repo layout

```
tesserae/
  app/             Flask app, transport, push pipeline, state, scheduler
  plugins/<id>/    widget plugins (drop-a-folder)
  renderers/<id>/  renderer plugins (drop-a-folder)
  devices/<id>/    device plugins (drop-a-folder)
  schema/          JSON Schemas for plugin/renderer/device manifests
  static/          Lit components, page entries, shared CSS, dist/
  templates/       Jinja shells
  tests/           top-level tests
  scripts/         install.sh, run.sh, install-service.sh
  docs/            architecture.md, contracts/{plugins,renderers,devices}.md
  data/            runtime state (gitignored)
```

## License

Not yet set. Will land before public release.
