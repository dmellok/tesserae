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
widget at any (w, h), the size token is derived from the longer side:

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
element, `ctx.cell.size` gives you the token, you stamp the class).

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

`<id>` is the folder name, it doubles as the URL slug and the disk path.
The loader skips any folder starting with `.` or `_`; beyond that,
lowercase `[a-z0-9_]` is convention, not enforced. Name it `<family>_<role>`
- e.g. `weather_now`, `weather_hourly`.

---

## Manifest, `plugin.json`

```json
{
  "tesserae_compat": "1.x",
  "name": "Weather, Now",
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

* **`supports.sizes`**, which test-render sizes you've designed for.
  Omit ones you don't support; the editor will skip your widget on
  cells that exceed them.
* **`cell_options`**, per-cell knobs. The editor renders one form
  field per option. Types: `string`, `textarea`, `number`, `select`
  (needs `choices` OR `choices_from`), `multiselect` (same),
  `boolean`, `color`. The user's values land in `ctx.cell.options` at
  render time. Don't ship a `variant` option for visual direction,
  that model is gone; the `data-style` axis at page level provides
  cross-widget shape selection. If a widget needs a genuine layout
  shape choice (e.g. `stack` vs `side`), name the option `layout` and
  use shape-describing values.
* **`settings`**, plugin-wide knobs (one set across all cells using
  this widget). Surfaces in `/settings/plugins/<id>`. `secret: true`
  stores under `<name>_secret` in `settings.json` so an on-disk grep
  for `secret` reveals every sensitive value.
* **`icon`**, Phosphor name used in the editor's widget picker.
* **`render.needs_network`**, hint for the renderer (not enforced).

Full schema: [`schema/plugin.schema.json`](https://github.com/dmellok/tesserae/blob/main/schema/plugin.schema.json).

### Dynamic cell-option choices, `choices_from`

A `select` / `multiselect` cell option can source its dropdown
contents at edit time instead of declaring a static `choices` array.
Set `choices_from: "<key>"` in the manifest, and the host calls
**this plugin's** `server.py:choices(name)` with `name = "<key>"`:

```jsonc
// plugin.json
{
  "name": "board_id",
  "type": "select",
  "label": "Board",
  "choices_from": "boards"
}
```

```python
# server.py
def choices(name: str) -> list[dict[str, str]]:
    if name != "boards":
        return []
    return [{"value": b["id"], "label": b["name"]} for b in _read_boards()]
```

The function returns a list of `{value, label}` dicts. Errors render
as an empty dropdown (the host catches + logs), so prefer returning
`[]` over raising.

**Important:** the host calls `choices()` on the **same plugin** as
the manifest, never on a sibling. If your widget reads data from a
companion `_core` plugin, you still expose `choices()` on the widget
and delegate to the core via the plugin registry:

```python
# plugins/widget_x/server.py
from flask import current_app

def choices(name: str) -> list[dict[str, str]]:
    core = current_app.config["PLUGIN_REGISTRY"].get("widget_x_core")
    if core is None or core.server_module is None:
        return []
    core_choices = getattr(core.server_module, "choices", None)
    return list(core_choices(name)) if callable(core_choices) else []
```

This is the `calendar_*` → `calendar_core` pattern (or, in the
catalog, `glances_status` → `glances_core` if you install the
monitoring bundle). The Dev Reference Bundle
(`devref_card` → `devref_core`) is a worked example you can
install from the catalog and read end-to-end.

### Companion `_core` plugins

A widget family that needs shared admin state (saved instances, API
keys, user-edited lists) splits into two plugin folders:

* `<family>_core` — `kind: "data"`, no widget. Exposes an admin page
  via `server.py:blueprint()` and a `choices()` function the
  display widgets delegate to.
* `<family>_<view>` — `kind: "widget"`. Reads from the core via the
  plugin registry, both at edit time (`choices_from`) and at render
  time (`server.py:fetch`).

Existing examples in the bundled tree: `calendar_core` +
`calendar_*`, `weather_core` + `weather_*`, `ha_core` + `ha_*`.
Marketplace bundles using the same pattern: `glances_core` +
`glances_status`, `github_core` + `github_*`, `spotify_core` +
`spotify_*`, `f1_core` + `f1_*`. When shipping a family through
the marketplace, declare all folders in the catalog entry's
`folders` array so the install lands them as siblings under
`plugins/`.

### Admin pages, `blueprint()`

Any plugin (widget OR `_core`) that exports `server.py:blueprint()`
gets a `/plugins/<id>/` admin page. Return a Flask Blueprint with
your routes; the host mounts it under the per-plugin URL prefix.

```python
# server.py
from flask import Blueprint, render_template

