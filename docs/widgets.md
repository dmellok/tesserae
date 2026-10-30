# Tesserae widget contract & design system

Drop-a-folder spec for building widgets. A widget is a `plugins/<id>/`
directory the loader picks up at boot; the composer mounts one per cell
into a Shadow DOM, hands it `(shadow, ctx)`, and Playwright screenshots
the result. The screenshot is then quantised to the panel's palette and
streamed over MQTT.

If you're designing a widget, this is the only doc you need. Everything
below is enforced by code; values come straight from the source.

> **New widgets ship through the catalog, not this repo.** The widgets in
> `plugins/` are the bundled set and stay where they are, but a new one
> belongs in [tesserae-widgets](https://github.com/dmellok/tesserae-widgets):
> publish it to a repo of your own, tag a release, and open a PR adding its
> `widgets.json` entry, and users install it from Settings → Widgets → Browse.
> The contract on this page is identical either way; the loader walks the
> bundled directory and the marketplace install directory and merges them, so
> `server.py`, `blueprint()` admin pages and `fetch()` all behave the same.
> Occasionally something generic enough gets adopted into the bundled set, but
> the catalog is the default answer. See that repo's CONTRIBUTING for the
> submission flow.

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
  "category": "weather",
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
* **Pictures on a `select`'s choices.** A choice may carry `image`, a
  path on the server (`/plugins/<plugin_id>/static/styles/poster.jpg`,
  served from the plugin's own `static/` folder) or an `https` URL. When
  any choice has one, the editor draws the option as a picture picker: a
  row with the chosen picture and name that opens a grid of every choice,
  with a filter box; choices without a picture sit under the grid as text
  buttons, and a choice's `group` heads a run of pictures. The saved value
  and `show_if` work as for a plain select. Keep the pictures small (a
  320x240 JPEG is plenty). Older servers ignore `image` and show the
  plain dropdown.
* **`secret` and `mask` on a cell option.** `secret: true` marks the
  value sensitive: it is stripped from the render context handed to the
  browser, and redacted on share to the catalog with an installer
  question suggested in its place. `mask: true` is the separate question
  of whether the editor renders the field as a password input, and
  defaults to whatever `secret` is. Set `mask: false` on a value that is
  sensitive but still has to be readable to maintain, a URL being the
  usual case: nobody can fix an endpoint they can't see (#237).
* **Careful with an option named `label`.** The host promotes the app-level
  Settings location into every widget's options and fills a blank `label`
  with the place name (see `_resolved_options` in `app/composer.py`). That's
  the intent for weather-style widgets; for anything else it means a cell
  titled "Melbourne" for no reason. Name the option something else
  (`title`) if it isn't a place.
* **`settings`**, plugin-wide knobs (one set across all cells using
  this widget). Surfaces in `/settings/plugins/<id>`. `secret: true`
  stores under `<name>_secret` in `settings.json` so an on-disk grep
  for `secret` reveals every sensitive value. `advanced: true` folds a
  setting into the card's "More options" section, as on cell options.
* **`icon`**, Phosphor name used in the editor's widget picker.
* **`name`**, shown in the widget picker. Widgets that belong to a family
  are named `Family, Item` ("Weather, Now", "GitHub, Streak"), and the
  picker shows the item in bold after the muted family. A widget on its own
  gets one plain Title Case name ("Webpage"). Don't leave the name as the
  widget's id.
* **`category`**, the subject the widget picker groups it under, one of
  `time`, `plans`, `weather`, `home-assistant`, `reading`, `art`, `places`,
  `media`, `sports`, `transport`, `dev`, `finance` or `utility`. Optional:
  without it the picker guesses from the family in the name and otherwise
  files the widget under Utility. Older servers ignore the field.
* **`render.needs_network`**, hint for the renderer (not enforced).
* **`render.per_placement`**, set `true` when what `fetch()` returns belongs
  to one placement: a payload fitted to the cell, or a rotation that keeps its
  own turn. Every widget's `ctx` carries `placement_id` (`page/cell@device`,
  or just the element id for a canvas shown outside a page; absent where the
  host has no page), which tells two identical cells apart where `cell_id`,
  built from the options, does not. With this flag the composer also stops
  sharing one canvas fetch, or one last-good fallback, between placements with
  the same options. Without it, nothing changes.
* **`updates.on_change`**, optional data-change sources this widget knows how
  to consume. Declaring one only exposes a host-owned, per-placement **Refresh
  when this widget's data changes** switch; it does not enable refreshes by
  default. `selector_option` may name one of the widget's `cell_options` to
  narrow the dependency to that placement's selected value (for example one
  published Reminders `list_id`). The selector must reference a declared
  option. Grid cells and Canvas widget elements store their opt-ins
  independently, so using the same widget on another dashboard is unaffected.

  ```json
  {
    "cell_options": [
      { "name": "list_id", "type": "select", "label": "Reminders list" }
    ],
    "updates": {
      "on_change": [
        { "source": "personal_data.reminders", "selector_option": "list_id" }
      ]
    }
  }
  ```
