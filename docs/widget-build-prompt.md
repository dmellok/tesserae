# Widget build prompt — current batch

Hand this to Claude Design. It outlines the widgets to design and the
recent design-language change. Output is one filled-in brief per
widget, using the template in [`widget-design-brief.md`](widget-design-brief.md).

---

## Who you are

You're designing widgets for **Tesserae** — an e-ink dashboard system.
Each widget is a self-contained plugin: a manifest, a `client.js` that
renders into a Shadow DOM, a `client.css`, and an optional `server.py`
for data fetching. The output of your design work goes back to Claude
Code, which builds the widget files directly from your brief.

## Read first

Three docs are the canonical contract. Read each before drafting:

1. [`docs/widgets.md`](widgets.md) — the build contract: plugin folder
   layout, `client.js` / `server.py` signatures, the 12 theme palette
   tokens, container queries, Phosphor icon vocabulary, e-ink
   considerations.
2. [`docs/widget-design-brief.md`](widget-design-brief.md) — the
   output template you'll fill in. **One filled brief per widget,
   sections 0 through 12.**
3. The three shipped widgets — [`plugins/weather_now`](../plugins/weather_now),
   [`plugins/weather_hourly`](../plugins/weather_hourly),
   [`plugins/weather_forecast`](../plugins/weather_forecast) — as
   reference for the patterns (WMO-code → icon lookup, condition-tone
   tinting, Chart.js loader memoisation, server-side disk-cache).

---

## Critical: icons are big and bold (and not filled)

Use Phosphor at the **`bold`** weight for any icon that should "read
big" — hero condition icons, day icons, podium markers, race-status
icons. **Do not use `ph-fill`.** Solid filled icons read as blobs at
size and quantise badly on Spectra 6. Bold-outline at large sizes
reads as a confident graphic.

Inline icons (small, flowing with text) stay at regular weight. The
distinction:

* **Bold, big** (clamp ~32-160 px depending on cell) — the visual
  anchor of a section. One or two per widget at most.
* **Regular, small** (~1em) — accompanying inline icons next to
  labels, stat values, table rows.

Duotone is fine for special two-tone accent moments (sun-horizon,
moon-stars). Avoid it for the primary identity icon.

