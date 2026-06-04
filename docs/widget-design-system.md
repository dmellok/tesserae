# Widget design system

The cross-widget rulebook. [`widgets.md`](widgets.md) is the
authoring contract (one widget, in isolation); this is the
"how widgets relate to each other on a panel" layer — what makes
58 separate widgets read as one design family instead of a patchwork.

The rules below codify what the **best current widgets already do**.
They're not aspirational — they're written down so the next widget
matches the family, and so a future-you doing a refactor pass knows
which existing widgets to align with vs lift up.

If you're building a new widget, read this AFTER
[`widgets.md`](widgets.md) (which covers the `render(shadow, ctx)`
contract and the colour-token layers) and BEFORE you start designing.
The [`widget-design-brief.md`](widget-design-brief.md) template
incorporates these rules into a fill-in-the-blanks shape.

---

## 1. Variant naming

Multi-direction widgets ship multiple visual layouts behind a single
`variant` cell option. The naming convention has stabilised:

### Canonical: 4 directions with single-letter prefix + number

For new widgets, use the **four-direction family + legacy**:

| value | direction | feel |
|---|---|---|
| `r1` | **Refined** | Bauhaus dark title bar, structured list/grid, soft `--wx-tint` washes + solid `--c-accent` anchor block. The "main" body. |
| `g2` | **Geometric** | Colour-block tiles, Archivo Black numerals, De Stijl rhythm |
| `s3` | **Swiss** | Hairline header, low-contrast type, newspaper / international-style |
| `d4` | **Data** | Stats forward — charts, sparklines, histograms, density |
| `legacy` | **Legacy** *(v0.16.21+)* | Quiet paper-and-ink card: charcoal header, hairline rules, no solid accent panels. Conservative fallback for users who prefer the pre-colour-pass look. Default stays `r1`. |

Reference: [`plugins/ha_battery`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_battery),
[`plugins/ha_locks`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_locks),
[`plugins/ha_todo`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_todo),
all weather widgets.

```json
{
  "name": "variant",
  "type": "select",
  "label": "Style",
  "default": "r1",
  "choices": [
    { "value": "r1",     "label": "1 · Bauhaus Refined" },
    { "value": "g2",     "label": "2 · Bauhaus Geometric" },
    { "value": "s3",     "label": "3 · Swiss / International" },
    { "value": "d4",     "label": "4 · Data forward" },
    { "value": "legacy", "label": "Legacy" }
  ]
}
```

### Grandfathered: 6 directions with widget-specific prefix

Older widgets ship six directions with a widget-keyed prefix:
`ha_climate` uses `c1`-`c6`, `ha_entities` uses `e1`-`e6`,
`ha_history` uses `h1`-`h6`, `ha_sensor` uses `s1`-`s6`. These work
and don't need rewriting, but **don't follow the pattern for new
widgets**. The four-direction family covers the visual archetypes
that matter; six directions invariably ends up with two or three
"barely different" ones that pad the picker.

### Grandfathered: 4 directions with widget-specific prefix

The github family follows the four-direction shape but with
widget-keyed prefixes: `github_repo` uses `re1-re4`, `github_actions`
uses `ci1-ci4`, `github_activity` uses `a1-a4`, `github_pr_queue`
uses `pr1-pr4`, `github_contributions` uses `co1-co4`. These map
position-for-position to canonical `r1/g2/s3/d4` (Refined / Geometric
/ Swiss / Data) — the prefix is the only difference. Grandfathered;
new widgets should prefer the canonical prefix.

### Per-widget layout pickers

Some widgets (e.g. `spotify_now_playing`) use layout-shape names
(`split / cover / minimal / vinyl / stack`) instead of design-direction
ids. That's fine when the variants describe **layout shapes** rather
than a design language family — they're not pretending to be the
same direction set as the HA / weather widgets. The dropdown label
("Style") stays the same so users get a consistent picker name across
the catalogue; the choice labels carry the per-widget specifics.

### Single-variant widgets are fine

Not every widget needs four directions. `clock_analog`, `picture_*`,
`webpage` are intentionally single-shape. Don't add a `variant`
option just to have one.

---

## 2. Title bar discipline

Every refined widget has a dark Bauhaus title strip. The strip must
land at **the same physical pixel height across every widget,
regardless of zoom level or cell size**, or panel-side dashboards
read as messy.

### The rule