* **`updates.on_schedule`**, optional host-owned scheduled refresh kinds for
  time-dependent output. The first contract supports `{"kind":"daily"}`.
  Each Grid or Canvas placement remains off by default; when enabled without a
  custom time it becomes due at the server's local day boundary. A manifest may
  include `suggested_at` in `HH:MM` form, but that value only prefills the
  progressive Custom time control and is not persisted until the user chooses
  it. Scheduled refreshes update displays already showing the dashboard and
  silently re-warm inactive Lineup pages; they never select or advance one.
  The due/satisfied bookkeeping is in memory, matching Tesserae Schedules: a
  server restart after today's target can defer that placement until its next
  local-day target rather than backfilling immediately.

  ```json
  {
    "updates": {
      "on_schedule": [
        { "kind": "daily", "suggested_at": "07:00" }
      ]
    }
  }
  ```

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

### Fields another option fills, `fill_from`

An option can declare that another option owns its value. The editor looks
the controlling option's current value up in a map; a hit fills the field
and renders it read-only (still submitted, so the value survives a save).

```jsonc
{
  "name": "rt_url",
  "type": "string",
  "label": "Realtime URL",
  "fill_from": {
    "option": "preset",
    "map": { "mta_ace": "https://api-endpoint.mta.info/..." }
  }
}
```

The `gtfs` widget uses this for its feed presets: pick "NYC Subway (A/C/E)"
and the URL fields below fill in and lock, so a cell can't end up
pairing one agency's timetable with another's realtime feed. Resolution is
server-side, so the fields update when the page reloads after a save, not
the instant the select changes. A widget whose behaviour depends on the
filled value should re-derive it from the controlling option in `fetch()`,
the way `gtfs` does, rather than trusting what a single save persisted.

### Fields that only apply sometimes, `show_if`

An option can say when it is relevant. The editor shows it only while the
other options match, and hides it again the moment they stop matching, so
a widget with several modes doesn't confront everyone with every mode's
settings at once.

```jsonc
{ "name": "cluster_style", "type": "select", "label": "Cluster style",
  "show_if": { "layout": "cluster" } },                  // equals
{ "name": "art_style", "type": "select", "label": "Art style",
  "show_if": { "pictures": ["gen", "plate_gen"] } },     // any of
{ "name": "token", "type": "string", "label": "Token", "secret": true,
  "show_if": { "url": { "not": "" } } }                  // none of: here, "url is set"
```

- Every key in `show_if` must match. Keys are other option names.
- A value is an exact match, a list matches any of its entries, and
  `{"not": value}` or `{"not": [...]}` matches none of them.
- Values compare as text, so `true` matches a ticked switch and `"8"` matches
  `8`. A multiselect matches when any of its picks does.
- A controlling option with no saved value yet counts as its `default`.
- A field whose controlling option is hidden is hidden too. In the example,
  a `cluster_every` with `"show_if": {"cluster_style": "blend"}` disappears
  whenever the layout isn't `cluster`, without repeating that condition.
- Hiding is only visual. A hidden field keeps its value and is still saved,
  so switching the mode back brings the old setting with it. Your `fetch()`
  and `client.js` still receive every option, and should ignore the ones the
  current mode doesn't use, as they would without `show_if`.

`show_if` works in the grid editor and the canvas editor's Configure drawer,
and `get_widget_options` passes it through to agents. Servers older than
0.452 ignore the key and show every field, so it is safe to ship before your
users update. Its companion for fine-tuning is `advanced`, below.

### Fine-tuning under "More options", `advanced`

An option a typical user never needs to touch can carry `"advanced": true`.
The editor renders those options last, in manifest order, inside a collapsed
**More options** section at the end of the form.