def blueprint() -> Blueprint:
    bp = Blueprint("widget_x_admin", __name__, template_folder="templates")

    @bp.get("/")
    def index() -> str:
        return render_template("widget_x/index.html", ...)

    return bp
```

Templates live under `templates/<plugin_id>/` (the folder namespacing
keeps Flask from colliding with the host's `templates/index.html`).
Extend `_base.html` for visual consistency with the rest of the
admin UI; the `card_head` macro + `card` class wrap content.

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
    plugin: "weather_now",
    plugin_id: "weather_now",
    options: { ... }       // cell_options values, defaults merged in
  },
  panel: {
    w: 1600, h: 1200,      // full panel dims
    portrait: false        // panel.h > panel.w
  },
  font: {
    family: "Inter",       // resolved page font (for non-Spectra widgets)
    weight: 400
  },
  data: { ... } | null,    // server.py fetch() result, if present
  preview: false           // true when rendered in the editor iframe
}
```

`ctx` no longer carries a `theme` block of hex strings. Spectra widgets
paint from CSS custom properties via the cascade; the chart helpers
resolve the live palette through a hidden probe (`tokens(shadow.host)`)
so canvas can read `t.accent1` / `t.surface` / `t.fontFamily` as
resolved colour strings.

* **Be idempotent**: the renderer may invoke you twice on a slow
  reload, overwrite `shadow.innerHTML` rather than appending.
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
    # Cache in data_dir for politeness, Open-Meteo, news APIs, etc.
    # See plugins/weather_now/server.py for the 10-minute cache pattern.
    return {"temp": 22.4, "label": options["label"]}
```

* Called fresh on every render. Cache yourself if rate-limited.
* `data_dir` is `data/plugins/<id>/`, gitignored, persisted across
  restarts.
* Network calls: use `urllib.request` with a timeout + a `User-Agent`
  header naming the widget (e.g. `tesserae/0.1 (+weather_now)`).
* Don't raise, return `{"error": "..."}`. Uncaught exceptions get
  caught upstream but produce uglier diagnostics.
* **Translate technical errors to friendly messages** before putting
  them in the `error` field. The string lands directly in the cell.
  `"HTTPError: 404 Not Found"` reads as "the widget broke";
  `"Country code 'XX' is not supported."` reads as "I typed
  something wrong, let me fix it." Catch the categories you can
  meaningfully name (invalid input, upstream down, rate-limited),
  pass everything else through with a tame fallback like
  `"Couldn't load <thing> right now."`.

---

## Capabilities, `requires:`

A widget can declare which capabilities it needs in its manifest's
`requires:` block. The host parses the declarations and enforces
network egress at the socket layer — a widget trying to phone home
to a host it didn't declare gets a `CapabilityDenied` instead of a
network round-trip. Settings + filesystem entries are review-only
in v1; the manifest forces the reviewer to notice the claim.

```jsonc
{
  "name": "Weather, Now",
  "kind": "widget",
  ...
  "requires": [
    "network:api.open-meteo.com",
    "settings:app"
  ]
}
```

### Vocabulary

| Capability | What it means | Enforced? |
|---|---|---|
| `network:<hostname>` | Allows outbound TCP/HTTPS to that exact hostname (matched at `socket.create_connection` before DNS). | **Yes** |
| `network:*` | Unrestricted egress. Catalog CI flags it; reviewers should push back unless the widget really needs arbitrary URLs (the gallery / picture widgets are the canonical case). | **Yes**, allows all |
| `settings:plugin` | Read this plugin's own settings. The host already passes them in via `fetch(opts, settings, ctx)`, so this declaration just makes the access explicit for the reviewer. | Review-only |
| `settings:plugin/<other_id>` | Read another plugin's settings section (e.g. a `_core` sibling's API token). Trips the reviewer to verify the claim. | Review-only |
| `settings:app` | Read top-level app settings (lat/lon, timezone, etc.). | Review-only |
| `filesystem:write:<path>` | Write outside the plugin's `data_dir`. Reads of `data_dir` and the plugin folder are implicit. | Review-only |

### How the network enforcement works

The host installs a hook over `socket.create_connection` (the bottom
of every Python HTTP stack — `urllib`, `requests`, `httpx`, the
sync path of `aiohttp`) and `socket.socket.connect`. When a widget's
`fetch()` runs, the host enters a capability context for the
duration of the call. Inside that context, every connect attempt
checks the active widget's `network:` allowlist; outside the context
(host code), the hook is a no-op.

Sketch:

```python
# in app/composer.py
with capability_scope(plugin.capabilities):
    return plugin.server_module.fetch(opts, settings, ctx=...)
