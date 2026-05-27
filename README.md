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
| 10 | ~~Port bedrock widgets (year_progress, sun_moon, weather, todo, calendar)~~ — dropped, rebuilding fresh | superseded |
| 11 | Settings split into Server / Renderers / Devices / Plugins sub-pages | done |
| 12 | Polish: timezone, test-broker, test-push, live SSE event stream | done |
| 13 | Theme builder + curated v4 theme subset | done |
| 14 | Form-driven page editor (CRUD pages + cells + live preview iframe) | done |
| 15 | Editor redesign — pre-selectable layouts, auto-save, click-to-edit preview | done |
| 16 | Reusable form components, sliders/switches, hidden IDs, panel-from-settings | done |
| 17 | `/` redirects to `/send`; reorder nav; Plugins dropdown for admin pages | done |
| 18 | Modern UI overhaul + locally vendored Phosphor icon set | done |

Server, renderers, devices, scheduler, HA discovery, settings, theme builder,
page editor, and the modern UI are all in. Tests + ruff + mypy --strict (on
contract modules) clean. CI runs ruff / pytest / mypy on every push (see
[.github/workflows/ci.yml](.github/workflows/ci.yml)).

The widget catalogue now spans weather, F1, calendar, news, finance, GitHub,
clocks, sky, pictures, todo, and Melbourne public transport — ~40 widgets in
the `plugins/` directory across `widget` / `theme` / `font` / `data` /
`admin` plugin kinds. See [Widget stability tiers](#widget-stability-tiers)
below for an upfront read on which ones depend on undocumented upstreams.

> **For tinkerers, not your mum.** Tesserae is a `git clone → tinker`
> dashboard. The admin chrome is polished but the deployment story still
> assumes you can read tracebacks and edit settings.json. Aimed at
> hobbyists who run a Pi appliance at home.

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

## Clients

The server publishes; something downstream paints the panel. Three reference
clients live in their own repos — pick whichever matches your hardware.

**[tesserae-pi-png-client](https://github.com/dmellok/tesserae-pi-png-client)**
pairs with the `pi_png` renderer. A Pi-side Python daemon that subscribes to
`tesserae/pi/frame/png` and hands incoming PNGs to
[`inky`](https://github.com/pimoroni/inky)'s high-level `set_image()`, so it
works on every panel the inky lib supports — pHAT, wHAT, Impression 4"/5.7"/
7.3"/13.3", 2/3/6/7 colour. Quantising on the Pi every frame makes it the
slower of the two Pi paths, but it stays wire-compatible with the inky-dash
v3/v4 listener protocol.

**[tesserae-pi-bin-client](https://github.com/dmellok/tesserae-pi-bin-client)**
pairs with the `pi_bin` renderer. Same Pi-side daemon shape, but it subscribes
to `tesserae/pi/frame/bin` and writes the server's already-packed 4-bpp buffer
straight into inky's internal `_buf` — no PIL on the Pi paint path. The result
is the fastest path on a Pimoroni Inky Impression (Spectra 6 / Waveshare E6,
any of the four sizes — auto-detected via the HAT EEPROM). The trade is a
private-API dependency: the `inky` version is pinned exactly.

**[tesserae-esp32-bin-client](https://github.com/dmellok/tesserae-esp32-bin-client)**
pairs with the `esp32_bin` renderer. Battery-powered ESP32-S3-WROOM-2 firmware
for the Waveshare 13.3" Spectra 6 panel: deep-sleeps between wakes, subscribes
to the retained `tesserae/esp32/frame/bin` topic, and skips the download when
the URL hash hasn't changed. Months of battery life from a single Li-Po;
refresh cadence is set by `sleep_interval_s` on `tesserae/esp32/config`.

## Install

One-liner for **macOS, Linux, and Raspberry Pi**:

```sh
curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/install.sh | bash
```

**Windows** (PowerShell):

```powershell
iwr https://raw.githubusercontent.com/dmellok/tesserae/main/install.ps1 -UseBasicParsing | iex
```

The installer:

- Sanity-checks `git` + Python 3.11+
- Clones the repo (default: `~/tesserae`, override with `TESSERAE_DIR`)
- Creates a venv and installs the project
- Asks for a port (default 8000)
- Installs Chromium via Playwright for the webpage-rendering features;
  if Playwright doesn't ship a binary for your platform (e.g. 32-bit
  Pi OS), it looks for system `chromium-browser` / Chrome / Edge and
  points Playwright at that. If neither exists it warns and continues
  — everything except webpage rendering still works.
- Writes a `run.sh` (or `run.ps1`) shortcut in the install dir.

After it finishes, start the server with `./run.sh` from the install
dir, then visit `http://localhost:8000/` (or whatever port you chose).
First run sends you to `/setup` to pick an admin password.

Manual install (or if you already cloned the repo):

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main         # production: waitress, port 8000
.venv/bin/python -m app.main --dev   # Flask dev server with auto-reload + debugger
```

`python -m app.main` runs under [waitress](https://docs.pylonsproject.org/projects/waitress/),
a pure-Python production WSGI server — same command on a Raspberry Pi
appliance, no need for nginx in front for a single-user install. `--dev`
opts into Flask's dev server when you're hacking on the admin and want
auto-reload.

### Chromium for webpage rendering

The Send → Webpage tab and the `webpage` widget shoot screenshots with
headless Chromium via Playwright. Playwright ships its own binaries for
most platforms; on 32-bit Raspberry Pi OS it doesn't, so the installer
falls back to the system browser. To override yourself, point at any
Chromium-compatible binary:

```sh
export TESSERAE_CHROMIUM_PATH=/usr/bin/chromium-browser
```

Or write the path to `data/core/.chromium` (single line) — the renderer
reads either at launch.

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

## Widget stability tiers

Every widget that hits the network depends on an upstream — and not every
upstream is equally reliable. Tesserae's tiers say what to expect when
your widget suddenly shows an error after a year of working fine.

### Stable

Backed by an official, documented API. Won't break unless the upstream
ships a deliberate breaking change (which usually comes with notice).

| Widget | Upstream |
|---|---|
| `weather_now`, `weather_hourly`, `weather_forecast`, `weather_air_quality`, `weather_pollen_count` (Open-Meteo path) | open-meteo.com |
| `f1_next`, `f1_last_race`, `f1_weekend`, `f1_standings_drivers` | jolpi.ca/ergast (maintained Ergast successor) |
| `news_rss` | any RSS 2.0 / Atom 1.0 feed |
| `news_hacker_news` | hacker-news.firebaseio.com (Firebase-hosted, documented) |
| `news_wikipedia_otd` | Wikimedia REST API |
| `finance_crypto` | CoinGecko v3 (documented) |
| `finance_currency` | frankfurter.app (ECB-sourced, documented) |
| `github_repo`, `github_releases`, `github_activity`, `github_pr_queue`, `github_actions`, `github_contributions` | api.github.com (REST v3 + GraphQL v4) |
| `calendar_month`, `calendar_week`, `calendar_day` | iCal feeds from Google / iCloud / Outlook |
| `clock_sunrise_sunset` | open-meteo.com |
| `sky_aurora` | services.swpc.noaa.gov (NOAA Space Weather, documented) |
| `sky_air_traffic` | opensky-network.org (rate-limited but documented) |
| `public_transport_times` | timetableapi.ptv.vic.gov.au (official PTV API v3) |
| `picture_apod` | api.nasa.gov |
| `picture_unsplash` | api.unsplash.com |

### Best-effort

Undocumented endpoint, but used by enough open-source projects for long
enough that breakage would be a notable event. Failure mode is usually
"widget returns an error payload until somebody updates the parser."

| Widget | Why best-effort |
|---|---|
| `finance_stock` | Yahoo Finance's `/v8/finance/chart/` endpoint isn't officially documented. Has been stable for 7+ years and is what most "free stock API" libraries reach for. |
| `picture_apple_album` | Reverse-engineered iCloud Shared Album endpoints. The shape has held up for ~10 years; Apple has shifted partitioning twice without breaking the protocol. |

### Fragile

Scraping or relying on a soft policy. Most likely to break with no
notice. The widget will surface an error payload rather than silently
serve stale data.

| Widget | Why fragile |
|---|---|
| `weather_pollen_count` (Melbourne fallback) | Scrapes melbournepollen.com.au's home page when Open-Meteo's pollen data is null (it's Europe-only). Any DOM redesign on their end breaks this path. The Open-Meteo path is stable. |
| `news_reddit` | Uses Reddit's public `/r/<sub>.json` endpoint. Reddit has tightened API access over time; this still works for low-volume reads but could be locked down. |

### Tier policy

* **Stable widgets** can be relied on for long-running dashboards. PR
  welcome if you spot an upstream that's tightened terms.
* **Best-effort widgets** should not be the only display in a
  twelve-month deployment. If they break, the widget's error payload
  tells you exactly which endpoint failed.
* **Fragile widgets** should be considered convenience. If your dashboard
  cares about pollen counts every day, treat the widget as a starting
  point and contribute a more durable backend.

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
  plugins/<id>/    widget + theme plugins (drop-a-folder). Currently only
                   themes_core ships bundled; widgets are being rebuilt.
  renderers/<id>/  renderer plugins (drop-a-folder)
  devices/<id>/    device plugins (drop-a-folder)
  schema/          JSON Schemas for plugin/renderer/device manifests
  static/          Lit components, page entries, shared CSS, dist/, icons/
                   (vendored Phosphor 2.1.1 — regular/fill/bold/duotone/light/thin, woff2 only, 1.5 MB)
  templates/       Jinja shells
  tests/           top-level tests
  data/            runtime state (gitignored)
```

## License

MIT — see [LICENSE](LICENSE).
