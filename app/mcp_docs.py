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
  "theme": str, "style": str,         # ids from list_widgets(section="appearance")
  "font": str, "bg": str,             # optional font id and background colour override
  "els": [ <element>, ... ],          # painted in list order: first = back, last = front
  "inputs": [ <config input>, ... ]   # optional; the settings this dashboard asks for
}
An <element> is {"kind": ..., "x","y","w","h", ...}. The type goes in "kind",
NOT "type": {"kind":"code","x":0,"y":0,"w":480,"h":800,"html":"","css":"","js":""}.
Unknown fields are refused with a 422 naming them rather than silently ignored,
so "type" fails outright instead of drawing nothing. Kinds: widget, code, data,
text, icon, shape, line, html, svg, hotspot, and the touch primitives
(button, switch, slider, stepper).
"inputs" is the dashboard's own config surface, shown at /pages/canvas/c/<id>/configure
and offered as the install questions when it's shared to the catalog. Declare one for
every value the operator will plausibly want to change later -- an API endpoint, a
postcode, an entity id, a city -- so they can edit it on a settings page instead of
hunting through element options. Skip it for values that are structural to the design.
  {"name":"trash_api", "label":"Bin collection API URL", "type":"string",
   "help":"", "default":"", "required":false, "secret":false, "mask":null,
   "targets":[{"el":"<element id>","slot":"source_options","index":0,"key":"url"}]}
