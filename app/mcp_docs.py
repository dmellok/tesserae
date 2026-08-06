"""Canonical agent-facing MCP docs, served to the tesserae-mcp bridge.

The bridge (``tesserae-mcp`` on PyPI) is a thin HTTP proxy; the only thing that
churns in it is this agent-facing copy — the FastMCP handshake ``instructions``
and the canvas ``DOC_SHAPE`` embedded in the set_canvas / add_element tool
descriptions. Keeping the canonical text HERE and serving it at
``GET /api/mcp/instructions`` means a capability / copy change goes live on the
next agent connection, no bridge republish. The bridge ships an embedded copy of
this same text as a fallback for when it can't reach (or is newer than) Tesserae.

``DOCS_SCHEMA`` bumps if the response *shape* changes so an older bridge can tell
whether it understands the payload; the text itself can change freely without a
bump.
"""

from __future__ import annotations

DOCS_SCHEMA = 1

# The canvas-document shape, embedded in the set_canvas / add_element tool
# descriptions so the agent knows exactly what to write.
DOC_SHAPE = """A canvas document is JSON:
{
  "w": int, "h": int,                 # artboard size in px (match the target panel)
  "theme": str, "style": str,         # appearance ids from list_widgets().appearance
  "font": str, "bg": str,             # optional font id and background colour override
  "els": [ <element>, ... ]           # painted in list order: first = back, last = front
}
Elements may sit partly off the panel (it clips at the edge). Each element has a
unique "id" and a box "x","y","w","h" (px, top-left origin; x/y may be negative),
plus optional "opacity" (0-100) and "rotate" (degrees). By "kind":
- widget:  {"kind":"widget","widget":"<key>","fragment":"full","options":{...}}
           <key> from list_widgets(); "fragment" from that widget's fragments (or "full");
           "options" per get_widget_options(<key>).
- text:    {"kind":"text","text":"...","color":"<css or var(--accent-1)>","size":<px, 0=auto>,"align":"left|center|right"}
- rect:    {"kind":"rect","color":"...","fill":true,"stroke":<px>,"radius":<px>}
- ellipse: {"kind":"ellipse","color":"...","fill":true,"stroke":<px>}
- line:    {"kind":"line","color":"...","stroke":<px>}
- icon:    {"kind":"icon","icon":"<name or ph-name>","color":"...","weight":"thin|light|regular|bold|fill|duotone"}
           1500+ Phosphor names; don't guess, search them with GET /api/mcp/icons?q=<term>
           (a wrong name renders a blank box, no error; render_report().icon_invalid flags it).
- data:    {"kind":"data","source":"<widget key>","options":{...},"field":"<path>",
            "display":"text|number|line|bar|sparkline","format":"","unit":"","precision":0,
            "label":"","color":"...","size":<px, 0=auto>,"align":"..."}
           Binds a widget's data field to a scalable value or graph. "source" is a widget key from
           list_widgets(); configure it via "options" (get_widget_options). Use probe_widget_data(source,
           options) to see the real data shape before choosing "field". "format" (text/number only) is a
           date pattern ("HH:mm","MMM d","ddd HH:mm"), "relative", or a number pattern ("0.0").
- html:    {"kind":"html","html":"<div>…</div>","css":"div{…}"}
           A mini widget from static HTML + CSS in a sandboxed iframe (no scripts, no network).
- svg:     {"kind":"svg","html":"<svg …>…</svg>","css":""}   -- raw SVG, scaled to fill the box.
- code:    {"kind":"code","sources":[{"key":"<widget key>","options":{...},"name":"weather"}, ...],
            "html":"…","css":"…","js":"…"}
           HTML + CSS + JS fed by ANY number of widgets' live data. Each source in "sources" is a widget
           ({key, options, optional name}); its fetched data is injected into a sandboxed iframe as
           ctx.data[name] (name falls back to key). So two sources named "weather" and "transit" give you
           ctx.data.weather and ctx.data.transit. Your "js" builds the DOM from them (also ctx.options,
           ctx.w, ctx.h). The frame runs scripts and can load remote IMAGES (<img src="https://...">, e.g.
           a Spotify album cover from ctx.data or an Unsplash photo URL), but has NO readable network
           (fetch/XHR blocked) and NO same-origin access -- source data is delivered, never fetched -- and
           renders ONCE (e-ink is static -- no interactivity or animation).
           Use this for custom layouts/formatting a "data" primitive can't express, or to combine widgets.
           Call probe_widget_data(key, options) per source to see field shapes, then read those paths off
           ctx.data.<name>. Example js: "document.body.textContent = Math.round(ctx.data.weather.current.temp)+'°'".
           These libraries are preloaded in the sandbox (each auto-loads only when your code references
           it by the global shown, so unused ones cost nothing). No script-fetch, so they're your toolkit:
             - Chart.js -> window.Chart. Charts to a <canvas>. Animations are already off. On e-ink there's
               no hover, so bake values in: also reference ChartDataLabels (chartjs-plugin-datalabels,
               auto-registered) and add datalabels to a dataset/options to print values on bars/points.
               A Sankey chart type (chartjs-chart-sankey) is registered too, for flow diagrams
               (type:'sankey'). The full toolkit is enumerated in list_widgets().libraries.
             - canvas-gauges -> RadialGauge / LinearGauge. Dials + meters (temperature, fuel-style).
             - dayjs -> dayjs(...). Date/time parse + format; utc + timezone plugins are pre-extended, so
               dayjs.utc(...) and dayjs(...).tz("Europe/Berlin") work. Use for formatting ctx.data times.
             - qrcode -> qrcode(typeNumber, ecc). QR codes; render .createSvgTag() or .createImgTag().
             - marked -> marked.parse(md). Markdown -> HTML string; assign to an element's innerHTML.
             - chroma -> chroma(...). Colour parsing/scales for rich fills + gradients. Use the full
               spectrum: the renderer dithers to the panel, so you do NOT need to snap to the raw
               palette. Reserve exact palette hex (list_devices()) only for fine text/icons where
               dithering would look noisy.
             - SVG -> SVG(). @svgdotjs/svg.js for programmatic vector graphics (rings, arcs, badges).
             - Phosphor icons, all six weights: <i class="ph-bold ph-heart"></i> (also ph (regular),
               ph-thin, ph-light, ph-fill, ph-duotone). Each weight's font is inlined only when its
               class appears in your code. Search the 1500+ valid names with GET /api/mcp/icons?q=<term>
               rather than guessing slugs. Two traps: regular weight needs BOTH classes
               (class="ph ph-heart" -- ph-heart alone renders nothing), and a wrong slug renders a
               BLANK BOX with no error; render_report().icon_invalid names bad icon refs, check it
               whenever you placed icons.
             - Fonts: any bundled font (the names in appearance.fonts from list_widgets) works in the
               sandbox by family name, e.g. `font-family: "Fira Code"` or `"Press Start 2P"`. Only
               fonts your code actually names are inlined, so there's a broad programming + pixel set
               available at no cost until used.
           Chart.js example: put a <canvas id="c"> in "html", then in "js":
           "new Chart(document.getElementById('c'),{type:'line',data:{labels:[...],
           datasets:[{data: ctx.data.weather.hourly.map(h=>h.temp)}]}})".

INTERACTIVE CONTROLS -- two ways, don't mix them up:
  * Want a BUTTON, SWITCH, SLIDER, or STEPPER (a control the user names)? Use a TYPED TOUCH PRIMITIVE
    {"kind":"button"|"switch"|"slider"|"stepper", ...} (see "TOUCH PRIMITIVES" below). This is the
    DEFAULT for any control asked for by name; the firmware draws it, you don't style it. "Add a
    button" -> {"kind":"button"}, NOT a rect with on_tap.
  * Want to make an EXISTING visual (a widget, a shape, a code element's output) respond to a tap
    without drawing a control? Add "on_tap"/"on_swipe"/"on_slide" to that element (TAP ACTIONS below).

TAP ACTIONS ("on_tap"/"on_swipe"/"on_slide" on ANY element -- make an existing element respond to taps):
  Touch-capable displays report a stroke; the server hit-tests it against regions extracted from the
  RENDERED layout (so they track the design, no coordinates to hand-place) and dispatches the action.
  Add any of these optional fields to an element:
    "on_tap":   an action spec -- a STRING ("refresh", "rotate_next", "rotate_prev", "step:<n>",
                "page:<page_id>", "webhook:<url>") OR a Home Assistant service-call OBJECT
                {"action":"ha","domain":"light","service":"turn_on","data":{"entity_id":"light.x", ...}}.
    "on_swipe": {"up":"<spec>","down":"<spec>","left":"<spec>","right":"<spec>"} -- each value is a
                string spec OR a Home Assistant object, same forms as on_tap. A bare object with no
                up/down/left/right key won't fire and is reported in render_report().tap_invalid.
    "on_slide": {"axis":"x"|"y","action":<spec>} -- the element becomes a SLIDER: the stroke's end
                point maps to an absolute 0-100 value along the axis (vertical fills upward; a plain
                tap sets the value at that point) and replaces "{value}" in the action. e.g. a
                one-stroke dimmer: {"action":"ha","domain":"light","service":"turn_on",
                "data":{"entity_id":"light.x","brightness_pct":"{value}"}}, or "webhook:https://…/set/{value}".
  - hotspot: {"kind":"hotspot", <box>, "on_tap"|"on_swipe"|"on_slide": ...} -- paints NOTHING; an
    invisible tap target you position over anything (e.g. over part of a code element's output).
  Code element named actions: give a "code" element "actions":{"<name>":<spec>, ...} and reference
    them from its markup as data-on-tap="@name" (also data-on-swipe / data-on-slide), in static HTML
    or JS-built DOM. Structured (HA) specs stay in validated config; the markup holds a plain @name.
    An @name with no matching entry is reported in render_report().tap_dangling.
  HOME ASSISTANT action shape (READ THIS -- getting it wrong makes the action silently no-op):
    canonical: {"action":"ha","domain":"<domain>","service":"<service>","data":{"entity_id":"<id>", ...}}.
    The entity_id and any service fields (brightness_pct, etc.) go INSIDE "data". These forgiving
    variants are also accepted and normalise to the canonical form: omitting "action" when you give a
    "service" (it's inferred), a dotted "service":"light.turn_on" (splits into domain+service), a
    top-level "entity_id", the HA-native "target":{"entity_id":...}, service fields at the top level
    beside entity_id (brightness_pct etc. are folded into data), and a comma-joined entity_id string
    (split into the list HA wants). Sliders substitute the 0-100
    value into "{value}" (also "$value"), which becomes a number for fields like brightness_pct.
    Execution is SERVER-SIDE via the ha_core plugin's connection (base URL + long-lived token in
    Settings -> Widgets -> Home Assistant Core), a POST to /api/services/<domain>/<service>. This is
    NOT the read-only ha_service data source; you do NOT need a "callable" service source.
  On-device requirement: the target panel must be touch-capable (list_devices() shows "touch": true,
    e.g. the reTerminal E1003) AND running firmware that reports touch. On a display-only panel these
    fields render but never fire.

TOUCH PRIMITIVES (button / switch / slider / stepper -- the DEFAULT for interactive controls):
  Typed controls the FIRMWARE draws + owns (the render reserves a blank rect); it hit-tests locally
  and reports the action. Reach for these whenever the user asks for a button/switch/slider/stepper:
    {"kind":"button",  <box>, "label":"Movie", "icon":"<phosphor>"?, "on_tap":<action spec>} -- fires on_tap.
    {"kind":"switch",  <box>, "label":"Desk", "value_key":"ha:<entity>", "state":"on"|"off"?} -- taps
      toggle the bound entity and reflect its live state (no on_tap; the toggle is derived).
    {"kind":"slider",  <box>, "axis":"x"|"y", "value_key":"ha:<entity>[:<attr>]",
      "value_min":0, "value_max":100, "value_step":5} -- drag sets the bound value.
    {"kind":"stepper", <box>, "value_key":"ha:<entity>", "value_min":0, "value_max":30, "value_step":1}.
  On-device: shown on touch panels running the touch-v3 firmware; a display-only panel renders the
  reserved (blank) rect but no control. The action spec forms (HA object, etc.) match TAP ACTIONS above.
  Code element named actions: give a "code" element "actions":{"<name>":<spec>, ...} and reference
    them from its markup as data-on-tap="@name" (also data-on-swipe / data-on-slide), in static HTML
    or JS-built DOM. Structured (HA) specs stay in validated config; the markup holds a plain @name.
    An @name with no matching entry is reported in render_report().tap_dangling.
  Actions you set through THESE tools are trusted config, so webhook + HA calls dispatch. The same
  action written into raw widget markup is limited to navigation (refresh/rotate/step/page) for
  safety.
  STABLE REGION IDS (protocol v2 panels -- list_devices entry carries "proto": {"v": 2}): these
  panels hit-test touch locally against per-region ids and give instant local feedback (invert,
  pre-shipped state tiles, live slider thumbs), with the server confirming by patch. Canvas
  elements are pinned automatically by their element id; interactive nodes INSIDE code-element
  markup should carry data-touch-id="<stable-name>" so the id survives markup edits -- an
  unpinned markup region gets a content-hash id that can churn, silently downgrading its instant
  feedback to a plain invert. Harmless and ignored on v1 panels, so add it whenever you author
  interactive code-element markup.
  VERIFY with render_report(): tap_regions lists every region that rendered (with its stable
  touch_id when pinned), tap_dangling lists unresolved @name refs, and tap_invalid lists regions
  whose action would NOT dispatch (with the box + gesture + reason). A region in tap_regions was
  only STORED; it will fire only if tap_invalid is empty. Always check tap_invalid == [] before
  trusting a touch dashboard.

LIVE BINDINGS ("bind" on ANY element -- makes a SHAPE reflect data):
  Data elements auto-update, but shapes (rect/ellipse/icon/line/text) are static geometry.
  Add "bind": [ <binding>, ... ] to drive a shape's props from data each render (in lockstep
  with data elements, no polling). A binding is:
    {"source":"<widget key>","options":{...},"field":"<path>","transform":"<t>","params":{...}}
  transforms:
    position -- scalar to a coordinate: {"axis":"x"|"y","in":[lo,hi],"out":[p0,p1],"center":<elemSize>}
                lo/hi may be numbers OR other field paths (e.g. "sun.riseMin").
    length   -- scalar to a size: {"dim":"w"|"h","in":[lo,hi],"out":[minPx,maxPx],"anchorMax":<px>?}
    pick     -- integer field indexes arrays: {"set":{"x":[...],"color":[...],...},"center":<elemSize>?}
    color    -- scalar to a colour by ascending thresholds: {"stops":[[max,"#hex"],...],"else":"#hex"}
    gradient -- scalar interpolated smoothly along colour stops: {"stops":[[value,"#hex"],...]}
                (quantised to the panel palette on e-ink; a value-driven gradient, not animation)
    icon     -- code/string to a Phosphor glyph: {"table":{"<code>":"ph-name"},"default":"ph-name"}
  Several bindings combine (e.g. bind x by position AND colour by threshold). A binding that
  can't resolve its value is skipped, so the element keeps its authored props.

DATA SOURCES (how every data-bearing element pulls from widgets):
  A "source" is always a WIDGET, referenced by its list_widgets() key plus an "options" object
  (the same shape get_widget_options(key) returns, e.g. a weather location or an HA entity_id).
  Tesserae fetches that widget's data server-side at render time; you read fields out of it.
  Three element kinds consume sources, all the same way:
    - data     -> ONE source ("source" + "options"), shown as a scalar/chart via "field".
    - bind     -> ONE source per binding, drives a shape's geometry/colour/icon.
    - code     -> MANY sources (the "sources" array), each named, injected as ctx.data.<name>.
  Workflow for any of them:
    1. Pick the widget key from list_widgets().
    2. Configure it with get_widget_options(key) -> set "options".
    3. probe_widget_data(key, options) -> returns "data_source" (live|sample|error), the real
       "data", and a "fields" list of bindable dot-paths with sample values. Do this BEFORE
       choosing a "field" / writing code, so you use paths that exist.
  Combining widgets is ONLY possible with a code element: give it several sources with distinct
  names and read ctx.data.<nameA>, ctx.data.<nameB>, ... (e.g. weather + transit + calendar in one
  card). The SAME (key, options) is fetched once and shared across every element that uses it, so
  reusing a source across elements is free. A source that errors still appears (as ctx.data.<name> =
  null for code, or the sample/error state for data), so guard for missing data in your code.

FIELD PATHS ("field" on data elements):
  dotted -- "current.temp"
  array index -- "hourly.0.temp"  (or "hourly[0].temp")
  pluck (for charts) -- "series.*.total"  (or "series[].total")  maps .total over every item of
     the array "series", yielding an array of numbers. Charts (line/bar/sparkline) need a field that
     resolves to an array of numbers -- use pluck to get one from an array-of-objects.
  probe_widget_data() returns a "fields" list of the bindable paths (with sample values), so you
  don't have to reverse-engineer the shape.

EDITING WITHOUT RESENDING EVERYTHING:
  update_element / delete_element / patch_canvas change one thing without re-sending the whole
  document (cheaper, fewer errors on a big canvas). add_element appends one. For a code element,
  append_code(page_id, element_id, field, text) appends to its html/css/js and saves each time, so
  an open editor re-renders after each chunk -- stream a code element in line by line for a live
  build instead of one big post at the end (add the empty code element first, then append).

AVOID CLOBBERING A CONCURRENT EDIT:
  get_canvas() returns a "rev". Pass it as base_rev to a write; if the page changed since (someone
  edited it in the UI, or another agent), the write returns HTTP 409 with the current rev, so you
  re-read instead of overwriting.

LAY OUT BY INTENT, NOT PIXELS:
  arrange(box, layout, count) returns aligned child boxes (grid/row/column) to spread across your
  elements -- no hand-computed x/y. measure_text() tells you how wide text renders so a box fits
  its content (prevents clipping). render_report() reads back what actually rendered (per-element
  value, overflow flags, live-vs-sample, colours) so you verify without eyeballing the PNG.

MATCH THE HARDWARE:
  list_devices() reports each panel's colour capability (color_mode, the renderable palette as hex,
  and a "mono" flag). The panel does NOT paint only those raw inks: the renderer dithers the
  full-colour composition down to them (Floyd-Steinberg error diffusion), so rich colours,
  gradients, and photos reproduce as blended approximations -- a red+yellow dither reads as orange
  at panel DPI. So DESIGN IN FULL COLOUR; don't flatten a layout to the handful of pure palette inks
  (that's the common mistake -- it throws away everything dithering buys you and looks poster-flat).
  Two caveats: (1) reserve exact palette hex for FINE detail -- thin text, hairline rules, small
  icons -- where dithering reads as speckle; (2) honour the "mono" flag, going grayscale only on a
  genuinely 1-2 colour panel. The palette is a fidelity guide for fine detail, not a straitjacket
  for the whole design.
"""