Browse [phosphoricons.com](https://phosphoricons.com) — every icon
is available in all six weights.

---

## Custom colours — escape hatch

The default rule "use theme tokens only, no hex" has a carve-out:
when the data has an **inherent visual identity that the user expects
to see**, hard-code the hex. Examples:

* F1 team colours (Ferrari red, Mercedes teal, Red Bull dark-blue)
* Brand logos / service indicators
* Country flag colours on race countdowns
* Podium gold/silver/bronze (where appropriate — sometimes
  `warn` / `fgSoft` / `accent` reads better)

Bar to clear: the colour is **part of the data**, not a design
choice. Document each custom colour in the brief's notes section
with the data field it's tied to.

Fall back to a theme token (`surface2` or `accent`) for unknown
values so the widget never produces a blank square. For dark themes,
plan a fallback variant of each brand colour if the original reads
poorly against dark surfaces.

See `docs/widgets.md` → "Custom colours — escape hatch" for the full
contract.

---

## Plugin static assets

Widgets can ship arbitrary static files (SVGs preferred): drop them
under `plugins/<id>/static/`, reference at `/plugins/<id>/static/...`.

Useful for:

* **Race circuit outlines** — vector SVG of each F1 track
* **Country flags** — small SVG flag per ISO code
* **Service logos** — when no Phosphor equivalent

Rules:

* **Phosphor first** — only ship a custom asset if there's no
  reasonable Phosphor icon. Custom SVGs are for things the icon font
  can't represent (circuit outlines, flags, team logos).
* **SVG preferred over raster** — scales, themes via `currentColor`
  if appropriate.
* **Keep files small** (< 10 KB each typically).

In the brief, list every asset you intend to ship in section 12
(Notes), with file path + purpose + approximate size.

See `docs/widgets.md` → "Plugin static assets" for examples.

---

## Critical: no-borders design language

The theme system was reworked. **Drawn borders are out.** Card shapes
come from `bg` vs `surface` contrast only — the way Notion / Linear /
iOS Notes handle it.

What this means in practice:

- Don't use `border:` / `border-top:` / `border-bottom:` on any element
  that's part of the visual layout. Browser-default form/input borders
  are fine.
- Separate sections with **padding + margin**, not lines.
- For emphasis (e.g. "today" in a 5-day grid), use `--theme-surface2`
  background, not an accent border.
- The `divider` token still exists but its use is restricted to chart
  axes and grid lines — **not** card borders. If you have a tone-rule
  that maps anything to `divider`, you're probably trying to draw a
  line. Stop.

Palette structure (each theme is tuned for this layering):

| token       | role                                                          |
|-------------|---------------------------------------------------------------|
| `bg`        | outer page background — slightly tinted, gives theme identity |
| `surface`   | card background — brighter than bg on light themes, brighter than bg on darks too. Cards "raise" from the page via lightness contrast. |
| `surface2`  | emphasised card background — darker than surface on lights, brighter than surface on darks. Used for "today" / "current" / "leader" |
| `fg`        | primary text                                                  |
| `fgSoft`    | secondary text, sub-values                                    |
| `muted`     | labels, uppercase metadata                                    |
| `accent`    | brand highlight — icons, primary fills, chart strokes         |
| `accentSoft`| accent fill at low contrast — chart areas, pills              |
| `ok` / `warn` / `danger` | semantic status                                 |

Read the [`plugins/themes_core/plugin.json`](../plugins/themes_core/plugin.json)
file to see the 9 themes you're designing against.

---

## Widgets to design

### Batch A — rebuild the weather suite under the new design language

The three weather widgets exist but were designed in the previous
border-heavy era. Same data sources, same supported sizes, **new
visual treatment only**. Brief each one as if greenfield — Claude
Code will replace the existing CSS/client.js wholesale.

- **`weather_now`** — current conditions: place label, condition icon,
  big temp, feels-like / humidity / wind / UV, sunrise/sunset.
- **`weather_hourly`** — Chart.js line of next 12/24/48 hours of
  temperature, plus a rain-probability strip at md/lg.
- **`weather_forecast`** — 5-day forecast as columns: day name,
  condition icon, high/low, rain %.

Reuse the established conventions: WMO-code → Phosphor icon mapping,
condition-tone tinting (clear→warn, partly cloudy→accent, rain→accent,
storms→danger), `ctx.theme.accent` for Chart.js stroke colour, lazy
Chart.js loader from `/static/vendor/chart.umd.min.js`.

### Batch B — new widgets

#### 5. `todo` — checklist

A simple todo list that paints recent items. For v1, back it with a
local JSON file (`data/plugins/todo/items.json`) — items get added /
toggled / removed via a small admin sub-page at `/plugins/todo` (the
plugin manifest's `admin` blueprint, like other admin-equipped
plugins).

Cell options:
- List name (string, default "Inbox")
- Max items shown (int, default 6)
- Show completed (boolean, default false)

Layout: tile per item — checkbox icon (`ph-square` / `ph-check-square`),
title text (strikethrough + `fgSoft` when done), optional due-date
chip on the right (`accent` if today, `warn` if overdue).

Empty state: `ph-list-checks` icon + "No tasks" — centred.

Sizes: xs (just count + next item), sm/md/lg (progressively more items
visible).

#### 6. `calendar_upcoming` — upcoming events

Vertical list of upcoming events pulled from an ICS feed (any service
that exposes a public ICS URL — Google, Outlook, Fastmail, etc.).

Cell options:
- ICS feed URL (string)
- Days ahead (int, default 7)
- Show all-day events (boolean, default true)
- Time format (select: `24h` / `12h`)

Layout: grouped by day. Each day starts with a small uppercase header
row (`muted`, with a `surface2` background swatch behind today). Event
rows: time chip on the left (accent bg, bg-coloured text), title in
the middle (`fg`), optional location on the right (`fgSoft` truncated).

Sizes:
- xs/sm — just the next 1-3 events, no grouping
- md — 7-day list with grouping
- lg — 7-day list, possibly two columns

Use the `ical.events` Python library OR parse minimally with `icalendar`.

#### 7. `f1_next_race` — countdown to next F1 race

Cell options:
- Time format (select: `24h` / `12h`)
- Highlight when ≤ 24h (boolean, default true) — flip the hero to
  `warn` colour when race is imminent.

Backing data: jolpi.ca (the public Ergast successor) at
`https://api.jolpi.ca/ergast/f1/current/next.json`. No API key.

Layout: hero countdown ("3 days 4 hours") at top, then circuit name +
country, then a tiny schedule row (FP1 / FP2 / FP3 / Sprint? / Quali /
Race) with each session's local-time chip. Use `ph-flag-checkered` as
the hero icon, `ph-map-pin` for the circuit, `ph-clock` for the
schedule strip.

Sizes:
- xs/sm — just the countdown + race name
- md — + circuit + a 2-line schedule
- lg — full session schedule

#### 8. `f1_last_results` — most recent race podium + top 10

Backing data: `https://api.jolpi.ca/ergast/f1/current/last/results.json`.

Cell options:
- Show top N (int, default 5)

Layout:
- Header: race name + date + flag-checkered icon
- Top 3 podium tiles with position number (gold=`warn`, silver=`fgSoft`,
  bronze=`accent`-tinted), driver name, team initials, gap
- Remaining finishers as compact rows with position + driver + gap

Sizes:
- xs — just the winner (1 line)
- sm — top 3 podium
- md/lg — podium + table

#### 9. `f1_standings` — drivers + constructors championship

Backing data:
- Drivers: `https://api.jolpi.ca/ergast/f1/current/driverStandings.json`
- Constructors: `https://api.jolpi.ca/ergast/f1/current/constructorStandings.json`

Cell options:
- Show top N (int, default 5)
- Show constructors (boolean, default true)

Layout:
- md/lg: two columns — Drivers left, Constructors right
- sm: single column, Drivers only (more compact)
- Each row: position chip, name (driver code or team initials), points
  (`fg`, tabular-numerics). Leader gets `surface2` background.

### Batch C — propose 3-5 more widgets

After the explicit list above, **propose 3-5 additional widgets** you
think would be useful for a self-hosted e-ink dashboard. Don't ask
permission — pick what's interesting and brief them properly.

Ground rules for the proposals:

* Public data source (no required API key OR an obviously-paid model
  like Spotify is fine if it adds enough value).
* Different in shape from anything already covered — a second weather
  widget doesn't count; a different *kind* of widget does.
* Each one should justify its existence: who's the user, why is this
  better than just looking at their phone?

A few directions worth considering (not a shopping list — pick or
invent):

* **Time / date** — analogue clock, year-progress bar, "days until X"
  countdown, world clocks (multi-timezone strip).
* **News / feeds** — Hacker News top headlines, RSS reader, Reddit
  subreddit hottest.
* **Astronomy / nature** — NASA Astronomy Picture of the Day, moon
  phase strip, aurora forecast, ISS pass tracker, tide schedule.
* **Transit** — local train/bus arrivals (any public GTFS feed),
  flight status by tail number.
* **Finance** — stock or crypto price strip, your-portfolio summary.
* **Health / activity** — Strava recent activities, step count.
* **Smart home** — Home Assistant entity tiles, room temperatures,
  energy use.
* **System / dev** — GitHub commit heatmap, CI status, deployment
  list, server uptime.
* **Domestic** — laundry done?, bin day reminder, pet feeding log.

Brief each proposed widget with the same 0-12 section template. In
section 0 (one-line summary) include why it earns a slot.

---

## Output format

One filled-in brief per widget, using the structure from
[`widget-design-brief.md`](widget-design-brief.md). Number every
section 0 through 12. Include:

- ASCII mockup per supported size with numbered annotations
- Icon manifest table (every Phosphor name + weight)
- Tone-rules table (palette-token-only — never raw hex)
- Size-adaptations table
- Sample data shape for `ctx.data`

Hand them all back in one Markdown document. Claude Code will paste
it back for build.

---

## Hard constraints — things to AVOID

- Drawn borders of any kind. No `border:` rules on layout elements.
- `ph-fill` weight icons. Use `ph-bold` for prominent icons; regular
  for small inline ones.
- Hard-coded hex colours **except for the documented data-identity
  cases** (team colours, brand colours, flags — documented in section
  12 of the brief).
- Per-widget chrome that fights the cell. The cell is the card.
- Animations / transitions. These get caught mid-frame by the
  screenshot pipeline. `animation: false` on Chart.js too.
- Reaching outside the shadow DOM. Widgets must be self-contained.
- Custom font loading. `font-family: inherit` on `:host`.
- Tone rules that map to the `divider` token. That token is for chart
  axes only — if you want a separator, use spacing or a surface shift.
- Bundling huge custom assets. SVG preferred, <10 KB each, Phosphor
  first.
