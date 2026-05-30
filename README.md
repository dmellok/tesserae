# Tesserae

E-ink dashboard companion. Compose tile-based dashboards in the browser,
render headless, push the resulting frame to one or more devices (Pi,
ESP32) over MQTT.

Sibling rebuild of [inky-dash](https://github.com/dmellok/inky-dash) —
same job, but renderers and devices are drop-a-folder plugins, so adding
new hardware is a contained change. The name: a
[*tessera*](https://en.wikipedia.org/wiki/Tessera) is the individual
tile of a mosaic; the editor composes a dashboard out of cells.

**📖 [Full documentation](https://dmellok.github.io/tesserae/)** —
install guides, the [widget gallery](https://dmellok.github.io/tesserae/widgets/gallery/)
(47 widgets), the [architecture deep dive](https://dmellok.github.io/tesserae/dev/architecture/),
and [how to build a widget](https://dmellok.github.io/tesserae/dev/writing-a-plugin/) (with AI).

> **For tinkerers, not your mum.** Tesserae is a `docker compose up` or
> `git clone → tinker` dashboard. Polished admin chrome, but the
> deployment story still assumes you can read tracebacks. Aimed at
> hobbyists running a Pi appliance at home.

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

## Clients

The server publishes; something downstream paints the panel. Three
reference clients live in their own repos — pick whichever matches your
hardware. Each takes a `device_id` (the topic prefix) and announces
itself on `tesserae/<device_id>/status` so the server can auto-discover it.

| Client | Pairs with | What it's for |
|---|---|---|
| [**tesserae-pi-png-client**](https://github.com/dmellok/tesserae-pi-png-client) | `pi_png` renderer | Pi-side Python daemon using [`inky`](https://github.com/pimoroni/inky)'s `set_image()`. Works on every panel the inky lib supports. Slower of the two Pi paths (quantises every frame) but wire-compatible with the inky-dash v3/v4 protocol. |
| [**tesserae-pi-bin-client**](https://github.com/dmellok/tesserae-pi-bin-client) | `pi_bin` renderer | Same Pi-side shape but writes the server's already-packed 4-bpp buffer straight into inky's internal `_buf` — no PIL on the paint path. Fastest path on a Pimoroni Inky Impression. |
| [**tesserae-esp32-bin-client**](https://github.com/dmellok/tesserae-esp32-bin-client) | `esp32_bin` renderer | Battery-powered ESP32-S3 firmware for the Waveshare 13.3" Spectra 6 panel. Deep-sleeps between wakes; months of battery life from a single Li-Po. Refresh cadence set via `sleep_interval_s` config. |

## Compatible displays

Any panel the [`inky`](https://github.com/pimoroni/inky) library drives
works via the Pi clients. The ESP32 client is purpose-built for the
Waveshare 13.3" Spectra 6.

### Pimoroni Inky lineup

| Panel | Resolution | Colours | Pi `pi_png` | Pi `pi_bin` | Tested |
|---|---|---|---|---|---|
| Inky pHAT | 212×104 | 2 / 3 colour | ✓ | — | — |
| Inky wHAT | 400×300 | 2 / 3 colour | ✓ | — | — |
| Inky Impression 4" (PIM789) | 640×400 | 6 colour (Spectra 6) | ✓ | ✓ | — |
| Inky Impression 5.7" | 600×448 | 7 colour (ACeP) | ✓ | ✓ | — |
| Inky Impression 7.3" (PIM773) | 800×480 | 6 colour (Spectra 6) | ✓ | ✓ | — |
| Inky Impression 13.3" | 1600×1200 | 6 colour (Spectra 6) | ✓ | ✓ | ✅ |

The `pi_bin` path is faster (the server packs the 4-bpp buffer) but
needs an Impression — the smaller pHAT / wHAT panels go through the
slower-but-universal `pi_png` route. Untested doesn't mean broken — it
just means nobody's confirmed it on real hardware yet.
[Open an issue or PR](https://github.com/dmellok/tesserae/issues) if
you've run a panel that isn't ticked.

### Waveshare panels

| Panel | Resolution | Colours | Client | Tested |
|---|---|---|---|---|
| Waveshare 13.3" E6 (ESP32-S3) | 1600×1200 | 6 colour (Spectra 6) | ESP32 `esp32_bin` | ✅ |

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
