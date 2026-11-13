# Build a widget with AI

A widget needs a manifest and `client.js`. It draws inside an isolated
Shadow DOM, with styles in its `<style>` block.
Add `server.py` if it needs data.

Have the model read [the widget contract & design system](../widgets.md).
Keep it open while you work.

!!! tip "Works with any capable coding assistant"
    The examples use [Claude Code](https://claude.com/claude-code), which can read files
    and run commands. Other coding agents work too. For a chat model, paste
    the contract alongside the prompts.

## Why this works well

- [`docs/widgets.md`](../widgets.md) defines the contract: `render(shadow, ctx)`, the `ctx` fields,
  and Spectra tokens (`--bg`, `--surface`, `--text-primary`, `--accent-1..6`, etc.).
  It also covers the independent `data-style` setting, container queries, and e-ink rules.
- The 37 widgets in `plugins/` give the model examples. Try "Model your widget on `weather_now`".
- `/_test/render?plugin=<id>&size=md` renders one widget without a dashboard in seconds.
  The dev server reloads automatically; refresh to see edits.
- Have the model write a smoke test, then run it to check the render.

## Setup

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main --dev      # auto-reload + /_test/render enabled
```

Sign in first. These routes require both the dev server and a session,
even on loopback. Open
`http://127.0.0.1:8765/_test/render?plugin=<id>&size=md` (also
`size=xs|sm|lg`). The whole-gallery review page is at
`http://127.0.0.1:8765/_test/widgets`.

## The loop

1. Orient the model: point it at the contract + a reference widget.
2. Describe the widget: data source, what each size shows, the layout.
3. Let it scaffold the files under `plugins/<id>/`.
4. Render and critique: open `/_test/render`, paste back a screenshot or describe what's off.
5. Write the smoke test and run `pytest plugins/<id>/`.
6. Check the constraints (below) and open a PR.

## Copy-paste prompts

### 1. Orient the model

```text
We're building a widget for Tesserae, a self-hosted e-ink dashboard.
A widget is a drop-a-folder plugin under plugins/<id>/ with:
  - plugin.json  (manifest)
  - client.js    (ES module, default export `render(shadow, ctx)`, renders into a Shadow DOM)
  - server.py    (optional, server-side data fetch -> ctx.data)

Before writing anything, read docs/widgets.md and
docs/widget-design-system.md end to end, together they're the full
contract: the ctx shape; the Spectra token layers (paint from the
semantic layer: --bg, --surface, --surface-sunken, --text-primary /
--text-secondary / --text-muted, --accent-1..6 plus their
--accent-*-soft pairs, --on-accent, never hard-coded hex except the
documented data-identity colours like team/brand/flag); the orthogonal
--data-style axis (typography / spacing / shape, never colour) and
the style-tunable tokens it exposes (--edge-weight, --label-transform,
--pill-radius, etc.); the seven archetype body classes (.stat-body,
.list-body, .chart-body, .status-body, .cal-body, .wx-body, .img-body);
container queries (cqw/cqh); Phosphor icon usage (bold for big icons,
never fill); and the e-ink constraints (no drawn borders, no
animations, no client-side fetch, use server.py).

Then read these three shipped widgets as the canonical patterns:
plugins/weather_now, plugins/weather_hourly, plugins/weather_forecast.

Confirm you've read them and summarise the contract back to me in 5 bullets
before we design anything.
```

### 2. Describe the widget to build

Fill in the data source and layout for each size:

```text
Build a widget: <id> ("<Display Name>").

Purpose: <one sentence, who's it for, why is it better than glancing at a phone>

Data source: <public API URL with no key, or which Core plugin's settings it
needs>. If it needs server-side data, write server.py with a urllib request,
a sensible timeout, a User-Agent of "tesserae/0.1 (+<id>)", and a short disk
cache in data_dir (copy the 10-minute cache pattern from weather_now/server.py).
Return {"error": "..."} on failure, never raise.

Sizes + layout:
  - xs (180x180): <what shows>
  - sm (380x240): <what shows>
  - md (640x400): <what shows>
  - lg (1200x800): <what shows>

Visual style: follow the no-borders, bold-block design language from the weather
widgets, colour blocks from --accent-1 / --accent-2 / --accent-3 (their
--accent-N-soft pairs for tinted backgrounds) plus --surface-sunken for
neutral chips, heavy type, one big bold Phosphor hero icon. Use ctx.cell.size
to drop non-essential sections at xs/sm.

Pick a body archetype (.stat-body for a hero metric, .list-body for rows,
.chart-body for charts, etc.), the archetypes already carry the font-size
cascade and gap rhythm, don't roll your own.

Write client.js + plugin.json (+ server.py if data-fetched) under
plugins/<id>/. Spectra widgets don't ship a client.css, paint inside
shadow.innerHTML via a <style> block. Don't hard-code hex except for the
documented data-identity cases in docs/widgets.md.
```

### 3. Critique the render

Open `/_test/render?plugin=<id>&size=md`, then:

```text
Here's how it renders at md and xs [paste screenshots or describe]. Issues:
- <e.g. the hero number overflows at xs>
- <e.g. the rain block uses danger red, switch to accent2/accent3, danger is
  reserved for semantic states per docs/widgets.md>
Fix these and keep it within the contract. Don't add borders or animations.
```

### 4. Write the smoke test

```text
Write plugins/<id>/tests/test_smoke.py following the pattern in docs/widgets.md
("Smoke test pattern"): parametrise over xs/sm/md/lg, patch urllib so no real
network call happens, hit /_test/render?plugin=<id>&size=<size>, and assert the
cell rendered with data-plugin="<id>" plus a couple of values from the fake
payload. Then run it: .venv/bin/python -m pytest plugins/<id>/ -q
```

## The constraints the model must respect

Tell the model to follow these rules from [the contract](../widgets.md):

- [ ] Use Spectra semantic tokens by default: `var(--bg)`, `var(--surface)`, `var(--surface-sunken)`,
  `var(--text-primary/-secondary/-muted)`, `var(--accent-1..6)` and `--accent-1-soft..6-soft` for tinted backgrounds.
  Use `var(--on-accent)` for text on accent fills. Hard-coded hex is allowed only for documented team/brand/flag colours.

  If gradients or layered shapes need more colours, declare `"design": {"palette": "extended"}` in `plugin.json`.
  Floyd-Steinberg dithering converts them to the panel palette. Only opt in when strict tokens cannot express the design.
  See [`plugins/weather_now_scenic`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now_scenic).
- [ ] Choose a body class: `.stat-body` / `.list-body` / `.chart-body` / `.status-body` / `.cal-body` / `.wx-body` / `.img-body`.
  These handle font sizes, gaps, and zoom. Don't build your own body layout.
- [ ] Use fallbacks: `var(--edge-weight, 1px)`, `var(--label-transform, uppercase)`, `var(--pill-radius, 999px)`, etc.
  Standard defaults live on `:root`; styles override them on `[data-style]`.
  Fallbacks keep unstyled widgets working.
- [ ] Don't draw borders. Use `--bg` / `--surface` contrast and spacing instead of `border:` rules.
  Borders disappear when dithered on Spectra 6.
- [ ] Use `ph-bold` for big icons. `ph-fill` turns into solid blobs.
- [ ] No animations, transitions, or `requestAnimationFrame`: screenshots can catch them half-rendered.
  Set `animation: false` on Chart.js too.
- [ ] No client-side `fetch`. Use `server.py`.
  Screenshots wait for declared `<img>` loads and fonts, so client fetches can finish too late.
  Don't assume the panel has internet access.
- [ ] Overwrite `shadow.innerHTML`; don't append. The renderer may call you twice, so render must be idempotent.
- [ ] Don't load fonts. Set `font-family: inherit` on `:host`; use `--font-family` and `ctx.font.family` for the page font.
- [ ] Don't add a `variant` cell option. Use the independent `data-theme` × `data-style` settings.
  Each widget should work with every theme and style. Add a theme or style for a new visual direction.
