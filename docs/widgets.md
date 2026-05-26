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

Source: [`app/composer.py`](../app/composer.py) → `SIZE_DIMENSIONS`,
[`static/composer.js`](../static/composer.js) → `SIZE_THRESHOLDS`.

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

Source: [`app/panel.py`](../app/panel.py) → `PANEL_PRESETS`.

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

`<id>` must match `^[a-z][a-z0-9_]*$` and is the URL slug + the disk
path. Convention: `<theme>_<role>` — e.g. `weather_now`, `weather_hourly`,
`year_progress`.

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

Full schema: [`schema/plugin.schema.json`](../schema/plugin.schema.json).

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
  theme: {                  // resolved palette (hex strings)
    bg: "#ffffff",
    surface: "#e2d4b8",
    // ...12 tokens total
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

## Theme palette — the 12 tokens

Available as both `var(--theme-<token>)` in CSS and `ctx.theme.<token>`
in JS (for canvas / Chart.js). User themes only define these 12 — no
hard-coded hex in widgets, ever.

| token       | typical use                                        |
|-------------|----------------------------------------------------|
| `bg`        | cell background                                    |
| `surface`   | subpanel / card background                         |
| `surface2`  | emphasised card (e.g. "today" in a 5-day grid)     |
| `fg`        | primary text                                       |
| `fgSoft`    | secondary text                                     |
| `muted`     | labels, supporting metadata                        |
| `accent`    | brand highlight — icons, links, charts             |
| `accentSoft`| accent fill at low contrast (chart areas, pills)   |
| `divider`   | chart grid lines + axes only — **not** for card borders (see no-borders note below) |
| `danger`    | errors, very-high warnings                         |
| `warn`      | caution, hot temps, high UV                        |
| `ok`        | success, low UV                                    |

Source: [`schema/plugin.schema.json`](../schema/plugin.schema.json)
→ `themes.palette`.

CSS custom properties are set on the **cell host** by the composer.
They cross the Shadow DOM boundary automatically — `var(--theme-fg)`
inside your shadow works without any extra wiring.

For Chart.js (canvas) you need actual hex strings — use `ctx.theme`:

```js
new Chart(canvas.getContext("2d"), {
  type: "line",
  data: { datasets: [{ borderColor: ctx.theme.accent, ... }] },
});
```

### Tinting by semantics

The weather suite establishes a convention worth copying:

* condition icons by code: clear→`warn`, partly cloudy→`accent`,
  overcast/fog→`muted`, rain→`accent`, snow→`fgSoft`, storms→`danger`
* UV value by band: low→`ok`, mod/high→`warn`, very high→`danger`
* range arrows: high→`warn`, low→`fgSoft`
* rain pills: highlighted (`accent`) when probability ≥ 30%

Pattern: data-driven `tone` argument that flows to inline
`style="color: var(--theme-${tone})"`. Stays palette-only — works on
every theme.

---

## Phosphor icons

Vendored locally under `/static/icons/phosphor/`. Six weights:

| weight   | usage                                            |
|----------|---------------------------------------------------|
| regular  | default; everywhere unless noted                  |
| fill     | hero / emphasis icons, big condition markers      |
| duotone  | special accents (sun-horizon, moon-stars)         |
| bold     | rarely; arrows can pick this up                   |
| light    | rarely; subtle iconography                        |
| thin     | rarely                                            |

Markup:

```html
<i class="ph ph-cloud-sun" aria-hidden="true"></i>
<i class="ph-fill ph-fill-warning-circle" aria-hidden="true"></i>
<i class="ph-duotone ph-duotone-sun-horizon" aria-hidden="true"></i>
```

Inside Shadow DOM you must load the weights you use:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/fill/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/duotone/style.css">
  ...`;
```

Each `<link>` is ~10 KB CSS + a ~250 KB woff2 font. Only load the
weights you actually use — `regular` is almost always required;
`fill` is the next most useful; everything else is opt-in.

Icon name reference: <https://phosphoricons.com/>.

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

## Optional design baseline — `widget-base.css`

[`static/style/widget-base.css`](../static/style/widget-base.css)
defines a tiny vocabulary you can opt into. Aligned with the
no-borders design language: every "card" element gets its shape from
its surface colour, not from a drawn outline.

* `.widget` — outer wrapper (grid + container queries + theme bg/fg)
* `.head` + `.head-icon` + `.head-title` + `.head-place` + `.head-time`
* `.tile` — generic card surface (uses `--theme-surface`)
* `.stat` + `.stat-ico` + `.stat-text` + `.stat-label` + `.stat-value`
* `.pill` (and `.pill.is-accent` / `.is-ok` / `.is-warn` / `.is-danger`)
* `.state-empty` / `.state-error` — centered fallback states

To opt in, link it before your own `client.css`:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/style/widget-base.css">
  <link rel="stylesheet" href="/plugins/<id>/client.css">
  ...`;
```

The current shipped widgets (`weather_now`, `weather_hourly`,
`weather_forecast`) do NOT use this baseline — they roll their own
CSS — but the conventions there are good defaults if Claude Design
needs a starting point.

---

## E-ink considerations

The Spectra 6 / Waveshare E6 panel has **6 colours**: black, white,
yellow, red, blue, green.

* Use the theme tokens — themes already map to palette-friendly hex
  ranges. Don't sample colours outside the palette.
* **No drawn borders.** Themes are tuned so `bg` vs `surface` contrast
  defines card shapes — sections rise through colour, not lines. Thin
  borders dither into invisibility on Spectra 6 anyway, and the
  no-borders pattern reads cleaner in the admin too. Use spacing,
  background shifts, and type hierarchy instead of `border:` rules.
  `divider` token survives for chart axes and grid lines only.
* Refresh time is ~25 s on the 13.3" panel. Static, dense layouts
  win; busy gradients quantise into noise.
* Tabular numerics align cleanly: `font-variant-numeric: tabular-nums`.
* Anti-aliased type works on Spectra 6 but won't survive heavy
  saturation tweaks. Stick to weight ≥ 500 for anything small.

---

## Reference: shipped widgets

* [`plugins/weather_now`](../plugins/weather_now/) — hero icon + stats
  grid + sun row. Adapts xs→lg. Open-Meteo + 10-min disk cache.
* [`plugins/weather_hourly`](../plugins/weather_hourly/) — Chart.js
  line of next 12/24/48 hours + rain-probability strip. Vendored
  Chart.js loaded lazily from `/static/vendor/chart.umd.min.js`.
* [`plugins/weather_forecast`](../plugins/weather_forecast/) — 5
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

* Don't hard-code hex colours. Use `var(--theme-*)` or `ctx.theme.*`.
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
