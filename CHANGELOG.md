# Changelog

All notable changes to Tesserae are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are
[SemVer](https://semver.org/) (pre-1.0, so minors can carry breaking changes).

## [Unreleased]

## [0.41.1], 2026-06-08

### Fixed

- **Browse page now lets you uninstall widgets that were originally
  bundled.** After the 0.38-0.41 slim-down, widgets that moved to
  the catalog still had their folders on disk for users who upgraded,
  but Browse showed "Install" (which then refused on folder
  collision) rather than "Uninstall". The marketplace now detects
  this state: when the catalog's declared folders all exist on disk
  but no marketplace record tracks them, the entry surfaces as
  "from a previous install" with the Uninstall flow enabled. Clicking
  Uninstall removes only the folders the catalog *currently* declares,
  never touches arbitrary plugin folders.

### Changed

- **Catalog badge wording: "official" → "verified".** "Official" read
  as an endorsement claim ("Spotify-official Tesserae integration")
  when the badge actually meant "reviewed + maintained by the catalog
  owner". Same shield icon + tooltip.

  Catalog-side companion: the `Spotify` and `GitHub` entries renamed
  to `Spotify Widgets` and `GitHub Widgets` to further disambiguate
  ("widgets ABOUT the service" rather than "from the service"). Other
  catalog entries with generic names (Finance, Sky, etc.) stay as-is.

- **`monitoring` and `picture_extras` bundles split** into single-
  purpose catalog entries: `glances` + `octoprint` (was `monitoring`),
  `unsplash` + `apple_album` (was `picture_extras`). The combined
  bundles lumped together widgets for very different audiences (a
  homelab user vs a 3D-printer hobbyist; an Unsplash fan vs an Apple
  Music user). Single-purpose entries respect those audiences. Pure
  catalog-side change, no Tesserae code touched for the split.

## [0.41.0], 2026-06-08

### Changed (breaking, but easy to fix)

- **Bundle slim-down completes: 18 more widgets moved to the
  community catalog, 7 new bundles.** The default install now ships
  ~30 universally-useful widgets instead of ~65. All moved widgets
  are installable in one click from Settings → Widgets → Browse
  community widgets.

  | Bundle | Widgets | Source repo |
  |---|---|---|
  | Finance | finance_crypto, finance_currency, finance_stock | [dmellok/tesserae-finance](https://github.com/dmellok/tesserae-finance) |
  | Sky | sky_air_traffic, sky_aurora, sky_bom_warnings, sky_moon | [dmellok/tesserae-sky](https://github.com/dmellok/tesserae-sky) |
  | Weather Extras | weather_air_quality, weather_pollen_count, weather_wind | [dmellok/tesserae-weather-extras](https://github.com/dmellok/tesserae-weather-extras) |
  | Picture Extras | picture_unsplash, picture_apple_album | [dmellok/tesserae-picture-extras](https://github.com/dmellok/tesserae-picture-extras) |
  | Clock Extras | clock_qlock, clock_world | [dmellok/tesserae-clock-extras](https://github.com/dmellok/tesserae-clock-extras) |
  | Monitoring | glances_core, glances_status, octoprint_status | [dmellok/tesserae-monitoring](https://github.com/dmellok/tesserae-monitoring) |
  | Public Transport | public_transport_times | [dmellok/tesserae-transport](https://github.com/dmellok/tesserae-transport) |

  Dashboards that referenced any of these will show "widget not
  installed" cells on upgrade until you reinstall from Browse.

  Why: completes the slim-down started in 0.38.0 (F1), 0.39.0
  (Spotify), 0.40.0 (GitHub). The remaining ~30 bundled widgets are
  what a new user can compose a useful dashboard from immediately
  with zero accounts or niche knowledge: clocks, weather, calendar,
  todo, RSS / Hacker News / Wikipedia news, picture_gallery,
  webpage, and the Home Assistant family.

  Also in this release:

  - Browse Card screenshots now ship for every official bundle (the
    catalog declared `screenshot_sizes: ["lg"]` for f1/spotify/github
    in earlier releases but no PNG was uploaded, so the card showed a
    broken-image placeholder).
  - 4 dead `_sky_moon` / `_weather_pollen_count` / `_glances_status` /
    `_octoprint_status` sample functions stripped from
    `app/widget_samples.py`.
  - `docs/widgets/tiers.md` marks moved entries as *(marketplace)*.
  - `docs/widget-build-prompt.md` + `docs/widgets.md` archetype
    examples swapped from moved widgets to still-bundled equivalents
    so AI-built widgets read from valid file paths.

## [0.40.0], 2026-06-08

### Changed (breaking, but easy to fix)

- **GitHub widget family moved out of the bundle.** The seven GitHub
  widgets (`github_core`, `github_actions`, `github_activity`,
  `github_contributions`, `github_pr_queue`, `github_releases`,
  `github_repo`) are no longer bundled and live in the community
  catalog. Reinstall via Settings → Widgets → Browse community
  widgets → Install GitHub.

  Why: continues the bundle slim-down (F1 in 0.38.0, Spotify in
  0.39.0). All seven need a personal access token to do anything
  useful; the typical user, especially a non-developer HA user,
  never enables them. Marketplace is the right home.

  Source repo: [dmellok/tesserae-github](https://github.com/dmellok/tesserae-github).
  Catalog entry: id `github`, official, bundle pattern.

  Also removed: three `_github_*` sample functions in
  `app/widget_samples.py`. The `docs/widgets/tiers.md` table marks
  the GitHub + F1 entries as `(marketplace)` so the stability tier
  doc still describes the upstreams accurately.

## [0.39.0], 2026-06-08

### Changed (breaking, but easy to fix)

- **Spotify widget family moved out of the bundle.** The four Spotify
  widgets (`spotify_core`, `spotify_now_playing`, `spotify_queue`,
  `spotify_album_art`) are no longer bundled and live in the
  community catalog. Dashboards that referenced them will show
  "widget not installed" cells on upgrade until you reinstall via
  Settings → Widgets → Browse community widgets → Install Spotify.

  Why: continuation of the bundle slim-down started in 0.38.0
  (F1). OAuth-required widgets aren't useful out of the box, every
  user has to register a Spotify Developer app and complete a
  connect flow before any cell renders. Marketplace is the right
  home: install if you want it.

  Source repo: [dmellok/tesserae-spotify](https://github.com/dmellok/tesserae-spotify).
  Catalog entry: id `spotify`, official, bundle pattern (one
  install lays down all 4 folders).

  The shared `_SPOTIFY_ART_DATA_URL` sample placeholder + the three
  `_spotify_*` sample functions in `app/widget_samples.py` were
  removed alongside the folders.

## [0.38.0], 2026-06-08

### Changed (breaking, but easy to fix)

- **F1 widget family moved out of the bundle.** The five F1 widgets
  (`f1_core`, `f1_last_race`, `f1_next`, `f1_standings_drivers`,
  `f1_weekend`) are no longer bundled with Tesserae and instead live
  in the community catalog. Existing dashboards that referenced these
  widgets will show "widget not installed" cells on upgrade until
  you reinstall them via Settings → Widgets → Browse community
  widgets → Install Formula 1.

  Why: kicking off the bundle slim-down. F1 is genuinely niche (most
  users don't follow the sport) and shipping 5 widgets every install
  inflated the picker for everyone. Marketplace is the right home,
  opt in if you want them, ignore them if you don't.

  Source repo for the moved bundle:
  [dmellok/tesserae-f1](https://github.com/dmellok/tesserae-f1).
  Catalog entry: id `f1`, official, bundle pattern (one install lays
  down all 5 folders).

  This is the first family to move. More niche / interest-specific
  families will follow in upcoming releases (Spotify, GitHub,
  Finance, etc.); each one will carry a CHANGELOG note in the same
  shape so you always know where a removed widget went.

## [0.37.0], 2026-06-07

### Added

- **`design.palette` manifest opt-in** for widgets that want to use
  arbitrary CSS colours (gradients, layered shapes, soft shadows)
  rather than the strict Spectra colour tokens. Default stays
  `strict`; widgets declare `"design": {"palette": "extended"}` to
  opt in. Renderer behaviour is unchanged: extended widgets rely on
  the existing Floyd-Steinberg dither pass to approximate their
  CSS colours on the panel palette. Typography + spacing tokens
  remain mandatory regardless of the palette flag so multi-widget
  dashboards stay visually coherent.
- **`weather_now_scenic` widget**, the reference implementation of
  the extended palette opt-in. Pill-shaped card with weather +
  time-of-day theming across nine presets (sunny day, clear night,
  partly day/night, cloudy day/night, rain, snow, storm). Same
  Open-Meteo data path as `weather_now` (shares the WMO mapping)
  but a slimmer payload tailored to the scenic layout. Ships as a
  separate widget rather than a style switch on `weather_now` so
  the two can coexist on a dashboard and the bundled widget keeps
  its strict-palette guarantees for BW panels.

## [0.36.2], 2026-06-07

### Fixed

- **Capability hook no longer rejects every real network call after
  DNS.** The egress check installed two socket hooks (one on
  `socket.create_connection`, one on `socket.socket.connect`) so
  raw-socket use couldn't bypass the hostname allowlist. Problem:
  stdlib's `create_connection` does `getaddrinfo()` and then calls
  `sock.connect((ip, port))` internally, which our `socket.connect`
  hook then re-checked against the hostname allowlist. The IP isn't
  in the manifest, so every legitimately-declared widget (weather_now,
  clock_sunrise_sunset, anything talking to a real upstream) failed
  with `CapabilityDenied: tried to connect to '188.40.99.226' but
  didn't declare it`. The connect hook now skips the post-DNS call
  via a contextvar set inside the approved `create_connection` path;
  raw `socket.socket().connect()` outside that path is still checked.

## [0.36.1], 2026-06-07

### Fixed

- **Playwright base image + pip pin re-coupled.** The Dockerfile was
  pinned at `mcr.microsoft.com/playwright/python:v1.49.0-noble` but
  `pyproject.toml` allowed `playwright>=1.42,<2`, so a fresh image
  build resolved Playwright 1.60.0 against a v1.49 Chromium and bombed
  at first render with `Executable doesn't exist at
  /ms-playwright/chromium_headless_shell-1223/...`. Bumped the base
  image to `v1.60.0-noble` and tightened the pip pin to
  `>=1.60,<1.61` so the Chromium revision the Python client expects
  and the one bundled in the image are guaranteed to match. The
  Dockerfile comment already promised this lockstep; now it actually
  is.

## [0.36.0], 2026-06-07

### Fixed

- **Data import + backup restore no longer refused in the HA add-on /
  Docker image.** `refuse_in_container()` was over-broad: it gated
  four routes (`update/apply`, `update/rollback`,
  `backup/<id>/restore`, `data/import`), but only the first two
  need it (those mutate the code tree via `git pull`). Restore +
  import only write to the persistent `data/` volume, which
  survives container upgrades; the post-restore `os.execv` cleanly
  replaces the container's PID 1 (the entrypoint already `exec`s
  through gosu). Previously, hitting Import in HA Settings →
  System → Backups returned a misleading "use docker compose
  pull" flash instead of importing. Both routes now work in HA,
  Docker, and bare installs alike; self-update remains refused
  under Docker.

## [0.35.2], 2026-06-07

### Changed

- **Marketplace card thumbnails switch to `object-fit: fill`.**
  After two iterations on aspect-ratio (5:3 → 3:2) and
  object-position tuning still left letterboxing on some catalog
  entries, dropped the aspect-ratio math and stretched the image
  to fill the frame. Predictable, no empty space, accepting minor
  distortion as the tradeoff. Cards are thumbnails; contributors'
  source repo link is one click away if a user wants the true
  ratio.

## [0.35.1], 2026-06-07

### Changed

- **Marketplace card thumbnail aspect-ratio 5:3 → 3:2** to match
  the lg widget cell dimensions (1200×800). Tight catalog
  screenshots (taken via element-screenshot of `.cell` rather than
  Playwright `full_page`) now fill the Browse card with no
  letterboxing. Wider/taller sources still crop cleanly via
  `object-fit: cover`; the `object-position` shifted from `center`
  to `top center` so legacy full-page screenshots show the widget
  area instead of dead space below it.
- **All three live catalog entries got fresh tight screenshots**
  (1200×800 each) so the new aspect-ratio doesn't show
  letterboxing on entries that pre-date this change.

## [0.35.0], 2026-06-07

Marketplace phase 2: widget capability manifest + runtime
enforcement. Closes [#2](https://github.com/dmellok/tesserae/issues/2).

### Added

- **`requires:` block in `plugin.json`.** Widgets declare which
  capabilities they need (network egress targets, settings reads,
  filesystem writes) as a list of `<category>:<value>` strings.
  Vocabulary: `network:<hostname>` (or `network:*` for unrestricted
  but flagged in review), `settings:plugin` / `settings:plugin/<id>`
  / `settings:app`, `filesystem:write:<path>`.
- **Runtime network enforcement.** The host installs a hook over
  `socket.create_connection` + `socket.socket.connect` and gates
  every connect against the active widget's allowlist. A widget
  trying to phone home to an undeclared host raises
  `CapabilityDenied`, which the composer surfaces as a cell-level
  error rather than a render failure. Lower-level socket usage is
  covered too — the hook fires before DNS so hard-coded IPs can't
  dodge the gate either.
- **Backward compatibility.** Widgets without a `requires:` block
  load with no enforcement (legacy behaviour), so the existing
  catalog + bundled widgets keep working unchanged. The catalog
  review checklist now asks contributors to declare for new
  submissions.

### Changed

- **Three bundled widgets ported as worked examples:** `weather_now`
  and `clock_sunrise_sunset` declare `network:api.open-meteo.com`;
  `clock_analog` declares `requires: []` (the explicit "no
  capabilities" form, distinct from missing block which is legacy).
- **Reviewer checklist** in [docs/dev/publishing-a-widget.md](https://dmellok.github.io/tesserae/dev/publishing-a-widget/)
  expanded to require `requires:` declarations in catalog
  submissions + grep the source against the declared set to confirm
  there's no drift.
- **Widget contract** at [docs/widgets.md](https://dmellok.github.io/tesserae/widgets/)
  gains a "Capabilities, `requires:`" section: vocabulary table,
  how enforcement works (contextvar scope + socket hook), backward
  compat, and an honest threat-model section noting Python's
  sandbox-hostility — this catches casual drift, not a determined
  attacker. Real isolation lives in #3.

### Trust model — what this catches

What it catches: a community widget that quietly tries to POST your
MQTT password to some upstream gets a `CapabilityDenied` and the
deny lands in the server log. The "reviewer reads the manifest +
greps the code" workflow becomes load-bearing — the manifest is a
machine-checked claim.

What it doesn't catch: a widget reaching around with `ctypes`,
frame inspection, or a subprocess. The hook is defence-in-depth on
top of the audit-only PR review, not a substitute for it.
Capability declarations PLUS the PR review is the trust model;
full isolation is [#3](https://github.com/dmellok/tesserae/issues/3).

### Internals

- `app/capabilities.py` (new): `Capabilities` dataclass + `parse()`
  + `capability_scope()` contextmanager + idempotent
  `install()` / `uninstall()` socket hooks. ContextVar-backed so
  concurrent renders don't cross-contaminate.
- `app/plugin_loader.py`: parses `requires:` during discovery and
  stores the snapshot on `Plugin.capabilities`. Malformed entries
  log + drop rather than failing the load.
- `app/composer.py`: `_fetch_plugin_data` enters
  `capability_scope(plugin.capabilities)` around the widget's
  `fetch()` call.
- `app/app_factory.py`: installs hooks once after the registry is
  built. Idempotent so the dev reloader doesn't stack hooks.
- 19 new tests covering parse / scope nesting / urllib enforcement /
  legacy bypass / install idempotence. 853 tests pass total.

## [0.34.2], 2026-06-07

### Added

- **Topbar battery indicator.** When any registered device instance
  is reporting a `battery_pct` heartbeat, a small Phosphor battery
  glyph appears next to the theme toggle. Single-device installs
  show the percentage inline; multi-device installs show a count
  badge with a hover/click popover listing every battery + level.
  The trigger paints in the tone of the worst battery
  (critical ≤10% / low ≤30% / ok) so a single critical device
  catches the eye when others are fine. Mains-powered devices
  (Pi paths) don't surface here, so a panel-only deployment stays
  uncluttered.

### Changed

- **Low-battery push overlay.** Dropped the black border (was picking
  up dither artifacts on some panel gamuts) and re-anchored the
  Phosphor glyph to share a baseline with the percentage text so
  they read as "on the same line" instead of the icon visibly
  floating above the digits.

## [0.34.1], 2026-06-07

### Changed

- **Rename Plugins → Widgets in the UI + docs.** Top-nav dropdown
  label, the "All plugins" link, the `/plugins/` page title + h1,
  the Settings tab + blurb, and every "Settings → Plugins"
  breadcrumb in the docs all now say "Widgets". The
  Plugin-development wiki section becomes Widget development;
  `docs/dev/writing-a-plugin.md` is renamed to `writing-a-widget.md`.
  Code paths (`plugin_loader`, `/plugins/` URLs, the `plugins/`
  directory, `plugin.json`, `plugin.schema.json`) are unchanged —
  those are the technical contract and renaming them would break
  every installed widget.

## [0.34.0], 2026-06-07

Marketplace bundle support for widget families.

### Added

- **Catalog bundles.** A catalog entry can now install a whole
  widget family (e.g. github_core + github_releases +
  github_actions) in one click. The tarball wraps every subplugin in
  a single containing folder, and the marketplace install path
  auto-detects the layout. Optional `folders: [...]` field on the
  entry declares the expected subfolders; when present, the install
  verifies the tarball matches exactly and the Browse card lists
  every folder so users see what's about to land.
- **Browse card bundle line.** Cards now show "Bundle, installs N
  plugin folders: foo_core, foo_widget" when the entry installs
  more than one folder, so the install action is unambiguous.

### Changed

- **`InstalledRecord` now carries a `folders` list** instead of a
  single `plugin_id`. Existing single-widget records (pre-0.34) read
  back as `folders=[catalog_id]` via a backward-compat shim in
  `from_json`, so v0.33 installs survive the upgrade untouched.
- **Marketplace uninstall is keyed by `catalog_id`** (the form field
  on the Browse uninstall button renamed from `plugin_id` to
  `catalog_id`). For bundle entries the uninstall removes every
  folder the install record lists, plus optionally each folder's
  data dir under `data/plugins/`.
- **Catalog CI workflow** (in the seed at
  `docs/marketplace-catalog-seed/`) now extracts each tarball and
  cross-checks the declared `folders` against the actual subfolders.
  Mismatched submissions die at the PR gate.

### Internals

- `app/marketplace.py` gains `_detect_layout`: unwraps the
  GitHub-style single-folder envelope, then returns either
  `{entry.id: <single folder>}` (single widget) or
  `{child_id: <child path>, ...}` (bundle). Tarball extraction still
  uses `tarfile.data_filter` (PEP 706) so path-traversal + suid
  attacks die at the gate.
- Per-folder install moves are now atomic-ish across the bundle: a
  mid-move OS error rolls back every backup + drops any
  partially-installed sibling, so a half-installed bundle can't
  leak onto disk.
- Embedded `kind` + `version` cross-checks are skipped for bundles
  because each subfolder has its own kind + version independently;
  the sha256 verify catches tarball drift regardless.
- 9 new test cases for bundles: happy path, auto-detect without
  declared folders, declared-vs-actual mismatch, subfolder without
  plugin.json, collision on one subfolder aborts atomically,
  uninstall removes every folder, uninstall with delete_data clears
  each data dir, reinstall replaces in-place, and a legacy-record
  backward-compat read.

## [0.33.0], 2026-06-07

Community widget marketplace, phase 1 (audit-only catalog).

### Added

- **Settings → Plugins → Browse community widgets.** A new Browse
  page (also reachable from the Plugins nav dropdown) shows a card
  grid of community-contributed widgets, each with a screenshot,
  description, author, tags, and an Install button. Behind the scenes
  the host fetches a static `widgets.json` index from a configurable
  catalog URL (default
  `raw.githubusercontent.com/dmellok/tesserae-widgets/main/widgets.json`),
  validates it against the new `schema/marketplace.schema.json`, and
  on install: downloads the pinned release tarball, verifies the
  declared sha256, validates the embedded `plugin.json` against
  `schema/plugin.schema.json`, and drops the result into
  `plugins/<id>/` alongside the bundled widgets.
- **Update + Uninstall flows.** Browse shows "Update available" when
  the catalog's `release.version` exceeds the installed one;
  Uninstall refuses to touch any plugin not tracked in
  `data/core/marketplace.json` (so bundled plugins are safe even
  against a hand-crafted POST), with an optional tick to also drop
  the plugin's data dir.
- **Restart-required banner + button.** Install / uninstall flash a
  "Restart Tesserae to load it" notice and add a one-click button
  that hits the existing updater re-exec path. Live re-discovery of
  the plugin registry would need blueprint deregistration + safe
  `importlib.reload` which Flask doesn't support cleanly, so v1
  treats every marketplace mutation as restart-to-pick-up.
- **`marketplace_index_url` setting** (Settings → Server → App). Point
  at a fork or empty the field to disable the Browse page entirely.
- **Catalog repo seed** at `docs/marketplace-catalog-seed/`. A
  copy-into-a-new-repo scaffold for `dmellok/tesserae-widgets`:
  empty `widgets.json`, a copy of the host's index schema,
  `CONTRIBUTING.md` (PR review checklist + submission flow), a PR
  template, and a GitHub Actions workflow that validates the index
  + fetches every tarball + verifies sha256 + checks screenshot
  PNGs exist before any PR can merge.

### Trust model (audit-only, phase 1)

Every catalog entry is a PR reviewed by the catalog maintainer. There
is no capability sandbox or process isolation in this phase — see
GitHub issues #2 and #3 for the follow-up work those represent.
Audit-only is fine while every entry passes through human review; it
does not scale to "anyone can publish without review", which is
gated behind those two issues.

### Internals

- `app/marketplace.py` — `Marketplace` orchestrator with `fetch_index`,
  `install`, `uninstall`, and a 5-minute in-memory index cache.
  Tarball extraction uses `tarfile.data_filter` (PEP 706) so path
  traversal + suid attacks die at extract time; downloads cap at
  4 MiB.
- `app/marketplace_routes.py` — Browse / install / uninstall / restart
  endpoints mounted at `/plugins/browse*` (alongside the existing
  plugin admin routes, but on a separate blueprint).
- `tests/test_marketplace.py` — 18 cases covering index fetch failure
  modes, install rejections (bundled collision, compat mismatch,
  sha256 mismatch, oversize tarball, missing manifest, kind
  mismatch), happy path, upgrade-in-place, uninstall safety net,
  data-dir preservation by default, corrupt-state recovery.

## [0.32.1], 2026-06-07

### Fixed

- CI mypy --strict failure on `app/push.py`: the `_ui_font` helper
  reassigned `font` from a `FreeTypeFont` to the base `ImageFont`
  type on the bitmap-default fallback path, which is incompatible
  under strict typing. Annotated as `Any` to match the function's
  return type.

## [0.32.0], 2026-06-07

Polish pass on Send + low-battery overlay for battery-powered devices.

### Added

- **Low-battery overlay on device pushes** (Settings → Server → App).
  When a device with a battery reports a charge at or below the
  threshold (default 15%, configurable 5-50%), a small white-on-black
  chip with a Phosphor `battery-warning` glyph + percent label paints
  in the top-right of the composition before the per-renderer
  transform. So the warning survives dithering / quantization and
  reaches the panel as drawn. Per-renderer (each device's last-known
  battery decides whether its push wears the chip), so a fan-out to a
  mains-powered Pi + a low TRMNL only marks the TRMNL. Toggle off
  entirely under Settings → Server → App.
- **Phosphor regular TTF vendored** at
  `static/icons/phosphor/regular/Phosphor.ttf`. Server-side image
  rendering (currently the low-battery overlay) needs the icon font
  on disk because PIL can't read the woff2 web font. Loaded lazily +
  cached by pixel size.

### Changed

- **Send page live preview now follows the picked target device.**
  Ticking a device on File / URL / Webpage / Gallery reshapes the
  preview frame to that device's panel aspect (e.g. 800×480 landscape
  vs 1200×1600 portrait). Previously the preview was pinned to the
  global virtual panel preset, so fit-mode previews on a non-default
  target were misleading. Falls back to the virtual panel when no
  device is ticked.
- **Removed the Saved-dashboard tab from Send.** Pushing a saved
  dashboard already lives on the Dashboards page (per-row Send
  button) and inside the editor (Push-now). Send is now arbitrary-
  input only: File / URL / Webpage / Gallery.

### Fixed

- **White flash on every page navigation in dark mode.** External
  CSS was responsible for the body bg, so the canvas painted white
  for a frame while base.css was in flight. An inline `<style>` in
  `<head>` now sets the `html` element's background + `color-scheme`
  to match the saved theme, so the canvas paints the right colour
  before any external CSS arrives.

## [0.31.0], 2026-06-06

Whole-catalogue widget visual pass (59 widgets, every family) plus an
admin-UI refresh on top, vivid light themes, a Glances instance
registry, history page filtering, and a slate of mobile polish.

### Widget visual pass — every family

Family-by-family visual pass that landed across v0.30.0-v0.30.3 and is
now formally cut. Every bundled widget got the same treatment: turn
paragraphs of numbers into visual anchors that scale cleanly across
cell sizes (xs/sm/md/lg).

- **Calendar** (`calendar_day`, `calendar_week`, `calendar_month`):
  time-of-day icons in the day gutter, event-density strips, weekend
  column tints, today-marker chips, per-feed micro-strips, heat-tint
  backgrounds, UTC → local TZ fixes. `calendar_week` server forwards
  event end times so multi-hour events span properly.
- **Clocks** (`clock_analog`, `clock_qlock`, `clock_sunrise_sunset`,
  `clock_word`, `clock_world`): five analog face styles + AM/PM sun
  indicator + date plate + roman/arabic/dots numerals; QLOCK gets
  vignette + paper-texture backgrounds; sun-arc widget gains twilight
  bands, golden-hour tints, full sun-path arc; word clock gets phase
  badges; world clock adds five-phase sun glyphs + 24-hour day/night
  strip per city.
- **GitHub** (6 widgets): per-workflow-type icons + 8-bar timeline +
  duration sparkline; stacked-by-type 7-day histogram with dominant-
  type glyph in each bar; paired streak hero chips + 12-month summary
  bars; PR-age tier chips + draft/ready icons + comment bubbles;
  SemVer bump pill + commits-since-release tail; top-contributors
  strip with avatars.
- **Home Assistant** (12 widgets): per-device-type lead glyph + SVG
  battery with fill bar + CRITICAL/LOW pills (ha_battery); corner
  timestamp + multi-camera grid (ha_camera); radial thermostat dial
  with grid for multi-entity (ha_climate); Chart.js Sankey for power
  flow with sun-position glyph + comparison sparkline (ha_energy);
  expanded device-class glyph table + recent-change wash + timer
  badge (ha_entities); threshold line + min/max markers + hourly-
  profile ghost overlay (ha_history); per-light brightness mini-bar
  tinted by colour + kelvin/RGB swatch (ha_lights); stateful kind
  glyphs + unsecured-since timer (ha_locks); blurred album-art bleed
  + track-deterministic waveform glyph (ha_media); expanded device-
  class glyphs + 24h trend arrow + inline sparkline (ha_sensor);
  due-date proximity colour chips + iCal priority dot + OVERDUE
  title bar (ha_todo); coloured-initials avatar fallback + zone-name
  glyph (ha_zones).
- **News** (4): story-type chips + score-strength bars (HN); post-
  type lead glyph + subreddit-coloured stripe (Reddit); source-host
  chip with initial (RSS); era glyph + thumbnail + HTML/CSS year-
  timeline strip (Wikipedia OTD).
- **Sky** (3 of 4, plus `sky_bom_warnings` from the weather pass):
  radar dial with bearing+distance plotting per flight + altitude
  bar + bolder rings (`sky_air_traffic`); half-circle Kp gauge with
  banded segments + needle (`sky_aurora`); progress ring around the
  moon disc + craters clipped to lit side + next-phase chip
  (`sky_moon`).
- **Finance** (3): up/down delta pills (crypto); country flag pair
  in title + Chart.js sparkline with 7-day rolling-average overlay
  (currency); day-range track with current-price pip (stock).
- **Spotify** (2): track-deterministic SVG waveform glyph under the
  progress bar (`spotify_now_playing`); circular progress ring around
  now-playing thumbnail + per-track duration mini-bar
  (`spotify_queue`).
- **Public transport** (`public_transport_times`): route-number
  colour chip + countdown ring around the next-departure glyph.
- **Other** (`glances_status`, `octoprint_status`, `todo`): per-metric
  ring gauges + load/uptime footer (glances); radial print-completion
  ring + percent centre (octoprint); completion progress bar at top
  of list (todo).

### Chart helpers

- `static/spectra-chart.js`: added `sankey()` helper backed by the
  vendored `chartjs-chart-sankey` UMD; `lineChart()` gained
  `threshold` / `markers` / `overlay` params for ha_history's
  threshold line + min/max marker dots + hourly-profile ghost;
  `sparkline()` gained an `overlay` param for finance_currency's
  rolling-average dashed line.
- `static/vendor/chartjs-chart-sankey.min.js` (10 KB UMD) loaded in
  `templates/compose.html` so the Sankey controller registers
  globally.

### Glances Core (new plugin)

- New **glances_core** data plugin with an admin page at
  `/plugins/glances_core/` that persists a list of Glances server
  instances (name + URL) to its `data_dir/instances.json`. Exposes
  `get_instance(id)` + `list_instances()` + `choices(name)`.
- `glances_status` cell now offers a dropdown that picks from saved
  instances; falls back to an inline URL for cells configured before
  the instance registry existed.
- Admin page restyled to match the schedules/themes admin vocabulary
  (`<section class="card">` + `card_head` macro + `.form` + `.field-
  grid` + `.button.primary`).

### Vivid light themes

- Three new bundled light themes with greater bg → surface contrast +
  more saturated accents than the default Light: `vivid-light` (warm
  stone canvas, ~22% L* delta), `citrus-light` (cream canvas, candy-
  bright accents), `arctic-light` (cool steel-blue canvas, jewel-tone
  accents). All three pass the theme-registry guard test.

### History page

- `/history` gained a chip-based source filter that scopes the log to
  one trigger (scheduler / webhook / page / Home Assistant / etc.).
  Chips use the same `.event-type-filter .chip` vocabulary as
  `/events/` so the two pages feel like the same product. Per-source
  count badges, with the active chip inverting to the accent. New
  `EventLog.list(source=)` + `EventLog.source_counts()` powers it.
- Scheduler row chip + nav icon swapped from `ph-clock-clockwise` to
  `ph-calendar-dots` (was too easily confused with History's
  `ph-clock-counter-clockwise`).
- Top-nav order: History moved after Schedules so the destructive +
  read-only views aren't adjacent.

### Mobile zoom lock

- New `mobile_zoom_lock` switch under Settings → Server → App
  (default ON). When ON, the viewport meta pins `maximum-scale=1,
  user-scalable=no` and a small JS gesture blocker catches iOS
  Safari's `gesturestart` events (which deliberately ignore the
  meta). `touch-action: manipulation` on html/body kills double-tap-
  to-zoom across all mobile browsers. Turn OFF to restore the
  browser zoom layer for accessibility.
- `app_settings` now forwarded to every template via
  `app_factory.py`'s context processor.

### Em-dash sweep

- Replaced every em-dash (U+2014) across the repo with the standard
  prose substitutes (3179 replacements across 431 files): `" — "` →
  `, `; bare `—` → `-`. Aligns with the project's "no em-dashes in
  prose" guideline. Doesn't touch en-dashes (U+2013), still used for
  numeric ranges.

### Glances ring sizing + picture chip cleanup

- `glances_status` 0.2.3: ring row claims flex space; rings scale
  with cell up to 14em; redundant CPU hero number dropped in favour
  of the rings.
- Picture widgets (`picture_apod`, `picture_apple_album`,
  `picture_gallery`, `picture_unsplash`): removed the day-badge /
  sequence pill / folder + count chips / credit-avatar chip after
  user feedback that they didn't render well. Original captions
  retained where they existed.

### Docs

- Gallery PNGs recaptured at `lg` (1200×800) for the docs site, so
  every widget card in `docs/widgets/gallery.md` shows the new
  visuals at full-detail size. Generated via
  `scripts/capture_widget_shots.py SIZE=lg`.

### F1 family visual pass

Continued the widget visual pass through the four F1 widgets. Same goal as
the weather pass: turn paragraphs of numbers into real visual anchors that
adapt cleanly across cell sizes.

- **f1_last_race** (0.1.0 → 0.1.4). Replaced the 3-cell status-grid podium
  with team-coloured podium steps in P2-P1-P3 visual order (P1 tallest,
  centre). Each block tinted by constructor livery (Ferrari red, Mercedes
  teal, Red Bull navy, etc.) using a small inline hex map keyed by
  Jolpica's `constructorId`. Trophy glyph hangs above the winner's code;
  plum lightning above whichever driver set the fastest lap (`data.podium[i].fastest`).
  Meta line (circuit · locality · country) progressively sheds bits as the
  cell narrows so it doesn't clip at the bottom. Circuit silhouette lives on
  the right column only at LG; SVG sized to `100% × 100%` with the
  `preserveAspectRatio` from f1_core's `trackSvg` doing the letterboxing.
- **f1_next** (0.1.0 → 0.1.3). Country flag emoji in the title (Canada →
  🇨🇦, Bahrain → 🇧🇭, etc.), every host on the current calendar plus a
  few historical venues mapped. Six session mini-cards (FP1 / FP2 / FP3 /
  Sprint / Quali / **Race**) replace the status-grid; the Race card was
  previously missing entirely. Each card has icon + label + `Sat 14` date
  + `14:00` time, accent-bordered and `color-mix`-soft-tinted by session
  type (practice = muted, sprint/quali = ochre, race = terracotta). Hero
  countdown gets `--accent-1` weight and a `ph-clock-countdown` glyph.
  Schedule is 3 columns × 2 rows at LG so "QUALI" no longer clips to
  "QUA", and adaptive height/width queries handle short MD cells.
- **f1_standings_drivers** (0.1.0 → 0.1.1). `server.py` now fetches the
  previous round's standings (`/current/{round-1}/driverStandings.json`)
  and computes a per-driver `delta` field so the client can render
  position-change chips (`↑3` accent-3, `↓1` accent-1, `-` muted,
  omitted when no previous-round data). Points-gap micro-bar under each
  row scales to `points / leader_points`, filled in the driver's team
  livery colour. Crown glyph (`ph-crown`) marks the championship leader
  to keep `ph-trophy` reserved for race wins in f1_last_race.
- **f1_weekend** (0.1.0 → 0.1.1). Sessions cluster under
  `FRIDAY · 14 MAR` / `SATURDAY · 15 MAR` / `SUNDAY · 16 MAR` day
  headers so the weekend's shape reads in one scan. Race row gets a soft
  accent-1 tinted background + accent-1 left border + black weight so
  "RACE at 13:00" always pops. Country flag in title matches f1_next.

### Color emoji rendering

- **`Dockerfile`**: added `fonts-noto-color-emoji` to the apt install
  alongside `gosu` (~12 MB on top of the existing image). Without it,
  country flag emoji in widgets fall back to regional-indicator letter
  pairs in boxes on Linux. macOS dev hosts use Apple Color Emoji so this
  bug wouldn't have surfaced in local preview.

### Weather widget visual pass

Family-by-family enhancement of every weather widget (plus `sky_bom_warnings`,
which is conceptually weather even though it lives under `sky_*`). Goal: give
each widget a real visual anchor instead of paragraphs of numbers, and make
the layout adapt cleanly across xs / sm / md / lg cells.

- **weather_now** (0.1.5 → 0.1.8). LG is now a 2-column grid, hero +
  4-metric strip on the left, full-height sunrise/sunset arc strip on the
  right (was a thin band crammed under the metrics). MD-tall (height ≥ 600)
  shows the arc band below the metrics; MD-tight (height ≤ 449) drops the
  metric labels and tightens the icon+value stack to fit without clipping.
- **weather_forecast** (0.1.1 → 0.1.5). Replaced the horizontal day strip
  with a vertical day stack: `[Day] [Icon] [Lo ─── Hi] [Rain%]` per row,
  today's row tinted with `--surface-sunken` and the day label flipped to
  `--accent-4`. LG cells get a side-by-side layout with a Chart.js filled
  `lineChart` of the daily highs in terracotta (`--accent-1`). Rain droplet
  icon repositioned to the right of the percentage so every row's icon
  column lines up.
- **weather_hourly** (0.2.0 → 0.2.2). Single-colour temperature line
  replaced with a mixed bar+line chart: rain probability bars on a right
  y-axis (teal), temperature line with a vertical warm-to-cool gradient
  (accent-1 → accent-2 → accent-5). Custom Chart.js plugin shades night
  hours (18-06) with a translucent text-primary tint, clamped to the
  chart area so the bands don't spill past the final tick. Hour count
  culls by cell width (24 lg / 18 md / 12 sm / 6 xs); axis tick font
  auto-scales 10–20px so wide cells paint legible numbers.
- **weather_air_quality** (0.1.1 → 0.1.3). Hero replaced with a
  half-circle gauge, 6 EAQI band segments (moss → teal → ochre →
  terracotta → plum → red) with a marker pip at the current value;
  number reads inside the arc, band label below. Per-pollutant grid
  cells gain a micro-bar showing `value / band_max`, tinted by the
  pollutant's own band. Cells participate in the grid via subgrid so
  every row's label / value / bar tracks stay synchronised, a wrapped
  "4.2 μg/m³" no longer drops one bar below its neighbours.
- **weather_pollen_count** (0.1.1 → 0.1.7). 4-step severity bar per tile
  (Low / Moderate / High / Very High). Icons scale by severity
  (0.75× / 1.0× / 1.25× / 1.5×) so a Very High weed tile visibly dwarfs
  a Low grass tile. No-data tiles collapse the value + bar + level word
  triplet into a single centred `ph-minus-circle`. Empty bar segments
  use a `color-mix` translucent overlay so they read against the soft-
  tinted backgrounds where `--surface-sunken` was invisible. Plus a bug
  fix: `item.level` from the server was a 0-100 percent being used as a
  string key, falling through to muted-grey on every tile.
- **weather_wind** (0.1.1 → 0.1.3). Compass rose now grows 8 teal petals
  sized by the server's 24h speed-weighted directional histogram
  (`data.rose`); current-direction needle (ochre) overlays as an outline
  so the petals stay visible underneath. Beaufort chip
  (`B7 NEAR GALE`-style) replaces the text-only Beaufort label, with the
  background tinted by severity (accent-3 → -2 → -1 → -6 by bucket). LG
  cells get a 12-hour filled gust sparkline below the main row.
- **sky_bom_warnings** (0.1.0 → 0.1.1). Vertical severity colour-band
  down the left of each row (`accent-1 / -3 / -5` by severity). Tag is
  now a proper severity-tinted chip; region (state code) + time-since-
  issued chip in the row meta. Rows sort worst-first
  (red → yellow → blue, then phase, then original order). The
  no-warnings empty state replaced with a moss-tinted card carrying a
  chunky `ph-shield-check` so "all clear" reads as confident
  reassurance instead of blank space.

### Widget preview rebuild

- The dev `/_test/preview` page is now a **single composed page** in one
  iframe instead of four separate cards. Cells lay out as a recursive
  halving spiral, cell 1 takes half the panel, cell 2 takes half the
  remainder, etc., so the same widget paints at LG → MD → MD → SM →
  SM → XS → XS-unassigned in a single render at panel native dimensions.
- **Panel-size dropdown** drives the synthetic page's dimensions (Inky
  / Waveshare presets), so the same widget can be eyeballed at every
  Tesserae-supported panel without composing a real page.
- Cell tags in preview mode now include the **size bucket** alongside
  the cell index and plugin id (e.g. `1 · LG · weather_now`), matching
  the bucket the widget's own container queries fire against.

### History page + per-trigger source chips

- **History moved to a top-level nav entry** (`/history`). Previously
  the push log was a tab buried inside Send; now it's one click from
  anywhere. Send loses its History tab; the four remaining tabs
  (File / Saved / URL / Webpage) still redirect to the new History
  page after a push, so the "I just pushed, where's the result?"
  flow stays muscle-memory.
- **Per-trigger chips on every history row**. Pushes now carry a
  `source` value through the whole pipeline so each row shows what
  kicked it off: **Send page** (paper plane, teal), **Schedule**
  (clock, ochre), **Webhook** (arrow-in, terracotta), **Home
  Assistant** (house, slate). The trigger was already in the event
  log but every page-push was getting logged as `page` regardless of
  caller; `PushManager.push()` now takes a `source=` kwarg and the
  scheduler / webhook / HA call sites pass the right value.

### Dev widget preview page

- **New `/_test/preview` page** (Dev nav → Widget preview). One
  widget rendered at every supported size (xs / sm / md / lg) in a
  single grid, with a left rail for the controls: widget picker,
  theme picker, style picker, sample-data toggle, and a form-builder
  generated from the plugin's `cell_options` schema. Useful when
  iterating on a widget's layout, you can tweak a place label or
  unit and see all four sizes reflow without composing a dashboard
  first. Dev-only, gated behind `debug or testing` like the rest of
  `/_test/`.
- The underlying `/_test/render` endpoint now accepts `?opts=<json>`
  so the preview page can inject cell options through the existing
  composer pipeline.

### Weather widget polish

- **weather_now sizing pass**. Content-adaptive at xs (hero-only,
  vertical stack), sm (two metrics, no labels), md (unchanged), lg
  (hero icon + temp grow to fill, new sunrise/sunset arc band shows
  the sun's current position between rise and set). Fixes the
  "feels empty at lg, cramped at sm" complaint from the visual pass.

## [0.29.0], 2026-06-05

Theme system rebuilt end-to-end, calibrated dither path landed,
admin password management filled in, plus a long tail of editor /
device-pipeline polish. Eighty-eight commits aggregated since
v0.16.26; the intermediate tags v0.16.27 through v0.28.2 carry the
incremental history.

### Themes, the headline rebuild

- **Spectra design system**, orthogonal `data-theme` × `data-style`
  axes, set on `<body>` and overridable per cell. Theme controls
  colour only; style controls typography / spacing / shape, never
  colour. Any of the 19 bundled themes composes with any of the 9
  bundled styles.
- **19 bundled themes** across four families: Light
  (light / sepia / cool-gray / high-contrast), Dark (dark / nord),
  Movement (bauhaus / destijl / brutalist palettes), and base16 (10
  popular code-editor palettes: Gruvbox, Solarized, Dracula,
  Catppuccin Mocha, Monokai, Tomorrow, One Dark).
- **Themes page** at top-nav → Themes, a vertical strip of every
  theme on the left, the builder pane in the middle, and a sticky
  preview on the right. Click any theme to load it; bundled themes
  show a "Duplicate to edit" CTA, user themes are editable +
  deletable.
- **Theme builder**: 20 colour tokens (3 surfaces + 4 text + 1 edge
  + 6 accents × 2 (base + soft) + 1 on-accent), plus mode
  (light/dark) and an optional font-family. Live preview tracks
  every input via inline CSS-variable overrides on the preview pane.
- **Image-to-theme**, upload a photo or poster, k-means picks
  dominant colours, the assignment heuristic spreads them across the
  Spectra tokens (light/dark mode auto-detected from the modal
  cluster's luminance). One click fills the form. Calibration data
  ported from
  [paperlesspaper/epdoptimize](https://github.com/paperlesspaper/epdoptimize)
  (Apache 2.0).
- **Auto-derive soft tints switch**, when on, every
  `accent_N_soft` becomes a mix of its accent with the page
  background, recomputed live as either edits. Persists on the
  user theme.
- **User-saved themes** at `data/themes/user.json` (no longer under
  `data/plugins/themes_core/`, since the themes_core plugin is gone).
  Served as a single `[data-theme="user-<slug>"]` stylesheet from
  `/themes/user.css`; loaded alongside the bundled Spectra cascade
  on every composed page.
- **Bundled-colour parsing**, the builder lifts each bundled
  theme's actual `bg / surface / accent-*` values straight from the
  Spectra CSS at import time, so duplicating Nord (or any other
  bundled theme) produces a copy carrying that theme's real colours
  instead of the Light defaults.

### Quantizer / colour pipeline

- **Opt-in calibrated palette + tone mapping**, per-device toggle on
  the `esp32_bin` and `pi_bin` renderers. Dithers Floyd-Steinberg
  against the panel's measured Spectra 6 / ACeP colours instead of
  nominal sRGB primaries, and runs a linear tone-map pre-pass that
  squeezes the source range into the calibrated black/white band so
  the dither has room to spread error. Calibration data ported from
  paperlesspaper/epdoptimize.
- **Eight dither modes** for the `.bin` packers: Floyd-Steinberg,
  none, Atkinson, Jarvis-Judice-Ninke, Stucki, Bayer-8x8, halftone,
  crosshatch.
- **Firmware-native panel orientation** auto-detected from the
  panel preset (`PanelPreset.native_landscape`); the renderer packs
  at the firmware's actual row stride regardless of how the user
  mounts the panel.
- **Pre-v0.20 ESP32 manifest backfill**, startup migration adds
  `native_w` / `native_h` to existing `esp32_client` instance
  manifests so legacy installs don't paint at the wrong stride.

### Authentication & admin ops

- **Change / disable / re-enable password** from Settings → System
  → Authentication. When disabled, the gate still 403s public IPs
  and only lets LAN traffic through.
- **`tesserae --reset-password`** CLI escape hatch for when the
  password is lost.
- **Firmware splash PNGs** at nine common sizes
  (64 / 96 / 128 / 192 / 256 / 384 / 512 / 768 / 1024) under
  `static/brand/firmware/` for client builders.
- **Dev dropdown** in the top-nav (under `--dev`) grouping the
  Widget gallery + Theme × style matrix.

### Editor / UI polish

- **Reactive editor**: floating back-to-top FAB on the small-viewport
  layout. Drag along the bottom edge to flip the FAB to the other
  side; the side preference persists in localStorage.
- **Composer remounts cells** whenever theme / style / font flips so
  the new cascade actually paints instead of inheriting stale
  variables.
- **Multiselect search box actually filters now** (composer regression).
- **Single-card palette** in the theme builder, surfaces, text, and
  the six accent pairs collapsed into one card with sub-group
  headings.
- **F1 widget pass**: track outline moved + bolder stroke, backing
  card behind the track, team-colour stripe on standings rows, more
  Phosphor icons across the family.
- **Weather suite visual punch**: shaded line chart in `weather_hourly`
  (12h default), hero icon scaling in `weather_now`, AQI scale in
  `weather_air_quality`, compass rose in `weather_wind`, tile cards
  in `weather_pollen_count`.

### Bug fixes

- `Device.panel` now propagates `native_w` / `native_h` through to
  the renderer (regression fixed the office Waveshare 13.3"
  appearing distorted).
- `push.py`'s `_panel_dims_for_send` builds the dict via
  `device_panel(device)` instead of hand-rolling, so native dims
  ride through.
- esp32_bin renderer packs at firmware-native dims, not the
  calibration choice.
- `ha_camera` unwraps `items[0]` from the server-side wrapper;
  full-bleed mode added.
- `weather_now` hero icon resizes via `cqmin` + container query so
  it doesn't clip text at high zoom levels.
- `sky_moon` row layout at medium widths + hard-coded moon colours
  so dark themes don't desaturate the disc.
- Chart self-referencing `--font-family` variable broke cell inline
  styles; fixed.

### Docs

- README + wiki refreshed against current state (widget count,
  theme tally, palette token count, removed-feature scrub).
- `dev/writing-a-plugin.md` refreshed for Spectra, drops the dead
  `--c-*` / `--theme-*` / variant-cell-option doctrine; replaces
  with the semantic-token list, the `data-style` axis, and the
  seven body archetypes.
- New `NOTICES.md` crediting paperlesspaper/epdoptimize for the
  calibration palette data (Apache 2.0).

### Quality

- **779 tests** passing (pytest), up from ~600.
- `mypy --strict` module list extended to include `themes_routes`.
- New guard test ensures the Spectra CSS `[data-theme="..."]` blocks
  and the Python theme registry never drift.

### Removed (since v0.16.26)

- Pre-v0.16.27 widget `--c-bg` / `--c-fg` / `--c-accent` /
  `--c-data-*` / `--c-ok/warn/danger` token cascade.
  The Spectra rebuild paints widgets from `--bg`, `--surface`,
  `--text-primary`, `--accent-1..6`, etc. directly. `--c-zoom`
  survived as the cell-content-zoom variable.
- `variant` cell option from widget manifests. The orthogonal
  `data-theme` × `data-style` axes mean one widget composes with
  every (theme, style) pair instead of shipping N visual directions.
- `plugins/themes_core/` plugin, themes now live in
  `static/style/spectra-*.css` + a Python registry, not the plugin
  tree.
- `scripts/capture_widget_variants.py`, the per-widget variant
  composite generator. `scripts/capture_widget_shots.py` still
  refreshes the gallery hero shot; cross-theme / cross-style
  comparison lives at `/_test/matrix`.

## [0.16.10], 2026-06-04

### Fixed

- **The "bare install" upgrade hint in Settings → System no longer
  suggests `pip install --upgrade tesserae`**, Tesserae isn't on
  PyPI yet, so that command does nothing useful. Replaced with the
  canonical install path (the `install.sh` curl one-liner, or a
  manual `git clone` + `pip install -e ".[dev]"`) and a note that
  the in-app pull-and-restart flow specifically needs a git
  checkout as the source dir. Canonical installs (which keep `.git`
  via install.sh's `git clone` + editable pip install) are
  unchanged, they still see the full Check / Apply / Rollback UI.

## [0.16.9], 2026-06-04

### Fixed

- **Version-metadata hotfix for v0.16.8.** A parallel-edit race in the
  v0.16.8 commit landed without bumping `pyproject.toml` or
  `plugins/ha_sensor/plugin.json`, so the v0.16.8 tag points at a
  commit where the on-disk files still say `0.16.7` / `0.3.0`. This
  release bumps both to the correct values; functionally identical
  to v0.16.8.

## [0.16.8], 2026-06-04

### Added

- **Per-entity name + icon overrides on `ha_sensor` and `ha_entities`.**
  New `overrides` textarea on each widget's cell-options form lets you
  rename and reicon any entity in the picker without renaming it in
  Home Assistant. Format is one entity per line:
  `entity_id | name | icon`, either name or icon can be left empty
  to keep the auto value (HA's friendly_name and the device-class /
  domain icon respectively). Icon is a Phosphor name (see
  phosphoricons.com) without the `ph-` prefix. Lines starting with
  `#` are comments.

  Example:
  ```
  sensor.living_room_temperature | Living Room | thermometer-simple
  sensor.bedroom_temperature | Bedroom |
  sensor.solar_power | | sun
  ```

  Both widget plugins bumped to 0.4.0 to reflect the new manifest
  field. Existing saved dashboards continue to work, the default is
  empty and falls through to auto.

## [0.16.7], 2026-06-04

### Fixed

- **Settings → System no longer flashes "Update state unavailable:
  not a git repository" on installs without a `.git` directory.** The
  in-app updater shells out to `git` for state / check / apply /
  rollback, which works for `git clone`-based installs but fails for
  pip wheels, unpacked release tarballs, or any other install method
  that doesn't carry git history. New `Updater.has_git_repo()` lets
  the Settings → System controller route those installs through the
  same GitHub release-API view it already used for Docker installs;
  the template gained a third-arm message ("upgrade via your install
  method, `pip install --upgrade tesserae` for venv installs,
  re-download the tarball otherwise") for the bare case. Docker
  installs and git checkouts are unchanged.

## [0.16.6], 2026-06-04

### Fixed

- **Pushing from the dashboards list or the editor no longer yanks
  you to Send → History.** The send_page endpoint was hard-redirecting
  to `/send?tab=history` regardless of where the user came from, which
  was helpful when initiating a push from the Send page itself but
  jarring when the user was triaging dashboards or actively editing a
  page. Forms on `/pages` and `/pages/<id>` now post a hidden
  `return_to` field (`dashboards` / `editor`) the route honours via a
  safelist, the Send-page Saved tab and any other caller without a
  `return_to` keep the legacy redirect-to-History behaviour. Flash
  message also shortened when the user isn't about to see the History
  tab (no point telling them to watch a tab they're not on).

## [0.16.5], 2026-06-04

### Fixed

- **Layout editor: every cell is now resizable, even when its
  neighbours don't share a full edge.** The previous shared-edge
  detection required exact y-range / x-range alignment across the
  full edge length, so as soon as one row's cells were resized to
  a different width than the row above or below, the cells with
  the misaligned edge lost their resize handles entirely. Replaced
  `findSharedEdges` with per-cell edge handles that detect aligned
  neighbours at pointerdown via exact perpendicular-range matching
 , aligned grid cases still resize the matched pair together
  (e.g. dragging a column edge that spans one row affects both
  cells in that row), but cells whose neighbours don't line up
  resize independently into the void (gap or overlap allowed).
  Dedup by edge-position key so shared edges aren't double-rendered
  in the aligned case.

## [0.16.4], 2026-06-04

### Fixed

- **Layout editor resize handles work per-row instead of per-column.**
  In a multi-row layout where a vertical edge spanned multiple rows
  (e.g. 2×2 grid), dragging that edge resized the cells in **every**
  row, not just the row the user clicked in. The fix: at pointerdown,
  filter `edge.left` / `edge.right` (or `above` / `below`) to only
  the cells whose y-range (or x-range) contains the pointer. Other
  rows stay put. After the drag the layout has a per-row column
  boundary; `findSharedEdges` detects each as a separate
  independently-draggable handle on the next render. The user can
  realign rows by dragging each independently.
- **`weather_now` no longer clips the sun row at narrow cell heights.**
  The grid was `auto 1fr auto auto` (header, hero, stats, sun); on
  wide-but-short cells (e.g. 1200×420) the auto rows + minimum hero
  exceeded the cell height and `overflow: hidden` clipped the bottom.
  Added a container query (`max-height: 420px`) that drops the sun
  row in that case, the size class was deciding on the longer side
  so a 1200×420 cell stayed `lg` and kept the sun row even when there
  wasn't room.

  Also explains why the bug was invisible in the editor preview: the
  preview iframe is `transform: scale(...)`'d down to fit the editor
  column (often ~0.4×), so 25 px of clipping at panel-native renders
  as a ~10 px sliver that reads as "the next section is just below
  the fold." The renderer screenshots at panel-native and the clip
  is obvious. Same auto-row-stack pattern lives in 9 other widgets;
  the same container-query fix can be applied per-widget if the bug
  reappears there too.

## [0.16.3], 2026-06-04

### Fixed

- **Dashboard editor preview iframe now auto-resets every 4 hours.**
  The composer iframe is mounted once when the editor opens, then
  runs forever, widget setInterval timers (clock, F1 countdown,
  public-transport refresh) accumulate small allocations every
  minute, and the webpage widget's auto-refresh swaps a foreign
  document in repeatedly. Over an overnight idle session those
  compound into multi-GB tab memory (saw 6.5 GB in the wild). A
  hard reset every 4 hours discards all accumulated state, the
  user sees nothing more than the same brief opacity fade as a
  normal save-driven reload.
- **`/renders/<digest>.png?w=<width>` thumbnail endpoint** with disk
  caching. Each row in the Events / Send-history feed previously
  loaded the full panel-sized PNG (1600×1200) into a `<img>`, which
  Chromium decoded to ~7.7 MB per element. With many push events
  retained in the bitmap cache, this also contributed to leaving
  admin tabs in the GB range. Templates + send.js + events.js now
  request the `?w=240` cached variant (~0.4 MB decoded per image),
  add `loading="lazy"` + `decoding="async"` + explicit width/height
  to defer off-screen decode.
- **Events page default row limit** dropped from 200 to 100 so an
  initial page load doesn't pre-load 200 thumbnails (even at the
  reduced size).

### Added

- **`octoprint_status` widget**, live 3D-print monitor for an
  OctoPrint instance. Four canonical directions (r1 Refined, g2
  Geometric, s3 Swiss, d4 Data) pull printer state, job progress
  with ETA, and hotend/bed temperatures via OctoPrint's REST API.
  Includes a sample fixture for the dev gallery.

## [0.16.2], 2026-06-04

### Changed

- **Variant-picker label renamed "Direction" / "Layout" → "Style"**
  across 29 widget manifests. Same picker, friendlier name.
- **Picture widget caption strips no longer invert on dark themes.**
  `picture_apod`, `picture_apple_album`, `picture_gallery`, and
  `picture_unsplash` painted their Bauhaus caption strip with
  `var(--c-text)` background + `var(--c-bg)` foreground, which
  flipped to "dark on dark" the moment a dark theme was active.
  Swapped to the pinned `--wb-bar-bg` / `--wb-bar-fg` tokens
  (same fix as v0.14.3's github bars). Also added a `--theme-font`
  cascade at `:host` for the three widgets that were missing it,
  and replaced `picture_gallery`'s hard-coded `ui-monospace`
  filename font with `var(--theme-font-mono)`.
- **Calendar family links `widget-bauhaus.css`** in every `client.js`
  render path (calendar_day, calendar_month, calendar_week). Divider
  lines (`.d3-rule`, `.d5-rule`, `.w3-rule`, `.w5-rule`, `.m3-rule`,
  plus the `.m2-weekhead` / `.m2-grid` grid-gap backgrounds) now
  paint from `--c-line` instead of `--c-text` so dividers stay quiet
  rather than asserting as primary text. Fixed the
  `.w6-card-head` body inversion. All 6 variants per calendar widget
  retained.
- **`spotify_now_playing` dropdown label** "Layout" → "Style"
  (the five variant ids `split/cover/minimal/vinyl/stack` retained
  as a per-widget layout picker, see rulebook).

### Fixed

- **`ha_history` trend colour is now categorical, not status.** A
  rising temperature isn't a hazard and a dropping battery isn't
  "good"; the previous `--c-danger` / `--c-ok` mapping read as an
  alarm on themes where danger was loud red. Swapped to
  `--c-data-3` / `--c-data-2` so the trend reads as direction, not
  judgement.
- **`sky_aurora` spark bars now actually paint per-Kp colour.** The
  forecast bars rendered with `class="wb-bar"`, which (a) inherited
  the title-bar dark styling from `widget-bauhaus.css` and (b) made
  the colour rules in `client.css` (`.ar-bar.kp-quiet` etc.) match
  no elements. Renamed the class to `.ar-spark-bar`, fixed the CSS
  selectors to match, and added the missing flex/min-width baseline
  styling. Visible bug; all spark bars previously rendered
  identical dark grey instead of categorical Kp colours.

### Documentation

- **Rulebook ([`docs/widget-design-system.md`](https://github.com/dmellok/tesserae/blob/main/docs/widget-design-system.md))**
  extended with grandfathered variant-id patterns: widget-keyed
  4-variant prefixes (the github family's `re1-re4`, `ci1-ci4`,
  `a1-a4`, `pr1-pr4`, `co1-co4`) are accepted as canonical-pattern
  variants; per-widget layout pickers (spotify_now_playing's
  `split/cover/minimal/vinyl/stack`) are accepted when variants
  describe layout shapes rather than design directions.
- **Audit notes** (`notes/widget-audit.md`, gitignored) updated with
  remediation status and the user-preference overrides applied
  during the v0.16.2 pass.

## [0.16.1], 2026-06-03

### Added

- **`docs/widget-design-system.md`**, the cross-widget rulebook.
  Codifies variant naming (`r1/g2/s3/d4` canonical), title-bar
  discipline (`--wb-bar-h` mandatory for refined bars), font cascade
  (`--theme-font` wins), colour discipline (semantic vs categorical
  vs decorative), CSS class naming, and when to link
  `widget-bauhaus.css` / `widget-bauhaus-wx.css`. Sits alongside
  `widgets.md` (single-widget contract) and `widget-design-brief.md`
  (per-widget template) as the "across all widgets" reference;
  wired into the mkdocs nav.
- Quick-lint checklist at the end so a widget author can score their
  finished widget in 30 seconds against the rulebook.

No widget code changed, the rulebook describes what the best
current widgets already do. A separate audit (gitignored at
`notes/widget-audit.md`) catalogues per-widget deviations for a
post-launch cleanup pass.

## [0.16.0], 2026-06-03

### Added

- **`ha_todo` widget**, items from a Home Assistant todo list
  (built-in shopping list, Google Tasks, Microsoft To-Do, CalDAV,
  anything exposed as a `todo.*` entity). Four selectable visual
  directions matching the HA family convention:
  - `r1` Bauhaus Refined, dark header + numbered list with due dates
  - `g2` Bauhaus Geometric, colour-block tiles with status colour band
  - `s3` Swiss / International, hairline header + tabular rows
  - `d4` Data forward, big stat block (X open / Y done) + compact list
  Cell options: `entity_id` (picker filtered to `todo.*` entities),
  `title`, `max_items` (1–20), `include_completed`. Due-date tone
  reflects state, OVERDUE renders danger, TODAY warn, future muted.
  Total widget count: **57**.
- **`ha_core.call_service_with_response()`**, POST helper for HA's
  service calls that need a payload back (HA 2024.5+ `return_response`).
  General-purpose, not just todo: any service that supports
  `return_response` (e.g. weather forecasts, conversation agent
  responses) can now be called from widgets.

## [0.15.1], 2026-06-03

### Fixed

- **Page editor's Push button can't fire an unbound push anymore.**
  When devices are registered but none are bound to the current
  dashboard, the underlying ``send_page`` endpoint would render at
  the virtual-panel size and silently miss every device. The button
  is now disabled with a title explaining how to fix it; the device
  checklist auto-saves + reloads on change, so ticking a device
  re-enables Push without an extra save step. Pages with no devices
  registered at all (legacy single-head install) keep the enabled
  button, the virtual-panel fan-out is intentional there.
- **Send page Send buttons mirror the same guard.** Each tab's Send
  button (File / URL / Webpage / Gallery) is disabled until at least
  one target device is ticked, surfaces the missing pick before the
  user clicks instead of after a POST round-trip. The Saved-page tab
  is unaffected since it inherits the picked-page's bindings.
- **Send page validation failure preserves form input.** Posting
  with no device ticked, a missing URL, or invalid viewport dims
  used to redirect to ``/send`` and destroy everything the user had
  typed, paste the URL again, re-pick fit, re-pick gallery file.
  The Send routes now re-render in place via a new
  ``_render_send_with_form`` helper that round-trips the form
  values + the picked device IDs through the template, so a fix is
  one corrected field and one resubmit.

### Added

- **`spotify_queue` widget**, current track + next few items from
  your Spotify queue. Refined Bauhaus shell with the standard
  `--wb-bar-h` header, accent-band lede showing now-playing + album
  art, and a numbered list of upcoming tracks (title / artist /
  duration). Two cell options: `max_items` (1–12, default 6) and
  `show_now_playing` (drop the lede for a queue-only feed).
  Total widget count: **56**.
- **`spotify_core.queue()`** wraps `GET /v1/me/player/queue` with the
  same OAuth + token-refresh dance as `now_playing()`. The endpoint
  is Premium-only, a 403 surfaces as a clear *"Spotify Premium is
  required to read the queue."* error, not a bare HTTP code. No
  re-auth needed: the existing `user-read-playback-state` scope
  already covers the queue endpoint.

## [0.14.4], 2026-06-03

### Fixed

- **news_reddit no longer deadlocks the renderer during a push.** The
  widget's fetch used to submit a `FetchRequest` to the same
  `BrowserPool` that was currently running the screenshot, the
  pool's single worker was busy with the render, so reddit's fetch
  blocked behind its own render until the hydration overall cap
  fired (~12 s). On a dashboard that includes reddit, that ate most
  of the renderer's 15 s `goto` budget, leaving only ~3 s for the
  `load` event + post-load image / font wait, enough margin under
  light load, but the trigger for the intermittent
  ``Page.goto: Timeout 15000ms exceeded`` errors users hit on
  HA-driven pushes (cold widget caches at random hours of the day).

  The fetch path is now context-aware:

  * **Editor / dev gallery** (``ctx["preview"]=True``), urllib
    against ``old.reddit.com`` first (less aggressively filtered
    than ``www.reddit.com``), falling through to the BrowserPool
    only if urllib fails. The pool's Chromium TLS/JA3 fingerprint
    is still available as a backstop when needed.
  * **Push render** (``ctx["preview"]=False``), the pool path is
    skipped entirely; urllib only. If urllib 403s the composer's
    last-good fallback ([app/composer.py:163](app/composer.py#L163))
    serves the prior payload so the cell still renders.

  The urllib path also gained a fuller browser-like header set
  (Accept-Language, Sec-Fetch-*, ``Cookie: over18=1``) so the
  Reddit bot filter accepts it more often.

## [0.14.3], 2026-06-03

### Added

- **Renderer retries `Page.goto` on transient Playwright
  `TimeoutError`.** ``RenderRequest.max_attempts`` (default 3)
  controls how many fresh-context attempts each render gets. Each
  retry tears down the half-loaded page + context so the next try
  starts clean. Only timeouts retry, other Playwright errors
  (invalid URL, browser-side crash, frame detached) surface
  immediately so we don't burn the deadline on something that
  won't recover. The browser pool's outer deadline scales with
  ``max_attempts`` so the worst-case 3×15s retry fits. The
  intermittent failure mode this fixes: HA-driven pushes that
  surfaced as ``Page.goto: Timeout 15000ms exceeded`` under no
  obvious cause, usually a brief loopback contention or a
  background-thread GC pause that ate the navigation window.

### Fixed

- **github widget title bars now match every other refined widget.**
  The five github widgets (`github_repo`, `github_actions`,
  `github_activity`, `github_contributions`, `github_pr_queue`) had
  hard-coded `clamp(...)` bar dimensions and didn't link
  `widget-bauhaus.css`, so their bars shrank with cell size instead
  of pinning to the shared `--wb-bar-h` / `--wb-bar-px` /
  `--wb-bar-fs` tokens. Each widget now `<link>`s
  `widget-bauhaus.css` and the `.gh-dark` / `.re1-dark` selectors
  read from the shared `--wb-bar-*` vars, so every github bar lands
  at the same physical pixel height as reddit / HA / weather bars
  across every zoom level. Background + colour also flipped to
  `--wb-bar-bg` / `--wb-bar-fg` so dark themes don't render the
  bar as "dark on dark".

## [0.14.2], 2026-06-03

### Changed

- **Telemetry copy: drop the finger-wag.** `docs/privacy.md` had a
  bolded "You control whether to send; you don't control where it
  goes." line right after the explanation of why the endpoint is
  hard-coded; the preceding sentence already makes that point, so
  the restatement read as a lecture without adding substance.
  Removed. Matching line in `app/telemetry.py`'s module docstring
  removed too for consistency.

## [0.14.1], 2026-06-03

### Added

- **Home Assistant integration doc** (`docs/install/home-assistant.md`)
  covering both the HA Add-on / Ingress install path and the MQTT
  auto-discovery surface, plus a webhook-from-HA RESTful-command
  example.
- **Webhook push, backup / export-import, and mDNS docs** added as
  sections in `docs/install/server.md` (the endpoints have been in
  the code for releases but were not user-documented).
- **Theme tokens section** in `docs/widgets.md` covers the full
  `--c-*` semantic layer (now 15 tokens with the `info` primitive),
  the decorative `--wx-*` layer (paper / ink / chromatic chips,
  type roles) used by the weather + sky widget family, the
  per-cell `--c-zoom` counter-scaling math, and the
  `--theme-font` / `--theme-font-mono` cascade so widgets respect
  the font picker.
- **`variant` cell-option pattern** documented in both
  `docs/widgets.md` and `docs/dev/writing-a-plugin.md`, the
  convention 28 shipped widgets use to ship multiple visual
  directions (Refined / Geometric / Swiss / Data / etc.) through a
  single dropdown.
- **TRMNL HTTP-pull pipeline** documented across
  `docs/install/clients.md`, `docs/install/devices.md`,
  `docs/install/server.md`, `docs/dev/architecture.md`, and
  `docs/compatibility.md`, pairing flow, the `/api/setup`,
  `/api/display`, `/api/log` endpoints, and the `trmnl_png`
  renderer's dither options.
- **Composition workflow walkthrough** in
  `docs/install/devices.md`, pick a layout preset, assign widgets
  per cell, tune via the per-cell zoom slider, bind devices.

### Changed

- **Documentation counts corrected throughout.** Widget count
  47 → 55, palette tokens 14 → 15, layout presets 17 → 10,
  themes 21 → 31, fonts 15 → 17, Phosphor weights 4 → 6,
  renderers 3 → 4, device kinds 3 → 4. mDNS hostname corrected
  to plain `tesserae.local` (the `tesserae-<id>.local` form is
  ESP32 captive-portal only).
- **Architecture pipeline diagram** in `docs/dev/architecture.md`
  now shows the TRMNL `trmnl_png` renderer + `trmnl_client` device,
  the HTTP-pull transport side-by-side with MQTT, and adds sections
  for the HTTP-pull API, the webhook push endpoint, and HA MQTT
  discovery.
- **Widget design brief** (`docs/widget-design-brief.md`) icon
  manifest flipped from `fill` weights to `bold` (fill was
  contradicting `docs/widgets.md` which calls out fill as the
  Spectra-6-quantises-into-blobs weight). Tone-rules example
  reworked so it uses semantic tokens explicitly (`--c-data-*` for
  decorative, `--c-ok/warn/danger` for genuine status).
- **`scripts/capture_widget_shots.py`** gained a login flow
  matching `scripts/widget_contact_sheet.py` (POST `/login`,
  forward the session cookie into the Playwright context) so
  rerunning the screenshot capture doesn't trip the auth gate.
  Drives via `TESSERAE_PASSWORD` env or `--password`.
- **`scripts/gen_compatibility.py` + `docs/_data/tested.json`**
  taught about the `trmnl_png` renderer and the `tesserae-trmnl-client`
  reference repo so the compatibility table includes the Kindle
  Paperwhite 2 / KOReader row.
- **README + index** copy refreshed: `first-class` framing
  dropped (banned per project voice), "Tesserae is young"
  softened to match the v0.14 feature surface, TRMNL transport
  surfaced alongside Pi/ESP32, four-client landscape made
  explicit.
- **CHANGELOG backfilled** with 0.13.0 / 0.13.1 / 0.13.2 / 0.14.0
  entries and the `[Unreleased]` compare link / version refs that
  had been silently stale since v0.8.x.
- **SECURITY.md** supported-versions table bumped to 0.14.x;
  scope expanded to include the TRMNL client repo and the HA
  Add-on companion repo.
- **55 per-widget screenshots regenerated** against the current
  styling, with the 8 new HA / weather widgets that landed in 0.13 /
  0.14 captured for the first time.

## [0.14.0], 2026-06-03

### Added

- **Two new bundled fonts.** `fonts_core` now ships **Archivo**
  (400 / 700 / 800) and **Space Mono** (400 / 700), bringing the
  total to 17 typefaces. Archivo is the Bauhaus widget family's
  default sans; Space Mono lands as the matched monospace.
- **Image-wait render phase.** The headless renderer now blocks the
  screenshot on every cell's `<img>` finishing its load (5 s cap,
  walks every shadow root). Fixes HA camera snapshots, Spotify album
  art, Unsplash CDN images, and any other widget that fetches via a
  plain `<img src>`, previously the screenshot fired during the
  download and captured a half-loaded / broken-image frame. New
  `images=N.NN` phase appears in the render-timing log.

### Changed

- **wx widget palette flows through theme tokens.** The decorative
  `--wx-paper` / `--wx-ink` / `--wx-paper-2/3` / `--wx-ink-60` /
  `--wx-hair` tokens (used across the weather + sky widget family)
  now resolve from the cell host's `--c-bg` / `--c-text` /
  `--c-text-soft` / `--c-line` so the widget body retints with the
  active theme. The Bauhaus title bar stays pinned dark via the new
  dedicated `--wb-bar-bg` / `--wb-bar-fg` tokens so refined widgets
  don't flip to "light bar on dark body" under dark themes.
- **Decorative `--wx-*` font role tokens lead with the theme font.**
  `--wx-grotesk`, `--wx-black`, `--wx-geo`, `--wx-mono`, `--wx-swiss`
  now reference `var(--theme-font, ...)` first so the user's font
  picker actually wins over the Bauhaus default; the Bauhaus family
  stays as the fallback.
- **HA refined widgets cascade `widget-bauhaus.css` +
  `widget-bauhaus-wx.css`.** `ha_sensor`, `ha_climate`, `ha_history`,
  and `ha_entities` now link both shared stylesheets so the
  `--wb-bar-*` and `--wx-*` tokens resolve consistently with the
  weather widgets, refined title bars across the whole family land
  at the same physical pixel size at every zoom level.

### Fixed

- **Multiselect option click yanked the page to the top.** The hidden
  checkbox inside `.multiselect-opt` was clipped to a 1×1 footprint
  via `clip-path: inset(50%)`, which made the browser's auto-focus
  `scrollIntoView` think the focused element was at the parent
  label's position, clicking an option further down the scrollable
  list bubbled up to the document and scrolled the whole page. Now
  the checkbox is `opacity: 0` and sized to fill the option label
  (which is `position: relative`), so the auto-focus scroll target is
  already in view and the page stays put.

## [0.13.2], 2026-06-03

### Added

- **Refined title bars pinned to physical pixels.** Shared
  `--wb-bar-h` / `--wb-bar-px` / `--wb-bar-fs` / `--wb-bar-icon-sz` /
  `--wb-mark-sz` CSS vars on `:host` in `widget-bauhaus.css`
  counter-scale by `var(--c-zoom, 1)` so every refined title bar
  lands at the same 36 physical pixels at every zoom level -
  consistent across `.wb-bar`, `.wx-header-dark`, and every
  per-widget header in the HA family.
- **Dev-mode data import.** `Settings → System → Data → Import`
  is callable in `--dev` mode, previously it refused; now it
  flashes a "stop and restart manually" hint instead of trying to
  `os.execv` the dev process.

## [0.13.1], 2026-06-03

### Fixed

- **CI mypy failure on `widget_samples.py`.** The `_ha_battery`
  sample builder mixed-typed dict made mypy infer `level` as
  `object`; rebound through a typed `list[tuple[str, int]]` staging
  list and a `copy.deepcopy` result captured in a typed local before
  return.

## [0.13.0], 2026-06-03

### Added

- **Data export / import.** `Settings → System → Data` exports your
  entire Tesserae install (pages, themes, devices, plugin settings,
  secrets) as a single ZIP, and imports a ZIP from another install.
  Every file is validated against the matching JSON Schema before
  writing; Docker / HA Add-on installs restart in place, venv
  installs flash a "stop and restart" hint so nothing is left
  mid-flight.
- **`info` palette primitive + `--c-info` semantic token.** Themes
  can now define an `info` colour for informational status
  (in addition to `ok` / `warn` / `danger`); `--c-info` falls back
  to `--theme-accent` when a theme omits it.
- **Snap-to-grid layout editor.** The "Custom layout" disclosure on
  the page editor gains a snap-to-grid toggle with adjustable cols /
  rows, useful when a preset doesn't quite fit but you don't want
  fractional drag-resize.
- **8 new weather + sky widget variants.** Each of the existing 8
  weather widgets gains 4 visual directions (Refined / Geometric /
  Swiss / Data) selectable via a `variant` cell option, plus a
  brand-new `weather_wind` widget.
- **7 new Home Assistant widgets.** `ha_battery`, `ha_camera`,
  `ha_energy`, `ha_lights`, `ha_locks`, `ha_media`, `ha_zones`. The
  existing 5 HA widgets also get a polish pass (refined Bauhaus
  title bar, decorative-vs-status tone clean-up).
- **Dev widget gallery.** `/_test/widgets` (dev-only) renders every
  widget at every size on one page for a "did anything regress?"
  scan during a polish pass.

### Changed

- **Decorative vs status colour discipline.** Audit pass across every
  bundled widget, every decorative use of `--c-ok` / `--c-warn` /
  `--c-danger` got rerouted through `--c-data-*` (categorical) so the
  status hues are reserved for genuine advisories / hazards / errors
  only. Themes can now retune status colours without warping weather
  / calendar / news colour blocking.
- **Dark Bauhaus title bar on remaining refined HA widgets.** Final
  refined widgets that were still using their own header styling
  switched over to the shared `--wb-bar-*` tokens; every refined
  header in the bundle now reads identically.

## [0.12.14], 2026-06-02

### Fixed

- **`github_repo` widget showed "No commit activity" on active
  repos.** GitHub's `/stats/commit_activity` endpoint is async, the
  first request returns HTTP 202 with an empty body while GitHub
  builds the stats; subsequent requests get the real data. Our
  `request_json` was crashing on the empty body
  (`json.loads("")` → `JSONDecodeError`), the widget caught the
  exception, set `activity = []`, and **cached that empty result for
  10 minutes**, so even after GitHub finished computing, the widget
  kept rendering empty until the cache expired.
  - `github_core.request_json` now raises a dedicated
    `GithubAcceptedError` on 202 responses instead of choking on an
    empty body.
  - `github_repo` catches that error explicitly and **skips writing
    the cache** when stats are still computing, so the next render
    picks up the real data.
  - For already-cached empty results (the ones currently sticking
    around for users hit by this), the widget now ignores a cached
    entry whose `commit_weeks` is empty and refetches, self-heals
    without waiting for the 10-minute TTL.

## [0.12.13], 2026-06-02

### Fixed

- **First push of a dashboard with a slow upstream painted
  "TimeoutError" into the cell; pushing again worked.** Users found
  themselves manually double-pushing because the executor's stragglers
  finished after the timeout and populated the on-disk cache, so the
  second attempt hit cache and rendered fine. The composer now keeps a
  process-lifetime "last-good" cache keyed on
  (plugin_id, options, panel_w, panel_h); when a widget hydration
  errors (or exceeds the 12s overall cap), we fall back to the most
  recent successful result for the same key instead of rendering an
  error state. Net effect: a transient upstream blip shows
  stale-but-real data, not red error text. Cleared on process restart
  (a fresh install has no fallback to serve anyway).

## [0.12.12], 2026-06-02

### Fixed

- **Renders still capped at 73s even after v0.12.11's hydration fix.**
  The per-phase log surfaced the real culprit: a 57s `evaluate` phase
  every time. Root cause was `page.goto(wait_until="networkidle")`.
  Widget client.js imports, font fetches, and the Phosphor icon CSS
  keep the network busy long after the page is visually ready, so
  `networkidle` timed out on every render. When that timed out,
  Playwright aborted the navigation, putting the page in a
  half-aborted state where the next `page.evaluate` stalled for
  ~60s waiting for stability. Two changes to fix:
  * `page.goto` now waits for `load` (deterministic, fast).
  * `composer.js` sets `window.__tesseraeComposed = true` after every
    cell mount-promise resolves; the renderer polls for that flag via
    `page.wait_for_function`, which is a precise "ready to screenshot"
    signal rather than the squishy `networkidle`.
  Per-phase log now includes a `compose=` field separate from `goto=`
  so a stuck-widget mount can be diagnosed independently from a slow
  page-load.

## [0.12.11], 2026-06-02

### Fixed

- **Hydration timeouts (45s overall / 35s per widget) blew past the
  renderer's 15s `page.goto` budget.** Caught by the per-phase render
  log added in v0.12.8: a Weather dashboard push showed
  `goto=15.02s evaluate=57.44s screenshot=0.19s`, total 73s, with a
  matching `page hydration overall timeout (45.0s)` warning. The
  server was still computing the response when Playwright timed out,
  so the browser saw a delayed/aborted navigation and the `evaluate`
  call stalled waiting for the page to stabilise. Hydration is now
  capped at 12s overall / 10s per widget so the compose endpoint
  always responds inside `goto`'s 15s window. Widgets whose upstream
  doesn't respond in 10s render an error state for that cycle rather
  than holding up the dashboard.

- **`weather_pollen_count` blocked hydration with a slow Melbourne
  scrape.** The fallback HTML scrape of `melbournepollen.com.au`
  still used bare `urllib.request.urlopen` with the 15s widget-level
  timeout, on top of the open-meteo fetch, worst case 46s for that
  one widget alone, blowing the new hydration cap. Switched to a new
  `app.plugin_http.fetch_text` helper (5s timeout, no retries -
  it's an explicitly-best-effort fallback).

### Added

- **`fetch_text()` in `app.plugin_http`**, sibling to `fetch_json`
  for non-JSON endpoints (HTML scrapes, RSS feeds). Same retry +
  backoff machinery; defaults to zero retries since text-scrape
  fallbacks shouldn't be retried into hydration timeouts.

## [0.12.10], 2026-06-02

### Fixed

- **F1 widgets surface `TimeoutError` when the Jolpica F1 API blips.**
  The four F1 plugins (`f1_next`, `f1_last_race`, `f1_weekend`,
  `f1_standings_drivers`) still used bare `urllib.request.urlopen`
  with a 10s timeout, same fragile pattern v0.12.5 fixed in the
  weather widgets but never propagated to F1. Switched them to
  `app.plugin_http.fetch_json` (15s timeout, one retry, 1s backoff),
  so a transient SSL handshake hang on jolpi.ca no longer paints
  "TimeoutError: the read operation timed out" into the cell.

## [0.12.9], 2026-06-02

### Fixed

- **Widget data fetches now run in parallel per page render.** The
  hydration loop in `app/composer.py` fetched each cell's `server.py`
  fetch() serially: six widgets each waiting 15s on a slow upstream
  meant 90s of compose-endpoint time, which blew past Playwright's
  navigation budget and surfaced as a blank PNG or a "TimeoutError:
  the read operation timed out" rendered into the cell. Hydration now
  uses a `ThreadPoolExecutor` (max 8 workers), so a dashboard's
  render time is bound by the slowest single widget rather than
  their sum. Two safety caps: per-widget 35s, overall 45s, beyond
  those an unfinished cell gets a synthetic `{"error": …}` so the
  widget template renders a clean failure state rather than blocking
  the whole page.

## [0.12.8], 2026-06-02

### Fixed

- **Renders capped at exactly the 75s BrowserPool deadline, surfacing
  as a bare "render:" error in History.** `page.set_default_timeout`
  governs Playwright actions (evaluate, click) but not navigation, so
  `page.goto` was using the upstream 30s default rather than our 15s.
  `goto + fallback + evaluate + screenshot` could sum to ~75s and
  race the pool's outer 75s future deadline; the resulting
  `concurrent.futures.TimeoutError` stringifies to an empty string,
  which is why the History row showed `render:` with no detail.
  Renderer now sets `set_default_navigation_timeout` too, and
  `push.py` falls back to the exception type name when the message is
  empty so future failures self-explain.

### Added

- **Per-phase render timing in the add-on log.** Each headless render
  now logs `goto / evaluate / screenshot` durations next to the
  composed URL. "Why is this push taking 70s?" investigations get a
  concrete breadcrumb instead of guesswork.

## [0.12.7], 2026-06-02

### Fixed

- **Pushes failing with "Execution context was destroyed" after ~75s.**
  Under HA, every dashboard push from the user's edge install fell
  through with that Playwright error, suggesting the persistent
  BrowserPool's Chromium had got into a state where `new_context()`
  produced pages whose evaluate hooks raced with an unfinished
  navigation. Two defenses: the `_FONT_WAIT_JS` evaluate is now
  best-effort (a missed font wait beats a whole-render fail), and the
  BrowserPool's exception handler now treats "Execution context was
  destroyed" / "target … has been closed" the same as a dead browser
 , drops the handle so the next render relaunches Chromium cleanly,
  even when `is_connected()` still returns True.

## [0.12.6], 2026-06-02

### Added

- **Brand mark as a real asset.** The in-nav brand mark was previously
  only available as a pure-CSS shape, so the browser tab showed a
  generic icon and the HA add-on store had no graphic. New
  `static/brand/icon.svg` bakes the shape into a vector that the
  browser tab and HA add-on share. A small `scripts/render_brand.py`
  rasterises the SVG into PNGs (128 for the HA sidebar, 32 for the
  Safari favicon fallback, 512 for future social cards). The HA stable
  + edge add-on directories now ship the 128 PNG as `icon.png`.

## [0.12.5], 2026-06-02

### Fixed

- **Chart.js 404'd under HA Ingress on the four chart-using widgets.**
  `finance_currency`, `finance_crypto`, `finance_stock`, and
  `weather_hourly` all loaded Chart.js by creating a `<script>` and
  setting `src = "/static/vendor/chart.umd.min.js"`, and because
  that script lives in `document.head`, not in the widget's shadow
  root, v0.12.4's shadow-DOM URL sweep didn't touch it. Patched the
  four widgets to prepend `window.TESSERAE_URL_PREFIX` themselves.
- **Weather widgets occasionally flashed an SSL handshake / URL
  timeout error.** A flaky LAN or a slow upstream (Open-Meteo / pollen
  sites) caused a single hung request to fail the whole render. New
  `app/plugin_http.py` adds a tiny `fetch_json` helper with one retry
  + 1s backoff and a bumped 15s timeout; the five weather plugins
  (`now`, `forecast`, `hourly`, `air_quality`, `pollen_count`) use it
  instead of bare `urllib.request.urlopen`. A blip on the first try
  no longer surfaces an error in the cell.

## [0.12.4], 2026-06-02

### Fixed

- **Widgets rendered with no CSS, fonts, or icons inside the HA Ingress
  composer / preview.** Every widget's `client.js` set
  `shadow.innerHTML` with root-relative `<link href="/static/…">` and
  `<link href="/plugins/…">`. Inside the ingress iframe those resolved
  to the HA host root and 404'd, so each shadow DOM rendered with
  default user-agent styles. `composer.js` now walks the freshly-
  rendered shadow root and prepends `TESSERAE_URL_PREFIX` to root-
  relative `href` / `src` attributes, one place, catches all 51
  widget files without touching them.
- **Inter / JetBrains Mono fonts missing under HA Ingress.** The
  `@font-face` rules in `static/style/base.css` used absolute
  `url("/plugins/fonts_core/…")` which resolved against the HA host
  root. Switched to CSS-relative `url("../../plugins/fonts_core/…")` so
  the browser resolves them against `base.css`'s own URL, works with
  or without an ingress prefix without runtime substitution.
- **Tesserae nav logo took users back to the HA dashboard.** The brand
  link in `_base.html` was a hardcoded `href="/"` which inside the
  ingress iframe meant the HA host root, not Tesserae's index. Now
  uses `url_for('index')`.
- **Onboarding "Edit page" + plugin admin links + Events thumbnails +
  device-discovery poller** all used absolute paths that bypassed the
  ingress prefix. All switched to `url_for(...)` or
  `request.script_root + …`.
- **Headless renderer hit the wrong loopback port on Edge.** The Edge
  add-on publishes its API on host port 8766, container 8765, but
  `to_loopback_url` preserved the URL's port (8766), so it tried
  `http://127.0.0.1:8766` inside the container where nothing was
  listening. A new `TESSERAE_BIND_PORT` env var (set in both add-on
  configs) tells the renderer which internal port the server actually
  binds, independent of the host-side mapping.

## [0.12.3], 2026-06-02

### Added

- **HA Add-on Configuration tab now actually wires through.** The
  `log_level`, `mqtt_host`, `mqtt_port`, `mqtt_username`, and
  `mqtt_password` options were declared in `config.yaml` but went
  nowhere, users had to set the same values twice (once in HA's
  form, once in Tesserae's Settings → MQTT broker page). A new
  `app/ha_options.py` reads `/data/options.json` on every container
  start (HA mode only) and applies log level to the root logger and
  MQTT details to the broker settings section. HA Configuration is
  now the canonical source for these fields; Tesserae's Settings →
  MQTT broker card hides `host`/`port`/`username`/`password` under HA
  and shows a "managed in the add-on's Configuration tab" blurb. The
  card keeps `keepalive` and `client_id` editable since they have no
  HA equivalent. Telemetry / mDNS / HA discovery / browser warmup
  intentionally stay Tesserae-side, those are user-tunable consent
  or runtime knobs, not connection config.

## [0.12.2], 2026-06-02

### Fixed

- **Panel URLs under HA Ingress pointed at HA's port (8123), not
  Tesserae's.** `_capture_http_port` read `request.host` on every
  request and stashed the port. Inside Ingress that host is the HA
  frontend (`homeassistant.local:8123`), so every MQTT push payload
  ended up with `http://<lan-ip>:8123/renders/…`, devices 404'd at
  HA. The before-request hook now short-circuits when
  `X-Ingress-Path` is present; under Ingress we fall back to
  `TESSERAE_HTTP_PORT` / the default 8765 instead, matching the
  add-on's actual host port mapping.

## [0.12.1], 2026-06-02

### Fixed

- **"Importing a module script failed." on every widget under HA
  Ingress.** `composer.js` did `import("/plugins/<id>/client.js")`
  with an absolute path, inside the ingress iframe that resolves to
  the HA host root and 404s with an HTML response, which the browser
  reports as a module-import failure. The compose page now exposes
  `window.TESSERAE_URL_PREFIX` the same way `_base.html` does, and the
  dynamic import prepends it. The three F1 widgets (`f1_next`,
  `f1_last_race`, `f1_weekend`) that absolute-imported the shared
  `f1_core/static/circuits.js` helper now use a relative import so
  they're prefix-independent.

## [0.12.0], 2026-06-02

### Breaking

- **Default HTTP port is now 8765** (was 8000). Picked to dodge the
  pile of dev tooling that owns 8000 (Django runserver, `python -m
  http.server`, generic admin UIs) so a fresh `docker compose up`
  doesn't immediately collide with whatever else the user has. Affects
  every entry point, `tesserae --port`, the Dockerfile EXPOSE, the
  compose example, the install.sh / install.ps1 prompt default, mDNS,
  and `TESSERAE_HTTP_PORT`'s fallback. ESP32 / Pi firmware images with
  `:8000` baked into the saved base URL will need their Tesserae URL
  re-pointed; the panel listeners pick up the new URL on the next push
  once you update it. The HA Add-on (stable) now exposes host `8765`;
  the Edge add-on uses host `8766` so the two can run side-by-side and
  both stay LAN-reachable.

### Changed

- **Built-in broker disabled under the HA Add-on.** Home Assistant's
  bundled Mosquitto add-on already owns port 1883 on the host, so
  running Tesserae's embedded amqtt alongside it creates two brokers
  on the same address, devices end up talking to whichever one their
  client happens to hit, and nothing reliable works. Inside an HA
  install the Settings → MQTT broker card hides every `embedded_*`
  field (toggle included) and the onboarding wizard skips the
  "use built-in" path and pre-fills Host with `core-mosquitto`. The
  transport-rebuild path treats `embedded_enabled` as false under HA
  regardless of saved settings, so a legacy config import can't
  re-enable it.

### Fixed

- **Events page indicator stuck on "offline" inside HA Ingress.** The
  Events page (and the live History tab on Send) opened `EventSource`
  against a root-relative `/events/stream` path. Inside the Ingress
  iframe that resolves to the Home Assistant host root, not the add-on,
  so the connection failed immediately and the indicator flipped to
  offline even though SSE / MQTT were both fine. The same bug affected
  the icon picker's Phosphor manifest fetch and the editor preview
  fetch. The base template now exposes `window.TESSERAE_URL_PREFIX`
  (Flask's `request.script_root`, the ingress prefix the WSGI
  middleware extracted from `X-Ingress-Path`, empty otherwise), and the
  four affected JS sites prepend it.
- **Noisy `ha_discovery` tracebacks during broker reconnect / shutdown.**
  When the MQTT transport is explicitly disconnected (settings swap,
  process exit), the discovery publishers used to fire a full
  `RuntimeError` stack trace per retained config, dozens of them per
  shutdown. Discovery configs are already retained on the broker and
  get re-published when discovery next starts, so we now skip publishes
  silently when the transport is disconnected and log any in-flight
  disconnect race at `debug` instead of `warning` with `exc_info`.
  Other publish failures still log loudly with a traceback.

## [0.11.17], 2026-06-02

### Fixed

- **MQTT client-id collisions between two installs sharing one
  broker.** A bare-metal Tesserae and the HA Add-on Tesserae both
  pointing at HA's bundled `core-mosquitto` saw "MQTT disconnected:
  Unspecified error" every couple of seconds, the broker evicted
  whichever client connected second the moment its duplicate
  client-id was already in use. The default client-id resolver now
  appends a 6-character hex suffix persisted to
  `data/core/.mqtt_client_id_suffix`. Random so it doesn't
  coordinate between hosts; persistent so MQTT subscriptions stay
  attached to a stable id across restarts; one-shot so existing
  installs don't get a new id and lose their retained-message
  bindings on upgrade, they'll generate one on first restart and
  hold it from then on. Settings → Broker → MQTT client id still
  overrides everything.

## [0.11.16], 2026-06-02

### Fixed

- **HA Add-on: panel base_url pointed at the docker bridge IP.**
  Tesserae's `detect_local_ip()` used a UDP-getsockname trick to find
  the host's outbound IPv4. Under `host_network: false` (which all HA
  Add-ons use) the trick returns the docker bridge address
  (172.x.x.x), which no LAN client can reach. Panels listening for
  MQTT push frames or polling the TRMNL BYOS endpoint at that URL
  would silently fail. Resolution order is now:
  1. `TESSERAE_HOST_IP` env var (unchanged, always wins).
  2. HA Supervisor's `/network/info` API, picks the primary
     interface's IPv4 address. Only reachable when
     `hassio_api: true` is set on the add-on (both add-on definitions
     bump that in the companion repo).
  3. The existing UDP-getsockname trick.

  Result is cached for the process lifetime so we don't hammer the
  Supervisor API on every `detect_local_ip()` call (multiple admin
  routes / page renders use it).

## [0.11.15], 2026-06-02

### Added

- **HA Add-on edge channel.** Every push to `main` now builds and
  publishes a per-commit Docker tag
  `ghcr.io/dmellok/tesserae:<pyproject>-edge.<sha7>` (in addition
  to the existing `:main` and `:latest`). The companion add-on repo
  gained a parallel `tesserae-edge/` add-on definition that tracks
  those tags via the sync-addon workflow, which now has two jobs:
  - `bump-stable`, fires on `release: published`, edits
    `tesserae/config.yaml`.
  - `bump-edge`, fires on `push: branches: [main]`, edits
    `tesserae-edge/config.yaml` to the per-commit edge version.
  HA users see two add-ons in the store. Stable installs the
  released Tesserae; edge installs whatever's on `main` right now.
  Both can be installed in parallel (different `slug:`, different
  persistent `/data` volume); edge intentionally doesn't expose
  port 8000 on the host so it can coexist with stable.

## [0.11.14], 2026-06-02

### Fixed

- **HA Add-on: `PermissionError` on `/data/plugins` on first boot.**
  The Docker entrypoint chowns `/app/data` to `pwuser` so the
  un-privileged worker can write to it after gosu-drops; it didn't
  know about `TESSERAE_DATA_ROOT` (added in 0.11.13), so when HA
  Supervisor mounted `/data` root-owned the gosu-dropped Tesserae
  process EPERM'd on the first `mkdir`. Entrypoint now also chowns
  `TESSERAE_DATA_ROOT` when set and different from `/app/data`.

## [0.11.13], 2026-06-02

### Added

- **`is_homeassistant` flag in telemetry.** Sits alongside `is_docker`
  on both `app.started` and `app.heartbeat`. True when the
  `TESSERAE_HA_INGRESS=1` env var is set (the companion HA Add-on
  exports it via its `config.yaml` `environment:` section). Lets the
  maintainer see the HA-Add-on subset of the installed fleet as it
  grows. No content sent, just a single `true` / `false` deployment
  flag, same shape as `is_docker`.
- **`TESSERAE_DATA_ROOT` env var** to override the data directory. The
  HA Add-on sets this to `/data` so Tesserae's settings, dashboards,
  schedules and event log land on HA Supervisor's per-add-on
  persistent volume, which Supervisor automatically backs up across
  add-on upgrades.

### Fixed

- **HA Ingress 404 on first install.** The URL-prefix middleware
  (added in 0.11.11) was only wrapping the WSGI app when
  `TESSERAE_HA_INGRESS=1` was set, AND the add-on's `image:` field
  was bypassing the custom Dockerfile that set that env var. Net
  result: Tesserae's `/` redirected to `/setup` with a bare
  `Location: /setup`, the iframe followed it to HA's root, and HA
  itself 404'd. The middleware now always wraps (it's a no-op when
  there's no `X-Ingress-Path` header), and the add-on's `config.yaml`
  uses `environment:` to set the env var directly. The auth-gate
  bypass still requires both env var + header, that part stays
  belt-and-braces.

## [0.11.12], 2026-06-02

### Added

- **CI workflow that syncs the companion HA Add-on repo on each
  published Release.** Watches `release: published`; on each new
  Release, bumps `tesserae/config.yaml` `version:`, the
  `Dockerfile`'s `ghcr.io/dmellok/tesserae:<tag>` reference, and
  prepends a CHANGELOG entry on
  [dmellok/homeassistant-tesserae-addon](https://github.com/dmellok/homeassistant-tesserae-addon).
  Requires an `ADDON_REPO_PAT` secret on this repo (fine-grained PAT
  with Contents: read+write on the add-on repo only). Patch tags
  without a matching GitHub Release do NOT churn the add-on.

## [0.11.11], 2026-06-02

### Added

- **Home Assistant Add-on / Ingress support.** Opt in by setting the
  `TESSERAE_HA_INGRESS=1` env var (the companion HA Add-on does this
  automatically). When set:
  - A WSGI middleware reads the `X-Ingress-Path` header HA Supervisor
    sets on every proxied request and patches `SCRIPT_NAME` so
    Flask's `url_for` emits URLs that resolve inside the iframe.
  - The auth gate bypasses Tesserae's own password gate when the
    `X-Ingress-Path` header is present (HA Supervisor authenticated
    upstream).
  - Both checks are belt-and-braces: env var alone won't bypass auth
    without the header, header alone won't bypass without the env
    var. A stray header from a misconfigured reverse proxy on a
    non-ingress install can't sneak past.

  The companion add-on lives at
  [dmellok/homeassistant-tesserae-addon](https://github.com/dmellok/homeassistant-tesserae-addon).

## [0.11.10], 2026-06-02

### Added

- **App footer with version + GitHub link.** Subtle dotted-underline
  link in the bottom margin of every page, deep-linked to the
  matching release tag on GitHub (`/releases/tag/vX.Y.Z`). Reads as
  "Tesserae v0.11.10". 60% opacity by default, brightens to 95% on
  hover. Pure cosmetic; no layout impact above the fold.

## [0.11.9], 2026-06-02

### Added

- **Update + Rollback show a modal with a throbber and stage hint.**
  Clicking "Update & restart" used to freeze the tab for 30+ seconds
  during git fetch + pip install and then show a browser connection
  error during the os.execv restart. Now a modal pops up with a
  spinner ("Pulling the new revision…" → "Installing dependencies if
  needed…" → "Almost there…" → "Restarting…"), polls /healthz until
  the server comes back, and auto-reloads the page. Same flow for
  the Rollback button (which carries the same restart cost). Pure
  client-side wiring; no backend changes.

### Fixed

- **Send page History tab now actually live-updates after a push.**
  v0.11.8 wired the SSE subscription but listened for the wrong
  event name (``event`` / default ``onmessage``), so push events
  came through under the SSE endpoint's actual name (``log``) and
  were silently dropped. Listener corrected to ``log``.

## [0.11.8], 2026-06-02

### Changed

- **Send page's History tab updates live.** v0.11.7 backgrounded the
  push so the browser didn't freeze, but the History tab still
  required a manual reload to see the new row land. Subscribed it to
  the same `/events/stream?type=push` SSE feed the Events tab uses;
  on each push event, the tab refreshes the history list in place
  (debounced 300 ms to collapse multi-target fan-outs into one swap).

## [0.11.7], 2026-06-02

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

## [0.11.6], 2026-06-01

### Fixed

- mypy `--strict` was failing in CI on the v0.11.5 `BrowserPool`
  worker because the queue's union type `Future[bytes] | Future[str]`
  didn't narrow when `request` was narrowed by `isinstance(request,
  FetchRequest)`. Cast the future explicitly on each branch.

## [0.11.5], 2026-06-01

### Changed

- **`news_reddit` widget gets a Chromium-fingerprinted fetch path.**
  Reddit's public RSS feed intermittently blocks plain `urllib`
  requests regardless of User-Agent, the bot-shape filter
  fingerprints on TLS / JA3 / HTTP/2 framing, not just the UA. The
  widget now prefers the warm `BrowserPool`'s `fetch_text` (Chromium's
  real fingerprint) and falls back to `urllib` only when the pool is
  off or the Playwright fetch fails. Each pool fetch uses a fresh
  incognito context so cookies don't accumulate.
- `BrowserPool` gains a generic `fetch_text(FetchRequest)` method
  alongside `render(RenderRequest)` so other widgets hitting flaky
  upstreams can opt in without touching the renderer code.

## [0.11.4], 2026-06-01

### Added

- **Docker self-update awareness.** The Settings → System "Updates"
  card now does the right thing inside the official container: hits
  GitHub's release API (with a `/tags` fallback for repos that don't
  publish Releases yet) to show whether a newer version is out, and
  surfaces a copy-pasteable `docker compose pull && docker compose up
  -d` instead of the git-based "Apply update" button. Result is cached
  for an hour to stay under GitHub's 60/hr anonymous rate limit. Source
  installs keep the existing git-pull / re-exec self-updater.

## [0.11.3], 2026-06-01

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

## [0.11.2], 2026-06-01

### Fixed

- **Telemetry was reporting the wrong version.** The app version sent in
  `appVersion` / `sdkVersion` came from `importlib.metadata.version("tesserae")`,
  which reads frozen wheel metadata, so an `-e .` install kept reporting
  whatever pyproject.toml said at the last `pip install`, even if the
  version got bumped on disk after. Source checkouts now read
  `pyproject.toml` directly; installed wheels still fall back to
  `importlib.metadata`.
- **Events page timestamps are now human-readable** (`Jun 1 14:23:45`,
  local time) on both the server-rendered rows and the live SSE stream.
  The machine-readable ISO timestamp stays in the `<time datetime="…">`
  attribute for accessibility.

## [0.11.1], 2026-06-01

### Changed

- `github_repo`, tightened the four directions to match an updated
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

## [0.11.0], 2026-06-01

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
  `--c-data-3` / `--c-data-4` / `--c-text` / `--c-text-soft` -
  intentionally NOT `--c-ok` / `--c-warn` / `--c-danger`, since the
  GitHub accents code identity, not semantic status.

## [0.10.1], 2026-06-01

### Changed

- `ha_climate`: dropped a dead `transparent` fallback on
  `var(--c-bg)`, the semantic token is always defined on the cell
  host, so the fallback never fired. Cosmetic; no behaviour change.

## [0.10.0], 2026-06-01

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

## [0.9.0], 2026-06-01

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

## [0.8.3], 2026-05-31

### Added

- **Six monochrome themes** for 1-bit panels (Paper, Carbon, Newsprint,
  Halftone, Ash, Graphite). Designed for the Kindle / native TRMNL
  rendering pipeline, Paper / Carbon are flat for sharp text, Newsprint
  / Halftone are halftone-friendly for printed-page texture, Ash /
  Graphite sit between as softer alternatives.
- **`tags` field on themes** to support family grouping. The theme
  picker on the page editor now groups by family in `<optgroup>`s, so
  the six mono themes cluster together, someone setting up a Kindle
  dashboard can spot them without scrolling past 20 colour themes.

## [0.8.2], 2026-05-31

### Fixed

- TRMNL discovery headers are case-insensitive, so KOReader's
  `Png-Width` / `Png-Height` (Title-case) land as `panel_w` / `panel_h`
  in the cache and pre-fill the Register form, previously they only
  matched the lowercase / native-TRMNL spellings and were silently
  dropped.

### Changed

- README updated with a "TRMNL-compatible (HTTP pull)" panels subsection;
  Kindle Paperwhite 2 (jailbroken, KOReader trmnl-display plugin) listed
  as tested.

## [0.8.1], 2026-05-31

### Fixed

- Scheduler skips schedules whose target page was deleted instead of
  letting them fire every tick and log "page not found" to the History
  view. Warns once per session per stale schedule so the operator sees
  something actionable in the log without spam.
- Schedules editor flags stale schedules with a red "page deleted" pill
  + a subtle row tint, so the user can rebind or delete them.

## [0.8.0], 2026-05-31

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

- HA discovery orphan sweep on start, retained discovery configs for
  devices deleted while Tesserae was offline get blanked, so HA stops
  showing ghost device tiles forever.

## [0.7.0], 2026-05-30

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
- **Curated theme set**, 25 named themes across light, dark, and neon
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

## [0.6.0], 2026-05-30

Quiet hours, webhooks, per-device timetable, and a stack of embedded-
broker fixes.

### Added

- **Quiet hours**, suppress automated pushes during a configurable
  window, with a per-device override in Settings → Devices.
- **Webhook push API**, `POST /api/v1/push` for external automation
  (Home Assistant, n8n, etc.).
- **Per-device Timetable card**, read-only view of which schedules
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

## [0.5.0], 2026-05-30

Onboarding polish + recording infrastructure for the docs.

### Added

- Panel-size picker on the onboarding device step.
- Playwright-driven recording scripts for the onboarding + dashboard
  flows (used to generate the docs GIFs).

### Changed

- Telemetry consent copy softened on the onboarding step.

## [0.4.0], 2026-05-30

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
- Windows self-restart no longer hangs, replaces `os.execv` with
  `Popen + os._exit + parent-pid handshake` (0.4.4).
- Every `Path.read_text` / `Path.write_text` pinned to
  `encoding="utf-8"` so Windows doesn't mangle em-dashes (0.4.8).
- Reloader-watcher process no longer double-inits MQTT, scheduler, and
  telemetry in dev mode (0.4.8).

## [0.3.0], 2026-05-30

A System tab (self-update + backup/restore), the `--c-*` semantic theme
token layer used by every widget, an event-log dedup, and the MkDocs
wiki scaffold.

### Added

- **Settings → System**, self-update from a GitHub tag, full data
  backup/restore (zips `data/` minus user-controlled exclusions like
  the picture-gallery cache).
- **MkDocs wiki** under `docs/` with auto-generated widget gallery and
  compatibility tables; deploys to GitHub Pages.
- **mDNS advertiser**, opt-in `tesserae.local` (and
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

- `news_reddit` widget reads the RSS feed, Reddit's `.json` endpoint
  now 403-blocks unauthenticated clients.

## [0.2.0], 2026-05-28

First release aimed at fellow hobbyists: multi-panel support is built
in throughout, the widget catalogue is broad, and a fresh clone of each
reference client works against the server defaults with no manual topic
editing.

### Multi-head devices (headline)

- **Device instances.** Register multiple physical panels in
  Settings → Devices, each with its own id, MQTT topics, and panel size.
  A built-in *kind* is a template; each panel you add is an *instance* of
  a kind. Per-instance add / edit-panel / delete, all hot-reloaded, no
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
  clocks, sky, pictures, todo, and Melbourne public transport, each with
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
 , default id `pi_bin`.
- [tesserae-pi-png-client](https://github.com/dmellok/tesserae-pi-png-client)
 , default id `pi_png`.
- [tesserae-esp32-bin-client](https://github.com/dmellok/tesserae-esp32-bin-client)
 , default id `esp32`, device id set via captive portal.

All three publish discovery hints (`kind`, `panel_w`, `panel_h`,
`fw_version`) so they auto-register on the server.

## [0.1.0], 2026-05-26

Initial milestone build: plugin / renderer / device loaders, composer,
MQTT transport + push pipeline, manifest-driven settings with an auth
gate, scheduler, Send page, generalised event log, and Home Assistant
MQTT discovery.

[Unreleased]: https://github.com/dmellok/tesserae/compare/v0.16.10...HEAD
[0.16.10]: https://github.com/dmellok/tesserae/releases/tag/v0.16.10
[0.16.9]: https://github.com/dmellok/tesserae/releases/tag/v0.16.9
[0.16.8]: https://github.com/dmellok/tesserae/releases/tag/v0.16.8
[0.16.7]: https://github.com/dmellok/tesserae/releases/tag/v0.16.7
[0.16.6]: https://github.com/dmellok/tesserae/releases/tag/v0.16.6
[0.16.5]: https://github.com/dmellok/tesserae/releases/tag/v0.16.5
[0.16.4]: https://github.com/dmellok/tesserae/releases/tag/v0.16.4
[0.16.3]: https://github.com/dmellok/tesserae/releases/tag/v0.16.3
[0.16.2]: https://github.com/dmellok/tesserae/releases/tag/v0.16.2
[0.16.1]: https://github.com/dmellok/tesserae/releases/tag/v0.16.1
[0.16.0]: https://github.com/dmellok/tesserae/releases/tag/v0.16.0
[0.15.1]: https://github.com/dmellok/tesserae/releases/tag/v0.15.1
[0.15.0]: https://github.com/dmellok/tesserae/releases/tag/v0.15.0
[0.14.4]: https://github.com/dmellok/tesserae/releases/tag/v0.14.4
[0.14.3]: https://github.com/dmellok/tesserae/releases/tag/v0.14.3
[0.14.2]: https://github.com/dmellok/tesserae/releases/tag/v0.14.2
[0.14.1]: https://github.com/dmellok/tesserae/releases/tag/v0.14.1
[0.14.0]: https://github.com/dmellok/tesserae/releases/tag/v0.14.0
[0.13.2]: https://github.com/dmellok/tesserae/releases/tag/v0.13.2
[0.13.1]: https://github.com/dmellok/tesserae/releases/tag/v0.13.1
[0.13.0]: https://github.com/dmellok/tesserae/releases/tag/v0.13.0
[0.12.0]: https://github.com/dmellok/tesserae/releases/tag/v0.12.0
[0.11.0]: https://github.com/dmellok/tesserae/releases/tag/v0.11.0
[0.10.0]: https://github.com/dmellok/tesserae/releases/tag/v0.10.0
[0.9.0]: https://github.com/dmellok/tesserae/releases/tag/v0.9.0
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