- [ ] Don't use `grid-template-rows: 1fr auto` for a hero above a list.
  It can look right at `lg`, but at `md` / `sm`, the `auto` list takes height from the `1fr` hero.
  Use `grid-template-rows: auto minmax(0, 1fr)` or limit rows with `.size-sm .row:nth-child(n+3) { display: none; }`.
  See [the contract's "Hero + list layouts"](../widgets.md#hero-list-layouts-dont-let-auto-rows-starve-the-hero).
- [ ] Check every size in `/_test/render`, including `md` and `lg`.
  Layouts often break at `sm` and `xs`. Get screenshots of all four sizes before calling it done.
- [ ] Write friendly `error` strings in `server.py:fetch()`; the cell displays them directly.
  Raw errors like `"HTTPError: 404 Not Found"` need useful explanations, such as `"Country code 'XX' is not supported."`.
  Handle invalid input, upstream failures, and rate limits; use a calm fallback for other errors.

## Make it composable (`fragments`)

Open Dashboards → New dashboard → *Freeform canvas* to place widgets anywhere.
By default, each widget is placed whole. To place parts separately, such as
a weather card's temperature or sun times, declare `fragments` in `plugin.json`
and have `render()` draw the selected part:

```json
"fragments": [
  { "id": "full", "label": "Full card",   "w": 320, "h": 200 },
  { "id": "temp", "label": "Temperature", "w": 160, "h": 90, "icon": "ph-thermometer" },
  { "id": "sun",  "label": "Sun times",   "w": 200, "h": 60, "icon": "ph-sun" }
]
```

```js
export default function render(shadow, ctx) {
  const frag = ctx.fragment || "full";        // "full" = the whole widget
  const data = ctx.data;                        // fetch() output, unchanged
  if (frag === "temp") { paintTemp(shadow, data); return; }
  if (frag === "sun")  { paintSun(shadow, data); return; }
  paintFull(shadow, data);
}
```

- Your `server.py` `fetch()` runs once with the instance's options.
  `ctx.fragment` selects the markup. Each fragment has its own location, entity, etc. in the `cell_options` drawer.
- Each fragment must stand alone. Use `100%`/container units and semantic tokens to fit the user's box.
  Don't depend on the surrounding card's layout.
- `w`/`h` set the default box size; users can resize it.
  Declare `full` only if whole-widget placement needs a different default size.

A widget without `fragments` is placed whole; `ctx.fragment` is `"full"`.
Prefer a few useful fragments. For independent pieces, consider separate
widgets, as in the `github_*` family.

## Structured design first (optional)

For a complex widget, fill in [`docs/widget-design-brief.md`](../widget-design-brief.md) before coding.
Include ASCII mockups for each size, an icon manifest, and a tone-rules table.

## Lineups arrows (optional)

A widget that walks a list can let the Lineups page step through its items.
Declare how in `plugin.json`:

```json
"lineups": { "pin_option": "paper" }
```

Give `server.py` a `lineup_items(options)` returning `[{"key": ..., "label": ...}]`.
The page renders with `options["paper"]` set to the chosen key.
Push now sends that picture.

If items cannot be named and each render differs, declare `"lineups": { "draw": true }`.
The arrows draw again. A sequential `mode` becomes `random` for these renders, so browsing does not move your cursor.

Draw renders also carry `_draw` (a token) and `_browse` (turns past the current one).
If your widget tracks its own turn, show that turn without advancing it.

An item can also carry `"thumb": "<image URL>"` to show a preview picture in the lineup
editor and on the Lineups page. Use an absolute http(s) URL or an app-relative path
(`url_for` works too); anything else is dropped. Core adds the app's URL prefix to an
app-relative path, so it loads under Home Assistant ingress. An item without a thumb shows
its rendered thumbnail or a label card.

If `lineup_items(options, *, ctx=None)` or `lineup_item_count(options, *, ctx=None)` takes
a `ctx` keyword, it also gets a plain dict with:

- `data_dir`
- `cell_w` and `cell_h`: the element's size in px on the target display
- `placement_id`
- `device_id`, or `None`
- `panel_w` and `panel_h`

A hook with one argument is called as before.

The lineup editor lists a dashboard's items in its rows. Two optional extras help it:

- `"lineups": { "noun": "papers" }` names the items ("6 papers" instead of "6 items").
- A draw widget can report how many items it draws from with `lineup_item_count(options)`
  in `server.py`. Return an int, or `None` when it can't say. Without it, the editor shows
  "draw" instead of a count.

## Submitting

You have three paths; pick the one that matches your widget's audience:

### Bundle into Tesserae itself

For foundational widgets you want every install to ship with:

- Run the smoke test and `.venv/bin/ruff check plugins/<id>/`.
- Open a PR against [dmellok/tesserae](https://github.com/dmellok/tesserae).
  New widgets are welcome, especially ones backed by a documented,
  key-free public API (those land in the **Stable** tier; see
  [Widget stability tiers](../widgets/tiers.md)).
- If your widget hits an undocumented or scraped endpoint, say so
  in the PR; it'll be tiered **Best-effort** or **Fragile** so users
  know what to expect.

Once it's merged and you've captured screenshots, it shows up
automatically in the [widget gallery](../widgets/gallery.md):

```sh
python scripts/capture_widget_shots.py    # single hero shot per widget
```

That refreshes the gallery image at `docs/screenshots/widgets/<id>.png`.
Use `/_test/matrix` in the dev server to check one widget across every theme and style.

For ongoing design work, `python scripts/widget_contact_sheet.py`
builds a single PNG showing your widget at all four sizes
side-by-side so you can check for regressions.

### Keep it to your own install

A widget that only makes sense for your setup doesn't need the catalog.
Zip the widget folder (the one holding `plugin.json`) and pick it with
**Upload widget** on Widgets → Installed widgets. A `.tar.gz` works too, and so does a
zip made with Finder's Compress.

The widget goes into `data/authored/<id>/`, survives updates, and appears in the picker immediately.
Use this on the Home Assistant App too; its data folder is unavailable over Samba or SSH.

Invalid manifests are refused with a reason. The folder name becomes the id
and must not match a bundled widget.

Uploading the same id again replaces it. **Remove** on the widget's row
deletes it. A widget with an admin page (`blueprint()` in `server.py`) needs
a restart before that page appears; the top bar offers one.

A widget's `server.py` runs on your server with the same access as Tesserae
itself, so upload only code you wrote or have read.

### Publish through the community catalog

Use the catalog for widgets users choose to install, including niche uses and third-party APIs.
Keep the widget in its own GitHub repo and submit a PR to the catalog index.
Users find it in Widgets → Browse catalog.

Follow [Publish a widget through the catalog](publishing-a-widget.md) for releases and review.
It also covers bundles, which install `_core` and display widgets together.