```

```python
# in app/capabilities.py:_hooked_create_connection (simplified)
caps = _active.get()
if caps is not None and not caps.allows_host(address[0]):
    raise CapabilityDenied(f"{caps.plugin_id} can't connect to {address[0]}")
return _original_create_connection(address, ...)
```

The lookup is by hostname, not resolved IP, so a widget can't dodge
the gate by hardcoding `1.2.3.4`. Lower-level direct
`socket.socket().connect()` is covered too.

### Backward compatibility

Widgets without a `requires:` block load with **no enforcement** —
the same behaviour they had before this layer existed. The catalog
review checklist asks contributors to add `requires:` for any new
submission, but existing bundled widgets and pre-#2 catalog entries
keep working unchanged.

### Threat model — what this catches and what it doesn't

What it catches: a community widget that quietly tries to POST your
MQTT password to some upstream gets a `CapabilityDenied` and the
deny shows up in the server log. The "reviewer reads the manifest
and the code" workflow is now load-bearing — the manifest is a
machine-checked claim about the widget's surface.

What it doesn't catch: a determined attacker. Python is sandbox-
hostile. A widget can reach around the hook with `ctypes`, frame
inspection, or by spawning a subprocess. The hook is best-effort
defence-in-depth on top of the audit-only PR review, not a
substitute for it. Real isolation lives in issue #3 (subprocess +
seccomp, or WASM); until that ships, "audit-only catalog with
declared capabilities" is the trust model.

Worked examples in the bundled tree:

- [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now) —
  single-host network declaration: `network:api.open-meteo.com`.
- [`plugins/clock_sunrise_sunset`](https://github.com/dmellok/tesserae/tree/main/plugins/clock_sunrise_sunset) —
  same shape, second target on the same upstream.
- [`plugins/clock_analog`](https://github.com/dmellok/tesserae/tree/main/plugins/clock_analog) —
  `requires: []` — the widget explicitly claims no capabilities.

---

## `design.palette`, opting out of strict colour tokens

The Spectra colour tokens (`--accent-1` through `--accent-6`, plus
their `-soft` companions) constrain widgets to colours that land
cleanly on every supported e-ink panel,
no dithering surprises. That's the right default. Some widgets,
typically decorative or scenic ones, want a richer surface: a sunset
gradient on a weather card, layered cloud shapes, a deep night-sky
background. Those land via the renderer's Floyd-Steinberg dither pass,
which approximates arbitrary CSS colours as patterned dot-mixes of the
panel's actual palette.

To opt in, declare it in the manifest:

```json
{
  "design": {
    "palette": "extended"
  }
}
```

`strict` (the default) and `extended` are the only legal values.

What this changes:

- **For widget authors**: you can use any CSS colour, gradient, or
  shadow you want. The browser preview shows the literal CSS; the
  panel render shows the dithered approximation.
- **For catalog reviewers**: the manifest flag is the signal that a
  widget is taking on the dither-tradeoff responsibility. Soft scenery
  reads well, fine details (small text on a gradient, sharp icon edges
  over a transition) read worse than strict-palette equivalents.
  Reviewers should ask "does the dithered output read clearly at the
  target panel resolution?" rather than only checking the browser preview.
- **For device-side logic** (future): the host can later prefer strict
  widgets when assigning to a BW or 3-colour panel, since extended
  widgets degrade harder there. The manifest declaration makes this
  possible without re-parsing CSS at runtime.

What this **does not** change:

- Typography and spacing tokens (`--fs-display`, `--space-4`, etc.)
  stay mandatory. Cross-widget consistency in type + layout is what
  keeps a multi-widget dashboard from feeling chaotic.
- The capability layer is unaffected. Extended widgets still need a
  `requires:` block for any network egress, same as strict ones.

The reference implementation is
[`plugins/weather_now_scenic`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now_scenic),
a weather card with weather + time-of-day theming using gradients and
layered shape decorations.

---

## Tokens, the Spectra design system

Spectra has three token layers. Widgets paint from the upper two; never
from primitives. The full source is in
[`static/style/spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css);
this is the cheat sheet.

