# Tesserae

[![License](https://img.shields.io/github/license/dmellok/tesserae)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/dmellok/tesserae)](https://github.com/dmellok/tesserae/releases/latest)
[![CI](https://github.com/dmellok/tesserae/actions/workflows/ci.yml/badge.svg)](https://github.com/dmellok/tesserae/actions/workflows/ci.yml)
[![GitHub Discussions](https://img.shields.io/github/discussions/dmellok/tesserae)](https://github.com/dmellok/tesserae/discussions)

E-ink dashboard companion. Compose tile-based dashboards in the browser,
render headless, push the resulting frame to one or more devices (Pi,
ESP32) over MQTT.

Renderers and devices are drop-a-folder plugins — adding new hardware
is a contained change.

**📖 [Full documentation](https://dmellok.github.io/tesserae/)** —
install guides, the [widget gallery](https://dmellok.github.io/tesserae/widgets/gallery/)
(47 widgets), the [architecture deep dive](https://dmellok.github.io/tesserae/dev/architecture/),
and [how to build a widget](https://dmellok.github.io/tesserae/dev/writing-a-plugin/) (with AI).

> **Self-hosted hobby project.** Tesserae installs with `docker compose up`
> or a `git clone`. The admin UI is polished, but the deployment story
> still assumes you can read tracebacks if something goes wrong. Aimed at
> people running a Pi appliance at home.

## Community

- **[Discussions](https://github.com/dmellok/tesserae/discussions)** — show your dashboard, pitch a widget, ask "how do I…", or feed back on what's coming next.
- **[Contributing](.github/CONTRIBUTING.md)** — dev setup, the three checks (pytest / ruff / mypy), and commit conventions.
- **[Security policy](.github/SECURITY.md)** — how to report a vulnerability privately.
- **[Code of conduct](.github/CODE_OF_CONDUCT.md)** — be kind, assume good faith, don't be a jerk.

<details>
<summary><b>Screenshots</b> — admin UI &amp; example device frames (click to expand)</summary>

<table>
  <tr>
    <td align="center" width="50%"><a href="docs/screenshots/ui-dashboards.png"><img src="docs/screenshots/ui-dashboards.png" width="340" alt="Dashboards list"></a><br><sub>Dashboards</sub></td>
    <td align="center" width="50%"><a href="docs/screenshots/ui-editor.png"><img src="docs/screenshots/ui-editor.png" width="340" alt="Editor with live preview"></a><br><sub>Editor + live preview</sub></td>
  </tr>
  <tr>
    <td align="center"><a href="docs/screenshots/ui-send.png"><img src="docs/screenshots/ui-send.png" width="340" alt="Send page"></a><br><sub>Send</sub></td>
    <td align="center"><a href="docs/screenshots/ui-schedules.png"><img src="docs/screenshots/ui-schedules.png" width="340" alt="Schedules"></a><br><sub>Schedules</sub></td>
  </tr>
</table>

<sub>Each dashboard renders to a panel-sized frame and publishes only to the display(s) it's bound to.</sub>

<table>
  <tr>
    <td align="center"><a href="docs/screenshots/dash-morning.png"><img src="docs/screenshots/dash-morning.png" height="160" alt="Morning"></a></td>
    <td align="center"><a href="docs/screenshots/dash-lounge.png"><img src="docs/screenshots/dash-lounge.png" height="160" alt="Lounge"></a></td>
    <td align="center"><a href="docs/screenshots/dash-office.png"><img src="docs/screenshots/dash-office.png" height="160" alt="Office"></a></td>
    <td align="center"><a href="docs/screenshots/dash-dev.png"><img src="docs/screenshots/dash-dev.png" height="160" alt="Dev"></a></td>
    <td align="center"><a href="docs/screenshots/dash-glance.png"><img src="docs/screenshots/dash-glance.png" height="160" alt="Glance"></a></td>
  </tr>
</table>

</details>

## Status

A working hobbyist build. Composer → renderers → transport → devices
pipeline, scheduler, Home Assistant discovery, theme builder, form-driven
page editor, and the modern admin UI are all in. Multi-head is
first-class — register multiple panels, bind a dashboard to a specific
display, auto-discover clients that announce themselves on the broker.

47 widgets bundled across weather, F1, calendar, news, finance, GitHub,
clocks, sky, pictures, todo, and Melbourne public transport. See
[widget stability tiers](https://dmellok.github.io/tesserae/widgets/tiers/)
for an upfront read on which depend on undocumented upstreams.

Tests + ruff + mypy `--strict` (on contract modules) clean. CI runs on
every push.

For support, head to [Discussions](https://github.com/dmellok/tesserae/discussions).

## Clients

The server publishes; something downstream paints the panel. Tesserae
supports two transport shapes:

* **MQTT push** — server publishes a frame the moment a dashboard
  changes; the panel boots, subscribes, paints the retained frame.
  Lowest latency, best for plugged-in Pi / ESP32 panels.
* **HTTP pull** — TRMNL [BYOS](https://help.trmnl.com/en/articles/9510536-bring-your-own-server)
  protocol. The panel polls `GET /api/display` on a server-set cadence
  and paints whatever PNG comes back. Best for battery-constrained
  pollers (jailbroken Kindles, native TRMNL hardware) — they
  deep-sleep between polls.

| Client | Pairs with | Transport | What it's for |
|---|---|---|---|
| [**tesserae-pi-png-client**](https://github.com/dmellok/tesserae-pi-png-client) | `pi_png` renderer | MQTT | Pi-side Python daemon using [`inky`](https://github.com/pimoroni/inky)'s `set_image()`. Works on every panel the inky lib supports. Slower of the two Pi paths (quantises every frame) but wire-compatible with the inky-dash v3/v4 protocol. |
| [**tesserae-pi-bin-client**](https://github.com/dmellok/tesserae-pi-bin-client) | `pi_bin` renderer | MQTT | Same Pi-side shape but writes the server's already-packed 4-bpp buffer straight into inky's internal `_buf` — no PIL on the paint path. Fastest path on a Pimoroni Inky Impression. |
| [**tesserae-esp32-bin-client**](https://github.com/dmellok/tesserae-esp32-bin-client) | `esp32_bin` renderer | MQTT | Battery-powered ESP32-S3 firmware for the Waveshare 13.3" Spectra 6 panel. Deep-sleeps between wakes; months of battery life from a single Li-Po. Refresh cadence set via `sleep_interval_s` config. |
| Any BYOS-compatible client | `trmnl_png` renderer | HTTP | TRMNL-spec client. Pair via the **Add device → TRMNL** flow in Settings: server mints a short access token, you paste it into the client, the client polls. Tested with the [KOReader trmnl-display plugin](https://github.com/koreader/koreader) on a jailbroken Kindle. |

## Compatible displays

Any panel the [`inky`](https://github.com/pimoroni/inky) library drives
works via the Pi clients. The ESP32 client is purpose-built for the
Waveshare 13.3" Spectra 6.

### Pimoroni Inky lineup

| Panel | Resolution | Colours | Pi `pi_png` | Pi `pi_bin` | Tested |
|---|---|---|---|---|---|
| Inky pHAT | 212×104 | 2 / 3 colour | ✓ | — | — |
| Inky wHAT | 400×300 | 2 / 3 colour | ✓ | — | — |
| Inky Impression 4" (ACeP) | 640×400 | 7 colour (ACeP) | ✓ | ✓ | — |
| Inky Impression 4" (PIM789) | 640×400 | 6 colour (Spectra 6) | ✓ | ✓ | — |
| Inky Impression 5.7" | 600×448 | 7 colour (ACeP) | ✓ | ✓ | ✅ |
| Inky Impression 7.3" (ACeP) | 800×480 | 7 colour (ACeP) | ✓ | ✓ | — |
| Inky Impression 7.3" (PIM773) | 800×480 | 6 colour (Spectra 6) | ✓ | ✓ | — |
| Inky Impression 13.3" | 1600×1200 | 6 colour (Spectra 6) | ✓ | ✓ | ✅ |

The `pi_bin` path is faster (the server packs the 4-bpp buffer) but
needs an Impression — the smaller pHAT / wHAT panels go through the
slower-but-universal `pi_png` route. The older ACeP and current
Spectra 6 revisions of the 4" and 7.3" are listed separately because
their palette and pre-quantise saturation defaults differ (the colour
gamut picker on each device card switches between them). Untested
doesn't mean broken — it just means nobody's confirmed it on real
hardware yet.
[Open an issue or PR](https://github.com/dmellok/tesserae/issues) if
you've run a panel that isn't ticked.

### Waveshare panels

| Panel | Resolution | Colours | Client | Tested |
|---|---|---|---|---|
| Waveshare 13.3" E6 (ESP32-S3) | 1600×1200 | 6 colour (Spectra 6) | ESP32 `esp32_bin` | ✅ |

### TRMNL-compatible (HTTP pull)

Any client implementing the TRMNL BYOS spec works against the
`trmnl_png` renderer. The renderer fits / contrast-tweaks / quantises
to 1-bit greyscale PNG; the client just paints what it polls. Pair via
**Add device → TRMNL** in Settings — the server mints a short access
token you paste into the client config.

| Panel | Resolution | Colours | Client | Tested |
|---|---|---|---|---|
| Amazon Kindle Paperwhite 2 (jailbroken) | 758×1024 | greyscale | [KOReader trmnl-display plugin](https://github.com/koreader/koreader) | ✅ |
| [Native TRMNL device](https://usetrmnl.com/) | 800×480 | 1-bit | TRMNL firmware | — |

The token is short on purpose (5 chars from a typeable alphabet)
because the typical client has an on-screen keyboard. It's
LAN-safe rather than internet-safe — use a Tailscale-style overlay or
keep Tesserae bound to your LAN.

### Custom panels

Anything else: pick `custom` in **Settings → Panel** and set the
dimensions. The renderer only cares about width × height; if your
chosen client drives it, Tesserae will too.

See [**Screens & compatibility**](https://dmellok.github.io/tesserae/compatibility/)
for the renderer / device kind matrix and current test status.

## Install

### Docker (quickest)

```sh
mkdir tesserae && cd tesserae
curl -fsSLO https://raw.githubusercontent.com/dmellok/tesserae/main/docker-compose.yml
docker compose up -d
```

Open <http://localhost:8000>. See
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
.venv/bin/python -m app.main         # production: waitress, port 8000
.venv/bin/python -m app.main --dev   # Flask dev server with reload + debugger
```

After it's running, visit <http://localhost:8000> — first request walks
through password setup and the onboarding wizard. See
[**Install Tesserae**](https://dmellok.github.io/tesserae/install/server/)
for the deep guide (broker config, Chromium overrides, dev workflow).

## Privacy & telemetry

**Off by default.** When opted in (Settings → Server → App), Tesserae
posts two anonymous events (`app.started`, `update.applied`) to the
project's [aptabase](https://github.com/aptabase/aptabase) backend. No
IPs, paths, settings, secrets, push contents, or anything tied to a
real-world identity. Disable with `TESSERAE_TELEMETRY=0` or the toggle
in Settings. Full details:
[**Privacy & telemetry**](https://dmellok.github.io/tesserae/privacy/).

## Contributing

Issues + the project board live on GitHub:
[dmellok/tesserae](https://github.com/dmellok/tesserae/issues). Multi-head
testers on panels other than the Inky 13.3" and the Waveshare 13.3"
ESP32 panel are especially welcome — the matrix is small and we
mark verified panels in
[Screens & compatibility](https://dmellok.github.io/tesserae/compatibility/#whats-been-tested-on-real-hardware).

## License

MIT — see [LICENSE](LICENSE).
