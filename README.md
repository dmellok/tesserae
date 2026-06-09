# Tesserae

[![License](https://img.shields.io/github/license/dmellok/tesserae)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/dmellok/tesserae)](https://github.com/dmellok/tesserae/releases/latest)
[![CI](https://github.com/dmellok/tesserae/actions/workflows/ci.yml/badge.svg)](https://github.com/dmellok/tesserae/actions/workflows/ci.yml)
[![GitHub Discussions](https://img.shields.io/github/discussions/dmellok/tesserae)](https://github.com/dmellok/tesserae/discussions)

<p align="center">
  <a href="docs/screenshots/hero-rack.jpg">
    <img src="docs/screenshots/hero-rack.jpg" alt="A wooden tabletop with six different e-ink panels, each painting a different Tesserae dashboard: a framed weather/clock/Spotify panel, a Waveshare Spectra 6 bare board showing F1 schedule, the centre 13.3 inch Spectra 6 panel running a GitHub stats dashboard, a Kindle Paperwhite running an OctoPrint dashboard, a Waveshare 7.3 inch PhotoPainter running a weather/pollen view, and a framed Inky 13.3 inch showing the month calendar plus Hacker News" width="900">
  </a>
  <br>
  <em>Six panels, one Tesserae server. Framed Inkys, bare Waveshare boards driven by ESP32, and a jailbroken Kindle, all painting different dashboards from the same composer.</em>
</p>

For more, see the
[admin UI gallery](https://dmellok.github.io/tesserae/gallery/)
and the
[bundled widget gallery](https://dmellok.github.io/tesserae/widgets/gallery/).

E-ink dashboard companion. Compose tile-based dashboards in the browser,
render headless, push the resulting frame to one or more devices, Pi
and ESP32 over MQTT, TRMNL / KOReader over HTTP-pull.

Every layer, widgets, themes, fonts, renderers, and device kinds, is
a drop-a-folder plugin. Adding new hardware (or a new widget) is a
contained change.

**📖 [Full documentation](https://dmellok.github.io/tesserae/)** -
install guides, the [bundled gallery](https://dmellok.github.io/tesserae/widgets/gallery/)
(30 widgets) and [community catalog](https://dmellok.github.io/tesserae/widgets/community/)
(15 + bundles), the [architecture deep dive](https://dmellok.github.io/tesserae/dev/architecture/),
and [how to build a widget](https://dmellok.github.io/tesserae/dev/writing-a-widget/) (with AI).

> **Self-hosted hobby project.** Tesserae installs with `docker compose up`
> or a `git clone`. The admin UI is polished, but the deployment story
> still assumes you can read tracebacks if something goes wrong. Aimed at
> people running a Pi appliance at home.

## Status

A working hobbyist build. Composer → renderers → transport → devices
pipeline, scheduler (with opt-in smart sync for JIT rendering against
device sleep telemetry), Home Assistant MQTT auto-discovery, webhook
push, data export / import, theme builder (with image-to-palette
extraction), form-driven page editor, the community widget catalog,
and the modern admin UI are all in. Multi-head is built in throughout,
register multiple panels, bind a dashboard to a specific display,
auto-discover clients that announce themselves on the broker.

30 widgets bundled across the universally-useful set, weather, clocks,
calendar, news (HN / RSS / Wikipedia OTD), HA, picture gallery, todo,
webpage, plus the `weather_now_scenic` reference for the
`design.palette: extended` opt-in. Another 15 entries cover niche /
credential-gated families (F1, Spotify, GitHub, Finance, Sky, Glances,
OctoPrint, Unsplash, iCloud Shared Albums, and more) via the
[community catalog](https://dmellok.github.io/tesserae/widgets/community/),
one-click install from Settings → Widgets → Browse. See
[widget stability tiers](https://dmellok.github.io/tesserae/widgets/tiers/)
for an upfront read on which depend on undocumented upstreams.

Tests + ruff + mypy `--strict` (on contract modules) clean. CI runs on
every push.

For support, head to [Discussions](https://github.com/dmellok/tesserae/discussions).

<details>
<summary><b>Full feature list</b>, what's actually in the box (click to expand)</summary>

### Composition

- **Browser-based page editor** with form-driven cell options, live preview, and per-cell content-zoom slider.
- **10 layout presets** (1-cell, 2/3 column, 2/3 row, 2×2 grid, hero top/bottom/left/right, hero sandwich), fraction-based so the same layout works at any panel size; "Custom layout" snaps to a grid you set.
- **Per-cell overrides**: theme, style (typography), font, content zoom. Cells inherit the page's pick by default; the override flag lets a single tile break from the rest.
- **Reactive layout for mobile editing**, preview stacks above the cell forms on small viewports, with a floating back-to-top button (drag along the bottom to flip side for left-handed grips).
- **30 widgets bundled** across the universal set (weather, clocks, calendar, news, HA, picture gallery, todo, webpage). The slim-down landed in 0.38–0.42; niche / credential-gated families moved to the [community catalog](https://github.com/dmellok/tesserae-widgets) as installable bundles (F1, Spotify, GitHub, Finance, Sky, Glances, OctoPrint, Unsplash, iCloud Shared Albums, more).
- **Drop-a-folder widget plugins**, `plugin.json` + `server.py` + `client.{js,css}`, manifest schema validated at load. The orthogonal `data-theme` × `data-style` Spectra axes let one widget compose with every theme + typography pairing instead of shipping N variants per widget.
- **`design.palette: extended` opt-in** for decorative widgets that want gradients / layered shapes / soft shadows (the renderer's Floyd-Steinberg dither approximates them on the panel palette); strict tokens stay the default. [Reference impl: weather_now_scenic](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now_scenic).
- **Capability declarations** (`requires: [network:..., settings:..., filesystem:write:...]` in `plugin.json`) gated at the socket layer for network egress so a widget can't quietly call home outside its declared upstreams; reviewer-visible for the rest. Bundled + catalog widgets ship declarations.

### Rendering

- **Headless Playwright** server-side renderer with a persistent browser pool (toggle to fall back to one-shot).
- **Drop-a-folder renderer plugins**, currently 4: `pi_png` (universal Pimoroni `inky` path over MQTT), `pi_bin` (pre-packed 4-bpp buffer for Inky Impression), `esp32_bin` (Waveshare 13.3" Spectra 6 + 7.3" PhotoPainter over MQTT), `trmnl_png` (1-bit greyscale PNG over HTTP).
- **Eight dither modes** for the `.bin` packers: Floyd-Steinberg + none (Pillow paths), plus Atkinson / Jarvis-Judice-Ninke / Stucki / Bayer-8x8 / halftone / crosshatch (NumPy paths).
- **Opt-in calibrated palette + tone mapping** (per device), dithers against the panel's measured colours instead of nominal sRGB primaries. Palette data ported from [paperlesspaper/epdoptimize](https://github.com/paperlesspaper/epdoptimize); paired with a linear sRGB tone-map pre-pass.
- **Firmware-native panel orientation** auto-detected from the panel preset, with a startup migration that backfills pre-v0.20 ESP32 instance manifests so legacy installs don't paint at the wrong row stride.
- **Per-device gamut switching** (ACeP vs Spectra 6) for Impressions sold in both revisions.
- **Partial-update preview** in the composer, skips full iframe reloads.
- **Stable per-device preview alias** for Home Assistant generic-camera entities.

### Devices & multi-head

- **Drop-a-folder device plugins**, 4 bundled (`pi_png_client`, `pi_bin_client`, `esp32_client`, `trmnl_client`).
- **Multi-head**: register multiple devices, bind a dashboard to a specific one, each can run its own theme.
- **MQTT push** (Pi / ESP32) and **HTTP pull** (Kindle / TRMNL BYOS) supported side-by-side.
- **mDNS auto-discovery** of LAN clients; discovered devices show up in the *Discovered* strip with panel dims pre-filled.
- **TRMNL pairing** mints a short 5-char access token (typeable on devices with no real keyboard).
- Per-device sleep cadence, rotation (CCW quarters), and panel dimensions.

### Scheduling & push

- **Background scheduler** (30s tick), two kinds of schedule:
  - **interval**, fires every N minutes inside a day-of-week mask + time-of-day window.
  - **daily**, once per day at a wall-clock time inside a day-of-week mask.
- **Schedule priority** ordering, **quiet hours** suppression, **stale-schedule detection** (deleted-page schedules don't fire silently).
- **Smart sync (JIT rendering)**, opt-in per interval schedule. The scheduler tracks each bound device's sleep telemetry (configured `sleep_interval_s` fallback or firmware-published `sleep_until` / `next_sleep_s` on the heartbeat) and fires within a configurable lead window of the next predicted wake. The rendered frame is waiting for the panel when it wakes instead of being rendered after the paint. Falls back to fixed-cadence firing when no bound device is trusted yet. Schedule list shows a green / yellow / red indicator dot per row; the device admin card carries a plain-English diagnostic for the confidence state. Added in v0.42.0; see [#10](https://github.com/dmellok/tesserae/issues/10).
- **Send page** for one-shot manual pushes.
- **Retained MQTT frames**, panels that boot mid-cycle get the latest frame on subscribe.

### Home Assistant integration

- **HA MQTT discovery**, every device shows up automatically as an HA device tile.
- Per-device **display name** that re-publishes discovery on save.
- **Generic-camera** preview via the per-device alias.
- **HA sensors** for battery, signal, IP, last-render time per device.
- **HA widget set**, `ha_battery`, `ha_climate`, `ha_entities`, `ha_lights`, `ha_media`, `ha_sensor` bundled; `ha_camera` / `ha_energy` / `ha_history` / `ha_locks` / `ha_todo` / `ha_zones` opt-in via the catalog.
- **Stale-discovery sweep on start** so deleted Tesserae devices stop ghosting HA tiles.

### Themes & typography (Spectra design system)

- **19 bundled themes** across four families: Light (light / sepia / cool-gray / high-contrast), Dark (dark / nord), Movement (bauhaus / destijl / brutalist palettes), and base16 (10 popular code-editor palettes adapted for dashboards: Gruvbox, Solarized, Dracula, Catppuccin Mocha, Monokai, Tomorrow, One Dark).
- **Themes page** (top-nav → Themes) with a vertical strip of every theme on the left, a builder pane in the middle, and a sticky preview on the right. Click any theme to load it; bundled themes show a "Duplicate to edit" CTA, user themes are editable + deletable.
- **Theme builder**: 20 colour tokens (3 surfaces + 4 text + 1 edge + 6 accents × 2 (base + soft) + 1 on-accent) plus mode (light/dark) and optional font-family. Live preview tracks every input. Optional auto-derive switch computes each `accent_*_soft` as `mix(accent, bg, 0.78)` for one less thing to tune.
- **Image-to-theme**, upload a photo or poster; k-means picks dominant colours and the assignment heuristic spreads them across the Spectra tokens (light/dark mode auto-detected from the modal cluster's luminance). One click fills the form.
- **User-saved themes** persist at `data/themes/user.json`. The user-CSS endpoint (`/themes/user.css`) emits one `[data-theme="user-<slug>"]` block per saved theme, loaded alongside the bundled Spectra cascade. Themes ride along in the data-export ZIP.
- **Orthogonal `data-style` axis**, typography / scale / shape, composes with any theme. Bundled styles: Standard / Display / Editorial / Mono / Elegant / Condensed plus Bauhaus / De Stijl / Brutalist forms.
- **17 bundled typefaces** (SIL OFL / Apache 2.0), Inter, IBM Plex, JetBrains Mono, Atkinson Hyperlegible, Archivo, Space Mono, and more in the `fonts_core` plugin.
- **Bundled-theme colour parsing**, the builder lifts every bundled theme's actual `bg / surface / accent-*` values straight from the Spectra CSS at import time, so duplicating Nord produces a Nord-coloured copy (not Light's defaults).

### Administration & ops

- **First-run onboarding wizard**, welcome → broker → device → dashboard. Every step skippable.
- **Top-nav structure**: Send / Dashboards / Schedules / Themes / Widgets (dropdown) / Settings, plus a Dev dropdown under `--dev` grouping Widget gallery + Theme × style matrix.
- **Settings UI** tabs: Server, Renderers, Devices, Widgets, System, Events.
- **Community widget catalog**, audit-only marketplace at [dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets); Settings → Widgets → Browse community widgets lists every entry, one-click install / update / uninstall with a sha256 + schema check on the tarball before it lands. Installed widgets persist under `<data_root>/marketplace/` so Docker / HA image upgrades don't wipe them.
- **Self-update** from the admin UI (reads GitHub tags, applies in-place, restarts), gated to git-clone installs; Docker shows an upgrade hint instead.
- **Data export / import**, pack the entire install (pages, themes, devices, plugin settings, secrets) into a single ZIP; restore on a fresh install. Validated against JSON Schemas before writing.
- **Webhook push**, `POST /api/v1/push` with a bearer token. Re-renders a named page and fans the frame out to every device bound to it. Generate / rotate the token from Settings → System → Webhook. Useful from HA automations, cron, GitHub Actions.
- **Event log** captures every push, schedule fire, and discovery event for the History view.
- **Auth**: password setup on first run, persistent session, change / disable / re-enable from Settings → System, and a `tesserae --reset-password` CLI escape hatch when the password is lost. When disabled, the gate still 403s public IPs and only lets LAN traffic through.
- **Brand assets**, firmware-ready PNG splash images at nine sizes (64 → 1024px square, transparent backdrop) under [`static/brand/firmware/`](static/brand/firmware/) for client builders.

### Networking

- **MQTT** transport (default `tesserae/<device>/frame/...`).
- **Embedded Mosquitto-compatible broker** as a fallback when no external broker is configured.
- **mDNS**, opt-in broadcast of `tesserae.local` on port 8765 (Settings → Server → mDNS). ESP32 captive portals use a per-device `tesserae-<id>.local` of their own.
- **HTTP API** for the TRMNL BYOS protocol (`/api/display`, `/api/setup`, `/api/log`) authed by per-device tokens, plus the webhook push (`POST /api/v1/push`) for external triggers.

### Cross-platform

- **Install methods**: Docker / Docker Compose, shell installer (`install.sh` for macOS / Linux / Pi), PowerShell installer (`install.ps1` for Windows).
- **Windows-specific**: re-exec via Popen + parent-PID handshake (`os.execv` is broken there); UTF-8 default encoding on every file I/O.
- **32-bit Pi OS** fallback to system Chromium when Playwright's bundled browser can't install.

### Telemetry & privacy

- **Off by default.** When opted in, four anonymous events: `app.started` (per process start), `app.heartbeat` (hourly fleet shape + activity counters, shape only, never content), `update.applied` (in-app updater applied a new revision), `theme.user_created` (first time a custom theme is saved, no theme content). No IPs, paths, settings, secrets, or push contents.
- Disable with `TESSERAE_TELEMETRY=0` or the Settings toggle.

### Quality

- **~790 tests** (pytest) green; CI runs every push.
- **`ruff check` + `ruff format --check`** in CI.
- **`mypy --strict`** on contract modules (state, push, plugin / renderer / device loaders, renderer, themes routes).
- **Spectra CSS ↔ theme-registry guard test**, every `[data-theme="..."]` block in the stylesheet has a registry entry and vice versa, so the picker and the cascade can never drift.

</details>

## Clients

The server publishes; something downstream paints the panel. Tesserae
supports two transport shapes:

* **MQTT push**, server publishes a frame the moment a dashboard
  changes; the panel boots, subscribes, paints the retained frame.
  Lowest latency, best for plugged-in Pi / ESP32 panels.
* **HTTP pull**, TRMNL [BYOS](https://help.trmnl.com/en/articles/9510536-bring-your-own-server)
  protocol. The panel polls `GET /api/display` on a server-set cadence
  and paints whatever PNG comes back. Best for battery-constrained
  pollers (jailbroken Kindles, native TRMNL hardware), they
  deep-sleep between polls.

| Client | Pairs with | Transport | What it's for |
|---|---|---|---|
| [**tesserae-pi-png-client**](https://github.com/dmellok/tesserae-pi-png-client) | `pi_png` renderer | MQTT | Pi-side Python daemon using [`inky`](https://github.com/pimoroni/inky)'s `set_image()`. Works on every panel the inky lib supports. Slower of the two Pi paths (quantises every frame) but wire-compatible with the inky-dash v3/v4 protocol. |
| [**tesserae-pi-bin-client**](https://github.com/dmellok/tesserae-pi-bin-client) | `pi_bin` renderer | MQTT | Same Pi-side shape but writes the server's already-packed 4-bpp buffer straight into inky's internal `_buf`, no PIL on the paint path. Fastest path on a Pimoroni Inky Impression. |
| [**tesserae-esp32-bin-client**](https://github.com/dmellok/tesserae-esp32-bin-client) | `esp32_bin` renderer | MQTT | Battery-powered ESP32-S3 firmware for the Waveshare 13.3" Spectra 6 panel. Deep-sleeps between wakes; months of battery life from a single Li-Po. Refresh cadence set via `sleep_interval_s` config. |
| [**tesserae-photopainter-7.3-bin-client**](https://github.com/dmellok/tesserae-photopainter-7.3-bin-client) | `esp32_bin` renderer | MQTT | ESP32-S3 firmware for the Waveshare 7.3" PhotoPainter (landscape-native, 800×480 Spectra 6). Same wire contract + deep-sleep pattern as the 13.3" client; just sized for the smaller panel. Pick `waveshare_photopainter_7_3` as the panel preset. |
| Any BYOS-compatible client | `trmnl_png` renderer | HTTP | TRMNL-spec client. Pair via the **Add device → TRMNL** flow in Settings: server mints a short access token, you paste it into the client, the client polls. Tested with the [KOReader trmnl-display plugin](https://github.com/koreader/koreader) on a jailbroken Kindle. |

## Compatible displays

Any panel the [`inky`](https://github.com/pimoroni/inky) library drives
works via the Pi clients. Two purpose-built ESP32 firmwares cover the
Waveshare Spectra 6 family: a 13.3" client and a 7.3" PhotoPainter
client (different sizes, same MQTT contract, same server-side
renderer).

### Pimoroni Inky lineup

| Panel | Resolution | Colours | Pi `pi_png` | Pi `pi_bin` | Tested |
|---|---|---|---|---|---|
| Inky pHAT | 212×104 | 2 / 3 colour | ✓ |, |, |
| Inky wHAT | 400×300 | 2 / 3 colour | ✓ |, |, |
| Inky Impression 4" (ACeP) | 640×400 | 7 colour (ACeP) | ✓ | ✓ |, |
| Inky Impression 4" (PIM789) | 640×400 | 6 colour (Spectra 6) | ✓ | ✓ |, |
| Inky Impression 5.7" | 600×448 | 7 colour (ACeP) | ✓ | ✓ | ✅ |
| Inky Impression 7.3" (ACeP) | 800×480 | 7 colour (ACeP) | ✓ | ✓ |, |
| Inky Impression 7.3" (PIM773) | 800×480 | 6 colour (Spectra 6) | ✓ | ✓ |, |
| Inky Impression 13.3" | 1600×1200 | 6 colour (Spectra 6) | ✓ | ✓ | ✅ |

Each device card carries a per-instance "Calibrated palette + tone
mapping" switch (off by default). Turning it on dithers against the
panel's measured colours instead of nominal sRGB; A/B with your own
content since the trade-off is content-dependent.

The `pi_bin` path is faster (the server packs the 4-bpp buffer) but
needs an Impression, the smaller pHAT / wHAT panels go through the
slower-but-universal `pi_png` route. The older ACeP and current
Spectra 6 revisions of the 4" and 7.3" are listed separately because
their palette and pre-quantise saturation defaults differ (the colour
gamut picker on each device card switches between them). Untested
doesn't mean broken, it just means nobody's confirmed it on real
hardware yet.
[Open an issue or PR](https://github.com/dmellok/tesserae/issues) if
you've run a panel that isn't ticked.

### Waveshare panels

| Panel | Resolution | Colours | Client | Tested |
|---|---|---|---|---|
| Waveshare 13.3" Spectra 6 (ESP32-S3) | 1200×1600 (portrait native) | 6 colour (Spectra 6) | [tesserae-esp32-bin-client](https://github.com/dmellok/tesserae-esp32-bin-client) | ✅ |
| Waveshare 7.3" PhotoPainter (ESP32-S3) | 800×480 (landscape native) | 6 colour (Spectra 6) | [tesserae-photopainter-7.3-bin-client](https://github.com/dmellok/tesserae-photopainter-7.3-bin-client) | ✅ |

### TRMNL-compatible (HTTP pull)

Any client implementing the TRMNL BYOS spec works against the
`trmnl_png` renderer. The renderer fits / contrast-tweaks / quantises
to 1-bit greyscale PNG; the client just paints what it polls. Pair via
**Add device → TRMNL** in Settings, the server mints a short access
token you paste into the client config.

| Panel | Resolution | Colours | Client | Tested |
|---|---|---|---|---|
| Amazon Kindle Paperwhite 2 (jailbroken) | 758×1024 | greyscale | [KOReader trmnl-display plugin](https://github.com/koreader/koreader) | ✅ |
| [Native TRMNL device](https://usetrmnl.com/) | 800×480 | 1-bit | TRMNL firmware |, |

The token is short on purpose (5 chars from a typeable alphabet)
because the typical client has an on-screen keyboard. It's
LAN-safe rather than internet-safe, use a Tailscale-style overlay or
keep Tesserae bound to your LAN.

### Custom panels

Anything else: pick `custom` in **Settings → Panel** and set the
dimensions. The renderer only cares about width × height; if your
chosen client drives it, Tesserae will too.

See [**Screens & compatibility**](https://dmellok.github.io/tesserae/compatibility/)
for the renderer / device kind matrix and current test status.

## Install

### Home Assistant App (easiest if you already run HA)

Tesserae ships as a Home Assistant App that installs through HA
Supervisor and shows up as a sidebar Ingress tab. No Docker, no
shell, no separate password, HA's auth gates the admin UI.

1. **Settings → Apps → app store → ⋮ → Repositories**, paste
   `https://github.com/dmellok/homeassistant-tesserae-addon`, click
   **Add**.
2. The new repository surfaces a **Tesserae** entry (stable) and a
   **Tesserae (edge)** entry (tracks `main`, rebuilt every release).
   Click **Install** on whichever you want.
3. After install, hit **Start**, then **Open Web UI**, the admin
   UI loads inside HA's Ingress tab.

The App persists `/data` across upgrades, sets
`TESSERAE_HA_INGRESS=1` so the standalone login is skipped (HA's
session gates it instead), and wires `SUPERVISOR_TOKEN` through so
Tesserae can call Supervisor for version + restart niceties. See
[**Install on Home Assistant**](https://dmellok.github.io/tesserae/install/home-assistant/)
for the full App guide, MQTT auto-discovery, and the broker-
sharing story.

### Docker (quickest standalone)

```sh
mkdir tesserae && cd tesserae
curl -fsSLO https://raw.githubusercontent.com/dmellok/tesserae/main/docker-compose.yml
docker compose up -d
```

Open <http://localhost:8765>. See
[**Install via Docker**](https://dmellok.github.io/tesserae/install/docker/)
for the Mosquitto sidecar variant, host-networking for mDNS, and the
upgrade story.

### macOS, Linux, Raspberry Pi

```sh
curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr https://raw.githubusercontent.com/dmellok/tesserae/main/install.ps1 -UseBasicParsing | iex
```

The installer sanity-checks git + Python 3.11+, clones the repo, sets up
a venv, installs Chromium via Playwright (with a system-browser fallback
for 32-bit Pi OS), and writes a `run.sh` / `run.ps1` shortcut.

### Manual

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main         # production: waitress, port 8765
.venv/bin/python -m app.main --dev   # Flask dev server with reload + debugger
```

After it's running, visit <http://localhost:8765>, first request walks
through password setup and the onboarding wizard. See
[**Install Tesserae**](https://dmellok.github.io/tesserae/install/server/)
for the deep guide (broker config, Chromium overrides, dev workflow).

## Privacy & telemetry

**Off by default.** When opted in (Settings → Server → App), Tesserae
posts four anonymous events to the project's
[aptabase](https://github.com/aptabase/aptabase) backend:

- `app.started`, per process start (version, Python, platform).
- `app.heartbeat`, hourly. Fleet shape (number of devices / pages /
  user themes, device kinds, `is_docker`, `is_homeassistant`) +
  activity counters since the previous heartbeat (pushes, push
  failures, widget errors). Shape and counts only, never push
  content, never settings values.
- `update.applied`, the in-app updater applied a new revision (from /
  to short SHAs + channel).
- `theme.user_created`, fired the first time a user persists a custom
  theme. No theme content is sent, just the fact that the theme
  builder got reached.

No IPs, paths, settings values, secrets, push contents, or anything
tied to a real-world identity. Disable with `TESSERAE_TELEMETRY=0` or
the toggle in Settings. Full details:
[**Privacy & telemetry**](https://dmellok.github.io/tesserae/privacy/).

## Contributing

Issues + the project board live on GitHub:
[dmellok/tesserae](https://github.com/dmellok/tesserae/issues). Multi-head
testers on panels other than the Inky 13.3" and the Waveshare 13.3"
ESP32 panel are especially welcome, the matrix is small and we
mark verified panels in
[Screens & compatibility](https://dmellok.github.io/tesserae/compatibility/#whats-been-tested-on-real-hardware).

- **[Discussions](https://github.com/dmellok/tesserae/discussions)**, show your dashboard, pitch a widget, ask "how do I…", or feed back on what's coming next.
- **[Contributing guide](.github/CONTRIBUTING.md)**, dev setup, the three checks (pytest / ruff / mypy), and commit conventions.
- **[Security policy](.github/SECURITY.md)**, how to report a vulnerability privately.
- **[Code of conduct](.github/CODE_OF_CONDUCT.md)**, be kind, assume good faith, don't be a jerk.

## Built with

Tesserae stands on generously-licensed open source:

- **[Phosphor Icons](https://phosphoricons.com/)**, every icon in the admin UI + widgets (6 weights, MIT).
- **[Chart.js](https://chartjs.org/)** v4.4.0, the line / bar / radar / pie / horizon plots across finance, weather, and stats widgets (MIT).
- **17 typefaces** under SIL OFL or Apache 2.0, Inter, IBM Plex, JetBrains Mono, Atkinson Hyperlegible, Archivo, Space Mono, and more in the `fonts_core` plugin.
- **[KOReader](https://github.com/koreader/koreader)** trmnl-display plugin, the upstream Lua client that turns a jailbroken Kindle into a TRMNL-compatible panel.
- **[paperlesspaper/epdoptimize](https://github.com/paperlesspaper/epdoptimize)**, calibrated Spectra 6 + ACeP palette measurements (Apache 2.0) that power Tesserae's opt-in calibrated dither path. Measurements made by the team at [paperlesspaper](https://paperlesspaper.de/en) on their eInk picture frames; we ported the numbers into [`app/quantizer.py`](app/quantizer.py) and paired them with our own linear tone-mapping pre-pass. Full attribution in [NOTICES.md](NOTICES.md).

Full list in [docs/credits](https://dmellok.github.io/tesserae/credits/).

## License

MIT, see [LICENSE](LICENSE).
