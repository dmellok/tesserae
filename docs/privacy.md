# Privacy

**The Tesserae app itself sends no phone-home telemetry.** The core app
does not contact any third-party analytics service. A fresh install
never reports back to the maintainer; no `app.started` /
`app.heartbeat` / `update.applied` events are emitted from the app
itself, and no analytics identifier is embedded in the app's own
outbound traffic.

Individual widgets you install may make external network calls
documented in their own READMEs. Two of them, both opt-in and both
shipping with v0.70.0, do fetch from a Tesserae-hosted aggregation
endpoint. Details in [Widgets that fetch from external services](#widgets-that-fetch-from-external-services) below.

## What the core app itself never sends

The Tesserae app itself does not send:

- IP addresses
- hostnames
- file paths
- settings values
- secrets (passwords, tokens, API keys)
- push contents
- dashboard layouts
- broker addresses
- anything tied to a real-world identity

This applies to the app itself. Individual widgets may make external
network calls documented in their own READMEs.

## Install identifier

On first startup Tesserae generates a **random UUID** and stores it
at `data/core/install_id.json`. The value has no connection to your
identity, hardware, IP address, or Tesserae account: it's a random
string, generated with `uuid.uuid4()`, persisted so widgets that
declare `needs_install_id` (shared-world features like the planned
tamagotchi pet or dashboard traveler) can key against a stable
per-install identity across restarts.

Widgets can also request a **widget-scoped derivation** via
`needs_scoped_id`, which returns `SHA-256(install_id + plugin_id)`.
Different widgets get different derived identifiers so their outbound
calls can't be correlated by an external service.

You can regenerate the identifier at any time in
**Settings → System → Install identifier**. Regeneration resets any
per-install state the widget side has accumulated (a pet's history, a
traveler's home waypoint), because from those services' point of view
you look like a new install.

## Widgets that fetch from external services

Widgets you install may fetch data from external sources: weather from
Open-Meteo, RSS from feed URLs, calendars from iCal endpoints. Two
widgets shipping with v0.70.0 optionally fetch from `api.tesserae.ink`.
Both are opt-in.

### `tesserae_status` update-indicator chip

When the update-indicator chip is enabled on a `tesserae_status` widget
placement, the widget fetches
`https://api.tesserae.ink/version/latest?channel=stable&current=<v>&install=<scoped-id>`
on each render so it can show an amber "update available" chip when a
newer Tesserae release exists. The `install` parameter is the
widget-scoped derivation of your install identifier (see above); it
lets aggregate install counts dedupe across days without correlating
with any other widget's identity. No IP address or User-Agent is
stored on the api.tesserae.ink side; only a coarse country lookup +
the query params. See the
[tesserae-api source](https://github.com/dmellok/tesserae-api) for the
exact implementation.

The chip is off by default. Turn it on per composition when you want
the update signal on your dashboard.

### Device firmware check

Tesserae can look up the latest known firmware version for each of
your registered device kinds against
`https://api.tesserae.ink/firmware/<kind>/latest`, so the Devices card
shows "v1.1.0 (v1.2.0 available)" when a device is behind. **Off by
default** as of v0.70.1: turn it on from **Settings → System → Check
for device firmware updates**. When enabled, the lookup happens
lazily (on the first Devices page render, and on demand every 60 min
after). The only outbound data is the device kind name; no install
identifier, no device-specific fields. When disabled, the Devices
card still shows the current firmware version reported by each
device's heartbeat; the "update available" pill just never fires.

### Home Assistant App auto-update

When installed as the Home Assistant App, Supervisor manages Tesserae's
update cycle through Home Assistant's own update infrastructure. That
path is a Home Assistant feature, not Tesserae phoning home; the
[Home Assistant privacy policy](https://www.home-assistant.io/privacy/)
covers what Supervisor sends.

## Per-device telemetry (stays on the server)

Tesserae *does* track per-device diagnostics for the displays you
register (battery percentage, RSSI, heartbeat cadence, smart-sync
predictions). That data stays on your server in
`data/core/device_telemetry.json` and `data/core/battery_history.db`,
and is only surfaced inside the admin UI (Settings → Devices, the
battery indicator, smart-sync scheduling). It is never transmitted
off the box.

## Docs site analytics

Aggregate analytics on the documentation site
(`dmellok.github.io/tesserae/`) are kept separately from the app and
provide a coarse traffic overview only. The docs site honours
Do-Not-Track headers and skips analytics entirely when DNT is set.