```jsonc
{ "name": "refresh_minutes", "type": "number", "label": "Refresh",
  "default": 15, "advanced": true },
{ "name": "cluster_every", "type": "select", "label": "Every",
  "show_if": { "cluster_style": "blend" }, "advanced": true }
```

- The section starts closed. The editor remembers, per browser, whether it
  was last left open, and opens it by itself when a field inside fails
  validation or a link's `#` names one of its fields.
- `advanced` and `show_if` combine. An advanced option whose condition is
  false stays hidden inside the section, and the section itself hides when
  none of its options would show.
- As with `show_if`, folding is only visual: every option is still in the
  form, still saved, and still reaches `fetch()` and `client.js`.
- `settings` entries accept `advanced` the same way, on the widget's
  settings card.
- `get_widget_options` reports `"advanced": true`, so an agent knows the
  option is fine-tuning and normally leaves it at its default.

Servers older than 0.473 ignore the key and show the option in place, so
order the manifest the way it should read on either (see below).

### Keep the options panel short

A good options panel shows the handful of choices that decide what the
widget is, and gets everything else out of the way until it matters. Some
guidance for laying one out:

1. **Few options up front.** Aim for about five that are always visible (no
   `show_if`, no `advanced`). Where they apply, pick them in this order:
   where the data comes from (source, address, place, account, feed), what
   the widget shows (page, layout, mode), how many, pictures (when there is
   a picture choice), and one look option (paper, or the widget's main
   look). A widget with five options or fewer in total is already there.
2. **Nest what depends on a choice.** An option that only matters for one
   value of another gets `show_if`: connection details under their source,
   layout-specific options under their layout, AI painting options under the
   pictures choice.
3. **Put fine-tuning under More options.** Mark `advanced` the options most
   people never change: refresh interval, minimum confidence, tokens that
   are normally blank, title, title line, foot of the page, text size,
   typeface, panel type, sort order, label lines, marks, exclusions, AI
   model, scale, packing and the like.
4. **Short, plain labels.** Leave the context to `show_if` rather than
   prefixing a label with its mode ("Cluster: style"), and put explanations
   in `help`, not in a parenthesis in the label. Use the same words for the
   same thing across widgets: Typeface, Text size, Title, Title line, Foot of
   the page, Paper, Panel, Refresh, Place, Within, How many, Layout,
   Pictures, Style.
5. **Order the list as it reads.** Essentials first, each followed directly
   by the options it reveals, then the rest of the look, then the advanced
   options last. That keeps the panel sensible on a server that ignores
   `advanced`.
6. **Never break saved cells.** Tidying a panel must not rename or remove an
   option's `name`, change a choice's `value`, or change a `default`.
   Reorder, relabel, rewrite help, and add `show_if` or `advanced`; saved
   cells keep working exactly as before.

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

### Serving your own media, `RENDER_SAFE_ENDPOINTS`

Blueprint routes sit behind the password gate. A render happens over
loopback with no session, so a widget whose markup points at its own
plugin route — an `<img src>` handed to the panel, say — needs that
route reachable during the render, or every panel shows a broken image
while the browser preview looks fine.

A plugin declares such routes by name:

```python
# plugins/<id>/server.py
RENDER_SAFE_ENDPOINTS: tuple[str, ...] = (
    "picture_gallery_admin.serve_image",
    "picture_gallery_admin.serve_thumbnail",
)
```

The loader collects these into `app.config["RENDER_SAFE_ENDPOINTS"]` and
`app/auth.py` consults them. `picture_gallery` is the in-tree example.

**Read-only, always.** Anything that writes, deletes, or reaches the
network does not belong in this list. The routes found when this was
tightened were mutations — `shutil.rmtree` on a gallery folder, calendar
feed writes, CalDAV discovery against a caller-supplied URL — and those
stay gated. Reviewing the list is part of reviewing a plugin.

The same rule applies to routes you *don't* declare. A loopback `GET`
reaches any of your plugin's sub-routes, declared or not, so a destructive
action exposed over `GET` is reachable during a render. Keep mutations on
`POST`; every in-tree plugin does.

**You may not need it.** A loopback `GET`/`HEAD` of a plugin's own
sub-route already passes without a declaration. What that rule does not
cover, and a declaration does:

- the plugin **index** (`/plugins/<id>/`) stays gated even on a read, since
  it lists loader errors and plugin contents