"name" is lowercase a-z0-9_ (max 32) and unique; "type" is string|textarea|number|
boolean|select|location_search ("select" needs "choices":[{"value","label"}]).
"secret" true keeps the value out of the render context and redacts it on share;
"mask" decides whether the settings field is a password box and defaults to "secret",
so set it false on a secret the operator still has to read back, a URL being the usual
case. A "target" says where the answer lands; list several to have one answer fill
several elements. "slot" is one of:
  options        -- the element's own options; "key" names the option
  source_options -- options of sources[index] on a code element; "key" names the option
  source_url     -- the url of sources[index]
  source_header  -- headers of sources[index]; "key" names one header, or omit "key"
                    to let the answer be the whole header map as JSON
  bind_options   -- options of bind[index]
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
             - Fonts: any bundled font (list_widgets(section="appearance").fonts) works in the
               sandbox by family name, e.g. `font-family: "Fira Code"` or `"Press Start 2P"`. Only
               fonts your code actually names are inlined, so there's a broad programming + pixel set
               available at no cost until used.
           Every one of those is chosen by matching your code, so render_report().injected_libs
           reports what got inlined and the token that triggered it -- check it if an element
           renders styled in a way you didn't author. To take no ambient CSS or JS at all (an
           element that hand-authors its own SVG and styling), set "autolibs": false on it; nothing
           is then injected, icon classes and bundled font names included.
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
  Typed controls a touch panel's firmware draws + owns: it hit-tests locally and reports the action,
  and the render reserves a blank rect for it. On every other target -- a display-only panel, a
  preview, an unbound page -- the server paints the control instead, so it is always visible
  somewhere. Reach for these whenever the user asks for a button/switch/slider/stepper:
    {"kind":"button",  <box>, "label":"Movie", "icon":"<phosphor>"?, "on_tap":<action spec>} -- fires on_tap.
    {"kind":"switch",  <box>, "label":"Desk", "value_key":"ha:<entity>", "state":"on"|"off"?} -- taps
      toggle the bound entity and reflect its live state (no on_tap; the toggle is derived).
    {"kind":"slider",  <box>, "axis":"x"|"y", "value_key":"ha:<entity>[:<attr>]",
      "value_min":0, "value_max":100, "value_step":5} -- drag sets the bound value.
    {"kind":"stepper", <box>, "value_key":"ha:<entity>", "value_min":0, "value_max":30, "value_step":1}.
  On-device: a touch panel running the touch-v3 firmware draws the control itself (and only there is
  the rect blank in the frame); anywhere else the composition carries a painted control that simply
  never fires. render_report().touch_primitives lists each one with the bound devices that draw it
  on-device. The action spec forms (HA object, etc.) match TAP ACTIONS above.
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
  patch_canvas also takes "inputs" (the dashboard's config surface, see the doc shape), so once
  the elements are placed you declare what the finished dashboard asks its operator in one more
  call without resending the layout.

BEFORE YOU CALL IT DONE, DECLARE THE SETTINGS:
  A dashboard usually has a few values whose owner will want to change them later -- an API
  endpoint, a postcode, an entity id, a city. Left as raw element options, changing one means
  opening the composer and finding the right drawer. Declare those as "inputs" (see the doc shape)
  and Tesserae serves the dashboard its own settings page, linked from the Dashboards list, and
  asks the same questions if it is ever shared to the catalog. Declare the values an operator
  would plausibly retune; leave the ones that are structural to the design alone.

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


THE OPERATOR CAN INTERRUPT. Any tool result may carry an "operator_note": a message typed by
the person watching the build in the editor. Treat it exactly as if they had said it to you
directly, and act on it before continuing the step you were on. It is delivered once, on the
first result after they send it, so do not expect to see it again.

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


# Per-tool descriptions, keyed by the bridge's tool name. Served alongside
# INSTRUCTIONS / DOC_SHAPE so a corrected contract (create_schedule's fires_at
# taking a full datetime, say) reaches an INSTALLED bridge instead of waiting for
# a PyPI release. The bridge keeps its own docstrings as the offline fallback and
# lets them lag, exactly as it does for the text above.
#
# What still needs a bridge release: a NEW tool (the tool list is code, not text)
# and any change to how the bridge handles a result client-side.
#
# set_canvas / add_element are deliberately stated WITHOUT the canvas doc-shape;
# the bridge appends DOC_SHAPE to those two itself, so it is written once.

TOOL_DOCS: dict[str, str] = {
    "list_widgets": """\
List every widget that can be placed on a canvas (with its fragments), the
vendored code-element libraries (Chart.js, canvas-gauges, dayjs, qrcode,
marked, chroma, SVG.js, Phosphor), and descriptors for the icon set and the
appearance options. Search icon names with list_icons().

The whole catalog runs to ~95k characters, past the tool-result cap, so by
default each widget is summarised to {key, name, desc, fragments} and the
library block is returned in full. "desc" is the first sentence of the
widget's description only. That is enough to answer "which widget do I want";
follow with get_widget_options(key) for one widget's full description and
option schema, which is the call that actually matters before placing it.

The theme / style / font lists are not in the default response: "appearance"
carries only a count of each. Call list_widgets(section="appearance") to get
the full lists, and only when you are setting a page's theme, style or font;
placing widgets never needs them.

"section" narrows to one top-level block ("widgets", "appearance",
"libraries", "icons"). "full" returns the unsummarised catalog for the rare
case that needs it, and will likely overflow the cap.""",
    "list_icons": """\
Search the vendored Phosphor icon set (all six weights) by case-insensitive
substring, so you pick a real slug instead of guessing. The query is
normalised to slug form first (a "ph-" prefix is stripped, underscores become
dashes), so q="ph-heart" and q="calendar_heart" both match. Use a returned
slug as an icon element's "icon" value, or in code-element markup as ph-<slug>
(weight via ph / ph-bold / ph-thin / ph-light / ph-fill / ph-duotone; regular
weight needs BOTH classes, class="ph ph-heart" -- ph-heart alone renders
nothing). An icon name that isn't in this set renders a BLANK BOX with no
error; render_report().icon_invalid names such references after the fact.
Empty "q" returns a capped sample plus the total; "limit" caps results
(max 500).""",
    "list_services": """\
List SERVICE data sources (kind "service") -- non-placeable plugins that
expose a whole external service's API (e.g. Home Assistant, a weather API, a
generic REST endpoint) as data for a code element. They don't appear in
list_widgets (they can't be placed), but you use one exactly like a widget
source: its "key" is a valid "source"/"sources[].key" for a code or data
element. Workflow: list_services() -> get_widget_options(key) to see its
scope options -> probe_widget_data(key, {}) with EMPTY options first (a
service returns a self-describing map of the scopes/endpoints it offers) ->
probe_widget_data(key, {scope...}) to see a specific slice -> use it as a
source. Returns {services: [{key, name, description, options}]}.""",
    "get_widget_options": """\
Get the configurable options for one widget, so you can fill an element's
"options" correctly (e.g. a weather widget's location). Also returns the
widget's full "desc" (list_widgets carries only its first sentence). Each
option carries a "format" hint for its type. Big choice lists (HA entity
pickers) are omitted by default (the option shows "choices_count" + a
"choices_endpoint"); pass include_choices=True to inline them, or call
get_widget_choices() to page.""",
    "get_widget_choices": """\
Page through the choice rows for one of a widget's options (kept out of
get_widget_options so a picker with hundreds of entries doesn't bloat the
schema). "q" filters by case-insensitive substring on value/label.""",
    "probe_widget_data": """\
Return a widget's data as JSON to pick "field" paths before binding a data
primitive. Returns {data, data_source, reason, fields}: "data_source" is
"live" (real fetch), "sample" (demo fallback because nothing was configured),
or "error" (fetch failed) so you never mistake a placeholder for a real
result; "fields" lists the bindable dot-paths with sample values (a wrong key
simply isn't in the list; an empty payload has fields with null values).

Long lists in "data" are truncated to "max_items" entries, each trimmed
list carrying a sibling "<name>__truncated" note with the real length, so
a big payload (a 24h Home Assistant history runs to hundreds of KB) stays
readable instead of blowing the result limit and being unusable. "fields"
is never truncated: it is the reason to call this tool. Pass full=True for
the whole payload, or raise max_items, when you genuinely need every row.""",
    "list_devices": """\
List registered display devices with panel dims AND colour capability:
"color_mode" (e.g. "6-colour (Spectra 6)"), "colors" (the renderable palette
as hex), "gamut", "orientation", and a "mono" flag. Match a canvas's w/h to
the target panel. The panel dithers the full-colour render down to these inks,
so DESIGN IN FULL COLOUR -- the palette guides fine detail (thin text/icons),
it doesn't cap the whole layout. Honour "mono" for grayscale-only panels.

"touch": true appears on panels with a touch digitizer (e.g. the Seeed
reTerminal E1003). On those, the on_tap / on_swipe / on_slide actions you
put on a dashboard's elements actually fire on the hardware. If a device
has no "touch" flag it's display-only (or button-driven), so don't add
touch actions expecting them to work there.

Firmware capability flags ride along when the hardware reports them:
"overlay": {schema, max_targets} means the panel repaints small regions
locally (instant tap echo, plus data-overlay-key live value slots --
see the LIVE VALUE SLOTS section of the server instructions for the
guardrails). "schema": 2 additionally means frame patches: after a tap
fires a Home Assistant action, and on periodic re-renders that only
move small chrome (a header clock), the panel partial-paints the
changed regions on its own instead of full-flashing, so control
dashboards stay truthful and clock chrome is flash-free without any
authoring tricks ("schema": 1 panels converge via a full re-push a
few seconds after a tap burst).
"proto": {"v": 2} means the panel speaks protocol v2: it hit-tests
touch locally against stable region ids and gives instant feedback
(inverts, state tiles, slider thumbs) with the server confirming by
patch -- pin ids on interactive code-element markup with
data-touch-id (see the STABLE REGION IDS section of the server
instructions). "deck_cache": {capacity_bytes} means the device caches
deck frames on local storage and navigates decks instantly with the
radio off. "kind" is the hardware model id. All read-only facts; never
ask the user to enable them, the firmware advertises them.""",
    "list_pages": """\
List existing canvas (freeform) dashboards.""",
    "create_canvas_page": """\
Create a new, empty canvas dashboard and return its id. Size it to your
target panel (see list_devices), then call bind_devices(page_id, [device_ids])
RIGHT AWAY so the artboard and Send/schedule target the real hardware from the
start. Then build the layout (add an empty code element + append_code, or
set_canvas).""",
    "delete_canvas_page": """\
Delete a canvas dashboard by id (e.g. a throwaway QA page). Returns
{ok: true} on success, or 404 if it isn't a canvas page.""",
    "get_canvas": """\
Get the full canvas document (size, appearance, and every element) for a
page, plus "rev" / "updated_at" / "updated_by". Keep the "rev" and pass it as
base_rev on your next write to be warned (HTTP 409) if the page drifted.""",
    "set_canvas": """\
Replace a canvas dashboard's document. Returns a compact {ok,id,rev,elements}
ack (not the full document), or an error with field-level "details" (HTTP 422) if
the document is invalid, so you can correct it and retry. Pass base_rev (the rev
from get_canvas) to be warned with HTTP 409 if the page changed under you. For a
one-field change prefer update_element / patch_canvas. After setting, call
render_preview() (or render_report()) to check the result.""",
    "set_canvas_background": """\
Generate an AI background image (fal.ai) from "prompt" and set it as the
canvas' full-bleed background; the data elements composite crisply ON TOP,
so the data never passes through the image model. Sized to the canvas'
aspect. Optional "model" (default flux/schnell), "style" (e.g. watercolor,
bauhaus, risograph), and "fit" (cover|contain|stretch).
The fal.ai API key lives on the "AI image" widget (the fal-image widget) --
that's the primary place it's configured, so if this returns a 400 about a
missing key, tell the user to paste their fal.ai key into that widget's
settings (Settings -> Plugins -> Fal Image). It also falls back to
app.fal.api_key or the FAL_KEY env var. Returns the ack plus the stored
"bg_image" URL; 502 if generation fails.""",
    "add_element": """\
Append ONE element to a canvas and save (each call is a separate save, so an
open editor updates live as you build). "element" is a single element object
(same shapes as set_canvas's "els"); returns {ok,id,rev,elements,element_id}.
Prefer this when building incrementally; use set_canvas to replace the whole
layout at once.

The element's type goes in "kind", NOT "type": {"kind": "code", "x": 0,
"y": 0, "w": 480, "h": 800, ...}. Unknown fields are refused with a 422
naming them rather than silently ignored, so "type" fails outright.""",
    "add_elements_bulk": """\
Append MANY elements to a canvas in one save (max 500). Built for a large
primitive board that won't fit in a single set_canvas body: chunk the elements
across a few calls instead of inlining a 20k+ document. All-or-nothing: if any
element is invalid (unknown field / schema error) nothing is appended and the
offending index is named, so a bad chunk never half-lands. Returns the ack plus
"element_ids" (in order) and "appended" (the count).""",
    "update_element": """\
Change ONE element in place without re-sending the whole document. "patch"
is a partial element ({field: value, ...}) merged over the existing one (a
provided "options"/"parts" replaces wholesale). The cheap edit path for a big
canvas: change a precision, a colour, a location, one box. Returns the ack.""",
    "append_code": """\
Append "text" to a code element's "field" ("html" | "css" | "js") and save.
Each call is a separate save, which pushes a live update to an editor open on
the page, so you can STREAM a code element in (chunk by chunk / line by line)
and watch it build up, instead of posting the whole blob at once. Typical flow:
add_element an empty code element (with its sources), then append_code repeatedly
for html, then css, then js. Returns the ack plus the field's new "length".
Don't thread base_rev while streaming (the rev changes every append).""",
    "delete_element": """\
Remove ONE element from a canvas by id. Returns the compact ack.""",
    "patch_canvas": """\
Change document-level fields (any of name, w, h, theme, style, font, bg,
bg_image, bg_fit) without touching the elements. Use update_element / set_canvas
for elements. Returns the ack.""",
    "arrange": """\
Compute "count" aligned child boxes inside "box" ({x,y,w,h}) for a
"grid" / "row" / "column" layout, so you place cells by intent instead of
hand-computing pixels. "gap" is the space between cells, "pad" the inset from
the box edge, "cols" forces a grid column count (default ~sqrt). Returns
{boxes:[{x,y,w,h}, ...]}; spread them across your elements' geometry (bake
them in as normal elements — they stay individually editable).""",
    "measure_text": """\
Measure how wide/tall text renders in a widget font, so a box fits its
content (prevents clipping). "items" is a list of {text, font?, size?, weight?,
max_width?}. Returns {items:[{text,width,height,fits}]} where "fits" is whether
the text is within max_width. Font names come from
list_widgets(section="appearance").fonts.""",
    "describe_actions": """\
The authoritative touch-action vocabulary for canvas elements, so you don't
have to reverse-engineer it: the element fields (on_tap / on_swipe / on_slide /
actions), the string grammar (refresh / rotate_next / rotate_prev / step:<n> /
page:<id> / webhook:<url>), the Home Assistant object form and every input
variation that normalises to it, the slider {value} placeholder, the provenance
rule (webhook/HA fire only from config, not raw markup), and how to verify wiring.
Element-level actions are NOT part of get_widget_options (that's cell data).""",
    "render_report": """\
Read back what a canvas actually rendered, as JSON (a companion to
render_preview's image). Per element: the resolved box, the text that
rendered, overflow/clip flags (overflow_x when content is wider than its box),
"data_source" (live | sample | error | static), and computed colours; plus the
board's resolved background / theme. Also "tap_regions" (every touch region that
rendered: box + resolved on_tap/on_swipe/on_slide action), "tap_dangling" (code
element @name refs with no matching entry), and "tap_invalid" (regions whose action
would NOT dispatch: box + gesture + reason). A region in tap_regions was only
STORED -- it fires only if it is NOT in tap_invalid, so check tap_invalid == [] to
trust a touch dashboard. Use it to verify a render — catch clipping, confirm live
data, read the real colours, check touch targets fire — without parsing a PNG.
(Widget cells render into shadow DOM, so their "text" may be empty; data
primitives and decorations report their text.)

"icon_invalid" (always on, same spirit as tap_invalid): icon references that
resolve to NO glyph and render a blank box -- an icon element's unknown slug or
weight, a bind icon-table value, or a ph-<name> class in code/html markup that
isn't a real Phosphor name -- each with the element id and reason. Check it
whenever you placed icons; fix with a slug from list_icons(q).

"injected_libs" (always on): the vendored bundles each code element's sandbox
inlined -- Chart.js, a Phosphor weight, a bundled font. The choice is INFERRED
from the element's own html/css/js, so each entry carries "inferred" and the
"matched" token behind it; an element that ended up carrying a stylesheet it
never asked for is named here instead of deduced from pixels. Set an element's
"autolibs": false to inject nothing at all.

On a large board the full report can be big. Pass view="touch" for just the
touch-wiring sections (tap_regions / tap_invalid / tap_dangling), or
fields="tap_invalid,tap_dangling" (any of board / elements / tap_regions /
tap_invalid / tap_dangling / injected_libs) to trim it. id + rev always ride
along.

debug=True adds a "diagnostics" section -- reach for it whenever a render
looks wrong and the cause isn't visible: "console" (error/warn output from
every frame; a throwing code-element script lands here tagged
[code-el <id>]), "page_errors" (uncaught exceptions), "network" (failed and
4xx/5xx requests -- a 404 font names its URL), "settle" (what gated the
capture: goto / compose-signal / image-wait / font-wait outcome + ms),
"fonts" (every face: loaded | pending-at-capture | failed | never-requested,
with src), "css" (CSS the browser silently dropped: invalid authored
declarations, plus any rule missing from the stylesheet a code element's
sandbox actually composed), "libraries" (the same record as injected_libs,
with per-element detail). Diagnose from this instead of pixel-hunting.

fresh=True re-fetches widget data (bypasses the last-good fallback and
widget caches), so a stale cached result can't mislead a debugging pass.""",
    "render_preview": """\
Render the canvas to a PNG at its authored size and return the image, so you
can visually check the layout and iterate. This is your feedback loop: place →
render_preview → adjust → set_canvas → render_preview again. For a
machine-readable check (values, overflow, colours), use render_report().
fresh=True re-fetches widget data (bypasses the last-good fallback and widget
caches) -- use it while debugging so a stale cached result can't mislead you.""",
    "push_to_device": """\
Render the canvas ONCE and fan it out to the given devices (ids from
list_devices), each fitted/quantised to its own panel by the server.
device_ids is required and may list many panels -- pushing is always
explicit. This is a one-off; use bind_devices to remember the target set
for scheduling. Returns {sent: [...], errors: [...]}.""",
    "bind_devices": """\
Persistently bind a canvas to a set of devices (replaces the set; []
unbinds). Call this EARLY -- right after create_canvas_page -- not just before
a push: it SAVES the target set on the page so the artboard, a later schedule /
rotation, and the visual editor's Send all hit the same panels. Unlike
push_to_device (a one-off fan-out), it doesn't render. Ids not matching a
registered device (list_devices) are dropped and returned under "unknown".
Returns {bound, unknown}.""",
    "list_rotations": """\
List rotations: ordered page cycles that advance on a wall-clock
anchor (and via prev/next buttons). Each has steps [{page_id,
dwell_minutes}] and device_ids.""",
    "create_rotation": """\
Create or replace a rotation. 'rotation' is a full object:
{id, name, device_ids, steps:[{page_id, dwell_minutes}], anchor?
("HH:MM"), days_of_week?}. Bind the step pages to the devices first
(bind_devices). Returns {ok, id}, or 422 with "details" on a bad shape.""",
    "delete_rotation": """\
Delete a rotation by id.""",
    "list_schedules": """\
List schedules: time-driven pushes of a page to its devices (interval
or daily, with day-of-week + time-window filters).""",
    "create_schedule": """\
Create or replace a schedule. 'schedule' is a full object:
{id, name, page_id, type ("interval"|"daily"), interval_minutes? or
fires_at?, days_of_week?, priority?}. Returns {ok, id}, or 422 with
"details" on a bad shape.

'name' is REQUIRED alongside 'id' and must be non-empty; omitting it
422s. A daily schedule's 'fires_at' is a FULL datetime, not "HH:MM":
only the time-of-day is used, so the date is a placeholder and
"2000-01-01T06:00:00" is the conventional way to write 6am.""",
    "delete_schedule": """\
Delete a schedule by id.""",
    "list_decks": """\
List decks: groups of pages kept pre-rendered per device so a button
press or touch that moves between them is instant. Each page links to
others by a button name or a touch zone.""",
    "suggest_decks": """\
Suggest decks derived from the page:<id> tap/swipe links you set on
page elements (on_tap / on_swipe): connected clusters of linked pages,
each a ready-to-create Deck with its graph + touch zones filled in. Use
this after wiring inter-page navigation to offer the user a deck.""",
    "create_deck": """\
Create or replace a deck. 'deck' is a full object: {id, name,
device_ids, pages:[{page_id, refresh_interval_minutes? (per-page
override of the deck default; 0 = warm only on first visit), links:
[{target_page_id, and exactly one of button:"left"/"right"/... OR
zone:{x,y,w,h in 0..1}}]}], entry_page_id?, refresh_interval_minutes?}.
Link targets must be pages in the deck. Prefer suggest_decks() to build
one from existing page links. Omitting links entirely is also fine: the
server derives the graph from the pages' page:<id> tap/swipe links, and
pages still link-less after that get default prev/next (left/right)
navigation on-device, so a plain {id, name, device_ids, pages:[{page_id}]}
deck navigates out of the box. Returns {ok, id, links_derived}, or 422
on a bad shape.""",
    "delete_deck": """\
Delete a deck by id.""",
}
