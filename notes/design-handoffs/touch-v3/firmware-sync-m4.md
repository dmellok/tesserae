# Touch v3 — firmware sync (server complete through M4)

Feed this to the firmware agent. The server backend, canvas authoring, opt-in
gating, and MCP authoring are all shipped and tested. All three earlier deltas
(`firmware-build-prompt.md` sync notes) are resolved, so text rendering and live
state are now unblocked.

```
SYNC: Tesserae touch-v3 server is complete through authoring + gating. All three
earlier deltas are RESOLVED, so text rendering and live state are now unblocked.
Update the firmware to the finalized contract below.

STATUS
- Server backend, canvas authoring, and MCP authoring all shipped and tested.
  touch-v3 is ON by default (no experiment gate). The firmware is the remaining half.
- /frame/spec carries primitives whenever the current page has them; an EMPTY spec
  just means a non-touch page -- render the image and hold no controls.

CAPABILITIES (advertise on register + heartbeat)
  { "touch": {"v":3,"primitives":["button","switch","slider","stepper"]},
    "can_stay_awake": true,           # hardware fact; server gates always_on on it
    "partial_refresh": true,
    "panel": {"depth_bpp":4,"grayscale":true},
    "icons": {"font":"phosphor","version":"<pinned>","weight":"bold"} }
  always_on is NOT advertised: it's a server SETTING in the /status config block
  (config.always_on, bool, default false). Read it each poll (deep-sleep vs
  hold-SSE), same as sleep_interval_s.

ENDPOINTS (finalized shapes)

1) GET /api/v1/device/<id>/frame/spec?layout=<held_digest>
   { "layout_digest":"<16 hex>",
     "primitives":[ ...framebuffer-coord rects... ],   # DELTA 3 resolved: draw as-is
     "atlases":[ ...glyph atlas descriptors... ] }      # DELTA 2 resolved: text
   - Rects are FINAL device-framebuffer coords (server applied the wire transform).
     Draw at the rect; only map GT911 -> framebuffer via panel calibration. No
     canvas scaling/rotation on-device.
   - layout_digest is stable across data-only redraws; re-fetch only when it changes.
   - Empty primitives (feature off, or a non-touch page) is valid: hold nothing.
   Primitive shapes:
     button  {id,type,rect,label?:{atlas,align,text},icon?:{name,weight,px},action:{tier,type}}
     switch  {id,type,rect,label?,value_key,state?:"on"/"off",action:{tier:1,type:"ha"}}
     slider  {id,type,rect,axis,min,max,step,value,value_key?,value_text:{atlas,align,max_chars},action}
     stepper {id,type,rect,min,max,step,value,value_key?,value_text,action}

2) GET /api/v1/device/<id>/atlas/<digest>   (DELTA 2: text glyphs)
   Immutable 4bpp-gray strip. Fetch once per digest referenced by the spec's
   atlases[]. Atlas descriptor (in the spec's atlases[]):
     {id, digest, url, format:"gray4", font:"Inter", px, weight,
      strip_h, ascent, descent, space_adv, glyphs:{ "<char>":{x,w,adv} }}
   Roles: "l20" = labels (20px/400), "v28" = values (28px/700).
   IMPORTANT: each packed glyph cell is ADVANCE-wide, so adv == w. Lay out text by
   blitting each char's {x,w} slice and advancing the cursor by adv. Vertically
   center with ascent/descent; align per the text ref's "align". high nibble =
   left pixel, 0=paper .. 15=ink.
   ICONS are NOT delivered via the atlas -- render button icons from your bundled
   Phosphor font (icon.name -> codepoint, pinned version). Text only in the atlas.

3) POST /api/v1/device/<id>/interact
   Send: {primitive_id, interaction:"tap"|"set", value?, event_id?}
   Response: {outcome, primitive_id}. Outcomes: ha_dispatched, dispatched,
   fetched, webhook_dispatched, noop, no_target, deduped. Do NOT branch on these;
   you already drew local feedback. The response does NOT carry confirmed state --
   reconcile from the values stream (below), not from this reply.
   Server fires the side-effect only (HA toggle / nav / webhook) with NO server
   re-render: the device owns feedback.

4) GET /api/v1/device/<id>/frame/stream  (SSE; DELTA 1: live state)
   For always_on devices. Events:
     event: values  data: {"seq":<ms>,"values":{"ha:light.desk":"on", ...}}
     event: sync    data: {"frame_digest":..., "bundle_digest"?:...}
   Values are keyed by value_key (the same string on your switch/slider/stepper
   primitives). MAP value_key -> your primitive and update its drawn state/thumb
   on change (a switch flips when HA changes externally). No primitive-id-keyed
   event; the mapping is yours via value_key.

BEHAVIOUR
- Feedback FIRST (partial-refresh the control's rect), THEN report. Never block
  the glass on the network.
- switch: tap flips optimistically + reports; reconcile to the values stream.
  slider/stepper: report value on release/change; reconcile from values.
  button: tap -> invert -> fire on_tap's action (server-resolved).
- always_on=false (battery): draw primitives statically from spec values, no SSE;
  each interaction wakes -> reports -> sleeps.
- Gracefully handle: empty spec (no controls), missing atlas (render chrome, no
  text), unknown primitive type (skip it), feature-off (image only).

NO CHANGE NEEDED
- Coordinate handling: keep treating rects as final framebuffer coords.
- Geometry: keep drawing to primitives.json (unchanged).
```
