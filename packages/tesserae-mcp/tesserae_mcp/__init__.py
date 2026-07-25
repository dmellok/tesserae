"""tesserae-mcp — the stdio MCP bridge an AI agent connects to.

A thin client over a running Tesserae's ``/api/mcp`` HTTP surface. It exposes
tools an agent uses to build **freeform (canvas) dashboards**: discover widgets
and devices, create + edit a canvas, **render a preview image** to check its own
work, and push to a panel.

Tesserae side: enable the ``mcp`` experiment under Settings → System → MCP.

Config via environment:
- ``TESSERAE_URL``        base URL of a running Tesserae (default http://127.0.0.1:8765)
- ``TESSERAE_MCP_TOKEN``  the MCP token (optional when the agent runs on the same
                          machine as Tesserae, since loopback is trusted)

Run it as ``tesserae-mcp`` (console script) or ``python -m tesserae_mcp``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

__version__ = "0.6.3"

_BASE = os.environ.get("TESSERAE_URL", "http://127.0.0.1:8765").rstrip("/")
_TOKEN = os.environ.get("TESSERAE_MCP_TOKEN", "").strip()

# The canvas-document shape, embedded in the set_canvas tool description so the
# agent knows exactly what to write.
_DOC_SHAPE = """A canvas document is JSON:
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
               class appears in your code.
             - Fonts: any bundled font (the names in appearance.fonts from list_widgets) works in the
               sandbox by family name, e.g. `font-family: "Fira Code"` or `"Press Start 2P"`. Only
               fonts your code actually names are inlined, so there's a broad programming + pixel set
               available at no cost until used.
           Chart.js example: put a <canvas id="c"> in "html", then in "js":
           "new Chart(document.getElementById('c'),{type:'line',data:{labels:[...],
           datasets:[{data: ctx.data.weather.hourly.map(h=>h.temp)}]}})".