### 1. Primitives, theme-independent raw values on `:root`

Scales (don't redefine):

| Token group | Examples |
|---|---|
| Type scale | `--fs-display 3em` · `--fs-jumbo 2.5em` · `--fs-value 1.9em` · `--fs-lead 1.25em` · `--fs-body 1em` · `--fs-label 0.8em` · `--fs-caption 0.72em` |
| Weights | `--fw-regular 500` · `--fw-medium 600` · `--fw-semi 700` · `--fw-bold 800` · `--fw-black 900` |
| Spacing | `--space-1 .25em` … `--space-7 3em` |
| Strokes | `--stroke-1 2px` (edge floor) · `--stroke-2 3px` (data) · `--stroke-3 4px` |
| Radii | `--radius-0 0` · `--radius-1 2px` · `--radius-2 4px` |
| Icons | `--icon-sm 1.1em` · `--icon-md 1.5em` · `--icon-lg 2.2em` |
| Fluid base | `--w-font-base: clamp(14px, 7cqmin, 28px)` (every widget element sizes from this) |

### 2. Semantic, what the active theme provides

Set per-theme on `[data-theme="<id>"]`. These are what widgets read.

| Token | Role |
|---|---|
| `--bg` | panel background behind all widgets |
| `--surface` | widget container fill |
| `--surface-sunken` | zebra rows, chart tracks, calendar grid lines, chips |
| `--text-primary` / `--text-secondary` / `--text-muted` | text by emphasis |
| `--icon` | icon colour (defaults to `--text-primary`) |
| `--edge` | the single permitted outer container edge |
| `--accent-1` … `--accent-6` | 6 categorical roles (fixed per slot, hue varies per theme) |
| `--accent-1-soft` … `--accent-6-soft` | low-saturation tint companion for each accent |
| `--on-accent` | text/icon colour legible on an accent fill |
| `--font-family` | active style font (paints `.w` via the shell rule) |

The 6 accents have **fixed roles by position**:

| Slot | Role | Reach for it when… |
|---|---|---|
| `--accent-1` | alerts / peaks / "now" | error pills, current-hour highlight, urgent state |
| `--accent-2` | warnings / capacity / "winner" | yellow-flag warnings, near-full battery, trophy gold |
| `--accent-3` | positive / "up" | success state, uptrend, online |
| `--accent-4` | primary / today / live | main chart series, today's column, "live" tag |
| `--accent-5` | secondary series | second chart series, comparison data |
| `--accent-6` | third category | third series, supplementary tags |

In light theme the hues are terracotta / ochre / moss / teal / slate-blue
/ plum; in dark they shift; in Dracula they're red / peach / mint /
lavender / cyan / pink. The hexes change; the roles don't. **Reach by
role, not by hue.** "I want red" is the wrong instinct, pick the slot
whose meaning matches; the theme provides the colour.

### 3. Style-tunable, what the active style overrides

Defaults on `:root` (the "standard" look); styles override on
`[data-style="<id>"]`. Consume via `var(--name, fallback)`.

| Token | Default | Purpose |
|---|---|---|
| `--edge-weight` | `var(--stroke-1)` | outer shell border weight |
| `--zebra-bg` | `var(--surface-sunken)` | list row alternating fill (`transparent` = whitespace grouping) |
| `--list-pad-y` / `--list-gap` | `var(--space-2)` / `var(--space-1)` | list density |
| `--pill-radius` | `var(--radius-0)` | status pill corner (999px = stadium) |
| `--title-marker` | `none` | `block` shows an accent eyebrow tick before the title |
| `--title-rule-w` | `0px` | optional accent rule under the title bar |
| `--row-rule-w` | `0px` | optional divider between list rows |
| `--label-transform` | `uppercase` | label casing (Editorial uses `none`) |

Two axes summary:

```
data-theme  →  colour (semantic + accent tokens)
data-style  →  type / scale / density / shape (primitives + style-tunable)
```

Set globally on `<body>`, overridable per cell. Any theme × any style.
See [`widget-design-system.md`](widget-design-system.md) for the
cross-widget conventions on how to use them.

---

## Phosphor icons

Vendored locally under `/static/icons/phosphor/`. Six weights:

| weight   | usage                                            |
|----------|---------------------------------------------------|
| regular  | inline icons, small icons that need to flow with text |
| bold     | **default for prominent icons**, hero icons, big condition markers, anything that should "read big". Outline-with-presence rather than solid shape. |
| duotone  | two-tone accent moments (sun-horizon, moon-stars). Use sparingly. |
| fill     | **avoid in new widgets**, solid shapes can quantise into blobs on Spectra 6 and read heavier than they should. Bold reads cleaner. |
| light, thin | special design needs; rare. |

Make prominent icons **big and bold**, that's the design language. A
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
`ph-warning-circle`). Both carry the bare icon name, `ph-bold-cloud-sun`
as a single class does NOT exist and won't render.

