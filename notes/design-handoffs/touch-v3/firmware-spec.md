# Tesserae Touch v3 — Firmware Specification (reTerminal E1003)

Engineering spec the firmware implements against. Deterministic: the server's
canvas preview draws to the same math, so the two match. Read alongside the
contract artifacts in this directory (`contract.md`, `primitives.json`,
`frame-spec.schema.json`, `atlas.schema.json`). Where this spec and the prose
prompt (`firmware-build-prompt.md`) differ in detail, **this file wins.**

Status: draft for Phase 2. Items marked **[confirm]** are pinned during Phase 1.

---

## 1. Target & budgets

- MCU: ESP32-S3 (PSRAM present).
- Controller: IT8951, 16-level grayscale, **4bpp** framebuffer, partial refresh
  (DU fast / GC16 quality).
- Touch: GT911 capacitive, interrupt line.
- Panel native dims: from device config. Reference E1003 frame ≈ **1.3 MB** at
  4bpp (≈2.6 Mpx). **[confirm exact WxH]**

Memory budget (PSRAM):

| Buffer | Size | Notes |
| --- | --- | --- |
| Framebuffer | ~1.3 MB | 4bpp, panel-sized |
| Shadow/compose buffer | ~1.3 MB | optional, for building a rect before blit |
| Spec cache | < 8 KB | parsed touch spec, keyed by `layout_digest` |
| Atlas cache | ~12 KB × N | N ≤ 2 (label, value) |
| Icon font (flash) | ~0.5–1 MB | one Phosphor weight (bold), or omit for atlas path |
| Icon raster scratch | ~ (px × px) B | one glyph at a time |

## 2. Coordinate space (critical — was a bug source in the old model)

