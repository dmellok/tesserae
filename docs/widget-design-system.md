# Widget design system

The cross-widget rulebook. [`widgets.md`](widgets.md) is the authoring
contract (one widget, in isolation); this is the "how widgets relate to
each other on a panel" layer. What makes 58 separate widgets read as one
design family.

If you're building a new widget, read [`widgets.md`](widgets.md) first
(the `render(shadow, ctx)` contract and the token layers), then this for
the cross-widget conventions, then use
[`widget-design-brief.md`](widget-design-brief.md) to design your widget
shape.

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

Pick the archetype that matches your widget's information shape. Two
metric blocks side by side? `stat-body` × 2. Six rows of "thing → value"?
`list-body`. A trend over time? `chart-body`.

Don't roll your own body layout from scratch. The archetypes carry the
font-size cascade, gap rhythm, and zoom-aware sizing that make widgets
read consistently when placed next to each other on a panel.

Full class reference + markup in [`spectra-widgets.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css).

---

## 2. Two orthogonal axes

Spectra has two independent axes, set on `<body>` and overridable on any
cell:

| Axis | Attribute | Controls | Shipped |
|---|---|---|---|
| **Theme** | `data-theme` | colour only (surfaces, text, accents) | 7 Light + 2 Dark + 3 Movement + 15 Vivid + 14 Gradient = 41 |
| **Style** | `data-style` | typography / scale / density / shape (never colour) | 6 neutral + 3 movement = 9 |

Any theme composes with any style. The user picks a `(theme, style)`
pair at page level; either can be overridden per cell.

This is the contract widgets opt into: don't hardcode font, don't
hardcode edge weight, don't hardcode text-transform, don't hardcode pill
shape. The two axes flow through CSS custom properties so when the user
flips Mono → Display, your widget repaints automatically.

Visual coverage: spin up the dev server, hit `/_test/matrix` to see one
widget across the full theme × style grid (171 combos).

---

## 3. Token layers

Three layers. Widgets paint from the upper two; never from primitives.

| Layer | Tokens | Purpose |
|---|---|---|
| **Primitives** | `--fs-*` (type), `--space-*` (spacing), `--fw-*` (weights), `--lh-*` (line height), `--stroke-1..3`, `--radius-0..2`, `--icon-sm/-md/-lg` | Theme-independent raw values on `:root`. Don't redefine. |
| **Semantic** | `--bg`, `--surface`, `--surface-sunken`, `--text-primary/-secondary/-muted`, `--edge`, `--accent-1..6` (+ `-soft`), `--on-accent`, `--icon`, `--font-family` | Set per-theme on `[data-theme]`. **Paint your widget from these.** |
| **Style-tunable** | `--edge-weight`, `--zebra-bg`, `--list-pad-y`, `--list-gap`, `--pill-radius`, `--title-marker`, `--title-rule-w`, `--row-rule-w`, `--label-transform` | Defaults on `:root` (the "standard" look); styles override on `[data-style]`. **Consume via `var(--name, fallback)`** so an unstyled widget still renders. |

Source: [`spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css).

---

## 4. Title bar discipline

Every Spectra widget with a title uses the same `.w-title` element. Two
non-negotiable behaviours:

1. **Zoom-locked size.** The title scales with the panel base, not with
   the cell's `--c-zoom`. When the user dials up a cell's content zoom,
   the title stays the same physical size; the body grows. This keeps
   panel-side dashboards readable as a strip of consistent headers
   instead of one giant title floating above a small chart.

2. **Label casing follows `--label-transform`.** Editorial style drops
   to sentence case; everything else stays uppercase. `.w-title h3`
   already does this; if you build a custom title-adjacent label,
   write `text-transform: var(--label-transform, uppercase)`.

The optional bits:

- **Lead icon**, `<i class="ph-bold ph-<name>">` before the `<h3>`,
  tinted with one of the `--accent-*` tokens. Echoes the widget's role
  colour (`accent-1` for alerts, `accent-3` for positive, etc.).
- **Right meta**, `<span class="w-title-meta">` for a small count /
  timestamp / status pill.
- **Eyebrow marker**, set `--title-marker: block` to show a coloured
  tick before the title. Styles like Mono and Bauhaus use this; widgets
  inherit when the style is active.
- **Title rule**, `--title-rule-w` paints an accent line under the
  title bar. Display style sets it to a 3px accent-1 rule.