Use the shared `--wb-bar-*` tokens from
[`static/style/widget-bauhaus.css`](https://github.com/dmellok/tesserae/blob/main/static/style/widget-bauhaus.css)
(or `widget-bauhaus-wx.css` for widgets in the weather/HA family):

```css
.your-title-bar {
  height: var(--wb-bar-h);          /* 36 physical px @ zoom 1 */
  padding: 0 var(--wb-bar-px);      /* 14 physical px @ zoom 1 */
  font-size: var(--wb-bar-fs);      /* 13 physical px @ zoom 1 */
  background: var(--wb-bar-bg, #1b1a16);
  color: var(--wb-bar-fg, #f1ece0);
}
.your-title-bar-mark { width: var(--wb-mark-sz); height: var(--wb-mark-sz); }
.your-title-bar-icon { font-size: var(--wb-bar-icon-sz); }
```

The tokens are pre-scaled against `--c-zoom` so the bar stays at
36 physical pixels at every zoom level. Don't redefine them per
widget.

### Two equally-good shapes

Pick whichever fits how your widget renders:

1. **Use the shared `.wb-bar` class** from `widget-bauhaus.css` directly.
   Zero extra CSS — link the stylesheet, write `<header class="wb-bar">`.
   Reference: [`plugins/news_reddit/client.js:90`](https://github.com/dmellok/tesserae/blob/main/plugins/news_reddit/client.js#L90).

2. **Roll your own class but consume the same vars.** Pinned by the
   shared tokens, but you control gap, layout, the right-side meta
   slot. Reference: [`plugins/github_actions/client.css:61`](https://github.com/dmellok/tesserae/blob/main/plugins/github_actions/client.css#L61)
   after the v0.14.3 fix.

### Anti-patterns to avoid

- `padding: clamp(...) clamp(...)` for the bar — the bar height
  becomes a function of cell size and clashes with neighbours at
  the same zoom. (This is what the v0.14.3 github bar fix was.)
- Hard-coding the bar background/foreground to specific values
  instead of using `var(--wb-bar-bg)` / `var(--wb-bar-fg)`. The
  shared tokens **invert against the body** (`var(--c-text)` /
  `var(--c-bg)`) so the bar reads dark-on-light in light themes
  (the original Bauhaus look) and light-on-dark in dark themes —
  always contrasts. (Changed in v0.16.13. Previously the tokens
  pinned a fixed Spectra dark; the inversion was the fix for
  refined widgets disappearing under dark themes.)
- Custom title-bar font tokens. Bar text is `var(--wx-mono)` or
  `var(--theme-font)` mono fallback; don't introduce a new family.

### Title-bar metadata

The right-aligned slot in the bar (count, timestamp, status pill)
uses the shared `.wb-bar-meta` class or `--wb-bar-meta` styling.
Treatment: paper at 70% opacity to read as quieter than the title.

---

## 3. Font cascade

The composer sets two CSS variables on every cell host:

```css
--theme-font:      <user's font pick>, system-ui, ...;
--theme-font-mono: <user's mono pick>, ui-monospace, ...;
```

### The rule

Widgets should respect the user's font pick. Concretely:

- **Lead with `var(--theme-font)`** in any `font-family:` declaration
  for body text:
  ```css
  body { font-family: var(--theme-font, "Inter"), sans-serif; }
  ```
- **Lead with `var(--theme-font-mono)`** for tabular / monospace text:
  ```css
  .number { font-family: var(--theme-font-mono, "JetBrains Mono"), ui-monospace, monospace; }
  ```
- **The decorative `--wx-*` role tokens already follow this rule** —
  `--wx-grotesk`, `--wx-black`, `--wx-mono`, `--wx-swiss` all lead
  with `var(--theme-font, ...)`. So widgets that paint from `--wx-*`
  inherit the picker correctly without thinking about it.

### Exception: distinct display weights

If a widget visually depends on a specific display weight that user
fonts won't reproduce (Archivo Black for hero numerals, Anton for
big-block typography), name the family first and use `--theme-font`
as the fallback:

```css
.hero-number {
  font-family: "Archivo Black", var(--theme-font, "Archivo"), sans-serif;
  font-weight: 900;
}
```

This way the design intent survives when the user's font is Inter
(no display weight) but the widget body still tracks the picker
where the display family is missing.

### Anti-patterns

- `font-family: system-ui` — breaks the user's font pick silently.
- `font-family: "Inter"` without `var(--theme-font)` — same.
- Inline `style="font-family: Helvetica"` — same again.

---

## 4. Colour discipline

The colour system has three layers; widgets paint from the upper two,
never the bottom one. [`widgets.md`](widgets.md#theme-tokens-primitives-and-the-semantic-layer)
covers this in depth; this section is the cross-widget reminder.

### Layer cheat sheet

| layer | tokens | purpose |
|---|---|---|
| **Theme primitives** | `--theme-bg`, `--theme-accent`, etc. | Theme authors only. Widgets **never** reference these. |
| **Semantic** | `--c-bg`, `--c-text`, `--c-accent`, `--c-ok`, `--c-warn`, `--c-danger`, `--c-info`, `--c-data-1..4` | Paint DOM from here. |
| **Decorative** | `--wx-paper`, `--wx-ink`, `--wx-red`, `--wx-grotesk` (font), etc. | Widgets in the weather / HA / sky family that share the Bauhaus chromatic system. |

### Categorical vs status

**The rule that catches the most bugs.** When you reach for a colour:

- "I need N distinguishable colours for stat blocks / chart series /
  day columns / battery levels rendered as decorative blocks" →
  **categorical**, use `--c-data-1..4`.
- "This value indicates the user should pay attention / it's broken
  / it's safe / it's informational" → **status**, use `--c-ok` /
  `--c-warn` / `--c-danger` / `--c-info`.

A sunny day is **categorical** (`--c-data-1`). A severe-storm warning
is **status** (`--c-danger`). They feel similar but they're not.

Reaching for `--c-danger` because you want red breaks the rule: in
a theme where `danger` is a loud alarm and `accent` is muted, your
sunny day reads as an emergency. Reach for `--c-data-*` for
decorative, save status tokens for genuine status.

### Decorative chromatic family

The weather / HA / sky widgets share a decorative palette via
`widget-bauhaus-wx.css`:

| token | role |
|---|---|
| `--wx-paper`, `--wx-paper-2`, `--wx-paper-3` | body backgrounds (flow through `--c-bg`) |
| `--wx-ink`, `--wx-ink-60` | body text (flow through `--c-text`) |
| `--wx-red`, `--wx-blue`, `--wx-yellow`, `--wx-green` | decorative colour chips. **All four are theme-aware** (changed v0.16.13) — `red` flows through `--c-accent`, `blue` through `--c-data-2`, `yellow` through `--c-warn`, `green` through `--c-data-3`. None pinned to Spectra hex any more. |
| `--wx-red-fg`, etc. | text colour for use ON each chip — flows through `--c-bg` |
| `--wx-red-t`, etc. | tinted variants (chip-fill at 22% over paper) |
| `--wx-tint`, `--wx-tint-strong`, `--wx-tint-blue`, `--wx-tint-green`, `--wx-tint-yellow` | **section-background washes** (added v0.16.15). `color-mix` of the underlying chip and paper at 14% (or 26% for `-strong`). Paint whole panels in a soft accent wash without the brightness of solid `var(--c-accent)`. R1 variants now use `--wx-tint` for hero text panels paired with a solid `--c-accent` block for the visual anchor. |

If your widget belongs to this family, link
`widget-bauhaus-wx.css` and paint from `--wx-*` — themes will
retint everything cleanly.

If your widget doesn't (a finance widget, a clock, a webpage), paint
from `--c-*` directly. Don't import the wx tokens just to get colour
names you like.

### E-ink ceiling

Spectra 6 panels render **4 distinct hues** reliably. Don't design a
6-way categorical split — the panel can't resolve it. For tonal
emphasis within one hue, use `--c-accent-soft`, but it dithers to a
stipple on the panel, so reserve it for large fills, never small text.

---

## 5. CSS class naming

Class names should be predictable when you're reading multiple
widgets side-by-side.

### Per-variant prefix

For multi-variant widgets, prefix every variant-specific class with
the variant value:

```css
.r1-header { ... }      /* refined variant's header */
.r1-list-row { ... }
.g2-tile { ... }        /* geometric variant's tile */
.s3-rule { ... }        /* swiss variant's hairline rule */
.d4-stat-block { ... }  /* data variant's hero stat */
```

This makes variant-specific styling obvious in the CSS file and
prevents class collisions when multiple variants share a `client.css`.

### Cross-variant shared classes

Classes used across multiple variants of the same widget get an
unprefixed widget-keyed name:

```css
.todo-empty { ... }      /* used by every ha_todo variant */
.todo-due-overdue { ... }
```

### Shared baseline classes

Classes defined by the shared stylesheets (`widget-bauhaus.css`,
`widget-bauhaus-wx.css`) own their names: `.wb-bar`, `.wb-mark`,
`.wb-bar-icon`, `.wb-bar-meta`, `.wb-empty`, `.wb-error`,
`.wx-art`, `.wx-tnum`, `.wx-header-dark`. Don't reuse these prefixes
for widget-local classes.

### Inline styles vs `client.css`

Either is fine, with a sensible split:

- **Reusable structural shapes** (the cell's root grid, the empty/
  error states, container queries) → `client.css`.
- **Variant-specific body styling** (a g2 tile's exact padding +
  font + colour combination) → **inline `style="..."` in the
  template literal** is OK. The HA / weather widget families do
  this and it keeps each variant's render function self-contained.

What's NOT OK: scattering style decisions between inline + CSS
file for the same element. Pick one place per concern.

---

## 6. When to link `widget-bauhaus.css` / `widget-bauhaus-wx.css`

### `widget-bauhaus.css` — almost always link it

Any widget with a refined title bar links this:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/style/widget-bauhaus.css">
  <link rel="stylesheet" href="/plugins/<id>/client.css">
  ...`;
```

It provides:
- The `--wb-bar-*` tokens on `:host`
- The `.wb-bar`, `.wb-mark`, `.wb-bar-icon`, `.wb-bar-meta` classes
- The `.wb-empty`, `.wb-empty-primary`, `.wb-empty-secondary` shapes
- The `.wb-root.is-error` / `.wb-error` shapes

Widgets that *don't* link it: `clock_analog`, `picture_*`, `webpage`,
`spotify_album_art` — anything with no title bar and no shared
error / empty shape. Everything else links it.

### `widget-bauhaus-wx.css` — link when in the wx family

Link this **only** if your widget paints from the `--wx-*`
decorative tokens (the weather / sky / HA family). It introduces:

- The `--wx-paper`, `--wx-ink`, `--wx-red`, etc. decorative tokens
- The `--wx-grotesk`, `--wx-black`, `--wx-geo`, `--wx-mono`,
  `--wx-swiss` font role tokens
- The `.wx-header-dark`, `.wx-art`, `.wx-tnum` shapes
- The `--wb-bar-bg` / `--wb-bar-fg` definitions (invert against the
  body via `--c-text` / `--c-bg` so the bar always contrasts; changed
  v0.16.13 from a pinned Spectra dark)
- The `--wx-tint*` section-wash tokens (added v0.16.15)

A finance widget linking `widget-bauhaus-wx.css` would inherit a
weather aesthetic it doesn't need. Don't import for the colour-name
convenience.

---

## 7. Reference implementations

Widgets that nail the rulebook — read these first when building
something similar:

| Pattern | Reference |
|---|---|
| Refined dark bar via `.wb-bar` | [`plugins/news_reddit`](https://github.com/dmellok/tesserae/tree/main/plugins/news_reddit) |
| Four-direction variant dispatcher | [`plugins/ha_battery`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_battery), [`plugins/ha_locks`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_locks) |
| Weather / wx decorative family | [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now), [`plugins/weather_forecast`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_forecast) |
| HA service-call widget | [`plugins/ha_todo`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_todo) |
| Chart.js + tone mapping | [`plugins/weather_hourly`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_hourly) |
| Categorical-vs-status colour discipline | [`plugins/ha_battery`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_battery) (statusFill uses --c-* semantic), [`plugins/weather_air_quality`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_air_quality) (UV/AQI bands as status) |
| `--c-zoom`-aware fixed-size elements | [`static/style/widget-bauhaus.css`](https://github.com/dmellok/tesserae/blob/main/static/style/widget-bauhaus.css) `--wb-bar-h` definition |
| Hero icon + stats grid + sun row | [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now) |

---

## 8. Quick lint — does my widget match?

A 30-second checklist when you finish a widget:

- [ ] Title bar uses `var(--wb-bar-h)` (not `clamp(...)`)
- [ ] Title bar background is `var(--wb-bar-bg, #1b1a16)` (not raw `var(--c-text)` — the `--wb-bar-bg` token flows through `--c-text` already and adds the dark-theme inversion behaviour)
- [ ] Title bar foreground is `var(--wb-bar-fg, #f1ece0)` (not raw `var(--c-bg)` — same reasoning as above)
- [ ] `widget-bauhaus.css` is linked (if there's a title bar)
- [ ] Body fonts lead with `var(--theme-font)` or paint from `--wx-*`
- [ ] Decorative colour uses `--c-data-*` or `--wx-*`, status uses `--c-ok/warn/danger/info`
- [ ] No raw `--theme-*` token references (semantic-token enforce test will catch this)
- [ ] No `ph-fill` for hero icons (use `ph-bold`)
- [ ] Variant option (if used) names directions `r1/g2/s3/d4`
- [ ] Class names per variant are prefixed: `.r1-*`, `.g2-*`, etc.

If your widget hits all of those, it'll sit cleanly next to every
other widget on a dashboard.
