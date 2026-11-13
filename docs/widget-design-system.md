# Widget design system

Use these conventions to make widgets look consistent on a panel.
[`widgets.md`](widgets.md) defines the contract for each widget.

Read [`widgets.md`](widgets.md) first for `render(shadow, ctx)` and tokens.
Then read this page and fill in [`widget-design-brief.md`](widget-design-brief.md).

---

## 1. Archetypes

Every Spectra widget renders the same shell:

```
.w                   , the widget container
├── .w-title         , optional title bar (lead icon + label + right meta)
└── .w-body          , body content, picks ONE archetype class
    ├── .stat-body   , big-number tile (one hero metric)
    ├── .list-body   , zebra row list (news, HA entities, PR queue, …)
    ├── .chart-body  , bar / line chart + axis labels + legend
    ├── .status-body , state pill + hero + metric grid
    ├── .cal-body    , agenda / day / week / month grid
    ├── .wx-body     , weather (now block + forecast strip)
    └── .img-body    , hero image + meta (album art, camera, …)
```

An archetype is a ready-made body layout. Choose one to match your data:

| Layout | Use |
|---|---|
| `stat-body` × 2 | Two metric blocks side by side |
| `list-body` | Six rows of "thing → value" |
| `chart-body` | A trend over time |

Don't build your own body layout. Archetypes handle font sizes, gaps, and zoom
so widgets stay consistent side by side.

Full class reference + markup in [`spectra-widgets.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css).

---

## 2. Two orthogonal axes

Set theme and style on `<body>`. Either can be overridden per cell:

| Axis | Attribute | Controls | Shipped |
|---|---|---|---|
| **Theme** | `data-theme` | colour only (surfaces, text, accents) | 9 Light + 2 Dark + 3 Movement = 14 bundled (more in the catalog) |
| **Style** | `data-style` | typography / scale / density / shape (never colour) | 6 neutral + 3 movement = 9 |

Any theme works with any style. Users choose a `(theme, style)` pair for the page.

Don't hardcode fonts, edge weight, text-transform, or pill shape.
Use CSS custom properties so a switch from Mono to Display repaints the widget automatically.

Start the dev server and open `/_test/matrix`.
It shows one widget across 9 themes × 9 styles (81 combinations).

---

## 3. Token layers

Of the three layers, widgets use semantic and style-tunable tokens. Never paint from primitives.

| Layer | Tokens | Purpose |
|---|---|---|
| **Primitives** | `--fs-*` (type), `--space-*` (spacing), `--fw-*` (weights), `--lh-*` (line height), `--stroke-1..3`, `--radius-0..2`, `--icon-sm/-md/-lg` | Theme-independent raw values on `:root`. Don't redefine. |
| **Semantic** | `--bg`, `--surface`, `--surface-sunken`, `--text-primary/-secondary/-muted`, `--edge`, `--accent-1..6` (+ `-soft`), `--on-accent`, `--icon`, `--font-family` | Set per-theme on `[data-theme]`. **Paint your widget from these.** |
| **Style-tunable** | `--edge-weight`, `--zebra-bg`, `--list-pad-y`, `--list-gap`, `--pill-radius`, `--title-marker`, `--title-rule-w`, `--row-rule-w`, `--label-transform` | Defaults on `:root` (the "standard" look); styles override on `[data-style]`. **Consume via `var(--name, fallback)`** so an unstyled widget still renders. |

Source: [`spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css).

---

## 4. Title bar discipline

Use `.w-title` for every widget title. Follow these rules:

1. The title scales with the panel base. Changing `--c-zoom` grows the body
   while the title keeps its physical size.

2. Follow `--label-transform`: Editorial uses sentence case; other styles use uppercase.
   `.w-title h3` handles this. For custom labels beside the title,
   use `text-transform: var(--label-transform, uppercase)`.

Optional parts:

- Lead icon: put `<i class="ph-bold ph-<name>">` before `<h3>`.
  Use `--accent-*` by role: `accent-1` for alerts, `accent-3` for positive, etc.
- Right-side metadata: use `<span class="w-title-meta">` for a count, timestamp, or status pill.
- Coloured marker: `--title-marker: block` adds a tick before the title.
  Widgets inherit this in Mono and Bauhaus styles.
