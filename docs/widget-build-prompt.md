# Widget build prompt

Hand this to an LLM (Claude Design / GPT / etc.) when you want a new
widget designed end-to-end against the current Tesserae design system.
Output is a filled-in brief that
[`widget-design-brief.md`](widget-design-brief.md) defines.

---

## Who you are

You're designing a widget for **Tesserae**, a self-hosted e-ink
dashboard system that renders bauhaus-styled "tiles" onto colour
e-paper panels (Spectra 6, 600×448 → 1600×1200). Each widget is a
self-contained plugin: a manifest, a `client.js` that renders into a
Shadow DOM, an optional `server.py` for data fetching, and a `tests/`
folder.

Tesserae uses **Spectra**, a token-themed design system with two
orthogonal axes:

- **`data-theme`**, colour only. 19 themes shipped (light, dark,
  high-contrast, sepia, nord, cool-gray, three movement themes, and
  ten base16 palettes). Set on `<body>`, cascades into every widget.
- **`data-style`**, typography, scale, density, shape (never colour).
  9 styles shipped (standard, display, editorial, mono, elegant,
  condensed, plus three movement styles).

A widget designed under Spectra renders correctly under any theme ×
style combination. The user picks the pair per dashboard; your widget
doesn't need to know which they're using, it paints from semantic
tokens that the active theme + style flow through.

---

## Read first

Three docs are the canonical contract. Read each before drafting:

1. [`docs/widgets.md`](widgets.md), the build contract: plugin folder
   layout, `client.js` / `server.py` signatures, the Spectra token
   layers (primitive / semantic / style-tunable), container queries,
   Phosphor icon vocabulary, e-ink considerations.
2. [`docs/widget-design-system.md`](widget-design-system.md), the
   cross-widget rulebook: archetypes, the two axes, title-bar
   discipline, colour discipline, chart helpers, anti-patterns.
3. [`docs/widget-design-brief.md`](widget-design-brief.md), the
   output template you'll fill in. **One filled brief per widget,
   sections 0 through the end.**
4. Pick two existing widgets that share your information shape and
   read their `client.js` as reference. Good examples by archetype:

   - **Status archetype**, [`plugins/f1_next`](../plugins/f1_next),
     [`plugins/sky_moon`](../plugins/sky_moon)
   - **List archetype**, [`plugins/news_hacker_news`](../plugins/news_hacker_news),
     [`plugins/ha_entities`](../plugins/ha_entities)
   - **Chart archetype**, [`plugins/weather_hourly`](../plugins/weather_hourly),
     [`plugins/ha_history`](../plugins/ha_history)
   - **Stat archetype**, [`plugins/finance_stock`](../plugins/finance_stock),
     [`plugins/weather_now`](../plugins/weather_now)
   - **Calendar archetype**, [`plugins/calendar_day`](../plugins/calendar_day),
     [`plugins/calendar_week`](../plugins/calendar_week)
   - **Image archetype**, [`plugins/spotify_now_playing`](../plugins/spotify_now_playing),
     [`plugins/ha_camera`](../plugins/ha_camera)

---

## Critical conventions

### 1. Pick an archetype

Every widget renders one of seven body archetypes (`.stat-body`,
`.list-body`, `.chart-body`, `.status-body`, `.cal-body`, `.wx-body`,
`.img-body`). Pick the one whose information shape matches yours. Don't
roll a custom body layout, the archetypes carry the font-size cascade
and gap rhythm that make widgets read as a family.

Two metric blocks side by side? Two `.stat-body` widgets, not one
custom. Six rows of "thing → value"? `.list-body` with `.list-row` for
each. A trend over time? `.chart-body` with `<canvas>`.

### 2. Paint from semantic tokens

Read colour from the Spectra semantic layer; **never hardcode hex**.
The active theme provides whatever hue means "alerts" / "warnings" /
"positive" / etc.

```html
<i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
<span class="pill" style="background:var(--accent-3)">PASSING</span>
<span style="color:var(--text-muted)">caption</span>
```

The six accent slots have fixed **roles by position**:

| Slot | Role |
|---|---|
| `--accent-1` | alerts / peaks / current |
| `--accent-2` | warnings / capacity / "winner" |
| `--accent-3` | positive / "up" / passing |
| `--accent-4` | primary / today / live |
| `--accent-5` | secondary series |
| `--accent-6` | third category |

Reach by role, not by colour. "I want red" is the wrong instinct -
pick the slot whose meaning is right; the theme gives you the hue.

