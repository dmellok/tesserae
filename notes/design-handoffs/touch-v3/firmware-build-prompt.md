# Tesserae Touch v3 — Firmware Build Prompt (reTerminal E1003)

Hand this to the firmware side. It is self-contained: a firmware engineer/agent
should be able to build from it against the companion server-side contract
artifacts listed below. Target the reTerminal **E1003** first; keep the
primitive renderer panel-agnostic so other grayscale touch panels can reuse it.

---

## 1. Context & the model shift

Tesserae is replacing server-inferred, coordinate-dispatch touch with a
**device-owned** model:

- The server sends a dashboard **image** with the touch-control rects left
  **blank**, plus a declarative **touch spec** describing the controls.
- The **firmware draws the controls** into those blank rects, owns hit-testing,
  gives **instant local partial-refresh feedback** (no server round-trip), and
  reports a **semantic event** (which control, what interaction, what value).
- The server resolves the event to an action (HA call, nav, webhook) and pushes
  back confirmed state for stateful controls. **Action payloads never reach the
  device** — it only ever learns a control id + an action tier/type.

This deletes the old extract-from-HTML / coordinate-hit-test / server-computed-
frame-patch pipeline entirely.

## 2. Hardware target (E1003)

- ESP32-S3 (has PSRAM).
- IT8951 controller, **16-level grayscale, 4bpp**, supports partial refresh
  (DU/GC16 per rect).
- GT911 capacitive touch (interrupt-driven).

## 3. REMOVE from current firmware

- Sending touch **coordinates** (`touch_x/y`) on `/frame` or `/tap`.
- Applying **server frame-patches** for tap-echo / overlay schema 1 & 2.
- **Interaction-manifest** fetch and `region_id` handling (protocol v2 design).
- Any server-patch-driven invert/restore of tap echoes.

## 4. Companion contract artifacts (server-owned; you consume)

These are the shared source of truth. Build against them; do not hardcode.

- **`primitives.json`** — geometric visual language for every primitive
  (corner radius, stroke widths, thumb sizes, state marks, padding). This is
  what guarantees the firmware drawing matches the server's canvas preview.
- **frame-spec schema** — the touch spec shape (§7).
- **atlas descriptor** — text glyph strip format (§8).
- **Phosphor name→codepoint map** + a **pinned Phosphor version** — the icon
  contract (§9). Version-lock this between server and firmware.

## 5. Capabilities to advertise (register + heartbeat)

```json
{
  "touch":   { "v": 3, "primitives": ["button","switch","slider","stepper"] },
  "can_stay_awake": true,
  "partial_refresh": true,
  "panel":   { "depth_bpp": 4, "grayscale": true },
  "icons":   { "font": "phosphor", "version": "2.1.0", "weight": "bold" }
}
```

- `can_stay_awake` is a hardware fact (this device *can* run without deep sleep).
  Whether it *does* is the **`always_on` setting**, which Tesserae sends in the
  config envelope on the `/status` response (same channel as `sleep_interval_s`):
  `{ "config": { "sleep_interval_s": 300, "always_on": true } }`. Read it each
  poll — `true` = disable deep sleep, keep GT911 powered and interrupt-driven,
  hold the state stream (§11); `false` = sleep per `sleep_interval_s`.
- Omit `icons` if the device can't hold the Phosphor font — the server then
  falls back to shipping icon glyphs inside the atlas (§9).
- `panel.depth_bpp` / `grayscale` gate duotone (§9).

## 6. Wire flow per frame

1. `GET /api/v1/device/<id>/frame` → the dashboard image (control rects blank).
2. `GET /api/v1/device/<id>/frame/spec?layout=<layout_digest>` → the touch spec.
   Cache by `layout_digest` (stable across data-only redraws — a clock tick does
   NOT invalidate it).
3. Fetch any referenced **atlases** (`GET .../atlas/<digest>`, immutable) and, if
   using the atlas icon path, icon glyphs come inside those atlases.
4. Draw the primitives on top of the image, into their rects.
5. GT911 hit-test locally; give local partial-refresh feedback (§10).
6. `POST /api/v1/device/<id>/interact` with the semantic event (§12).
7. If `always_on`: hold SSE `GET .../frame/stream` for state pushback (§11).

## 7. The touch spec you parse

```json
{
  "layout_digest": "8603c4372c14",
  "atlases": [ /* atlas descriptors, §8 */ ],
  "primitives": [
    { "id": "btn_scene", "type": "button", "rect": {"x":40,"y":900,"w":180,"h":80},
      "label": {"atlas":"l20","align":"center","text":"Movie"},
      "icon":  {"name":"film-slate","weight":"duotone","px":40},
      "action": {"tier":1,"type":"ha"} },

    { "id": "sw_desk", "type": "switch", "rect": {"x":240,"y":900,"w":160,"h":80},
      "label": {"atlas":"l20","text":"Desk"},
      "value_key": "ha:light.desk", "state": "on",
      "action": {"tier":1,"type":"ha"} },

    { "id": "sl_bri", "type": "slider", "rect": {"x":40,"y":1000,"w":360,"h":70},
      "axis": "x", "min": 0, "max": 100, "step": 5, "value": 60,
      "value_key": "ha:light.desk:attributes.brightness_pct",
      "value_text": {"atlas":"v28","align":"center","suffix":"%","max_chars":3},
      "action": {"tier":1,"type":"ha"} },

    { "id": "st_vol", "type": "stepper", "rect": {"x":420,"y":1000,"w":160,"h":70},
      "min": 0, "max": 30, "step": 1, "value": 12,
      "value_text": {"atlas":"v28","align":"center","max_chars":2},
      "action": {"tier":1,"type":"ha"} }
  ]
}
```

