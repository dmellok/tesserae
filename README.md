# Tesserae

E-ink dashboard companion. Compose tile-based dashboards in the browser, render
headless, push the resulting frame to one or more devices (Pi client, ESP32
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
| 3 | `/settings` page generated from manifests + auth gate | done |
| 4 | `pi_bin` + `esp32_bin` renderers | done |
| 5 | Device layer (`pi_client` + `esp32_client`) | done |
| 6 | Scheduler | done |
| 7 | Send page | done |
| 8 | Generalise event log (renderer / device / scheduler events) | done |
| 9 | Home Assistant MQTT discovery | done |
| 10 | Port bedrock widgets (year_progress, sun_moon, weather, todo, calendar) | done |
| 11 | Settings split into Server / Renderers / Devices / Plugins sub-pages | done |
| 12 | Polish: timezone, test-broker, test-push, live SSE event stream | done |
| 13 | Theme builder + curated v4 theme subset | done |
| 14 | Form-driven page editor (CRUD pages + cells + live preview iframe) | done |
| 15 | Editor redesign — pre-selectable layouts, auto-save, click-to-edit preview | done |
| 16 | Reusable form components, sliders/switches, hidden IDs, panel-from-settings | done |
| 17 | `/` redirects to `/send`; reorder nav; Plugins dropdown for admin pages | done |
| 18 | Modern UI overhaul + locally vendored Phosphor icon set | done |

All milestones from the build prompt + polish bundle are complete. 251 tests
passing; ruff + mypy --strict (on contract modules) clean. Remaining v4 widgets
(aligner, apod, aqi_trend, countdown, day_view, gallery, genart,
github_heatmap, hn, home_assistant, news, note, pollen_vic, ptv, qr, radar,
reddit, starmap, trakt_watchlist, unsplash, webpage, wikipotd, world_clock,
xkcd) are future work.

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
│                    ├─ pi_client    (subscribes tesserae/pi/status)  │
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

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main
```

Open <http://127.0.0.1:8000/> — on first boot you'll be sent to `/setup`
to pick an admin password. After that, sign in at `/login` and configure
the broker + base URL at `/settings`. Renderers and plugins that declare
settings show up as their own sections, generated from manifests.

To preview a single widget without composing a dashboard, hit
<http://127.0.0.1:8000/_test/render?plugin=clock&size=md> from loopback
(the auth gate keeps `/compose` and `/_test/render` reachable from
`127.0.0.1` only).

Run the test suite:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app/
```

The renderer + transport + push pipeline + auth + settings flow are all
covered with no broker or Chromium dependency.

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
  static/          Lit components, page entries, shared CSS, dist/, icons/
                   (vendored Phosphor 2.1.1 — regular/fill/bold/duotone/light/thin, woff2 only, 1.5 MB)
  templates/       Jinja shells
  tests/           top-level tests
  scripts/         install.sh, run.sh, install-service.sh
  docs/            architecture.md, contracts/{plugins,renderers,devices}.md
  data/            runtime state (gitignored)
```

## License

Not yet set. Will land before public release.