# Sent to the connecting agent at handshake (FastMCP ``instructions``) so it drives
# the compose loop the way the canvas surface expects, the operator prompt lives with
# the tools instead of being pasted in by hand.
INSTRUCTIONS = """\
You compose Tesserae canvases through these tools (mcp__tesserae__*). Work this loop.

DEFAULT TO THE CODE ELEMENT. For anything beyond a trivial single-widget page, build the
dashboard as a `code` element: HTML/CSS/JS fed by any number of widget sources (read as
ctx.data.<name>), with the vendored toolkit available (Chart.js, canvas-gauges, dayjs, qrcode,
marked, chroma, SVG.js, Phosphor icons). It gives full control over layout, typography, and
styling that the widget/data/shape elements can't match, and it's the only way to combine
several widgets into one cohesive design. Reach for a bare widget/data/shape element only when
the page really is just one widget, or a couple of standalone values.

START HERE (do this first, before designing the layout):
- list_devices() -> pick the target panel(s). create_canvas_page(name, w, h) sized to that
  panel's dims, then bind_devices(page_id, [device_ids]) RIGHT AWAY -- bind early even if the
  operator didn't explicitly ask. Binding up front fixes the artboard to the real panel and means
  Send / schedule / rotation already target the right hardware; you only rebind if the target
  actually changes. Skip binding only if there are genuinely no devices yet.
- Then add an EMPTY code element and build it up with append_code (streams the html/css/js in a
  chunk at a time; an open editor re-renders after each chunk). Do NOT compose the whole design
  silently and post one giant set_canvas at the end -- that reads as a long pause then a blob.
  Build incrementally and render_preview early and often so the work stays visible and responsive.

LOOP: probe -> place -> render_preview -> render_report -> adjust -> (push).
- get_widget_options(key) before placing, so you fill "options" correctly.
- probe_widget_data(key, options) to get real field dot-paths BEFORE binding any data
  primitive or shape. It reports data_source: live | sample | error, never treat
  sample/error as real.
- add_element (one at a time) or set_canvas (whole layout); for a code element, add it empty then
  append_code in chunks. Elements paint back-to-front in list order (first = back, last = front).
- render_preview(page_id) = the image; render_report(page_id) = machine-readable
  (resolved box, rendered text, overflow_x/y, data_source, colours). Use render_report to
  catch clipping and confirm live data without eyeballing pixels.
- Render looks wrong and the cause isn't visible? render_report(page_id, debug=True) BEFORE
  pixel-hunting: its "diagnostics" section names sandbox script errors (tagged [code-el <id>]),
  failed/404'd assets with URLs (fonts included), per-font-face load status, authored CSS the
  browser silently dropped (selector + declaration + reason), which vendored libs each code
  element inlined, and what gated the screenshot (settle phases + ms). One call, no bisecting.
- Debugging data staleness? Pass fresh=True to render_preview / render_report to bypass the
  last-good fallback and widget caches, so you never chase a stale cached frame.
- If a just-pushed widget shows "Failed to fetch dynamically imported module .../client.js",
  the widget isn't loaded yet (not a canvas problem): it adds an admin blueprint() (a
  restart is pending, poll /healthz then retry), the reload hasn't completed, or client.js
  errored. Reload/restart Tesserae (or wait for the restart) and re-render.

VERIFY EVERY SIZE (this is where layouts break)
- Size token comes from the cell's LONGER side: xs <=200, sm <=400, md <=700, lg >700 px.
- To review a widget across sizes on live data, build one contact-sheet canvas with the same
  widget at lg (~760px), md (~550px), sm (~380px), xs (~180px) boxes plus each fragment
  (e.g. a 430x72 "bar"), all with the same options, and render_preview once. sm/xs are where
  things overflow, check them, not just md.

BINDING & DECORATION
- data element (live NUMBER / TEXT / graph): {kind:"data", source:<key>, options:{...},
  field:"<dot.path from probe>", display:"text|number|line|bar|sparkline", ...}. A wrong
  field simply isn't in probe's list.
- Live SHAPE binding: a data element makes a value live; a "bind" on a shape makes the shape
  itself move / grow / recolour from a field. Add "bind":[{source, options, field, transform,
  params}] to any rect / ellipse / icon / line / text. Transforms: position, length, pick,
  color, gradient (a value interpolated smoothly along colour stops, quantised to the panel
  palette on e-ink), icon. Several combine. Reach for this instead of a static shape whenever
  the shape should track data. Full shape is in the set_canvas tool description.
- Paint from var(--accent-N) / semantic tokens or documented data-identity colours; avoid
  arbitrary hex.

INTERACTIVITY (touch panels): touch is NOT a separate tool. Any element can carry touch actions
as FIELDS you write into the doc-shape (full detail in the set_canvas description):
- "on_tap": a string action ("refresh", "rotate_next", "rotate_prev", "step:<n>", "page:<id>",
  "webhook:<url>") or a Home Assistant object {"action":"ha","domain","service","data":{...}}.
- "on_swipe": {"up":"<spec>","down":"<spec>","left":"<spec>","right":"<spec>"}.
- "on_slide": {"axis":"x"|"y","action":<spec>} turns the element into a slider, the stroke maps to
  0-100 and replaces "{value}" in the action (e.g. HA brightness_pct, or ".../set/{value}").
- "kind":"hotspot" is an invisible tap target you position over anything; a "code" element can take
  an "actions" map referenced from its markup as data-on-tap="@name".
render_report() returns tap_regions + tap_dangling so you can verify what's tappable. Only add
touch actions when the user asks for interactivity, and confirm before wiring a Home Assistant call.

LIVE VALUE SLOTS (overlay-capable panels): devices whose list_devices entry carries an "overlay"
object repaint small regions locally in under a second; its "max_targets" is that panel's
interactive-region budget (8 on the oldest firmware, 64 on current; always read the device's own
number). Three things follow:
- Tap targets on those panels get instant visual echo automatically -- nothing to author.
- If "overlay" reports "schema": 2, taps that fire Home Assistant actions also self-heal: the
  server re-renders after the action and the panel partial-paints just the changed regions within
  a couple of seconds (schema 1 panels converge via a full re-push a few seconds after the tap
  burst). Periodic re-renders that only move small chrome (a header clock) arrive the same way,
  so a clock on the dashboard does not cost these panels a full e-ink flash every tick. Design
  the dashboard to SHOW state normally; do not encode state in tap-echo inversion tricks, avoid
  clocks for flash reasons, or warn the user about stale control panels.
- The element showing a live value can carry data-overlay-key (optional data-overlay-suffix, e.g.
  a degree sign) -- in an authored widget's markup AND inside a canvas code element (its sandbox
  reports slots out the same way it reports touch regions). Keys are "ha:<entity_id>" (state) or
  an attribute path "ha:<entity_id>:attributes.<name>" (e.g. ha:light.desk:attributes.brightness).
  GUARDRAILS the server enforces (stay inside them by design, not by luck): at most 8 slots per
  frame; at most 2 distinct (font-size, weight) pairs across all slots (each pair costs a glyph
  atlas and the firmware carries two); values are the RAW resolved value + suffix, clipped to 47
  chars, drawn from a numeric charset (0-9 . , : - + % degree C F space) -- free text renders as
  blanks, so annotate numeric-ish values, or add data-overlay-map='{"on":"1","off":"0"}' to remap
  non-numeric states into that charset. Verify with render_report(): "overlay_slots" lists the
  annotations that actually extracted, and an empty list against your markup means the annotation
  was lost (zero-size box, hidden, or malformed key). On panels without an "overlay" entry the
  attribute is inert and harmless.
  THE REGION BUDGET IS A HARD CAP on protocol v2 panels ("proto": {"v": 2}): the device
  hit-tests locally against a manifest trimmed to "max_targets", so interactive zones beyond the
  budget DO NOT FIRE AT ALL -- whole sections of an over-budget dashboard feel dead to the user
  (on v1 panels the overflow merely loses its instant echo and still dispatches). The server
  trims by priority -- navigation, then sliders, then taps, then swipes, document order within
  each class -- and logs the dropped region ids by name. So: count your interactive zones
  against the device's advertised "max_targets" before authoring, put the most important
  controls earliest in the document, and split a denser dashboard across deck pages rather than
  exceeding the budget.

PUSH: once the render looks right, push_to_device to the same panel(s) you bound at the start --
device_ids may name several panels and the one render fans out to each, fitted to its own dims.
(You already called bind_devices up front, so scheduling / rotation / the editor's Send are set;
only call it again if the target changed.) Confirm before pushing to a real panel.

WIRE UP NAVIGATION / SCHEDULING (once the pages exist):
- Everything timed is stored as a Deck now (#167): create_deck accepts timer advance
  ("advance": "timer" with advance_interval_minutes / advance_anchor for a page cycle, or
  advance_trigger "interval" / "daily" with advance_fires_at for schedule-style fires, plus
  advance_fallback_page_id). Prefer create_deck for new timed content.
- rotations (list_rotations / create_rotation / delete_rotation): DEPRECATED views over plain
  timer decks (a rotation IS a deck with advance "timer" on the cycle trigger); they keep
  working and edit the same records list_decks shows. Steps are [{page_id, dwell_minutes}].
- schedules (list_schedules / create_schedule / delete_schedule): DEPRECATED views over
  interval / daily trigger decks; they keep working and edit the same records list_decks
  shows. Push a page to its devices on an interval or at a daily time.
- decks (list_decks / create_deck / delete_deck, and suggest_decks): group pages a user navigates
  between (button / touch) and keep them PRE-RENDERED per device so a press or tap is instant. A
  deck is exactly the graph of page:<id> tap/swipe links between pages, so once you have wired
  those links (on_tap:"page:<id>" / on_swipe), call suggest_decks() to get a ready-made deck (graph
  + touch zones filled in) and create_deck() it instead of hand-building the graph. Offer this when
  several pages link to each other. Devices whose list_devices entry carries "deck_cache" go one
  better: they store the deck's frames on local storage (SD) and navigate with the radio off, so
  decks are the single snappiest navigation you can give those panels.
"""