Ignore any primitive whose `type` isn't in your advertised list.

## 8. Text rendering (atlas)

- Format `gray4`: 4bpp grayscale horizontal strip, **high nibble = left pixel**,
  **0 = paper, 15 = full ink** (map/invert for the panel as needed).
- Descriptor gives `strip_h`, `ascent`, `descent`, `space_adv`, and per glyph
  `{x, w, adv}`. Uniform height, so blitting is a horizontal copy.
- Shape a string by walking chars → glyph `{x,w}` blit → advance cursor by `adv`.
  No kerning. Vertically center with `ascent`/`descent`; horizontally per `align`.
- `max_chars` bounds a value's footprint so layout is stable as the number moves.
- Charset is fixed printable ASCII + `°`, so atlases are effectively static —
  fetch once per `(px,weight)` digest, cache forever.

## 9. Icons

Two delivery paths, chosen per device by the `icons` capability:

- **Firmware font (E1003 default):** ship one **Phosphor** weight (bold) + a
  TrueType rasterizer (e.g. `stb_truetype`). Render `icon.name` → codepoint (via
  the pinned name→codepoint map) at `icon.px`, any size, locally.
- **Atlas fallback (constrained devices):** if you don't advertise `icons`, the
  server bakes the icon into the atlas as a named glyph; blit it like text.

Guarantee **glyph-identity, not pixel-identity** — a one-shade AA difference on a
grayscale icon is invisible; what matters is *which* icon, size, and position,
all fixed by the spec + pinned Phosphor version.

**Duotone** (grayscale panels only):
- Preferred: let the server **composite duotone into a 4bpp gray atlas glyph**
  (the atlas is already grayscale, so this is free and exact) — blit as text.
- Optional firmware path: ship the Phosphor **duotone** weight (two glyphs per
  icon), rasterize both layers, composite **primary at ink level 15, secondary
  at level 3** (values come from the shared spec; don't hardcode).
- **1bpp B/W panels**: no second ink level → the server sends a solid **bold**
  weight instead. Gate on `panel.grayscale`.

## 10. Primitives — draw + interact (geometry from `primitives.json`)

GT911 interrupt-driven; hit-test the touch point against spec rects locally.
**Feedback FIRST (partial-refresh the primitive's rect only, DU mode), then
report.** Never block the glass on the network.

- **button** — momentary. Press → invert rect. On release → fire (report).
- **switch** — bistate bound to `value_key`. Tap → flip local `state` + redraw
  (optimistic) → report. Reconcile to server-confirmed state on the response.
- **slider** — drag along `axis`; move the thumb + update `value_text` live
  (partial refresh); report `value` (snapped to `step`) on release.
- **stepper** — two implicit zones (−/+) adjust `value` by `step` within
  `min..max`; redraw `value_text`; report each change.

Only ever partial-refresh the control's own rect. Count partial refreshes per
rect and force a **full refresh after N** for ghosting hygiene (reuse existing
threshold).

## 11. Always-on / power

`always_on` is a server setting from the config envelope (§5), read each poll.

- `always_on` = true: no deep sleep; hold SSE
  `GET /api/v1/device/<id>/frame/stream`. On `state` events
  `{"primitive_id","value"}`, partial-redraw the bound primitive so a switch/
  slider stays correct when HA changes externally.
- `always_on` = false: draw primitives **statically** from spec values, no SSE.
  Each interaction wakes → reports → sleeps per `sleep_interval_s`. Touch still
  works; live external state does not.

## 12. Reporting

```
POST /api/v1/device/<id>/interact
{ "primitive_id": "sw_desk", "interaction": "tap" | "set",
  "value": <number?>, "layout_digest": "8603c4372c14", "event_id": <monotonic> }
```

The response may carry confirmed state for stateful primitives — reconcile the
drawn state to it (partial redraw if it differs from the optimistic state).

## 13. Constraints (non-negotiable)

- Primitive **geometry must match `primitives.json`** so the device matches the
  server's canvas preview. Drift breaks WYSIWYG.
- **Partial-refresh the control rect only** on interaction; never full-flash.
- **Reject/ignore** primitives whose `type` isn't in your advertised list.
- **Version-lock Phosphor** (font + name→codepoint map) with the server build.

## 14. Deliverables

- Primitive renderer for the 4 types (geometry-driven from `primitives.json`).
- GT911 hit-test + gesture recognition (tap / drag-along-axis).
- Partial-refresh feedback path (DU per rect + ghosting counter).
- Atlas text renderer (blit + advance-width shaping).
- Icon renderer: Phosphor font + rasterizer, with atlas-glyph fallback; duotone
  compositing (or atlas-composited duotone).
- `/interact` reporting + confirmed-state reconcile.
- SSE state-sync client (always-on) + battery static fallback.
- `always_on` obeyed from the config envelope (SSE hold vs sleep).

Build and validate on the E1003 reference unit first.