**Spec rects are delivered in the device framebuffer coordinate space.** The
server applies the orientation / scale / underscan transform before emitting the
spec (it reuses `rect_to_wire`, retained from the audit's KEEP list). Therefore:

- Firmware draws each primitive **directly at its rect** — no rotation/flip math.
- Firmware maps GT911 points → framebuffer coords using its existing panel touch
  calibration only. It does **not** reimplement the composition orientation chain.

This keeps orientation logic in exactly one place (server) and removes the class
of "touch landed 90° off" bugs. Implemented: `touch_spec.wire_transform` scales
the authored canvas rect to composition dims, then runs `rect_to_wire` (the .bin
renderer's rotate/flip/scale/underscan chain), so `/frame/spec` rects are final
framebuffer coordinates.

## 3. Data structures (illustrative C)

```c
typedef struct { int16_t x, y, w, h; } rect_t;

typedef enum { P_BUTTON, P_SWITCH, P_SLIDER, P_STEPPER } ptype_t;
typedef struct { uint8_t tier; uint8_t type; } action_t;   // type: nav/ha/webhook/refresh/fetch

typedef struct {                       // resolved from an atlas glyph map
  const atlas_t *atlas; uint8_t align; char suffix[4]; uint8_t max_chars;
  const char *text;                    // static label, or NULL for value-bound
} text_ref_t;

typedef struct { char name[24]; uint8_t weight; uint16_t px; } icon_ref_t;

typedef struct {
  char id[33]; ptype_t type; rect_t rect; action_t action;
  text_ref_t label, value_text; bool has_label, has_value_text;
  icon_ref_t icon; bool has_icon;
  char value_key[48];
  // numeric primitives:
  float vmin, vmax, vstep, value; uint8_t axis;   // axis: 0=x,1=y
  uint8_t state;                                   // switch: 0=off,1=on
} primitive_t;

typedef struct {
  char layout_digest[24];
  atlas_t atlases[2]; uint8_t n_atlases;
  primitive_t prims[MAX_PRIMS]; uint8_t n_prims;
} touch_spec_t;
```

`atlas_t` holds the strip bytes + a glyph lookup (`char -> {x,w,adv}`), plus
`strip_h, ascent, descent, space_adv`.

## 4. Main loop / state machine

```
BOOT
  init GT911, IT8951; connect   (always_on arrives via config, S11)
FETCH
  GET /frame            -> image (control rects blank); note frame_digest, layout_digest
  if spec cache miss for layout_digest:
     GET /frame/spec?layout=<layout_digest> -> parse; fetch missing atlases/icons
RENDER
  blit image to framebuffer; draw each primitive (S6); GC16 full refresh once
IDLE
  always_on: hold SSE (S11); wait GT911 IRQ or refresh timer
  battery:   wait GT911 IRQ or sleep timer; on timer -> FETCH
ON TOUCH
  hit-test (S8) -> primitive
  feedback FIRST (S9, DU partial) -> POST /interact (S10) -> reconcile (S10)
ON SSE state event (always_on)
  update bound primitive value/state -> partial redraw (S9)
```

Frame refresh (data changed) re-runs FETCH→RENDER; if `layout_digest` unchanged,
reuse the cached spec and only the image changes.

## 5. Fetch protocol

- `GET /frame` — existing transport; body carries `frame_digest` + `layout_digest`.
- `GET /frame/spec?layout=<layout_digest>` — JSON per `frame-spec.schema.json`.
  Cache by `layout_digest`; refetch only when it changes.
- `GET /atlas/<digest>` — immutable strip binary; cache by digest forever.
- Failures: on spec 404 → treat as "no touch this frame" (render image only). On
  atlas fetch fail → skip text for that atlas, keep chrome. Never block the image
  on a spec/atlas failure. Bounded retry (3×, backoff) then degrade.

## 6. Rendering

**Ink model.** Framebuffer is 4bpp gray, 0 = paper … 15 = ink. Draw ops write
levels from `primitives.json` palette (`paper 0, soft 3, mid 8, ink 15`). Map to
the panel's waveform LUT at refresh.

**Text layout.** For a string: cursor = start; for each char, look up glyph
`{x,w,adv}` in the atlas, blit the `w×strip_h` slice from the strip to
`(cursor, baseline-ascent)`, advance `cursor += adv`; space uses `space_adv`.
Total width = Σ adv. Horizontal per `align` within the text box; vertical center
using `ascent`/`descent`.

**Icons.** If `icons` capability present: rasterize `name`→codepoint (pinned
map) at `px` via the bundled Phosphor weight; blit coverage as ink. Else the
server supplied the icon as an atlas glyph `@name` — blit like text. Duotone
(grayscale only): atlas path is already a two-level gray glyph (blit as-is);
firmware-font path rasterizes both layers and composites **secondary→level 3,
primary→level 15** (from `primitives.json.duotone`).

## 7. Per-primitive geometry (deterministic — matches the canvas preview)

All within rect `R={x,y,w,h}`, tokens from `primitives.json`.

**button**
- Frame: rounded-rect outline, radius 12, stroke 2, inset stroke/2 so it's crisp;
  fill paper.
- Content (mode `label`/`icon`/`icon_label`): width = icon_w + gap(8) + label_w
  (omit missing parts); center in R. Icon px 40. Label via S6.
- Press: XOR-invert all of R, DU refresh; restore + fire on release.

**switch** (label left)
- label_w = text width; gap 10; control area = R minus (label_w+gap) on the left.
- Track: pill, height `th = min(R.h, 44)`, width = control area width; radius th/2,
  stroke 2; fill paper(off)/mid(on); vertically centered.
- Thumb: circle d = th − 2·inset (inset 4), fill ink.
  - off: center_x = track_left + inset + d/2
  - on:  center_x = track_right − inset − d/2
- Tap: flip `state`, redraw track fill + thumb at new pos, DU refresh track rect.

**slider** (axis x; y is the vertical analog, top = max)
- Track: thickness 8, radius 4, inset 20 from each end; length L = R.w − 2·inset;
  track_left = R.x + inset; vertically centered (reserve top strip for value_text).
- Thumb: circle d 36, stroke 2, fill paper.
- Position: `t = (value−min)/(max−min)`; center_x = track_left + t·L.
- Active fill: track_left→center_x = mid; rest = paper.
- value_text: centered above thumb, clamped inside R.
- Drag: `t = clamp((fx−track_left)/L, 0, 1)`; `value = min + round(t·(max−min)/step)·step`;
  move thumb + repaint active fill + value_text (DU); report on release.

**stepper**
- Frame: rounded-rect stroke 2. Thirds: minus=left, value=center, plus=right;
  dividers at 1/3 and 2/3 (vertical, stroke 2, soft).
- Marks: minus "−" horizontal line, plus "+" cross; length 0.28·third_w, stroke 3.
- value_text: centered in center third.
- Tap a side zone: invert that zone (DU); `value = clamp(value ± step, min, max)`;
  repaint value_text; report.

## 8. Touch input (GT911)

- IRQ-driven; read point(s). Map to framebuffer coords (S2).
- Gesture: track down→move→up. If total displacement < TAP_R (30 px) and
  duration < 500 ms → **tap** at down point. Else, for the primitive under the
  down point, if it's a slider → **drag** along its axis; otherwise ignore.
- Hit-test: point-in-rect over `prims` in array order; first match wins. (Rects
  don't overlap — the editor prevents it. If they do, first wins.)
- Debounce: ignore a second down within 120 ms of an up on the same primitive.

## 9. Feedback & partial refresh

- **Feedback before network, always.** Draw the new local state and DU-refresh
  **only the primitive's rect** (≈250–450 ms on E1003). Never full-flash on
  interaction.
- Ghosting: per-rect refresh counter; after **N=8** partial refreshes force a
  GC16 full refresh of that rect (or the frame). **[confirm N on hardware]**
- Tier 2 (refresh/webhook/cold-nav): show a pending affordance (e.g. dim/spinner
  mark) after the tap; settle on the next frame rather than an optimistic state.

## 10. Interaction report & reconcile

```
POST /interact  { "primitive_id", "interaction":"tap"|"set", "value"?,
                  "layout_digest", "event_id" }
```
- `event_id`: monotonic per device (dedup on the server).
- Response may include confirmed state for the primitive. If it differs from the
  optimistic draw, partial-redraw to the confirmed state. On network failure,
  keep the optimistic state (tier 1) or revert to pending (tier 2) per action tier.

## 11. State sync (always-on) & power

`always_on` is a **server setting**, delivered in the config envelope on the
`/status` response (same channel as `sleep_interval_s`). Read it each poll.

- always_on = true: open SSE `GET /frame/stream`. Events:
  `state {primitive_id,value}` → update bound primitive + partial redraw;
  `sync {layout_digest}` → if changed, go FETCH. Keepalive comment every ~25 s;
  reconnect with `Last-Event-ID`.
- always_on = false: no SSE; primitives drawn statically from spec values. Each
  interaction: wake → feedback → report → sleep per `sleep_interval_s`.

## 12. Latency targets

| Path | Target |
| --- | --- |
| Touch → local feedback on glass | ≤ ~500 ms (GT911 read + one DU rect) |
| Feedback → `/interact` sent | async, off the feedback path |
| SSE state event → primitive redraw | ≤ ~600 ms |

## 13. Error handling & edge cases

- Spec references an atlas/icon not yet fetched → fetch on demand; if it fails,
  render chrome without text/icon (never a blank control).
- `interaction` for a `primitive_id` not in the current spec → drop (spec raced a
  frame change); the next FETCH reconciles.
- `layout_digest` mismatch between held spec and a touch → ignore the touch,
  trigger FETCH.
- Value out of `[min,max]` from a race → clamp before drawing and reporting.
- Unknown `type` (server newer than firmware) → skip that primitive, render the
  rest.

## 14. Capability advertisement (register + heartbeat)

```json
{ "touch": {"v":3,"primitives":["button","switch","slider","stepper"]},
  "can_stay_awake": true, "partial_refresh": true,
  "panel": {"depth_bpp":4,"grayscale":true},
  "icons": {"font":"phosphor","version":"<pinned>","weight":"bold"} }
```

## 15. Acceptance checklist

- [ ] Each primitive renders pixel-aligned to the canvas preview at the same rect.
- [ ] Tap feedback appears in ≤ one DU refresh; no full flash on interaction.
- [ ] Switch/slider reconcile to server-confirmed state on the `/interact` reply.
- [ ] SSE state event moves a switch when HA changes externally (always-on).
- [ ] Battery mode: interaction works wake→report→sleep with no SSE.
- [ ] Orientation: touch maps correctly in all supported panel rotations.
- [ ] Ghosting: forced GC16 after N partials; no residual after full refresh.
- [ ] Graceful degrade: spec 404 / atlas fail / unknown type all render the image.
