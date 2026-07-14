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

__version__ = "0.5.8"

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
           ctx.w, ctx.h). The frame runs scripts but has NO network and NO same-origin access (data is
           delivered, never fetched), and renders ONCE (e-ink is static -- no interactivity or animation).
           Use this for custom layouts/formatting a "data" primitive can't express, or to combine widgets.
           Call probe_widget_data(key, options) per source to see field shapes, then read those paths off
           ctx.data.<name>. Example js: "document.body.textContent = Math.round(ctx.data.weather.current.temp)+'°'".
           These libraries are preloaded in the sandbox (each auto-loads only when your code references
           it by the global shown, so unused ones cost nothing). No network, so they're your toolkit:
             - Chart.js -> window.Chart. Charts to a <canvas>. Animations are already off. On e-ink there's
               no hover, so bake values in: also reference ChartDataLabels (chartjs-plugin-datalabels,
               auto-registered) and add datalabels to a dataset/options to print values on bars/points.
             - canvas-gauges -> RadialGauge / LinearGauge. Dials + meters (temperature, fuel-style).
             - dayjs -> dayjs(...). Date/time parse + format; utc + timezone plugins are pre-extended, so
               dayjs.utc(...) and dayjs(...).tz("Europe/Berlin") work. Use for formatting ctx.data times.
             - qrcode -> qrcode(typeNumber, ecc). QR codes; render .createSvgTag() or .createImgTag().
             - marked -> marked.parse(md). Markdown -> HTML string; assign to an element's innerHTML.
             - chroma -> chroma(...). Colour parsing/scales; snap values to the panel palette so colours
               don't quantise away (list_devices() gives the palette hex).
             - SVG -> SVG(). @svgdotjs/svg.js for programmatic vector graphics (rings, arcs, badges).
             - Phosphor icons: use <i class="ph-bold ph-heart"></i> in your html; the bold icon font is
               inlined when your code contains a ph- class.
           Chart.js example: put a <canvas id="c"> in "html", then in "js":
           "new Chart(document.getElementById('c'),{type:'line',data:{labels:[...],
           datasets:[{data: ctx.data.weather.hourly.map(h=>h.temp)}]}})".

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
  and a "mono" flag). Design within that palette so colours don't quantise away on the panel.
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

LOOP: probe -> place -> render_preview -> render_report -> adjust -> (push).
- get_widget_options(key) before placing, so you fill "options" correctly.
- probe_widget_data(key, options) to get real field dot-paths BEFORE binding any data
  primitive or shape. It reports data_source: live | sample | error, never treat
  sample/error as real.
- create_canvas_page then set_canvas (whole layout) or add_element (one at a time).
  Elements paint back-to-front in list order (first = back, last = front).
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

PUSH: list_devices, then push_to_device once the render looks right. Confirm before pushing
to a real panel.
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
        the target panel, and design within its palette so colours don't quantise
        away on the hardware."""
        return _json("GET", "/devices")

    def list_pages() -> Any:
        """List existing canvas (freeform) dashboards."""
        return _json("GET", "/pages")

    def create_canvas_page(name: str, w: int = 800, h: int = 480) -> Any:
        """Create a new, empty canvas dashboard and return its id. Then set_canvas()
        to lay it out. Size it to your target panel (see list_devices)."""
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

    def render_report(page_id: str) -> Any:
        """Read back what a canvas actually rendered, as JSON (a companion to
        render_preview's image). Per element: the resolved box, the text that
        rendered, overflow/clip flags (overflow_x when content is wider than its box),
        "data_source" (live | sample | error | static), and computed colours; plus the
        board's resolved background / theme. Use it to verify a render — catch
        clipping, confirm live data, read the real colours — without parsing a PNG.
        (Widget cells render into shadow DOM, so their "text" may be empty; data
        primitives and decorations report their text.)"""
        return _json("GET", f"/pages/{page_id}/render_report")

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
        """Render the canvas and push it to the given devices (ids from list_devices).
        device_ids is required — pushing is always explicit."""
        return _json("POST", f"/pages/{page_id}/push", {"device_ids": device_ids})

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
        update_element,
        append_code,
        delete_element,
        patch_canvas,
        arrange,
        measure_text,
        render_report,
        render_preview,
        push_to_device,
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
