# Changelog

All notable changes to the `tesserae-mcp` bridge. Versions from 0.5.0 onward are
on [PyPI](https://pypi.org/project/tesserae-mcp/) and their dates are the PyPI
upload dates; each is tagged `mcp-v<version>` in the
[tesserae](https://github.com/dmellok/tesserae) monorepo. Versions before 0.5.0
lived in the standalone `dmellok/tesserae-mcp` repo (now archived) and were
never published.

Since 0.11.0 the handshake instructions and canvas doc-shape are served live by
Tesserae from `/api/mcp/instructions`, and since 0.14.0 so are the per-tool
descriptions. A bridge release is therefore only needed for a new tool, a change
to how the bridge handles a result, or a refresh of the embedded fallback text.
The Tesserae version each release tracks is the one on the same commit; see the
main [CHANGELOG](../../CHANGELOG.md) for the server side of each change.

## [Unreleased]

### Fixed

- A slow call no longer holds up every other tool call. Each tool ran directly
  on the server's one event loop, so while a `render_preview` or
  `push_to_device` waited on Tesserae (up to tens of seconds for a first render),
  every other call, including ones the agent issued in parallel, sat in a queue
  behind it and the client showed the bridge as unresponsive. Tools now run on a
  worker thread, so a `list_pages` sent mid-render returns in milliseconds
  instead of after the render.

## [0.17.0], 2026-09-09

### Added

- `add_font`, `list_fonts` and `delete_font`. A Google Fonts family (chosen
  weights and styles, latin subset by default) or a single face from a direct
  woff2/ttf/otf URL is fetched once by Tesserae and cached server-side, then
  resolves in canvas pages and code elements like a bundled font. Renders never
  touch the network for it.

## [0.16.0], 2026-09-07

### Changed

- `list_widgets(section="appearance")` fetches the theme, style and font lists
  from the new `/api/mcp/appearance` endpoint. The catalog itself now carries
  only counts for those, which takes roughly a sixth off every default
  `list_widgets` read. An older bridge against a newer Tesserae returns counts
  where the docs promise lists, so upgrade together.
- The served tool docs, canvas shape and `measure_text` text point at the
  appearance section instead of `list_widgets().appearance`.

## [0.15.0], 2026-08-29

### Fixed

- The default `list_widgets` summary dropped every widget description. The
  catalog names the field `desc` and the summary keys only carried
  `description`. The key is added and the docs describe the first-sentence
  convention (the full text comes back from `get_widget_options`).

## [0.14.0], 2026-08-23

### Added

- Per-tool descriptions are served, not baked in. `/api/mcp/instructions` now
  carries `tool_docs` and the bridge prefers a served description over its
  embedded docstring, so a wording fix reaches an installed bridge on the next
  agent connection. Additive key, no schema bump, so older bridges keep reading
  the payload.
- An out-of-date bridge says so in the agent session. The bridge compares its
  own version with the `bridge.latest` the server reports and, when behind,
  appends a note to the handshake instructions asking the operator to run
  `pipx upgrade tesserae-mcp`. Silent when level, ahead (running from a clone)
  or unparseable.
- `set_canvas` gains a docstring, so every registered tool has a description to
  fall back on.

## [0.13.0], 2026-08-23

First release since 0.12.0 went to PyPI on 31 July; everything below had been
sitting on `main`.

### Fixed

- `probe_widget_data` truncates long lists. A large payload (a 24-hour Home
  Assistant history, say) overflowed the MCP result cap with no truncation,
  leaving the whole result unusable. Lists are capped at `max_items` and each
  trimmed list gains a sibling note with its real length; scalars, short lists
  and the bindable field paths are never trimmed, and `full=True` returns
  everything.
- `list_widgets` summarises the catalog. The full catalog is about 95k
  characters and overflowed the result cap; each widget is now trimmed to its
  identity, description and fragments, with `full=True` for the whole thing.
  The appearance and library blocks pass through untouched.
- Every bridge call already sent its version in `User-Agent`; Tesserae now
  records it and shows a "Connected bridge" card under Settings, System, MCP
  when the bridge is behind the release the server ships. A test fails the
  build if the server's expected version drifts from this package.
- The install command in the README and the missing-SDK error pointed at the
  archived standalone repo. Both now say `pipx install tesserae-mcp`.

### Docs

- The canvas doc-shape names the element discriminator (`kind`) and expands
  the element shape, so an agent no longer has to read an existing page to
  learn the field. Unknown fields such as `type` 422 rather than being ignored.
- `create_schedule`'s contract corrected: `fires_at` is a full datetime whose
  date is a placeholder (only the time of day is read) and `name` is required
  alongside `id`. The old doc described `fires_at` as `"HH:MM"` and omitted
  `name`, so a schedule written straight from it failed twice over.
- `render_report` documents the `injected_libs` section and the CSS
  diagnostics (rules the browser dropped from an element's authored CSS), and
  the `autolibs: false` opt-out for library injection.

## [0.12.0], 2026-07-31

### Added

- `render_report(debug=True)` returns render diagnostics: console errors from
  every frame (code-element sandbox errors tagged with the element id),
  uncaught page errors, failed and 4xx/5xx requests with URLs, per-font-face
  load status, authored CSS the browser silently dropped, per-element library
  injection, and the settle record that gated the screenshot.
- `render_preview` and `render_report` accept `fresh=True` to bypass the
  last-good fallback and widget data caches.
- `render_report` always includes `icon_invalid`, mirroring `tap_invalid`:
  unknown Phosphor slugs or weights on icon elements, bad icon-transform bind
  values, and a markup scan for `ph-<name>` classes that are not real icons,
  each with element id and reason.

### Docs

- The regular Phosphor weight needs the compound class pair; a wrong slug or
  weight renders as a silent blank. Both are now spelled out.

## [0.11.1], 2026-07-31

### Added

- `list_icons(q, limit)` searches the Phosphor icon set so an agent can find a
  valid slug instead of guessing one. The search normalises its query (a `ph-`
  prefix is stripped, underscores become dashes).

### Docs

- Embedded fallback text synced with the served instructions (Sankey chart
  type, icon-search pointer).

## [0.11.0], 2026-07-27

### Changed

- The handshake instructions and canvas doc-shape are fetched from Tesserae at
  startup (`GET /api/mcp/instructions`) and used verbatim. The embedded copy
  remains as a per-key fallback for when Tesserae is unreachable, the MCP
  experiment is off, or the payload schema is unknown. Doc changes now go live
  on the next agent connection with no bridge release.

## [0.10.3], 2026-07-27

### Docs

- The bridge leads with the touch v3 primitives: a button, switch, slider or
  stepper is authored as a typed element (`{"kind": "button"}`) by default, and
  `on_tap` / `on_swipe` is reframed as making an existing element tappable.
  Stale experiment-gate wording dropped.

## [0.10.2], 2026-07-27

### Docs

- A device-owned touch primitives section in the agent docstring, so agents
  discover the button / switch / slider / stepper kinds and their bindings. A
  test confirms each round-trips through the element API and that a mistyped
  binding field surfaces as a 422.

## [0.10.1], 2026-07-25

### Docs

- On protocol v2 panels the region budget is a hard cap: the device hit-tests
  locally against a manifest trimmed to `max_targets`, so zones beyond the
  budget do not fire at all. The instructions and `list_devices` description
  state the cap, the trim priority, the advise-count-first guidance, and the
  current firmware budget of 64.

## [0.10.0], 2026-07-25

### Docs

- Protocol v2 on the MCP surfaces: `list_devices` exposes the `proto`
  capability next to `overlay` and `deck_cache`, the instructions explain
  stable region ids and `data-touch-id` pinning, and `render_report` documents
  the `touch_id` field on `tap_regions`.

## [0.9.1], 2026-07-25

Includes the unpublished 0.9.0 bump.

### Docs

- Schema-2 overlay capability and slot grammar: `overlay.schema` (post-action
  frame patches versus a debounced re-push), attribute-path slot keys,
  `data-overlay-map`, and code-element slot extraction. The claim that the
  extractor skips code-element iframes is removed.

## [0.8.2], 2026-07-24

### Changed

- `create_deck` derives the navigation graph from `page:<id>` links on page
  elements when `graph` is omitted.

## [0.8.1], 2026-07-23

### Changed

- `list_devices` reports each device's `kind` and its `overlay` / `deck_cache`
  firmware capabilities, including the overlay target budget
  (`overlay.max_targets`).
- `render_report` extracts `overlay_slots` alongside `tap_regions`.

### Docs

- The live-value-slot vocabulary and its server-enforced caps are documented in
  the handshake instructions.

## [0.8.0], 2026-07-21

### Added

- Scheduling and navigation tools: `list_rotations`, `create_rotation`,
  `delete_rotation`, `list_schedules`, `create_schedule`, `delete_schedule`,
  `list_decks`, `create_deck`, `delete_deck` and `suggest_decks`. Create
  validates against the model and returns 422 with field-level detail.
  `suggest_decks` proposes a deck from the `page:<id>` links already on
  elements. Deck pages accept an optional `refresh_interval_minutes`.

### Docs

- A wire-up section covering the three primitives.

## [0.7.0], 2026-07-18

### Added

- `describe_actions` returns the authoritative touch-action vocabulary so it is
  not reverse-engineered from examples.
- `add_elements_bulk` appends many elements in one all-or-nothing save, for
  large primitive boards built in chunks.
- `render_report` accepts `view="touch"` and `fields=` to trim the response on
  large boards.

### Docs

- `on_swipe` and flat Home Assistant action shapes corrected in the doc-shape.

## [0.6.3], 2026-07-17

### Docs

- Home Assistant touch actions: the canonical shape, the shapes Tesserae now
  accepts (a service implies `action: ha`, a top-level `entity_id` is hoisted
  into `data`, dotted services split, HA-native `target`, `$value` as well as
  `{value}`), the executor, and `render_report`'s new `tap_invalid` list of
  regions whose stored action would not dispatch.

## [0.6.2], 2026-07-17

### Changed

- `list_devices` surfaces `touch: true` on panels with a digitizer (the
  reTerminal E1003), so an agent can tell whether tap, swipe and slider
  actions will fire on a target.

## [0.6.1], 2026-07-17

### Docs

- An INTERACTIVITY section in the handshake instructions, so an agent learns
  touch actions exist without reading the full doc-shape.

## [0.6.0], 2026-07-17

### Docs

- A TOUCH ACTIONS section in the canvas doc-shape: `on_tap`, `on_swipe` and
  `on_slide` on any element (string specs, HA service-call objects, `{value}`
  slider substitution), the `hotspot` kind, and code-element named actions
  referenced from markup as `data-on-tap="@name"`. `render_report` documents
  `tap_regions` and `tap_dangling`.

## [0.5.14], 2026-07-17

### Docs

- The instructions open with a START step: pick the panel, size the canvas to
  it, and call `bind_devices` right away rather than only at push time, then
  add an empty code element and stream it in with `append_code`, previewing
  early, instead of one large `set_canvas` at the end.

## [0.5.13], 2026-07-16

### Docs

- Bundled fonts can be used by family name inside the code element sandbox.

## [0.5.12], 2026-07-16

### Docs

- All six Phosphor weights (thin, light, regular, bold, fill, duotone) are
  available in the code element.
- The guidance now defaults to the code element for anything beyond a trivial
  single-widget page.

## [0.5.11], 2026-07-15

### Added

- `bind_devices` persists a canvas's target device set (any number of panels)
  for scheduling and Send. `push_to_device` already fanned out.

## [0.5.10], 2026-07-14

### Docs

- Design in full colour, not just the panel inks. The panel dithers the
  composition down to its palette, so rich colours and gradients reproduce as
  blends; reserve exact palette hex for fine detail where dithering reads as
  speckle, and honour the mono flag. The old guidance flattened layouts to the
  few Spectra 6 / ACeP inks.

## [0.5.9], 2026-07-14

### Docs

- The code element sandbox permits remote images (`img-src` from the web), so
  an element can paint artwork it pulls from a source. Fetch, XHR and
  WebSocket stay blocked.

## [0.5.8], 2026-07-14

### Added

- `list_services` lists non-placeable "service" plugins (Open-Meteo, REST/JSON,
  Home Assistant) that expose an external API as a data source for code and
  data elements. Probing a service with empty options returns a
  self-describing scope map.

## [0.5.7], 2026-07-14

### Added

- `append_code` appends text to a code element's `html`, `css` or `js` and
  saves on each call, so an agent can stream an element in chunk by chunk and
  an open editor re-renders as it grows. Returns the field's new length.

## [0.5.6], 2026-07-13

### Docs

- Every vendored sandbox library documented with usage: Chart.js with the
  datalabels plugin, canvas-gauges, Day.js with utc and timezone, qrcode,
  marked, chroma.js, SVG.js and Phosphor icons. Each is inlined only when the
  code references it.
- `set_canvas_background` notes that the fal.ai key lives on the AI-image
  widget.

## [0.5.5], 2026-07-13

### Docs

- A DATA SOURCES section: a source is always a widget key plus options, `data`
  and `bind` take one, `code` takes many named ones as `ctx.data.<name>`,
  probe first, and a shared fetch is free. Chart.js is available in the code
  element as `window.Chart`.

## [0.5.4], 2026-07-13

### Docs

- Code elements take a list of named `sources`, each resolved server-side and
  injected as `ctx.data.<name>`, so one element combines data from several
  widgets. Rides the existing element endpoints, no new tool.

## [0.5.3], 2026-07-13

### Docs

- The `code` element kind: author HTML, CSS and JavaScript fed by a widget's
  live data as the `ctx.data` global, run once in a sandboxed iframe with no
  network and an opaque origin. Drivable through `add_element` and
  `set_canvas` with no new endpoint.

## [0.5.2], 2026-07-13

### Added

- `set_canvas_background` generates a full-bleed background image for a canvas
  from a text prompt through fal.ai. Widgets composite on top, so the data
  never passes through the image model.

## [0.5.1], 2026-07-13

### Added

- `delete_canvas_page` removes a canvas dashboard (canvas pages only).

### Fixed

- Every request to Tesserae sends an explicit `tesserae-mcp/<version>`
  `User-Agent`. (#106)

## [0.5.0], 2026-07-13

First PyPI release. The version was cut in the standalone repo and published
the same day from the monorepo, which is where the standalone repo's history
ends.

### Added

- The compose-agent loop (probe, place, preview, report, adjust, push) ships
  as MCP server instructions, so the agent receives the workflow at handshake.

### Changed

- The bridge moved from the standalone `dmellok/tesserae-mcp` repo into
  `packages/tesserae-mcp` of the Tesserae monorepo, so its tool list and
  doc-shape are tested against the `/api/mcp` surface they wrap in the same
  CI run. It still builds a thin wheel (stdlib plus the `mcp` SDK), published
  through PyPI trusted publishing on `mcp-v*` tags. Install is
  `pip install tesserae-mcp` (now `pipx install tesserae-mcp`).

### Docs

- Client configuration for Codex, Cursor, Windsurf, Cline, VS Code and the
  SDKs.

## [0.4.0], 2026-07-13

### Docs

- Live shape bindings documented in the canvas doc-shape.

## [0.3.0], 2026-07-12

Tracks the Tesserae 0.107.0 API.

### Added

- `update_element`, `delete_element` and `patch_canvas` for partial edits
  without re-sending the document.
- `render_report` for a machine-readable render check.
- `measure_text` and `arrange` for content-fit and intent-based layout.
- `get_widget_choices` pages through one option's choice rows.
- Writes accept `base_rev` from `get_canvas`; a page changed since returns 409
  instead of clobbering a concurrent edit.

### Docs

- `probe_widget_data`, `get_widget_options` and `list_devices` cover
  `data_source`, field paths, format hints and device colour capability.

## [0.2.0], 2026-07-12

### Added

- `probe_widget_data` shows a widget's live data before binding a data
  primitive.
- `add_element` appends one element per call for live builds.

### Docs

- Field-path grammar (index and pluck for charts), the data `format` option,
  and the `svg` element kind.

## [0.1.1], 2026-07-12

### Docs

- The `data` element kind (a widget field bound to text, number or graph) and
  the `html` kind (a sandboxed mini widget) added to the doc-shape.

## [0.1.0], 2026-07-12

### Added

- Initial stdio bridge with nine tools: `list_widgets`, `get_widget_options`,
  `list_devices`, `list_pages`, `create_canvas_page`, `get_canvas`,
  `set_canvas`, `render_preview` and `push_to_device`. A thin client over a
  running Tesserae's `/api/mcp` surface, depending only on the `mcp` SDK.
