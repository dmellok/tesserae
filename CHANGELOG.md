# Changelog

All notable changes to Tesserae are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are
[SemVer](https://semver.org/) (pre-1.0, so minors can carry breaking changes).

## [Unreleased]

— in flight on `main` —

## [0.11.7] — 2026-06-02

### Changed

- **Send page no longer blocks the browser on long renders.** All
  five Send-tab POSTs (File, Saved page, URL, Webpage, Gallery) and
  the History "Resend" button now hand the push off to a daemon
  thread and redirect immediately with a "queued" flash. The actual
  render + transport (5–15 s for a 1600×1200 panel) happens off the
  request thread; results stream into the History tab live via the
  existing SSE event log. No more frozen tab.
- Tests run the bg path synchronously (under `app.testing`) so
  ``assert_called_with`` patterns stay deterministic.

## [0.11.6] — 2026-06-01

### Fixed

- mypy `--strict` was failing in CI on the v0.11.5 `BrowserPool`
  worker because the queue's union type `Future[bytes] | Future[str]`
  didn't narrow when `request` was narrowed by `isinstance(request,
  FetchRequest)`. Cast the future explicitly on each branch.

## [0.11.5] — 2026-06-01

### Changed

- **`news_reddit` widget gets a Chromium-fingerprinted fetch path.**
  Reddit's public RSS feed intermittently blocks plain `urllib`
  requests regardless of User-Agent — the bot-shape filter
  fingerprints on TLS / JA3 / HTTP/2 framing, not just the UA. The
  widget now prefers the warm `BrowserPool`'s `fetch_text` (Chromium's
  real fingerprint) and falls back to `urllib` only when the pool is
  off or the Playwright fetch fails. Each pool fetch uses a fresh
  incognito context so cookies don't accumulate.
- `BrowserPool` gains a generic `fetch_text(FetchRequest)` method
  alongside `render(RenderRequest)` so other widgets hitting flaky
  upstreams can opt in without touching the renderer code.

## [0.11.4] — 2026-06-01

### Added

- **Docker self-update awareness.** The Settings → System "Updates"
  card now does the right thing inside the official container: hits
  GitHub's release API (with a `/tags` fallback for repos that don't
  publish Releases yet) to show whether a newer version is out, and
  surfaces a copy-pasteable `docker compose pull && docker compose up
  -d` instead of the git-based "Apply update" button. Result is cached
  for an hour to stay under GitHub's 60/hr anonymous rate limit. Source
  installs keep the existing git-pull / re-exec self-updater.

## [0.11.3] — 2026-06-01

### Changed

- **Cache-busting on every static asset.** `url_for('static', …)` now
  auto-appends `?v=<version>` via a `url_defaults` hook, so a shipped
  JS / CSS change picks up on a soft reload instead of needing
  `Cmd+Shift+R`. In prod the suffix is the app version (busts on every
  release); in `--dev` it's `<version>-<startup-ts>` so each dev restart
  also breaks the cache (useful when iterating on client.js / .css).
- App version is now resolved once in `create_app` and exposed via
  `app.config["APP_VERSION"]` for reuse by telemetry and the static-
  asset cache buster, with pyproject.toml taking precedence over
  `importlib.metadata` (the source-checkout vs. installed-wheel split
  already shipped in 0.11.2).

## [0.11.2] — 2026-06-01

### Fixed

- **Telemetry was reporting the wrong version.** The app version sent in
  `appVersion` / `sdkVersion` came from `importlib.metadata.version("tesserae")`,
  which reads frozen wheel metadata — so an `-e .` install kept reporting
  whatever pyproject.toml said at the last `pip install`, even if the
  version got bumped on disk after. Source checkouts now read
  `pyproject.toml` directly; installed wheels still fall back to
  `importlib.metadata`.
- **Events page timestamps are now human-readable** (`Jun 1 14:23:45`,
  local time) on both the server-rendered rows and the live SSE stream.
  The machine-readable ISO timestamp stays in the `<time datetime="…">`
  attribute for accessibility.

## [0.11.1] — 2026-06-01

### Changed

- `github_repo` — tightened the four directions to match an updated
  handoff for the repo card specifically:
  - RE1 (Refined): repo name stays lowercase; stat row is hairlines now
    (no solid-colour tiles), and description carries inline lang +
    license chips.
  - RE2 (Geometric): added the slim paper description strip between the
    green header and the language strip.
  - RE3 (Swiss): repo name lowercase + bold (not uppercase); stats
    swapped from dots to small squares; numerals are light-weight (300);
    ink bars fill the bottom more densely.
  - RE4 (Data): repo name lowercase; left column is now a vertical
    language list (row per language, distributed to fill the column)
    instead of a wrapped legend.

## [0.11.0] — 2026-06-01

### Added

- **Four visual directions per GitHub widget.** `github_activity`,
  `github_actions`, `github_contributions`, `github_pr_queue`, and
  `github_repo` each ship four selectable looks from a Bauhaus / Swiss
  handoff: Refined (charcoal `DarkHeader` + solid stat tiles), Geometric
  (De Stijl colour blocks), Swiss / International (hairlines only), and
  Data (donut + bars + outlined tiles). Pick per cell via the new
  `variant` option.

### Changed

- GitHub widgets map the design's categorical accent palette (green /
  red / yellow / blue / ink / muted) to `--c-data-2` / `--c-accent` /
  `--c-data-3` / `--c-data-4` / `--c-text` / `--c-text-soft` —
  intentionally NOT `--c-ok` / `--c-warn` / `--c-danger`, since the
  GitHub accents code identity, not semantic status.

## [0.10.1] — 2026-06-01

### Changed

- `ha_climate`: dropped a dead `transparent` fallback on
  `var(--c-bg)` — the semantic token is always defined on the cell
  host, so the fallback never fired. Cosmetic; no behaviour change.

## [0.10.0] — 2026-06-01

### Added

- **Six visual directions per Home Assistant widget.** `ha_climate`,
  `ha_entities`, `ha_history`, and `ha_sensor` each ship six selectable
  looks from a Bauhaus / Swiss handoff: Refined, Geometric (De Stijl),
  Swiss / International, Data (Gauge Dial / Meters / Chart / Ring
  Gauges), Editorial / Editorial Ledger, and Glanceable. Pick per cell
  via the new `variant` option. State→colour (heat/cool/ok/warn/idle)
  is derived from each entity's current action/value and maps to the
  theme's `--c-*` semantic tokens, so every Tesserae theme restyles
  cleanly across colour, mono, and neon.

## [0.9.0] — 2026-06-01

### Added

- **Six visual directions per calendar widget.** `calendar_day`,
  `calendar_week`, and `calendar_month` each ship six selectable looks
  pulled from a Bauhaus / Swiss design handoff: Refined, Geometric,
  Swiss / International, Timeline / Agenda Split, Editorial, and
  Glanceable / Dot Density. Pick per cell via the new `variant` option.
  Each direction maps cleanly onto the theme's `--c-*` tokens, so the
  same widget restyles across colour, mono, and neon themes without
  hard-coded hex. Per-event colour comes from the feed configured in
  *Plugins → Calendar Feeds*.

## [0.8.3] — 2026-05-31

### Added

- **Six monochrome themes** for 1-bit panels (Paper, Carbon, Newsprint,
  Halftone, Ash, Graphite). Designed for the Kindle / native TRMNL
  rendering pipeline — Paper / Carbon are flat for sharp text, Newsprint
  / Halftone are halftone-friendly for printed-page texture, Ash /
  Graphite sit between as softer alternatives.
- **`tags` field on themes** to support family grouping. The theme
  picker on the page editor now groups by family in `<optgroup>`s, so
  the six mono themes cluster together — someone setting up a Kindle
  dashboard can spot them without scrolling past 20 colour themes.

## [0.8.2] — 2026-05-31

### Fixed

- TRMNL discovery headers are case-insensitive, so KOReader's
  `Png-Width` / `Png-Height` (Title-case) land as `panel_w` / `panel_h`
  in the cache and pre-fill the Register form — previously they only
  matched the lowercase / native-TRMNL spellings and were silently
  dropped.

### Changed

- README updated with a "TRMNL-compatible (HTTP pull)" panels subsection;
  Kindle Paperwhite 2 (jailbroken, KOReader trmnl-display plugin) listed
  as tested.

## [0.8.1] — 2026-05-31

### Fixed

- Scheduler skips schedules whose target page was deleted instead of
  letting them fire every tick and log "page not found" to the History
  view. Warns once per session per stale schedule so the operator sees
  something actionable in the log without spam.
- Schedules editor flags stale schedules with a red "page deleted" pill
  + a subtle row tint, so the user can rebind or delete them.

## [0.8.0] — 2026-05-31

TRMNL HTTP-pull compatibility lets a jailbroken Kindle (running the
KOReader trmnl-display plugin) or any native TRMNL hardware paint a
Tesserae-managed dashboard alongside the existing MQTT-push Pi / ESP32
panels. Plus a stale-discovery sweep on HA start so deleted device
tiles stop ghosting Home Assistant.

### Added

- **TRMNL BYOS protocol.** New `trmnl_client` device kind, `trmnl_png`
  renderer (greyscale + 1-bit quantise), and `/api/display` /
  `/api/setup` / `/api/log` HTTP blueprint authed by per-device 5-char
  access tokens. Onboarded clients show up in the existing *Discovered*
  strip with panel dims pre-filled; tokens are short on purpose so they
  can be typed into clients without a keyboard.
- **Per-device Display name** field on the Settings card; saving
  re-publishes Home Assistant discovery so the HA device tile title
  updates without a Tesserae restart.

### Changed

- TRMNL device heartbeats (request headers) feed the same
  `DEVICE_STATUS` cache + HA discovery path as MQTT clients, so battery
  / signal / IP sensors appear in HA for TRMNL panels too.
- `latest_render_for(device_id)` on `PushManager` is now persisted to
  `data/core/latest_renders.json` so fresh polls after a restart don't
  serve placeholders.

### Fixed

- HA discovery orphan sweep on start — retained discovery configs for
  devices deleted while Tesserae was offline get blanked, so HA stops
  showing ghost device tiles forever.

## [0.7.0] — 2026-05-30

Docker shipped, Settings got a refactor + a complete picture-quality
control surface, and themes were curated down from a sprawl to 25
deliberate variants.

### Added

- **Official Docker image + compose** publishing to GHCR. Host
  networking by default (so mDNS and broker discovery work without YAML
  edits); `TESSERAE_HOST_IP` env-var surfaces the right LAN IP in
  render URLs when running bridged.
- **Per-device picture quality** controls (dither / saturation /
  contrast) on the device card; per-device renderer clones inherit
  their fleet's averaged defaults on creation.
- **Curated theme set** — 25 named themes across light, dark, and neon
  families, with a dev-only widget-gallery theme picker.

### Changed

- `app/main.py` split into `app_factory` + `transport_wiring`;
  `settings_routes.py` split into the `app/settings/` package.
- README trimmed; depth lives in the MkDocs wiki.

### Fixed

- Portrait rotation actually rotates (was no-op'd by a wrong axis swap).
- Docker entrypoint chowns `/app/data` then drops privileges via
  `gosu`.
- Safari login auto-save works (hidden username field on setup + login).

## [0.6.0] — 2026-05-30

Quiet hours, webhooks, per-device timetable, and a stack of embedded-
broker fixes.

### Added

- **Quiet hours** — suppress automated pushes during a configurable
  window, with a per-device override in Settings → Devices.
- **Webhook push API** — `POST /api/v1/push` for external automation
  (Home Assistant, n8n, etc.).
- **Per-device Timetable card** — read-only view of which schedules
  reach this display, sorted by next-fire.
- **Modal webhook-token reveal** so the secret isn't pasted into the
  Settings form.

### Changed

- Device card saves all subsections via one "Save changes" button.
- Collapsible device cards keep the Settings page scannable when the
  fleet grows.

### Fixed

- Embedded broker rebuilt against `amqtt` 0.11 (auth + system topics
  restored).
- "Test broker connect" works for the built-in broker.
- Send page auto-ticks the only registered device instead of erroring
  on submit.

## [0.5.0] — 2026-05-30

Onboarding polish + recording infrastructure for the docs.

### Added

- Panel-size picker on the onboarding device step.
- Playwright-driven recording scripts for the onboarding + dashboard
  flows (used to generate the docs GIFs).

### Changed

- Telemetry consent copy softened on the onboarding step.

## [0.4.0] — 2026-05-30

Anonymous opt-in telemetry (Aptabase) and the Windows port. Several
quick follow-ups to align with Aptabase's wire format and fix Windows
self-restart.

### Added

- **Anonymous opt-in telemetry.** Off by default; `app.started` and
  `update.applied` events sent to a self-hosted Aptabase instance with
  no PII. Consent prompt added to onboarding.
- Test-event button in the System tab + telemetry attempts surfaced in
  the Events tab.
- Pre-push hook that nudges when `pyproject.toml` hasn't been bumped.

### Fixed

- Aptabase wire format: `isDebug` must be a bool, SDK version must be
  `name@version` (0.4.3).
- Windows self-restart no longer hangs — replaces `os.execv` with
  `Popen + os._exit + parent-pid handshake` (0.4.4).
- Every `Path.read_text` / `Path.write_text` pinned to
  `encoding="utf-8"` so Windows doesn't mangle em-dashes (0.4.8).
- Reloader-watcher process no longer double-inits MQTT, scheduler, and
  telemetry in dev mode (0.4.8).

## [0.3.0] — 2026-05-30

A System tab (self-update + backup/restore), the `--c-*` semantic theme
token layer used by every widget, an event-log dedup, and the MkDocs
wiki scaffold.

### Added

- **Settings → System** — self-update from a GitHub tag, full data
  backup/restore (zips `data/` minus user-controlled exclusions like
  the picture-gallery cache).
- **MkDocs wiki** under `docs/` with auto-generated widget gallery and
  compatibility tables; deploys to GitHub Pages.
- **mDNS advertiser** — opt-in `tesserae.local` (and
  `tesserae-dev.local` in `--dev`).
- **Per-push image-fit picker** on the Send page with an accurate live
  preview.
- **Spotify Now Playing** gains five selectable layout variants.

### Changed

- Theme system now exposes a `--c-*` semantic token layer; every widget
  reads from it instead of raw palette tokens, so a single theme switch
  updates the whole dashboard.
- Event log caps device-status rows separately from push events and
  skips unchanged heartbeats, so the log stays useful at high panel
  count.
- MQTT default client id is per-host so multiple Tesserae instances on
  the same broker don't reconnect-loop.

### Fixed

- `news_reddit` widget reads the RSS feed — Reddit's `.json` endpoint
  now 403-blocks unauthenticated clients.

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

[Unreleased]: https://github.com/dmellok/tesserae/compare/v0.8.3...HEAD
[0.8.3]: https://github.com/dmellok/tesserae/releases/tag/v0.8.3
[0.8.2]: https://github.com/dmellok/tesserae/releases/tag/v0.8.2
[0.8.1]: https://github.com/dmellok/tesserae/releases/tag/v0.8.1
[0.8.0]: https://github.com/dmellok/tesserae/releases/tag/v0.8.0
[0.7.0]: https://github.com/dmellok/tesserae/releases/tag/v0.7.0
[0.6.0]: https://github.com/dmellok/tesserae/releases/tag/v0.6.0
[0.5.0]: https://github.com/dmellok/tesserae/releases/tag/v0.5.0
[0.4.0]: https://github.com/dmellok/tesserae/releases/tag/v0.4.0
[0.3.0]: https://github.com/dmellok/tesserae/releases/tag/v0.3.0
[0.2.0]: https://github.com/dmellok/tesserae/releases/tag/v0.2.0
[0.1.0]: https://github.com/dmellok/tesserae/releases/tag/v0.1.0