- routes outside the `/plugins/<id>/…` prefix
- being explicit, so the requirement survives someone tightening the gate
  later

Declaring a route you serve media from is the safer default even where the
sub-route rule would carry it today.

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
  locale: "fr",            // active locale for this render, see "Locales & strings" below
  strings: { ... },        // this widget's resolved strings for `locale`, {} if untranslated
  t(key, fallback) { ... },// strings[key] ?? fallback ?? key
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

### Locales & strings

Optional. A widget with no `locales` in its manifest renders exactly as
before this existed: `ctx.locale` and `ctx.strings` are still present,
but `ctx.strings` is `{}`, so `ctx.t(key, fallback)` always returns
`fallback` (or `key` if you omit it). There's no reason to touch
`client.js` at all unless you want the widget's panel text to actually
translate.

To opt in:

```json
// plugin.json
"locales": ["en", "fr"]
```

```json
// strings/en.json
{ "sunrise": "Sunrise", "daylight": "Daylight" }
// strings/fr.json
{ "sunrise": "Lever du soleil", "daylight": "Durée du jour" }
```

```js
// client.js
label.textContent = ctx.t("sunrise", "Sunrise");
```

`ctx.locale` is resolved once per page render (every widget on a page
gets the same one) from, in order: the target device's own `locale`
(Settings → Devices), the app-wide `settings.app.locale`, the host's
own locale, else `"en"`. `ctx.strings` is the widget's own
`strings/<locale>.json`, falling back to the base language
(`fr-CA` → `fr`) and then to English if the exact tag isn't shipped.

Dates and numbers aren't part of this contract: use
`Intl.DateTimeFormat(ctx.locale, {...})` /
`Intl.NumberFormat(ctx.locale, {...})` directly rather than a hand-rolled
month/weekday table, so day names, ordering, and separators come from
the browser's own locale data instead of one you'd have to maintain.

Translating a widget is a normal contribution to that widget: a PR
adding `strings/<locale>.json` (bundled widgets) or a new catalog
release (marketplace widgets, installed the normal way from Browse).
There's no separate override mechanism, and no per-widget forking
required to add a language. The language pickers (Settings → Server,
Settings → Devices, and the dev preview) list whatever locales the
installed widgets ship, plus English, so a new `strings/<tag>.json`
becomes selectable as soon as the widget loads.

Every bundled widget ships on this contract. The bundled set carries
English, Czech, Dutch, French, German, Italian, Lithuanian, Norwegian
Bokmål, Polish, Portuguese, Slovak, Spanish, Swedish, and Chinese
(Simplified `zh-CN` and Traditional `zh-TW`), chosen from where installs
report from; a wording fix in any of them is a one-file PR against
`plugins/<id>/strings/<tag>.json`, and `tests/test_widget_i18n_contract.py`
requires every translation to include the English base keys. A locale may
also add grammatical variants of an existing key: the plural suffixes
`_zero`, `_one`, `_two`, `_few`, `_many`, `_other`, or case suffixes
`_genitive` (word clock) and `_accusative` (countdown). Variants remain
flat string values.

Widgets with count labels can import `withPluralForms` from
`../../static/widget-i18n.js` and wrap `ctx.t` with the active locale.
The resulting `t(key, fallback, count)` selects an optional variant using
`Intl.PluralRules`, then falls back to the base translation. For example,
Polish `events_few: "wydarzenia"` handles 2–4 and 22–24, while
`events: "wydarzeń"` handles 0, 5 and 12–14. Existing singular keys
continue to handle 1. The word clock supports `{this_genitive}` and
`{next_genitive}` in its five-minute templates, with hour words falling
back to their usual forms when no genitive translation is provided.

To eyeball a translation without changing any settings, the dev preview
surfaces (`/_test/widgets` and `/_test/preview`) carry a language picker;
it just adds `?locale=<tag>` to the render, which is also handy on a raw
`/_test/render?plugin=<id>&locale=fr` URL.

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

### Helper modules beside `server.py`

A widget can ship more Python than one file. Everything in the plugin
folder is installed (the catalog installer extracts the whole tarball),
so a helper module or a small vendored library sits beside `server.py`
and travels with it.

The catch is that nothing imports it for you. The host loads
`server.py` by file path under a synthetic module name; the plugin
folder is neither a package nor on `sys.path`, so `import gcal` and
`from . import gcal` both fail. Load siblings by path, under a module
name that is unique to your widget:

```python
# plugins/<id>/server.py
import importlib.util
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"_tesserae_plugins.<id>.{name}", _HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gcal = _load_sibling("gcal")


def fetch(options, settings, *, ctx):
    return gcal.events(settings["calendar_id"], settings["token"])
```

Adding the plugin folder to `sys.path` also works, but `sys.path` is
process-wide: a helper called `utils` or `google` then shadows, or is
shadowed by, the same name from another widget or a real installed
package. The unique module name above sidesteps that.

* The network allowlist wraps the whole `fetch()` call, so code in a
  helper is held to the same `requires:` declarations as `server.py`.
* Vendored third-party code needs its licence file in the folder and
  gets read in review like everything else. A single module is fine;
  a whole client library and its dependency tree is not, and the
  usual answer there is a few `urllib` calls against the REST API
  instead. Widgets cannot declare pip dependencies.

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

### Background jobs, the progress tray

A widget that works in the background (painting pictures, syncing photos, an
import) can show it in the job tray at the bottom right of every admin page.
The tray survives navigating between pages, animates the bar, and offers a Stop
button when the job can be stopped. Report from `server.py`:

```python
try:
    from app import jobs
except ImportError:  # an older Tesserae
    jobs = None

if jobs:
    jobs.report("my_widget", "sync", "Syncing photos",
                progress=0.4, detail="40 of 100",
                label="My Widget", href="/plugins/my_widget/",
                stop=cancel)             # progress 0..1, or None for "working"
...
if jobs:
    jobs.finish("my_widget", "sync", "100 photos")   # or jobs.fail(...) / jobs.cancel(...)
```

A job is named by `(source, key)`, so calling `report` again updates the same
card and nothing needs carrying about. `stop` is optional; with it the card
gets a Stop button that calls it. A finished card stays for a few seconds, a
failed one for 25, and a job nobody has reported on for 15 minutes is taken to
have died and is dropped. Calls are cheap and thread-safe, so report from the
worker thread as often as the progress moves. Guard the import and any call so
the widget still runs on a Tesserae without the tray.

### Live previews while work is on the way

A widget whose background work will change what it draws (pictures still being
painted, a cut-out still running) can say so in its data:

```python
return {"title": "...", "live_refresh_s": 6}   # 2 to 120; 0 or absent when nothing is pending
```

While that field is set, the page editor's preview and the Panels editor ask
for the widget's data again after that many seconds and repaint if it changed,
so the cell fills in as the work lands instead of waiting for the next edit.
Polling stops as soon as a fetch comes back without the field, when the cell
leaves the page, and while the tab is hidden. A Tesserae without this ignores
the field, so it is safe to always send. Keep `fetch()` cheap enough to run
every few seconds, and never start paid work from a fetch more often than the
widget's own limits allow.

### Work ahead of a scheduled send

A scheduled send renders the page the moment it fires, and `fetch()` has a few
seconds. A widget whose work takes longer than that (an AI painting is ten to
forty seconds) can ask to be run ahead of time:

```json
"render": {"warm_ahead_s": 150}
```

