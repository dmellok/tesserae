# Tesserae widget contract & design system

Drop-a-folder spec for building widgets. A widget is a `plugins/<id>/`
directory the loader picks up at boot; the composer mounts one per cell
into a Shadow DOM, hands it `(shadow, ctx)`, and Playwright screenshots
the result. The screenshot is then quantised to the panel's palette and
streamed over MQTT.

If you're designing a widget, this is the only doc you need. Everything
below is enforced by code; values come straight from the source.

---

## Cell sizes (test-render fixtures)

The composer uses these dims when rendering a single widget via
`/_test/render?plugin=<id>&size=<size>`. Real dashboards can place a
widget at any (w, h) — the size token is derived from the longer side:

| size | dims      | trigger (longer side) | typical role                  |
|------|-----------|-----------------------|-------------------------------|
| xs   | 180×180   | ≤ 200 px              | tiny tile (icon + 1 datum)    |
| sm   | 380×240   | ≤ 400 px              | small card (icon + 2-3 fields)|
| md   | 640×400   | ≤ 700 px              | half-panel widget             |
| lg   | 1200×800  | > 700 px              | full-panel feature            |

Source: [`app/composer.py`](https://github.com/dmellok/tesserae/blob/main/app/composer.py) → `SIZE_DIMENSIONS`,
[`static/composer.js`](https://github.com/dmellok/tesserae/blob/main/static/composer.js) → `SIZE_THRESHOLDS`.

Design at all four sizes. The same widget should hide non-essential
sections at `xs`/`sm` (use `.size-xs` / `.size-sm` class on your root
element — `ctx.cell.size` gives you the token, you stamp the class).

---

## Real panel dimensions

What cells actually live on. Sizes are **native landscape**; users can
mount portrait, which swaps to the other axis via the panel
orientation setting.

| preset            | native landscape | notes                              |
|-------------------|-----------------|--------------------------------------|
| `inky_4`          | 640×400         | Spectra 6, Pimoroni Inky Impression 4" |
| `inky_5_7`        | 600×448         | 7-colour legacy                       |
| `inky_7_3`        | 800×480         | Spectra 6                             |
| `inky_13_3`       | 1600×1200       | Spectra 6 (Pimoroni)                  |
| `waveshare_e6_7_5`| 800×480         | Waveshare E6                          |

Source: [`app/panel.py`](https://github.com/dmellok/tesserae/blob/main/app/panel.py) → `PANEL_PRESETS`.

Most users will put 1–4 widgets on a panel. A widget filling an
`inky_13_3` portrait alone lives in a 1200×1600 cell. Two-up on the
same panel: ~1200×800 each. Four-up grid: ~600×800.

---

## Plugin folder

```
plugins/<id>/
  plugin.json        manifest (required)
  client.js          ES module, default export = render fn (required)
  client.css         widget styles (optional but expected)
  server.py          server-side data fetch (optional)
  tests/test_smoke.py smoke test (optional but recommended)
```

`<id>` is the folder name — it doubles as the URL slug and the disk path.
The loader skips any folder starting with `.` or `_`; beyond that,
lowercase `[a-z0-9_]` is convention, not enforced. Name it `<family>_<role>`
— e.g. `weather_now`, `weather_hourly`.

---

## Manifest — `plugin.json`

```json
{
  "tesserae_compat": "1.x",
  "name": "Weather — Now",
  "version": "0.1.0",
  "kind": "widget",
  "description": "Current conditions...",
  "icon": "ph-cloud-sun",
  "supports": { "sizes": ["xs", "sm", "md", "lg"] },
  "cell_options": [
    { "name": "latitude", "type": "number", "label": "Latitude", "default": -37.8136 },
    { "name": "label", "type": "string", "label": "Place label", "default": "Melbourne" },
    {
      "name": "units", "type": "select", "label": "Units", "default": "metric",
      "choices": [
        { "value": "metric", "label": "Metric" },
        { "value": "imperial", "label": "Imperial" }
      ]
    }
  ],
  "settings": [
    { "name": "api_key", "type": "string", "label": "API key", "secret": true }
  ],
  "render": { "needs_network": true }
}
```

* **`supports.sizes`** — which test-render sizes you've designed for.
  Omit ones you don't support; the editor will skip your widget on
  cells that exceed them.
* **`cell_options`** — per-cell knobs. The editor renders one form
  field per option. Types: `string`, `textarea`, `number`, `select`
  (needs `choices`), `boolean`, `color`. The user's values land in
  `ctx.cell.options` at render time.
* **`settings`** — plugin-wide knobs (one set across all cells using
  this widget). Surfaces in `/settings/plugins/<id>`. `secret: true`
  stores under `<name>_secret` in `settings.json` so an on-disk grep
  for `secret` reveals every sensitive value.
* **`icon`** — Phosphor name used in the editor's widget picker.
* **`render.needs_network`** — hint for the renderer (not enforced).

Full schema: [`schema/plugin.schema.json`](https://github.com/dmellok/tesserae/blob/main/schema/plugin.schema.json).

---

## `client.js` contract

```js
// plugins/<id>/client.js

export default async function render(shadow, ctx) {
  // shadow: the cell's ShadowRoot. Replace innerHTML, attach <link>s.
  // ctx:    see below.
  shadow.innerHTML = `<link rel="stylesheet" href="...">
                      <link rel="stylesheet" href="/plugins/<id>/client.css">
                      <div class="root">...</div>`;
}
```

### `ctx` shape

```js
{
  cell: {
    w: 640,                // current cell width in pixels
    h: 400,                // current cell height
    size: "md",            // "xs" | "sm" | "md" | "lg"
    options: { ... }       // cell_options values, defaults merged in
  },
  panel: {
    w: 1600, h: 1200,      // full panel dims
    portrait: false        // panel.h > panel.w
  },
  theme: {                  // resolved primitive palette as hex strings —
    bg: "#ffffff",          // for canvas/Chart.js only; style DOM with --c-*
    surface: "#e2d4b8",
    // ...all 14 primitives
  },
  font: {
    family: "Inter",       // resolved page font
    weight: 400
  },
  data: { ... } | null,    // server.py fetch() result, if present
  preview: false           // true when rendered in the editor iframe
}
```

* **Be idempotent**: the renderer may invoke you twice on a slow
  reload — overwrite `shadow.innerHTML` rather than appending.
* **`async` allowed**: the renderer awaits your default export before
  screenshotting. Don't kick off work that finishes off-Promise (the
  screenshot will fire while it's still pending).
* **Animations off**: this gets rasterised. Disable Chart.js
  animations, CSS transitions, requestAnimationFrame loops.
* **Error path**: if `ctx.data?.error` is set (server.py raised),
  render a small error card with `ph-warning-circle` and the message.

---

## `server.py` contract (optional)

Use this when the widget needs data the browser can't get to (API
calls, file reads, cross-origin fetches). Output is JSON-serialised
and lands in `ctx.data`.

```python
# plugins/<id>/server.py

def fetch(options: dict, settings: dict, *, ctx: dict) -> dict:
    """
    options: cell_options for THIS cell (defaults merged in).
    settings: plugin settings (secrets are real values, not masked).
    ctx: {"panel_w": int, "panel_h": int, "preview": bool, "data_dir": str}

    Return ANY JSON-serializable value (usually a dict). On error,
    return {"error": "..."} so client.js can render an error state.
    """
    # Cache in data_dir for politeness — Open-Meteo, news APIs, etc.
    # See plugins/weather_now/server.py for the 10-minute cache pattern.
    return {"temp": 22.4, "label": options["label"]}
```

* Called fresh on every render. Cache yourself if rate-limited.
* `data_dir` is `data/plugins/<id>/` — gitignored, persisted across
  restarts.
* Network calls: use `urllib.request` with a timeout + a `User-Agent`
  header naming the widget (e.g. `tesserae/0.1 (+weather_now)`).
* Don't raise — return `{"error": "..."}`. Uncaught exceptions get
  caught upstream but produce uglier diagnostics.

---

## Colour — primitives and the semantic layer

Colour has two layers. **Themes define 14 primitive tokens.** Widgets
**never reference those primitives directly** — they paint from the `--c-*`
**semantic layer** the composer derives on every cell host. The split keeps
two ideas apart that are easy to conflate: *categorical* colour ("I need N
distinguishable blocks / chart series") and *status* colour ("this means
good / caution / bad"). A sunny day is categorical; a very-high UV reading
is status.

### Primitives — what a theme defines

14 tokens, validated by
[`schema/plugin.schema.json`](https://github.com/dmellok/tesserae/blob/main/schema/plugin.schema.json)
→ `themes.palette`. Theme authors pick these; **widgets don't touch them.**

| primitive | role |
|---|---|
| `bg` | outer cell background |
| `surface` | card / subpanel background |
| `surface2` | raised / emphasised card |
| `fg` | primary text |
| `fgSoft` | secondary text |
| `muted` | labels, supporting metadata |
| `accent` / `accent2` / `accent3` | the theme-coordinated colour triad |
| `accentSoft` | low-contrast accent fill |
| `divider` | chart axes / grid lines only |
| `ok` / `warn` / `danger` | status hues |

### Semantic tokens — what widgets paint with

Defined on the cell host in
[`templates/compose.html`](https://github.com/dmellok/tesserae/blob/main/templates/compose.html)
and inherited into every widget's shadow (whether or not it links
`widget-bauhaus.css`). **Reference these, never `--theme-*`** — the enforce
test [`tests/test_semantic_tokens.py`](https://github.com/dmellok/tesserae/blob/main/tests/test_semantic_tokens.py)
fails the build otherwise.

| semantic token | maps to | use for |
|---|---|---|
| `--c-bg` | bg | cell background |
| `--c-surface` | surface | cards |
| `--c-raised` | surface2 | emphasised cards |
| `--c-text` / `--c-text-soft` / `--c-text-mute` | fg / fgSoft / muted | text by emphasis |
| `--c-line` | divider | chart axes / grid only — never card borders |
| `--c-accent` | accent | brand highlight |
| `--c-accent-soft` | accentSoft | soft tonal fills (dithers — large fills only) |
| `--c-data-1` … `--c-data-4` | accent / accent2 / accent3 / surface2 | **categorical** — N distinguishable colours, no meaning |
| `--c-ok` / `--c-warn` / `--c-danger` / `--c-info` | ok / warn / danger / accent | **status** — advisory / hazard / error ONLY |

### Categorical vs status — the rule

Use `--c-data-*` for anything decorative or "these are different things":
stat blocks, chart series, language swatches, day columns. Use
`--c-ok` / `--c-warn` / `--c-danger` **only** when the value is a genuine
advisory, hazard, or error — a UV/AQI band, an overdue task, a failed
fetch, a missing entity. Never reach for a status token just because you
want a particular colour: it breaks on themes where `warn` is a loud alarm,
and makes a sunny day read as a warning.

**E-ink ceiling.** The categorical ramp tops out at **4 distinct hues** on a
6-ink E6 panel (yellow / red / blue / green; `--c-data-4` is the neutral
block), 5 on the 7-colour Inky (adds orange). Don't design a 6-way
categorical split — the panel can't resolve it. For tonal emphasis within
one hue use `--c-accent-soft`, but it dithers to a stipple on the panel, so
reserve it for large fills, never small text.

### Chart.js (canvas) needs hex

Canvas can't read CSS custom properties, so charts pull primitive hex from
`ctx.theme` (e.g. `ctx.theme.accent`) — the one place JS reads a primitive
directly, and only to feed a value to canvas, not to style DOM:

```js
new Chart(canvas.getContext("2d"), {
  type: "line",
  data: { datasets: [{ borderColor: ctx.theme.accent, ... }] },
});
```

---

## Phosphor icons

Vendored locally under `/static/icons/phosphor/`. Six weights:

| weight   | usage                                            |
|----------|---------------------------------------------------|
| regular  | inline icons, small icons that need to flow with text |
| bold     | **default for prominent icons** — hero icons, big condition markers, anything that should "read big". Outline-with-presence rather than solid shape. |
| duotone  | two-tone accent moments (sun-horizon, moon-stars). Use sparingly. |
| fill     | **avoid in new widgets** — solid shapes can quantise into blobs on Spectra 6 and read heavier than they should. Bold reads cleaner. |
| light, thin | special design needs; rare. |

Make prominent icons **big and bold** — that's the design language. A
hero condition icon at clamp(72px, 20cqw, 160px) in `ph-bold` reads as
a confident graphic; the same icon at the same size in `ph-fill` reads
as a blob.

Markup:

```html
<i class="ph ph-cloud-sun" aria-hidden="true"></i>
<i class="ph-bold ph-warning-circle" aria-hidden="true"></i>
<i class="ph-duotone ph-sun-horizon" aria-hidden="true"></i>
```

The class form is **compound**: a weight class (`ph-bold`, `ph-fill`,
`ph-duotone`) **plus** the bare icon-name class (`ph-cloud-sun`,
`ph-warning-circle`). Both carry the bare icon name — `ph-bold-cloud-sun`
as a single class does NOT exist and won't render.

Inside Shadow DOM you must load the weights you use:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  ...`;
```

Each `<link>` is ~10 KB CSS + a ~250 KB woff2 font. Only load the
weights you actually use — `regular` is almost always required;
`bold` is the next most useful; everything else is opt-in.

Icon name reference: <https://phosphoricons.com/>.

---

## Custom colours — escape hatch

The "always use theme tokens" rule has a narrow carve-out: when the
data you're rendering has an **inherent visual identity that the user
expects to see**, you can hard-code hex values. Examples:

* **F1 team colours** — Ferrari red `#DC0000`, Mercedes silver/black
  `#27F4D2`, Red Bull `#1E41FF`. Painting Ferrari in `--theme-accent`
  reads wrong; the data carries its own colour.
* **Brand logos and indicators** — Spotify green, GitHub black,
  particular calendar-tag colours.
* **Real-world flag colours** — country flags on race countdowns.
* **Established conventions** — gold / silver / bronze on podiums
  (though even those are sometimes better expressed as
  `warn` / `fgSoft` / `accent`).

Bar to clear: the colour is **part of the data**, not a design choice.
"It would look nice in red" doesn't pass; "this is Ferrari's red"
does.

Implementation:

```js
const TEAM_COLOURS = {
  ferrari:   "#DC0000",
  mercedes:  "#27F4D2",
  red_bull:  "#1E41FF",
  // ...
};

// Apply via inline style so it slots alongside palette-token elements.
`<span class="team-chip" style="background: ${TEAM_COLOURS[team] || 'var(--c-raised)'}">...</span>`
```

Fall back to a semantic token (`--c-raised` or `--c-accent`) for unknown
values so the widget never produces a blank/black square.

For dark mode: many brand colours need a fallback variant. Define
both in a lookup and switch based on a hint (e.g. is the theme dark?
inspect `getComputedStyle(host).getPropertyValue('--theme-bg')` and
choose). Document the choice in the widget's brief.

---

## Plugin static assets

Widgets can ship arbitrary static files alongside the source — useful
for things the icon font doesn't cover: race-track SVGs, team logos,
country flags, calendar service logos.

Drop them under `plugins/<id>/static/` or `plugins/<id>/files/`. The
plugin asset route serves anything matching those prefixes:

```
plugins/f1_next_race/
  static/
    circuits/
      silverstone.svg
      monza.svg
      ...
    flags/
      uk.svg
      it.svg
```

Reference at `/plugins/<id>/static/...`:

```js
shadow.innerHTML = `
  <img src="/plugins/f1_next_race/static/circuits/${slug}.svg"
       alt="${trackName}" class="circuit-map">
`;
```

Conventions:

* **SVG preferred over raster** — scales cleanly, ditches well on
  Spectra 6, no woff2-weight cost.
* **Monochrome SVGs that pick up `currentColor`** if you want them to
  theme automatically: `<svg fill="currentColor" stroke="currentColor">`
  in your SVG file, then set `color: var(--c-text)` on the parent.
* **Bake brand colours into the SVG** when the data IS the colour
  (team logo, flag).
* **Keep files small** — under 10 KB each ideally. The renderer waits
  for `networkidle`; large images stretch the screenshot.
* **Phosphor first** — if there's a Phosphor icon for what you want,
  use that. Custom SVG is for things Phosphor doesn't have: circuit
  outlines, team logos, country flags.

The asset route is loopback-bypassed (same gate as `/compose/`) — so
the Playwright renderer can fetch them without a session.

---

## Responsive sizing — container queries

The cell host has `container-type: size` set by the composer, so
`cqw` / `cqh` / `cqmin` units work inside your shadow:

```css
.hero-icon { font-size: clamp(48px, 18cqw, 144px); }
.label      { font-size: clamp(10px, 1.7cqw, 13px); }
```

* `cqw` = 1% of the cell's width (not the panel, not the viewport).
* `cqh` = 1% of the cell's height.
* `cqmin` = 1% of the cell's smaller side — useful for square-ish
  scaling.

Pair with explicit `.size-xs` / `.size-sm` / `.size-md` / `.size-lg`
classes for layout-level adaptation (e.g. drop entire sections at xs):

```js
shadow.innerHTML = `<div class="root size-${ctx.cell.size}">...</div>`;
```

```css
.size-xs .stats, .size-xs .sun { display: none; }
.size-xs .now-icon              { font-size: clamp(44px, 22cqw, 80px); }
```

---

## Shared baseline — `widget-bauhaus.css`

Most widgets link
[`static/style/widget-bauhaus.css`](https://github.com/dmellok/tesserae/blob/main/static/style/widget-bauhaus.css)
before their own `client.css`. It carries the shared shell so a header-bar
tweak is one edit, not 40 — and it sets the `:host` defaults (sizing,
`color: var(--c-text)`, `background: var(--c-bg)`). The `--c-*` semantic
tokens themselves come from the cell host (the composer), so they're
available whether or not a widget links this file.

* `.wb-bar` + `.wb-mark` + `.wb-title` + `.wb-bar-icon` + `.wb-bar-meta` — the inverted header strip
* `.wb-empty` + `.wb-empty-primary` + `.wb-empty-secondary` — empty state
* `.wb-root.is-error` / `.wb-error` — error state (paints `--c-danger`)

Tune proportions per widget via `--wb-*` custom properties on `:host`
(e.g. `--wb-bar-fs`, `--wb-bar-fw`) instead of redeclaring the bar.

Link it before your own `client.css`:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/<id>/client.css">
  ...`;
```

The weather suite (`weather_now`, `weather_hourly`, `weather_forecast`)
and ~40 others build on this baseline — read any of them for the pattern.

---

## E-ink considerations

The Spectra 6 / Waveshare E6 panel has **6 colours**: black, white,
yellow, red, blue, green.

* Use the `--c-*` semantic tokens — themes map their primitives to
  palette-friendly hex ranges. Don't sample colours outside the palette.
* **No drawn borders.** Themes are tuned so `--c-bg` vs `--c-surface`
  contrast defines card shapes — sections rise through colour, not lines.
  Thin borders dither into invisibility on Spectra 6 anyway, and the
  no-borders pattern reads cleaner in the admin too. Use spacing,
  background shifts, and type hierarchy instead of `border:` rules.
  `--c-line` survives for chart axes and grid lines only.
* Refresh time is ~25 s on the 13.3" panel. Static, dense layouts
  win; busy gradients quantise into noise.
* Tabular numerics align cleanly: `font-variant-numeric: tabular-nums`.
* Anti-aliased type works on Spectra 6 but won't survive heavy
  saturation tweaks. Stick to weight ≥ 500 for anything small.

---

## Reference: shipped widgets

* [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now) — hero icon + stats
  grid + sun row. Adapts xs→lg. Open-Meteo + 10-min disk cache.
* [`plugins/weather_hourly`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_hourly) — Chart.js
  line of next 12/24/48 hours + rain-probability strip. Vendored
  Chart.js loaded lazily from `/static/vendor/chart.umd.min.js`.
* [`plugins/weather_forecast`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_forecast) — 5
  day-columns with today-highlighted via `surface2`.

Read the `client.js` of any of these for the canonical patterns:
WMO-code → Phosphor icon lookup, condition tone mapping, Chart.js
loader memoisation, server.py disk-cache pattern.

---

## Smoke test pattern

```python
# plugins/<id>/tests/test_smoke.py

import json
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

_FAKE_PAYLOAD = json.dumps({"some": "fake data"}).encode()


class _FakeResp:
    def read(self) -> bytes: return _FAKE_PAYLOAD
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.mark.parametrize("size", ["xs", "sm", "md", "lg"])
def test_widget_renders(client: FlaskClient, size: str) -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResp()):
        resp = client.get(f"/_test/render?plugin=<id>&size={size}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'data-plugin="<id>"' in body
    # assert specific values from _FAKE_PAYLOAD landed in the rendered cell
```

The `client` fixture lives in `conftest.py`; it gives you a Flask
test client with the app in testing mode, every plugin discovered,
auth gate off, `/_test/render` enabled.

Run: `./.venv/bin/python -m pytest plugins/<id>/ -q`

---

## Building the widget — checklist

1. `plugins/<id>/plugin.json` — manifest (start by copying from
   `plugins/weather_now/plugin.json`).
2. `plugins/<id>/client.js` — render function. Inject the icon
   stylesheets you need, then `<link>` your `client.css`, then your
   DOM. Use `ctx.theme` / `ctx.cell.size` / `ctx.data`.
3. `plugins/<id>/client.css` — `:host { display: block; ... }` + a
   `.root` grid with size variants.
4. `plugins/<id>/server.py` — optional, if you need server-side data.
5. `plugins/<id>/tests/test_smoke.py` — parametrised over sizes.

Then hit `http://127.0.0.1:8000/_test/render?plugin=<id>&size=md` in
the browser to iterate. The Flask dev server auto-reloads on file
changes; refresh the page to see updates.

---

## What NOT to do

* Don't hard-code hex colours. Style DOM with `var(--c-*)`; use
  `ctx.theme.*` only to feed canvas / Chart.js.
* Don't reach into the parent document — you're sandboxed in a
  Shadow DOM. The composer expects that.
* Don't kick off intervals / animations / async work that finishes
  after your default-export resolves — the screenshot fires then.
* Don't load fonts. The composer's renderer already waits for
  `document.fonts.ready` and the page-level font is propagated as
  `ctx.font.family`. Setting `font-family: inherit` in `:host` is the
  right move.
* Don't fetch from your client.js. Use server.py — Playwright
  enforces `networkidle` and your fetch will stretch the screenshot.
* Don't assume internet from server.py either, on the panel side —
  it's the Tesserae host that runs `fetch()`, and the panel may be
  reading the rendered .bin offline.
