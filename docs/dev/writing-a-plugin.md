# Build a widget with AI

A Tesserae widget is a small, self-contained plugin: a manifest, a `client.js`
that renders into a Shadow DOM, a `client.css`, and an optional `server.py` for
data. That shape — **a tight, documented contract with a fast feedback loop** —
makes widgets an unusually good fit for AI-assisted coding. You describe what
you want, the model writes the files against the contract, and you watch it
render in the browser in seconds.

This page is the AI workflow. The authoritative spec lives in
[the widget contract & design system](../widgets.md) — keep it open; the model
should read it too.

!!! tip "Works with any capable coding assistant"
    The examples assume [Claude Code](https://claude.com/claude-code) or a
    similar agentic tool that can read files and run commands, but the prompts
    work in any chat model — just paste the contract doc in alongside them.

## Why this works well

- **The contract is small and explicit.** [`docs/widgets.md`](../widgets.md) defines the whole surface: the `render(shadow, ctx)` signature, the `ctx` shape, the `--c-*` colour layer, container queries, the e-ink rules. A model that reads it has everything it needs.
- **There are 58 worked examples** in `plugins/`. "Model your widget on `weather_now`" is a one-line instruction that carries an enormous amount of design and structure.
- **The feedback loop is seconds.** `/_test/render?plugin=<id>&size=md` renders a single widget with no dashboard. The dev server auto-reloads; refresh to see edits.
- **Every widget ships a smoke test.** The model can write it and you can run it — objective "did it work" rather than vibes.

## Setup

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main --dev      # auto-reload + /_test/render enabled
```

Sign in once (these routes need the dev server **and** a session — they
aren't loopback-exempt), then iterate at
`http://127.0.0.1:8765/_test/render?plugin=<id>&size=md` (also
`size=xs|sm|lg`). The whole-gallery review page is at
`http://127.0.0.1:8765/_test/widgets`.

## The loop

1. **Orient the model** — point it at the contract + a reference widget.
2. **Describe the widget** — data source, what each size shows, the layout.
3. **Let it scaffold** the four files under `plugins/<id>/`.
4. **Render and critique** — open `/_test/render`, paste back a screenshot or describe what's off.
5. **Write the smoke test** and run `pytest plugins/<id>/`.
6. **Check the constraints** (below) and open a PR.

## Copy-paste prompts

### 1. Orient the model

```text
We're building a widget for Tesserae, a self-hosted e-ink dashboard.
A widget is a drop-a-folder plugin under plugins/<id>/ with:
  - plugin.json  (manifest)
  - client.js    (ES module, default export `render(shadow, ctx)`, renders into a Shadow DOM)
  - client.css   (styles)
  - server.py    (optional, server-side data fetch -> ctx.data)

Before writing anything, read docs/widgets.md end to end — it is the full
contract: the ctx shape, the colour layers (paint from the --c-* semantic
tokens, never the raw --theme-* primitives; --c-data-* for categorical
colour, --c-ok/warn/danger for status only; no hard-coded hex), container
queries (cqw/cqh), Phosphor icon usage (bold for big icons, never fill), and
the e-ink constraints (no drawn borders, no animations, no client-side fetch
— use server.py).

Then read these three shipped widgets as the canonical patterns:
plugins/weather_now, plugins/weather_hourly, plugins/weather_forecast.

Confirm you've read them and summarise the contract back to me in 5 bullets
before we design anything.
```

### 2. Describe the widget to build

Fill in the blanks — the more specific the data source and per-size layout, the
better the first pass:

```text
Build a widget: <id> ("<Display Name>").

Purpose: <one sentence — who's it for, why is it better than glancing at a phone>

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
widgets — colour blocks from --theme-accent / accent2 / accent3 + surface2,
heavy type, one big bold Phosphor hero icon. Use ctx.cell.size to drop
non-essential sections at xs/sm.

Write all four files under plugins/<id>/. Don't hard-code hex except for the
documented data-identity cases in docs/widgets.md.
```

### 3. Critique the render

Open `/_test/render?plugin=<id>&size=md`, then:

```text
Here's how it renders at md and xs [paste screenshots or describe]. Issues:
- <e.g. the hero number overflows at xs>
- <e.g. the rain block uses danger red — switch to accent2/accent3, danger is
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

These are the things AI most often gets wrong on e-ink. Call them out explicitly
(they're all in [the contract](../widgets.md), but worth repeating):

- [ ] **Semantic tokens only** — style DOM with `var(--c-*)` (`--c-data-*` for categorical colour, `--c-ok/warn/danger` for status only); `ctx.theme.*` is canvas-only. Never raw `--theme-*` or hard-coded hex (except documented data-identity colours: team/brand/flag).
- [ ] **No drawn borders.** Card shapes come from `bg` vs `surface` contrast and spacing, not `border:` rules. They dither into invisibility on Spectra 6 anyway.
- [ ] **Bold, not fill, for big icons.** `ph-bold` reads clean at size; `ph-fill` quantises into blobs.
- [ ] **No animations / transitions / `requestAnimationFrame`.** The frame is screenshotted; anything mid-flight gets caught half-rendered. `animation: false` on Chart.js too.
- [ ] **No client-side `fetch`.** Use `server.py` — the renderer waits only for declared `<img>` loads + fonts, not arbitrary fetches, so a client fetch will screenshot before its data arrives. Don't assume internet on the panel side either.
- [ ] **Idempotent render.** Overwrite `shadow.innerHTML`; don't append (the renderer may call you twice).
- [ ] **Don't load fonts.** `font-family: inherit` on `:host`; the page font arrives as `ctx.font.family`.
- [ ] **`danger` / `warn` / `ok` are semantic only.** Use `accent` / `accent2` / `accent3` (i.e. the `--c-data-*` family) for decorative colour blocks.
- [ ] **Multiple visual directions go in a `variant` cell option.** When a widget ships Refined / Geometric / Swiss / Data variants, expose them via a single `select` option named `variant` and dispatch to per-variant render functions in `client.js`. 34 shipped widgets follow this pattern — copy `weather_now/client.js` or `ha_climate/client.js`. Ship the `legacy` value too for users who prefer the quiet pre-colour-pass look.

## Structured design first (optional)

For a more involved widget, have the model produce a **filled-in design brief**
before any code, using the template in
[`docs/widget-design-brief.md`](../widget-design-brief.md) — ASCII mockups per
size, an icon manifest, a tone-rules table. It front-loads the layout decisions
and makes the build pass cleaner.

## Submitting

- Run the smoke test and `.venv/bin/ruff check plugins/<id>/`.
- Open a PR. New widgets are welcome — especially ones backed by a documented, key-free public API (those land in the **Stable** tier; see [Screens & compatibility](../compatibility.md)).
- If your widget hits an undocumented or scraped endpoint, say so in the PR — it'll be tiered **Best-effort** or **Fragile** so users know what to expect.

Once it's merged and you've captured screenshots, it shows up automatically
in the [widget gallery](../widgets/gallery.md):

```sh
python scripts/capture_widget_shots.py                # single hero shot per widget
python scripts/capture_widget_variants.py             # 2×N composite of every direction
```

The first refreshes `docs/screenshots/widgets/<id>.png` (the gallery's
default hero image). The second walks every variant of every widget,
stitches them into `<id>--variants.png`, and the gallery generator
embeds it as a "N directions — click to view" caption under the hero
shot.

For ongoing design work, `python scripts/widget_contact_sheet.py` builds a
single PNG showing your widget at all four sizes side-by-side — the easiest
"did anything regress?" loop while iterating on a variant or polish pass.
