# Widget design brief — handoff template

Paste this template into your Claude Design conversation and ask it to
fill out one copy per widget you want built. The output is what gets
pasted back to Claude Code for the actual build.

Pair this with [`docs/widgets.md`](widgets.md) — that's the wire +
render contract this brief builds on. Claude Design should read both
before drafting.

Every section is required unless marked **(optional)**. Omitting a
required section forces Claude Code to either guess or stop to ask —
worth filling in even if the answer feels obvious.

---

## 0. One-line summary

> A short sentence describing what the widget shows + where the data
> comes from. Used for the manifest `description`.

Example: *Current weather conditions + feels-like, humidity, wind, UV,
and sun times. Data from Open-Meteo (no API key).*

---

## 1. Identity

- **id**: `<lowercase_snake_case>` — the folder name and URL slug. The
  loader only skips `.`/`_`-prefixed folders; lowercase `[a-z0-9_]` is
  convention. Use `<family>_<role>` (e.g. `weather_now`, `news_headlines`).
- **name**: short display name as it'll show in the widget picker.
- **version**: `0.1.0` for a new widget.
- **icon**: Phosphor icon name (regular weight) for the editor picker.
  Browse [phosphoricons.com](https://phosphoricons.com).

---

## 2. Sizes supported

Subset of `["xs", "sm", "md", "lg"]`. Justify exclusions.

Cell dimensions: xs = 180×180 · sm = 380×240 · md = 640×400 · lg = 1200×800.

Example: *xs, sm, md, lg — supports the full range; xs collapses to
just the temp + condition icon.*

Example: *sm, md, lg — skipping xs because the chart needs at least
~280 px of vertical space for axis labels to be legible.*

---

## 3. Cell options (per-cell user knobs)

The form fields the editor will render. Types: `string` · `textarea` ·
`number` · `select` · `boolean` · `color`. `select` requires `choices`.

| name | type | default | label | choices |
|------|------|---------|-------|---------|
| `latitude` | `number` | `-37.8136` | Latitude | — |
| `longitude` | `number` | `144.9631` | Longitude | — |
| `label` | `string` | `Melbourne` | Place label | — |
| `units` | `select` | `metric` | Units | `metric` (Metric °C) · `imperial` (Imperial °F) |

---

## 4. Plugin settings (optional, global)

Only fill in if the widget needs server-wide config (API keys,
shared polling intervals). Same table shape as cell options. Mark
sensitive fields with `secret: true`.

| name | type | default | label | secret? |
|------|------|---------|-------|---------|
| `api_key` | `string` | — | API key | ✓ |

---

## 5. Data source

Pick one. **A** for anything needing API calls / file reads / cross-
origin fetches; **B** for purely computed widgets (time of day, etc.).

### A. Server-side fetch (`server.py`)

- **Endpoint URL**: full URL template with `{lat}` / `{units}` etc.
  placeholders. Multiple URLs OK if the widget composes several calls.
- **Method**: GET / POST. Headers? Body shape?
- **Auth**: none · API key (from settings.`<field>`) · bearer · basic.
- **Cache TTL**: how long to keep the response in `data_dir`. Express
  in minutes/hours. Politeness ≥ 10 min for public APIs.
- **Sample response** (paste a real one, or a representative shape):

```json
{
  "current": { "temperature_2m": 22.4, "weather_code": 3 },
  "daily": { "temperature_2m_max": [25], "sunrise": ["2026-05-27T06:42"] }
}
```

### B. Client-side only (no `server.py`)

- **Inputs**: `ctx.cell.options.*` (per-cell), `ctx.theme`, `ctx.cell.size`.
- **What you compute**: e.g. *time and date from `Date.now()` and the
  user's timezone option.*

---

## 6. `ctx.data` shape

The JSON `server.py` returns (or `null` if client-side). The contract
between server.py and client.js — be explicit about units and types.

```json
{
  "label": "Melbourne",       // string, copied from options for convenience
  "temp": 22.4,               // float, in the selected unit
  "feels": 21.2,              // float, in the selected unit
  "humidity": 65,             // int 0-100
  "wind": 12,                 // float, km/h or mph per units
  "wind_dir": 180,            // int 0-360 (compass degrees) or null
  "code": 3,                  // WMO weather code (int)
  "is_day": true,             // bool
  "uv": 4.5,                  // float or null
  "sunrise": "2026-05-27T06:42",  // ISO 8601 or null
  "sunset":  "2026-05-27T17:09",
  "today_max": 25,            // float
  "today_min": 11,            // float
  "rain_chance": 40           // int 0-100 or null
}
```

For error: `{"error": "<message>"}`.

---

## 7. Layout mockups

One block per supported size — ASCII mockup + a numbered annotation
list. Don't worry about pixel-perfection; communicate hierarchy,
grouping, and which theme token / icon goes where.

### lg (1200×800) — full feature

```
┌──────────────────────────────────────────────────────────────────┐
│ MELBOURNE                                                  [1]   │
│                                                                  │
│  ┌──────────────┐    Feels  22°  · Humid 65%   · Wind 12 [2]    │
│  │              │    ──────────────────────────────────────      │
│  │     ☁        │    Sunrise  06:42      Sunset  20:14    [3]   │
│  │    25°       │                                                 │
│  │   Cloudy     │                                                 │
│  │   ↑27 ↓18    │                                                 │
│  └──────────────┘                                                 │
└──────────────────────────────────────────────────────────────────┘

[1] place label — uppercase, weight 700, fgSoft. ph-map-pin (regular,
    accent) before the text.
[2] stats — 4 columns. label uppercase muted; value weight 700.
    Icons (left of each value): ph-thermometer-simple at bold weight (warn),
    ph-drop-half at bold (accent), ph-wind at bold (fgSoft), ph-sun-dim at bold
    (tone by UV band).
[3] sun row — ph-duotone-sun-horizon + ph-duotone-moon-stars (accent).
```

### md (640×400)
```
... mockup
```
... annotations

### sm (380×240)
```
... mockup
```
... annotations

### xs (180×180)
```
... mockup
```
... annotations

---

## 8. Icon manifest

Every Phosphor icon the widget uses, with weight. Drives which icon
stylesheets get `<link>`-ed inside the shadow root.

| name                  | weight   | where                  |
|-----------------------|----------|------------------------|
| `cloud-sun`           | bold     | hero condition icon    |
| `map-pin`             | regular  | place label            |
| `thermometer-simple`  | bold     | feels-like stat        |
| `drop-half`           | bold     | humidity stat          |
| `wind`                | bold     | wind stat              |
| `sun-dim`             | bold     | UV stat                |
| `sun-horizon`         | duotone  | sunrise row            |
| `moon-stars`          | duotone  | sunset row             |
| `arrow-up`/`arrow-down` | bold   | range high/low         |
| `warning-circle`      | regular  | error state            |

Weights needed: `regular`, `bold`, `duotone`. (Each weight adds one
`<link>` and ~250 KB font — only ship what you use.) **Default to
`bold` for prominent / hero icons** — `fill` quantises into solid
blobs on Spectra 6 and reads heavier than intended; `bold` is the
outline-with-presence the design language calls for.

---

## 9. Tone rules (semantic colour)

If element colour depends on data, table it. Output is **always** a
semantic token (`--c-data-*` for categorical, `--c-ok` / `--c-warn` /
`--c-danger` / `--c-info` for status only). Reach for status tokens
only when the value is a genuine advisory, hazard, or error — never
because you want a particular hue. See `docs/widgets.md` →
**Categorical vs status — the rule**.

| element         | data value             | semantic token | rationale |
|-----------------|------------------------|----------------|-----------|
| hero icon       | code 0–1 (clear)       | `--c-data-1`   | decorative — sunny day, not a "warning" |
| hero icon       | code 2 (partly cloudy) | `--c-data-2`   | decorative |
| hero icon       | code 3/45/48 (overcast)| `--c-text-mute`| decorative — drained colour reads as overcast |
| hero icon       | code 51–82 (rain)      | `--c-data-3`   | decorative |
| hero icon       | code 71–86 (snow)      | `--c-text-soft`| decorative |
| hero icon       | code 95–99 (storm)     | `--c-danger`   | **status** — severe storm IS a hazard advisory |
| UV value        | uv < 3                 | `--c-ok`       | status — safe band |
| UV value        | uv 3–7.9               | `--c-warn`     | status — caution band |
| UV value        | uv ≥ 8                 | `--c-danger`   | status — burn risk |
| range high arrow| —                      | `--c-data-1`   | decorative |
| range low arrow | —                      | `--c-text-soft`| decorative |
| rain pill       | rain ≥ 30%             | `--c-data-2`   | decorative |

---

## 10. Size adaptations

What gets hidden / shrunk as the cell gets smaller. Be specific.

| at size | hide                              | shrink                          |
|---------|-----------------------------------|---------------------------------|
| `lg`    | nothing                           | nothing                         |
| `md`    | nothing                           | hero icon → 14cqw               |
| `sm`    | stats grid, sun row               | hero icon → 18cqw               |
| `xs`    | stats, sun, range, condition text | hero icon → 22cqw, temp → 14cqw |

---

## 11. Edge cases

| case                  | behaviour                                              |
|-----------------------|--------------------------------------------------------|
| `ctx.data.error` set  | render error card with `ph-warning-circle` + message  |
| `ctx.data.points` empty | render `state-empty` block: "No data available"     |
| field is `null`       | show `—` (em-dash)                                     |
| temp out of range     | clamp to int + ° suffix (e.g. `45°`)                   |

---

## 12. Notes (optional)

Anything that doesn't fit above. Examples:

- "Bias toward `warn` for hot temps on the Spectra 6 panel — the
  yellow primary is the most attention-grabbing."
- "Chart.js: enable the `tension: 0.35` smoothing; disable
  animations (`animation: false`)."
- "Default saturation should be 1.4 — Spectra 6 quantises pale
  colours into white otherwise."

---

# Minimal example brief

Below is what a brief for a **`year_progress`** widget — a single
horizontal bar showing how much of the year is done — would look
like. It's intentionally short because the widget is small; longer
widgets (charts, multi-section dashboards) need proportionally more
detail in sections 6, 7, 9, 10.

---

## 0. One-line summary

A horizontal progress bar + percentage showing how far through the
current calendar year we are.

## 1. Identity

- **id**: `year_progress`
- **name**: Year progress
- **version**: 0.1.0
- **icon**: `ph-calendar-check`

## 2. Sizes supported

xs, sm, md, lg — bar collapses gracefully at xs.

## 3. Cell options

| name | type | default | label | choices |
|------|------|---------|-------|---------|
| `show_percent` | `boolean` | `true` | Show % number | — |

## 4. Plugin settings

None.

## 5. Data source

Client-side only. Compute `pct = (today - jan_1) / (dec_31 - jan_1)`.

## 6. `ctx.data` shape

`null` — widget is fully client-side.

## 7. Layout mockups

### md (640×400)

```
┌──────────────────────────────────────────────┐
│ YEAR PROGRESS                          2026  │  ← head: title + year
│ ──────────────────────────────────────────── │
│                                              │
│  ████████████████░░░░░░░░░░░░░░░░░░░░░  41%  │  ← bar + percent
│                                              │
│  May 27  →  Dec 31 · 218 days remaining      │  ← footer line
└──────────────────────────────────────────────┘
```

[1] head — uppercase title (fgSoft), year (accent) on the right.
[2] bar — full-width, height 14cqh. Fill: `accent`. Track: `surface2`.
    Rounded corners (`border-radius: 999px`).
[3] percent — right-aligned, weight 700, fg, clamp(20px, 4cqw, 36px).
[4] footer — muted, regular weight, single line.

### sm (380×240) — same, smaller type
### xs (180×180) — just the bar + percent, drop head + footer

## 8. Icon manifest

| name             | weight  | where        |
|------------------|---------|--------------|
| `calendar-check` | regular | head icon    |
| `warning-circle` | regular | error state  |

Weights: `regular` only.

## 9. Tone rules

| element | data value | semantic token | rationale |
|---------|------------|----------------|-----------|
| bar fill | <50%      | `--c-accent`   | decorative — progress, not a status |
| bar fill | 50–80%    | `--c-accent`   | decorative |
| bar fill | >80%      | `--c-warn`     | status — running out of year IS the advisory |

## 10. Size adaptations

| at size | hide                   | shrink              |
|---------|------------------------|---------------------|
| md/lg   | nothing                | nothing             |
| sm      | footer                 | percent → 3.6cqw    |
| xs      | head, footer           | bar height → 22cqh  |

## 11. Edge cases

None — purely computed from today's date.

## 12. Notes

Animation off; tabular-numerics on percent.
