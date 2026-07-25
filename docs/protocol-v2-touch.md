# Protocol v2: device-owned touch

Status: **design only** — no implementation. Audience: server + firmware
maintainers. Companion to `docs/dev/client-protocol.md` (the v1 contract,
which stays live for the whole v1 deprecation window).

Premises this design assumes:

- All *new* device work is REST over HTTPS. **Decision (2026-07-25): MQTT
  stays indefinitely** for existing back-compat devices — `app/transport.py`,
  `app/embedded_broker.py`, and the MQTT branches remain, and every v2
  surface gates on `_renderer_is_http_polled` exactly the way the v1
  patch code does today. v2 features are simply never offered over MQTT.
- Rendering stays server-side (Playwright -> per-renderer `.bin`/`.png`).
  v2 does not move layout to the device; it moves **hit testing, feedback,
  and state presentation** to the device.
- The v1 overlay work (schema 1 tap-echo + value slots, schema 2 frame
  patches, deck SD cache) shipped and works on the E1003. v2 is not a
  rewrite of those mechanisms; it is their unification under one manifest,
  one tier model, and one push channel.

---

## Part A — audit

Verdicts are against the v2 design: **KEEP** (survives as-is), **ADAPT**
(survives with a changed role or shape), **DELETE** (superseded; removed
at the end of the v1 window).

### Server: endpoints (`app/rest_api.py`)

| Item | What it does today | Verdict |
|---|---|---|
| `GET /frame` `touch_*` query params (~line 757) | Tap coordinates dispatched server-side *before* the frame lookup, so a nav action's repaint returns on the same wake | **ADAPT** — becomes an *action report* (`region_id` + digest + gesture + value); coordinates stay as diagnostics. The same-wake-repaint contract is dropped for v2 clients (feedback is already on glass) |
| `POST /tap` (~line 878) | Standalone stroke dispatch for continuously-powered clients | **ADAPT** — same report shape; keeps coordinate fallback for v1 clients |
| `GET /frame/overlay/<digest>` | Schema-1 overlay spec: tap-echo targets, value slots, atlas refs | **DELETE** — folds into the interaction manifest (§B1). Served until v1 window closes |
| `GET /frame/overlay/atlas/<digest>` | 4bpp glyph-atlas strips, content-addressed | **KEEP** — becomes the bitmap-font store for text regions (§B5) |
| `GET /frame/data` | Values doc + pending patch doc for a digest | **ADAPT** — values keyed by region id; also mirrored on the push channel (§B4) |
| `GET /frame/patch/<digest>` | Content-addressed patch blobs (`fb-rect`) | **KEEP** — tier-1 corrections and state-bundle tiles reuse this format |
| `/status` piggybacks: `overlay_values`, `overlay_patches`, `deck` envelope, `rotation` envelope | Per-beat sync state | **ADAPT** — collapses into one `sync` envelope (bundle digest, patch seq, values seq, proto version) |
| `GET /deck`, `GET /deck/frame/<digest>` | Deck SD-cache manifest + frame fetch | **ADAPT** — generalises into state bundles (§B3); deck becomes one bundle producer |
| `_auth_device`, Bearer tokens, `_parse_button_event_id` equality dedup | Auth + wake-event dedup (opaque RNG ids) | **KEEP** |
| `_ingest_deck_report` recency guard (`_render_older_than`) | On-glass report may not revert a pending push | **KEEP** — same guard applies to bundle-state reports |

### Server: dispatch (`app/button_service.py`, `app/button_actions.py`)