**Exception, the extended palette opt-in.** Scenic / decorative
widgets (weather cards with sunset gradients, anything atmospheric)
can declare `"design": {"palette": "extended"}` in `plugin.json` and
use arbitrary CSS colours (gradients, layered shapes, soft shadows).
The renderer's Floyd-Steinberg dither approximates them on the panel
palette. Only opt in if your widget genuinely *needs* that surface;
strict tokens are the right default and read cleaner on BW panels.
Typography + spacing tokens stay mandatory either way.

Reference:
[`plugins/weather_now_scenic`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now_scenic).

### 3. Icons are bold Phosphor

Use Phosphor at the **`bold`** weight for everything. Other weights
(thin / fill / duotone) dither on E6 or disappear at small sizes.

```html
<i class="ph-bold ph-flag-checkered" style="color:var(--accent-1)"></i>
```

Bold Phosphor is loaded automatically by Spectra widgets that link
`spectra-widgets.css`. Icon names: [phosphoricons.com](https://phosphoricons.com).
Sizes via the `--icon-sm/-md/-lg` tokens.

### 4. Charts via the Spectra Chart.js wrapper

Don't talk to Chart.js directly. Use the wrapper:

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

`tokens()` resolves the live theme palette + font from the cell's
cascade. The chart re-renders on theme/style change because the
composer remounts cells.

### 5. Container queries for compactness

Spectra widgets shrink at `@container (max-width: 360px)` (compact)
and `@container (max-width: 240px)` (tiny tile). Hide secondary
content at compact, leave only the hero metric + label at tiny.

```css
@container (max-width: 360px) {
  .my-secondary { display: none; }
}
```

### 6. Anti-patterns

- **`text-transform: uppercase` hardcoded.** Use
  `var(--label-transform, uppercase)`, Editorial style sets it to
  `none`.
- **Hardcoded font family.** Inherit via the `.w` shell; widgets get
  `--font-family` automatically.
- **`--font-family: 'foo', var(--font-family, …)` inline.**
  Self-reference; CSS invalidates the cascade. Use a non-recursive
  fallback (`system-ui, sans-serif`).
- **Animations / transitions.** No motion on e-ink, ever.
- **Pure `#000` / `#fff`.** Ghosts on E6. Use `--text-primary` / `--bg`.
- **A `variant` cell option for "visual directions".** That model is
  gone. Style is set at the page level via `data-style`; widgets render
  one shape.

---

## What to design

The user will tell you what widget they want. Your job is to fill in
the brief: archetype choice, information shape, data source (existing
plugin? new server.py?), the chosen Phosphor icons, the accent slot
each piece of data binds to, sample data shape, and any edge cases
(loading state, empty state, error state).

If the user hasn't said which archetype, propose one in section 1 of
the brief and explain why. If two archetypes could work, name both and
recommend.

If the data source needs an API key or upstream service, name it
explicitly (e.g. "Open-Meteo, no key required" / "GitHub PAT in
Plugins → GitHub Core" / "Home Assistant Core plugin"). The user
configures those out-of-band.

---

## Output format

One filled-in brief per widget, following
[`widget-design-brief.md`](widget-design-brief.md) section by section.
Include sample markup in section 3 (Markup sketch) and a sample data
payload in section 8 (Server contract). When you finish the brief,
note any open questions in a "Questions" subsection at the end. Don't
guess on data structure, API endpoints, or naming if the user's spec
is ambiguous.

If the widget is part of a family (`f1_*`, `weather_*`, `news_*`),
mention it explicitly in section 0 (Identity) so the build path
follows the family's existing shared-helper patterns (e.g. `f1_core`
exposes `getCircuit()` + `trackSvg()` for every F1 widget).

---

## Hard constraints

These consistently bite if ignored. Honour them without comment in the
brief, the user knows them already.

- **Static panels.** No animation, no transitions, no hover effects.
  E6 refreshes in seconds and ghosts.
- **No fine gradients.** They dither badly. Use solid fills + soft
  tints (`--accent-*-soft`) for everything.
- **No hairlines.** Minimum stroke 2px; data strokes (chart bars, axis
  rules) 3px+. Use the `--stroke-1/2/3` tokens.
- **Near-black, never pure.** True `#000` ghosts; use `--text-primary`.
- **Phosphor bold weight only.** Thin / regular / fill / duotone don't
  read on E6.
- **One archetype per widget.** Composability across the panel comes
  from picking the right archetype, not building a custom shell.