Anti-patterns:

- Hardcoded `text-transform: uppercase`, breaks Editorial.
- A custom title-bar background, Spectra widgets are flat; the
  archetypes don't ship a tinted title strip.
- Hardcoded font-family for the title, the style sets the family;
  widgets inherit.

---

## 5. Font cascade

The active style sets `--font-family` on the body. Widgets that need to
read it explicitly do so via `var(--font-family)`. The Spectra widget
shell already does this via `.w { font-family: var(--font-family) }`,
so most widgets get the right font for free.

Overrides flow top-down:

```
page picker  → body inline --font-family
cell picker  → cell inline --font-family
style (active data-style) → body / cell --font-family
:root default → Helvetica Neue stack
```

**Do not** set `--font-family` inline as `'<font>', var(--font-family, …)`.
That's a self-reference. CSS treats it as invalid-at-computed-value-time,
the property reverts to the guaranteed-invalid sentinel, and every
descendant `var(--font-family, …)` falls through to its fallback, the
chart helpers see this as "the font cascade is broken" and chart text
ends up in Helvetica regardless of style. This bug burned us in v0.19.5;
see the commit. Set inline `--font-family: '<font>', system-ui, sans-serif`
when overriding, or leave the cascade alone.

Charts read fonts the same way via
[`tokens()`](https://github.com/dmellok/tesserae/blob/main/static/spectra-chart.js):

```js
import { tokens } from "../../static/spectra-chart.js";
const t = tokens(shadow.host);
// t.fontFamily is the resolved style font
```

---

## 6. Colour discipline

The six accent slots have **fixed roles by position**, not by hue. Reach
for the slot whose meaning matches; the active theme provides whatever
hue carries that meaning.

| Slot | Role | Used for |
|---|---|---|
| `--accent-1` | alerts / peaks / "now" | error pills, current-hour highlight, urgent state |
| `--accent-2` | warnings / capacity / "winner" | yellow-flag warnings, near-full battery, trophy gold |
| `--accent-3` | positive / "up" | success state, uptrend, online |
| `--accent-4` | primary / "today" / live | the main series in a chart, today's column, "live" tag |
| `--accent-5` | secondary series | second chart series, comparison data |
| `--accent-6` | third category | third series, supplementary tags |

In the light theme the colours are roughly: terracotta, ochre, moss,
teal, slate-blue, plum. In Dracula they're red, peach, mint, lavender,
cyan, pink. The hexes change; the roles don't.

Every accent has a `-soft` companion (`--accent-1-soft` etc.) for
low-saturation background tints, pill backgrounds, list-row highlights,
chart area fills.

`--on-accent` is the legible text colour to draw on an accent fill (e.g.
the text inside a `<span class="pill">`).

**Anti-pattern:** reaching for `--accent-1` because you want red.
Bauhaus theme makes accent-1 red AND accent-6 red (both = primary red),
but accent-3 is blue. Always reach by role.

### Extended palette opt-in

The accent-by-role discipline above is the default and the right answer
for ~95% of widgets. Some widgets, scenic / decorative ones, weather
cards with sunset gradients, anything visually atmospheric, want a
richer surface than the six accent slots can express. For those, the
manifest has an explicit opt-in:

```json
{
  "design": {
    "palette": "extended"
  }
}
```

With `"extended"` declared, the widget can use any CSS colour,
gradient, or shadow. The renderer's Floyd-Steinberg dither pass
approximates those colours on the panel's actual palette; the browser
preview shows the literal CSS, the panel render shows the dithered
approximation.

What stays the same:

- Typography + spacing tokens (`--fs-display`, `--space-4`, etc.) are
  still mandatory. The opt-in covers colour only.
- The capability layer is unaffected; `requires:` declarations still
  apply.

What changes:

- The widget no longer reads as "this picks an accent role and the
  theme decides the hue". Themes don't influence an extended palette,
  the widget owns its colour story.
- The dither tradeoff: soft gradients and atmospheric scenery dither
  beautifully; sharp text-on-gradient or fine details read worse. The
  reviewer's question becomes "does the dithered output read clearly
  at the target panel resolution?" rather than "did they use the
  right token?".
- BW + 3-colour panels degrade harder than 7-colour Spectra. Extended
  widgets are at their best on the colour panels; consider whether
  yours has a meaningful fallback for low-colour hardware.

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

`tokens(shadow.host)` resolves the live `--accent-*`, `--surface*`,
`--text-*`, `--font-family` from the cell's cascade via a hidden probe
element. Pass it to a chart helper and the chart's bars / line / axis
text / grid lines bind to the active theme, flip the theme and the
chart repaints to match (composer remounts cells on theme/style change
so the canvas re-draws, since Chart.js bakes colour into the canvas).

Existing helpers:

- `sparkline(canvas, values, color)`, no axes, no legend, area fill,
  used by finance, weather, energy.
- `barChart(canvas, { values, labels, color, highlightIdx, … })`, bars
  with axis labels.
- `lineChart(canvas, { values, labels, fill, … })`, line trace with
  axis labels, sparkline-styled area fill.
- `hbar(canvas, { values, labels, color })`, horizontal bars for
  battery / level lists.

All helpers set `animation: false` (the renderer screenshots mid-frame
otherwise; the E6 spec forbids motion anyway).

---

## 8. Phosphor icons

Bold weight for prominent icons (stats, hero glyphs, title-bar lead
icons) — this is the default and what most widgets use. Regular weight
for small inline icons that flow alongside text (e.g. a `map-pin`
next to a place label). Duotone for accent moments (sun-horizon,
moon-stars). Avoid `ph-fill` — it quantises into solid blobs on
Spectra 6. Sizes always come from the `--icon-*` tokens.

```html
<i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
```

The icon font is loaded at document level by compose.html and
`@import`ed inside every Spectra widget's shadow root (via
`spectra-widgets.css`) so the glyphs render inside shadow scope.

