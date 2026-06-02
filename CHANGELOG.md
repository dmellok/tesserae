# Changelog

All notable changes to Tesserae are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are
[SemVer](https://semver.org/) (pre-1.0, so minors can carry breaking changes).

## [Unreleased]

— in flight on `main` —

## [0.12.13] — 2026-06-02

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

## [0.12.12] — 2026-06-02

### Fixed

- **Renders still capped at 73s even after v0.12.11's hydration fix.**
  The per-phase log surfaced the real culprit: a 57s `evaluate` phase
  every time. Root cause was `page.goto(wait_until="networkidle")`.
  Widget client.js imports, font fetches, and the Phosphor icon CSS
  keep the network busy long after the page is visually ready, so
  `networkidle` timed out on every render. When that timed out,
  Playwright aborted the navigation — putting the page in a
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

## [0.12.11] — 2026-06-02

### Fixed

- **Hydration timeouts (45s overall / 35s per widget) blew past the
  renderer's 15s `page.goto` budget.** Caught by the per-phase render
  log added in v0.12.8: a Weather dashboard push showed
  `goto=15.02s evaluate=57.44s screenshot=0.19s` — total 73s — with a
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
  timeout, on top of the open-meteo fetch — worst case 46s for that
  one widget alone, blowing the new hydration cap. Switched to a new
  `app.plugin_http.fetch_text` helper (5s timeout, no retries —
  it's an explicitly-best-effort fallback).

### Added

- **`fetch_text()` in `app.plugin_http`** — sibling to `fetch_json`
  for non-JSON endpoints (HTML scrapes, RSS feeds). Same retry +
  backoff machinery; defaults to zero retries since text-scrape
  fallbacks shouldn't be retried into hydration timeouts.

## [0.12.10] — 2026-06-02

### Fixed

- **F1 widgets surface `TimeoutError` when the Jolpica F1 API blips.**
  The four F1 plugins (`f1_next`, `f1_last_race`, `f1_weekend`,
  `f1_standings_drivers`) still used bare `urllib.request.urlopen`
  with a 10s timeout — same fragile pattern v0.12.5 fixed in the
  weather widgets but never propagated to F1. Switched them to
  `app.plugin_http.fetch_json` (15s timeout, one retry, 1s backoff),
  so a transient SSL handshake hang on jolpi.ca no longer paints
  "TimeoutError: the read operation timed out" into the cell.

## [0.12.9] — 2026-06-02

### Fixed

- **Widget data fetches now run in parallel per page render.** The
  hydration loop in `app/composer.py` fetched each cell's `server.py`
  fetch() serially: six widgets each waiting 15s on a slow upstream
  meant 90s of compose-endpoint time, which blew past Playwright's
  navigation budget and surfaced as a blank PNG or a "TimeoutError:
  the read operation timed out" rendered into the cell. Hydration now
  uses a `ThreadPoolExecutor` (max 8 workers), so a dashboard's
  render time is bound by the slowest single widget rather than
  their sum. Two safety caps: per-widget 35s, overall 45s — beyond
  those an unfinished cell gets a synthetic `{"error": …}` so the
  widget template renders a clean failure state rather than blocking
  the whole page.

## [0.12.8] — 2026-06-02

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

## [0.12.7] — 2026-06-02

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
  — drops the handle so the next render relaunches Chromium cleanly,
  even when `is_connected()` still returns True.

## [0.12.6] — 2026-06-02

### Added

- **Brand mark as a real asset.** The in-nav brand mark was previously
  only available as a pure-CSS shape, so the browser tab showed a
  generic icon and the HA add-on store had no graphic. New
  `static/brand/icon.svg` bakes the shape into a vector that the
  browser tab and HA add-on share. A small `scripts/render_brand.py`
  rasterises the SVG into PNGs (128 for the HA sidebar, 32 for the
  Safari favicon fallback, 512 for future social cards). The HA stable
  + edge add-on directories now ship the 128 PNG as `icon.png`.

## [0.12.5] — 2026-06-02

### Fixed

- **Chart.js 404'd under HA Ingress on the four chart-using widgets.**
  `finance_currency`, `finance_crypto`, `finance_stock`, and
  `weather_hourly` all loaded Chart.js by creating a `<script>` and
  setting `src = "/static/vendor/chart.umd.min.js"` — and because
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

## [0.12.4] — 2026-06-02

### Fixed

- **Widgets rendered with no CSS, fonts, or icons inside the HA Ingress
  composer / preview.** Every widget's `client.js` set
  `shadow.innerHTML` with root-relative `<link href="/static/…">` and
  `<link href="/plugins/…">`. Inside the ingress iframe those resolved
  to the HA host root and 404'd, so each shadow DOM rendered with
  default user-agent styles. `composer.js` now walks the freshly-
  rendered shadow root and prepends `TESSERAE_URL_PREFIX` to root-
  relative `href` / `src` attributes — one place, catches all 51
  widget files without touching them.
- **Inter / JetBrains Mono fonts missing under HA Ingress.** The
  `@font-face` rules in `static/style/base.css` used absolute
  `url("/plugins/fonts_core/…")` which resolved against the HA host
  root. Switched to CSS-relative `url("../../plugins/fonts_core/…")` so
  the browser resolves them against `base.css`'s own URL — works with
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
  add-on publishes its API on host port 8766, container 8765 — but
  `to_loopback_url` preserved the URL's port (8766), so it tried
  `http://127.0.0.1:8766` inside the container where nothing was
  listening. A new `TESSERAE_BIND_PORT` env var (set in both add-on
  configs) tells the renderer which internal port the server actually
  binds, independent of the host-side mapping.

## [0.12.3] — 2026-06-02

### Added

- **HA Add-on Configuration tab now actually wires through.** The
  `log_level`, `mqtt_host`, `mqtt_port`, `mqtt_username`, and
  `mqtt_password` options were declared in `config.yaml` but went
  nowhere — users had to set the same values twice (once in HA's
  form, once in Tesserae's Settings → MQTT broker page). A new
  `app/ha_options.py` reads `/data/options.json` on every container
  start (HA mode only) and applies log level to the root logger and
  MQTT details to the broker settings section. HA Configuration is
  now the canonical source for these fields; Tesserae's Settings →
  MQTT broker card hides `host`/`port`/`username`/`password` under HA
  and shows a "managed in the add-on's Configuration tab" blurb. The
  card keeps `keepalive` and `client_id` editable since they have no
  HA equivalent. Telemetry / mDNS / HA discovery / browser warmup
  intentionally stay Tesserae-side — those are user-tunable consent
  or runtime knobs, not connection config.

## [0.12.2] — 2026-06-02

### Fixed

- **Panel URLs under HA Ingress pointed at HA's port (8123), not
  Tesserae's.** `_capture_http_port` read `request.host` on every
  request and stashed the port. Inside Ingress that host is the HA
  frontend (`homeassistant.local:8123`), so every MQTT push payload
  ended up with `http://<lan-ip>:8123/renders/…` — devices 404'd at
  HA. The before-request hook now short-circuits when
  `X-Ingress-Path` is present; under Ingress we fall back to
  `TESSERAE_HTTP_PORT` / the default 8765 instead, matching the
  add-on's actual host port mapping.

## [0.12.1] — 2026-06-02

### Fixed

- **"Importing a module script failed." on every widget under HA
  Ingress.** `composer.js` did `import("/plugins/<id>/client.js")`
  with an absolute path — inside the ingress iframe that resolves to
  the HA host root and 404s with an HTML response, which the browser
  reports as a module-import failure. The compose page now exposes
  `window.TESSERAE_URL_PREFIX` the same way `_base.html` does, and the
  dynamic import prepends it. The three F1 widgets (`f1_next`,
  `f1_last_race`, `f1_weekend`) that absolute-imported the shared
  `f1_core/static/circuits.js` helper now use a relative import so
  they're prefix-independent.

## [0.12.0] — 2026-06-02

### Breaking

- **Default HTTP port is now 8765** (was 8000). Picked to dodge the
  pile of dev tooling that owns 8000 (Django runserver, `python -m
  http.server`, generic admin UIs) so a fresh `docker compose up`
  doesn't immediately collide with whatever else the user has. Affects
  every entry point — `tesserae --port`, the Dockerfile EXPOSE, the
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
  on the same address — devices end up talking to whichever one their
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
  (Flask's `request.script_root` — the ingress prefix the WSGI
  middleware extracted from `X-Ingress-Path`, empty otherwise), and the
  four affected JS sites prepend it.
- **Noisy `ha_discovery` tracebacks during broker reconnect / shutdown.**
  When the MQTT transport is explicitly disconnected (settings swap,
  process exit), the discovery publishers used to fire a full
  `RuntimeError` stack trace per retained config — dozens of them per
  shutdown. Discovery configs are already retained on the broker and
  get re-published when discovery next starts, so we now skip publishes
  silently when the transport is disconnected and log any in-flight
  disconnect race at `debug` instead of `warning` with `exc_info`.
  Other publish failures still log loudly with a traceback.

## [0.11.17] — 2026-06-02

### Fixed

- **MQTT client-id collisions between two installs sharing one
  broker.** A bare-metal Tesserae and the HA Add-on Tesserae both
  pointing at HA's bundled `core-mosquitto` saw "MQTT disconnected:
  Unspecified error" every couple of seconds — the broker evicted
  whichever client connected second the moment its duplicate
  client-id was already in use. The default client-id resolver now
  appends a 6-character hex suffix persisted to
  `data/core/.mqtt_client_id_suffix`. Random so it doesn't
  coordinate between hosts; persistent so MQTT subscriptions stay
  attached to a stable id across restarts; one-shot so existing
  installs don't get a new id and lose their retained-message
  bindings on upgrade — they'll generate one on first restart and
  hold it from then on. Settings → Broker → MQTT client id still
  overrides everything.

## [0.11.16] — 2026-06-02

### Fixed

- **HA Add-on: panel base_url pointed at the docker bridge IP.**
  Tesserae's `detect_local_ip()` used a UDP-getsockname trick to find
  the host's outbound IPv4. Under `host_network: false` (which all HA
  Add-ons use) the trick returns the docker bridge address
  (172.x.x.x), which no LAN client can reach. Panels listening for
  MQTT push frames or polling the TRMNL BYOS endpoint at that URL
  would silently fail. Resolution order is now:
  1. `TESSERAE_HOST_IP` env var (unchanged, always wins).
  2. HA Supervisor's `/network/info` API — picks the primary
     interface's IPv4 address. Only reachable when
     `hassio_api: true` is set on the add-on (both add-on definitions
     bump that in the companion repo).
  3. The existing UDP-getsockname trick.

  Result is cached for the process lifetime so we don't hammer the
  Supervisor API on every `detect_local_ip()` call (multiple admin
  routes / page renders use it).

## [0.11.15] — 2026-06-02

### Added

- **HA Add-on edge channel.** Every push to `main` now builds and
  publishes a per-commit Docker tag
  `ghcr.io/dmellok/tesserae:<pyproject>-edge.<sha7>` (in addition
  to the existing `:main` and `:latest`). The companion add-on repo
  gained a parallel `tesserae-edge/` add-on definition that tracks
  those tags via the sync-addon workflow, which now has two jobs:
  - `bump-stable` — fires on `release: published`, edits
    `tesserae/config.yaml`.
  - `bump-edge` — fires on `push: branches: [main]`, edits
    `tesserae-edge/config.yaml` to the per-commit edge version.
  HA users see two add-ons in the store. Stable installs the
  released Tesserae; edge installs whatever's on `main` right now.
  Both can be installed in parallel (different `slug:`, different
  persistent `/data` volume); edge intentionally doesn't expose
  port 8000 on the host so it can coexist with stable.

## [0.11.14] — 2026-06-02

### Fixed

- **HA Add-on: `PermissionError` on `/data/plugins` on first boot.**
  The Docker entrypoint chowns `/app/data` to `pwuser` so the
  un-privileged worker can write to it after gosu-drops; it didn't
  know about `TESSERAE_DATA_ROOT` (added in 0.11.13), so when HA
  Supervisor mounted `/data` root-owned the gosu-dropped Tesserae
  process EPERM'd on the first `mkdir`. Entrypoint now also chowns
  `TESSERAE_DATA_ROOT` when set and different from `/app/data`.

## [0.11.13] — 2026-06-02

### Added

- **`is_homeassistant` flag in telemetry.** Sits alongside `is_docker`
  on both `app.started` and `app.heartbeat`. True when the
  `TESSERAE_HA_INGRESS=1` env var is set (the companion HA Add-on
  exports it via its `config.yaml` `environment:` section). Lets the
  maintainer see the HA-Add-on subset of the installed fleet as it
  grows. No content sent — just a single `true` / `false` deployment
  flag, same shape as `is_docker`.
- **`TESSERAE_DATA_ROOT` env var** to override the data directory. The
  HA Add-on sets this to `/data` so Tesserae's settings, dashboards,
  schedules and event log land on HA Supervisor's per-add-on
  persistent volume — which Supervisor automatically backs up across
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
  bypass still requires both env var + header — that part stays
  belt-and-braces.

## [0.11.12] — 2026-06-02

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

## [0.11.11] — 2026-06-02

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

## [0.11.10] — 2026-06-02

### Added

- **App footer with version + GitHub link.** Subtle dotted-underline
  link in the bottom margin of every page, deep-linked to the
  matching release tag on GitHub (`/releases/tag/vX.Y.Z`). Reads as
  "Tesserae v0.11.10". 60% opacity by default, brightens to 95% on
  hover. Pure cosmetic; no layout impact above the fold.

## [0.11.9] — 2026-06-02

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

## [0.11.8] — 2026-06-02

### Changed

- **Send page's History tab updates live.** v0.11.7 backgrounded the
  push so the browser didn't freeze, but the History tab still
  required a manual reload to see the new row land. Subscribed it to
  the same `/events/stream?type=push` SSE feed the Events tab uses;
  on each push event, the tab refreshes the history list in place
  (debounced 300 ms to collapse multi-target fan-outs into one swap).

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