Before each projected send of a page carrying such a widget (a schedule, a
rotation step, the page's own refresh cadence), the server runs the page's
widgets as the send will, with `ctx["preview"]` false, that many seconds early,
and keeps running them every ten to thirty seconds until the page stops
sending `live_refresh_s`. Nothing is sent to a panel by a warm. The number in
the manifest is only the first guess: the server learns from each warm. Say
how much work is pending alongside the hint:

```python
return {..., "live_refresh_s": 6, "live_pending": 3}   # three paintings still to land
```

and the server works out seconds per item from how long the page took to
settle against the count it started with, keeps a typical count per send, and
sizes the next lead from the two with a margin (30 s to 15 min): a page that
paints three aircraft at fifteen seconds each is warmed about a minute ahead,
one that paints ten gets three. Without `live_pending` the page is learned as
a whole. A warm that runs out of lead asks for more next time.

So `fetch()` must start the work it needs when `ctx["preview"]` is false and
report `live_refresh_s` (and `live_pending`) while any of it is pending; those
are what the warm acts on. A send that still catches work in flight is followed up:
the push pipeline sends the page again once the hint clears.

## Pictures a widget makes and keeps: `app.artwork`

A widget that paints with fal.ai (or makes any slow, paid picture) does not
carry a queue, a budget, a hold rule or an admin page of its own. It declares
a collection once, with the function that makes one picture, and asks for
pictures by key:

```python
from app import artwork

ART = artwork.Collection("specimen_sheet", "paintings", make=paint,
                         label="Specimen Sheet", title="Painting specimens",
                         facet="style", facet_label=lambda s: STYLES[s][0])

def paint(job: artwork.Job) -> tuple[bytes, dict]:
    prompt = prompt_for(job.payload)                          # the widget's own words
    raw = job.fal.generate("nano_banana", prompt, refs=[job.payload["photo"]])
    return raw, {"credit": "AI painting (Nano Banana on fal.ai)", "style": job.payload["style"]}

def fetch(options, settings, *, ctx):
    art = ART.open(ctx, settings, subject=subject_id(options))
    for row in rows:
        key = f"{slug(row['name'])}--{style}--p3"
        got = art.request(key, payload={"style": style, "photo": row["photo"]}, title=row["name"])
        if got.ready:
            row["pic"] = art.url(key, px=400, levels=options.get("panel", "colour"), cut=False)
    art.finish(ctx, facet=style)
    return {..., "notices": art.notes(), "status": art.status(), **art.live()}
```

What the host does with that:

- **Store.** `<data_dir>/<name>/<key>.jpg` (or `.png`), its `.json` credit and
  the payload it was made from, `.err` with why it failed, `archive/` of
  earlier versions, `derived/` of resized and panel-converted copies.
  `art.url(key, px=, levels=, cut=)` hands back a data URI, cached per size
  and panel; `cut=True` keeps transparency for a cut-out.
- **Budget.** US cents at the models' listed prices, per collection per
  day, from the widget's `gen_daily_usd` setting (an older `gen_daily_cap`
  count still counts at the typical price). `job.fal.generate` charges it
  before each call; a spent day answers `capped`.
- **The queue.** Worker threads (`gen_parallel`), one job per key, persisted
  so a restart resumes what was waiting. Stop one or all from the tray or
  the admin page.
- **Hold.** A preview (the editors, the dashboard list) is not settled:
  `ctx["settled"]` is false, the picture is held and the tray shows "N
  waiting, Paint now". A push, a warm-ahead or an MCP render is settled and
  starts what its page needs. `ctx["cell_id"]` identifies the placement; on
  an older host without either field the collection falls back to a settle
  timer.
- **What the dashboards asked for.** A panel render records its page's keys
  with the payloads, so the admin page groups pictures by dashboard with a
  tab per facet and "Render now" paints exactly what the panels are missing.
- **The admin page** at `/plugins/<id>/artwork/<name>/`: spend and budget,
  Render now, Paint the held, Purge, a live ticker, the grid, and per picture
  Repaint, Delete, Clear error and earlier versions. Add your own per-picture
  action with `ART.action("recut", "Cut out again", fn)`.
- **The tray and the editors.** `art.live()` returns `live_refresh_s` and
  `live_pending`, so the editors poll while work is on the way and a
  warm-ahead learns the page's pace.

A widget that uses this needs Tesserae 0.463 or later: say so with
`"tesserae_min": "0.463.0"` in `plugin.json` and on the catalog entry, which
keeps a host at or above 0.463 from installing a version it cannot run.
Older hosts ignore the field, so guard the import and fall back to what the
widget can draw without paintings, with a notice that says what it is
missing: the Overhead and Specimen Sheet widgets show the shape.

`make` runs on a worker thread. Raise `artwork.ArtworkError("why")` for a
failure worth keeping (no reference photo, say); `fal.FalError` from the client
is kept the same way. `app.fal` holds the model table (`fal.MODELS`,
`fal.EDIT_MODELS`) and resolves the key: the widget's `fal_api_key` setting,
else the app's key under Settings, else `FAL_KEY` in the environment.

### The server's ceiling and ledger

Every charge a collection makes is also written to the server's ledger
(`<data root>/artwork/ledger.jsonl`: when, which widget and collection,
which model, how many US cents). Settings › Generative AI shows it: to-day
and the month, a strip of the last thirty days, each widget's spend with a
link to its pictures page, and the month by widget and model.

