# Changelog

All notable changes to Tesserae are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are
[SemVer](https://semver.org/) (pre-1.0, so minors can carry breaking changes).

## [Unreleased]

## [0.64.10], 2026-06-23

### Added

- **`GET /mirror/<device_id>` browser-friendly mirror page.** A tiny
  auto-refreshing HTML wrapper that embeds the existing
  ``/preview/<id>.png`` so old tablets, jailbroken Kindles in
  browser mode, kiosk PCs, or any screen with a URL bar can run a
  Tesserae dashboard without a native client. Defaults to the
  device's ``sleep_interval_s`` for the refresh cadence; override
  via ``?refresh=N`` (clamped to ``[5, 86400]`` seconds). Optional
  ``?rotate=90/180/270`` applies a CSS rotation client-side so a
  sideways-mounted iPad showing a portrait panel lands the right way
  up. Equivalent in spirit to TRMNL's ``/mirror`` endpoint. Same
  LAN-bypass auth as ``/preview/`` and ``/renders/``.
- **Settings → Devices: Preview + Mirror links** on every device
  card's footer toolbar. The URLs were always reachable but
  undocumented; surfacing them as visible buttons (with descriptive
  tooltips) means an admin can ship a panel-on-a-browser setup in
  one click without reading the spec.

### Why

Real community ask in [#8](https://github.com/dmellok/tesserae/discussions/8)
(RealGandy): an old iPad running iOS 12 has no TRMNL or Tesserae
native client but can run Safari, so a refresh-tagged page pointing
at the existing per-device preview alias unlocks the device as a
display target with zero firmware work.

## [0.64.9], 2026-06-23

### Added

- **`/status` response carries resolved local-time fields.** The
  heartbeat response (``POST /api/v1/device/<id>/status``) now
  includes ``local_time`` (ISO 8601 with offset), ``tz`` (IANA
  name actually used), ``tz_offset_seconds``, and ``dst_active``
  alongside the existing ``status`` / ``config`` / ``next_poll_s``
  / ``server_time``. The client's heartbeat body can optionally
  include ``tz`` to pick the zone; absent / invalid falls through
  silently to the server's configured ``settings.app.timezone``
  and then to the host's TZ.
- **Spec doc: "Client guarantees" section** in
  ``docs/dev/client-protocol.md`` anchoring the thin-client design
  principle. Future protocol changes get tested against the list
  (no RTC required, no IANA db, no NTP, no schedule math, no
  locale formatting).

### Why

The generic CircuitPython client work (collaborator: bablokb /
Bernhard in [discussion #24](https://github.com/dmellok/tesserae/discussions/24))
hit the constraint that memory-constrained embedded clients can't
carry the IANA timezone database (~200 KB of flash) or a DST rule
engine. Server-resolved local time is the only sane path. The
existing ``server_time`` is UTC; clients still needed to do the
zone + DST math on every wake. The new fields hand them
everything pre-resolved.

### Backwards compatibility

Fully compatible:

- Existing clients that don't read the new fields silently ignore
  them (per the "clients ignore unknown fields" rule already in
  the spec).
- Existing clients that don't send ``tz`` in the heartbeat get the
  server's TZ fallback, same as if they'd never asked.
- The request shape is unchanged for any caller that ignores the
  new optional field.

## [0.64.8], 2026-06-22

### Fixed

- **Send page: photo uploads from Android Chrome no longer fail
  with ``ERR_UPLOAD_FILE_CHANGED``**. The dropzone now reads the
  selected file's bytes into memory immediately at selection time
  (via ``File.arrayBuffer()``) and replaces the
  ``<input type="file">`` with a fresh in-memory ``File`` backed by
  those bytes. Form submission therefore no longer depends on the
  OS file handle that Android lets drift — Google Photos sync,
  HEIC→JPEG conversion, EXIF rewriting, and similar background
  modifications between selection and submit are now invisible to
  Chrome's upload comparison. The form submit handler awaits any
  in-flight snapshot so a quick tap on **Push file** while
  ``arrayBuffer()`` is still resolving doesn't sneak the original
  URI through. Sub-second for the 16 MiB max even on mid-range
  phones, so the delay is invisible in the common case.

### Why

Multiple Android users reported the file picker showing the
selected photo and "2.82 MB" file size, then Chrome aborting the
submit with ``ERR_UPLOAD_FILE_CHANGED`` and dropping them on
Chrome's "Your file couldn't be accessed" error page. The bytes
never reached Flask — Chrome compares the file size + mtime it
cached at selection time with what it sees at submit time and
refuses to send mismatched data. The fix attacks the underlying
race: snapshot the bytes once, hold them in JS memory, and submit
those instead of asking Chrome to re-read.

## [0.64.7], 2026-06-22

### Fixed

- **Dashboard editor: live preview now actually sticks**. The
  preview card has ``position: sticky`` so it stays visible as the
  user scrolls through the cell editors below it, but
  ``html, body { overflow-x: hidden }`` in ``base.css`` was
  silently establishing a new scroll container and breaking
  sticky positioning across the whole document. Swapped the
  declaration for ``overflow-x: clip`` (same visual no-scrollbar
  guarantee, no scroll-container side-effect), with ``hidden`` as
  a cascade fallback for browsers without ``clip``.
- **Dashboard editor: preview no longer hides behind the title
  header**. The preview-card was sticky-pinned at ``top: 84px``
  while the editor-header (sticky at ``64px``, ~68px tall) sits
  in the same band — so the preview tucked behind the header on
  scroll. Bumped the preview's sticky top to ``140px`` so it sits
  cleanly below the header with an 8px gap.
- **Dashboard editor: preview frame capped to viewport height**.
  Portrait panels (e.g. 800×1200) made the preview card taller
  than the viewport, so sticky pinned only the top portion. Added
  a ``max-width`` driven by available vertical space
  (``min(720px, (100vh - 252px) × --panel-ar)``) mirroring the
  trick already used on mobile, so the whole card fits regardless
  of panel orientation.
- **Sparkline charts: spline overshoot no longer clipped**.
  Chart.js with ``tension: 0.3`` produces a curve that overshoots
  the data max on a sharp spike; the sparkline's y-axis was sized
  exactly to ``max(values)`` so the overshoot got sliced off flat
  against the top edge. Added ``grace: "12%"`` so the spline has
  headroom to breathe.

## [0.64.6], 2026-06-22

### Fixed

- **Telemetry: accurate IP-suppression mechanism**. The
  ``_privacy_props`` helper used to ship ``$ip: ""`` on every
  PostHog event, claiming this prevented IP storage. It didn't —
  ``$ip`` is a PostHog *override* for the IP used during geo
  enrichment, not a suppression of storage. The real mechanism is
  the project-level **Discard client IP data** toggle in PostHog
  Project Settings, which has now been enabled on the maintainer's
  project. The ``$ip`` property has been dropped from the payload
  (it was a no-op), and the module docstring + privacy doc + test
  assertions now describe the actual mechanism.

### Why

A live look at incoming events showed the full client IP
(``180.181.192.166``) being stored alongside city / postal code /
lat-lon. The ``$ip: ""`` we'd been shipping since v0.64.0 wasn't
doing anything; PostHog was reading the request IP from the
reverse-proxy ``X-Forwarded-For`` header and storing it
regardless. The privacy doc + onboarding consent footnote
promised "no IP storage" — that promise is now actually upheld by
the project-level toggle. Behaviour for users who already
consented to telemetry is otherwise unchanged.

## [0.64.5], 2026-06-22

### Added

- **Onboarding wizard: new "Timezone" step**. Slots in right
  after Welcome, before Transport. The picker is pre-selected
  with the host-detected IANA name (``TZ`` env var or
  ``/etc/localtime`` symlink target), so a sensible default lands
  one click away. The user can keep it or pick another. Skip
  button writes ``system`` (scheduler-time auto-detect, defaults
  to UTC on bare Docker).

### Why

The scheduler interprets every daily fire time and time-of-day
window against ``settings.app.timezone``. On Docker images
without ``TZ=`` set, that resolves to UTC — so a brand-new user
creates an *8:00 AM* schedule expecting breakfast, gets pushed
to dinner-time. Surfacing the picker during onboarding instead
of burying it under Settings → App means new installs land
already pointing at the right zone.

### Side benefit

v0.64.4's ``timezone`` / ``timezone_region`` telemetry props
now populate for every event from the first heartbeat onward
(instead of for installs where the system path happens to find
a real IANA name). The wizard's save handler also live-updates
the in-process ``Telemetry._cfg`` so the very next heartbeat
already carries the new timezone — no restart required.

### Tests

Three new tests in ``test_onboarding.py``: the timezone step
renders with a populated picker, picking a valid IANA name
saves it + redirects to broker, and a bogus hand-typed value
falls through to ``"system"`` instead of slipping into
settings.

## [0.64.4], 2026-06-22

### Added

- **Timezone properties on every telemetry event**. Every event
  now carries ``timezone`` (the resolved IANA name, e.g.
  ``Australia/Melbourne``) and ``timezone_region`` (the first
  segment, ``Australia``) when one can be derived. The resolution
  order is: ``settings.app.timezone`` (validated against
  ``zoneinfo.available_timezones()``) → ``TZ`` env var → parse
  ``/etc/localtime`` symlink target. When nothing resolves, the
  properties are *omitted* from the event rather than shipped as
  ``UTC`` (which would collapse every default-Docker install into
  one bucket and lose the signal). Gives the maintainer
  user-set geographic data without IP geolocation — works on
  Docker, doesn't depend on the reverse-proxy's
  ``X-Forwarded-For`` plumbing.

### Notes

- The v0.64.5 followup will add a timezone-picker step to the
  onboarding wizard so new installs land with a real IANA name
  set instead of relying on the system auto-detect path.

## [0.64.3], 2026-06-22

### Fixed

- **Rotation edit jumped to the wrong card and lost context on
  save**. Clicking *Edit* on a rotation routed the browser to
  ``#rotation-form-card`` — the *New rotation* card at the bottom
  of the page — instead of the rotation being edited. And after a
  successful save the redirect dropped both the ``?edit=`` query
  param and any URL fragment, leaving the user at the top of the
  rotations list with no idea where they were. Fixed both:
  - The *Edit* link now anchors to ``#rotation-<id>`` so the
    browser scrolls the in-flight edit form into view.
  - ``rotations.update``'s success + validation-error redirects
    both append ``#rotation-<id>`` so the page lands back at the
    just-edited card.
- **In-flight rotation card now wears an accent halo**. The
  rotation being edited picks up a new ``.is-editing`` class on
  the rotation card (``.dx-rotation-status`` already pins the
  status pill to the top-right). The card border flips to the
  accent colour with a soft accent-tint outer shadow so the user
  can see at a glance which card holds the edit form — useful
  when they scroll up to compare the read view of the rotation
  to the form below.

## [0.64.2], 2026-06-22

### Changed

- **PostHog endpoint moved behind the maintainer's reverse proxy
  at ``https://t.dmello.io``** (forwards to PostHog Cloud US).
  Both the in-app telemetry POST URL and the docs-site JS
  snippet's ``api_host`` are updated; the proxy forwards both
  ``/i/v0/e/`` (events) and ``/static/array.js`` (the lazy-loaded
  SDK bundle). The events PostHog actually receives are byte-
  identical to before.
- **Why**: bypasses network-level ad-blockers (uBlock's default
  lists, Pi-hole, NextDNS) that silently drop requests to known
  analytics origins — a non-trivial fraction of the privacy-
  conscious Tesserae audience runs one of these. Going via a
  first-party-looking domain means opt-in installs that would
  previously have failed at the DNS layer now actually deliver
  events.
- **JS SDK**: docs snippet picks up ``ui_host:
  'https://us.posthog.com'`` so PostHog's occasional "view in
  PostHog" deep-links resolve to the real dashboard instead of
  trying to point back at the proxy.
- **Privacy doc** updated: the "block this to opt out at the
  network level" instruction now names ``t.dmello.io``.

### Notes

- Self-host instructions for the reverse proxy aren't in-repo;
  it's just an nginx/Caddy block that maps ``t.dmello.io/i/* ->
  us.i.posthog.com/i/*`` and ``t.dmello.io/static/* ->
  us-assets.i.posthog.com/static/*``.

## [0.64.1], 2026-06-22

### Fixed

- **Telemetry copy mentioned only two of the four events**. The
  Settings → Server → App toggle help text and the System tab's
  Telemetry card subtitle still listed only ``app.started`` +
  ``update.applied``. ``app.heartbeat`` (added v0.5x) and
  ``theme.user_created`` (v0.6x) were silently shipping but the
  user-facing copy never caught up. v0.64.0's PostHog swap was
  a good moment to fix it; also threaded in the country/region
  detail that's new with PostHog.
- **CI fix for v0.64.0 rename**: ``tests/test_system_routes.py``
  was still constructing ``TelemetryConfig`` with the old
  ``app_key`` kwarg instead of ``project_key``. Already pushed
  as ``4e8762b``; included here for changelog completeness.

## [0.64.0], 2026-06-21

### Changed

- **Telemetry backend swapped from self-hosted Aptabase to
  PostHog Cloud (US region)**. Same three events
  (``app.started``, ``app.heartbeat``, ``update.applied``) plus
  ``theme.user_created``, same anonymous install UUID, same
  no-PII data footprint, same opt-in default-off behaviour, same
  ``TESSERAE_TELEMETRY=0`` kill switch. The wire format changed:
  POSTs now go to ``https://us.i.posthog.com/i/v0/e/`` with a
  flat ``{api_key, event, distinct_id, properties, timestamp}``
  body instead of the Aptabase-shaped ``{sessionId, eventName,
  systemProps, props}``.
- **Privacy hardening on every event**. Every POST carries
  ``$ip: ""`` (request IP not stored on the event) and
  ``$process_person_profile: false`` (no PostHog "person"
  profile created or updated for the install UUID). PostHog
  still uses the request IP at ingestion to derive country +
  region columns — the maintainer wants those to see roughly
  where Tesserae is running — then drops the IP. Pinned via
  ``test_privacy_props_present_on_every_event`` so a future
  refactor can't drift.
- **Docs site analytics**: ``overrides/main.html`` swapped from
  the Umami snippet (``analytics.dmello.io``) to the PostHog JS
  snippet, configured for ``autocapture: false``,
  ``disable_session_recording: true``, ``respect_dnt: true``,
  ``person_profiles: 'identified_only'``. Same project as the
  in-app telemetry so docs traffic + in-app events can be
  cross-queried.
- **Why**: Aptabase's dashboards weren't giving the maintainer
  the cohort + funnel views needed to actually answer questions
  about how Tesserae is used. PostHog's free tier covers the
  expected fleet shape comfortably.
- **Env var rename**: ``TESSERAE_TELEMETRY_APP_KEY`` →
  ``TESSERAE_TELEMETRY_PROJECT_KEY``. ``TESSERAE_TELEMETRY_HOST``
  unchanged. The ``TESSERAE_TELEMETRY=0`` hard kill is unchanged.

### Notes

- Onboarding telemetry-consent copy + ``docs/privacy.md`` +
  ``README.md`` rewritten to describe the new backend +
  surveillance-feature kill-switches.
- The legacy ``aptabase.dmello.io`` endpoint can be sunset after
  enough installs have updated to v0.64.0+.

## [0.63.13], 2026-06-21

### Fixed

- **CORS ``Allow-Headers`` now includes ``X-Pairing-Code``**. The
  ``/api/v1/device/register`` endpoint reads the 6-digit pair
  code from that header (see ``post_register`` in
  ``app/rest_api.py``). v0.63.11 listed every other custom
  header the API uses but missed this one, so the browser-based
  emulator's first call — the pairing fetch — failed at
  preflight with "Request header field x-pairing-code is not
  allowed by Access-Control-Allow-Headers." Added a test that
  preflights ``/register`` with the header and asserts it's in
  the allow list.

## [0.63.12], 2026-06-21

### Added

- **CORS on ``/renders/<digest>.<ext>`` and ``/preview/<id>.png``**.
  v0.63.11 added CORS to the REST API but the image endpoints
  the API returns URLs for stayed same-origin only. A browser
  drawing those images into a ``<canvas>`` cross-origin tainted
  the canvas — the image displayed fine but ``getImageData()``
  was blocked, so the device emulator's per-pixel palette-
  quantization preview (Spectra 6 / mono / 4-grey simulation)
  couldn't run. Added ``Access-Control-Allow-Origin: *`` to both
  routes. No new data exposed: the image was already fetchable
  from any origin via ``<img src>`` — the header just unlocks
  pixel access. The auth bypass for these routes (loopback /
  LAN-only via ``app/auth.py`` ``_LAN_PATHS``) is unchanged.

## [0.63.11], 2026-06-21

### Added

- **CORS on the ``/api/v1/device/*`` REST API**. Every response
  now carries ``Access-Control-Allow-Origin: *`` plus
  ``Allow-Methods`` / ``Allow-Headers`` / ``Expose-Headers``;
  ``OPTIONS`` requests short-circuit to a 204 with the same
  headers. Unblocks browser-based callers: the in-browser
  device emulator at ``emulator.tesserae.ink`` (planned), the
  "Test push" UI in the future HTTP-push transport (#23), and
  any other browser tool that needs to pair + poll a Tesserae
  server. ``Allow-Origin: *`` is safe here because every endpoint
  already requires a Bearer token — the token is the security
  boundary, not the origin. Admin UI / settings / plugin routes
  outside this blueprint are unaffected.

## [0.63.10], 2026-06-21

### Fixed

- **``.sr-only`` was used but never defined**, so the rotation
  step rows' "Play step N now" hidden labels rendered as visible
  text — and once v0.62.x clamped icon-only buttons to a 34×34
  square, the visible text overflowed the box and showed up as a
  ghost rectangle next to the icon. Added the canonical
  visually-hidden utility class to base.css.
- **History rows redesigned for mobile**. The v0.63.9 attempt
  flex-wrapped each section to its own line and ballooned each
  row to ~250 px tall on a phone. Replaced with a compact 2-col
  grid (thumbnail in the left column spanning all rows; the
  right column stacks time / source / status on three short
  lines). Reads as a tight card-style row instead of a 4-line
  pile-up.

## [0.63.9], 2026-06-21

### Fixed

- **Mobile overflow pass** (Rotations + History + Widgets +
  Settings + Schedule editor + Rotation editor). Four screenshots
  in a row surfaced the same root: pages built at desktop widths
  had no responsive collapse at narrow viewports, so form heads
  with trailing meta + input chrome overflowed their cards,
  row-style content (history rows, widget rows) starved their
  middle column of width, and the field-grid clung to multi-
  column layouts past the point of usability.
  - New ``static/style/dx-responsive.css`` (~110 lines) holds
    the entire mobile pass. Loaded last in ``_base.html`` so its
    ``@media`` rules win against any per-page sheet's
    breakpoints.
  - ``html, body { overflow-x: hidden; max-width: 100% }`` in
    base.css as a safety net so a stray wide descendant can't
    force horizontal page scroll.
  - Section card padding compresses 22/24 → 16/14 at ≤640 px,
    then 14/12 at ≤480 px.
  - ``.dx-section-head`` flex-wraps so trailing meta / cta /
    URL inputs drop below the title.
  - History + Widgets row layouts collapse to stacked at
    ≤640 px (same pattern Dashboards picked up in v0.63.8).
  - Tabs row scrolls horizontally instead of wrapping to two
    lines on small screens.
  - Dashboard create-row stacks at ≤480 px (Create-dashboard
    button goes full-width below the input).
- **Rotation status pill pinned to the top-right corner of its
  card**. The "Not active right now" / "Now: step N…" pill was
  flowing inside the section head row, vertically centred next
  to the title. Switched to ``position: absolute; top: 18px;
  right: 22px`` on the card so the pill always hugs the card's
  top-right corner regardless of how tall the title block grows.
  At ≤640 px the pin releases and the pill flows back below the
  title (would otherwise overlap a narrow title block).

## [0.63.8], 2026-06-21

### Fixed

- **Dashboards page now responsive at phone widths**. The row
  layout (icon + name + meta strip on the left, three action
  buttons on the right) was fine at desktop but starved the left
  half of horizontal space on a 390-px viewport — the dashboard
  name wrapped one word per line and the buttons visually
  overlapped the meta strip. Added a ``@media (max-width: 640px)``
  collapse that flips ``.dx-dashboard-row`` to a column layout
  with the actions dropping below the row content. Same pattern
  ``.dx-discovered-row`` on Devices already uses. Also added a
  single-line truncation on ``.dx-dashboard-name`` so a long
  title doesn't push the meta strip out of the row.

## [0.63.7], 2026-06-21

### Changed

- **settings.css → dx-history.css + dx-events.css** (the last
  two page-specific extractions). History (~130 lines, row
  layout + timestamp + thumbnail + source-pill palette + device
  chip) and Events (~200 lines, per-type swatch on rows + on
  filter chips, summary/body/expand grid, lazy-hydrate JSON
  placeholder) move to their own stylesheets. settings.css drops
  another ~330 lines.
- **settings.css** now ends at ~3000 lines (down from 3370),
  with the page-specific blocks (Battery / Dashboards /
  Marketplace / History / Events) extracted. What remains is
  true Settings-page styling, dx-* shared primitives, the
  card_head adapter, and the dark-mode block — i.e. the file
  is roughly what it should have been all along.

## [0.63.6], 2026-06-21

### Changed

- **settings.css → dx-dashboards.css + dx-marketplace.css**.
  Dashboards (~35 lines, ``.dx-dashboard-create`` / ``-group``
  / ``-group-head``) and Widgets Browse (~85 lines, ``.dx-mkt-
  search`` / ``.dx-mkt-chips`` / ``.dx-mkt-chip``) each move
  to their own stylesheet. Batched as one release because the
  two blocks together are small. Cascade order preserved. No
  rules changed.

## [0.63.5], 2026-06-21

### Changed

- **settings.css → dx-battery.css**: Battery dashboard rules
  (97 lines, ``dx-battery-grid`` / ``-card`` / ``-head`` / etc.)
  moved to their own stylesheet. Cascade order preserved by
  linking the new file in the same slot the rules previously
  occupied (right after dx-schedules.css). No rules changed.
  settings.css banner kept so the file map still reflects the
  extraction.

## [0.63.4], 2026-06-21

### Changed

- **Login + Setup**: outer card adds the ``.dx-section-card``
  class (keeping ``.narrow`` for max-width) so the auth-flow
  pages inherit the v0.56 chrome (box-shadow, border-radius,
  border) instead of the legacy card look. Surgical — the
  ``card_head`` adapter handles the inner header chrome and
  the form layout is otherwise unchanged. ``onboarding.html``'s
  one remaining ``card--wizard-info`` block is intentionally
  left on the legacy chrome; it's an info-callout role inside
  a wizard step body, not a section card, so it doesn't map
  onto the ``section_card`` vocabulary.

## [0.63.3], 2026-06-21

### Changed

- **Page editor: outer card chrome flips to ``.dx-section-card``**.
  Five live ``<section class="card">`` blocks (per-cell card +
  Live preview + Dashboard + Layout + Schedules) become
  ``.dx-section-card`` so the editor reads as siblings of the
  rest of v0.56-uplifted pages. The custom drag-tile-to-canvas
  interaction model + the bespoke cell-card header styling are
  preserved; only the outer shell flipped. ``card_head`` adapter
  carries the header chrome inside each card.

## [0.63.2], 2026-06-21

### Changed

- **Settings page: remaining live ``<section class="card">``
  blocks flip to ``.dx-section-card``**. Three small cards (top-
  level Diagnostics + Server-tab Diagnostics + Sign-out) were
  still on the legacy card class while everything else around
  them had already moved. The pragmatic ``card_head`` adapter in
  settings.css handles the header chrome; this is just the outer
  shell. Most of the file's remaining ``class="card"`` matches
  live inside ``{% if False %}`` dead-branch guards left over
  from the device-card v2 migration.

## [0.63.1], 2026-06-21

### Changed

- **Themes builder uplifted to ``section_card`` chrome**. The
  three builder sections (Seed from image / Identity / Colour
  palette) flip from the legacy ``<section class="card">`` +
  ``<header class="card-head">`` markup to proper
  ``.dx-section-card`` + ``.dx-section-head`` (teal icon square +
  title + description). Builder head action buttons flip to
  ``.dx-btn-ghost-sm`` / ``.dx-btn-primary``; the delete button
  goes icon-only. The v0.59.0 release note that called Themes
  "Tier 4 done" had only flipped the Update button; this is the
  actual structural pass.

## [0.63.0], 2026-06-21

Hygiene pass. Cleans up two pre-v0.56 selectors that survived the
admin UI uplift and were quietly diverging from the rest of the
admin surface.

### Fixed

- **events.js targets the v0.56 markup**. Pre-v0.63 the live-SSE
  script looked for ``.events`` / ``.event-row`` (the legacy
  classes the template stopped emitting at v0.56). It silently
  spawned a phantom unstyled ``<ul class="events">`` at the
  bottom of the page on every load to receive SSE rows that the
  user couldn't see. Rewrote to target ``.dx-events-list`` and
  emit the full ``.dx-event-row`` / ``.dx-event-summary`` /
  ``.dx-event-body`` / ``.dx-pill`` markup so streamed events
  look identical to the server-rendered rows (including the
  per-type icon swatch via ``.dx-event-row--<type>``). The
  live indicator label now matches the static "LIVE" /
  "Connecting…" / "Offline" treatment.
- **Condition picker JSON highlight consolidated**. The picker
  emitted bespoke ``cp-jh-key`` / ``cp-jh-str`` / ``cp-jh-num``
  / ``cp-jh-kw`` classes whose palette lived in schedules.css,
  parallel to the events page's shared ``dx-code-*`` palette in
  settings.css. The picker now emits ``dx-code-*`` directly so
  the condition editor + events JSON share a single source of
  truth; the duplicated rules in schedules.css are removed.

## [0.62.5], 2026-06-21

### Fixed

- **Saved schedules table: action buttons stop stacking**. The
  Disable / Fire now / Edit / Delete buttons were each wrapping
  to their own line because the cell uses ``width: 1%`` (shrinks
  to content) and the labelled buttons overflowed at any
  reasonable column width. Flipped all four to ``.dx-btn-icon-
  only`` (with title + aria-label so the verb is still
  discoverable), pinned ``flex-wrap: nowrap`` on the action row,
  and made the inline forms ``display: inline-flex`` so they
  don't break the row themselves.
- **Edit schedule form picks up the same input + alignment
  polish as Rotations**. The v0.62.2/0.62.4 selectors were
  scoped to ``.rotation-form``; mirror them under ``.schedule-
  form`` so the Name / Dashboard / Interval / Active window /
  Priority fields land on the v0.56 ``.dx-input`` baseline, the
  Smart sync row vertically centres next to Render lead, and
  the ``info_pop`` button sits inline with the toggle.

## [0.62.4], 2026-06-21

### Fixed

- **Day-of-week chips now centre properly**. The v0.62.2/0.62.3
  attempts didn't account for the hidden checkbox AND anonymous
  whitespace text nodes both still acting as flex items between
  the input and the visible span. Took the input out of flow
  entirely (``position: absolute; opacity: 0``) so it covers the
  chip for click handling without consuming any layout, then
  flipped the chip itself to ``display: grid; place-items: center``
  so positioning is unambiguous.
- **Smart sync row vertical alignment**. The switch component is
  a single-line label; the neighbouring Render lead field has a
  label-above-input stack. Without ``align-items: center`` on the
  parent grid, the toggle floated next to the input's label
  instead of the input itself. Added the alignment override to
  ``.rotation-form .field-grid``, plus a flex layout on
  ``.field--switch`` so the trailing ``info_pop`` button sits
  inline with the toggle instead of wrapping to its own line.

### Added

- **Conditions toggle: live count update**. The
  ``Conditions (N)`` label now reflects condition adds/removes in
  the picker, not just the server-rendered initial count. The
  condition picker already fires a synthetic ``change`` event on
  its hidden textarea whenever ``writeRows`` runs; the rotation
  form's container delegates on that event, parses the JSON, and
  updates the matching step's toggle label in place.

### Changed

- **Condition picker rows: responsive layout**. The kind select
  was stretching to full row width at narrow viewports because
  it was the only flex child on its line. Constrained it to
  content width (max 180 px), pinned the remove button to the
  right edge via ``margin-left: auto``, and rebalanced the
  ``.cp-row-body`` flex weights so entity/operator/value wrap
  consistently.

## [0.62.3], 2026-06-21

### Fixed

- **Condition picker inputs now match the v0.56 input baseline**.
  The previous pass only styled inputs inside ``.field`` wrappers,
  which the condition picker doesn't use — its selects + text
  inputs land directly in ``.cp-row-body`` / ``.cp-row``. Added
  selectors for ``.condition-picker .cp-row-body input/select``
  + ``.condition-picker .cp-row > select`` so the HA entity
  dropdown, source-id input, operator select, and value input
  all read as siblings of the rotation form's anchor fields.
- **Day-of-week chips: centring rule promoted to base
  ``.dow-chip``** rather than scoped to ``.rotation-form``, so
  the rule fires anywhere the chip is used (rotation form,
  schedule form, condition picker time-window builder).

## [0.62.2], 2026-06-21

### Fixed

- **Rotation form inputs match Tesserae's v0.56 input baseline**.
  The Name / Cycle starts / Cycle ends / Priority fields (plus
  the page select + dwell number on each step) inherited the
  legacy ``.field`` shape (6 px radius, 8/12 px padding, lighter
  border). They now use the ``.dx-input`` baseline (8 px radius,
  11/13 px padding, slightly heavier ``--t-input-border``) so the
  form visually belongs to the same admin surface as the rest of
  v0.56-uplifted pages.
- **Day-of-week chips: text now sits dead-centre**. The hidden
  checkbox inside each ``.dow-chip`` was a flex child sharing a
  gap with the visible label, which shifted the label visually
  right. Set ``justify-content: center; gap: 0`` on the chip and
  gave it a minimum width so the labels read as a balanced
  segmented control.

## [0.62.1], 2026-06-21

### Changed

- **Rotations form: each step becomes a self-contained sub-card**.
  The ``.rot-step-wrap`` wrapper picks up real chrome (inset
  background + 1px border + 12px radius + padding) so the controls
  row and the Conditions panel underneath read as one tile per
  step instead of two free-floating blocks. When the user opens
  Conditions, the panel "swells" inside the same sub-card,
  separated only by a top divider — no floating panel, no visual
  disconnect from its step.
- **Rotations form: Conditions toggle gains a filled active state**.
  ``aria-expanded="true"`` swaps the ghost outline for an
  accent-tint fill so the toggle visually pins to its expanded
  panel, matching the spec's "active when open" pattern.
- **Rotations form: structure pass**.
  - New "STEPS — ROTATE THROUGH THESE IN ORDER" uppercase
    sub-heading above the steps list (``dx-form-subhead``).
  - ``<hr class="dx-divider">`` between the anchor / steps /
    days / sync / routing / enabled groups so the form reads
    as distinct logical sections.
  - "+ Add step" affordance becomes a full-width dashed button
    (``.dx-rot-add-step``) — reads as "drop another step here"
    instead of "click this small thing in the corner."
  - Form submit button flips to ``.dx-btn-primary``; Cancel
    flips to ``.dx-btn-ghost-sm``.

The condition picker itself is unchanged (works at v0.55 fidelity,
JSON highlighter just got fixed in v0.60.1); the sub-card is the
visual containment.

## [0.62.0], 2026-06-21

The parked Rotations + Schedules uplift from v0.60.1's notes, plus
a navigation pass on the settings.css "junk drawer."

### Changed

- **Rotations** (``/rotations``): each rotation card now uses the
  proper ``section_card`` macro — teal icon square, rotation
  name, id as the description code, status pills on the right —
  instead of the legacy ``.rotation-head`` markup. Steps preview
  list converts to ``.dx-inset-row`` rows with a step-number
  tile, page name + dwell, bound-device chips, and an inline
  play button for the active step. Action buttons (Disable /
  Fire now / Edit / Delete) move to the ``.dx-btn-ghost-sm``
  vocabulary and the trash button goes icon-only. The "New
  rotation" form gets the same ``section_card`` chrome via the
  macro instead of going through the legacy ``card_head``
  adapter.
- **Schedules** (``/schedules``): saved-schedule table actions
  flip to ``.dx-btn-ghost-sm``; state pills move from ``.pill``
  to ``.dx-pill`` with a tone dot; trash button goes icon-only.
  The "page deleted" warning pill picks up the danger-pill
  chrome from the v0.56 token set.
- **New stylesheets**: ``static/style/dx-rotations.css`` (~150
  lines) holds the new Rotations chrome; ``dx-schedules.css``
  (~40 lines) holds the action-row layout. Loaded after
  ``schedules.css`` so the cascade order matches existing
  expectations.
- **settings.css navigation**: 3300-line file gains a top-of-
  file Table of Contents banner + per-page section banners
  marking the parts that are candidates for extraction (Battery,
  History, Events, Dashboards, Marketplace). No rules moved,
  no behaviour change — preparation for the eventual split.

## [0.61.2], 2026-06-21

### Fixed

- **REST ``/api/v1/device/<id>/frame`` was missing renderer-specific
  payload fields**. A pi_png client polling the endpoint logged
  ``download/paint failed: payload missing 'rotate'`` because the
  response only carried the REST envelope (``url``, ``format``,
  ``panel_w/h``, ``render_id``, ``renderer_id``) and not the
  v3-frozen ``{rotate, scale, bg, saturation}`` fields its MQTT-
  subscribed cousins receive. ``get_frame`` now resolves the
  renderer for the latest frame, pulls its runtime settings, and
  merges ``renderer.payload()`` into the response. Pi BIN /
  ESP32 / TRMNL / pico_bin REST clients pick up their renderer-
  specific fields the same way; nothing kind-specific in the new
  code path.

## [0.61.1], 2026-06-21

### Changed

- **Default sleep / refresh cadence dropped from 15 min to 60 s
  across every device kind** (esp32, esp32_bw, pico_bin, pi_bin,
  pi_png, trmnl). Newly paired devices stay responsive enough for
  the user to log in and pick a reasonable cadence before the
  device disappears into long sleep. Existing devices keep their
  saved value untouched.

## [0.61.0], 2026-06-21

### Added

- **pi_bin_client / pi_png_client: sleep interval setting**
  ([devices/pi_bin_client/device.json](devices/pi_bin_client/device.json),
  [devices/pi_png_client/device.json](devices/pi_png_client/device.json)).
  Both Pi clients now declare a ``sleep_interval_s`` field in their
  ``config_schema`` (default 15 min, bounds 30 s – 7 days) with the
  same preset list the ESP32 client carries. REST-polled instances
  pick the new cadence up via the existing ``_next_poll_s`` /
  ``_current_config`` helpers in [app/rest_api.py](app/rest_api.py),
  which already echo per-device config back to the client in the
  ``/api/v1/device/<id>/status`` response. MQTT-driven instances
  ignore the field (they wake on retained-frame publishes).
  ``validate_config`` enforces the bounds server-side before a
  typoed cadence reaches the client. Manifest version bumped on
  both kinds (0.1.0 → 0.2.0).

## [0.60.5], 2026-06-21

### Fixed

- **Events page server freeze after clicking through filter
  chips**: each chip click opens a new SSE connection to
  ``/events/stream``; the previous connection's generator was
  blocked in ``queue.get(timeout=15s)`` and only learned the
  client had disconnected on the next yield (i.e. after a
  keepalive fired up to 15 seconds later). With waitress's 8-
  worker thread pool, eight rapid clicks pinned every thread on
  stale SSE generators and the server stopped answering new
  requests until cleanup eventually ran. Shortened the keepalive
  interval to 2 seconds so dead clients are detected within ~2
  seconds and worker threads return to the pool. v0.60.3's lazy
  payload hydration already fixed the browser-side cumulative
  cost; this fixes the server-side wedge.

## [0.60.4], 2026-06-21

Two Send fixes from the next walkthrough.

### Fixed

- **Send (Live preview)**: the right card was sitting ~16px
  below the left because the global
  ``.dx-section-card + .dx-section-card { margin-top: 16px }``
  sibling rule (added in v0.60.0 for stacked admin cards) was
  also firing inside the 2-column grid. Cancelled the margin
  scoped to ``.send-pair > .dx-section-card`` so both card
  tops align again.
- **Send (Live preview)**: the preview-frame was capped at 480px
  (from the v0.60.2 patch) which left obvious empty space on a
  page-wide layout. Lifted the cap entirely and bumped the
  column ratio to the spec's ``1fr 1.18fr`` so the preview
  grows to fill the available column width.

## [0.60.3], 2026-06-21

Events page performance + readability, Send alignment.

### Fixed

- **Events page lag after clicking through filter chips**:
  expanded payloads now lazily hydrate on first click. The JSON
  payload is stashed in a ``<script type="application/json"
  data-event-json>`` block per row; the in-house highlighter only
  walks + tokenises a row's JSON when the user actually opens it.
  Initial DOM is ~50× lighter for a 100-row telemetry view and
  the cumulative slowdown from clicking through chip filters is
  gone.
- **Events filter-chip active state readability**: the active
  chip used the per-type pastel pair (fg-on-bg) which was hard
  to read at small sizes. Flipped to a high-contrast pattern —
  the chip's saturated fg becomes the background and the text
  flips to white. Count pill stays visible via a translucent
  white tint.
- **Events Device chip**: gained its own colour swatch (rose)
  rather than the muted neutral that read as "no colour".
- **Send (Live preview) alignment**: the right card's section
  header sat lower than the left because ``.send-pair-preview``
  was a 2-row grid with a 12px gap between header and body.
  Reverted to plain block flow so the section_head sits at the
  same Y as the options card's header.

## [0.60.2], 2026-06-21

Three regressions from the v0.60.1 polish: search refresh, broken
Send preview, missing event-type colours.

### Fixed

- **Widgets Browse search**: typing no longer refreshes the page.
  Switched from a debounced auto-submit + sessionStorage focus
  restore to a client-side DOM filter — each card carries a
  ``data-search-haystack`` of its id + name + description +
  author + tags, and the JS filter toggles ``hidden`` on a plain
  ``indexOf`` match. Focus + caret stay put naturally. Submitting
  the form (Enter) still does a full GET so ``?q=`` URLs stay
  shareable, and ``_filter_entries`` server-side honours the
  query for that case + no-JS users.
- **Send (Live preview)**: the v0.60.1 flex + ``width: auto``
  override collapsed the preview-frame to zero width when its
  ``.dx-section-body`` flex container was ``align-items: center``.
  Reverted the flex shim and replaced it with a simple
  ``max-width: 480px`` on the preview-frame so a portrait panel
  ratio (1200×1600 → 3:4) caps at ~640px tall while keeping the
  frame's actual width visible.

### Changed

- **Events**: ``renderer`` + ``rotation`` event types now have
  their own icon swatches (blue and teal). The previous pass
  only added ``render`` and missed the real type strings emitted
  by the push pipeline + scheduler. Per-type swatches are now
  expressed as CSS custom properties (``--evt-fg/bg/bd``) so the
  row icon and the filter chip pick up the same colour from one
  source of truth.
- **Events filter chips**: each chip mirrors the swatch of the
  event type it filters by. Active state fills with the per-type
  background instead of the generic accent tint.

## [0.60.1], 2026-06-21

Second round of post-uplift polish from a live walkthrough.

### Changed

- **Inset background**: lightened the warm-taupe inset surface from
  ``#f6f5f1`` / ``#f3f2ee`` to ``#faf9f6`` so inset rows on
  Dashboards, History, Events, Plugins index, and the per-tab
  sub-cards on Settings sit closer to the section card surface
  with less visual weight.
- **Events**: per-type icon backgrounds gain distinct hues —
  render = blue, conditions = violet, heartbeat = danger,
  auth = mint, plugin = amber, transport/telemetry = teal,
  device = neutral — so the event type is recognisable at a
  glance without reading the pill.
- **Send (Live preview)**: the preview card now stretches to
  match the options card height (``align-items: stretch``) and
  the preview frame is clamped to ``min(70vh, 640px)`` so a
  portrait panel aspect ratio (1200×1600) no longer dwarfs the
  left options column. The pair reads as a balanced 2-up.
- **Widgets Browse**: search input preserves focus + caret
  position across the debounced auto-submit roundtrip via a
  sessionStorage marker — typing no longer loses focus mid-query.
- **Widgets Browse**: kind + tag chips moved off the legacy
  ``.pill`` vocabulary to a dedicated ``.dx-mkt-chip`` set
  matching the spec — outlined pill with a tiny count badge,
  filled accent tint on active.

### Fixed

- **Condition picker raw JSON**: the syntax highlighter was
  matching the HTML entity ``&quot;`` against escaped output,
  but quote characters weren't included in the escape map so the
  regex never hit a token — the overlay rendered as plain text.
  Switched to matching real quotes against the partially-escaped
  string, so keys / strings / numbers / keywords now colour in
  the rotation + schedule condition editors.

### Notes

- The Rotations + Schedules page-level card chrome is still on
  the legacy ``.rotation-card`` / ``.timeline-card`` shapes from
  before the uplift. A heavier rewrite to put them on the same
  ``section_card`` macro as the rest of v0.56 is parked as a
  follow-up; this release covers the spec-derived polish that
  the existing surface needed first.

## [0.60.0], 2026-06-21

Post-uplift polish from a live walkthrough of the v0.59.0 admin UI.
Six fixes across five pages plus a missing search input on Browse.

### Changed

- **Dashboards** (``/pages``): the inline "New dashboard" form and
  the saved list fuse into a single section card so there is no
  sibling gap between them. Saved dashboards now group under their
  bound device, alphabetical within each group, with an "Unbound"
  group at the end. Pages without an ``icon`` set fall back to
  ``ph-cube`` instead of ``ph-squares-four``.
- **Schedules** / Rotations / Settings tabs: a global
  ``.dx-section-card + .dx-section-card { margin-top: 16px; }``
  rule gives every page with stacked section cards consistent
  vertical rhythm without per-page CSS.
- **History** (``/history``): each push row gains a 64×64 square
  thumbnail on the left. Click opens the same lightbox as the
  timestamp link. Pushes with no stored render show a placeholder
  square so the layout stays aligned.
- **Battery dashboard** (``/devices/battery``): the 4-tile stat
  grid forces ``repeat(4, 1fr)`` so Samples / Drain rate / Reaches
  20% / Reaches 0% sit in one row at desktop widths; collapses to
  2×2 below 720px.
- **Events** (``/events``): expanded payload uses a two-column
  grid — raw JSON (or conditions block) fills the left column,
  the optional 120px render thumb sits on the right — so the
  expand region uses the full row width instead of stacking
  narrowly under the icon. Single-column fallback on narrow
  viewports.
- **Rotations** (``/rotations``): the per-step Conditions panel
  stays collapsed by default even when the saved step has
  conditions, matching the user's expectation that the form is
  empty until they click in.

### Added

- **Widgets Browse** (``/plugins/browse``): catalog search input
  per the design spec. Free-text matches case-insensitively on
  entry id, name, description, author, and tags. Debounced 300ms
  auto-submit so the URL stays shareable; ``?q=`` composes with
  the kind + tag chip filters.

## [0.59.0], 2026-06-21

Tier 4 (final) of the v0.56 admin-UI uplift. Onboarding and Themes
flip onto the section-card chrome, closing the four-tier arc.

### Changed

- **Onboarding** (``/onboarding``): the wizard step body card flips
  to ``.dx-section-card``. Every per-step "Back" link is dropped
  (per the spec: forward-only wizard with Skip as the escape
  hatch). The step-pip indicator + Skip setup link stay; pips were
  already polished in v0.54.1.
- **Themes** (``/themes``): the Update theme primary button swaps
  to ``.dx-btn-primary``. The bespoke 3-column layout + reactive
  live preview already track the form state, so the design's
  "preview updates as you edit" requirement was already met at
  v0.55 fidelity; no additional JS wiring needed.

### Notes

- Closes the 4-tier admin-UI uplift arc (v0.56.0 → v0.59.0).
  Every top-level admin page outside the composer + page editor
  is now on the shared ``.dx-section-card`` chrome + status pill +
  filter chip vocabulary.
- A page-level "the composer needs design love" follow-up is on
  the backlog; it has a custom interaction model that didn't fit
  the tier-based card uplift.

## [0.58.0], 2026-06-21

Tier 3 of the v0.56 admin-UI uplift. Schedules, Rotations, and the
System Settings sub-tab flip onto the section-card chrome.

### Changed

- **Schedules** (``/schedules``): Next 24 hours card, New schedule
  form, and Saved schedules table all wrapped in ``.dx-section-card``
  with the icon-in-teal-square headers + descriptions.
- **Rotations** (``/rotations``): existing-rotation cards + New
  rotation form lift onto the section-card chrome.
- **Settings → System** (``/settings/system``): every System tab
  card (Updates / Authentication / Backups / Webhook + auxiliary
  card--compact slots) converts to ``.dx-section-card``.

### Added

- Pragmatic CSS adapter for the legacy ``card_head`` macro: when a
  page wraps content in ``.dx-section-card`` but still calls
  ``card_head``, the existing ``<header class="card-head">`` markup
  re-styles to the v0.56 chrome (icon-in-teal-square + 15px/700
  title + meta + action). Lets Tier 3+ pages flip the outer card
  class without touching every ``card_head`` call.

### Notes

- The conditions builder partial originally scoped to Tier 3 was
  deferred: the existing ``condition-picker.js`` already drives the
  per-schedule + per-rotation-step builders and works at v0.55
  fidelity, so the surgical chrome conversion ships the visual win
  without rebuilding the picker. Picker UI overhaul can land as a
  follow-up.
- Tier 4 (Onboarding + Themes) still ahead.

## [0.57.0], 2026-06-21

Tier 2 of the v0.56 admin-UI uplift. Send, Events, and Widgets
Browse adopt the shared section-card chrome + filter chips +
inset-row vocabulary.

### Changed

- **Send** (``/send``): surgical chrome conversion. Tab strip kept;
  every per-tab card (File / URL / Webpage / Gallery / Live preview)
  now uses the ``.dx-section-card`` + ``.dx-section-head`` pattern
  with the icon-in-teal-square header. Push buttons swap to
  ``.dx-btn-primary``.
- **Events** (``/events``): new chrome — page-level filter chip
  strip (All / push / render / heartbeat / schedule / auth /
  plugin / transport / telemetry / conditions) + single section
  card with LISTENING indicator. Every event row becomes a
  click-to-expand button; the expanded body shows the raw JSON
  payload through the in-house JSON highlighter
  (``static/pages/json-highlight.js``). Rich conditions decision
  view (rotation steps, schedule outcome) preserved verbatim.
- **Widgets Browse** (``/plugins/browse``): outer card chrome
  flips to ``.dx-section-card``; Install / Update buttons swap to
  ``.dx-btn-primary``. The bespoke ``marketplace-card`` grid +
  screenshot carousel stays as-is.

### Added

- New ``.dx-event-*`` CSS bundle (event row + summary button +
  expand body + caret + type-specific icon tones).
- ``.dx-filter-strip`` + ``.dx-filter-chip`` reused across History,
  Events, and Battery dashboard so the three pages read as one
  filter-chip vocabulary.

### Notes

- The page-level click-to-expand on Events is the first user of
  PR 0's in-house JSON highlighter; ``<pre class="dx-code"
  data-json>`` blocks auto-highlight on DOMContentLoaded.
- Tier 3 (Schedules + Rotations + System Settings) lands the
  conditions builder partial as a new shared component.

## [0.56.0], 2026-06-21

First tier of the v0.56 admin-UI uplift onto the v0.54 ``.dx-*``
design system. Four list / dashboard pages flip onto the shared
section-card chrome + inset rows + status pill vocabulary.

### Changed

- **Dashboards** (``/pages``): flat saved-list inside a single
  section card, inline "New dashboard" name + create row, meta strip
  per dashboard (Size / Cells / Last pushed), Edit / Push / Delete
  inline. Drops the device-grouping accordion.
- **History** (``/history``): filter chip strip across the top,
  inset row per push with mono timestamp + device chip + colour-coded
  source pill + status pill + duration + Resend / Delete icon
  actions. Failed rows pick up the danger soft-band background.
- **Widgets index** (``/plugins``): per-kind section cards (Widget /
  Data / Font / etc.) with count chip + per-row icon + name + id +
  description + admin badge + "Open admin" ghost action.
- **Battery dashboard** (``/devices/battery``): page-level window
  picker as the v0.56 filter strip, single outer section card, per-
  device inset cards with name + mono id + Chart.js drain curve + the
  4-tile stat grid (Samples / Drain rate / Reaches 20% / Reaches 0%).
  Chart.js kept (per project decision).

### Added — shared infrastructure (PR 0 of the uplift)

- ``static/style/base.css`` gains the v0.56 token block
  (``--t-inset``, ``--t-accent-tint``, ``--t-pill-{ok,warn,danger,
  neutral}-*``, ``--t-code-{bg,border,fg,key,str,num,kw,punct}``) +
  dark-mode equivalents.
- ``section_card`` Jinja macro in ``templates/_components.html`` —
  ``{% call section_card(icon, title, description, cta, meta, id) %}``
  wraps the canonical chrome from v0.54 (icon-in-teal-square + title
  + description + body) so every page composes via one call.
- ``static/pages/json-highlight.js`` — in-house tokenizer (~50
  lines, no deps) that auto-highlights every ``<pre class="dx-code"
  data-json>`` block on the page; importable via
  ``highlightJson(str)`` for on-the-fly use.
- ``.dx-pill`` (status chip) + ``.dx-dow-pill`` (day-of-week chip) +
  ``.dx-input`` (input baseline) + ``.dx-inset-row`` +
  ``.dx-code`` (with syntax-highlight overlay) + ``.dx-meta-strip``
  + ``.dx-disclosure`` — shared primitives for every uplifted page.
- ``EventLog.last_event_by_target()`` — one-roundtrip MAX(timestamp)-
  per-target query used by the Dashboards list for "last pushed".
- ``History`` formatter now resolves ``target_devices`` (device
  name + icon) per push row so the device chip on the history row
  has real content.

### Notes

- Tier 1 of a 4-tier arc; Tiers 2-4 add the remaining pages
  (Send + Events + Widgets Browse, Schedules + Rotations + System,
  Onboarding + Themes).
- Battery prediction lands now even on devices with a mid-window
  recharge thanks to v0.55.1's segment regression: the bedside ESP32
  in the screenshot fixture surfaces ``-5.5 %/day`` + ``Reaches 20%
  14.5 days`` + ``Reaches 0% 18.2 days``.

## [0.55.1], 2026-06-21

### Fixed

- **Deleted devices kept lingering on /devices/battery + the
  live status cache.** ``devices_delete`` already purged the
  smart-sync telemetry but not the battery history or the
  ``DEVICE_STATUS`` dict; the battery dashboard intentionally
  surfaces devices with stored history (so an offline-but-
  registered device still gets a card), which re-rendered the
  dead device. Adds the missing
  ``battery_history.forget(instance_id)`` + ``status_cache.pop()``
  calls + a regression test.

### Changed

- Per-instance device cards on Settings → Devices now collapse
  by default. Calibration-mid cards still open by default
  (the "which number is in the top-left" form needs to be
  visible without an extra click).
- "Built-in device kinds" card (the #22 prototype) is no
  longer rendered. The KindOverridesStore + routes + partial
  remain in the repo, ready for a future revisit once an
  admin workflow that's clearly better than editing
  ``devices/<kind>/device.json`` directly emerges.
- Discovered strip + unified Add device card moved INSIDE the
  flex container so they share the 16px gap with the per-
  instance cards (previously they sat as siblings to the
  section loop and had no inter-card gap).

### Added

- ``scripts/capture_ui_uplift_screenshots.py`` — playwright-
  driven capture for the UI uplift design handoff bundle.
  Boots an isolated testing-mode Tesserae on a free port, seeds
  a populated fleet (two devices, two pages, an interval
  schedule with HA/Sun conditions, a two-step rotation), and
  walks 15 routes including New-schedule-form + edit-form-with-
  conditions-open variants for Schedules and Rotations.

## [0.55.0], 2026-06-21

Settings → Devices tab adopts the v0.54 design system end-to-end,
closing the remaining UX backlog from the v0.54 handoff
(issues #16, #17, #22).

### Changed

- **#17 — Discovered strip splits by transport.** The single
  homogeneous list becomes a section card with two transport-
  grouped sub-strips: REST-announced (auto-claim on register)
  first, then MQTT-discovered. Each sub-group carries a count
  pill + a one-line explainer; empty groups vanish and the
  radar empty state takes over when both are empty. Row layout
  is identical across groups so the distinction is structural,
  not buried in a single pill.
- **#16 — Unified Add device card.** "Add device" + "Pair new
  device (REST)" + the standalone Pending codes table collapse
  into one ``.dx-section-card`` with a transport segmented
  control at the top (REST-default). Both branches stay in the
  DOM so typed values are preserved across flips. The REST
  branch surfaces pending codes inline; the MQTT branch shows
  a warning band linking to Server → MQTT broker when
  ``broker.host`` isn't set. Pair-code reveal moves from an
  inline block to a modal that shares its shell with the TRMNL
  token reveal so the two reveal flows look + behave the same.
- **#22 — Built-in device kinds card (new).** Adds an editable
  defaults layer per built-in kind under
  ``data/devices/_kind_overrides/<kind_id>.json``. UI is a
  collapsible row per kind with the editable fields
  (display-name default, panel preset, custom W/H, default
  rotation, default sleep interval) and an inline confirm bar
  for the Reset action. Override applied at load time so
  subsequent ``create_instance`` reads see the new defaults.

### Added

- ``app.state.kind_overrides.KindOverridesStore`` — JSON-per-
  kind store with a five-field whitelist + per-field coercion;
  empty saves remove the file (revert to bundled defaults).
- ``app.device_loader._apply_kind_override`` — merge helper
  that maps the override into the manifest's panel block + a
  pair of top-level default keys
  (``display_name_default`` / ``sleep_interval_s_default``).
- ``POST /settings/devices/kinds/<kind_id>/defaults`` +
  ``POST /settings/devices/kinds/<kind_id>/reset`` — save /
  reset endpoints with EventLog audit rows.
- ``scripts/capture_ux_screenshots.py`` — playwright
  capture script that boots an isolated testing-mode Tesserae
  on a free port, pre-populates the discovered + registered
  fixtures, and writes current-state PNGs into
  ``notes/design-handoffs/ux-backlog/reference/current-state/``.

### Notes

- Plugin-defined kinds aren't currently surfaced in the kinds
  card; the view-model carries a reserved ``plugin_source``
  column for the future. Out of scope per the handoff brief.
- The pair-reveal previously used a session-stashed inline
  block at the top of the Pair card. That block is gone; the
  same session key now drives a modal that matches the
  TRMNL token reveal's shape (close + copy + Done button).

## [0.54.3], 2026-06-21

### Fixed

- **Dark-mode support for the redesigned cards.** The new
  ``.dx-device-card`` + ``.dx-section-card`` rules hard-coded light-
  mode hex values for backgrounds, borders, and text, so under
  ``<html data-theme="dark">`` they stayed white-on-dark and read
  as broken. Added a ``:root[data-theme="dark"]`` override block
  that maps every dx-* colour onto the existing slate ``--t-*``
  palette declared in ``base.css``. Smart Sync band keeps its warm
  tones (status indicator), the save bar stays dark (intentionally
  always dark), and the teal accent flips to its dark-mode value.

## [0.54.2], 2026-06-21

### Fixed

- CI mypy strict check rejected ``_humanize_signal`` in
  ``app.settings.index_routes``: the ``int(rssi)  # type:
  ignore[arg-type]`` line had an unused type-ignore (the failure was
  a ``call-overload``, not an ``arg-type``) AND the underlying call
  was still wrong because ``int(object)`` isn't a valid overload.
  Switched to an ``isinstance(rssi, (int, float, str))`` narrow
  before the ``int()`` call, so the type-checker sees a real branch
  with no escape hatch. Runtime behaviour unchanged.

## [0.54.1], 2026-06-21

### Changed

- **Settings → Server: handoff redesign for the App fields.** Single
  "App" card replaced with seven grouped section cards (Network &
  integrations, Location & time, Quiet hours, Low-battery warnings,
  Display & performance, Widget marketplace, Privacy). Quiet hours
  + Low-battery carry their master toggle in the section header and
  dim the dependent controls below when off. The Network card pins
  a read-only ``NETWORK IP`` chip to its header. Sticky save bar
  matches the device-card pattern.
- **MQTT broker + Virtual panel cards adopt the same section-card
  pattern.** Icon-in-header + title + description + switches as
  full-row toggle rows + the same sticky save bar. The legacy
  external-vs-embedded broker show/hide JS still fires, just on the
  restyled markup.
- **The dx-section-card pattern is applied globally to Renderers and
  Widgets too**, so every settings tab shares one visual treatment.
  Side-effect: ``.dx-*`` rules are no longer scoped to
  ``.dx-server-area``, so plugin/renderer cards on the Widgets/
  Renderers tabs also get the teal icon-in-square header.
- **Description-text colours unified across the app.** ``.lede``
  steps to 13.5px / ``#7a7a74`` (the handoff page-subtitle
  treatment); ``.field-help`` steps to 12px / ``#9a9a93`` (matching
  the new ``.dx-toggle-row-desc``). All four description classes
  now share one colour family with a size hierarchy.

### Fixed

- ``settings_tabs`` Events tab labelled "Events" with the pulse
  icon (was incorrectly rendering as a second "Settings" h1 with
  the gear icon).
- ``rotations.html`` page header gains the arrows-clockwise icon
  and switches from ``<p class="muted">`` to ``<p class="lede">``
  so it matches every other top-level page.
- Stray "No system sections yet." paragraph removed from the
  System tab (the loop is intentionally empty there).
- Gap below the settings tabs is now identical on every sub-page.
  ``.settings-stack { margin-top }`` + ``.dx-server-cards
  { margin-top }`` were stacking with the tab bar's own bottom
  margin (32px instead of 16px on System / Server).

## [0.54.0], 2026-06-20

### Changed

- **Settings → Devices: per-device card redesign.** Each device card
  now opens to a tabbed layout (Status / General / Rendering /
  Schedule) instead of one long scroll. Status replaces the raw
  diagnostics dict with three humanized tiles, signal bars + dBm
  reading, mains-or-percent power label, firmware + IP, plus a
  Smart Sync panel with a confidence meter and a plain-English
  explainer. Editable controls live on General + Rendering; the
  Schedule tab pulls the per-device timetable. A sticky save bar
  reveals only when the form is dirty and animates in / out;
  Discard resets every field to its initial value.
- **Connection details disclosure.** Renderer id, instance-of,
  server URL, and the access token (with a Reveal button on REST
  devices) now collapse behind a "Connection details" disclosure
  at the top of the card instead of always taking up meta-block
  space. Transport flip moves into this disclosure too, so it sits
  next to the current transport label and confirms before flipping
  (issue #19).
- **Reveal full token (issue #20).** Admins who closed the
  one-shot reveal modal previously had to ``cat`` the on-disk
  manifest to recover the token; a "Reveal" affordance on the
  Connection details strip now POSTs ``/settings/devices/<id>/
  reveal-token`` (with explicit confirmation), stashes the token
  in the session reveal slot, and logs the reveal to the
  EventLog for audit.
- **Dormant MQTT meta hidden on REST devices (issue #21).** REST
  instances no longer surface the dormant ``status_topic`` /
  ``config_topic`` rows in the meta block; they keep on the
  manifest so a flip back to MQTT remains one click.
- **Server tab visual restyling.** Each card on Settings → Server
  picks up the handoff redesign's surface treatment (white card,
  ``border-radius: 12px``, ``0 1px 2px`` shadow, 22px padding) so
  the Server tab reads with the same visual hierarchy as the
  redesigned device card. Field-set grouping into 7 named
  sections is intentionally deferred.

### Added

- **Status humanization helpers** (``_humanize_signal``,
  ``_humanize_power``, ``_humanize_firmware``, ``_status_tiles``,
  ``_smart_sync_header``, ``_reported_panel_hint``) on the
  Settings index walker, with unit tests. Mains devices (Pi /
  ESP32 dev boards) now read as ``Mains · No battery`` instead
  of ``0 mV / 0%`` (which looked like a dead battery), and the
  Rendering tab carries a reconcile hint when the device-reported
  panel dims are swapped relative to the edit form because of a
  90°/270° rotation.
- **``static/pages/settings.js`` controller** (no framework, no
  build) wiring tab switching with querystring persistence, dirty
  tracking + sticky save bar, dependent-field dimming for the
  quiet-hours override, and a collapse toggle.

### Notes

- Device-card data shape on the section dict gains four fields
  (``connection_details``, ``transport_badge``,
  ``reveal_token_endpoint``, plus humanized fields under
  ``status``); the existing ``meta`` dict + the per-field branches
  in the legacy template stay in place so any out-of-tree callers
  rendering the old shape keep working.
- Backlog issues #16 (unify add forms), #17 (split Discovered
  strip), #18 (the device card restructure that landed here), #19
  (transport flip), #20 (token reveal), #21 (dormant MQTT meta),
  and #22 (per-kind defaults overrides) tracked the cleanup. #18
  through #21 are addressed in this release; #16, #17, and #22
  remain open.

## [0.53.2], 2026-06-20

### Fixed

- **``GET /send`` returned 500 when any device's panel had
  ``w == 0`` or ``h == 0``.** ``device_panel(dev)`` builds a
  Pydantic ``Panel`` which validates ``w > 0`` and ``h > 0``, and
  ``send_routes._device_options`` iterates every registered
  instance and calls it without a try/except, so a single corrupted
  device 500'd the whole page. After the fix the bad device is
  skipped with a warning log and the rest of the fleet remains
  pickable.
- **The discover-and-claim flow no longer registers instances with a
  zero panel.** Firmware that reports ``panel_w: 0`` / ``panel_h: 0``
  in a ``/api/v1/device/discover`` POST (a default-int from a C
  struct that wasn't populated) now falls back to the kind's
  default panel instead of corrupting the instance. Fix lives in
  both ``app.settings.devices_routes.devices_register_discovered``
  and ``app.onboarding.register_discovered``.

### Notes

- An existing instance with ``panel: {w: 0, h: 0}`` keeps showing up
  in Settings → Devices (so the admin can fix it via Panel form) but
  is now skipped on /send so the page works again. Fixing the bad
  instance via the admin UI's Panel form (pick a preset or type the
  real dims) restores it as a send target.

## [0.53.1], 2026-06-20

### Fixed

- **REST device "last seen" stuck at epoch 0.** The v0.52 REST status
  handler wrote a flat dict ``{... "last_seen": ts, "transport":
  "rest"}`` to ``DEVICE_STATUS``, but the Devices admin page's
  ``_status_view`` reads ``cache["received_at"]`` and
  ``cache["parsed"]`` (the shape the MQTT path uses). The mismatched
  field names meant REST device freshness always showed "20624 days
  ago" (now - 0) and the diagnostic-fields dl rendered empty.
- **REST devices missing from smart-sync telemetry and battery
  history.** The MQTT status path records to
  ``DEVICE_TELEMETRY`` (issue #10) and ``BATTERY_HISTORY`` on every
  heartbeat; the v0.52 REST path skipped both, so REST devices never
  appeared on the device_battery widget and the scheduler had no
  wake-prediction data for them.

### Changed

- ``app/transport_wiring.py`` gains a public ``record_status_heartbeat``
  helper. Both the MQTT subscribe callback in
  ``_subscribe_device_status`` and the REST ``POST /<id>/status`` route
  call this helper, so the live status cache update + telemetry +
  battery history + EventLog row + HA discovery notify all stay in one
  place. A future third transport can't drift the way the initial REST
  handler did.

### Notes

- The pre-fix REST cache records (with ``last_seen``) are simply
  overwritten on the next heartbeat with the correct
  ``{received_at, parsed}`` shape; no migration needed.
- Tests:
  - ``test_rest_status_updates_received_at_so_last_seen_is_fresh``
    pins the field contract.
  - ``test_rest_status_records_battery_history`` proves the
    BATTERY_HISTORY side effect runs.

## [0.53.0], 2026-06-20

### Added

- **Discover-then-claim flow for REST devices: zero typing on the
  firmware side.** New devices auto-register without pairing codes:
  firmware POSTs ``/api/v1/device/discover`` with its proposed
  device_id + kind + MAC; the entry appears in the Settings ->
  Devices Discovered strip; admin clicks Register on the card; the
  resulting instance carries the captured MAC + ``transport: "rest"``;
  the firmware's next discover POST matches by MAC and receives its
  ``device_token`` + ``config``. No pairing code typed, no flashed
  credentials. Mirrors the MQTT discovery UX (heartbeat -> admin
  clicks Register) but without needing a broker.
- ``POST /api/v1/device/discover`` extended with the MAC-match
  claim path. Response shapes:
  - ``{registered: true, device_token, device_id, config,
    server_time}`` when the MAC matches a registered instance.
  - ``{registered: false, discovered: true, retry_after_s, next_step}``
    otherwise. Firmware sleeps and retries.
- DiscoveryCache entries carry a ``transport: "rest"`` hint on the
  parsed payload when sourced from ``/discover``, so the admin's
  Register click creates an instance with the right transport (and
  the MAC) without further input.
- Settings -> Devices Discovered strip shows a green
  "REST, auto-claim on register" pill on REST-sourced entries so
  the admin can tell the new flow apart from the legacy MQTT
  discovery on the same strip.
- ``devices_register_discovered`` propagates the cached MAC +
  transport hint through to ``create_instance`` so the
  resulting REST instance is immediately claimable by the
  matching firmware.

### Notes

- The pairing-code flow (``/api/v1/device/register`` with
  ``X-Pairing-Code``) stays supported for users who want admin-
  driven gating before any instance is created. The discover-claim
  flow is the friendlier default; pairing is the stricter option.
- MAC matching is case- and separator-insensitive
  (``aa:bb:cc:dd:ee:ff`` matches ``AABBCCDDEEFF`` matches
  ``aabb-ccdd-eeff``).
- MAC is not a secret (it's on the wire), but the security boundary
  is the admin's deliberate Register click. The rate limiter on
  ``/discover`` shields against a misconfigured firmware spamming
  the cache.

## [0.52.5], 2026-06-20

### Fixed

- **CI red on the v0.52.2 onboarding-transport tests.** My new fixture
  in ``tests/test_onboarding_transport.py`` used
  ``create_app(testing=False, ...)`` which triggers the embedded amqtt
  broker startup when the MQTT-path test posts ``use_builtin=on`` and
  ``save_broker`` calls ``_rebuild_transport()``. Locally amqtt starts
  in <1s; the CI runner can't bind 1883 in time and the broker thread
  raises a ``RuntimeError: embedded broker did not become ready within
  5.0s``, which pytest surfaces as an unhandled-thread-exception
  warning that fails the suite.
  Fixed by switching the fixture to ``create_app(testing=True, ...)``
  matching the pre-existing ``tests/test_onboarding.py`` pattern.
  ``testing=True`` skips the embedded-broker startup the same way it
  does for the existing MQTT tests. The wire-shape persistence (the
  actual assertion target of these tests) is what they're testing,
  not the broker side.

## [0.52.4], 2026-06-20

### Fixed

- **Onboarding step pip showed an empty circle on completed-but-not-
  current steps.** The icon classname was ``ph-fill ph-fill-check-
  circle``; Phosphor uses ``ph-fill`` for the weight and
  ``ph-<icon>`` for the glyph, so ``ph-fill-check-circle`` resolved
  to no CSS rule and the ``<i>`` rendered empty. The branched-out
  digit (the step number) was also hidden, so completed steps in
  the progress bar appeared as blank circles. Fix is a one-word
  classname correction.
- **Onboarding welcome copy still listed "Broker" as step 2.**
  v0.52.2 reframed that step as a transport choice with REST as
  the default; the welcome overview and the progress-pip label
  were missed in that pass. Now reads "Transport" everywhere,
  with the inline copy explaining REST (no broker) vs MQTT.
  Wizard URL stays ``/onboarding/broker`` for backward
  compatibility with bookmarks and the ``save_broker`` route.

## [0.52.3], 2026-06-20

### Added

- **Phase 1c: ``transports/<id>/`` drop-a-folder discovery surface.**
  New ``transports/mqtt/transport.json`` + ``transports/rest/transport.json``
  metadata manifests + ``app/transport_loader.py`` that walks the dir
  at startup and exposes a ``TransportRegistry`` under
  ``app.config["TRANSPORT_REGISTRY"]``. Mirrors the pattern of
  renderers/ and devices/. The MQTT and REST implementations stay
  where they live (app/transport.py + app/transport_wiring.py +
  app/embedded_broker.py for MQTT; app/rest_api.py for REST); the
  loader is metadata + visibility, not a rewrite. Future third
  transports (WebSocket, gRPC, MQTT 5) can be added by dropping a
  folder and landing their implementation, no manifest field needs
  threading through five places. ``schema/transport.schema.json``
  validates manifests.
- **Per-device transport flip (MQTT ↔ REST).** New form on each
  device card on Settings → Devices: "Switch to REST" or
  "Switch to MQTT". Backed by ``POST /settings/devices/<id>/set-
  transport``. Flip preserves the device's id, panel settings, and
  per-clone renderer settings. MQTT → REST mints (or reuses) an
  access token and shows it in the one-shot reveal modal so the
  user can copy it into firmware. REST → MQTT drops the transport
  field; the token stays so flipping back is one click.
- **Transport column on the Devices area.** Each device card's
  meta block now shows the device's transport explicitly ("Transport:
  MQTT" / "Transport: REST" / "Transport: HTTP polling"). REST
  devices show the first 4 chars of their access token + "..." (so
  a screenshot of Settings → Devices doesn't leak the full token).
- **Rate limit on ``POST /api/v1/device/register``.** 10 failed
  attempts per client IP per 60s window; successful registrations
  release the bucket so a user pairing several devices in a row
  isn't penalised. 6-digit codes have only ~20 bits of entropy;
  this caps brute force at <1 attempt/minute averaged. Sliding
  window, in-memory, lives in ``app/state/rate_limiter.py``.
  Returns 429 + ``Retry-After`` header when exceeded.
- **``POST /api/v1/device/discover``.** Unauthenticated announce
  endpoint for firmware that booted but doesn't yet have a pairing
  code. Adds the firmware to the existing ``DiscoveryCache``; it
  shows up in the Settings → Devices Discovered strip alongside
  MQTT-discovered devices. Shares the register endpoint's rate
  limiter to prevent Discovered-strip spam.
- **Public docs for the REST transport.**
  ``docs/install/rest-transport.md`` covers the full end-to-end
  flow: when to pick REST vs MQTT, the pairing UI walkthrough, the
  endpoint reference, transport-flip semantics, security notes, and
  migration tips. Linked from ``docs/install/server.md`` and the
  mkdocs nav. The firmware prompts in ``notes/prompts/`` are
  referenced as the next step for porting existing firmware.

### Changed

- ``app/settings/index_routes.py``'s ``_device_meta_block`` branches
  on ``Device.transport == "rest"`` first, then the legacy TRMNL
  ``access_token``-on-instance signal, then defaults to MQTT.

### Notes

- The "drop-a-folder" pattern for transports is intentionally
  metadata-only. MQTT and REST have fundamentally different shapes
  (push vs pull, persistent connection vs HTTP request, broker-
  mediated vs direct). Forcing a common Transport ABC on them
  would be a fiction that obscures more than it reveals. The
  loader surfaces capabilities + identity; each transport's
  actual wiring stays where it makes sense in the app.
- Existing MQTT installs see zero behaviour change. The new
  endpoints, rate limiter, and UI controls are additive.

## [0.52.2], 2026-06-20

### Changed

- **REST transport Phase 2: REST is now the default for new
  installs.** Fresh installs no longer hit the broker setup detour
  on first boot.
- **Onboarding wizard reframes the broker step as a transport
  choice.** New top-level radio: REST (recommended, no broker
  needed) vs MQTT (broker required). REST is checked by default.
  Picking REST persists ``app.default_transport = "rest"`` and
  skips the broker save entirely; picking MQTT keeps the existing
  built-in / external broker flow. The wizard URL stays at
  ``/onboarding/broker`` for stability; the step's heading reads
  "Pick a transport" now.
- **Onboarding device step branches by chosen transport.** REST
  users see a Pair card inline (issue + show + revoke 6-digit
  pairing codes, same store the Settings -> Devices Pair card
  uses) instead of the classic MQTT discovery + add-device form.
  MQTT users see the existing flow unchanged.
- **``is_onboarded`` recognises a REST install as onboarded.**
  Without this, a REST user who finished the wizard would get the
  wizard again on the next visit (the legacy "has broker host?"
  signal never fires for REST users). Now ``app.default_transport``
  being set is the same signal.
- **New ``app.default_transport`` setting** under Settings -> App.
  Default ``rest``; pickable as ``mqtt`` for users who want MQTT
  as the new-device default after onboarding.

### Notes

- Existing MQTT installs see zero behaviour change. The wizard
  only runs on installs that aren't already considered onboarded;
  a real install with a broker host or a registered device skips
  the wizard entirely.
- The bundled embedded amqtt broker stays in tree and stays
  available, just no longer auto-enabled by the wizard's default
  path.
- Phase 1c (the ``transports/<id>/`` drop-a-folder loader
  refactor) deferred indefinitely. Pure infrastructure churn with
  no user-visible payoff until a third transport actually arrives;
  not worth the refactor cost now.

## [0.52.1], 2026-06-20

### Added

- **REST transport Phase 1b: per-device transport selection +
  Pair card UI.**
- **``Device.transport`` field on instance manifests.** New optional
  ``"transport": "rest"`` key, default ``"mqtt"`` for any pre-0.52
  instance (no rewrite needed on upgrade). ``Device.transport``
  property reads it; ``device_loader.load_instance_file`` propagates
  the field through the kind-manifest merge so a REST-mode
  instance keeps its transport choice across restarts.
  ``create_instance`` accepts the field as a kwarg, persists it on
  the manifest, and automatically mints an ``access_token`` for
  ``transport="rest"`` (with ``"native"`` strength: 20-char
  alphanumeric, stored in firmware flash, never hand-typed).
- **Push pipeline skips MQTT publish for REST devices.**
  ``PushManager._renderer_is_http_polled`` now returns True for
  either a kind with no ``status_topic`` (the legacy TRMNL signal)
  OR a per-instance ``transport == "rest"`` (the new v0.52 signal).
  Same kind can have MQTT instances AND REST instances; the
  transport field on each instance decides whether a publish runs.
- **REST ``POST /api/v1/device/register`` automatically tags new
  instances as ``transport: "rest"``.** Devices that arrive via the
  pairing-code flow are REST-mode from creation; no broker calls
  ever happen for them.
- **Settings -> Devices: Pair card.** New section sits next to
  Add device (which stays the MQTT manual path). Generates a
  6-digit code via the existing ``PairingStore``, shows a copy-
  friendly reveal, lists pending codes with their remaining TTL +
  the user's note, and lets the admin revoke any code mid-flight.
  POST endpoints:
  - ``POST /settings/devices/pair`` (issue)
  - ``POST /settings/devices/pair/<code>/revoke``
  Both session-gated. The ``/api/v1/device/admin/pairing/*`` JSON
  endpoints stay too, useful for curl-from-terminal testing and
  any future scripted provisioning.

### Notes

- Existing MQTT instances and existing MQTT clients keep working
  unchanged. The "transport" field is opt-in; missing field reads
  as ``"mqtt"`` everywhere.
- Phase 1c remaining: drop-a-folder ``transports/<id>/`` loader
  refactor that pulls the existing MQTT path + new REST path under
  one loader, mirroring renderers/ and devices/. That's a pure
  restructuring step for future-transport extensibility; no user-
  visible change.
- Phase 2 (default-to-REST onboarding + bundled-amqtt-not-auto-
  enabled) still to come.

## [0.52.0], 2026-06-20

### Added

- **REST transport, Phase 1: ``/api/v1/device/*`` endpoints landed
  alongside MQTT.** Background: amqtt 0.11.x has reliability issues
  for retained-message delivery, and the Mosquitto alternative is
  high-friction for new users (install service, edit config, generate
  creds, paste them into every firmware). The new REST transport
  removes the broker from the new-install path entirely; existing
  MQTT setups keep working unchanged. See
  ``notes/rest-transport-design.md`` for the full scoping.
- **Endpoints** (all auth via per-device ``Authorization: Bearer
  <token>``; same primitive TRMNL devices use):
  - ``GET /api/v1/device/<id>/frame``: returns the latest rendered
    frame's URL + format + panel dims + render id. ``ETag`` header
    carries the render digest; firmware sends ``If-None-Match`` on
    subsequent wakes and gets ``304 Not Modified`` when nothing
    changed (skip fetch + paint = save battery on Spectra 6 panels).
    ``204`` when no frame has been rendered for the device yet.
  - ``POST /api/v1/device/<id>/status``: heartbeat body parsed via
    the device kind's existing ``parse_status`` (same hook the MQTT
    path uses) and merged into the live ``DEVICE_STATUS`` cache, so
    Settings -> Devices shows REST-mode device freshness uniformly
    with MQTT-mode. Response piggybacks the current per-device
    config and a ``next_poll_s`` field telling the firmware when to
    wake again. One round-trip per wake; no separate config poll
    needed.
  - ``POST /api/v1/device/register``: first-boot pairing. Firmware
    presents an ``X-Pairing-Code`` header (the 6-digit code the
    admin generated via PairingStore.issue), the body declares the
    chosen device id + kind + panel dims, the server creates the
    instance and returns a per-device ``device_token``. Idempotent
    on the device-id-already-exists case (firmware retries get the
    existing token, not a duplicate). Single-use codes with 10-min
    TTL, in-memory only.
  - ``POST /api/v1/device/<id>/log``: optional client-side log
    line, appended to the EventLog so the Events page surfaces
    firmware diagnostics alongside server events.
- ``app/state/pairing_store.py``: thread-safe pairing-code store
  with TTL + single-use + constant-time compare. Pluggable in the
  app config under ``PAIRING_STORE``.
- ``app/rest_api.py``: the endpoint module. Mirrors
  ``app/trmnl_api.py``'s structure (auth helpers, registry lookups)
  but kind-agnostic so any device kind can be served over REST.

### Notes

- This is Phase 1 (REST beside MQTT, both transports active for every
  device). Phase 1b will decouple the push pipeline so a REST-only
  device skips the MQTT publish. Phase 2 flips the default in
  onboarding. See ``notes/rest-transport-design.md`` and the per-
  firmware prompts in ``notes/prompts/``.
- The Devices admin UI still issues pairing codes via the existing
  TRMNL token machinery for now; a dedicated "Pair new device"
  button on Settings -> Devices that wraps ``PairingStore.issue``
  is a small follow-up.

## [0.51.9], 2026-06-20

### Added

- **``scripts/install-systemd.sh``: optional follow-up to ``install.sh``
  that wires Tesserae as a systemd service on Linux** so it survives
  reboots + restarts on crash. Refuses on non-Linux / non-systemd
  platforms (macOS gets launchd separately). Generates the unit file
  from the install dir + port + current user, ``sudo`` installs to
  ``/etc/systemd/system/tesserae.service``, enables it (auto-start on
  reboot), and starts it now. Idempotent: re-running prompts before
  overwriting an existing unit. Env-var overrides for unattended
  installs: ``TESSERAE_DIR``, ``TESSERAE_PORT``,
  ``TESSERAE_SERVICE_NAME`` (rename for parallel installs),
  ``TESSERAE_USER``, ``NONINTERACTIVE=1``. ``install.sh`` now points
  Linux users at this script in its Done message, and
  ``docs/install/server.md`` has a "Run as a service (Linux)"
  section with the common ``systemctl``/``journalctl`` recipes.

## [0.51.8], 2026-06-20

### Fixed

- **Send -> Webpage no longer renders external URLs as a blank page**
  (most visibly with JS-driven SPAs, but the underlying defect cost
  every external URL a ~15s wait). Two compounding bugs:
  1. ``app/renderer.py``'s ``_screenshot_attempt`` waited up to 15 s
     for ``window.__tesseraeComposed === true`` on every render. That
     flag is set by composer.js after every dashboard cell mounts; on
     an external URL it never fires, so the wait always burned the
     full 15 s before falling through. Cost: every Send -> Webpage
     push waited an extra 15 s for nothing.
  2. The goto used ``wait_until="load"`` for every render (the
     composer-tuned default), which fires before SPAs have hydrated.
     For Reddit-style React-shell sites the screenshot captured an
     empty shell. The original ``networkidle`` choice was abandoned
     because composer renders stalled at it.
  ``RenderRequest`` gains an ``is_composer`` flag, default True for
  the composition path, set False by ``Push.push_webpage``. When
  False the renderer skips the composer-mount wait and uses a hybrid
  wait strategy: ``goto`` on ``load`` so ad-heavy pages don't hard-
  fail at navigation, then a best-effort 8 s
  ``wait_for_load_state("networkidle")`` so SPAs get time to
  hydrate. Sites whose networks never idle (analytics-heavy news
  sites) hit the wait_for timeout and we screenshot what's painted.
- **Caveat (not fixed)**: Reddit specifically still renders as a
  near-blank page because Cloudflare's bot gate serves an empty
  "You've been blocked by network security" page to Playwright,
  regardless of wait strategy. That's a server-side block on their
  side; routing around it needs either a stealth-flavoured browser
  build or RSS-style fetch path, which is a much larger change.

## [0.51.7], 2026-06-18

### Added

- **New device kind ``pico_bin_client`` + renderer ``pico_bin`` for the
  battery-powered Pico Plus 2 firmware** (``tesserae-device-pico-bin``,
  in development) that drives a Pimoroni Inky-style Spectra 6 panel
  over SPI. The split exists because neither existing kind matched the
  new firmware's needs: ``pi_bin`` packs landscape-native (correct) but
  publishes non-retained, and a deep-sleep client that just woke up
  would miss the current frame on first wake. ``esp32_bin`` retains
  (correct) but packs portrait-native (wrong for the Inky-library-style
  on-device rotation the Pico firmware does). ``pico_bin`` is byte-
  identical to ``pi_bin`` for the same input (content-addressed disk
  storage shares one file when both targets are active), but flips
  ``retain: true`` so freshly-woken clients see the current frame.
  ``pico_bin_client`` inherits ``esp32_client``'s ``sleep_interval_s``
  config schema + heartbeat contract (battery_mv / battery_pct / rssi /
  ip / sleep_until / next_sleep_s). Default panel is the Inky
  Impression 13.3" Spectra 6 (1600x1200 landscape).
- ``app/discovery.py`` and ``docs/dev/architecture.md`` now enumerate
  ``pico_bin_client`` alongside the existing kinds; the auto-generated
  ``docs/compatibility.md`` regen picks it up automatically.

### Changed

- **Settings: Renderers tab is now dev-only.** In prod every base
  renderer's user-facing settings (dither, saturation, contrast,
  calibrated) are already surfaced per-device-instance on the
  Devices tab via the ``device_setting: true`` flag on each field,
  so the base Renderers page was duplicate surface for the typical
  install. The tab is now rendered only when ``--dev`` is set so
  plugin authors poking at base-renderer wiring still have a UI;
  the route itself is unchanged so deep-linking still works in
  prod.

## [0.51.6], 2026-06-18

### Changed

- **Marketplace install/uninstall queues a restart instead of asking
  the user to find a button.** A new "Restart required" button lights
  up in the topbar (rendered site-wide from ``_base.html`` via a
  ``marketplace_restart_pending`` context-processor flag) whenever
  one or more widget installs or uninstalls are waiting on a process
  restart. Click any number of Install / Update / Uninstall buttons
  on Settings -> Widgets -> Browse; the chip stays lit until you hit
  it, which opens the spinner modal, kicks ``Updater.restart()``, and
  auto-reloads the page when the new process is back. Earlier in this
  release I tried an auto-restart-per-install model: it broke the
  "queue several widgets at once" workflow that users actually want,
  so this re-do scopes the single auto-restart wire to the explicit
  ``/plugins/browse/restart`` endpoint and treats install/uninstall
  as queueable.
- **``static/restart-form.js`` no longer crashes the UX when the POST
  itself fails.** The restart-after-submit handler used to land in
  the outer ``.catch`` if ``fetch(form.action)`` rejected with
  "Failed to fetch" (the server killing the connection before
  flushing the response, common when the Werkzeug reloader races the
  restart timer in ``--dev``, occasional in production too). The
  POST-level rejection is now squashed into a resolved chain so the
  ``/healthz`` down-then-up poll becomes the source of truth: if the
  server really is restarting, the modal transitions cleanly through
  ``Restarting -> Waiting for it to come back -> Up. Reloading``; if
  it isn't, the 120 s ``/healthz`` poll times out and the error path
  fires (now correctly attributing the timeout, not the POST blip).
- **Refactor**: the restart-form spinner-modal markup and its
  ``/healthz`` poll-and-reload script extracted out of
  ``templates/settings.html`` into a shared partial
  (``templates/_restart_modal.html``) and a static JS file
  (``static/restart-form.js``), both included from ``_base.html``.
  Any page that drops a ``<form data-restart-form>`` now inherits
  the full UX, which is why the new topbar restart chip works
  identically to Settings -> System's self-update + rollback.

## [0.51.5], 2026-06-18

### Changed

- **Footer links no longer leak host hostname/IP in the Referer
  header.** All three outbound links (GitHub release tag, Sponsors,
  dmello.io) gain ``rel="noreferrer noopener"`` so the destination
  never sees the Tesserae host's address. On loopback that was just
  ``127.0.0.1``; on LAN installs it could have been the host's LAN IP
  or ``tesserae.lan``-style hostname, neither of which we want to
  hand to a third party by accident. Attribution for the dmello.io
  link still works via UTM tags carried in the URL itself, those
  aren't affected by the Referer policy. The dmello.io URL also
  gains ``utm_campaign=tesserae`` so the dedicated Campaign panel in
  Umami breaks out Tesserae-driven clicks without having to pivot
  through UTM Source.
- **Footer**: the dmello.io link's external-link icon moves to the
  left so all three footer entries lead with their glyph.

## [0.51.4], 2026-06-18

### Fixed

- **TRMNL battery samples weren't accumulating in history.** The
  battery-history hook in `trmnl_api._update_status_from_headers`
  (and the matching path in `transport_wiring._subscribe_device_status`)
  read `parsed.get("battery_pct")`, but TRMNL kit firmware only sends
  the `battery-voltage` header. `parse_status` lands `battery_pct=None`
  in that case; the LiPo-curve derivation runs INSIDE
  `merge_status_parsed`, so the populated value lives on `merged`,
  not `parsed`. Result: the hook always skipped the record. Fixed by
  reading `merged` for both the check and the values; same fix in the
  MQTT path so any future voltage-only firmware accumulates too.

### Changed

- **`/history` no longer fills with empty renders from quiet hours.**
  Rows with status `quiet` (every bound device in its quiet window)
  or `held` (schedule conditions kept the default page suppressed and
  no fallback was configured) are now hidden from the History page
  by default. A "Show skipped" chip in the filter row brings them
  back when you actually want to see why a slot didn't fire. The
  underlying events are still written to the EventLog and visible at
  `/events`, so nothing's lost. `EventLog.list` gains an
  `exclude_statuses` parameter for the new filter shape.
- **Footer**: GitHub icon now sits next to the version number; small
  Sponsor link (Phosphor heart) pointing at github.com/sponsors/dmellok;
  link to dmello.io with the external-link icon and a
  `?utm_source=tesserae_<version>&utm_medium=footer` tag so visitor
  analytics surface which Tesserae version drove the click.

## [0.51.3], 2026-06-18

### Fixed

- **Rotation conditions silently fail-opening in prod.** The
  scheduler tick's HA-state refresh runs in a background thread.
  ``ha_core.server`` resolves its base URL + token via
  ``current_app.config``, which is a request-scoped proxy and raises
  ``RuntimeError: Working outside of application context`` outside
  a Flask request. The exception was swallowed by the closure in
  ``app_factory._ha_get_states``, which returned ``[]``, and
  ``ConditionEvaluator.refresh_ha_states`` then replaced the cache
  with empty. Every condition's entity was then "not in HA cache",
  fail-open kicked in, and gated rotation steps fired regardless of
  the entity state. The manual "Test conditions" button worked
  because it runs in a request context. Fixed by pushing an app
  context inside the closure so the background thread can resolve
  ``current_app`` correctly.
- **Defence in depth on the same bug.** ``refresh_ha_states`` now
  refuses to overwrite a populated cache with an empty result. Logs
  a warning instead. Without this, a future closure-level swallow
  (or a transient HA blip that returns ``[]``) would silently fail-
  open every condition again.

### Changed

- **Events page condition rows**: dropped the green/red left rail on
  each step row in favour of a small pass/fail dot at the start of
  the line. Page ids are now resolved to friendly names via the page
  store (slug stays in the data layer so a later rename updates the
  display).

## [0.51.2], 2026-06-18

### Fixed

- **Drawer battery item leaking into the desktop top nav.** v0.51.0
  switched the mobile-drawer Batteries item from `<div>` to `<a>` so
  the indicator could navigate to `/devices/battery`. That made the
  generic `.topnav a { display: inline-flex }` rule beat the
  unscoped `.topbar-batteries--drawer { display: none }` hide rule on
  desktop (specificity 0,1,1 vs 0,1,0), so the drawer's icon + label
  + device list rendered inline in the desktop header alongside the
  popover trigger. Scoped the drawer rules to `.topnav` so the
  specificity matches, restoring the hide-on-desktop / show-in-mobile
  drawer behaviour.

, 2026-06-18

### Fixed

- **Drawer battery item leaking into the desktop top nav.** v0.51.0
  switched the mobile-drawer Batteries item from `<div>` to `<a>` so
  the indicator could navigate to `/devices/battery`. That made the
  generic `.topnav a { display: inline-flex }` rule beat the
  unscoped `.topbar-batteries--drawer { display: none }` hide rule on
  desktop (specificity 0,1,1 vs 0,1,0), so the drawer's icon + label
  + device list rendered inline in the desktop header alongside the
  popover trigger. Scoped the drawer rules to `.topnav` so the
  specificity matches, restoring the hide-on-desktop / show-in-mobile
  drawer behaviour.

## [0.51.1], 2026-06-18

### Fixed

- **mypy CI on strict-typed modules.** `BatteryHistory.recent` had a
  bare `tuple` type annotation (no parameters) and
  `device_battery_routes.index` did `(names.get(i, i)).lower()` when
  `display_name` could be `None`. Both flagged by mypy --strict on the
  v0.51.0 push.

## [0.51.0], 2026-06-18

### Added

- **`device_battery` widget.** Dashboard tile listing every
  registered device reporting a `battery_pct` heartbeat. Sorted
  lowest-charge-first with critical/low/ok tone colouring, fill bar
  per device, optional days-to-empty estimate, container-query size
  tiers (xs through lg).
- **Persistent battery history.** New `BatteryHistory` SQLite store at
  `data/core/battery_history.db` writes one row per battery-carrying
  heartbeat. Both MQTT (`transport_wiring`) and TRMNL HTTP-pull
  (`trmnl_api`) hook into it, so every device kind that reports
  battery accumulates history.
- **`/devices/battery` admin page.** Per-device card with name + current
  percentage, Chart.js trace tied to `--t-accent` (theme-reactive),
  stats table (samples, drain rate %/day, projected days-to-20%, days-to-0%).
  Selectable window: 1d / 3d / 7d / 14d / 30d / 90d. Status dot
  in the top-right corner conveys tone without a screaming rail.
  Reachable from the existing top-bar battery indicator (single
  device → direct link, multi-device → "View charts & trends"
  footer link in the popover).
- **Linear-regression projection.** 8-sample minimum, returns no
  projection for flat/charging batteries (the slope is reported
  alone). Powers both the widget's days-to-empty line and the admin
  page's reaches-20%/reaches-0% columns.
- **Condition decision logging.** Every rotation tick that evaluates
  step conditions, and every schedule fire involving conditions,
  writes one `type="conditions"` event row with per-condition
  observed values, pass/fail, the time-slot step vs the actually
  picked step, and any fail-open reason. Surfaced as a new filter
  chip on `/events?type=conditions` with a structured "why" panel
  per row. Debounced so a quiet rotation doesn't flood the log every
  30 seconds.

### Changed

- **Rotation projection bar** now respects conditions: the band shown
  in each time slot is the step the picker would actually pick, not
  the time-naive cycle position. Slots where all steps gate out
  render with a diagonal "Held" stripe; slots where the picker walked
  past the original step get a small amber underline so the user can
  see "the cycle shifted here".
- **Rotation projection bar palette.** Replaced the warm-everything
  terra/ochre/sage/rose/mauve set with a monochromatic ladder built
  from `--t-accent` at five intensities so the bar reads as one
  coherent strip and tracks the active theme.

### Notes

- No downsampling yet; at the default 15-min wake cadence each device
  grows the store by ~35 k rows / year. SQLite is comfortable through
  multi-million rows, so we'll add a rolldown when a real install
  hits multi-year retention.

## [0.50.3], 2026-06-18

### Fixed

- **Manual "Fire now" button on rotations now respects per-step
  conditions.** The autonomous scheduler tick already walked past
  steps whose conditions failed (an `octoprint_printing == on`
  condition would skip the 3D-print step when the printer was idle).
  The manual Fire button called ``_fire_rotation`` straight from the
  time-based step index, bypassing the eligibility check, so a user
  hitting "Fire now" while the gated step was time-current would push
  it regardless of the entity state. Routed manual Fire through the
  same ``_pick_eligible_step`` path as the tick; the per-step
  "Play this step" button keeps its bypass since explicit per-step
  intent is the whole point of that button.
- **Rotation projection bar fills the full 24h window.** The timeline
  preview's inner loop was capped at 200 iterations, so a rotation
  with 5-minute dwells covered only 1000 of 1440 minutes (~69% of the
  bar). Cap is now proportional to the window so short-dwell
  rotations fill end to end.

## [0.50.2], 2026-06-17

### Fixed

- **Editing a rotation or schedule after saving any condition no
  longer 500s.** The edit form seeds its conditions textarea via
  `step.conditions | tojson`, and Flask's JSON provider couldn't
  serialise Pydantic v2 `Condition` instances by default. App
  factory now installs a `JSONProvider` that defers `BaseModel` to
  `model_dump()`, which fixes the form re-render and any future
  `jsonify(model)` use site. Regression test pins both /rotations
  and /schedules paths.

## [0.50.1], 2026-06-17

### Docs

- **Bulk-renamed every standalone repo** so widgets live at
  `tesserae-widget-<name>`, themes at `tesserae-theme-<name>`, and
  device firmwares at `tesserae-device-<name>` (dropping the
  `-client` suffix since "device" already implies it). 34 repos
  renamed; GitHub's auto-redirect keeps every old link working.
- **README, CHANGELOG, install docs, compatibility table, and
  community widget docs** updated to the canonical names. 136
  references across 15 files.
- **Compatibility / settings tables**: cleaned up 20+ blank cells
  that were rendering as a literal `, ` after the v0.31.0 em-dash
  sweep (em-dashes had been used as "N/A" markers).

## [0.50.0], 2026-06-17

### Licence

- **Relicensed from MIT to AGPL-3.0-or-later.** Tesserae core, the
  catalog repo (`tesserae-widgets`), every standalone client repo
  (`tesserae-device-pi-bin`, `tesserae-device-pi-png`), and every
  bundled widget repo all move together.
- **What that means for you:**
  - **Self-hosting Tesserae on your own hardware: no change.** Run it,
    modify it, share modifications with friends — same freedoms as
    under MIT.
  - **Distributing a modified version: must ship the source.** Includes
    network-hosted modifications, which the AGPL closes (the
    distinguishing feature vs plain GPL).
  - **Combining Tesserae with proprietary software you ship: AGPL
    obligations apply** to the combined work. Widgets that just plug
    in via the documented plugin API can keep permissive licences
    (MIT / Apache-2.0) as long as they don't ship Tesserae itself.
- **Why:** to keep the project ecosystem open. A closed-source SaaS
  fork wouldn't be a contribution back to the community, and AGPL is
  the established licence for ruling that path out cleanly while
  leaving everything else (self-hosting, study, modification,
  contribution) wide open.
- No code change other than the LICENCE file, SPDX identifier in
  pyproject.toml, and licence references in docs / README.

## [0.49.6], 2026-06-17

### Fixed

- **countdown_date and year_progress now actually respond to cell size.**
  Both widgets used `@container w (max-width: ...)` style queries where
  `w` is a container *name* that nothing in the codebase declares. The
  queries silently matched nothing, so the size-tiered behaviour
  documented at the top of each widget (xs hides everything, sm adds
  the bar, md adds the grid, lg adds the meta footer) was never firing.
  Both widgets just rendered the largest variant at every cell size.
  Fix: drop the `w` name so the queries match the cell's own size
  container (set on every `.cell` element in the composer). No
  behaviour change for users who happened to be looking at a cell big
  enough to fit the largest variant; users with smaller cells will now
  see the appropriate compressed layout. Same root pattern caused
  spotify_top's side-by-side breakpoint to silently fail; fixed in
  the tesserae-widget-spotify v0.2.4 catalog release.

## [0.49.5], 2026-06-17

### Fixed

- **Public URL no longer corrupts LAN device frame URLs.** Regression
  from 0.49.4. When the Public URL setting was set (e.g.
  `https://tesserae.example.org:8443`), the override middleware
  rewrote `HTTP_HOST` to the public host:port. The
  `_capture_http_port` before-request hook then captured that proxy
  port (8443) as `DETECTED_HTTP_PORT`, and the push pipeline built
  LAN render URLs as `http://<lan-ip>:8443/renders/…`. Devices
  (pi_bin, pi_png, esp32) trying to fetch those frames hit the
  reverse proxy's HTTPS port over HTTP and got 400 Bad Request, so
  no panel could paint a new frame. Reported by @dmellok after
  setting Public URL during Spotify setup.
  Fix: `_capture_http_port` returns early when Public URL is set,
  leaving `DETECTED_HTTP_PORT` at its real value (Flask bind port,
  default 8765). External browser-facing URLs still use the Public
  URL via the existing middleware; device-facing LAN URLs revert to
  the actual bind port.

## [0.49.4], 2026-06-17

### Added

- **"Public URL" setting under Settings → App.** Operator-supplied
  override for the URL Tesserae uses when building external links
  (OAuth callbacks, HA discovery image URLs, etc.). Use this when
  running behind a reverse proxy whose `X-Forwarded-*` headers don't
  reach Flask cleanly. NGINX Proxy Manager in particular ignores
  `proxy_set_header` directives in its Advanced tab unless they're
  inside a Custom Location block (an undocumented quirk that breaks
  ProxyFix's auto-detection); setting Public URL bypasses that mess
  entirely. Leave blank to keep the existing auto-detect behaviour.
- Example value: `https://tesserae.example.org:8443` (no trailing
  slash; trailing slash is stripped tolerantly). Malformed values
  silently fall back to auto-detect so a typo doesn't lock you out.

## [0.49.3], 2026-06-17

### Fixed

- **OAuth callbacks now build the public URL when Tesserae runs behind
  a reverse proxy.** Wired `werkzeug.middleware.proxy_fix.ProxyFix` into
  the WSGI stack so `X-Forwarded-Proto` / `X-Forwarded-Host` /
  `X-Forwarded-Port` from an upstream NGINX Proxy Manager, Caddy,
  Cloudflare Tunnel, etc. are honoured. Before the fix, plugin OAuth
  flows (e.g. Spotify Core) generated redirect URIs like
  `http://internal-host/plugins/spotify_core/callback` from the
  internal HTTP connection between the proxy and Tesserae, so the
  Spotify Developer dashboard rejected the redirect URI even though
  the user registered the correct public `https://...:8443/...` URI.
  Reported by @dmellok during HA add-on Spotify setup behind NGINX
  Proxy Manager on a non-standard external port.
- Trusts one proxy hop by default. Operators stacking multiple
  proxies can override via `TESSERAE_FORWARDED_HOPS=<n>`; `0` disables
  ProxyFix entirely (bare-metal installs where the headers could be
  spoofed by a client).

## [0.49.2], 2026-06-16

### Fixed

- **TRMNL X devices now auto-provision at their native 1872×1404 panel
  size.** The native TRMNL firmware's `/api/setup` request only carries
  `ID / Content-Type / FW-Version / Model` headers; `Width` / `Height`
  are only sent on `/api/display`. Tesserae's auto-provision was
  reading the (absent) `Width` / `Height` and falling back to the
  original-TRMNL 800×480 default, so the composer would design the
  dashboard at 800×480 and the rendered PNG would come out blurry on
  the X's 13.3" panel (even though the `/api/display` path served a
  correctly-sized image, since per-request headers took over there).
  Setup now looks up panel dims from the `Model` header instead:
  `x` → 1872×1404, `og` / `TRMNL` → 800×480, anything else falls
  back to the original-TRMNL default until we add it to the table.
  Reported by @tommerty on
  [discussion #8](https://github.com/dmellok/tesserae/discussions/8).

## [0.49.1], 2026-06-16

### Fixed

- **TRMNL pushes no longer require an MQTT broker.** TRMNL clients are
  HTTP-polled (`/api/display`), not MQTT subscribers, but the push
  pipeline was unconditionally calling `transport.publish()` for every
  renderer including HTTP-polled ones. On hosts without Mosquitto the
  publish raised `RuntimeError: transport not connected`, the
  latest-render pointer never got stamped, and `/api/display` kept
  serving the placeholder image. The pipeline now skips the publish
  for devices whose manifest declares no `status_topic`, lifting the
  broker requirement for TRMNL-only setups. Reported by @tommerty on
  [discussion #8](https://github.com/dmellok/tesserae/discussions/8).

## [0.49.0], 2026-06-16

### Added

- **At-rest encryption for connector secrets.** Manifest-declared
  `secret: true` fields (HA tokens, plugin API keys, etc.) are now
  AES-GCM-wrapped on disk and unwrapped transparently when the
  scheduler / push / fetch pipelines read them. Wire format
  `enc:v1:<base64(nonce||ciphertext||tag)>` carries a version tag so
  future algorithm upgrades are mechanical. Bootstrap secrets
  (`app.session_secret_secret`, `auth.password_hash_secret`,
  `broker.password_secret`) stay in their existing forms because
  they're key material or already hashed.
- **Key resolution.** `TESSERAE_SECRET_KEY` env var (64 hex chars =
  32 bytes) takes precedence; if absent, the box derives a stable
  key from the Flask session secret via HKDF-SHA256 with the info
  string `b"tesserae.secret_box.v1"`. The fallback logs at info on
  first use so the operator can promote to an env-pinned key later.
- **Two new widgets.** `Countdown, Date` (large N days / hours hero
  against a target date, friendly meta line with the formatted date)
  and `Year, Progress` (year-in-weeks or life-in-weeks dot grid with
  a percentage hero). Both pure client-side, no network.

### Internals

- New `app.secret_box` module wrapping PyCA `cryptography`'s AESGCM
  + HKDF primitives.
- `SettingsStore` gains an optional `secret_box=` constructor arg
  and a `set_secret_box()` injector. Wrap-on-write / unwrap-on-read
  is transparent to consumers (`get_for_runtime`, `get_for_admin`,
  `update_for_namespace`); `get_section` recursively unwraps any
  `_secret`-suffixed string at any depth so plugin server modules
  that read their own state directly (e.g. `ha_core`) keep seeing
  plaintext.
- Legacy plaintext values keep reading (unwrap is a no-op for
  non-prefixed input). Migration to ciphertext happens
  opportunistically on the next save; no separate walker.
- Wrong-key reads raise `SecretBoxError` rather than silently
  returning an empty string, so a misconfigured `TESSERAE_SECRET_KEY`
  surfaces immediately instead of as a 401 from HA.
- Added `cryptography>=42,<46` as a runtime dependency. Rust-backed
  primitives, available as a manylinux wheel so the Docker base
  image stays slim.

### Upgrade notes

- **Upgrading 0.48.x → 0.49.0 needs no action.** Existing plaintext
  secrets in `settings.json` keep working (the unwrap path is a no-op
  for non-prefixed input). They migrate to ciphertext the next time
  you Save any setting under Plugins / Renderers / Devices.
- **Migrating to a new install works out of the box for the default
  setup.** The built-in Backups and Migrate flows include
  `settings.json`, which carries the session secret the fallback key
  is derived from. Same secret on both machines means the same
  decryption key, so connector secrets keep working after import.
- **If you set `TESSERAE_SECRET_KEY`**, copy that env var to the new
  install before importing the data zip. The key lives in your
  environment, not in `data/`, so without it the new machine derives
  a different key and connector secrets won't decrypt. Pinning the
  key is recommended for real installs (`openssl rand -hex 32`)
  because rotating the session secret then won't lock you out of
  your own connectors.
- **Downgrading 0.49.0 → 0.48.x.** Any secret re-saved on 0.49 is
  stored as `enc:v1:<base64>` on disk; an older Tesserae would read
  that literal string as your HA token and fail to authenticate. To
  downgrade, either restore `settings.json` from a pre-0.49 backup
  or re-save each affected secret in the older version.

## [0.48.6], 2026-06-16

### Added

- **Running-state pills on Schedules and Rotations.** The State column
  on the Schedules table and each Rotation card now surfaces what the
  scheduler is actually doing for that record, rather than just
  enabled / disabled. New states: `active` (last fire sent),
  `fallback` (conditions failed, fallback page pushed), `held`
  (silently skipped because conditions failed), `quiet hours`,
  `failed`, and `pending` (no tick yet since process start). Each
  pill carries a tooltip with the underlying reason so the user
  doesn't need to tail the event log to find out why a schedule
  isn't firing.
- Endpoint tests for `GET /api/conditions/ha-entities` covering the
  happy path plus three graceful-fallback branches (no `ha_core`
  installed, `get_states()` raises, `PLUGIN_REGISTRY` absent).

### Changed

- Rotation 24-hour timeline bands now use a warm five-hue palette
  (terra, honey, sage, dusty rose, dusty mauve) instead of the
  Material-style primaries. Reads more harmoniously against the
  brand terracotta accent and stops the bar from competing with the
  card content.
- Schedule editor's "Conditions + fallback page" block now sits as a
  full-width row below Smart sync instead of being squeezed into the
  three-column form grid, so the condition picker and fallback select
  have room to breathe.

### Internals

- `Scheduler.status()` now also returns `last_status` + `last_reason`
  per schedule; new `Scheduler.rotation_status()` exposes the same
  shape for rotations. Both are populated on every `_fire` /
  `_fire_rotation` and when `_pick_eligible_step` returns no
  eligible step.
- New `.pill` base + tone modifiers (`is-ok`, `is-warn`, `is-danger`,
  `is-accent`, `is-held`) in `static/style/schedules.css`; the
  previously implicit pill styling is now spelled out.

## [0.48.0], 2026-06-16

### Added

- **Conditional schedules and rotations.** Schedules and rotation
  steps can now declare zero or more `Condition` rows that the
  scheduler evaluates at fire time. All conditions on a schedule or
  step are AND'd; an unmet condition routes the schedule to its
  optional `fallback_page_id` (or skips silently if unset), and an
  unmet condition on a rotation step advances to the next eligible
  step. Three source kinds are supported: `ha_entity` (state /
  numeric / `in` / `present_within_seconds` against any HA entity),
  `time_window` (HH:MM wall-clock window with optional weekday
  mask), and `sun` (`before_sunrise`, `after_sunset`, `is_day`,
  `is_night` with optional minute offset, computed locally from
  `settings.app.latitude` + `longitude` so no extra HA call). The
  evaluator's HA state cache is refreshed once per scheduler tick;
  HA-unreachable falls open so dashboards keep refreshing on the
  existing cadence.
- **Rotation routing modes.** New `mode: "scheduled" | "priority"`
  on rotations. `scheduled` (default) keeps the existing time-based
  cycle but skips steps with failing conditions; `priority` ignores
  step durations and always pushes the first step in declared order
  whose conditions are met (a step with no conditions becomes the
  always-on fallback). Existing rotations default to `scheduled`
  with empty conditions, so behaviour is unchanged until you opt
  in.
- **Per-rotation flap protection.** New `min_hold_minutes` on
  rotations (default 5 min) gates step transitions so a HA sensor
  oscillating near a numeric threshold can't thrash the displayed
  page. Manual "play step N" overrides bypass the gate.
- **`POST /api/conditions/test` endpoint** for the schedule and
  rotation editor's preview button. Accepts a JSON array of
  condition dicts, refreshes the HA state cache, returns a
  per-condition `{passed, observed, reason}` so the user can see
  exactly which condition would fail and why.
- **`"held"` push status** so the History event log can distinguish
  "schedule didn't fire because conditions" from "schedule fired but
  failed". Held schedules with no fallback skip the History row
  entirely (INFO log only) to keep the audit trail focused on
  actual pushes.

### Changed

- Schedule + rotation editor forms expose conditions as a raw JSON
  textarea for the 0.48.0 ship. The full Bauhaus condition picker
  (entity autocomplete, operator dropdown, value type-shifting) lands
  in 0.48.1; the JSON path is the underlying contract so any picker
  UI just produces the same payload shape.

### Internals

- `app/state/conditions.py` carries the per-source-kind validators;
  `app/scheduler_conditions.py` owns the evaluator + a locally-computed
  sunrise/sunset (NOAA-style approximation, no `astral` dep).
- `Scheduler.__init__` gained an optional `condition_evaluator`. Tests
  that pass `None` keep the legacy "all conditions pass" behaviour so
  the existing 34-test scheduler suite required no updates.
- New tests: `tests/test_scheduler_conditions.py` (11 scheduler /
  rotation integration tests) and `tests/test_condition_routes.py`
  (3 API endpoint tests). All 941 tests green.

## [0.47.17], 2026-06-16

### Docs

- **CHANGELOG backfilled for 0.47.11 through 0.47.16.** Those six
  patch releases shipped without changelog entries; this catches up
  the record. No runtime change.

## [0.47.16], 2026-06-16

### Fixed

- **History rows for scheduler and rotation pushes now show the page
  name** instead of the raw hex id. The view's name-resolution was
  gated on `ev.source == "page"`, so manual sends got resolved but
  scheduler / rotation events stayed as hex slugs (`875b37e3a8c1`
  rather than the actual page name). The gate is removed; all
  `type="push"` rows go through the page-name lookup with the dict
  fallback covering URL / webpage one-offs. Follow-up to #15.

## [0.47.15], 2026-06-16

### Fixed

- **Blank scheduled and rotation pushes when a page uses the
  webpage widget (#15).** The widget mounts an iframe in a shadow
  root; the iframe is its own browsing context whose content load
  is not part of the parent compose page's network state. The
  composer's `__tesseraeComposed` flag fired the instant the widget's
  `render()` returned (synchronously, right after the iframe element
  was created), so Playwright screenshotted a blank cell before the
  iframe finished loading. Manual "Send page" worked most of the
  time because that path is `_push_arbitrary_url` and renders the
  source URL directly with Playwright's own load wait, bypassing
  compose entirely. Fix: the webpage widget's `render()` is now async
  and awaits the iframe's `load` event (capped at 6 s so a hung site
  doesn't pin the render). The composer already awaits each cell's
  render Promise, so `__tesseraeComposed` correctly waits for visible
  content.

- **`ValueError("unknown file extension: .tmp")` in the History
  thumbnail serve path.** The atomic-rename temp filename was built
  via `thumb_path.with_suffix(suffix + ".tmp")`, producing
  `foo.png.tmp`. Pillow's `save()` then inferred the format from the
  extension and raised. Cosmetic only (broken thumbnails in the
  editor's History view, not blank panels), but logged a Python
  traceback on every render. Fix: pass `format=` explicitly so the
  temp filename can't break format inference.

## [0.47.14], 2026-06-15

### Fixed

- **mypy `--strict` regression in the
  `_github_commit_cadence` widget sample.** The sample's
  `sum(b["count"] for b in bars)` and `max(bars, key=...)` walked a
  `dict[str, object]` list, so mypy strict choked on the implicit
  `int()` calls. Compute the totals from the raw `seed: list[int]`
  directly and look up the peak by index; same payload, no type-
  narrowing dance.

## [0.47.13], 2026-06-15

### Added

- **Dev-gallery sample payloads for the seven new GitHub hero
  widgets + `devref_egress`.** Lets `/_test/widgets` render
  `github_star_count`, `github_streak`, `github_pr_count`,
  `github_ci_status`, `github_star_growth`,
  `github_activity_heatmap`, `github_commit_cadence`, and the
  network-egress contract demo without needing a live GitHub token
  or unrestricted network egress. Gallery-only; no runtime change to
  the host.

## [0.47.12], 2026-06-14

### Fixed

- **Community and user themes were shown correctly in the live
  preview but rendered as the Light fallback when pushed to a
  panel.** The `/compose/<id>` route bypasses the auth gate from
  loopback so the in-process Playwright renderer can fetch it without
  a session. The template references `/themes/user.css` and
  `/themes/community.css` via `<link>` tags, but those endpoints
  weren't on the loopback allowlist, so Playwright's subresource
  fetches got redirected to `/login` and the panel render fell back
  to bundled tokens only. The editor's iframe carries an authed
  session cookie which is why preview worked. Fix: add both theme
  CSS endpoints to `_LOOPBACK_PATHS` in `app/auth.py` + regression
  tests.

## [0.47.11], 2026-06-14

### Added

- **Kind filter chips on the marketplace Browse page.** Splits
  Widgets / Themes / Fonts with per-type counts and icons; cross-
  filters with the existing tag chips via shared query params. Sits
  inside the Filter card so the page structure doesn't change. The
  chip row auto-hides when only one kind is present, so a widget-only
  catalog looks identical to before.

## [0.47.10], 2026-06-14

### Fixed

- **CI failures introduced in 0.47.8 / 0.47.9.** Two mypy strict
  errors: `CatalogEntry.kind`'s Literal didn't include `"theme"` so
  the install path's `kind == "theme"` branch was reported as a
  non-overlapping equality, and `community_themes.py` carried an
  unused `type: ignore` after the ThemeFamily literal was widened.
  Plus a catalog-side validate.yml fix that landed via the seed
  copy: the widget-bundle layout check ran on theme entries too and
  always reported "tarball contains []" because theme tarballs are
  flat `<id>.json` + `<id>.css` pairs, not plugin folders.

## [0.47.9], 2026-06-14

### Changed

- **Vivid and Gradient theme families moved to the community catalog.**
  29 themes (tangerine, lime, cobalt, magenta, emerald, crimson, cyan,
  aubergine, mustard, teal-pop, hot-pink, lavender-pop, olive-pop,
  burgundy, forest + sunset, aurora, twilight, spectrum, coral, mist,
  sand, sage, linen, mauve, marble, glacier, honey, pearl) used to ship
  bundled. They now live as two opt-in catalog packs:
  [tesserae-theme-vivid](https://github.com/dmellok/tesserae-theme-vivid) and
  [tesserae-theme-gradient](https://github.com/dmellok/tesserae-theme-gradient).
  Install from **Settings → Widgets → Browse community widgets**. The
  packs ship the same theme ids and CSS blocks as the bundled versions,
  so dashboards already pinned to one of these themes paint correctly
  the moment the matching pack is installed. **Until you install the
  pack**, dashboards bound to one of those theme ids fall back to the
  Light theme. The bundled set is now down to 13 themes (Light, Sepia,
  Cool gray, High contrast, Paper, Newsprint, Vivid, Citrus, Arctic in
  Light; Dark, Nord in Dark; Bauhaus, De Stijl, Brutalist in Movement).

### Fixed

- **Catalog schema's `id` + `folders` patterns now permit hyphens.**
  Theme ids commonly use hyphens (`tonal-slate`, `teal-pop`); the
  schema only allowed `^[a-z][a-z0-9_]*$`, which blocked valid theme
  pack entries from validating. Pattern relaxed to `^[a-z][a-z0-9_-]*$`
  in both `schema/marketplace.schema.json` and the seed mirror.
  Widget ids still use underscores by convention; nothing existing
  changes.

## [0.47.8], 2026-06-14

### Added

- **Themes as a catalog `kind`.** Marketplace gains a third installable
  kind alongside `widget` and `font`. Tarball convention is flat: each
  theme is two files at the envelope root, named by id (`<id>.json` +
  `<id>.css`). Single-theme entries ship one pair; **packs** ship N
  pairs and declare `folders: [...]` on the catalog entry mirroring
  widget bundles. The install path validates pairing, manifest-id ==
  file-stem, and `[data-theme="<id>"]` presence in the CSS; it refuses
  any id that clashes with a bundled Spectra theme. Installed themes
  land in `data/themes/community/<id>/theme.json` + `theme.css`. A new
  `GET /themes/community.css` endpoint mounts all of them after the
  bundled tokens + user themes in the cascade. The themes browse strip
  and the page editor's theme picker (page + per-cell) now surface
  community themes alongside bundled ones; the per-theme "Show in
  picker" toggle from 0.47.7 works on them identically. Detail pane
  treats community themes as read-only with a "from the catalogue"
  label and Duplicate-to-edit affordance, parallel to bundled themes.
  Backwards-compat: `InstalledRecord.kind` defaults to `widget` for
  pre-0.47.8 records.
- **`docs/dev/publishing-a-theme.md`** — contributor guide for
  shipping a theme or theme pack through the catalog. Covers the
  flat-file convention, the `theme.json` shape, the validated
  contract, and the PR flow against `tesserae-widgets`.
- **New `community` theme family** (and matching `From the catalogue`
  picker optgroup) for installed themes that declare a family outside
  the bundled set.

## [0.47.7], 2026-06-14

### Added

- **Per-theme "Show in picker" toggle.** Tesserae ships 43 bundled
  themes; the page editor's theme select was a long scroll past
  themes most users never use. Each theme card now carries a small
  eye-toggle (open / closed) and the detail pane gets a matching
  "Hide from picker" / "Show in picker" button. Hidden themes drop
  out of the page-editor's theme picker AND the per-cell override
  picker, but the CSS block stays loaded, so any dashboard already
  using a hidden theme keeps rendering correctly. The themes browse
  page deliberately still shows every theme (with a "hidden" badge
  and faded card) so re-enabling never requires remembering an id.
  Stored as `settings.app.disabled_theme_ids: list[str]` — opt-in
  per Tesserae instance.
  [`app/state/theme_registry.py`](app/state/theme_registry.py),
  [`app/themes_routes.py`](app/themes_routes.py),
  [`app/page_routes.py`](app/page_routes.py),
  [`templates/themes.html`](templates/themes.html),
  [`static/style/themes.css`](static/style/themes.css).

## [0.47.6], 2026-06-14

### Changed

- **Dashboards list groups pages by device.** Settings → Dashboards
  no longer renders one flat insertion-order list. Pages now bucket
  under the first bound device that still resolves, each section
  head labelled with the device name + icon + a small count chip.
  Within each section pages are alphabetical (case-insensitive);
  device sections are alphabetical by display name; an **Unbound
  (virtual panel)** section always sits last for pages with no
  device binding. Pages bound to multiple devices appear once,
  under their primary, with a small `+N` chip whose `title`
  tooltip lists the other devices. A primary device that's been
  deleted falls through to the next still-existing device in the
  binding list, so half-deleted topologies don't lose their pages
  to the Unbound bucket.
  [`app/page_routes.py`](app/page_routes.py),
  [`templates/pages_list.html`](templates/pages_list.html).

## [0.47.5], 2026-06-14

### Added

- **`segno` as a host dependency.** Pure-Python QR code generator
  (~50 KB, no transitive deps). Available to any widget or
  renderer that wants to embed a scannable link without us baking
  per-plugin QR code into client-side JS. First consumer is the
  community `recipes` widget at
  [github.com/dmellok/tesserae-widget-recipes](https://github.com/dmellok/tesserae-widget-recipes);
  any future widget can `import segno` directly.
  [`pyproject.toml`](pyproject.toml).

## [0.47.4], 2026-06-14

### Added

- **Carousel preview for marketplace screenshots.** The community
  widget Browse page now renders multi-screenshot widgets as an
  inline carousel inside the existing 3:2 thumbnail, with prev/next
  arrows (revealed on hover/focus), clickable dot indicators, and
  native touch-swipe + keyboard arrow navigation via CSS scroll-
  snap. Single-screenshot widgets (every existing catalog entry)
  render byte-identically to before, no JS path, no new DOM nodes.
  Schema gains an optional `extra_screenshot_count: int` (0-9);
  when > 0 the catalog also ships
  `screenshots/<id>/extra-<n>.png` for n=1..count. The catalog-side
  CI (in the `tesserae-widgets` repo) verifies every declared
  extra exists with valid PNG magic bytes. Contributors who want
  to show off multiple widget states (playing vs paused, day vs
  night, sun vs rain) can now do so without leaving the grid.
  [`schema/marketplace.schema.json`](schema/marketplace.schema.json),
  [`app/marketplace.py`](app/marketplace.py),
  [`app/marketplace_routes.py`](app/marketplace_routes.py),
  [`templates/plugins_browse.html`](templates/plugins_browse.html),
  [`static/plugins_browse_carousel.js`](static/plugins_browse_carousel.js).

## [0.47.3], 2026-06-13

### Added

- **Smart sync (JIT) for rotations.** The wake-aware fire gate that
  schedules picked up in issue #10 now applies to rotations too:
  when smart sync is on, a step transition is held until at least
  one bound device is within `smart_sync_lead_s` seconds of its
  predicted next wake. The step that ends up firing is whichever
  step is current at fire-time, so long wake intervals naturally
  skip intermediate steps the panel slept through (matching what
  the panel would render on wake anyway). Falls back to natural
  step-boundary firing when no bound device reports telemetry, when
  every device is still in the warm-up window, or when smart sync
  is left off. New form fields on the rotation editor mirror the
  schedule UI: a "Smart sync (JIT)" toggle plus a "Render lead (s)"
  input (default 10s, 0-600 range).
  [`app/state/rotation_model.py`](app/state/rotation_model.py),
  [`app/scheduler.py`](app/scheduler.py),
  [`app/rotation_routes.py`](app/rotation_routes.py),
  [`templates/rotations.html`](templates/rotations.html).

### Changed

- `Scheduler._smart_sync_should_wait` now takes
  `(page_id, lead_s, now)` so the rotation and schedule code paths
  can share one gate. Behaviour for the existing schedule call site
  is unchanged.

## [0.47.2], 2026-06-13

### Added

- **Two new B&W e-ink-ready themes in the Light family.** *Paper*
  is strict 1-bit (pure `#FFFFFF` canvas, pure `#000000` ink, no
  greys), best for 2-colour panels where any mid-tone dithers to
  noisy checker. *Newsprint* uses the same white canvas but admits
  a small greyscale hierarchy (muted/secondary text, hairline
  edges, light-grey sunken surface + accent-soft fills) so panels
  with grey support get tonal depth and pure-B&W panels render the
  greys as deliberate stipple texture. Both use the standard
  Helvetica Neue stack so typography stays a Style concern.
  [`static/style/spectra-tokens.css`](static/style/spectra-tokens.css),
  [`app/state/theme_registry.py`](app/state/theme_registry.py).

## [0.47.1], 2026-06-13

### Fixed

- **Editor: cell config/theme reverting after save.** When the
  editor needed to reload (binding/unbinding a device, plugin
  swap, layout-form submit, batch cell ops), unsaved cell-form
  draft inputs were dropped, so the last-typed prompt or theme
  override silently reverted to whatever the server had on disk.
  Every reload path now flushes all dirty cell forms first via a
  shared `window.tesseraeSaveAllForms` helper, and the editor now
  warns on raw browser reload while the Save button is hot.
  [`static/pages/editor.js`](static/pages/editor.js),
  [`static/pages/layout_editor.js`](static/pages/layout_editor.js).
- **Custom layout garbled when binding a second device with a
  different aspect ratio.** `_ensure_cells_fit_panel` was running
  a non-uniform rescale every time the primary panel resolved to
  different dimensions, so binding (or unbinding) a
  different-aspect device silently rewrote every cell's
  geometry, and repeated rebinds accumulated rounding errors
  until the layout looked random. The function is now a no-op
  unless the panel actually flipped orientation or the existing
  cells overflow the new panel. Paired with that,
  `resolve_panel_for_page` now picks the *largest* bound panel by
  area deterministically, so bind order can no longer swap the
  design canvas under an existing layout. A new "Refit to current
  panel" button in the layout editor's custom-layout details is
  the explicit escape hatch when you actually do want every cell
  proportionally rescaled to a freshly-bound display.
  [`app/page_routes.py`](app/page_routes.py),
  [`app/panel.py`](app/panel.py),
  [`templates/page_editor.html`](templates/page_editor.html).

## [0.47.0], 2026-06-13

### Added

- **Live rotation countdown + per-step "play now" button.** Each
  active rotation on Settings → Rotations now renders a live
  progress bar above its step list, ticking every second toward the
  next step transition. When the countdown hits zero the page soft-
  reloads so the server's recompute drives the next step's bar.
- **Manual step override.** Every step row gets a small play icon
  (visible on hover) that re-anchors the cycle so the clicked step
  starts at the moment of the click and subsequent steps follow at
  their normal dwell intervals — "play this dashboard now and
  continue from here". The override is in-memory only; a server
  restart resumes the rotation's anchor-deterministic schedule.
  Disabling or deleting a rotation drops its override.
- New POST `/<rotation_id>/play/<step_index>` route, new
  `Scheduler.compute_step_state` / `Scheduler.force_step` /
  `Scheduler.clear_anchor_override` methods, new `StepState`
  dataclass exposing the dwell-window edges for the template.
  [`app/scheduler.py`](app/scheduler.py),
  [`app/rotation_routes.py`](app/rotation_routes.py),
  [`templates/rotations.html`](templates/rotations.html),
  [`static/rotations.js`](static/rotations.js),
  [`static/style/schedules.css`](static/style/schedules.css).
- Six new tests in
  [`tests/test_rotation_scheduler.py`](tests/test_rotation_scheduler.py)
  cover the override math (jumps to requested step, continues from
  there, clears `_last_step` so the same-step case re-fires, raises
  on invalid index, GCs on next-day anchor, clears via
  `clear_anchor_override`).

## [0.46.10], 2026-06-13

### Fixed

- **Cleanup: removed the `_ai_brief` sample data from
  [`app/widget_samples.py`](app/widget_samples.py).** It was a
  screenshot-capture helper that slipped into 0.46.8 alongside the
  bundled-plugin noise; should never have been part of the released
  tarball. Removing now closes the 0.46.8 carry-over cleanly. ai_brief
  is a community widget published via the marketplace
  ([`dmellok/tesserae-widget-ai-brief`](https://github.com/dmellok/tesserae-widget-ai-brief))
  and the catalog-install path doesn't touch widget_samples.py.

## [0.46.9], 2026-06-13

### Fixed

- **Cleanup: removed `plugins/ai_core` + `plugins/ai_brief` + 30
  regenerated docs/screenshots/widgets PNGs accidentally committed
  in 0.46.8.** `ai_*` is a community widget shipped via the marketplace
  catalog ([`dmellok/tesserae-widget-ai-brief`](https://github.com/dmellok/tesserae-widget-ai-brief));
  it shouldn't be bundled. The screenshots got regenerated by an
  in-session capture run for the catalog's `lg.png` and slipped into
  the staging set unintentionally.

## [0.46.8], 2026-06-13

### Fixed

- **Plugin schema rejected `variables_textarea` field type.** The
  0.46.7 release added the `variables_textarea` macro + JS + CSS but
  forgot to add the new value to the `cell_options[*].type` enum in
  [`schema/plugin.schema.json`](schema/plugin.schema.json). Plugins
  declaring it would load-fail with "'variables_textarea' is not one
  of [...]" and the cell would render a "couldn't fetch dynamically
  imported module" error. Fixed.

## [0.46.7], 2026-06-13

### Added

- **`variables_textarea` field type** for cell options. Renders the
  textarea plus a click-to-insert chip rack grouped by category;
  clicking a chip drops its `{placeholder}` at the textarea's cursor
  position. Used by the new `ai_brief` community widget, available to
  any plugin that wants to ship a templatable prompt with discoverable
  placeholders. New macro in [`templates/_components.html`](templates/_components.html),
  JS at [`static/variables-textarea.js`](static/variables-textarea.js),
  styles in [`static/style/forms.css`](static/style/forms.css).
- **`home_lat` / `home_lon` injected into widget `ctx`** from the
  server-level home location (`app.latitude` / `app.longitude`).
  Widgets opt in by reading `ctx.get("home_lat")` as a fallback when
  the cell's own latitude/longitude is empty, so users don't re-type
  coordinates on every weather / sky / ai widget. The bundled
  `weather_now`, `weather_forecast`, `weather_hourly`,
  `weather_now_scenic`, and `clock_sunrise_sunset` widgets are wired
  to use it.

### Fixed

- **`auto_field` now passes `rows` and `placeholder` through to
  `textarea_field`.** Previously a plugin declaring
  `{"type": "textarea", "rows": 14}` got the macro's default of 3
  rows silently. Same fix applies to `placeholder`.

## [0.46.6], 2026-06-13

### Added

- **Star counts on community widget catalog cards.** Each entry on
  Settings → Widgets → Browse now shows a `★ N` chip next to the
  author byline when the widget's source repo has at least one
  GitHub star. The count comes from a `stars.json` sidecar published
  next to `widgets.json` by a GitHub Action in the `tesserae-widgets`
  catalog repo (hourly cron, `GITHUB_TOKEN`-authenticated GitHub API
  calls, only commits when counts actually change). Tesserae itself
  makes no extra GitHub API calls — every install reads `stars.json`
  with the same TTL as `widgets.json`. Cleanly fits the no-extra-
  telemetry stance. Sidecar 404 / parse failure is non-fatal: the
  catalog renders without the chip. New `CatalogEntry.stars` field
  defaults to `None`; the template hides the chip on `None` or `0`
  so widgets with no star data don't display "★ 0" as discouraging
  noise. See [`app/marketplace.py`](app/marketplace.py) for the
  sidecar fetch + merge, [`templates/plugins_browse.html`](templates/plugins_browse.html)
  for the chip, and two new tests in [`tests/test_marketplace.py`](tests/test_marketplace.py)
  covering the happy path and the sidecar-missing fallback.

## [0.46.5], 2026-06-13

### Fixed

- **Thin white border at the corner edges of the iOS home-screen
  icon.** The 180×180 `apple-touch-icon.png` was rendered with the
  brand's own rounded-square mask baked in, so the corners of the
  PNG were transparent. iOS then applies its own squircle mask on
  top, and the slight radius mismatch exposed a band of
  home-screen background colour wherever iOS's mask sat outside
  ours. Per Apple's HIG ("don't add a layer mask of an icon's
  shape to your image; iOS automatically applies an icon mask"),
  the apple-touch-icon now renders with `maskable=True` so the
  gradient fills every pixel edge-to-edge. iOS does the rounding;
  the corners come out clean. Other icons (favicon, HA add-on
  sidebar icon, social-card 512) keep the rounded mask since they
  display as-is. [`scripts/render_brand.py`](scripts/render_brand.py).

## [0.46.4], 2026-06-12

### Changed

- **Document the missing panels and firmware clients.** The 0.46.0
  `esp32_bw_client` + `esp32_bw_bin` work shipped but the README and
  docs hadn't been updated to mention them; the
  `tesserae-device-photopainter-7.3-bin` was in the README but absent
  from the install-a-client doc. README now lists 5 renderer plugins
  and 5 device plugins (was 4/4), includes the
  `tesserae-device-esp32-bw` firmware row, and the Waveshare panels
  table calls out the 4.2" B/W panel (Tested column intentionally
  blank — wire contract verified, awaiting in-the-wild feedback).
  `docs/compatibility.md` gains `waveshare_42_bw` preset, `esp32_bw_bin`
  renderer, `esp32_bw_client` device-kind, and a per-renderer test-
  status row marked `Untested`. `docs/install/clients.md` gains
  sections for both `tesserae-device-photopainter-7.3-bin` (confirmed
  on hardware) and `tesserae-device-esp32-bw` (with an explicit
  "untested in the wild" admonition), and the stale "all three
  clients" claim is replaced with a pointer to the compatibility
  table that gets actively maintained.

## [0.46.3], 2026-06-12

### Added

- **Proper home-screen icons across iOS, iPadOS, macOS, Android, and
  Windows.** Tesserae now ships a 180×180 `apple-touch-icon.png`
  (the canonical Apple Add-to-Home-Screen / Add-to-Dock size), a
  192×192 PNG for Android Chrome, a 512×512 maskable variant for
  Android adaptive-icon launchers (rendered without the outer
  rounded-square so the launcher can mask to circle / squircle /
  rounded square without double-clipping the corners), and a
  `manifest.webmanifest` declaring all of the above plus brand
  colour and standalone display. The manifest uses relative
  `start_url` / `scope` (`../../`) so the same file works for direct
  hosting and HA Ingress. Added `theme-color` (light + dark),
  `apple-mobile-web-app-capable`, `apple-mobile-web-app-title`, and
  `apple-mobile-web-app-status-bar-style` metas to
  [`templates/_base.html`](templates/_base.html). Registered
  `application/manifest+json` for `.webmanifest` in
  [`app/app_factory.py`](app/app_factory.py) so Alpine containers
  and Windows installs (where the system mime.types file may not
  include the entry) serve the manifest with the right Content-Type.
  [`scripts/render_brand.py`](scripts/render_brand.py) bakes all
  the new sizes from the same SVG source.

## [0.46.2], 2026-06-12

### Changed

- **Credit TRMNL + Terminus in the README and credits page.** Added
  explicit acknowledgment that TRMNL's open BYOS protocol is what
  makes Tesserae's HTTP-pull path possible, that Terminus is the
  reference server I aligned envelopes against, and that the
  rotations feature is a Tesserae take on TRMNL's playlists concept
  rather than an original design.

## [0.46.1], 2026-06-12

### Changed

- **Stop calling Seeed-built hardware "native TRMNL hardware".** TRMNL
  is the firmware / software; the physical devices are built by Seeed
  Studio running the TRMNL firmware. The phrasing made some folks
  read Tesserae as conflating the two. Swept all user-facing docs,
  manifests (`devices/trmnl_client/device.json`,
  `renderers/trmnl_png/renderer.json`), and load-bearing code
  comments (`app/auth.py`, `app/trmnl_api.py`, `app/device_loader.py`,
  `renderers/trmnl_png/renderer.py`) and reworded as "TRMNL devices"
  or "TRMNL device (Seeed hardware, TRMNL firmware)" depending on
  context. CHANGELOG entries left alone; convention is don't rewrite
  history.

## [0.46.0], 2026-06-12

### Added

- **New device kind `esp32_bw_client` + new renderer `esp32_bw_bin`
  for 1-bpp B/W e-paper panels.** Closes the loop on the
  `tesserae-device-esp32-bw` firmware (generic ESP32 + mono e-paper,
  canonical target Waveshare 4.2" 400x300, but the renderer + packer
  are resolution-agnostic). Before this, a device heartbeating with
  `kind:"esp32_bw_client"` showed up in the Discovered strip but
  one-click Register failed with "Unknown device kind", and no
  renderer emitted the strict 1-bpp wire format the firmware decoder
  demands (exactly `width * height / 8` bytes, 8 pixels per byte,
  MSB = leftmost, bit-set = white).
  - `app/quantizer.py`: new `pack_to_panel_bin_1bpp()` mirrors
    `pack_to_panel_bin` but for the 1-bpp wire. Same full dither
    suite works (Floyd-Steinberg, Atkinson, Jarvis, Stucki,
    Bayer 8x8, halftone, crosshatch, none).
  - `app/panel.py`: new `waveshare_42_bw` preset (400x300,
    landscape-native).
  - The device's `parse_status` extracts `panel_w` / `panel_h` from
    the heartbeat (with `width` / `height` as aliases) so any
    width-multiple-of-8 BW panel (296x128, 480x280, 800x480, etc.)
    registers in one click with the correct dims via the existing
    Discovered card pre-fill path.
  - Wire-contract tests lock the firmware byte format: all-white
    400x300 packs to 15000 bytes of `0xFF`; all-black packs to
    `0x00`; a single white column at x=0 makes every byte `0x80`.

## [0.45.7], 2026-06-11

### Added

- **Community widget gallery auto-refresh.** The community gallery
  (`docs/widgets/community.md`) is generated from the catalog repo's
  `widgets.json` on every docs build, but the docs workflow only
  fires on pushes to *this* repo. Catalog changes silently drifted
  behind the wiki. Two new triggers in `.github/workflows/docs.yml`:
  a daily cron at 06:00 UTC (zero-config, catches drift within 24 h)
  and a `repository_dispatch` listener (`catalog_updated` event) so
  the catalog repo can ping this one on PR merge for an immediate
  refresh.

### Changed

- **Community gallery refreshed.** Adds `calendar_schedule`,
  `fal_image`, `paperlesspaper_art`, and pins the latest versions
  of the other community entries (18 entries total, was 15 at the
  last deploy).

## [0.45.6], 2026-06-11

### Fixed

- **Install guides: audited against current code, five real
  discrepancies fixed.** Onboarding wizard described as 3 steps in
  `server.md` (it's 5); timezone + HA discovery wrongly listed under
  Settings → Server (they're App-level fields); Backups vs Data
  export collapsed into one feature in `server.md` + `docker.md`
  (they're two separate `/settings/system/{backup,data}` endpoints);
  TRMNL MAC-based auto-provision (primary path since 0.44.1) missing
  from `clients.md` + `devices.md`; "Settings → Pages" and
  "Settings → Widgets" referenced in `devices.md` +
  `spotify-home-assistant.md` (they're top-nav entries Dashboards
  and Widgets, not Settings areas). Rotations mention added to
  `devices.md`.

## [0.45.5], 2026-06-11

### Fixed

- **History page: rotation source-chip now shows the shuffle icon.**
  Rotation pushes were already being recorded with
  `source='rotation'`, but `history.html`'s `SOURCE_META` had no
  entry, so the chip fell through to the neutral question-mark
  fallback. Added `rotation → ('Rotation', 'shuffle',
  'accent-ochre')` to the metadata map and included `rotation` in
  `history_routes.FILTERABLE_SOURCES` so the filter strip at the
  top of `/history` exposes a Rotation tab too.

## [0.45.4], 2026-06-11

### Fixed

- **Rotations editor form: 4-column grid + equal field heights.**
  The `end_at` field's "leave blank to cycle until midnight" help
  text was a `<p class="field-help">` underneath the input, which
  pushed that column taller than the others and bumped Priority
  onto a second row. Help text now lives in the input's `title`
  tooltip; the grid is `1.5fr 1fr 1fr 1fr` so Name | Starts | Ends
  | Priority share one row at desktop, collapse to 2-up under
  960px and 1-up under 540px.

## [0.45.3], 2026-06-11

### Added

- **Rotations: optional `end_at` field stops the cycle at a
  wall-clock time.** Default behaviour is unchanged (cycle until
  midnight, re-anchor next day), but you can now set e.g.
  `anchor=09:00` + `end_at=17:00` so the rotation only runs during
  the workday and falls silent overnight. `end_at < anchor` is a
  wrap-around window (e.g. 22:00 to 06:00) matching the existing
  schedule semantics.

## [0.45.2], 2026-06-11

### Changed

- **Rotations: drop the device picker; show each step's page-bound
  devices in the preview instead.** Each dashboard already binds to
  devices, so making the rotation re-bind was duplicate work and a
  source of confusion. The form's Devices section is gone; the
  read-only step preview on each rotation card now shows little
  device chips under the page name, so at a glance you see "step 1:
  Morning Briefing → Lounge + Kitchen panels." Empty bindings show
  a warning chip so you don't accidentally save a rotation whose
  step has no destination.

## [0.45.1], 2026-06-11

### Fixed

- **Rotations editor: Add Step button now works.** The `<template>`
  element with the row markup sat outside the `<form>` (Jinja macro
  put it as a sibling), so the form-scoped `form.querySelector`
  couldn't find it and the click handler bailed silently. Moved the
  template inside the form so each rotation form binds to its own
  template.
- **Rotations editor: device picker now uses the same wide-card
  `device-checklist` style as the dashboard editor and Send page**
  (icon + name + dimensions per row) instead of the inline-chip
  fallback.

## [0.45.0], 2026-06-11

### Added

- **Rotations: cycle dashboards on a wall-clock anchor.** New top-nav
  entry next to Schedules. A rotation is an ordered list of
  `(page, dwell_minutes)` steps that loop on a daily anchor.
  Common ask was "show dashboard A for 30 min, then B for 30 min,
  repeat" or "morning dashboard 06:00, midday dashboard 12:00,
  evening dashboard 18:00." That now configures with a couple of
  clicks instead of needing six daily schedules.
  - Anchor reseeds at the configured `HH:MM` each local day, so long
    cycles don't drift across DST flips.
  - Day-of-week filter mirrors Schedules.
  - Priority field lets existing schedules preempt the rotation
    (e.g. a daily 09:00 schedule with `priority=10` overrides the
    rotation at 09:00 the same way it would override another
    schedule, eink shows the most recently pushed frame).
  - First tick after enable fires the current step immediately;
    subsequent ticks within the same step are no-ops.
  - "Fire now" button manually pushes whichever step the rotation is
    currently on, useful for previewing edits without waiting for
    the next transition.
  - New `rotation_routes` blueprint at `/rotations`, new
    `RotationStore` persisting to `data/core/rotations.json`,
    new `Rotation` pydantic model under `app.state.rotation_model`.

## [0.44.11], 2026-06-11

### Added

- **Dev gallery sample for `calendar_schedule`.** The new community
  widget (Google-Calendar-style agenda view, lives in
  `tesserae-widget-calendar-schedule`) would render blank in the dev gallery
  without an ICS feed configured. Bundling a synthetic
  school-week sample under `widget_samples` so users browsing
  `/_test/widgets` (and the catalog preview pipeline) see a
  representative frame without having to wire up calendar_core.

## [0.44.10], 2026-06-11

### Fixed

- **Rendered frames now honour the app-level timezone setting.**
  Tesserae's preview iframe paints in the user's browser, so it picks
  up the laptop's local timezone. The actual frame pushed to the
  device is painted by a headless Chromium *inside the Tesserae
  container*, which previously read its timezone from the container's
  `TZ` env var (defaulting to UTC under Docker / the HA add-on). For
  users on Europe/London during BST, that meant clock + calendar
  widgets rendered an hour behind, even with
  `settings.app.timezone = "Europe/London"` configured.
  `RenderRequest` now carries a `timezone_id` field; `PushManager`
  reads the app setting on every push and forwards it to
  `browser.new_context(timezone_id=...)`. `"system"`, empty, or
  unparseable values still fall through to the container TZ (pre-fix
  behaviour). DST transitions are handled by the underlying
  `tzdata` package, so a BST→GMT change at the end of October will
  follow automatically without restarting Tesserae.

## [0.44.9], 2026-06-10

### Fixed

- **TRMNL `Battery-Voltage` accepts decimal volts.** Some native TRMNL
  firmware sends voltage as a decimal string (e.g. `"3.86"`) instead
  of the integer millivolt form `"3860"`. Tesserae's parser previously
  only accepted the integer form, so `battery_mv` came out as `None`
  for those devices, which then meant no entry in the topbar battery
  indicator and no `battery` sensor in HA discovery. The parser now
  accepts both: any positive value below 100 is treated as volts and
  multiplied by 1000; everything else stays interpreted as mV.
  Threshold is unambiguous, a LiPo never reads in the 100-1000 mV
  range. Values of 0 or negative are rejected as sensor noise.

## [0.44.8], 2026-06-10

### Changed

- **`/api/display` envelope alignment with Terminus.** Three small
  corrections so any TRMNL-compatible firmware reads identical fields
  off Tesserae as it does off the upstream BYOS reference server:
  - `special_function` now defaults to `"sleep"` (was `"none"`).
    Native firmware branches on this; `"sleep"` is the documented
    "deep-sleep until next poll" signal, which is what the firmware
    expects when there's no admin action queued. The prior `"none"`
    value caused some firmware builds to stay in standby between
    polls, draining the LiPo.
  - New `maximum_compatibility: false` field. Per-device flag in
    Terminus; we ship `false` so firmware uses its modern features
    (partial refresh, etc.) by default.
  - `/api/log/` now parses Terminus's documented payload shapes
    (`{"logs": [...]}` and the nested `{"log": {"logs_array": [...]}}`)
    and surfaces each entry as its own log line rather than logging
    the raw request body as one blob. Unknown shapes still get
    accepted + raw-logged + a 200 response (firmware refuses to poll
    if `/api/log/` 4xxs).

### Added

- **Derive `battery_pct` from `battery_mv` on every heartbeat.** Native
  TRMNL kit firmware sends raw millivolts (`Battery-Voltage` header)
  but no percentage; that meant TRMNL devices were absent from the
  topbar battery indicator AND from the HA MQTT auto-discovery
  battery sensor. The merge step now runs a LiPo curve (4200 mV =
  100 %, 3300 mV = 0 %, clamped both ends, linear in between) when
  the firmware reports mV without an explicit pct. ESP32 + TRMNL
  panels that send both keep their explicit reading; TRMNL panels
  that send only mV gain a derived pct that flows through the topbar
  indicator and the HA `battery` sensor uniformly.
- **HA auto-discovery now publishes a `battery_voltage` sensor** for
  any device that reports `battery_mv` (voltage device-class, unit
  mV). Lets HA automations run off the raw value rather than the
  derived percentage. Lazy-published the first time a heartbeat
  carries the key, same pattern as the existing `battery` / `signal`
  / `ip` sensors.

## [0.44.7], 2026-06-10

### Added

- **Plugin `ctx` now carries `cell_w` / `cell_h`.** The composer
  hydrates every widget's `fetch()` with the cell's actual pixel
  dimensions alongside the existing `panel_w` / `panel_h`. Widgets
  that pull images from upstream APIs (e.g. the new `fal_image`
  community widget) can now request an image at the exact size
  they'll be painted at, instead of falling back to an aspect-ratio
  guess derived from the whole panel. Defaults to 0 / 0 in sample-
  mode and single-cell preview paths, so existing widgets keep
  working unchanged.

### Changed

- **Page editor: text inputs now defer preview refresh to blur, not
  keystroke.** The live preview in the dashboard editor still updates on
  every slider tick / checkbox flip / dropdown change, but text and
  number fields wait for the `change` event (fires on blur or Enter)
  before re-rendering the preview iframe. This matters for widgets
  whose `fetch()` calls a paid API (e.g. the new `fal_image` community
  widget on Fal.ai): typing a prompt no longer fires a generation per
  character. The dirty indicator + save-button enable still happen on
  every keystroke, so save flow is unchanged.

## [0.44.6], 2026-06-10

### Added

- **Inline schedules card on the page editor.** A new "Schedules"
  card now sits at the bottom of the editor column (last after the
  Dashboard, Layout, and Cell editor cards) showing the schedules
  pinned to this dashboard with their cadence, smart-sync state, and
  an Edit link per row. "Add schedule" links straight to the full
  schedules form with the dashboard already selected (new
  `?prefill_page=<id>` query param on `/schedules` opens the
  New-schedule form automatically). Empty state nudges you to add
  one when none exist.

### Changed

- **Mobile page-editor layout reshaped.** Below 1100px the live
  preview is now `position: sticky` pinned just under the global
  topbar so you can keep editing cells without losing sight of the
  rendered output. The "Live preview" title bar and the
  `1200 × 1600 · Lounge, Office` dims line are hidden at narrow
  widths to maximise the preview area in the sticky card. The
  page-editor header (Save / Send / Delete) goes back to being
  non-sticky on mobile so it scrolls away naturally; the desktop
  sticky-glass-blur behaviour is preserved.
- **README + docs drift cleanup.**
  - README's theme block called out "19 themes across four families
    including base16"; base16 was retired in 0.43.0 and the actual
    count is 41 themes across 5 families (Light / Dark / Movement /
    Vivid / Gradient). Fixed.
  - README's community-catalog mentions updated from 15 to 16
    entries (the new `paperlesspaper_art` widget published today).
  - README's "~790 tests" bumped to "~800 tests" (currently 802 in
    CI).
  - `.github/SECURITY.md` supported-versions table refreshed: 0.44.x
    is current at v0.44.5; 0.43.x rolled off to ❌.
  - `docs/widget-design-system.md` theme breakdown corrected
    (`7 Light + 2 Dark + 3 Movement + 15 Vivid + 14 Gradient = 41`).

## [0.44.5], 2026-06-10

### Changed

- **README images re-encoded with EXIF metadata removed.** Smaller
  files (`hero-rack.jpg` 600 KB → 387 KB, `widget-sizing.jpg` 325 KB
  → 193 KB), no other visual difference.

## [0.44.4], 2026-06-09

### Changed

- **README slimmed down.** Replaced the aged 0.20-era hero image
  with a new top-down shot of six different e-ink panels (framed
  Inkys, bare Waveshare boards driven by ESP32, a jailbroken
  Kindle), all painting different dashboards from the same Tesserae
  server. Moved the five admin / UI screenshots that lived inline
  in the README (HA hub, composition, paper calendar, bedside,
  widget sizing) to a new `docs/gallery.md` wiki page; the README
  now points at the gallery instead of carrying all five inline.
- **Wiki nav restructured for clarity.** New top-level "Gallery"
  page collects the admin UI shots. The two existing widget pages
  (previously "Gallery (bundled)" and "Gallery (community
  catalog)") are renamed to "Bundled widgets" and "Community
  catalog" so the word "Gallery" only means one thing now.

## [0.44.3], 2026-06-09

### Changed

- **TRMNL Add-device + token-reveal copy aligned with the 0.44.1+
  auto-provision model.**
  - The Add-device card now opens with an info paragraph telling
    users that **native TRMNL hardware** (XIAO DIY kit, commercial
    TRMNL devices) doesn't need the manual form, those clients
    auto-register the moment they poll. Only the **KOReader on
    Kindle** path needs a manual add (where the user types the
    access token on the Kindle's on-screen keyboard).
  - The one-shot token-reveal modal dropped its "or native TRMNL
    app config" line (no such app exists for BYOS) and now
    explicitly notes that native TRMNL hardware ignores this token
    entirely, the modal is purely for KOReader users.

  Copy-only fix; no contract change.

## [0.44.2], 2026-06-09

### Fixed

- **`/api/display` now auto-provisions when it sees a novel MAC.**
  0.44.1 made the box-fresh device flow work via `/api/setup`, but
  the official TRMNL firmware caches its `api_key` in flash and only
  hits `/api/setup` on first boot. A device that had already cached
  a bad / placeholder token (from a pre-0.44.1 Tesserae) would keep
  polling `/api/display` with that token, get rejected, and land in
  the Discovered strip — defeating the auto-provision flow.

  Now `/api/display` runs the same auto-provision logic when it sees
  a MAC (``Id`` header) that doesn't match any existing device.
  Result: any TRMNL client polling Tesserae with its MAC ends up
  registered after exactly one poll, regardless of which endpoint
  it called.

  The auto-provision helper is factored out so both `/api/setup` and
  `/api/display` use the same code path; no behaviour drift between
  the two endpoints.

## [0.44.1], 2026-06-09

### Changed

- **Full Terminus BYOS parity: TRMNL devices auto-provision by MAC.**
  After reading the official Terminus reference implementation
  ([usetrmnl/terminus](https://github.com/usetrmnl/terminus)),
  Tesserae's flow was off in two ways:

  1. **Auth model.** Terminus authenticates `/api/display` by the
     `Id` (MAC) header; the access token is optional. Tesserae was
     auth'ing by access token only.
  2. **Pairing.** Terminus auto-creates the device record on the
     first `/api/setup` call. Tesserae was parking it in the
     Discovered strip and making the admin click Register.

  The result was that a box-fresh TRMNL device pointed at Tesserae
  needed an admin two-step before it'd actually paint frames — not
  the "BYOS = device just works" experience users expect.

  Now:

  - `/api/setup` looks up the device by MAC. If novel, auto-creates a
    full TRMNL instance with the MAC stored on the manifest, mints a
    20-char alphanumeric `api_key` (matches Terminus's
    `SecureRandom.alphanumeric(20)`), and returns the credentials.
    The device immediately starts polling `/api/display` with a real,
    recognised token; no admin click.
  - `/api/display` resolves the device by MAC first, falls back to
    access-token lookup for KOReader (which doesn't send a MAC).
    Existing TRMNL-on-Tesserae installs keep working unchanged.
  - The 5-char typeable token form stays for the KOReader path
    (where the user types the token on the Kindle's on-screen
    keyboard); the new 20-char form is only used for native
    auto-provisioning where the device stores the key in flash.
  - `/api/display` response envelope now exactly matches Terminus's
    shape: dropped the invented `pending_status_change` and
    `network_diagnostics_url` fields (introduced in 0.44.0 from
    second-hand BYOS docs), added the official `firmware_version`
    field, kept everything else. `friendly_id` still surfaces in
    both `/api/setup` and `/api/display`.
  - `/api/setup` response now includes a `message: "Welcome to
    Tesserae."` field to mirror Terminus's shape.

  Backwards compatibility:

  - Existing TRMNL devices using token-based auth continue to work
    (token lookup is the MAC-miss fallback).
  - KOReader Kindle path is unaffected; it never sent a MAC.
  - Admin still sees all auto-created devices in the Devices list,
    can rename / delete / regenerate tokens as before.

  Tests:

  - `test_trmnl_api_setup_auto_provisions_native_device_by_mac`
  - `test_trmnl_api_setup_returns_same_credentials_for_known_mac`
  - `test_trmnl_api_setup_koreader_path_falls_back_to_discovery`
  - `test_trmnl_api_display_envelope_matches_terminus_shape`
  - `test_trmnl_api_display_auths_by_mac_when_id_header_present`

## [0.44.0], 2026-06-09

### Added

- **BYOS protocol Tier 1: full compliance with the official TRMNL
  contract.** Any TRMNL-compatible client (XIAO ESP32-C3 DIY kit,
  native commercial hardware, KOReader Kindle) now talks to Tesserae
  exactly the same way it talks to the upstream TRMNL service.

  Concretely:

  - **`POST /api/log/level`**: BYOS log-level config endpoint
    acknowledged with `200 OK` + a `log_level: "info"` default. Some
    native firmwares refuse to continue polling if this 404s.
  - **`friendly_id`**: every TRMNL device now gets a six-character
    uppercase id (e.g. `7B3X9K`) auto-populated at instance creation,
    picked from an alphabet that omits ambiguous glyphs (0/O, 1/I/L).
    Surfaced in both `/api/setup` and `/api/display` responses so
    firmwares can show it on their setup / about screens. Older
    devices (pre-0.44.0) fall back to the instance id cleanly.
  - **Optional `/api/display` envelope fields**: `image_url_timeout`,
    `pending_status_change`, `network_diagnostics_url` are now in
    every response. Some native firmwares parse them; harmless to
    unaware clients (they ignore unknown fields).

  Tier 2 (firmware OTA) and Tier 3 (TRMNL recipe / plugin ecosystem)
  filed as [#11](https://github.com/dmellok/tesserae/issues/11) and
  [#12](https://github.com/dmellok/tesserae/issues/12); BMP format
  negotiation as [#13](https://github.com/dmellok/tesserae/issues/13).
  None of those are needed for the XIAO DIY kit, native TRMNL, or
  KOReader, which all accept PNG.

### Fixed

- **`device_loader` now carries `friendly_id` through** alongside
  `access_token`. Without this, the field that `device_service`
  writes to the instance JSON would be stripped when the loader
  merges instance overrides on top of the kind manifest.

## [0.43.7], 2026-06-09

### Fixed

- **`ruff format` CI failure.** A late-breaking comment edit in
  `app/settings/devices_routes.py` had drifted from the formatter's
  preferred wrap. No behaviour change.

## [0.43.6], 2026-06-09

### Fixed

- **`/api/setup` now mints real tokens for unrecognised TRMNL
  clients.** The official TRMNL firmware contract is: device sends
  its MAC in the `Id` header to `GET /api/setup`, server hands back
  an `api_key` the device stores locally and uses for every
  subsequent `/api/display` poll. Tesserae was literally returning
  the string `paste-a-server-issued-token-into-your-client` as the
  api_key when the device's incoming token didn't resolve, which the
  firmware then dutifully cached as its access token forever. Now
  `/api/setup` mints a fresh short-form token, records a Discovered
  entry pre-populated with the new token + MAC + Model + panel dims,
  and hands the real token back to the device. The device transitions
  from "polling with a real token" to "polling with a recognised
  token" the moment the admin clicks Register in the Discovered
  strip — no firmware-side reconfig, no captive-portal revisit, no
  token re-entry. Matches the BYOS contract every official TRMNL
  variant follows (XIAO DIY kit, native hardware, KOReader Kindle).

  The placeholder-detection added in 0.43.5 stays in place as
  defence-in-depth (e.g. a non-official firmware that doesn't honour
  the `/api/setup` response), but the bug it was working around is
  now gone at the source.

## [0.43.5], 2026-06-09

### Added

- **Official TRMNL DIY-kit (XIAO-based ESP32-C3) headers parsed.** The
  TRMNL header parser now picks up `Id` (MAC) and `Model` (board
  identifier, e.g. `xiao_epaper_display`) and surfaces both in the
  device card's Diagnostics block alongside battery, RSSI, and
  firmware. Lets a glance distinguish the official DIY kit from a
  Kindle running KOReader.

### Fixed

- **TRMNL placeholder-token pairing UX.** A client polling with the
  firmware's literal placeholder token (e.g.
  `paste-a-server-issued-token-into-your-client`) used to be
  registered as-is, which left the new device's access secret a
  publicly-known string. Now the discovery layer detects placeholder
  patterns, flags `needs_pairing: true`, and the register flow mints
  a fresh token instead of preserving the placeholder. After
  registration the existing one-shot reveal modal pops with the new
  token AND the device's polling IP, so the user knows exactly where
  to paste it ("the device polled in from `192.168.50.125`, open its
  config UI there"). The Discovered card also gains a "Unpaired,
  click Register to mint a token" pill, plus `Model` and `MAC` rows
  for at-a-glance hardware identification.

- **Discovery synthetic IDs prefer MAC over token.** Previously the
  Discovered card's id was `trmnl_<first-20-chars-of-token>`, which
  drifted between reboots if the token changed (and looked weird if
  the token was a placeholder). Now keyed off MAC when the client
  provides one, so the same physical device always resolves to the
  same Discovered row.

## [0.43.4], 2026-06-09

### Changed

- **Renamed "Add-on" → "App" in user-facing docs and prose.** Home
  Assistant rebranded "Add-ons" to "Apps" in its 2026 UI refresh
  (Settings → **Add-ons** → Settings → **Apps**; Add-on Store →
  **app store**). Updated the README, the install guides
  (`docs/install/home-assistant.md`, `docs/install/spotify-home-assistant.md`,
  `docs/install/server.md`, `docs/install/devices.md`),
  `docs/index.md`, `.github/SECURITY.md`, the SECURITY versions
  table to include 0.38–0.43, and two user-visible strings in
  `templates/onboarding.html` and `app/settings/index_routes.py`
  (the broker blurb).

  Companion `homeassistant-tesserae-addon` repo's README updated
  in lockstep.

  Internal references (Python code comments, log lines,
  `HA_INGRESS_MODE` config keys, the `homeassistant-tesserae-addon`
  repo slug, the `sync-addon` workflow name, the Supervisor
  `config.yaml` schema) stay as-is, those are platform-contract
  names and historical code paths, not user-visible labels.
  CHANGELOG history is preserved unchanged (the language was
  accurate at shipping time).

## [0.43.3], 2026-06-09

### Fixed

- **`clock_word` capitalisation no longer mixes cases.** Phrasing
  tokens were stored ALL CAPS in `MIN_WORDS`; hours were Title Case
  in `HOUR_WORD`; the renderer `.toLowerCase()`'d the prefix and
  suffix but left the hour Title-cased, producing "twenty past
  Three" and "three o'clock"-without-apostrophe (the source said
  `OCLOCK`). Now every token is lowercase, the renderer
  capitalises the first letter of the joined sentence, and the
  word "o'clock" gets its apostrophe back. Output reads
  consistently as "Twenty past three" / "Quarter to eleven" /
  "Three o'clock".

## [0.43.2], 2026-06-09

### Removed

- **`firmware-prompts/sleep-until-clock-skew-fix.md`.** The firmware
  fix shipped on the user's ESP32 build; the handover prompt was a
  point-in-time artefact and is no longer needed in-repo. The
  defensive server-side fallback from 0.43.1 stays in place to
  protect anyone else who hits the same firmware-side bug pattern;
  the prompt itself is preserved in git history at the v0.43.1 tag
  if anyone needs the full diagnostic context later.

## [0.43.1], 2026-06-09

### Fixed

- **Smart sync: defensive fallback when `sleep_until` disagrees with
  `next_sleep_s`.** Real-world firmware (ESP32) was publishing both
  fields on every heartbeat, but the absolute `sleep_until`
  timestamp didn't match the relative `next_sleep_s` duration. The
  server-side priority chain trusted `sleep_until` first, so it
  predicted wakes 5+ minutes out for devices actually sleeping 60s,
  producing a constant `-307s` offset that never let confidence
  ramp.

  Server now checks `abs((sleep_until - received_at) - next_sleep_s)`
  on every heartbeat that carries both fields. If the disagreement
  exceeds 30 seconds, `sleep_until` is rejected as untrustworthy
  (almost certainly clock skew at compute time) and `next_sleep_s`
  is used for the prediction. A `WARNING` log line records the
  disagreement so the firmware bug stays discoverable.

  Firmware-side handover prompt for the underlying bug is at
  [`firmware-prompts/sleep-until-clock-skew-fix.md`](firmware-prompts/sleep-until-clock-skew-fix.md)
  in the repo.

## [0.43.0], 2026-06-08

### Added

- **29 new bundled themes + a Gradient family + a Vivid family.**
  - 4 vivid linear-gradient surfaces (Sunset, Aurora, Twilight, Spectrum).
  - 10 subtle gradients (Coral, Mist, Sand, Sage, Linen, Mauve, Marble,
    Glacier, Honey, Pearl) — each with bespoke accents derived from
    its own gradient hue, not a shared Light-theme palette.
  - 15 vivid flat surfaces (Tangerine, Lime, Cobalt, Magenta, Emerald,
    Crimson, Cyan, Aubergine, Mustard, Teal Pop, Hot Pink, Lavender
    Pop, Olive Pop, Burgundy, Forest) — brightened canvases with
    accents that harmonise with each canvas hue.
  - New `--surface-gradient` opt-in CSS token (falls back to flat
    `--surface`), so existing themes are unaffected and any future
    theme can paint a vivid gradient backdrop on `.w` cards.
- **Theme builder gradient support.** UserTheme grew
  `gradient_enabled` / `gradient_a` / `gradient_b` / `gradient_angle`;
  the Colour palette card has a "Card-surface gradient" switch + two
  stop colour pickers + a Tesserae-styled angle slider that live-
  updates the preview. The gradient subsection disables itself when
  the switch is off.
- **Mobile tab shell on the Themes page.** Below 900px the 3-column
  layout collapses to a tabbed view (Themes / Edit / Preview) so
  each task gets full viewport focus. Desktop layout is unchanged.
- **Tesserae-themed scrollbar globally.** 12px-wide, soft track
  (`color-mix` 14% of the foreground), rounded pill thumb in
  `--t-fg-soft` with a min-height grab target. Firefox + Webkit
  covered.

### Changed

- **Themes page UI polish.**
  - Colour palette card now lays each field as `label | swatch`
    (label left, 72×36px chrome-wrapped swatch right) instead of
    label-above-tiny-swatch. ~10 fields fit where 4 used to.
  - Gradient subsection's angle slider sits on its own full-width
    row below the two stop swatches, with a "Angle 135°" header
    line and a Tesserae-styled `.ts-range` thumb / track.
  - Theme strip is now `position: sticky` with a viewport-bound
    `max-height` and an always-visible scrollbar — no JS, no
    race conditions on read-only views, no "list runs past the
    palette card" overflow.
- **User themes appear in the page editor's theme picker.** The
  editor route was passing `user_themes=None` to `build_registry`,
  silently dropping every custom theme from the dropdown. Now
  pulls from `USER_THEMES_STORE` like the Themes admin route
  already did.

### Removed

- **base16 family + all 10 base16 themes.** Gruvbox / Solarized /
  Dracula / Catppuccin Mocha / Monokai / Tomorrow / One Dark are
  no longer bundled. Dashboards using a base16 theme will fall
  back to Light on next load; the equivalent code-editor palette
  can be rebuilt in the theme builder or pinned by saving the old
  values as a user theme before upgrading.
  `static/style/spectra-base16.css` is deleted along with the
  registry entries and template `<link>` references.

## [0.42.3], 2026-06-08

### Changed

- **README + bundled plugin descriptions refreshed for the 0.38–0.42
  state.** The README's bundled widget count went from 58 (pre
  slim-down) to 30 plus the community catalog, the top-nav rename
  ("Plugins" → "Widgets") propagated to user-facing copy, and new
  surfaces (smart sync, `design.palette`, `requires:` capabilities,
  marketplace install persistence) got mentions in the feature list.
  Five bundled plugin manifests (`calendar_day`, `calendar_week`,
  `calendar_month`, `picture_gallery`, `todo`) had a "manage at
  Plugins → …" hint in their description that's now "manage at
  Widgets → …". Auto-generated widget gallery regenerated to pick up
  the new text.

## [0.42.2], 2026-06-08

### Fixed

- **Marketplace widgets no longer wiped by Docker / HA Add-on image
  upgrades.** Prior to this release, `Marketplace.install` wrote new
  widget folders to the same path the bundled widgets live at
  (`/app/plugins/`), which is inside the Docker image layer. Every
  image upgrade (HA Supervisor pull, `docker compose pull`) replaced
  that layer, wiping anything the user installed via Browse community
  widgets while leaving the rest of `/data/` (pages, schedules,
  settings) intact.

  Now:
  - Marketplace installs write to `<data_root>/marketplace/<id>/`,
    which is on the persistent volume (`/data/marketplace/` in HA,
    `/app/data/marketplace/` in standalone Docker, `data/marketplace/`
    in bare-metal installs).
  - The plugin loader walks both the bundled dir (`/app/plugins/`,
    immutable, shipped with each image) and the user marketplace dir,
    merging the results. Bundled wins on duplicate ids with a logged
    warning so the admin notices and resolves manually.
  - Marketplace's install collision check also looks at the bundled
    dir, refusing to install a catalog entry whose folder name
    clashes with a shipped widget.

  **Migration for existing HA / Docker users**: marketplace widgets
  installed before 0.42.2 are gone from the filesystem (the image
  upgrade did that), but their `marketplace.json` records still
  exist. On the first 0.42.2 Browse visit, those entries show as
  installed but the actual code is missing; click Uninstall to drop
  the stale record, then Install to land the widget at the new
  persistent path. Future upgrades preserve installs.

  Bare-metal / git-clone installs aren't affected by the original
  bug (no image layer to wipe) but pick up the new path on upgrade
  too. Existing marketplace widgets at `plugins/` keep working until
  the user moves them to `data/marketplace/` (or just reinstalls).

## [0.42.1], 2026-06-08

### Fixed

- **mypy strict on `app.state.device_telemetry`.** The 0.42.0 ship
  failed CI on a missed type annotation: `effective_interval` was
  inferred as `int` from the firmware-published branches but the
  no-signal else branch assigns `prev.last_sleep_interval_s` which
  is `int | None`. Added the explicit union annotation. No behaviour
  change.

## [0.42.0], 2026-06-08

### Added

- **Smart sync (JIT rendering)** — opt-in per schedule. When enabled
  on an interval schedule, the scheduler consults each bound device's
  telemetry-derived `predicted_next_wake_at` and fires within
  `smart_sync_lead_s` seconds of a trusted device's wake instead of
  on a fixed cadence. The rendered frame is waiting for the panel
  when it wakes, rather than being rendered after the panel paint.
  Falls back to plain interval firing when no bound device is
  trusted yet (warm-up window) or when the schedule has no device
  bindings. `interval_minutes` stays in force as a floor so smart
  sync can't push faster than the configured cadence. Tracked in
  [#10](https://github.com/dmellok/tesserae/issues/10).

  Device telemetry plumbing:
  - New `app/state/device_telemetry.py` persists per-device
    derived state (`predicted_next_wake_at`, confidence counter,
    last-wake offset). One JSON file under
    `data/core/device_telemetry.json`.
  - `devices/esp32_client/device.py` parses optional
    `sleep_until` / `next_sleep_s` heartbeat fields. Firmwares can
    publish either for accurate predictions; absent both, the server
    falls back to the device's configured `sleep_interval_s`.
  - Heartbeats arriving within 10s of the previous one are
    debounced (some firmwares send a connect-beat + a sleep-beat
    per wake; without this, the second beat sets offset ≈
    -sleep_cycle and confidence never accumulates).
  - Confidence ramps on each on-time wake (±60s tolerance), resets
    on a miss. Three consecutive on-time wakes = trusted.

  Admin surface:
  - **Schedule list dot** (issue #10 follow-up request): green
    (active) / yellow (warming) / red (blocked) indicator per
    schedule row showing smart-sync readiness at a glance. Tooltip
    explains the current state.
  - **Schedule form**: smart sync toggle + render-lead input.
  - **Device admin card**: always-on Smart sync section with a
    plain-English reason line ("Last wake missed the prediction by
    Xs", "1/3 consecutive on-time wakes", etc.) so you can diagnose
    why a device isn't trusted yet.

### Changed

- **Widget card borders removed.** The 1px outer frame on `.w` is now
  off by default. The matting gap (page-level `gap` + the white
  `bleed_color` default that landed in 0.41.2) provides cleaner
  cell separation than a thin line that doesn't dither well on
  e-ink, especially around rounded corners. The `--edge-weight` +
  `--edge` Spectra tokens stay defined for anyone who wants to
  re-enable the frame in a custom style; `.w-title` and `.w-body`
  internal accents continue to use `--edge` for dividers. If your
  dashboard uses `gap: 0`, set a few pixels of gap or the cells
  will appear seamless.
- **`weather_now_scenic` light presets switched to dark text.** The
  three light-background presets (snow, partly_day, cloudy_day) now
  paint deep-navy / slate text on their gradients rather than white,
  so the temperature reads at a glance on every preset without a
  shadow workaround. The snow preset's gradient also brightens a
  touch and its snowflake glyphs flip to translucent dark blue so
  they show on the lighter bg.

### Fixed

- **Corner radius slider now live-updates the preview.** The editor
  was sending `corner_radius` in its postMessage patch but
  `applyPagePatch` in the composer never applied it; you had to
  reload to see the new shape. The handler now writes both
  `border-radius` and the `--cell-corner-radius` CSS variable to
  every mounted `.cell` so the preview tracks the slider live.
  Note: `gap` still requires a reload (matting padding gets baked
  into cell `x/y/w/h` server-side).
- **Battery indicator popover now dismisses cleanly.** Auto-closes
  5 seconds after opening (matches the flash-notification timing)
  and closes immediately on any click outside the indicator or its
  panel.

## [0.41.2], 2026-06-08

### Fixed

- **Widget inner border now follows the cell's corner radius.** When
  a page had a non-zero `corner_radius`, the cell rounded its
  corners + `overflow: hidden` clipped the inner widget's 1px border
  rectangle at the curves, so the border looked truncated at each
  corner. The cell now exposes its radius as a `--cell-corner-radius`
  CSS variable that crosses the shadow DOM boundary, and `.w` in
  `spectra-widgets.css` uses it as the default for its own
  border-radius. The widget's outer edge now curves to match the
  cell.

### Changed

- **Matting colour default → white.** Pages used to default to
  `bleed_color = ""` which fell back to `var(--bg)` (theme-following)
  and showed as black in the editor's colour picker (the macro's
  fallback). New pages now default to `#ffffff` so the picker
  starts on a sensible value. Existing pages with the empty default
  read as white in the editor too; rendering is unchanged for any
  page that already has an explicit colour set.

- **`picture_gallery` renamed for the widget picker.** Was "Gallery
  Core" landing under the "Other" group; now "Picture, Gallery" so
  the picker's split-on-comma convention groups it under Picture
  next to NASA APOD. Same widget, same id, no migration.

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
  | Finance | finance_crypto, finance_currency, finance_stock | [dmellok/tesserae-widget-finance](https://github.com/dmellok/tesserae-widget-finance) |
  | Sky | sky_air_traffic, sky_aurora, sky_bom_warnings, sky_moon | [dmellok/tesserae-widget-sky](https://github.com/dmellok/tesserae-widget-sky) |
  | Weather Extras | weather_air_quality, weather_pollen_count, weather_wind | [dmellok/tesserae-widget-weather-extras](https://github.com/dmellok/tesserae-widget-weather-extras) |
  | Picture Extras | picture_unsplash, picture_apple_album | [dmellok/tesserae-picture-extras](https://github.com/dmellok/tesserae-picture-extras) |
  | Clock Extras | clock_qlock, clock_world | [dmellok/tesserae-widget-clock-extras](https://github.com/dmellok/tesserae-widget-clock-extras) |
  | Monitoring | glances_core, glances_status, octoprint_status | [dmellok/tesserae-monitoring](https://github.com/dmellok/tesserae-monitoring) |
  | Public Transport | public_transport_times | [dmellok/tesserae-widget-transport](https://github.com/dmellok/tesserae-widget-transport) |

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

  Source repo: [dmellok/tesserae-widget-github](https://github.com/dmellok/tesserae-widget-github).
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

  Source repo: [dmellok/tesserae-widget-spotify](https://github.com/dmellok/tesserae-widget-spotify).
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
  [dmellok/tesserae-widget-f1](https://github.com/dmellok/tesserae-widget-f1).
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

- [tesserae-device-pi-bin](https://github.com/dmellok/tesserae-device-pi-bin)
 , default id `pi_bin`.
- [tesserae-device-pi-png](https://github.com/dmellok/tesserae-device-pi-png)
 , default id `pi_png`.
- [tesserae-device-esp32-bin](https://github.com/dmellok/tesserae-device-esp32-bin)
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