Inside Shadow DOM you must load the weights you use:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  ...`;
```

Each `<link>` is ~10 KB CSS + a ~250 KB woff2 font. Only load the
weights you actually use, `regular` is almost always required;
`bold` is the next most useful; everything else is opt-in.

Icon name reference: <https://phosphoricons.com/>.

---

## Custom colours, escape hatch

The "always paint from semantic tokens" rule has a narrow carve-out:
when the data you're rendering has an **inherent visual identity that
the user expects to see**, you can hard-code hex values. Examples:

* **F1 team colours**, Ferrari `#E80020`, Mercedes `#27F4D2`, Red Bull
  `#3671C6`. Painting Ferrari in `--accent-1` reads wrong; the data
  carries its own colour. See [`plugins/f1_standings_drivers`](https://github.com/dmellok/tesserae/tree/main/plugins/f1_standings_drivers)
  for the canonical implementation (constructor → hex map, fallback to
  `var(--surface-sunken)` for unknown teams).
* **Brand logos and indicators**, Spotify green, GitHub black,
  particular calendar-tag colours.
* **Real-world flag colours**, country flags on race countdowns.
* **Established conventions**, gold / silver / bronze on podiums
  (though even those usually map cleanly to `--accent-2` / `--text-secondary` /
  `--accent-1`).

Bar to clear: the colour is **part of the data**, not a design choice.
"It would look nice in red" doesn't pass; "this is Ferrari's red" does.

Implementation:

```js
const TEAM_COLOURS = {
  ferrari:   "#E80020",
  mercedes:  "#27F4D2",
  red_bull:  "#3671C6",
  // ...
};

// Apply via inline style so it slots alongside semantic-token elements.
const team = TEAM_COLOURS[id] || "var(--surface-sunken)";
return `<span style="background:${team}">…</span>`;
```

Fall back to a semantic token (`--surface-sunken` for neutral chips,
`--accent-5` for unknown series) so the widget never produces a
blank / pure-black square.

Most brand colours read fine on every Spectra theme, the modern F1
liveries (Mercedes mint, McLaren papaya, Alpine pink) sit comfortably
on both light and dark surfaces. If a colour genuinely doesn't work
in dark themes, define both variants in the lookup and switch via the
body's `data-theme` attribute. Document the choice in the widget's
brief.

---

## Plugin static assets

Widgets can ship arbitrary static files alongside the source, useful
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

* **SVG preferred over raster**, scales cleanly, ditches well on
  Spectra 6, no woff2-weight cost.
* **Monochrome SVGs that pick up `currentColor`** if you want them to
  theme automatically: `<svg fill="currentColor" stroke="currentColor">`
  in your SVG file, then set `color: var(--text-primary)` (or any
  accent) on the parent.
* **Bake brand colours into the SVG** when the data IS the colour
  (team logo, flag).
* **Keep files small**, under 10 KB each ideally. The renderer waits
  for every cell's `<img>` to load (with a 5 s cap), so large remote
  images stretch the screenshot phase.
* **Phosphor first**, if there's a Phosphor icon for what you want,
  use that. Custom SVG is for things Phosphor doesn't have: circuit
  outlines, team logos, country flags.

The asset route is loopback-bypassed (same gate as `/compose/`), so
the Playwright renderer can fetch them without a session.

---

## Responsive sizing, container queries

The cell host has `container-type: size` set by the composer, so
`cqw` / `cqh` / `cqmin` units work inside your shadow:

```css
.hero-icon { font-size: clamp(48px, 18cqw, 144px); }
.label      { font-size: clamp(10px, 1.7cqw, 13px); }
```

* `cqw` = 1% of the cell's width (not the panel, not the viewport).
* `cqh` = 1% of the cell's height.
* `cqmin` = 1% of the cell's smaller side, useful for square-ish
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

### Hero + list layouts: don't let `auto` rows starve the hero

A common widget shape is **hero on top + bullet list below** (the
weather forecast, the holiday countdown, the F1 standings). The trap
is reaching for this grid:

```css
/* ⚠️ Looks right at lg, collapses the hero at md/sm */
.body { display: grid; grid-template-rows: minmax(0, 1fr) auto; }
```

The `auto` row claims its **content height first**, leaving whatever's
left for the `1fr` hero. At `lg` there's room for both; at `md` and
especially `sm` the list eats the hero and clips the headline number.
Two reliable patterns instead:

**Pattern 1, hero takes its natural size + list fills the rest** —
prefer this when the hero is a fixed-size stat / status block:

```css
.body { display: grid; grid-template-rows: auto minmax(0, 1fr); }
.list { overflow: hidden; }  /* let the list clip, not the hero */
```

**Pattern 2, both rows constrained + per-size row hiding** — prefer
this when the hero scales fluidly via `clamp()`:

```css
.body { display: grid; grid-template-rows: 1fr auto; gap: var(--space-3); }

/* Cap how many list rows are even rendered at smaller sizes so the
   `auto` row never claims more than it should. */
.size-sm .list-row:nth-child(n+3) { display: none; }
.size-md .list-row:nth-child(n+5) { display: none; }
```

Whichever pattern you pick, **walk every size in `/_test/render`
before declaring the widget done**. The lg cell is forgiving; sm and
xs aren't. The dev-server contact-sheet script renders all four
side-by-side for fast inspection:

```sh
python scripts/widget_contact_sheet.py <id>
```

A few related sizing rules of thumb:

- **At xs**, drop the entire list (`display: none`) and let the hero
  fill the cell. `xs` is roughly a single information point.
- **Title bars survive but compress.** The shared `.w-title` from
  `spectra-widgets.css` shrinks gracefully; you don't need to
  re-style it per size, but the title TEXT should be short enough to
  not need truncating at sm (overflow ellipsis is acceptable on lists,
  awkward on titles).
- **`clamp(min, fluid, max)` is the right tool, not media queries.**
  The cell can be any size between roughly 180×180 (xs) and 1200×800
  (lg); use `clamp(N, M·cqmin, P)` for type / icon scaling rather than
  branching on `.size-*`. Reserve the size classes for **structural**
  changes (dropping sections, swapping grid shape).
- **`minmax(0, 1fr)` keeps `<pre>` / `<code>` / long words from
  blowing out a column.** The default `1fr` minimum is "auto", which
  is content-derived; `minmax(0, 1fr)` lets the row actually shrink.

---

## Shared baseline, `spectra-widgets.css` (+ `spectra-tokens.css`, `spectra-styles.css`)

Every Spectra widget links
[`static/style/spectra-widgets.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css)
inside its shadow root. The stylesheet carries:

* the **shell**: `.w` → `.w-title` (optional) → `.w-body`
* the **archetype body classes**: `.stat-body`, `.list-body`,
  `.chart-body`, `.status-body`, `.cal-body`, `.wx-body`, `.img-body`,
  plus the supporting per-element classes (`.list-row`, `.status-cell`,
  `.pill`, `.chart-legend`, …)
* shared **helpers**: `.u-label`, `.u-muted`, `.u-row`, `.u-spread`,
  `.dot` (accent swatches)
* @container queries that trim secondary content at compact (`360px`)
  and tiny (`240px`) breakpoints

The link goes inside `shadow.innerHTML` so the class rules pierce the
shadow boundary (they don't otherwise):

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/style/spectra-widgets.css">
  <div class="w" data-widget="<id>">
    <div class="w-title">...</div>
    <div class="w-body status-body">...</div>
  </div>
`;
```

The Spectra token system itself lives in three sheets, all loaded at
the document level by `compose.html` and inherited into every shadow
root via the cascade:

| File | What it sets |
|---|---|
| [`spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css) | primitives + every bundled theme (`[data-theme="..."]`) |
| [`spectra-styles.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-styles.css) | 9 styles (`[data-style="..."]`) + @font-face for the vendored families |

Widgets don't link these, the document does it once. Custom
properties cascade through the shadow boundary, so widgets just read
`var(--accent-1)` / `var(--surface)` / etc. as if the tokens were
defined inside their own shadow.

---

## Per-cell content zoom, `--c-zoom`

Every cell host exposes a `--c-zoom` CSS variable (default `1`) that
the user can adjust via the page-editor zoom slider on each cell.
The composer applies it by shrinking `.cell-content` to `1/zoom` and
counter-scaling back up with `transform: scale(zoom)`. CSS units
inside the widget (`px`, `cqw`) are measured against the smaller
virtual viewport, so a `36px` height ends up rendering as `36 * zoom`
physical pixels.

For anything that must stay at a **fixed physical size** regardless of
the slider (title bars, hairlines, icon strips), counter-scale by
`var(--c-zoom, 1)`:

```css
.my-header { height: calc(36px / var(--c-zoom, 1)); }
.my-rule   { height: calc(1px  / var(--c-zoom, 1)); }
```

This is what `spectra-widgets.css` does for `.w-title`: the title's
`font-size` is a `calc(clamp(...) / var(--c-zoom, 1))` so the bar
stays at its natural physical pixel size at every zoom level.
Everything else (body text, hero icons, charts) should scale with the
slider, that's the whole point of the zoom.

`ctx.cell.size` does NOT change when zoom changes; the size token
reflects the cell's true dimensions on the panel. Zoom is a viewing
adjustment, not a layout one.

---

## Font cascade, `--font-family`

The active style sets `--font-family` on `<body>` (see
[`spectra-styles.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-styles.css)),
and that cascades into every shadow root. The `.w` shell already does
`font-family: var(--font-family)`, so most widgets get the right font
for free.

