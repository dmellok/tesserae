# Tesserae widget contract & design system

A widget is a `plugins/<id>/` folder loaded at startup.
The composer puts one widget in each cell's Shadow DOM, an isolated part of
the page, and passes it `(shadow, ctx)`.
Playwright takes a screenshot, which is reduced to the panel's colours and sent over MQTT.

> Publish new widgets through [tesserae-widgets](https://github.com/dmellok/tesserae-widgets).
> Put the widget in your own repo, tag a release, then open a PR adding its
> `widgets.json` entry. Users install it from Settings → Widgets → Browse catalog.
>
> Bundled widgets stay in `plugins/`. Generic widgets may occasionally join that set.
> Both locations use the same contract: the loader merges them, and
> `server.py`, `blueprint()` admin pages and `fetch()` behave the same.
> See the catalog repo's CONTRIBUTING for submission details.

---

## Cell sizes (test-render fixtures)

The composer uses these dimensions for a single-widget render at
`/_test/render?plugin=<id>&size=<size>`. Real dashboards can place a
widget at any (w, h). The longer side determines its size token:

| size | dims      | trigger (longer side) | typical role                  |
|------|-----------|-----------------------|-------------------------------|
| xs   | 180×180   | ≤ 200 px              | tiny tile (icon + 1 datum)    |
| sm   | 380×240   | ≤ 400 px              | small card (icon + 2-3 fields)|
| md   | 640×400   | ≤ 700 px              | half-panel widget             |
| lg   | 1200×800  | > 700 px              | full-panel feature            |

Source: [`app/composer.py`](https://github.com/dmellok/tesserae/blob/main/app/composer.py) → `SIZE_DIMENSIONS`,
[`static/composer.js`](https://github.com/dmellok/tesserae/blob/main/static/composer.js) → `SIZE_THRESHOLDS`.

Design at all four sizes. The widget should hide non-essential sections at `xs`/`sm`.
Read the token from `ctx.cell.size` and add `.size-xs` / `.size-sm` to your root element.

---

## Real panel dimensions

Sizes are native landscape unless noted. The panel orientation setting swaps the axes for portrait mounting.

| preset            | native landscape | notes                              |
|-------------------|-----------------|--------------------------------------|
| `inky_4`          | 640×400         | Spectra 6, Pimoroni Inky Impression 4" |
| `inky_5_7`        | 600×448         | 7-colour legacy                       |
| `inky_7_3`        | 800×480         | Spectra 6                             |
| `inky_13_3`       | 1600×1200       | Spectra 6 (Pimoroni)                  |
| `waveshare_e6_7_5`| 800×480         | Waveshare E6                          |
| `waveshare_photopainter_7_3` | 800×480 | Spectra 6, Waveshare 7.3" PhotoPainter (ESP32-S3) |
| `waveshare_e6_13_3` | 1200×1600 | Spectra 6 (ESP32), **portrait-native** |
| `waveshare_42_bw` | 400×300 | 4.2" black/white, 1-bpp |

Source: [`app/panel.py`](https://github.com/dmellok/tesserae/blob/main/app/panel.py) → `PANEL_PRESETS`.

Most users put 1 to 4 widgets on a panel. On an `inky_13_3` portrait panel:

- One widget fills 1200×1600.
- Two widgets get about 1200×800 each.
- Four widgets get about 600×800 each.

---

## Plugin folder

```
plugins/<id>/
  plugin.json        manifest (required)
  client.js          ES module, default export = render fn (required)
  client.css         widget styles (optional; no bundled widget ships one)
  server.py          server-side data fetch (optional)
  tests/test_smoke.py smoke test (optional but recommended)
```

`<id>` is the folder name, URL slug and disk path.
The loader skips folders starting with `.` or `_`.
Lowercase `[a-z0-9_]` is a convention, not a requirement. Name it
`<family>_<role>`, e.g. `weather_now`, `weather_hourly`.

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

* `supports.sizes` lists the test-render sizes you support.
    Omit unsupported sizes. The editor skips your widget on cells that exceed them.

* `cell_options` defines one editor field per option, with values in `ctx.cell.options` at render time.
    Types: `string`, `textarea`, `number`, `slider`,
    `select` (needs `choices` OR `choices_from`), `multiselect` (same),
    `boolean`, `color`, `location_search`, plus the special-purpose
    `variables_textarea` and `entity_overrides`.

    Don't ship a `variant` option for visual direction. Page-level `data-style` controls shapes across widgets.
    For layout choices such as `stack` vs `side`, use an option named `layout` with values that describe the shape.

* A `select` choice may carry `image`: a server path or an `https` URL.
    For example, `/plugins/<plugin_id>/static/styles/poster.jpg` comes from the plugin's `static/` folder.

    If any choice has an image, the editor shows a picture picker.
    The selected picture and name open a grid with a filter box.
    A choice's `group` labels a run of pictures; choices without images appear as text buttons below.

    Saved values and `show_if` work as for a plain select.
    Keep images small; a 320x240 JPEG is plenty.
    Older servers ignore `image` and show a dropdown.

* Use `aliases` on a `select` choice when renaming or merging values.
    Put old values on their replacement: `{"value": "cluster", "label": "Cluster",
  "aliases": ["packed", "scatter"]}`.

    An old value selects the replacement instead of showing "Unavailable: packed".
    `show_if` also uses the replacement, and the next save writes its current value.
    Until then, `fetch` receives the old value, so map it there too.

    Older servers ignore `aliases` and show the old value as unavailable.

* For a `textarea`, `presets` accepts a list of `{label, value}` entries.
    These appear in an Examples dropdown. Picking one fills the field with an editable value.

* `secret` and `mask` control sensitive cell options.
    With `secret: true`, the value is removed from the browser's render context.
    Catalog shares redact it and suggest an installer question in its place.

    `mask: true` makes the editor use a password input; its default follows `secret`.
    Set `mask: false` when a sensitive value must remain readable, such as a URL someone needs to fix (#237).

* The host copies the app-level Settings location into every widget's options.
    A blank `label` is filled with its place name.
    See `_resolved_options` in `app/composer.py`.

    This suits weather widgets, but can give unrelated cells the title "Melbourne".
    Use another name, such as `title`, if the option isn't a place.

* `settings` defines plugin-wide options shared by all cells using this widget, shown at `/settings/plugins/<id>`.
    `secret: true` stores a value under `<name>_secret` in `settings.json`; searching for `secret` finds sensitive values.
    `advanced: true` puts a setting under "More options", as for cell options.

* `icon` is the Phosphor name used in the widget picker.

* `name` appears in the widget picker. Use `Family, Item` for families ("Weather, Now", "GitHub, Streak").
    The picker shows the family muted and the item bold.

    Standalone widgets get a plain Title Case name, such as "Webpage". Don't use the widget's id.

* `category` groups the widget by subject. Values are
    `time`, `plans`, `weather`, `home-assistant`, `reading`, `art`, `places`,
    `media`, `sports`, `transport`, `dev`, `finance` or `utility`.

    Without it, the picker guesses from the family name, then falls back to Utility.
    Older servers ignore the field.

* `render.needs_network` is a renderer hint, not enforced.

* Set `render.per_placement` to `true` when `fetch()` returns data specific to one placement.
    Examples include cell-sized payloads and rotations with separate positions.
    The flag stops the composer sharing canvas fetches or last-good fallbacks between placements with identical options.

    Every widget's `ctx` carries `placement_id` to distinguish identical cells; `cell_id` is built from options.
    Its value is `page/cell@device`, or the element id for a canvas outside a page.
    It is absent when the host has no page.

    Without the flag, sharing is unchanged.

* Set `render.full_bleed` to `true` to remove outer page padding where a cell touches the panel wall.
    `picture_gallery` and `picture_apod` use it.
    Gaps between cells remain, and per-cell padding overrides win.

* `fragments` names widget pieces users can place separately in the Panels canvas editor, such as a weather widget's temperature.
    Each entry needs an `id` and `label`; optional fields are `icon` and default `w` / `h` in pixels.
    `render()` receives the chosen id as `ctx.fragment`; `"full"` is reserved for the whole widget.

    See [Writing a widget](dev/writing-a-widget.md).

* `data_schema` describes `fetch()`'s result so the Panels canvas editor can bind its fields as data sources.
    Required `fields` holds one `{name, type}` per exposed result key.
    Each `type` is `num`, `str` or `arr`, with optional `label` and `unit`.

    `sample` supplies preview values and render fallbacks while a source isn't configured.
    `color` tints the widget's card in the Data panel.

* `lineups` controls how the Lineups page steps through items.
    `pin_option` names the cell option the host sets to an item's key to render that item.
    `server.py` provides `lineup_items(options)`, a list of `{key, label, version?}`.

    Use `draw: true` when output changes between renders and items have no keys.
    The arrows then redraw the dashboard. See
    [Writing a widget](dev/writing-a-widget.md).

* `updates.on_change` declares optional data-change sources the widget can use.
    It exposes the host's per-placement Refresh when this widget's data changes switch, off by default.

    `selector_option` may name a `cell_options` entry to limit the dependency to that placement's selection, such as a Reminders `list_id`.
    The selector must reference a declared option.
    Each Grid cell and Canvas widget element stores its opt-in separately, including across dashboards.

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

* `updates.on_schedule` declares host-scheduled refreshes for time-dependent output; `{"kind":"daily"}` is supported.
    Each Grid or Canvas placement is off by default.
    Without a custom time, enabled placements become due at the server's local day boundary.

    A manifest may provide `suggested_at` as `HH:MM` to prefill Custom time.
    It is saved only when the user chooses it.
    Refreshes update displays showing the dashboard and re-warm inactive Lineup pages; they never select or advance a page.

    Due/satisfied state stays in memory, as with Tesserae Schedules.
    Restarting after today's target can defer a placement until the next local-day target, without an immediate catch-up.

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

For `select` / `multiselect` options, use `choices_from` to load choices when editing.
This replaces a static `choices` array.
Set `choices_from: "<key>"`; the host calls this plugin's `server.py:choices(name)` with `name = "<key>"`:

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

Return a list of `{value, label}` dicts. The host catches and logs errors, then shows an empty dropdown.
Prefer returning `[]` over raising.

The host calls `choices()` on the manifest's plugin, never a sibling.
For a companion `_core` plugin, expose `choices()` on the widget and delegate through the plugin registry:

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

Examples:

- Bundled calendars: `calendar_*` → `calendar_core`.
- Catalog monitoring bundle: `glances_status` → `glances_core`.
- Dev Reference Bundle: `devref_card` → `devref_core`. Install it from the catalog for a complete worked example.

### Fields another option fills, `fill_from`

Use one option's value to fill another through a map.
A match fills the field and makes it read-only. The field is still submitted and saved.

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

In `gtfs`, choosing "NYC Subway (A/C/E)" fills and locks the feed URLs.
This prevents mixing one agency's timetable with another's realtime feed.
The server resolves the fields after saving and reloading; changing the select alone does not update them.

If your widget depends on the filled value, it should derive it again from the controlling option in `fetch()`.
Follow `gtfs`; don't rely on the value from a single save.

### Fields that only apply sometimes, `show_if`

Show an option only when other options match its conditions.
The editor hides it as soon as they stop matching.

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
- A field is hidden when its controlling option is hidden.
    A `cluster_every` with `"show_if": {"cluster_style": "blend"}` also disappears outside the `cluster` layout.
    You don't need to repeat the layout condition.
- Hidden fields keep their values and are still saved. Switching back restores the old setting.
    Your `fetch()` and `client.js` receive every option and should ignore those the current mode doesn't use, as without `show_if`.

`show_if` works in the grid editor and the canvas editor's Configure drawer.
`get_widget_options` passes it to agents.
Servers older than 0.452 ignore it and show every field, so users can update later.

### Fine-tuning under "More options", `advanced`

Mark rarely changed options with `"advanced": true`.
The editor puts them last, in manifest order, in a collapsed More options section.

```jsonc
{ "name": "refresh_minutes", "type": "number", "label": "Refresh",
  "default": 15, "advanced": true },
{ "name": "cluster_every", "type": "select", "label": "Every",
  "show_if": { "cluster_style": "blend" }, "advanced": true }
```

- The section starts closed; each browser remembers its last state.
    It opens when a field inside fails validation or a link's `#` names one of its fields.
- `advanced` and `show_if` work together. False conditions hide an option, and the section hides when no options are visible.
- Collapsing is visual, as with `show_if`. Every option remains in the form, is saved, and reaches `fetch()` and `client.js`.
- `settings` entries also accept `advanced` on the widget's settings card.
- `get_widget_options` reports `"advanced": true`. Agents normally leave these options at their defaults.

Servers older than 0.473 ignore the key and show options in place. Keep the manifest readable in both cases.

### Keep the options panel short

1. Aim for about five always-visible options, without `show_if` or `advanced`.
    Where relevant, use this order:
    - Data source: source, address, place, account or feed.
    - Content: page, layout or mode.
    - How many.
    - Pictures, if there is a picture choice.
    - One main look option, such as paper.

    Widgets with five options or fewer already meet this aim.

2. Use `show_if` for dependent options: connection details under their source, layout settings under their layout, AI painting under pictures.

3. Put rarely changed options under More options with `advanced`.
    Examples: refresh interval, minimum confidence, normally blank tokens, title, title line, foot of the page, text size,
    typeface, panel type, sort order, label lines, marks, exclusions, AI model, scale and packing.

4. Keep labels short. Use `show_if` for context instead of mode prefixes like "Cluster: style".
    Put explanations in `help`, outside the label.

    Use consistent names: Typeface, Text size, Title, Title line, Foot of the page, Paper, Panel,
    Refresh, Place, Within, How many, Layout, Pictures, Style.

5. Put essentials first, each followed by the options it reveals, then other look options, then `advanced` options.
    This also works on servers that ignore the key.

6. Tidying must not rename or remove an option's `name`, change a choice's `value`, or change a `default`.
    You can reorder, relabel, rewrite help, and add `show_if` or `advanced` without breaking saved cells.

### Companion `_core` plugins

A family with shared state, such as saved instances, API keys or user-edited lists, uses two plugin folders:

* `<family>_core`: `kind: "data"`, no widget. Exposes an admin page
    via `server.py:blueprint()` and a `choices()` function the
    display widgets delegate to.
* `<family>_<view>`: `kind: "widget"`. Reads from the core via the
    plugin registry, both at edit time (`choices_from`) and at render
    time (`server.py:fetch`).

Bundled examples are `calendar_core` + `calendar_*`, `weather_core` + `weather_*`, and `ha_core` + `ha_*`.
Catalog examples:

- `glances_core` + `glances_status`
- `github_core` + `github_*`
- `spotify_core` + `spotify_*`
- `f1_core` + `f1_*`

List every family folder in the catalog entry's `folders` array.
The installer puts them beside each other under `data/marketplace/`.

### Query previews

Image widgets can let users preview which images their filters match. Set
`"query_preview": true` in the manifest and export
`query_preview(options, settings, *, ctx)` from `server.py`.

The grid editor and the canvas source drawer then show **Preview query**. The
host runs the current unsaved options through the normal form parser and calls
the hook under the widget's declared capabilities. `settings` holds the runtime
settings; `ctx` holds `data_dir`, `cell_w`, `cell_h`, `preview=True` and
`settled=False`. A preview must not advance a display's cursor or repeat history.

Return `{"images": [...], "total": 42}`. Each image has:

- an HTTPS `url`, plus optional HTTPS `thumbnail` and `permalink`
- `width` and `height`
- optional `username` or `prompt`
- `matches`: text badges that explain why the image qualifies

The host shows at most 20 images in a keyboard-accessible popup. Use `error`,
`warning` and `stale` to explain incomplete or cached results. The endpoint is
an authenticated POST to `/plugins/<id>/query-preview`, and agents can call it too.

### Admin pages, `blueprint()`

A widget or `_core` plugin can export `server.py:blueprint()` to get a `/plugins/<id>/` admin page.
Return a Flask Blueprint with your routes. The host adds the plugin's URL prefix.

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

Put templates under `templates/<plugin_id>/` to avoid collisions with the host's `templates/index.html`.
Extend `_base.html` to match the admin UI. Wrap content with the `card_head` macro and `card` class.

### Serving your own media, `RENDER_SAFE_ENDPOINTS`

Blueprint routes require a password. Rendering uses loopback, a connection to the same host, without a login session.
Your plugin's media routes, such as an `<img src>`, must allow those requests.
Otherwise panels show broken images even when the browser preview works.

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

Only declare read-only routes. Anything that writes, deletes, or accesses the network must stay password-protected.
This includes `shutil.rmtree` on a gallery folder, calendar feed writes, and CalDAV discovery against caller-supplied URLs.
Review this list when reviewing a plugin.

A loopback `GET` can reach any of your plugin's sub-routes, declared or not.
Keep mutations, such as writes and deletes, on `POST`, as every bundled plugin does.
A destructive `GET` is reachable during rendering.

Loopback `GET`/`HEAD` requests to plugin sub-routes already pass without a declaration. A declaration also covers:

- the plugin index (`/plugins/<id>/`), which stays gated even on a read, since
    it lists loader errors and plugin contents
- routes outside the `/plugins/<id>/…` prefix
- keeping access explicit if the password rules become stricter later

Declare media routes by default, even when the sub-route rule already allows them.

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

`ctx` has no `theme` block of hex strings. Spectra widgets use inherited CSS custom properties.
Chart helpers read the active palette through `tokens(shadow.host)`, a hidden probe.
Canvas can then read resolved values from `t.accent1` / `t.surface` / `t.fontFamily`.

For widgets declaring `fragments`, `ctx.fragment` (also `ctx.cell.fragment`) names the piece to draw.
`"full"` means the whole widget and is used for every grid cell.

- The renderer may call you twice on a slow reload. Overwrite `shadow.innerHTML`; don't append.
- `async` is allowed. The renderer awaits your default export before taking the screenshot.
    Keep all work in that Promise; work outside it may still be pending when the screenshot fires.
- Disable Chart.js animations, CSS transitions and requestAnimationFrame loops. The output is a still image.
- If `ctx.data?.error` is set (server.py raised),
    render a small error card with `ph-warning-circle` and the message.

### Locales & strings

Translation is optional. Without manifest `locales`, `ctx.locale` is present, `ctx.strings` is `{}`, and rendering is unchanged.
`ctx.t(key, fallback)` returns `fallback`, or `key` if omitted.

Only change `client.js` if you want translated panel text.

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

Every widget on a page gets the same `ctx.locale`, chosen once per render in this order:

1. The device's `locale` (Settings → Devices).
2. App-wide `settings.app.locale`.
3. The host's locale.
4. `"en"`.

`ctx.strings` comes from the widget's `strings/<locale>.json`.
Missing tags fall back to the base language (`fr-CA` → `fr`), then English.

For dates and numbers, use `Intl.DateTimeFormat(ctx.locale, {...})` /
`Intl.NumberFormat(ctx.locale, {...})` directly.
The browser supplies day names, ordering and separators; don't maintain your own month/weekday table.

To add a language, contribute `strings/<locale>.json` to the widget:

- Bundled widgets: open a PR.
- Marketplace widgets: publish a catalog release, installed through Browse catalog.

No fork is needed; there is no separate override mechanism.
Settings → Server, Settings → Devices and the dev preview list installed widget languages plus English.
A new `strings/<tag>.json` becomes selectable when the widget loads.

Every bundled widget follows this contract. Bundled languages are
English, Czech, Dutch, French, German, Italian, Lithuanian, Norwegian
Bokmål, Polish, Portuguese, Slovak, Spanish, Swedish, and Chinese
(Simplified `zh-CN` and Traditional `zh-TW`), based on where installs report from.

Fix wording with a one-file PR to `plugins/<id>/strings/<tag>.json`.
`tests/test_widget_i18n_contract.py` requires every translation to include the English base keys.
A locale may add grammatical variants of existing keys as flat strings:

- Plurals: `_zero`, `_one`, `_two`, `_few`, `_many`, `_other`.
- Cases: `_genitive` for the word clock, `_accusative` for countdowns.

For count labels, import `withPluralForms` from `../../static/widget-i18n.js` and wrap `ctx.t` with the active locale.
The resulting `t(key, fallback, count)` uses `Intl.PluralRules` to choose a variant, then falls back to the base translation.

In Polish, `events_few: "wydarzenia"` handles 2 to 4 and 22 to 24.
`events: "wydarzeń"` handles 0, 5 and 12 to 14. Existing singular keys handle 1.

The word clock accepts `{this_genitive}` and `{next_genitive}` in five-minute templates.
Without a genitive translation, hour words use their usual forms.

Preview translations with the language picker at `/_test/widgets` or `/_test/preview`.
It adds `?locale=<tag>` without changing settings.
You can also use `/_test/render?plugin=<id>&locale=fr` directly.

---

## `server.py` contract (optional)

Use server-side code for API calls, file reads or cross-origin fetches the browser cannot make.
Its JSON-serialised output goes into `ctx.data`.

```python
# plugins/<id>/server.py

def fetch(options: dict, settings: dict, *, ctx: dict) -> dict:
    """
    options: cell_options for THIS cell (defaults merged in).
    settings: plugin settings (secrets are real values, not masked).
    ctx: {"panel_w": int, "panel_h": int, "cell_w": int, "cell_h": int,
          "preview": bool, "data_dir": str, ...}

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

* Additional `ctx` fields:
    - `home_lat` / `home_lon`: Settings location, `0.0` when unset.
    - `cell_id` and `settled`: see `app.artwork` below.
    - `placement_id` and `page_name` / `page_icon`: present when the host knows the page.
    - `fresh: True`: set on `?fresh=1` renders to request bypassing the widget's `data_dir` cache.

* Network calls: use `urllib.request` with a timeout + a `User-Agent`
    header naming the widget (e.g. `tesserae/0.1 (+weather_now)`).

* Don't raise; return `{"error": "..."}`. The host catches uncaught exceptions, but diagnostics are less clear.

* Put readable messages in `error`; users see the text directly in the cell.
    `"HTTPError: 404 Not Found"` only says the widget broke.
    `"Country code 'XX' is not supported."` tells the user what to fix.

    Catch errors you can explain, such as invalid input, an upstream outage or a rate limit.
    For other errors, use a fallback such as `"Couldn't load <thing> right now."`.

### Helper modules beside `server.py`

Put helper modules or small vendored libraries beside `server.py`.
The catalog installer extracts the whole tarball, including these files.

The host loads `server.py` by path under a generated module name.
The folder is neither a package nor on `sys.path`, so `import gcal` and `from . import gcal` fail.
Load sibling files by path with a module name unique to your widget:

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

Adding the folder to `sys.path` works, but changes `sys.path` for the whole process.
Names such as `utils` or `google` can then collide with another widget or installed package.
Use the unique module name above to avoid this.

* The network allowlist covers all of `fetch()`, including helpers. The same `requires:` declarations apply as in `server.py`.
* Include the licence file for vendored code; reviewers read it like other code.
    A single module is fine. A whole client library with dependencies is not; use a few `urllib` REST calls instead.

    Widgets cannot declare pip dependencies.

---

## Capabilities, `requires:`

A widget can declare needed capabilities in its manifest's `requires:` block.
The host checks outgoing network connections at the socket layer.
An undeclared host gets `CapabilityDenied` before a connection is made.

Settings and filesystem entries are review-only in v1. Their declarations make the access visible to reviewers.

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

Widgets that declare `plugin:ha_core` may use the Home Assistant client
through that delegate. Tesserae grants only the hostname in the current
Home Assistant Core `base_url`; it does not grant access to other hosts or
require a direct `network:` declaration on the widget.

### How the network enforcement works

The host wraps `socket.create_connection` and `socket.socket.connect`.
These cover `urllib`, `requests`, `httpx` and the sync path of `aiohttp`.

During a widget's `fetch()`, each connection is checked against its `network:` allowlist.
The hook does nothing outside that capability context, such as in host code.

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

Checks use the hostname, so hardcoding an IP such as `1.2.3.4` cannot bypass the allowlist.
Direct `socket.socket().connect()` calls are covered too.

### Backward compatibility

Widgets without a `requires:` block load without enforcement.
Catalog review asks for `requires:` on every new submission.
Bundled widgets and catalog entries from before #2 keep working unchanged.

### Threat model — what this catches and what it doesn't

A widget trying to POST your MQTT password to an undeclared host gets `CapabilityDenied`, logged by the server.
Reviewers still need to read the manifest and code; the host checks the manifest's declared access.

This cannot stop a determined attacker. Python code can bypass the hook with `ctypes`, frame inspection or a subprocess.
The hook adds a best-effort check to PR review; it cannot replace review.

Issue #3 covers real isolation through subprocess + seccomp, or WASM.
Until then, trust depends on catalog review and declared capabilities.

Worked examples in the bundled tree:

- [`plugins/weather_now`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now):
    single-host network declaration: `network:api.open-meteo.com`.
- [`plugins/clock_sunrise_sunset`](https://github.com/dmellok/tesserae/tree/main/plugins/clock_sunrise_sunset):
    same shape, second target on the same upstream.
- [`plugins/clock_analog`](https://github.com/dmellok/tesserae/tree/main/plugins/clock_analog):
    `requires: []` explicitly claims no capabilities.

---

### Background jobs, the progress tray

Show background work, such as painting, photo sync or imports, in the job tray at the bottom right of admin pages.
It stays visible across page changes, animates progress and provides Stop when supported.
Report from `server.py`:

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

Jobs use `(source, key)` as their identity. Calling `report` again updates the same card; no job handle is needed.
Optional `stop` adds a Stop button that calls it.

- Finished cards stay for a few seconds.
- Failed cards stay for 25 seconds.
- Jobs with no report for 15 minutes are dropped as dead.

Calls are cheap and thread-safe. Report from the worker thread whenever progress changes.
Guard the import and every call so older Tesserae hosts without the tray still work.

### Live previews while work is on the way

If background work will change the image, report that in the widget's data:

```python
return {"title": "...", "live_refresh_s": 6}   # 2 to 120; 0 or absent when nothing is pending
```

The page editor's preview and Panels editor fetch data again after the given number of seconds.
They redraw when it changes, without waiting for an edit.

Polling stops when a fetch omits the field, the cell leaves the page, or the tab is hidden.
Older hosts ignore the field, so it is safe to send.
Keep `fetch()` cheap enough to run every few seconds, and never start paid work more often than the widget's limits allow.

### Work ahead of a scheduled send

A scheduled send gives `fetch()` a few seconds to render.
For slower work, such as an AI painting taking ten to forty seconds, request an early run:

```json
"render": {"warm_ahead_s": 150}
```

Before a schedule, rotation step or page refresh, the server runs the page's widgets early by that many seconds.
This warm-up uses the send's settings, with `ctx["preview"]` false, and sends nothing to the panel.
It repeats every ten to thirty seconds until the page stops sending `live_refresh_s`.

The manifest value is a starting estimate; the server learns from each warm-up.
Include the number of pending items:

```python
return {..., "live_refresh_s": 6, "live_pending": 3}   # three paintings still to land
```

The server divides settling time by the starting item count to estimate seconds per item.
It tracks a typical count per send and adds a margin to the next lead time, within 30 s to 15 min.

Three aircraft paintings at fifteen seconds each get about a minute's lead; ten get about three minutes.
Without `live_pending`, the server learns the page's total time.
If a warm-up runs out of time, the next gets more lead.

`fetch()` must start its work when `ctx["preview"]` is false and report `live_refresh_s` (and `live_pending`) while work is pending.
The warm-up uses those hints. If a send catches unfinished work, the push pipeline sends the page again once the hint clears.

## Pictures a widget makes and keeps: `app.artwork`

For fal.ai or other slow, paid pictures, declare a collection with a function that makes one picture.
Request pictures by key. The host handles the queue, budget, hold rules and admin page:

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

- Storage includes `<data_dir>/<name>/<key>.jpg` (or `.png`), `.json` credits and the source payload.
    Failures go in `.err`, earlier versions in `archive/`, and resized or panel-converted copies in `derived/`.
    `art.url(key, px=, levels=, cut=)` returns a data URI cached by size and panel; `cut=True` preserves transparency.
- Daily budgets are per collection, in US cents at listed model prices, from `gen_daily_usd`.
    Legacy `gen_daily_cap` counts use the typical price.
    `job.fal.generate` charges before each call; an exhausted budget returns `capped`.
- The queue runs one job per key on worker threads (`gen_parallel`).
    Waiting jobs survive restarts. Stop one or all from the tray or admin page.
- Editor and dashboard-list previews have `ctx["settled"]` false, so pictures wait; the tray shows "N waiting, Paint now".
    Pushes, warm-aheads and MCP renders are settled and start the needed pictures.
    `ctx["cell_id"]` identifies the placement; older hosts missing either field use a settle timer.
- Panel renders record the page's keys and payloads.
    The admin page groups pictures by dashboard with a tab per facet.
    Render now paints the pictures panels are missing.
- `/plugins/<id>/artwork/<name>/` shows spend, budget, a live ticker and picture grid, with Render now, Paint the held and Purge.
    Each picture has Repaint, Delete, Clear error and earlier versions.
    Add actions with `ART.action("recut", "Cut out again", fn)`.
- `art.live()` returns `live_refresh_s` and `live_pending` for editor polling and learned warm-ahead timing.

This requires Tesserae 0.463 or later.
Set `"tesserae_min": "0.463.0"` in `plugin.json` and the catalog entry; hosts at or above 0.463 enforce it.
Older hosts ignore it: guard the import, draw without paintings and show a notice explaining what's missing.

Overhead and Specimen Sheet are examples.

`make` runs on a worker thread. Raise `artwork.ArtworkError("why")` to save a failure, such as a missing reference photo.
Client `fal.FalError` failures are saved too.

`app.fal` holds model tables `fal.MODELS`, `fal.EDIT_MODELS` and resolves keys in order:

1. Widget `fal_api_key`.
2. App key under Settings.
3. Environment `FAL_KEY`.

### The server's ceiling and ledger

The server records every charge in `<data root>/artwork/ledger.jsonl` with time, widget, collection, model and US cents.
Settings › Generative AI shows:

- Today's and this month's spend.
- The last thirty days.
- Each widget's spend, linked to its pictures page.
- Monthly totals by widget and model.

The page also stores a shared fal.ai key; `fal.resolve_key` checks it after the widget's `fal_api_key`.
Set daily and monthly limits in US dollars across all collections.
Each widget's own budget still applies within these limits.

At either limit, every collection's `Art.request` returns `capped`, `room()` is 0, and queued jobs fail with the reason.
This is included in `app.artwork`; widgets need no extra code.

Set an OpenAI key with `app.openai.api_key` or environment `OPENAI_API_KEY` as fallback.
Then `gpt_image` calls OpenAI's Images API directly, charged at OpenAI's price and logged as `gpt_image_openai`.

With an OpenAI key and no fal.ai key, `fal.painting_key` returns `fal.DIRECT_ONLY` and all key checks pass.
Every edit or text-to-picture call then uses GPT Image, regardless of the widget's chosen model.
Background cut-outs (`birefnet`) still need fal.ai.

For Gemini, use `app.gemini.api_key`, falling back to `GEMINI_API_KEY` or `GOOGLE_API_KEY`.
These models call Google's Gemini API directly, with charges logged under the matching names:

- `nano_banana`: `nano_banana_gemini`.
- `nano_banana_pro`: `nano_banana_pro_gemini`.
- `nano_banana_t2i`: `nano_banana_gemini_t2i`.

Without a fal.ai key, other edits and text-to-picture calls use Nano Banana if a Gemini key exists, otherwise GPT Image.
`gpt_image` still uses OpenAI when both keys are set.
Widgets keep calling `job.fal.generate`; reference-image URLs are fetched through `app.net_guard`.

### Pictures from Wikimedia Commons: `app.commons`

Use the host's Wikimedia Commons lookup for reference pictures instead of copying it into each widget.
It requires Tesserae 0.465 or later; guard the import as for `app.artwork`:

```python
from app import commons

pic = commons.file("File:HST-SM4.jpeg")                 # by title
pic = commons.portrait("IMO 9458054", name="Maersk Elba", ident="9458054", descend="(ship")
raw = commons.fetch(pic)                                 # the thumbnail's bytes, kept on disk
ref = commons.data_uri(pic)                              # inline, for a painter that cannot fetch Commons
credit = commons.credit(pic, painted_by="Nano Banana")   # "painted by … from a picture by … (Wikimedia Commons, CC BY-SA 4.0)"
```

A picture has `{title, src, link, by, lic, w, h}`, with author and licence read from the file.
`portrait` ranks category files by identifier (such as an IMO number), then subject name.
It excludes plaques, cabins, models and visits, checks the best dozen, and selects a suitably sized landscape picture.

If a category holds only a subcategory, it follows that, such as `Category:IMO <n>` → `Category:<Name> (ship, <year>)`.
`category(name)` returns files and subcategories.
Use `search(query)` for tools that build curated tables, not during page rendering.

The cache is under `<data root>/commons/`: hits last a month, misses a week, and image bytes three months.
Requests run one at a time with a short gap.
Each uses the host's User-Agent and the contact from Settings › Generative AI, as Wikimedia requires.

Credit the author and licence on the page, as CC BY and CC BY-SA require.
Paintings made from these pictures are derivatives; anyone publishing one should say so.

### A send waits for the pictures

For widgets declaring `render.warm_ahead_s`, a push first renders the page without sending it.
While a refresh hint remains, it repeats after the hinted wait, up to five minutes total (`push.SETTLE_MAX_S`).
This lets the panel draw the finished page once.

Manual sends, schedules, rotations and webhooks wait; follow-ups and warm-ups do not.
History shows the wait time and number of pending jobs.
At the cap, the page sends as it is; follow-up pushes finish it.

## `design.palette`, opting out of strict colour tokens

Use Spectra colour tokens (`--accent-1` through `--accent-6` and their `-soft` companions) by default.
They map cleanly to every supported e-ink panel.

Decorative widgets may need gradients, layered clouds or night-sky backgrounds.
Floyd-Steinberg dithering approximates these CSS colours with patterns of dots from the panel's palette.

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

- Authors can use any CSS colour, gradient or shadow.
    Browser previews show the CSS colours; panel renders show their dithered approximation.
- Authors accept the dithering tradeoff. Soft scenery reads well; small text on gradients and sharp icon edges read worse than strict-palette versions.
    Reviewers should check readability in the dithered output at the target resolution, beyond the browser preview.
- The flag could later let the host prefer strict widgets on BW or 3-colour panels without parsing CSS again.
    Extended widgets lose more detail on these panels.

What this does not change:

- Typography and spacing tokens (`--fs-display`, `--space-4`, etc.)
    stay mandatory for consistent type and layout across widgets.
- The capability layer is unaffected. Extended widgets still need a
    `requires:` block for any network egress, same as strict ones.

See
[`plugins/weather_now_scenic`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now_scenic),
a reference weather card with gradients and layered shapes based on weather and time of day.

---

## Tokens, the Spectra design system

Spectra has three token layers. Use the upper two; never draw from primitives.
Source:
[`static/style/spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css).

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

The 6 accents have fixed roles by position:

| Slot | Role | Reach for it when… |
|---|---|---|
| `--accent-1` | alerts / peaks / "now" | error pills, current-hour highlight, urgent state |
| `--accent-2` | warnings / capacity / "winner" | yellow-flag warnings, near-full battery, trophy gold |
| `--accent-3` | positive / "up" | success state, uptrend, online |
| `--accent-4` | primary / today / live | main chart series, today's column, "live" tag |
| `--accent-5` | secondary series | second chart series, comparison data |
| `--accent-6` | third category | third series, supplementary tags |

Light uses terracotta / ochre / moss / teal / slate-blue / plum; dark shifts the hues.
Nord uses Aurora red / yellow / green / frost cyan / frost blue / purple.
Choose a slot by its meaning; the theme sets its colour.

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

```
data-theme  →  colour (semantic + accent tokens)
data-style  →  type / scale / density / shape (primitives + style-tunable)
```

Set both on `<body>` or override per cell. Any theme works with any style.
See [`widget-design-system.md`](widget-design-system.md) for usage across widgets.

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

Use big, bold icons for prominent information.
At clamp(72px, 20cqw, 160px), `ph-bold` stays clear; `ph-fill` becomes a blob.

Markup:

```html
<i class="ph ph-cloud-sun" aria-hidden="true"></i>
<i class="ph-bold ph-warning-circle" aria-hidden="true"></i>
<i class="ph-duotone ph-sun-horizon" aria-hidden="true"></i>
```

Use a weight class (`ph-bold`, `ph-fill`, `ph-duotone`) plus an icon class (`ph-cloud-sun`, `ph-warning-circle`).
`ph-bold-cloud-sun` does NOT exist as a single class and won't render.

Inside Shadow DOM you must load the weights you use:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/icons/phosphor/regular/style.css">
  <link rel="stylesheet" href="/static/icons/phosphor/bold/style.css">
  ...`;
```

Each `<link>` loads about 80 KB of CSS (230 KB for duotone) plus a ~150 KB woff2 font.
Load only the weights you use.
`spectra-widgets.css` already imports `bold`, used by bundled widgets; other weights are opt-in.

Icon name reference: <https://phosphoricons.com/>.

---

## Custom colours, escape hatch

You can hard-code hex colours when the colour is part of the data's expected identity:

* F1 teams: Ferrari `#E80020`, Mercedes `#27F4D2`, Red Bull `#3671C6`.
    Don't use `--accent-1` for Ferrari's colour.
    See [`f1_standings_drivers`](https://github.com/dmellok/tesserae-widget-f1/tree/main/f1_standings_drivers) in the catalog F1 bundle for a constructor-to-hex map with `var(--surface-sunken)` for unknown teams.
* Brand logos and indicators: Spotify green, GitHub black,
    particular calendar-tag colours.
* Flags: country colours on race countdowns.
* Established conventions: gold / silver / bronze on podiums
    (though even those usually map cleanly to `--accent-2` / `--text-secondary` /
    `--accent-1`).

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

Most brand colours work across Spectra themes, including Mercedes mint, McLaren papaya and Alpine pink on light and dark surfaces.
If a colour fails in dark themes, define both variants and switch using the body's `data-theme` attribute.
Document the choice in the widget's brief.

---

## Plugin static assets

Ship static files for images Phosphor lacks, such as race tracks, team logos, flags or calendar-service logos.

Put files under `plugins/<id>/static/` or `plugins/<id>/files/`.
The asset route serves those folders, plus `client.js` and `client.css`.

Extra modules imported by `client.js`, such as `import { pack } from './static/pack.js'`, must also use those folders.
Otherwise the whole widget fails with "Failed to fetch dynamically imported module".

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

* Prefer SVG: it scales cleanly, dithers well on Spectra 6 and needs no woff2 font.
* For automatic theming, use `currentColor` in monochrome SVGs: `<svg fill="currentColor" stroke="currentColor">`.
    Set `color: var(--text-primary)` or an accent on the parent.
* Embed brand colours when colour is part of the data, such as logos or flags.
* Aim for under 10 KB per file. The renderer waits up to 5 s for every cell's `<img>` to load.
    Large remote images slow screenshots.
* Use Phosphor whenever it has the icon. Reserve custom SVGs for missing shapes such as circuits, logos and flags.

Like `/compose/`, the asset route allows loopback requests without a login session so Playwright can fetch files.

---

## Responsive sizing, container queries

The composer sets `container-type: size` on the cell host.
Use `cqw` / `cqh` / `cqmin` units inside the shadow root:

```css
.hero-icon { font-size: clamp(48px, 18cqw, 144px); }
.label      { font-size: clamp(10px, 1.7cqw, 13px); }
```

* `cqw` = 1% of the cell's width, independent of panel or viewport width.
* `cqh` = 1% of the cell's height.
* `cqmin` = 1% of the cell's smaller side, useful for square-ish
    scaling.

Use `.size-xs` / `.size-sm` / `.size-md` / `.size-lg` classes for layout changes, such as hiding sections at xs:

```js
shadow.innerHTML = `<div class="root size-${ctx.cell.size}">...</div>`;
```

```css
.size-xs .stats, .size-xs .sun { display: none; }
.size-xs .now-icon              { font-size: clamp(44px, 22cqw, 80px); }
```

### Hero + list layouts: don't let `auto` rows starve the hero

Weather forecasts, holiday countdowns and F1 standings often put a large main value above a list.
Avoid this grid:

```css
/* ⚠️ Looks right at lg, collapses the hero at md/sm */
.body { display: grid; grid-template-rows: minmax(0, 1fr) auto; }
```

The `auto` list takes its full height first, leaving the rest for the `1fr` hero.
This fits at `lg`, but clips the main number at `md` and especially `sm`.

For a fixed-size stat or status block, let the hero take its natural height and the list fill the rest:

```css
.body { display: grid; grid-template-rows: auto minmax(0, 1fr); }
.list { overflow: hidden; }  /* let the list clip, not the hero */
```

For a hero that scales with `clamp()`, constrain both rows and hide list rows at smaller sizes:

```css
.body { display: grid; grid-template-rows: 1fr auto; gap: var(--space-3); }

/* Cap how many list rows are even rendered at smaller sizes so the
   `auto` row never claims more than it should. */
.size-sm .list-row:nth-child(n+3) { display: none; }
.size-md .list-row:nth-child(n+5) { display: none; }
```

Check every size in `/_test/render`, especially sm and xs, before finishing.
This script renders all four sizes side by side:

```sh
python scripts/widget_contact_sheet.py <id>
```

Sizing rules:

- At xs, hide the list with `display: none` and let the hero fill the cell.
    `xs` fits roughly one piece of information.
- Shared `.w-title` from `spectra-widgets.css` shrinks without per-size styling.
    Titles should fit at sm without truncation. Ellipsis is acceptable in lists, but awkward in titles.
- Use `clamp(min, fluid, max)` instead of media queries.
    Cells can be any size from roughly 180×180 (xs) to 1200×800 (lg).
    Scale type and icons with `clamp(N, M·cqmin, P)`; reserve `.size-*` for hiding sections or changing grid shape.
- Use `minmax(0, 1fr)` to stop `<pre>` / `<code>` / long words widening columns.
    The default `1fr` minimum is content-derived "auto"; `minmax(0, 1fr)` allows shrinking.

---

## Shared baseline, `spectra-widgets.css` (+ `spectra-tokens.css`, `spectra-styles.css`)

Every Spectra widget links
[`static/style/spectra-widgets.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css)
inside its shadow root. It provides:

* the shell: `.w` → `.w-title` (optional) → `.w-body`
* layout body classes: `.stat-body`, `.list-body`,
    `.chart-body`, `.status-body`, `.cal-body`, `.wx-body`, `.img-body`,
    plus the supporting per-element classes (`.list-row`, `.status-cell`,
    `.pill`, `.chart-legend`, …)
* helpers: `.u-label`, `.u-muted`, `.u-row`, `.u-spread`,
    `.dot` (accent swatches)
* @container queries that trim secondary content at compact (`360px`)
    and tiny (`240px`) breakpoints

Put the link inside `shadow.innerHTML`; class rules outside the shadow root cannot reach its contents:

```js
shadow.innerHTML = `
  <link rel="stylesheet" href="/static/style/spectra-widgets.css">
  <div class="w" data-widget="<id>">
    <div class="w-title">...</div>
    <div class="w-body status-body">...</div>
  </div>
`;
```

`compose.html` loads both token stylesheets at document level.
Their custom properties are inherited by every shadow root:

| File | What it sets |
|---|---|
| [`spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css) | primitives + every bundled theme (`[data-theme="..."]`) |
| [`spectra-styles.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-styles.css) | 9 styles (`[data-style="..."]`) + @font-face for the vendored families |

Don't link these again in widgets.
Read inherited properties such as `var(--accent-1)` / `var(--surface)` directly.

---

## Per-cell content zoom, `--c-zoom`

The page editor's per-cell zoom slider controls `--c-zoom`, default `1`, on the cell host.
The composer shrinks `.cell-content` to `1/zoom`, then scales it up with `transform: scale(zoom)`.

Widget units (`px`, `cqw`) measure this smaller virtual viewport.
A `36px` height therefore draws at `36 * zoom` physical pixels.

For elements that must keep a fixed physical size, divide by `var(--c-zoom, 1)`.
This suits title bars, hairlines and icon strips:

```css
.my-header { height: calc(36px / var(--c-zoom, 1)); }
.my-rule   { height: calc(1px  / var(--c-zoom, 1)); }
```

`spectra-widgets.css` does this for `.w-title` with `font-size` set to `calc(clamp(...) / var(--c-zoom, 1))`.
The bar keeps its physical size at every zoom level.
Body text, hero icons and charts should scale with the slider.

`ctx.cell.w`, `ctx.cell.h` and `ctx.cell.size` also use the virtual viewport.
The composer divides panel dimensions by zoom before choosing the size token.
A 640×400 cell at zoom 2 reports 320×200 and `sm`.

---

## Font cascade, `--font-family`

The active style sets `--font-family` on `<body>` through
[`spectra-styles.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-styles.css).
Every shadow root inherits it.
The `.w` shell uses `font-family: var(--font-family)`, so most widgets need no font rule.

Overrides flow top-down:

| Source | What it sets |
|---|---|
| Page font picker | inline `--font-family` on body (overrides style font) |
| Per-cell font picker | inline `--font-family` on the cell (overrides page font) |
| Active style | `--font-family` via the `[data-style="..."]` rule |
| `:root` default | Helvetica Neue stack |

For a font outside the picker, such as Archivo Black for heavy headers, name it first with `--font-family` as fallback:

```css
.hero-stat { font-family: "Archivo Black", var(--font-family), sans-serif; }
```

Do NOT write:

```css
--font-family: 'Inter', var(--font-family, system-ui, sans-serif);
```

This references the property it is defining, so CSS treats it as invalid at computed-value time.
The property becomes the guaranteed-invalid sentinel: every descendant `var(--font-family, …)` uses its fallback.
Charts then draw in Helvetica regardless of style.

Use a non-recursive fallback (`system-ui, sans-serif`) for inline overrides, or let the style set the family.

---

## E-ink considerations

The Spectra 6 / Waveshare E6 panel has 6 colours: black, white,
yellow, red, blue, green.

* Use the Spectra semantic tokens: every theme's accent palette is
    tuned to quantise cleanly into the panel's 6-ink range. Don't sample
    colours outside the token set.
* Use one outer shell edge (`--edge-weight` solid `var(--edge)`).
    Use spacing, weight and `var(--surface-sunken)` contrast inside; hairline borders disappear when dithered on E6.
    Optional dividers (`--title-rule-w`, `--row-rule-w`) come from the style and default to 0.
* Refresh time is ~25 s on the 13.3" panel. Static, dense layouts
    win; busy gradients quantise into noise.
* Tabular numerics align cleanly: `font-variant-numeric: tabular-nums`.
* Anti-aliased type works on Spectra 6 but won't survive heavy
    saturation tweaks. Stick to weight ≥ 500 for anything small.
* No `transition`, `animation` or `:hover` effects.
    E6 ghosts mid-refresh and the renderer screenshots
    `animations="disabled"` anyway.

---

## Reference: shipped widgets

Read each `client.js` for examples.

Pick the closest layout:

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

Other patterns:

* WMO-code → Phosphor icon lookup ([`weather_*`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_now))
* Chart helpers via `tokens()` probe ([`weather_hourly`](https://github.com/dmellok/tesserae/tree/main/plugins/weather_hourly), [`ha_history`](https://github.com/dmellok/tesserae/tree/main/plugins/ha_history))
* server.py disk-cache pattern ([any `weather_*`, `f1_*` widget])
* Family-shared module ([`f1_core`](https://github.com/dmellok/tesserae-widget-f1/tree/main/f1_core) → `getCircuit()` + `trackSvg()`)
* `--c-zoom`-aware title bar ([`spectra-widgets.css`'s `.w-title`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-widgets.css))
* Team-colour stripe via inset box-shadow ([`f1_standings_drivers`](https://github.com/dmellok/tesserae-widget-f1/tree/main/f1_standings_drivers))

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

The `client` fixture in `conftest.py` supplies a Flask test client.
The app is in testing mode with all plugins discovered, authentication off and `/_test/render` enabled.

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

Most widgets need no `client.css`; Spectra supplies shell and body layouts.
Add `client.css` only for widget-specific rules, such as custom positioning or SVG tweaks.

Open `http://127.0.0.1:8765/_test/render?plugin=<id>&size=md` in your browser.
The Flask dev server reloads when files change; refresh the page to see updates.

---

## What NOT to do

* Don't hard-code theme colours. Use Spectra tokens (`var(--accent-1..6)`, `var(--surface)`, `var(--text-*)`).
    For canvas / Chart.js, use `tokens()` from [`spectra-chart.js`](https://github.com/dmellok/tesserae/blob/main/static/spectra-chart.js).

    Brand colours, such as F1 teams and Spotify green, are exceptions; see "Custom colours" above.

* Don't access the parent document. Stay inside the Shadow DOM.

* Don't start intervals, animations or async work that finishes after your default export resolves; that's when the screenshot fires.

* Don't load fonts. The renderer waits for `document.fonts.ready` and passes the page font as `ctx.font.family`.
    Use `font-family: inherit` in `:host`.

* Don't fetch from client.js. Use server.py; the renderer only waits for declared `<img>` loads and fonts.
    The screenshot fires before client-side data arrives.

* Don't assume the panel has internet. The Tesserae host runs `fetch()` from server.py; panels may read the .bin offline.