TOUCH ACTIONS ("on_tap"/"on_swipe"/"on_slide" on ANY element -- respond to taps on a touch panel):
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
    Settings -> Plugins -> Home Assistant Core), a POST to /api/services/<domain>/<service>. This is
    NOT the read-only ha_service data source; you do NOT need a "callable" service source.
  On-device requirement: the target panel must be touch-capable (list_devices() shows "touch": true,
    e.g. the reTerminal E1003) AND running firmware that reports touch. On a display-only panel these
    fields render but never fire.
  Code element named actions: give a "code" element "actions":{"<name>":<spec>, ...} and reference
    them from its markup as data-on-tap="@name" (also data-on-swipe / data-on-slide), in static HTML
    or JS-built DOM. Structured (HA) specs stay in validated config; the markup holds a plain @name.
    An @name with no matching entry is reported in render_report().tap_dangling.
  Actions you set through THESE tools are trusted config, so webhook + HA calls dispatch. The same
  action written into raw widget markup is limited to navigation (refresh/rotate/step/page) for
  safety.
  VERIFY with render_report(): tap_regions lists every region that rendered, tap_dangling lists
  unresolved @name refs, and tap_invalid lists regions whose action would NOT dispatch (with the box
  + gesture + reason). A region in tap_regions was only STORED; it will fire only if tap_invalid is
  empty. Always check tap_invalid == [] before trusting a touch dashboard.

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


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, bytes, str]:
    """Call the Tesserae MCP API. Returns (status, raw_bytes, content_type)."""
    url = f"{_BASE}/api/mcp{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    # Cloudflare (and similar WAFs) block urllib's default "Python-urllib/x.y"
    # user agent, so identify ourselves explicitly.
    req.add_header("User-Agent", f"tesserae-mcp/{__version__}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if _TOKEN:
        req.add_header("Authorization", f"Bearer {_TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "") if exc.headers else ""
    except urllib.error.URLError as exc:
        detail = getattr(exc, "reason", exc)
        raise RuntimeError(
            f"Cannot reach Tesserae at {_BASE} ({detail}). Is it running, and is the "
            f"'mcp' experiment enabled in Settings → System → MCP?"
        ) from exc


def _json(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Call the API expecting JSON. Non-2xx bodies are returned as-is (so the agent
    sees 422 validation details) rather than raised."""
    status, raw, _ = _request(method, path, body)
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {"error": raw.decode("utf-8", "replace")}
    if status >= 400 and isinstance(parsed, dict):
        parsed.setdefault("_status", status)
    return parsed


# Sent to the connecting agent at handshake (FastMCP ``instructions``) so it drives
# the compose loop the way the canvas surface expects, the operator prompt lives with
# the tools instead of being pasted in by hand.
_INSTRUCTIONS = """\
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
object repaint small regions locally in under a second; its "max_targets" is how many tap zones
get instant echo on that panel (8 on older firmware, 32 on current). Three things follow:
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
  attribute is inert and harmless. Keep interactive zones within "max_targets" or accept that
  navigation targets win the echo budget and the rest respond without the instant flash.

PUSH: once the render looks right, push_to_device to the same panel(s) you bound at the start --
device_ids may name several panels and the one render fans out to each, fitted to its own dims.
(You already called bind_devices up front, so scheduling / rotation / the editor's Send are set;
only call it again if the target changed.) Confirm before pushing to a real panel.

WIRE UP NAVIGATION / SCHEDULING (once the pages exist):
- rotations (list_rotations / create_rotation / delete_rotation): cycle a device through several
  pages on a wall-clock cadence; steps are [{page_id, dwell_minutes}]. Bind the step pages first.
- schedules (list_schedules / create_schedule / delete_schedule): push a page to its devices on an
  interval or at a daily time.
- decks (list_decks / create_deck / delete_deck, and suggest_decks): group pages a user navigates
  between (button / touch) and keep them PRE-RENDERED per device so a press or tap is instant. A
  deck is exactly the graph of page:<id> tap/swipe links between pages, so once you have wired
  those links (on_tap:"page:<id>" / on_swipe), call suggest_decks() to get a ready-made deck (graph
  + touch zones filled in) and create_deck() it instead of hand-building the graph. Offer this when
  several pages link to each other. Devices whose list_devices entry carries "deck_cache" go one
  better: they store the deck's frames on local storage (SD) and navigate with the radio off, so
  decks are the single snappiest navigation you can give those panels.
"""


def build_server() -> Any:
    """Construct the FastMCP server with all tools registered.

    Tools are registered via ``add_tool`` (not the ``@tool`` decorator) so the
    optional, untyped ``mcp`` SDK doesn't strip the type info off our functions.
    """
    from mcp.server.fastmcp import FastMCP, Image

    def list_widgets() -> Any:
        """List every widget that can be placed on a canvas (with its fragments) and
        the available theme/style/font appearance options."""
        return _json("GET", "/catalog")

    def list_services() -> Any:
        """List SERVICE data sources (kind "service") -- non-placeable plugins that
        expose a whole external service's API (e.g. Home Assistant, a weather API, a
        generic REST endpoint) as data for a code element. They don't appear in
        list_widgets (they can't be placed), but you use one exactly like a widget
        source: its "key" is a valid "source"/"sources[].key" for a code or data
        element. Workflow: list_services() -> get_widget_options(key) to see its
        scope options -> probe_widget_data(key, {}) with EMPTY options first (a
        service returns a self-describing map of the scopes/endpoints it offers) ->
        probe_widget_data(key, {scope...}) to see a specific slice -> use it as a
        source. Returns {services: [{key, name, description, options}]}."""
        return _json("GET", "/services")

    def get_widget_options(widget: str, include_choices: bool = False) -> Any:
        """Get the configurable options for one widget, so you can fill an element's
        "options" correctly (e.g. a weather widget's location). Each option carries a
        "format" hint for its type. Big choice lists (HA entity pickers) are omitted
        by default (the option shows "choices_count" + a "choices_endpoint"); pass
        include_choices=True to inline them, or call get_widget_choices() to page."""
        suffix = "?include_choices=true" if include_choices else ""
        return _json("GET", f"/widgets/{widget}/options{suffix}")

    def get_widget_choices(
        widget: str, option: str, q: str = "", limit: int = 100, offset: int = 0
    ) -> Any:
        """Page through the choice rows for one of a widget's options (kept out of
        get_widget_options so a picker with hundreds of entries doesn't bloat the
        schema). "q" filters by case-insensitive substring on value/label."""
        path = f"/widgets/{widget}/choices?option={option}&limit={limit}&offset={offset}"
        if q:
            path += f"&q={q}"
        return _json("GET", path)

    def probe_widget_data(widget: str, options: dict[str, Any] | None = None) -> Any:
        """Return a widget's data as JSON to pick "field" paths before binding a data
        primitive. Returns {data, data_source, reason, fields}: "data_source" is
        "live" (real fetch), "sample" (demo fallback because nothing was configured),
        or "error" (fetch failed) so you never mistake a placeholder for a real
        result; "fields" lists the bindable dot-paths with sample values (a wrong key
        simply isn't in the list; an empty payload has fields with null values)."""
        return _json("POST", f"/widgets/{widget}/data", {"options": options or {}})

    def list_devices() -> Any:
        """List registered display devices with panel dims AND colour capability:
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
        "deck_cache": {capacity_bytes} means the device caches deck frames on
        local storage and navigates decks instantly with the radio off. "kind"
        is the hardware model id. All read-only facts; never ask the user to
        enable them, the firmware advertises them."""
        return _json("GET", "/devices")

    def list_pages() -> Any:
        """List existing canvas (freeform) dashboards."""
        return _json("GET", "/pages")

    def create_canvas_page(name: str, w: int = 800, h: int = 480) -> Any:
        """Create a new, empty canvas dashboard and return its id. Size it to your
        target panel (see list_devices), then call bind_devices(page_id, [device_ids])
        RIGHT AWAY so the artboard and Send/schedule target the real hardware from the
        start. Then build the layout (add an empty code element + append_code, or
        set_canvas)."""
        return _json("POST", "/pages", {"name": name, "w": w, "h": h})

    def delete_canvas_page(page_id: str) -> Any:
        """Delete a canvas dashboard by id (e.g. a throwaway QA page). Returns
        {ok: true} on success, or 404 if it isn't a canvas page."""
        return _json("DELETE", f"/pages/{page_id}")

    def get_canvas(page_id: str) -> Any:
        """Get the full canvas document (size, appearance, and every element) for a
        page, plus "rev" / "updated_at" / "updated_by". Keep the "rev" and pass it as
        base_rev on your next write to be warned (HTTP 409) if the page drifted."""
        return _json("GET", f"/pages/{page_id}/canvas")

    def _rev_suffix(base_rev: str) -> str:
        return f"?base_rev={base_rev}" if base_rev else ""

    def set_canvas(page_id: str, canvas: dict[str, Any], base_rev: str = "") -> Any:
        return _json("PUT", f"/pages/{page_id}/canvas{_rev_suffix(base_rev)}", canvas)

    def set_canvas_background(
        page_id: str,
        prompt: str,
        model: str = "",
        style: str = "",
        fit: str = "",
    ) -> Any:
        """Generate an AI background image (fal.ai) from "prompt" and set it as the
        canvas' full-bleed background; the data elements composite crisply ON TOP,
        so the data never passes through the image model. Sized to the canvas'
        aspect. Optional "model" (default flux/schnell), "style" (e.g. watercolor,
        bauhaus, risograph), and "fit" (cover|contain|stretch).
        The fal.ai API key lives on the "AI image" widget (the fal-image widget) --
        that's the primary place it's configured, so if this returns a 400 about a
        missing key, tell the user to paste their fal.ai key into that widget's
        settings (Settings -> Plugins -> Fal Image). It also falls back to
        app.fal.api_key or the FAL_KEY env var. Returns the ack plus the stored
        "bg_image" URL; 502 if generation fails."""
        body: dict[str, Any] = {"prompt": prompt}
        if model:
            body["model"] = model
        if style:
            body["style"] = style
        if fit:
            body["fit"] = fit
        return _json("POST", f"/pages/{page_id}/background", body)

    def add_element(page_id: str, element: dict[str, Any], base_rev: str = "") -> Any:
        """Append ONE element to a canvas and save. Each call is a separate save, so
        an editor open on the page updates live as you build. "element" is a single
        element object (same shapes as set_canvas's "els"). Returns the compact ack
        plus the new "element_id". Prefer this when building incrementally; use
        set_canvas to replace the whole layout at once."""
        return _json("POST", f"/pages/{page_id}/elements{_rev_suffix(base_rev)}", element)

    def add_elements_bulk(page_id: str, elements: list[dict[str, Any]], base_rev: str = "") -> Any:
        """Append MANY elements to a canvas in one save (max 500). Built for a large
        primitive board that won't fit in a single set_canvas body: chunk the elements
        across a few calls instead of inlining a 20k+ document. All-or-nothing: if any
        element is invalid (unknown field / schema error) nothing is appended and the
        offending index is named, so a bad chunk never half-lands. Returns the ack plus
        "element_ids" (in order) and "appended" (the count)."""
        return _json(
            "POST",
            f"/pages/{page_id}/elements/bulk{_rev_suffix(base_rev)}",
            {"elements": elements},
        )

    def update_element(
        page_id: str, element_id: str, patch: dict[str, Any], base_rev: str = ""
    ) -> Any:
        """Change ONE element in place without re-sending the whole document. "patch"
        is a partial element ({field: value, ...}) merged over the existing one (a
        provided "options"/"parts" replaces wholesale). The cheap edit path for a big
        canvas: change a precision, a colour, a location, one box. Returns the ack."""
        return _json(
            "PATCH", f"/pages/{page_id}/elements/{element_id}{_rev_suffix(base_rev)}", patch
        )

    def append_code(page_id: str, element_id: str, field: str, text: str) -> Any:
        """Append "text" to a code element's "field" ("html" | "css" | "js") and save.
        Each call is a separate save, which pushes a live update to an editor open on
        the page, so you can STREAM a code element in (chunk by chunk / line by line)
        and watch it build up, instead of posting the whole blob at once. Typical flow:
        add_element an empty code element (with its sources), then append_code repeatedly
        for html, then css, then js. Returns the ack plus the field's new "length".
        Don't thread base_rev while streaming (the rev changes every append)."""
        return _json(
            "POST",
            f"/pages/{page_id}/elements/{element_id}/append",
            {"field": field, "text": text},
        )

    def delete_element(page_id: str, element_id: str, base_rev: str = "") -> Any:
        """Remove ONE element from a canvas by id. Returns the compact ack."""
        return _json("DELETE", f"/pages/{page_id}/elements/{element_id}{_rev_suffix(base_rev)}")

    def patch_canvas(page_id: str, patch: dict[str, Any], base_rev: str = "") -> Any:
        """Change document-level fields (any of name, w, h, theme, style, font, bg,
        bg_image, bg_fit) without touching the elements. Use update_element / set_canvas
        for elements. Returns the ack."""
        return _json("PATCH", f"/pages/{page_id}/canvas{_rev_suffix(base_rev)}", patch)

    def arrange(
        box: dict[str, int],
        count: int,
        layout: str = "grid",
        gap: int = 0,
        pad: int = 0,
        cols: int = 0,
    ) -> Any:
        """Compute "count" aligned child boxes inside "box" ({x,y,w,h}) for a
        "grid" / "row" / "column" layout, so you place cells by intent instead of
        hand-computing pixels. "gap" is the space between cells, "pad" the inset from
        the box edge, "cols" forces a grid column count (default ~sqrt). Returns
        {boxes:[{x,y,w,h}, ...]}; spread them across your elements' geometry (bake
        them in as normal elements — they stay individually editable)."""
        return _json(
            "POST",
            "/layout",
            {"box": box, "count": count, "layout": layout, "gap": gap, "pad": pad, "cols": cols},
        )

    def measure_text(items: list[dict[str, Any]]) -> Any:
        """Measure how wide/tall text renders in a widget font, so a box fits its
        content (prevents clipping). "items" is a list of {text, font?, size?, weight?,
        max_width?}. Returns {items:[{text,width,height,fits}]} where "fits" is whether
        the text is within max_width. Font names come from list_widgets().appearance."""
        return _json("POST", "/measure-text", {"items": items})

    def render_report(page_id: str, view: str = "", fields: str = "") -> Any:
        """Read back what a canvas actually rendered, as JSON (a companion to
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

        On a large board the full report can be big. Pass view="touch" for just the
        touch-wiring sections (tap_regions / tap_invalid / tap_dangling), or
        fields="tap_invalid,tap_dangling" (any of board / elements / tap_regions /
        tap_invalid / tap_dangling) to trim it. id + rev always ride along."""
        params = []
        if view:
            params.append(f"view={view}")
        if fields:
            params.append(f"fields={fields}")
        suffix = ("?" + "&".join(params)) if params else ""
        return _json("GET", f"/pages/{page_id}/render_report{suffix}")

    def describe_actions() -> Any:
        """The authoritative touch-action vocabulary for canvas elements, so you don't
        have to reverse-engineer it: the element fields (on_tap / on_swipe / on_slide /
        actions), the string grammar (refresh / rotate_next / rotate_prev / step:<n> /
        page:<id> / webhook:<url>), the Home Assistant object form and every input
        variation that normalises to it, the slider {value} placeholder, the provenance
        rule (webhook/HA fire only from config, not raw markup), and how to verify wiring.
        Element-level actions are NOT part of get_widget_options (that's cell data)."""
        return _json("GET", "/actions/describe")

    def render_preview(page_id: str) -> Any:
        """Render the canvas to a PNG at its authored size and return the image, so you
        can visually check the layout and iterate. This is your feedback loop: place →
        render_preview → adjust → set_canvas → render_preview again. For a
        machine-readable check (values, overflow, colours), use render_report()."""
        status, raw, ctype = _request("GET", f"/pages/{page_id}/preview.png")
        if status >= 400 or not ctype.startswith("image/"):
            raise RuntimeError(
                f"preview failed (HTTP {status}): {raw.decode('utf-8', 'replace')[:300]}"
            )
        return Image(data=raw, format="png")

    def push_to_device(page_id: str, device_ids: list[str]) -> Any:
        """Render the canvas ONCE and fan it out to the given devices (ids from
        list_devices), each fitted/quantised to its own panel by the server.
        device_ids is required and may list many panels -- pushing is always
        explicit. This is a one-off; use bind_devices to remember the target set
        for scheduling. Returns {sent: [...], errors: [...]}."""
        return _json("POST", f"/pages/{page_id}/push", {"device_ids": device_ids})

    def bind_devices(page_id: str, device_ids: list[str]) -> Any:
        """Persistently bind a canvas to a set of devices (replaces the set; []
        unbinds). Call this EARLY -- right after create_canvas_page -- not just before
        a push: it SAVES the target set on the page so the artboard, a later schedule /
        rotation, and the visual editor's Send all hit the same panels. Unlike
        push_to_device (a one-off fan-out), it doesn't render. Ids not matching a
        registered device (list_devices) are dropped and returned under "unknown".
        Returns {bound, unknown}."""
        return _json("POST", f"/pages/{page_id}/devices", {"device_ids": device_ids})

    # -- rotations / schedules / decks -------------------------------------

    def list_rotations() -> Any:
        """List rotations: ordered page cycles that advance on a wall-clock
        anchor (and via prev/next buttons). Each has steps [{page_id,
        dwell_minutes}] and device_ids."""
        return _json("GET", "/rotations")

    def create_rotation(rotation: dict[str, Any]) -> Any:
        """Create or replace a rotation. 'rotation' is a full object:
        {id, name, device_ids, steps:[{page_id, dwell_minutes}], anchor?
        ("HH:MM"), days_of_week?}. Bind the step pages to the devices first
        (bind_devices). Returns {ok, id}, or 422 with "details" on a bad shape."""
        return _json("POST", "/rotations", rotation)

    def delete_rotation(rotation_id: str) -> Any:
        """Delete a rotation by id."""
        return _json("DELETE", f"/rotations/{rotation_id}")

    def list_schedules() -> Any:
        """List schedules: time-driven pushes of a page to its devices (interval
        or daily, with day-of-week + time-window filters)."""
        return _json("GET", "/schedules")

    def create_schedule(schedule: dict[str, Any]) -> Any:
        """Create or replace a schedule. 'schedule' is a full object:
        {id, page_id, type ("interval"|"daily"), interval_minutes? or fires_at?
        ("HH:MM"), days_of_week?, priority?}. Returns {ok, id}, or 422 with
        "details" on a bad shape."""
        return _json("POST", "/schedules", schedule)

    def delete_schedule(schedule_id: str) -> Any:
        """Delete a schedule by id."""
        return _json("DELETE", f"/schedules/{schedule_id}")

    def list_decks() -> Any:
        """List decks: groups of pages kept pre-rendered per device so a button
        press or touch that moves between them is instant. Each page links to
        others by a button name or a touch zone."""
        return _json("GET", "/decks")

    def suggest_decks() -> Any:
        """Suggest decks derived from the page:<id> tap/swipe links you set on
        page elements (on_tap / on_swipe): connected clusters of linked pages,
        each a ready-to-create Deck with its graph + touch zones filled in. Use
        this after wiring inter-page navigation to offer the user a deck."""
        return _json("GET", "/decks/suggest")

    def create_deck(deck: dict[str, Any]) -> Any:
        """Create or replace a deck. 'deck' is a full object: {id, name,
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
        on a bad shape."""
        return _json("POST", "/decks", deck)

    def delete_deck(deck_id: str) -> Any:
        """Delete a deck by id."""
        return _json("DELETE", f"/decks/{deck_id}")

    mcp = FastMCP("tesserae", instructions=_INSTRUCTIONS)
    for fn in (
        list_widgets,
        list_services,
        get_widget_options,
        get_widget_choices,
        probe_widget_data,
        list_devices,
        list_pages,
        create_canvas_page,
        delete_canvas_page,
        get_canvas,
        set_canvas_background,
        add_elements_bulk,
        update_element,
        append_code,
        delete_element,
        patch_canvas,
        arrange,
        measure_text,
        describe_actions,
        render_report,
        render_preview,
        push_to_device,
        bind_devices,
        list_rotations,
        create_rotation,
        delete_rotation,
        list_schedules,
        create_schedule,
        delete_schedule,
        list_decks,
        suggest_decks,
        create_deck,
        delete_deck,
    ):
        mcp.add_tool(fn)
    mcp.add_tool(
        set_canvas,
        description=(
            "Replace a canvas dashboard's document. Returns a compact {ok,id,rev,elements} "
            'ack (not the full document), or an error with field-level "details" (HTTP 422) '
            "if the document is invalid, so you can correct it and retry. Pass base_rev (the rev "
            "from get_canvas) to be warned with HTTP 409 if the page changed under you. For a "
            "one-field change prefer update_element / patch_canvas. After setting, call "
            "render_preview() (or render_report()) to check the result.\n\n" + _DOC_SHAPE
        ),
    )
    mcp.add_tool(
        add_element,
        description=(
            "Append ONE element to a canvas and save (each call is a separate save, so an "
            "open editor updates live as you build). 'element' is a single element object; "
            "returns {ok,id,rev,elements,element_id}. Use set_canvas to replace the whole "
            "layout at once.\n\n" + _DOC_SHAPE
        ),
    )
    return mcp


def main() -> None:
    try:
        server = build_server()
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] == "mcp":
            raise SystemExit(
                "The MCP SDK isn't installed. Reinstall the bridge with its dependency:\n\n"
                "    pip install git+https://github.com/dmellok/tesserae-mcp\n"
            ) from exc
        raise
    server.run()
