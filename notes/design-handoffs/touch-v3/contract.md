# Tesserae Touch v3 — Contract (Phase 0)

The shared source of truth for device-owned touch. Both the server (compose +
canvas preview + delivery) and the firmware consume this. Supersedes
`docs/protocol-v2-touch.md`, which is retired in Phase 3.

Companion artifacts in this directory:

- `primitives.json` — geometric visual language (draw the same on both sides).
- `frame-spec.schema.json` — the touch spec shape.
- `atlas.schema.json` — text glyph strip descriptor.
- `phosphor-codepoints.example.json` — icon name→codepoint map contract.
- `firmware-build-prompt.md` — the firmware companion build brief.

## 1. Model

- Server renders the dashboard **image** with touch-control rects left **blank**,
  and emits a declarative **touch spec** describing the controls.
- Firmware **draws the controls** into the blank rects (from `primitives.json`),
  owns hit-testing, gives **instant local partial-refresh feedback**, then
  reports a **semantic event**.
- Server resolves the event to an action and pushes back confirmed state.
  **Action payloads never reach the device** — only a tier + type.

This removes server-side region extraction, coordinate hit-testing, and
server-computed frame patches entirely.

## 2. Endpoints (Phase 1 surface)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/device/<id>/frame` | Dashboard image; control rects left blank. Unchanged transport, new render behaviour. |
| GET | `/api/v1/device/<id>/frame/spec?layout=<layout_digest>` | The touch spec (`frame-spec.schema.json`). Cached by `layout_digest`. |
| GET | `/api/v1/device/<id>/atlas/<digest>` | Glyph strip binary (immutable, content-addressed). |
| POST | `/api/v1/device/<id>/interact` | Semantic event report (§6). Returns confirmed state. |
| GET | `/api/v1/device/<id>/frame/stream` | SSE state pushback for always-on devices (§7). |

## 3. Capability negotiation

Device advertises on register + heartbeat:

```json
{
  "touch":   { "v": 3, "primitives": ["button","switch","slider","stepper"] },
  "can_stay_awake": true,
  "partial_refresh": true,
  "panel":   { "depth_bpp": 4, "grayscale": true },
  "icons":   { "font": "phosphor", "version": "TBD", "weight": "bold" }
}
```

- `can_stay_awake` — hardware fact: the device *can* run mains-powered without
  deep sleep. Whether it *does* is the **`always_on` setting**, not a capability
  — only the user knows how the panel is powered.
- `icons` — present ⇒ server sends icons by name (firmware renders locally);
  absent ⇒ server bakes icon glyphs into the atlas (§5).
- `panel.grayscale` — gates duotone (§5).

**The `always_on` setting (server → device).** A per-device toggle in Tesserae,
delivered in the **config envelope** on the `/status` response, same channel as
`sleep_interval_s`:

```json
{ "config": { "sleep_interval_s": 300, "always_on": true }, "next_poll_s": 300 }
```

