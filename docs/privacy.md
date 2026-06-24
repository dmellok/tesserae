# Privacy

**Tesserae sends no phone-home telemetry.** The app does not contact
any third-party analytics service. A fresh install never reports back
to the maintainer; no app.started / app.heartbeat / update.applied
events are emitted, and no instance identifier is generated or
persisted.

## What's never sent

There is no opt-in toggle for usage analytics. The app does not send:

- IP addresses
- hostnames
- file paths
- settings values
- secrets (passwords, tokens, API keys)
- push contents
- dashboard layouts
- broker addresses
- a stable install identifier
- anything tied to a real-world identity

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