- Underline: `--title-rule-w` sets an accent line below the title.
  Display uses a 3px accent-1 line.

Anti-patterns:

- Hardcoded `text-transform: uppercase`: breaks Editorial.
- A custom title-bar background: Spectra widgets are flat; the
  archetypes don't ship a tinted title strip.
- Hardcoded font-family for the title: the style sets the family;
  widgets inherit.

---

## 5. Font cascade

The style sets `--font-family` on the body. Read it with `var(--font-family)`.
The widget shell already does this: `.w { font-family: var(--font-family) }`.

Overrides flow top-down:

```
page picker  → body inline --font-family
cell picker  → cell inline --font-family
style (active data-style) → body / cell --font-family
:root default → Helvetica Neue stack
```

Do not set `--font-family` to `'<font>', var(--font-family, …)`.
This self-reference is invalid CSS. Descendant `var(--font-family, …)` calls fall back,
and chart text becomes Helvetica regardless of style.

Use `--font-family: '<font>', system-ui, sans-serif` for an inline override,
or leave the inherited font alone.

Charts read fonts the same way via
[`tokens()`](https://github.com/dmellok/tesserae/blob/main/static/spectra-chart.js):

```js
import { tokens } from "../../static/spectra-chart.js";
const t = tokens(shadow.host);
// t.fontFamily is the resolved style font
```

---

## 6. Colour discipline

Choose accent slots by meaning. Each slot has a fixed role; the theme sets its colour.

| Slot | Role | Used for |
|---|---|---|
| `--accent-1` | alerts / peaks / "now" | error pills, current-hour highlight, urgent state |
| `--accent-2` | warnings / capacity / "winner" | yellow-flag warnings, near-full battery, trophy gold |
| `--accent-3` | positive / "up" | success state, uptrend, online |
| `--accent-4` | primary / "today" / live | the main series in a chart, today's column, "live" tag |
| `--accent-5` | secondary series | second chart series, comparison data |
| `--accent-6` | third category | third series, supplementary tags |

| Theme | Approximate colours, in slot order |
|---|---|
| Light | terracotta, ochre, moss, teal, slate-blue, plum |
| Nord | red, yellow, green, frost cyan, frost blue, purple |

Every accent has a `-soft` companion, such as `--accent-1-soft`, for tinted backgrounds
on pills, list rows, and chart areas.

`--on-accent` is the legible text colour to draw on an accent fill (e.g.
the text inside a `<span class="pill">`).

Don't choose `--accent-1` just to get red.
In Bauhaus, accent-1 and accent-6 are primary red; accent-3 is blue.

### Extended palette opt-in

Role-based accents are the default and suit ~95% of widgets.
Scenic widgets, such as sunset weather cards, may need more colours.
Opt in through the manifest:

```json
{
  "design": {
    "palette": "extended"
  }
}
```

With `"extended"`, any CSS colour, gradient, or shadow is allowed.
The browser preview shows the CSS colours. Floyd-Steinberg dithering
approximates them using the panel's palette.

What stays the same:

- Typography + spacing tokens (`--fs-display`, `--space-4`, etc.) are
  still mandatory. The opt-in covers colour only.
- The capability layer is unaffected; `requires:` declarations still
  apply.

What changes:

- Themes no longer set the widget's colours.
- Soft gradients and scenery dither well. Fine details and text on gradients read worse.
  Reviewers check readability at the target panel resolution instead of token use.
- BW and 3-colour panels lose more detail than 6-colour Spectra.
  Extended palettes work best on colour panels. Consider a fallback for low-colour hardware.

Reference implementation:
[`plugins/weather_now_scenic`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now_scenic).

---

## 7. Charts

Chart.js is loaded at the document level by compose.html, and
[`static/spectra-chart.js`](https://github.com/dmellok/tesserae/blob/main/static/spectra-chart.js)
wraps it with theme-aware helpers:

```js
import { tokens, sparkline, barChart, lineChart, hbar } from "../../static/spectra-chart.js";

const t = tokens(shadow.host);
barChart(canvas, {
  tokens: t,
  labels,
  values,
  color: t.accent5,
  highlightColor: t.accent1,
  highlightIdx: nowIndex,
});
```

`tokens(shadow.host)` reads `--accent-*`, `--surface*`, `--text-*`, and `--font-family`
from a hidden element in the cell. Pass the result to a chart helper
to theme its bars, lines, axes, and grid.

Theme/style changes remount cells and redraw charts. Chart.js stores colours
in the canvas, so it needs this redraw.

Existing helpers:

- `sparkline(canvas, values, color)`: no axes, no legend, area fill,
  used by finance, weather, energy.
- `barChart(canvas, { values, labels, color, highlightIdx, … })`: bars
  with axis labels.
- `lineChart(canvas, { values, labels, fill, … })`: line trace with
  axis labels, sparkline-styled area fill.
- `hbar(canvas, { values, labels, color })`: horizontal bars for
  battery / level lists.
- `sankey(canvas, { flows, colors })`: flows between source and sink
  rails with proportional bands, used by `ha_energy`.

All helpers set `animation: false` (the renderer screenshots mid-frame
otherwise; the E6 spec forbids motion anyway).

---

## 8. Phosphor icons

Use bold for every icon, including small inline icons.
It is the only weight bundled widgets use and `spectra-widgets.css` imports.
Avoid `ph-fill`: it becomes solid blobs on Spectra 6.

Size icons with `--icon-*` tokens.

```html
<i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
```

compose.html loads the icon font. `@import` in `spectra-widgets.css` makes it
available inside each widget's shadow root.

Don't use the regular / thin / fill / duotone weights. Bold reads as a
single solid block on E6; the others either dither or disappear.

Choose icon colours by role:

- Countdown clock: `--accent-1` when the countdown is the main metric.
- List checkmark: `--accent-3` for positive.
- Warning triangle: `--accent-2` for warnings.

---

## 9. Compact mode (`@container` queries)

Spectra widgets shrink-to-fit via container queries on the cell. The
breakpoints that matter:

```css
@container (max-width: 360px) { /* compact */ }
@container (max-width: 240px) { /* tiny tile */ }
```

`spectra-widgets.css` trims content at these breakpoints:

- Chart legends collapse.
- Status grids hide P3+.
- Weather forecasts shrink to 3 cells.
- Lists show at most 4 rows.

Mirror those breakpoints in custom widget rules. New compact rules go
into `spectra-widgets.css` near the existing block so they stay
discoverable.

---

## 10. Anti-patterns to avoid

- `text-transform: uppercase` hardcoded. Use
  `var(--label-transform, uppercase)`. Editorial style sets it to
  `none`; hardcoding uppercase makes your widget's casing differ from
  the rest of the page.
- `--font-family` set with `var(--font-family, …)` fallback.
  Self-reference. Breaks every chart probe and every
  `var(--font-family)` read in the shadow root. Use a non-recursive
  fallback (`system-ui, sans-serif`) or don't override.
- Hardcoded stroke widths. Use `--stroke-1` (2px edge floor),
  `--stroke-2` (3px data strokes), `--stroke-3` (4px heavy emphasis),
  or `--edge-weight` for the widget shell border.
- Pure `#000` / `#fff`. True black/white ghosts on E6. Use
  `--text-primary` / `--bg`.
- Animations or transitions. E6 refreshes in seconds and ghosts;
  every widget is a still frame. The renderer screenshots
  `animations="disabled"` and the Spectra spec forbids motion.
- Reaching for `--accent-1` because you want red. Roles by
  position; the active theme provides the hue.
- A custom widget background or border that doesn't use the
  semantic tokens. Breaks theme contrast in dark themes.
- `color-mix()` for soft accent tints when an `--accent-N-soft`
  exists. Each accent has a `-soft` companion in every theme; use
  those directly.

---

## See also

- [`widgets.md`](widgets.md), the per-widget authoring contract
- [`widget-design-brief.md`](widget-design-brief.md), fill-in-the-blanks
  brief for new widgets
- [`widget-build-prompt.md`](https://github.com/dmellok/tesserae/blob/main/docs/widget-build-prompt.md), LLM prompt for
  scaffolding a widget end-to-end
- [`spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css),
  [`spectra-styles.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-styles.css),
  [`spectra-widgets.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css)
 , the design system source
