# Touch v3 — device-owned touch (design handoff)

Re-architecture of Tesserae touch: the server ships an image (control rects
blank) + a declarative spec; the firmware draws the controls, owns interaction
with instant local partial-refresh feedback, and reports semantic events. This
replaces the fragile server-side extract → coordinate-dispatch → frame-patch
model (and the whole v1/v2 fix-tail) with an explicit, device-owned contract.

## Artifacts (read in this order)

1. `contract.md` — the anchor: model, endpoints, capabilities, tiers, icons.
2. `primitives.json` — shared geometric visual language (button/switch/slider/stepper).
3. `frame-spec.schema.json` — the touch spec shape.
4. `atlas.schema.json` — text glyph strip descriptor.
5. `phosphor-codepoints.example.json` — icon name→codepoint map contract (regenerate to real values).
6. `firmware-build-prompt.md` — hand this to the firmware side (build against 1–5).

## Phase plan

- **Phase 0 (this dir) — contract.** DONE: the artifacts above are the shared
  source of truth both sides consume.
- **Phase 1 — authoring + delivery (server/frontend), behind a flag.** Extend the
  canvas editor from generic hotspot to the four typed primitives with faithful
  preview; extend `panel_store`; blank primitive rects in the render; graduate
  the schemas to `schema/`; add the endpoints (`/frame/spec`, `/interact`,
  `/frame/stream`, `/atlas/<digest>`). Validate end-to-end WITHOUT firmware.
- **Phase 2 — firmware (E1003).** Build against the frozen contract
  (`firmware-build-prompt.md`).
- **Phase 3 — rip legacy.** Remove `touch_regions` extraction, coordinate `/tap`,
  `manifest.py`, overlay schema 1/2, touch-reconcile in `button_service` +
  `frame_patch`, `touch_monitor`; retire `docs/protocol-v2-touch.md`.
- **Phase 4 — broaden.** More primitives, more always-on touch panels.

Ordering is load-bearing: **contract → authoring → firmware → rip.** Never strip
the old stack before the new one carries load.

## What we keep vs strip (from the audit)

- **Keep:** action grammar + registry (`button_actions.py`), provenance gate, HA
  dispatch, event log, glyph atlas + `rect_to_wire`, capability handshake,
  canvas drag/place/config infra + `touch_interaction.js`, `panel_store` element
  schema, layout-digest anchoring.
- **Strip (Phase 3):** HTML region extraction, coordinate hit-test/dispatch,
  interaction-manifest builder, overlay schema 1/2, server touch-reconcile /
  frame-patch, `touch_monitor`, v1 `/tap`.