Overrides flow top-down:

| Source | What it sets |
|---|---|
| Page font picker | inline `--font-family` on body (overrides style font) |
| Per-cell font picker | inline `--font-family` on the cell (overrides page font) |
| Active style | `--font-family` via the `[data-style="..."]` rule |
| `:root` default | Helvetica Neue stack |

If a widget needs an exact display family (e.g. Archivo Black for
heavy headers that the picker won't provide), name the family first
and use `--font-family` as the fallback:

```css
.hero-stat { font-family: "Archivo Black", var(--font-family), sans-serif; }
```

**Critical anti-pattern.** Do NOT write:

```css
--font-family: 'Inter', var(--font-family, system-ui, sans-serif);
```

The RHS references the property being defined; CSS treats this as
invalid-at-computed-value-time and the property reverts to the
guaranteed-invalid sentinel. Every descendant `var(--font-family, …)`
falls through to its fallback, including the chart helpers, so
charts paint in Helvetica regardless of style. Use a non-recursive
fallback (`system-ui, sans-serif`) when overriding inline, or leave
the cascade alone and let the style set the family.

---

## E-ink considerations

The Spectra 6 / Waveshare E6 panel has **6 colours**: black, white,
yellow, red, blue, green.

* Use the Spectra semantic tokens, every theme's accent palette is
  tuned to quantise cleanly into the panel's 6-ink range. Don't sample
  colours outside the token set.
* **The widget shell carries a single outer edge** (`--edge-weight`
  solid `var(--edge)`). Beyond that, hierarchy comes from spacing,
  weight, and `var(--surface-sunken)` contrast, not internal borders.
  Hairline borders dither into invisibility on E6 anyway. Optional
  dividers (`--title-rule-w`, `--row-rule-w`) are style-driven and
  default to 0.
* Refresh time is ~25 s on the 13.3" panel. Static, dense layouts
  win; busy gradients quantise into noise.
* Tabular numerics align cleanly: `font-variant-numeric: tabular-nums`.
* Anti-aliased type works on Spectra 6 but won't survive heavy
  saturation tweaks. Stick to weight ≥ 500 for anything small.
* **No motion.** No `transition`, no `animation`, no `:hover` effects.
  E6 ghosts mid-refresh and the renderer screenshots
  `animations="disabled"` anyway.

---

## Reference: shipped widgets

Pattern reference for new widgets. Read each `client.js` to see how
the conventions land in practice.

**By archetype**, pick the closest information shape:

* `.stat-body`, [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now),
  [`plugins/ha_battery`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_battery)
* `.list-body`, [`plugins/news_hacker_news`](https://github.com/dmellok/tesserae/tree/main/plugins/news_hacker_news),
  [`plugins/ha_entities`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_entities),
  [`plugins/todo`](https://github.com/dmellok/tesserae/tree/main/plugins/todo)
* `.chart-body`, [`plugins/weather_hourly`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_hourly),
  [`plugins/ha_history`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_history),
  [`plugins/ha_energy`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_energy)
* `.status-body`, [`plugins/ha_climate`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_climate),
  [`plugins/clock_sunrise_sunset`](https://github.com/dmellok/tesserae/tree/main/plugins/clock_sunrise_sunset)
* `.cal-body`, [`plugins/calendar_day`](https://github.com/dmellok/tesserae/tree/main/plugins/calendar_day),
  [`plugins/calendar_week`](https://github.com/dmellok/tesserae/tree/main/plugins/calendar_week),
  [`plugins/calendar_month`](https://github.com/dmellok/tesserae/tree/main/plugins/calendar_month)
* `.wx-body`, [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now),
  [`plugins/weather_forecast`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_forecast)
* `.img-body`, [`plugins/picture_gallery`](https://github.com/dmellok/tesserae/tree/main/plugins/picture_gallery),
  [`plugins/picture_apod`](https://github.com/dmellok/tesserae/tree/main/plugins/picture_apod),
  [`plugins/ha_camera`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_camera)

Canonical patterns to lift:

* WMO-code → Phosphor icon lookup ([`weather_*`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_core))
* Chart helpers via `tokens()` probe ([`weather_hourly`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_hourly), [`ha_history`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_history))
* server.py disk-cache pattern ([any `weather_*`, `f1_*` widget])
* Family-shared module ([`f1_core`](https://github.com/dmellok/tesserae/tree/main/plugins/f1_core) → `getCircuit()` + `trackSvg()`)
* `--c-zoom`-aware title bar ([`spectra-widgets.css`'s `.w-title`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css))
* Team-colour stripe via inset box-shadow ([`f1_standings_drivers`](https://github.com/dmellok/tesserae/tree/main/plugins/f1_standings_drivers))

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

## Building the widget, checklist

1. `plugins/<id>/plugin.json`, manifest (start by copying from a
   widget in the same archetype, e.g. `plugins/weather_now/plugin.json`).
2. `plugins/<id>/client.js`, render function. Link
   `/static/style/spectra-widgets.css` first, then render the `.w`
   shell with one archetype body class. Use `ctx.cell.size` /
   `ctx.cell.options` / `ctx.data`. For charts, `import { tokens, … }
   from "../../static/spectra-chart.js"`.
3. `plugins/<id>/server.py`, optional, if you need server-side data.
4. `plugins/<id>/tests/test_smoke.py`, parametrised over sizes.

No `client.css` needed in most cases, Spectra's archetypes carry the
shell + body layout. Add a `client.css` only if your widget has rules
that don't belong in the shared stylesheet (e.g. one-off positioning,
SVG-specific tweaks).

Then hit `http://127.0.0.1:8765/_test/render?plugin=<id>&size=md` in
the browser to iterate. The Flask dev server auto-reloads on file
changes; refresh the page to see updates.

---

## What NOT to do

* Don't hard-code hex colours for theme-driven elements. Paint from
  Spectra semantic tokens (`var(--accent-1..6)`, `var(--surface)`,
  `var(--text-*)`). For canvas / Chart.js, use the `tokens()` probe
  from [`spectra-chart.js`](https://github.com/dmellok/tesserae/blob/main/static/spectra-chart.js)
  rather than naming hexes inline. Brand colours (F1 teams, Spotify
  green, etc.) are the documented carve-out, see "Custom colours" above.
* Don't reach into the parent document, you're sandboxed in a
  Shadow DOM. The composer expects that.
* Don't kick off intervals / animations / async work that finishes
  after your default-export resolves, the screenshot fires then.
* Don't load fonts. The composer's renderer already waits for
  `document.fonts.ready` and the page-level font is propagated as
  `ctx.font.family`. Setting `font-family: inherit` in `:host` is the
  right move.
* Don't fetch from your client.js. Use server.py, the renderer
  doesn't wait for arbitrary in-page fetches (only for declared
  `<img>` loads + fonts), so a client-side fetch will screenshot
  before its data arrives.
* Don't assume internet from server.py either, on the panel side -
  it's the Tesserae host that runs `fetch()`, and the panel may be
  reading the rendered .bin offline.