The same page holds the fal.ai key every widget without one of its own
uses (`fal.resolve_key` finds it after the widget's own `fal_api_key`),
and two ceilings over every collection together, US dollars a day and a
month. When either is reached, `Art.request` answers `capped` for every
collection, `room()` is 0 and a queued job is noted as failed with the
reason, exactly as a widget's own budget behaves; a widget's budget still
applies inside the ceiling. A widget needs nothing for this: it comes with
`app.artwork`.

The page also takes an OpenAI key (`app.openai.api_key`, else
`OPENAI_API_KEY`). With one, a `gpt_image` call goes straight to OpenAI's
Images API instead of through fal.ai, and is charged and logged as
`gpt_image_openai` at OpenAI's price. With an OpenAI key and no fal.ai key,
`fal.painting_key` answers `fal.DIRECT_ONLY`, every gate that asks for a key
passes, and every edit or text-to-picture call goes to GPT Image on OpenAI
whatever model the widget chose; a background cut-out (`birefnet`) still
needs fal.ai.

A Gemini key (`app.gemini.api_key`, else `GEMINI_API_KEY` or
`GOOGLE_API_KEY`) does the same for Nano Banana: `nano_banana`,
`nano_banana_pro` and `nano_banana_t2i` go straight to Google's Gemini API
and are charged and logged as `nano_banana_gemini`,
`nano_banana_pro_gemini` and `nano_banana_gemini_t2i`. With no fal.ai key,
any other edit or text-to-picture call goes to Nano Banana on Google when
there is a Gemini key, else to GPT Image on OpenAI; `gpt_image` still goes to
OpenAI when both keys are set. A widget calls `job.fal.generate` exactly as
before; reference pictures that are URLs are fetched through
`app.net_guard`.

### Pictures from Wikimedia Commons: `app.commons`

A widget that paints a real thing wants a free picture of it to paint from.
The host looks them up on Wikimedia Commons so no widget carries its own copy
(Tesserae 0.465 and later; guard the import as for `app.artwork`):

```python
from app import commons

pic = commons.file("File:HST-SM4.jpeg")                 # by title
pic = commons.portrait("IMO 9458054", name="Maersk Elba", ident="9458054", descend="(ship")
raw = commons.fetch(pic)                                 # the thumbnail's bytes, kept on disk
ref = commons.data_uri(pic)                              # inline, for a painter that cannot fetch Commons
credit = commons.credit(pic, painted_by="Nano Banana")   # "painted by … from a picture by … (Wikimedia Commons, CC BY-SA 4.0)"
```

A picture is `{title, src, link, by, lic, w, h}`, the author and licence read
off the file. `portrait` ranks a category's files by name (an identifier such
as an IMO number first, then the subject's name, never a plaque, a cabin, a
model or a visit), asks for the best dozen, and picks a landscape picture of
a good size; when the category holds only a subcategory (Commons files a ship
under `Category:IMO <n>` → `Category:<Name> (ship, <year>)`) it reads that.
`category(name)` gives a category's files and subcategories; `search(query)`
is for a tool that builds a curated table, not for a page at render time.

Everything is kept under `<data root>/commons/` (a hit a month, a miss a
week, bytes three months), requests go one at a time with a small gap, and
each carries the host's User-Agent with the contact set under Settings ›
Generative AI, as Wikimedia asks. Credit the author and the licence on the
page, as CC BY and CC BY-SA ask; a painting made from the picture is a
derivative, and anyone who publishes one should say so.

### A send waits for the pictures

A push of a page with a widget that declares `render.warm_ahead_s` first
runs the page as a push would (nothing sent) and, while the page carries a
refresh hint, runs it again after that wait, up to five minutes in all
(`push.SETTLE_MAX_S`), so the panel paints the finished page once. Manual
sends, schedules, rotations and webhooks all wait; follow-ups and warms do
not. The History row says how long the send waited and for how many pieces
of work. Past the cap the page goes as it is and the follow-up pushes
finish it.

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
plugin asset route serves anything matching those prefixes, and nothing
else beside `client.js` and `client.css`: a second module that `client.js`
imports (`import { pack } from './static/pack.js'`) has to live under one of
them too, or the browser answers "Failed to fetch dynamically imported
module" for the whole widget.

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
* `.list-body`, [`plugins/gtfs`](https://github.com/dmellok/tesserae/tree/main/plugins/gtfs),
  [`plugins/news_hacker_news`](https://github.com/dmellok/tesserae/tree/main/plugins/news_hacker_news),
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
