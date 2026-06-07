# Build a widget with AI

A Tesserae widget is a small, self-contained plugin: a manifest, a `client.js`
that renders into a Shadow DOM, a `client.css`, and an optional `server.py` for
data. That shape, **a tight, documented contract with a fast feedback loop** -
makes widgets an unusually good fit for AI-assisted coding. You describe what
you want, the model writes the files against the contract, and you watch it
render in the browser in seconds.

This page is the AI workflow. The authoritative spec lives in
[the widget contract & design system](../widgets.md), keep it open; the model
should read it too.

!!! tip "Works with any capable coding assistant"
    The examples assume [Claude Code](https://claude.com/claude-code) or a
    similar agentic tool that can read files and run commands, but the prompts
    work in any chat model, just paste the contract doc in alongside them.

## Why this works well

- **The contract is small and explicit.** [`docs/widgets.md`](../widgets.md) defines the whole surface: the `render(shadow, ctx)` signature, the `ctx` shape, the Spectra semantic tokens (`--bg`, `--surface`, `--text-primary`, `--accent-1..6`, etc.) and the orthogonal `data-style` axis, container queries, the e-ink rules. A model that reads it has everything it needs.
- **There are 58 worked examples** in `plugins/`. "Model your widget on `weather_now`" is a one-line instruction that carries an enormous amount of design and structure.
- **The feedback loop is seconds.** `/_test/render?plugin=<id>&size=md` renders a single widget with no dashboard. The dev server auto-reloads; refresh to see edits.
- **Every widget ships a smoke test.** The model can write it and you can run it, objective "did it work" rather than vibes.

## Setup

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m app.main --dev      # auto-reload + /_test/render enabled
```

Sign in once (these routes need the dev server **and** a session, they
aren't loopback-exempt), then iterate at
`http://127.0.0.1:8765/_test/render?plugin=<id>&size=md` (also
`size=xs|sm|lg`). The whole-gallery review page is at
`http://127.0.0.1:8765/_test/widgets`.

## The loop

1. **Orient the model**, point it at the contract + a reference widget.
2. **Describe the widget**, data source, what each size shows, the layout.
3. **Let it scaffold** the four files under `plugins/<id>/`.
4. **Render and critique**, open `/_test/render`, paste back a screenshot or describe what's off.
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

Fill in the blanks, the more specific the data source and per-size layout, the
better the first pass:

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