| Item | Today | Verdict |
|---|---|---|
| `handle_touch` guard chain: dedup / no_frame / stale / no_target / blocked | Server hit-tests the stroke against the region sidecar | **ADAPT** — v2 devices report a `region_id`; the server *validates* (id exists in that digest's manifest, action matches) instead of hit-testing. Coordinate hit-testing kept as the v1 path and as a cross-check diagnostic |
| `hit_test`, `classify_stroke`, swipe end-point forgiveness, `slide_value` (`app/touch_regions.py`) | Geometry resolution server-side | **ADAPT** — the algorithms move into the firmware spec (documented, not exported); server copies retained for v1 clients and for the touch monitor |
| Provenance gate (`is_side_effecting` + `origin != config` block) | Markup can't aim webhooks/HA calls | **KEEP** — enforced at manifest *build* time in v2: side-effecting actions from markup origin are never emitted into a manifest at all |
| `_dispatch_structured` + `_call_ha` | Synchronous HA call, outcome on the wake | **KEEP** — this is the tier-1 effect path |
| `_schedule_ha_reconcile` debounce worker + `_run_reconcile` page fallback chain | Post-action re-render -> patches or push | **KEEP** — this *is* the tier-1 confirm/correct loop |
| `_try_deck_button` / `_try_deck_touch` / `_deck_navigate` / `_reconcile_deck_frame` | Server-side deck nav + promote | **ADAPT** — v2 devices navigate bundles locally and *report*; these become the report-ingestion path (position accounting + promote), not the trigger path |
| `_spawn_prewarm` / precompose cache (`push.py`) | Speculative Playwright capture during linger | **KEEP** — cuts tier-2 latency; unchanged |
| Rotation/button dispatch (`_dispatch_spec`, `button_actions.dispatch`, `DEFAULT_BUTTON_MAP`) | Physical-button grammar | **KEEP** — buttons are not touch; unchanged. The action *grammar* (`page:`, `step:`, `webhook:` …) is shared vocabulary with manifests |

### Server: render-path branches + state

| Item | Today | Verdict |
|---|---|---|
| `EXTRACT_INTERACTIVE_JS` + code-element mirror collection (`touch_regions.py`, `static/panels/decorate.js`) | One Playwright pass extracts touch regions + value slots, incl. sandbox mirrors | **ADAPT** — same extraction feeds the manifest builder; new requirement: stable region IDs (§B1, open question 1) |
| Region/slot sidecars (`<comp>.regions.json`) | Persisted per composition digest | **ADAPT** — sidecar gains ids/feedback fields; becomes the manifest's source of truth |
| `overlay_sync.rect_to_wire` + `_panel_geometry` | Composition→wire transform chain | **KEEP** — manifests are wire-space, same as v1 specs |
| `overlay_sync.build_spec` (schema-1 doc) | Targets/slots/atlases doc | **DELETE** — manifest supersedes |
| `overlay_sync.build_atlas` / `pack_atlas_strip` / `browser_rasterizer` | Glyph atlas pipeline | **KEEP** — §B5 |
| `overlay_sync.values_document` (+ maps, attribute paths) | Pre-formatted value strings, ms seq | **ADAPT** — keyed by region id; delivered on the push channel too |
| `frame_patch.py` (composition-space diff, `fb-rect` blobs, caps) | Patch payloads | **KEEP** — unchanged; bundles reuse `fb-rect` for tiles |
| `push.py` patch store, `_build_patch_payload`, `_stage_patches_locked`, `_divert_to_patches_locked`, `shadow_render_page`, `_promote_shadow` | Post-action + chrome patches, digest stability | **KEEP** — tier-1 backbone. ADAPT: stamping a live render also stamps its manifest digest |
| `push.py` deck warm cache (`warm_deck_page`, `promote_deck_page`, `_deck_renders`) | Pre-rendered nav targets | **ADAPT** — becomes the bundle warmer |
| Overlay capability sticky ingest + facts persistence (`transport_wiring.py`, `device_facts.py`, `app_factory` seed) | `overlay.schema` survives restarts | **ADAPT** — grows a `proto` version field (§B7) |
| MQTT: `transport.py`, `embedded_broker.py`, MQTT publish branch in `_publish_artifact`, retained frame topics | Legacy transport | **KEEP** (decision 2026-07-25: MQTT stays for back-compat). `_renderer_is_http_polled` remains the gate; v2 surfaces are REST-only by that gate, never by MQTT removal |

### Widget/dashboard model: how interactivity is declared

| Item | Today | Verdict |
|---|---|---|
| Canvas element fields `on_tap` / `on_swipe` / `on_slide`, hotspot kind, code-element `actions` map + `@name` refs (`panels_schema.py`, editor, MCP) | The authoring surface | **KEEP** — no breaking change to the grammar. ADAPT: optional additive fields (`feedback`, `states`, `touch_id`) for tier-0 tiles and stable ids |
| Raw markup `data-on-tap` / `data-on-swipe` / `data-on-slide` (widgets) | Markup-origin regions, nav-only | **KEEP** |
| `data-overlay-key` / `-suffix` / `-map` slots | Live value slots | **ADAPT** — renamed "text regions" in v2 manifests; attribute vocabulary unchanged |
| MCP: `describe_actions`, `render_report` touch sections, bridge instruction blob | Agent-facing docs + verification | **ADAPT** — docs updated; `render_report` gains manifest ids so agents can assert stability |

### Tests

| File | Covers | Verdict |
|---|---|---|
| `test_touch_regions.py`, `test_touch_canvas.py`, `test_touch_grid.py` | Extraction, normalization, hit-testing | **ADAPT** (hit-test cases become v1-path + firmware-spec fixtures) |
| `test_touch_service.py`, `test_rest_touch.py`, `test_touch_reconcile.py`, `test_patch_end_to_end.py` | Guard chain, dispatch, reconcile worker, e2e patch flow | **ADAPT** — add region-id report path beside coordinate path |
| `test_overlay_sync.py`, `test_overlay_phase2.py`, `test_rest_overlay.py` | Schema-1 spec, slots, atlases, values | **ADAPT**; the `build_spec` shape assertions **DELETE** with the endpoint |
| `test_frame_patch.py`, `test_rest_frame_patch.py`, `test_push_patch_divert.py` | Diff, blobs, digest stability | **KEEP** |
| `test_deck_*.py`, `test_rest_deck.py` | Deck model, sync manifest, nav, prerender | **ADAPT** — become bundle tests |
| `test_button_actions.py`, `test_button_service.py`, `test_touch_capability.py`, `test_touch_monitor.py`, `test_history_button_detail.py` | Grammar, buttons, capability ingest, diagnostics | **KEEP** |

---

## Part B — design

### B1. Interaction manifest

One JSON document per served frame, replacing the schema-1 overlay spec.
Fetched at `GET /api/v1/device/<id>/frame/manifest?digest=<frame_digest>`
(Bearer); the `/frame` 200 response carries `"manifest": {"digest":
"<16-hex>", "url": ...}` so a device knows without a probe whether the
manifest changed (it usually doesn't when only pixels patch).

```json
{
  "proto": 2,
  "frame_digest": "8603c4372c14c7b9",
  "manifest_digest": "5f1e9c22ab34cd01",
  "regions": [
    {
      "id": "el:tile_desk:tap",
      "rect": {"x": 128, "y": 640, "w": 300, "h": 90},
      "gestures": {"tap": true},
      "action": {"tier": 1, "type": "ha"},
      "feedback": {"mode": "tiles", "set": "st:tile_desk", "cycle": ["off", "on"]}
    },
    {
      "id": "el:nav_next:tap",
      "rect": {"x": 1700, "y": 0, "w": 172, "h": 172},
      "gestures": {"tap": true, "swipe": {"left": true}},
      "action": {"tier": 0, "type": "nav", "target": "page:b2f1..."},
      "feedback": {"mode": "invert"}
    },
    {
      "id": "el:dimmer:slide",
      "rect": {"x": 100, "y": 900, "w": 60, "h": 400},
      "gestures": {"slide": {"axis": "y"}},
      "action": {"tier": 1, "type": "ha"},
      "feedback": {"mode": "slider", "track": "vertical", "value_text": "tx:dim_pct"}
    }
  ],
  "text": [
    {
      "id": "tx:dim_pct",
      "rect": {"x": 180, "y": 1060, "w": 120, "h": 40},
      "align": "right",
      "atlas": {"digest": "ab12...", "url": ".../frame/overlay/atlas/ab12...", "height": 32},
      "key": "ha:light.desk:attributes.brightness_pct",
      "max_chars": 5
    }
  ],
  "caps": {"max_regions": 32, "max_text": 8, "max_atlases": 2}
}
```

Rules:

- **Coordinates are wire-space** (post `rect_to_wire`), applied verbatim,
  exactly like v1 specs and patches.
- **The device never sees action payloads.** `action.type` + `tier` tell
  it how to behave; domains/services/URLs stay server-side (same
  provenance/security posture as v1 — a stolen manifest leaks geometry,
  not credentials). The device reports `{region_id, gesture, value?,
  frame_digest, event_id}` and the server resolves the payload from its
  own sidecar.
- **Stable IDs.** `el:<element_id>:<gesture-slot>` for canvas elements
  (canvas elements already carry ids). Markup-origin and code-element
  mirror regions get `mk:<hash>` where the hash covers the region's
  action spec + normalized rect bucket; authors can pin one with an
  optional `data-touch-id`. IDs are what tier-1 optimism and value
  routing key on, so churn is a correctness issue, not cosmetic
  (open question 1).
- **Manifest digest** hashes the document minus `frame_digest`, so a
  patch-only pixel change (same layout) leaves the manifest digest
  unchanged and the device re-anchors it to the new frame without a
  re-fetch.
- Caps mirror the firmware budget model: hard limits advertised by the
  device (`max_regions`, today 32), enforced at build time with the same
  nav-priority trimming and drop-with-log discipline as v1.

Local feedback vocabulary (`feedback.mode`) the firmware applies with no
network:

| mode | behaviour |
|---|---|
| `invert` | v1 tap-echo: invert rect, partial refresh, cleared by the next patch/frame that overlaps it |
| `tiles` | blit the next tile in `cycle` from the named state-set (§B3); position in the cycle is device-held until a patch/frame overrules |
| `slider` | draw the thumb/fill at the touch position within the rect; optional live `value_text` update through a text region |
| `none` | dispatch only (webhooks with no visual) |

### B2. Feedback tiers

Tier = what the panel shows before any server involvement. Every
interaction still *reports* to the server (dedup'd by event id); tiers
differ only in whether pixels wait for it.

- **Tier 0 — purely local.** The device already holds every pixel it
  needs. No correction expected.
- **Tier 1 — optimistic local.** The device shows its best guess
  immediately; the server confirms or corrects via a patch (or full
  frame) on the next sync. The v1 reconcile worker + `fb-rect` patch
  machinery is exactly this corrector.
- **Tier 2 — must round trip.** Nothing meaningful can be shown locally;
  the device shows the `invert` echo and waits for pixels.

Classification of every interaction Tesserae supports:

| Interaction | Tier | Justification |
|---|---|---|
| Tap-echo on any region | 0 | Pure acknowledgment; no state claim |
| `page:<id>` nav, bundle state cached | 0 | Target frame is on device (bundle/SD); blit + async report. Wrong only if the cached frame is stale, which TTL + bundle version already bound |
| `step:`/`rotate_next`/`rotate_prev`, warmed | 0 | Same as page nav once rotations publish their step frames as a bundle |
| `page:`/`step:` nav, cold (no cached state) | 2 | Nothing to blit; echo + fetch. Prewarm exists to make this rare |
| HA toggle with declared state tiles | 1 | Tile flips instantly; reconcile patch corrects if the service failed or an external change raced it. Justified because HA writes succeed overwhelmingly and the correction loop is already proven on hardware |
| HA toggle without state tiles | 1 (invert as the optimistic hint) | Inversion-parity is the current E1003 workaround; v2 keeps it working but prefers declared tiles |
| Slider (`on_slide` -> HA) | 1 | Thumb tracks the finger locally; value text blits from the local value; server corrects the true brightness on sync. Round-tripping a drag is unusable on e-ink |
| `webhook:<url>` | 0 for feedback, effect async | The device cannot and must not call the webhook (server-held URL, provenance gate); there is no pixel truth to show, so `invert`/`none` is complete feedback |
| HA action whose visual lives elsewhere on the page (e.g. tap lamp, badge elsewhere updates) | 1 | The badge is a text region or patch target; correction arrives on the same sync |
| `refresh` | 2 | Its meaning *is* "re-render and show me the truth" |
| `fetch_latest` | 2 | Explicit re-download of server pixels |
| Deck link nav (graph zones) | 0 cached / 2 cold | Identical to page nav; deck cache is the prototype |
| Value/clock updates (not touch-initiated) | 1-style stream | Server pushes strings; device blits via text regions; `local:` clock keys are tier 0 (§B5) |

### B3. Multi-state tiles (state bundles)

Generalises the deck SD cache: a **bundle** is the set of reachable
states for what a device is showing, pushed ahead of need so tier-0
navigation and tier-1 toggles blit locally.

```json
{
  "bundle_digest": "9c01d2e4f6a81b3c",
  "states": [
    {"kind": "frame", "state_id": "page:b2f1", "frame_digest": "cd15...",
     "manifest_digest": "77aa...", "bytes": 1314144, "ttl_s": 900},
    {"kind": "tile", "state_id": "st:tile_desk/on", "rect": {"x": 128, "y": 640, "w": 300, "h": 90},
     "tile_digest": "e4b0...", "format": "fb-rect", "bytes": 13500},
    {"kind": "tile", "state_id": "st:tile_desk/off", "rect": {"x": 128, "y": 640, "w": 300, "h": 90},
     "tile_digest": "a119...", "format": "fb-rect", "bytes": 13500}
  ],
  "links": {"page:home": {"swipe_left": "page:b2f1", "el:nav_next:tap": "page:b2f1"}}
}
```

- **Frames** are whole reachable pages (deck pages, rotation steps,
  `page:` targets present in the manifest) — fetched via the existing
  digest-addressed frame endpoint, stored on SD, exactly today's deck
  cache with `links` generalised to name manifest region ids.
- **Tiles** are rect-sized alternate states in `fb-rect` packing — the
  identical format patches use, so the firmware's patch painter is the
  tile blitter. Produced by rendering the page per declared state
  (`states` on the element / `data-state-set` in markup) through the
  existing silent `shadow_render_page` path and cutting the rect, or by
  capturing both sides of the first few live toggles (open question 3).
- **Content-hash keying**: every member is digest-addressed and immutable;
  `bundle_digest` = hash of the sorted member digests + links, same
  scheme as the deck manifest version. Firmware diffs manifests and
  fetches only unknown digests.
- **Invalidation**: the `/status` (and push-channel) `sync` envelope
  repeats the current `bundle_digest`; mismatch → re-fetch the bundle
  manifest. Per-state `ttl_s` (from page update cadence, as deck sync
  does today) disqualifies a stale frame from tier-0 nav without forcing
  a wake. A tile is invalidated only by disappearing from the manifest —
  content-addressing makes eviction, not mutation, the rule.

### B4. Transport for pushes (values, corrections, bundle bumps)

What needs pushing is small and idempotent: values seqs, "patch pending"
notices, bundle-version bumps, state corrections. Bulk bytes always flow
over plain digest-addressed GETs regardless.

| Option | For | Against |
|---|---|---|
| Short polling (today: 1 s `/frame/data` in linger, 5-min `/status`) | Already shipped; deep-sleep devices poll anyway; zero infra | Latency = poll period; always-on panels burn radio + waitress threads on empty polls |
| SSE | Plain HTTP/1.1 chunked response — works through the existing waitress + Caddy stack (the admin UI already runs an SSE feed at `/events/stream`); auto-reconnect semantics are trivial (`Last-Event-ID`); one-directional fits the model (device→server traffic already has REST endpoints) | Held connection per always-on device; no server→device RPC (not needed) |
| WebSocket | Bidirectional, lowest per-message overhead | Waitress cannot upgrade — would force an ASGI sidecar or a different server for one route; a second protocol stack in CircuitPython/ESP-IDF firmware; ping/pong keepalive management; Caddy upgrade pass-through is fine but the server-side cost is structural |

**Pick: SSE** at `GET /api/v1/device/<id>/stream` (Bearer), carrying the
same envelopes `/status` piggybacks today (`values`, `patches` notice,
`sync` bundle digest), each with its ms `seq`; **short polling stays as
the universal fallback** and remains the only mode for deep-sleep
devices, which are asleep between wakes no matter the transport.
Tradeoff accepted: SSE holds one waitress thread per connected always-on
panel; with `threads = 24` (`TESSERAE_THREADS`) the budget supports a
realistic home fleet but must be documented, and the endpoint sends a
keepalive comment every 25 s and drops idle connections so dead panels
release threads (open question 4: a thread-per-connection audit at >8
always-on panels).

Caddy implications: no upgrade config needed (unlike WS). The proxy must
not buffer the stream — set `flush_interval -1` on the `reverse_proxy`
for the `/api/v1/device/*/stream` route (Caddy streams most responses by
default, but the explicit setting removes doubt), and raise the route's
idle/read timeouts above the keepalive period. Document in
`docs/install/rest-transport.md`.

### B5. Text regions (device-rendered text)

Already prototyped as v1 value slots; v2 promotes them to first-class
manifest citizens (`text` array in §B1) so numeric and clock updates
never touch Playwright:

- Declaration is unchanged for authors: `data-overlay-key` (+ `-suffix`,
  `-map`) on any element, widget markup or code-element sandbox alike;
  canvas `data` primitives may also opt in via an additive element flag.
- The manifest entry carries the rect (wire-space), alignment, the atlas
  reference (existing 4bpp content-addressed strips, rasterized through
  the composition browser so blitted text is pixel-consistent), a
  `max_chars` clip, and the value `key`.
- Keys: `ha:<entity>`, `ha:<entity>:<dotted.path>` (existing), plus a new
  `local:` namespace evaluated on the device with zero network:
  `local:clock:HH.MM` ticks minutes from the device RTC (the `/status`
  response already ships resolved local-time fields for drift-free
  timezone handling). A page whose only sub-minute chrome is a clock
  then produces *no* server render churn at all — the digest-stability
  divert (v0.195) becomes a fallback rather than the mechanism.
- Server-fed values arrive on the SSE stream / `/frame/data` with ms
  seqs, newest-wins, exactly the v1 values contract, but keyed by text
  region id so two regions bound to the same entity can format
  differently.
- **Decision (2026-07-25): raise the firmware glyph cap.** The firmware
  bumps its per-atlas glyph budget (~64+) and the server ships an
  uppercase A–Z slice alongside the numeric set, so short state words
  (`ON`, `HEAT`, `AWAY`) render on glass without maps. `data-overlay-map`
  stays for anything longer or lowercase-styled. This is a coordinated
  firmware + server release; until the fleet advertises the raised cap
  the server keeps emitting numeric-charset atlases (the cap rides the
  existing `overlay.max_targets`-style capability advertisement).

### B6. Panel capability matrix

"Partial" = hardware partial refresh usable per-rect. Latency = touch to
visible feedback for a tier-0 invert. Estimates marked ~ need bench
confirmation (open question 6).

| Kind / panel | Touch HW | Partial refresh | Tier-0 latency | Viable tiers | Interactive dashboards? |
|---|---|---|---|---|---|
| `esp32_client` + IT8951 gray (reTerminal E1003) | Yes (GT911) | Yes (DU/GC16 per rect) | ~250–450 ms | 0, 1, 2 | **Yes** — the reference target; everything in this doc is proven or benchable here |
| `esp32_bw_client` (SSD1680-class B/W) | Board-dependent | Yes (~300–500 ms typical) | ~300–500 ms | 0, 1, 2 | **Yes where touch HW exists**; smaller region budgets |
| `esp32_client` + Spectra 6 (ACeP-class colour) | Rarely | **No** (full flash 15–30 s) | n/a | 2 only | **No.** Colour e-paper cannot give sub-second feedback; ship display-only, reject `touch: true` pairing with a clear error rather than degrade |
| `picpak_client` (BWRY) | No | No (BWRY full refresh ~10–20 s) | n/a | 2 only | **No** |
| `pico_bin_client` (Pico B/W panels) | No shipped touch | Panel-dependent | ~400 ms if SSD16xx | 0,1 possible, untested | Not until a touch-bearing board exists |
| `pi_bin_client` / `pi_png_client` (Pi + Inky / Kindle via KOReader) | Kindle: yes; Inky: no | Kindle: yes (eips partial); Inky ACeP: no | n/a | 2 only | **No** — decision 2026-07-25: Kindle stays officially display-only (the KOReader poller stays dumb); Inky: no |
| `trmnl_client` | No | Client doesn't expose it | n/a | 2 only | **No** (display-only by design) |
| `circuitpython_generic` | Board-dependent | Mostly no | n/a | 2 only | Not in general |
| `opendisplay` / `opendisplay_ha` tags | No | No | n/a | 2 only | **No** |

Server behaviour follows the matrix automatically: tiers are gated on
the advertised capability (`proto`/`overlay` handshake), never on kind
guesses — a Spectra 6 device that never advertises partial-capable
protocol simply never receives manifests, and its dashboards render
exactly as today (non-regression constraint).

### B7. Protocol versioning

- Capability handshake grows a sibling of `overlay`:
  `"proto": {"v": 2}` in register/status bodies, sticky and persisted in
  the device-facts store like `overlay` is today (v0.195). `overlay:
  {schema: 1|2}` remains the v1 handshake and keeps meaning exactly what
  it means now.
- **Old firmware, new server**: nothing changes. v1 endpoints
  (`/frame/overlay/<digest>`, slots-in-spec, coordinate tap dispatch,
  deck manifest) continue to be served for the whole deprecation window;
  they are generated from the same sidecars as v2 manifests, so there is
  one source of truth and two projections. The v1 window closes only
  after the fleet's sticky facts show no `proto < 2` device for a full
  release cycle, and removal is a major-version event in the CHANGELOG.
- **New firmware, old server**: firmware treats a 404 from
  `/frame/manifest` as "v1 server", falls back to the overlay spec +
  coordinate dispatch it already implements. The `proto` field in an
  unknown body is ignored by old servers by construction.
- The `/frame` response only gains the `manifest` block for devices whose
  sticky facts advertise `proto >= 2`, keeping v1 responses byte-stable
  (same discipline as `ota`/`deck` envelopes today).

---

## Constraints check

- **Non-touch dashboards**: manifests, bundles, and the SSE stream are
  all capability-gated additive surfaces; a device that never advertises
  them gets byte-identical behaviour. The render pipeline itself is
  untouched.
- **Standards**: new contract modules (`manifest builder`, bundle sync)
  join the mypy `--strict` override list alongside `app.push` /
  `app.state.*`; ruff unchanged; every wire shape gets the same
  fixture-level tests the v1 overlay/patch/deck features have.
- **Widget API breaking changes**: none required. Two *additive* changes
  are needed for full tier-0/1 fidelity and are flagged explicitly:
  1. Optional `data-touch-id` / element `touch_id` to pin stable region
     ids (without it, markup-region ids are content hashes that can
     churn when a widget's DOM changes, downgrading those regions'
     optimism to plain invert).
  2. Optional state declarations (`states` on canvas elements /
     `data-state-set` in markup) for tier-0 alternate tiles; toggles
     without them stay tier 1 with invert feedback, which is not a
     regression from today.

## Decisions (2026-07-25)

All eight open questions were put to the maintainer; resolutions:

1. **Region ids**: `data-touch-id` pinning with content-hash fallback.
   Unpinned markup regions may churn ids and silently downgrade to
   invert-only feedback; bundled widgets adopt `data-touch-id` over time.
   (DOM-path synthesis rejected: reordering markup would misroute
   tier-1 optimism.)
2. **Rotations as bundles**: neighbours only — bundle the prev/next of
   the current step, matching today's prewarmer. A fast multi-step skip
   degrades to tier 2; SD footprint stays small.
3. **Tile production**: n+1 lazy renders — each single toggled state
   rendered against the current base via `shadow_render_page`,
   rect-cropped, refreshed lazily by the reconcile worker. No eager
   state fan-out; no capture-from-live warm-up requirement.
4. **SSE budget**: document the cap. Thread-per-connection under
   waitress is fine for a home fleet (~8 always-on panels under the
   24-thread default); `TESSERAE_THREADS` guidance + keepalive reaping
   documented in `rest-transport.md`. Revisit only if real fleets hit it.
5. **Letters atlas**: raise the firmware glyph cap and ship an A–Z
   slice (see §B5). Coordinated firmware + server release, gated on the
   advertised cap.
6. **Latency bench**: E1003 only. Its numbers are measured facts; every
   other row in §B6 stays an estimate until that hardware exists on the
   bench, and tier gating must therefore key on advertised capability,
   never on the matrix's latency column.
7. **MQTT**: kept indefinitely for back-compat. v2 surfaces stay gated
   on `_renderer_is_http_polled`; no retirement precondition.
8. **Kindle**: officially display-only. The capability matrix marks it
   tier-2; no client work owed, no community-spec commitment.

## Remaining open items

- Adding `data-touch-id` to the bundled widgets is tracked as v2
  implementation work, not blocked on design.
- The raised glyph cap needs a firmware ticket (cap value, RAM cost,
  capability advertisement field) before the server-side A–Z slice is
  specified in `client-protocol.md`.