The device reads it each poll and obeys: `true` ⇒ no deep sleep, keep GT911
powered, hold SSE (§7); `false` ⇒ sleep per `sleep_interval_s`. The server offers
the toggle only for devices advertising `can_stay_awake` + `touch`, and
**requires `always_on: true` before it will place interactive primitives** (a
device left to sleep can't hold live state).

## 4. The touch spec

Anchored to `layout_digest` (stable across data-only redraws). Full shape in
`frame-spec.schema.json`. Example:

```json
{
  "layout_digest": "8603c4372c14",
  "atlases": [ { "id": "l20", "digest": "…", "url": "…", "format": "gray4",
                 "px": 20, "weight": 400, "strip_h": 24, "ascent": 16,
                 "descent": 4, "space_adv": 6, "glyphs": { "A": {"x":0,"w":14,"adv":15} } } ],
  "primitives": [
    { "id": "sw_desk", "type": "switch", "rect": {"x":240,"y":900,"w":160,"h":80},
      "label": {"atlas":"l20","text":"Desk"}, "value_key": "ha:light.desk",
      "state": "on", "action": {"tier":1,"type":"ha"} }
  ]
}
```

Tiers (feedback contract):

- **0** local only (deck nav, tile cycle) — instant, no server needed.
- **1** optimistic local + server confirm (HA toggle, slider) — draw the new
  state immediately, reconcile on the `/interact` response / SSE.
- **2** must round-trip before settling (refresh, cold nav, webhook) — show a
  pending affordance until the next frame.

## 5. Primitives, text, and icons

**Geometry** — from `primitives.json`. Palette is 4bpp grayscale
(`paper 0 … ink 15`). The four primitives: `button`, `switch`, `slider`,
`stepper`. Firmware draws vector chrome; the server canvas preview draws the
identical geometry so WYSIWYG holds. Geometry drift is the primary fidelity
risk — `primitives.json` is the single source of truth.

**Text** — the atlas (`atlas.schema.json`) is a 4bpp-gray strip, text-only by
default, fixed charset (printable ASCII + the degree sign) so atlases are
effectively static and content-addressed. Two roles: `label` (20/400) and
`value` (28/700).

**Icons** — capability-negotiated, two delivery paths:

- *Firmware font* (E1003 default): ship one Phosphor weight (`bold`) + a
  TrueType rasterizer. Spec references `{"name":"lightbulb","px":40}`.
- *Atlas fallback* (constrained devices): server bakes the icon as a named atlas
  glyph (`@lightbulb`), blit like text.

Guarantee **glyph-identity, not pixel-identity** — pin the Phosphor version and
share `phosphor-codepoints.example.json` (regenerated to real values) so both
sides resolve the same glyph. A one-shade AA difference at 4bpp is invisible.

**Duotone** (grayscale panels only) — two ink levels: `primary 15`,
`secondary 3` (from `primitives.json`). Preferred delivery is the **atlas**
(server composites the two layers into a gray glyph — free, exact match).
Firmware-font duotone (two-glyph composite) is an optional E1003 upgrade. On
**1bpp B/W** panels the server sends solid `bold` instead (gated on
`panel.grayscale`).

## 6. Interaction report

```
POST /api/v1/device/<id>/interact
{ "primitive_id": "sw_desk", "interaction": "tap" | "set",
  "value": <number?>, "layout_digest": "8603c4372c14", "event_id": <monotonic> }
```

Server validates the id against the layout's spec, resolves the bound action
(reusing the existing `button_actions` grammar + provenance gate + HA dispatch),
and returns confirmed state for stateful primitives. Firmware reconciles its
optimistic draw to the confirmed state (partial redraw if different).

## 7. State sync (always-on)

Devices with the `always_on` setting hold SSE at
`/api/v1/device/<id>/frame/stream`. On `state` events `{"primitive_id","value"}`,
the device partial-redraws the bound primitive so a switch/slider stays correct
when HA changes externally. Value
keys reuse the `ha:<entity>[:<dotted.path>]` grammar. Battery devices skip SSE
and draw primitives statically from spec values.

## 8. Versioning

- `touch.v = 3`. The spec is layout-anchored and additive within v3.
- This contract supersedes `docs/protocol-v2-touch.md`; the v1 coordinate path
  and v2 manifest/overlay surfaces are removed in Phase 3 (see README plan).
- When Phase 1 wires this in, `frame-spec.schema.json` + `atlas.schema.json`
  graduate to `schema/` and the endpoints go live; `primitives.json` +
  the codepoint map ship as build artifacts to both server and firmware.

## 9. Open items to close during Phase 1

- Pin the Phosphor version and generate the real codepoint map.
- Confirm `layout_digest` derivation (reuse the existing layout-anchor hash).
- Decide slider `value_text` placement per axis (leading vs above-thumb).
- Confirm the `always_on` config field name/placement matches the existing
  sleep-config envelope on the `/status` response.
