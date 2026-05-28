# Changelog

All notable changes to Tesserae are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are
[SemVer](https://semver.org/) (pre-1.0, so minors can carry breaking changes).

## [0.2.0] — 2026-05-28

First release aimed at fellow hobbyists: multi-panel support is now
first-class, the widget catalogue is broad, and a fresh clone of each
reference client works against the server defaults with no manual topic
editing.

### Multi-head devices (headline)

- **Device instances.** Register multiple physical panels in
  Settings → Devices, each with its own id, MQTT topics, and panel size.
  A built-in *kind* is a template; each panel you add is an *instance* of
  a kind. Per-instance add / edit-panel / delete, all hot-reloaded — no
  restart.
- **Per-page targeting.** Bind a dashboard to a specific device; it sizes
  to that panel and pushes only to its renderers. Unbound pages fan out
  to every renderer at the virtual-panel size (the renamed global panel
  fallback).
- **Auto-discovery.** The server listens on `tesserae/+/status`; any
  client publishing a heartbeat for an unregistered id shows up in a
  *Discovered* strip with its kind and panel size pre-filled, for
  one-click registration.
- **Orientation calibration.** Push a numbered test card to a panel, say
  which number landed in the top-left, and the orientation is set for
  you. Manual rotation (0 / 90 / 180 / 270°) is a single dropdown.
- **Send-page device picker** on the File / URL / Webpage tabs routes a
  manual push to one display.

### Breaking changes

- The `pi_client` device kind split into **`pi_bin_client`** (topic
  prefix `pi_bin`) and **`pi_png_client`** (prefix `pi_png`). Default
  topics moved from `tesserae/pi/...` to `tesserae/pi_bin/...` /
  `tesserae/pi_png/...`. Update clients to the new defaults (the
  reference clients already are), or set `device_id = "pi"` explicitly to
  keep the old prefix.
- MQTT grammar is now `tesserae/<device-id>/<channel>[/<format>]`, where
  `<device-id>` is the per-device topic prefix.

### Widgets

- ~40 widgets across weather, F1, calendar, news, finance, GitHub,
  clocks, sky, pictures, todo, and Melbourne public transport — each with
  a documented [stability tier](README.md#widget-stability-tiers).
- New since 0.1.0 include the full weather / news / finance / GitHub /
  calendar / sky / pictures families, analog & word clocks, and a
  `webpage` screenshot widget.

### Editor & themes

- Interactive layout editor: drag-resize, insert, and delete cells, with
  auto-save and click-to-edit live preview.
- Theme builder with live preview, image-to-theme palette extraction
  (k-means + token assignment), and an eyedropper.
- Dashboard icon picker (Phosphor), dark-mode toggle, mobile-responsive
  admin.

### Install & ops

- One-liner installer for macOS / Linux / Raspberry Pi (`install.sh`) and
  Windows (`install.ps1`), with interactive port selection and a Chromium
  fallback for platforms Playwright doesn't ship a binary for.
- Ships under [waitress](https://docs.pylonsproject.org/projects/waitress/)
  by default; `--dev` opts into the Flask reloader.
- CI runs ruff + pytest + mypy `--strict` (on contract modules) on every
  push.

### Reference clients (separate repos)

- [tesserae-pi-bin-client](https://github.com/dmellok/tesserae-pi-bin-client)
  — default id `pi_bin`.
- [tesserae-pi-png-client](https://github.com/dmellok/tesserae-pi-png-client)
  — default id `pi_png`.
- [tesserae-esp32-bin-client](https://github.com/dmellok/tesserae-esp32-bin-client)
  — default id `esp32`, device id set via captive portal.

All three publish discovery hints (`kind`, `panel_w`, `panel_h`,
`fw_version`) so they auto-register on the server.

## [0.1.0] — 2026-05-26

Initial milestone build: plugin / renderer / device loaders, composer,
MQTT transport + push pipeline, manifest-driven settings with an auth
gate, scheduler, Send page, generalised event log, and Home Assistant
MQTT discovery.

[0.2.0]: https://github.com/dmellok/tesserae/releases/tag/v0.2.0
[0.1.0]: https://github.com/dmellok/tesserae/releases/tag/v0.1.0