These are the things AI most often gets wrong on e-ink. Call them out explicitly
(they're all in [the contract](../widgets.md), but worth repeating):

- [ ] **Spectra semantic tokens only.** Paint from `var(--bg)`, `var(--surface)`, `var(--surface-sunken)`, `var(--text-primary/-secondary/-muted)`, `var(--accent-1..6)` (and `--accent-1-soft..6-soft` for tinted backgrounds), `var(--on-accent)` for text on accent backgrounds. Never hard-coded hex except the documented data-identity carve-out (team/brand/flag colours; see docs/widgets.md).
- [ ] **Pick an archetype body class.** `.stat-body` / `.list-body` / `.chart-body` / `.status-body` / `.cal-body` / `.wx-body` / `.img-body`, the seven archetypes carry the font-size cascade, gap rhythm, and zoom-aware sizing that keep widgets consistent next to each other on a panel. Don't roll your own body layout.
- [ ] **Consume style-tunable tokens with fallbacks.** Use `var(--edge-weight, 1px)`, `var(--label-transform, uppercase)`, `var(--pill-radius, 999px)`, etc., defaults live on `:root` (the Standard look); styles override on `[data-style]`. The fallback means an unstyled widget still renders.
- [ ] **No drawn borders.** Card shapes come from `--bg` vs `--surface` contrast and spacing, not `border:` rules. They dither into invisibility on Spectra 6 anyway.
- [ ] **Bold, not fill, for big icons.** `ph-bold` reads clean at size; `ph-fill` quantises into blobs.
- [ ] **No animations / transitions / `requestAnimationFrame`.** The frame is screenshotted; anything mid-flight gets caught half-rendered. `animation: false` on Chart.js too.
- [ ] **No client-side `fetch`.** Use `server.py`, the renderer waits only for declared `<img>` loads + fonts, not arbitrary fetches, so a client fetch will screenshot before its data arrives. Don't assume internet on the panel side either.
- [ ] **Idempotent render.** Overwrite `shadow.innerHTML`; don't append (the renderer may call you twice).
- [ ] **Don't load fonts.** `font-family: inherit` on `:host`; the page font arrives via `--font-family` and `ctx.font.family`.
- [ ] **No `variant` cell option.** The Spectra rebuild replaced per-widget variants with the orthogonal `data-theme` × `data-style` axes, one widget should compose with every theme and every style instead of shipping N visual directions. If you find yourself reaching for variants, you probably want a new style or a new theme.
- [ ] **Hero + list layouts don't use `grid-template-rows: 1fr auto`.** Models reach for this when stacking a hero stat on top of a bullet list, and it looks right at `lg`. At `md` / `sm` the `auto` list claims its full content height first and the `1fr` hero collapses to a sliver. Use `grid-template-rows: auto minmax(0, 1fr)` (hero takes its size, list fills) or cap visible list rows per size via `.size-sm .row:nth-child(n+3) { display: none; }`. See [the contract's "Hero + list layouts"](../widgets.md#hero-list-layouts-dont-let-auto-rows-starve-the-hero) section.
- [ ] **Verify every size in `/_test/render`, not just `md`.** `lg` is forgiving and `md` usually looks fine; `sm` and `xs` are where layouts break. Insist the model produces screenshots at all four sizes before declaring the widget done.
- [ ] **Translate technical errors to friendly messages.** The `error` string from `server.py:fetch()` lands directly in the cell; `"HTTPError: 404 Not Found"` reads as "broken widget" while `"Country code 'XX' is not supported."` reads as "I typed something wrong." Catch the categories you can name (invalid input, upstream down, rate-limited) and pass everything else through with a tame fallback.

## Structured design first (optional)

For a more involved widget, have the model produce a **filled-in design brief**
before any code, using the template in
[`docs/widget-design-brief.md`](../widget-design-brief.md), ASCII mockups per
size, an icon manifest, a tone-rules table. It front-loads the layout decisions
and makes the build pass cleaner.

## Submitting

You have two paths, pick the one that matches your widget's audience:

### Bundle into Tesserae itself

For foundational widgets you want every install to ship with:

- Run the smoke test and `.venv/bin/ruff check plugins/<id>/`.
- Open a PR against [dmellok/tesserae](https://github.com/dmellok/tesserae).
  New widgets are welcome, especially ones backed by a documented,
  key-free public API (those land in the **Stable** tier; see
  [Screens & compatibility](../compatibility.md)).
- If your widget hits an undocumented or scraped endpoint, say so
  in the PR; it'll be tiered **Best-effort** or **Fragile** so users
  know what to expect.

Once it's merged and you've captured screenshots, it shows up
automatically in the [widget gallery](../widgets/gallery.md):

```sh
python scripts/capture_widget_shots.py    # single hero shot per widget
```

That refreshes `docs/screenshots/widgets/<id>.png` (the gallery's
default hero image). For visual-regression spot-checks across themes
or styles, hit `/_test/matrix` in the dev server, it renders one
widget across the full theme × style grid in a single page.

For ongoing design work, `python scripts/widget_contact_sheet.py`
builds a single PNG showing your widget at all four sizes
side-by-side, the easiest "did anything regress?" loop while
iterating on a polish pass.

### Publish through the community catalog

For your own widgets, third-party APIs, niche use cases — anything
you want users to opt into per install rather than shipping to
everyone. Your widget lives in its own GitHub repo and ships via a
PR to the catalog index; users find it on Settings → Widgets →
Browse community widgets.

See **[Publish a widget through the catalog](publishing-a-widget.md)**
for the full flow: pinned-release tarballs, sha256 verification, the
catalog PR template, bundle entries for widget families
(`_core` + display widgets installed together), and what the
review checklist looks at.