Don't use the regular / thin / fill / duotone weights, bold reads as a
single confident block on E6, the others either dither or disappear.

Pair icons with their colour role: a clock icon next to a countdown
gets `--accent-1` if the countdown is the headline metric; a checkmark
in a list cell uses `--accent-3` (positive); a warning triangle uses
`--accent-2` (warnings).

---

## 9. Compact mode (`@container` queries)

Spectra widgets shrink-to-fit via container queries on the cell. The
breakpoint that matters:

```css
@container (max-width: 360px) { /* compact */ }
@container (max-width: 240px) { /* tiny tile */ }
```

`spectra-widgets.css` already trims secondary content at these
breakpoints, chart legends collapse, status grids hide P3+, weather
forecast trims to 3 cells, list rows cap at 4.

Mirror those breakpoints in custom widget rules. New compact rules go
into `spectra-widgets.css` near the existing block so they stay
discoverable.

---

## 10. Anti-patterns to avoid

A consolidated list of things that quietly break the design system:

- **`text-transform: uppercase` hardcoded.** Use
  `var(--label-transform, uppercase)`. Editorial style sets it to
  `none`; your widget will scream at every other widget on the page.
- **`--font-family` set with `var(--font-family, …)` fallback.**
  Self-reference. Breaks every chart probe and every
  `var(--font-family)` read in the shadow root. Use a non-recursive
  fallback (`system-ui, sans-serif`) or don't override.
- **Hardcoded stroke widths.** Use `--stroke-1` (2px edge floor),
  `--stroke-2` (3px data strokes), `--stroke-3` (4px heavy emphasis),
  or `--edge-weight` for the widget shell border.
- **Pure `#000` / `#fff`.** True black/white ghosts on E6. Use
  `--text-primary` / `--bg`.
- **Animations or transitions.** E6 refreshes in seconds and ghosts;
  every widget is a still frame. The renderer screenshots
  `animations="disabled"` and the Spectra spec forbids motion.
- **Reaching for `--accent-1` because you want red.** Roles by
  position; the active theme provides the hue.
- **A custom widget background or border that doesn't use the
  semantic tokens.** Breaks theme contrast in dark themes.
- **`color-mix()` for soft accent tints when an `--accent-N-soft`
  exists.** Each accent has a `-soft` companion in every theme; use
  those directly.

---

## See also

- [`widgets.md`](widgets.md), the per-widget authoring contract
- [`widget-design-brief.md`](widget-design-brief.md), fill-in-the-blanks
  brief for new widgets
- [`widget-build-prompt.md`](widget-build-prompt.md), LLM prompt for
  scaffolding a widget end-to-end
- [`spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css),
  [`spectra-styles.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-styles.css),
  [`spectra-widgets.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css)
 , the design system source
