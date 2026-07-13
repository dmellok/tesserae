# Build widgets with Studio

[Tesserae Studio](https://github.com/dmellok/tesserae-studio) is a companion app for
authoring Tesserae widgets end to end: a code editor with live and faithful preview, a
widget linter, data-schema mining, and an MCP authoring server an AI agent drives from
scaffold to a registered, rendering widget. Studio is a separate app that connects to a
running Tesserae for render fidelity and the widget registry.

There are two ways to use it, and they share one workspace:

- **The web UI** (a Monaco editor + live/faithful preview) at `http://localhost:8770`.
- **An AI agent** over MCP (`tesserae-studio-mcp`), which drives the same build loop
  through tools. See [MCP servers: install & use](mcp-servers.md) to set that up.

If you would rather hand-write a widget without Studio, see
[Build a widget with AI](writing-a-widget.md) and the [Widget contract](../widgets.md).

## What you need first

Studio runs in one of three modes; more of it lights up as you connect more:

| Mode | You have | You get |
| --- | --- | --- |
| **none** | Studio only | the editor + interactive (shadow-mount) preview |
| **disk** | a local `tesserae` checkout | + Spectra assets and the plugin schema from disk |
| **live** | a running Tesserae reachable over HTTP | + live `fetch()` data and **faithful e-ink render** |

For real work you want **live mode**: point Studio at a running Tesserae so it can run your
`server.py`, screenshot the true dithered output, and register the widget. A faithful e-ink
render is the only thing that shows real dither; the in-browser preview is for speed, not
ground truth.

## Install and run

One-command install (macOS / Linux / Raspberry Pi):

```bash
curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae-studio/main/install.sh | bash
```

Windows (PowerShell):

```powershell
iwr https://raw.githubusercontent.com/dmellok/tesserae-studio/main/install.ps1 -UseBasicParsing | iex
```

Docker:

```bash
docker run -d --name tesserae-studio -p 8770:8770 \
  -e STUDIO_TESSERAE_URL=http://<your-tesserae-host>:8765 \
  -v "$PWD/data:/app/data" ghcr.io/dmellok/tesserae-studio:latest
```

From source (dev), backend then frontend:

```bash
cd server
python -m venv .venv && . .venv/bin/activate     # Python >= 3.11
pip install -e .
STUDIO_TESSERAE_URL=http://localhost:8765 uvicorn studio_server.app:app --port 8770 --reload

cd ../web
npm install && npm run dev                         # http://localhost:5173 in dev
```

Or, with the package installed, the console script serves the built UI on `:8770`:

```bash
STUDIO_TESSERAE_URL=http://localhost:8765 tesserae-studio
```

Configuration (environment variables; point it at Tesserae for live mode):

| Env var | Default | What it does |
| --- | --- | --- |
| `STUDIO_TESSERAE_URL` | `http://localhost:8765` | the running Tesserae for live data + faithful render |
| `STUDIO_TESSERAE_MCP_TOKEN` | (none) | bearer token to reach a remote / Home Assistant Tesserae |
| `STUDIO_WORKDIR` | `<repo>/examples` | where the widgets you author live |
| `STUDIO_TESSERAE_PATH` | autodetect `../tesserae` | a disk checkout for assets + schema (disk mode) |
| `STUDIO_PORT` / `STUDIO_HOST` | `8770` / `127.0.0.1` | where Studio serves |
| `STUDIO_CATALOG_PATH` | autodetect `../tesserae-widgets` | catalog clone for the publish PR |

## Anatomy of a widget

A widget is a folder of files (the seed widget `examples/hello_stat` is the minimal shape):

```
hello_stat/
  plugin.json          # manifest (required, schema-validated)
  client.js            # the render (required): export default function(shadow, ctx)
  server.py            # optional: fetch(options, settings, *, ctx) -> dict
  tests/test_smoke.py  # optional but recommended: renders at every size
```

`client.js` is an ES module whose default export paints into a shadow root, idempotently:

```javascript
export default function (shadow, ctx) {
  const o = (ctx.cell && ctx.cell.options) || {};
  const fragment = (ctx.cell && ctx.cell.fragment) || "full";  // canvas fragments
  shadow.innerHTML = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css" />
    <style>/* paint from Spectra tokens + container queries */</style>
    <div class="w">…</div>`;   // set innerHTML, never append
}
```

`server.py` (only when the widget needs data) returns a plain dict, and never raises:

```python
def fetch(options, settings, *, ctx):
    try:
        ...
    except Exception:
        return {"error": "Something the user can act on."}   # renders verbatim
    return {"value": 42}
```

The [Widget contract](../widgets.md) is the authoritative reference for the manifest,
`ctx`, Spectra tokens, and fragments.

## The build loop

Whether you drive it in the editor or through the agent, the loop is the same. The agent
version runs these as MCP tools (see [MCP servers](mcp-servers.md)); the UI exposes the same
actions as buttons.

1. **Scaffold.** `scaffold_widget(name, archetype, server=true)`. Archetypes:
   `stat | list | chart | status | weather | calendar | image`. You get a fragment-first,
   lint-clean skeleton. For a family of widgets sharing one data source + an admin page, use
   `scaffold_bundle(name, members)` (it makes a `<name>_core` companion plus members).
2. **Edit.** Read the generated files, then edit them. Keep `client.js` painting from
   Spectra tokens and branching on `ctx.cell.fragment`; keep `server.py` returning dicts.
3. **Lint until clean.** `lint_widget` runs the Golden Rules + the manifest schema. Fix
   every error before moving on (rules below).
4. **Register.** `register_widget` wires the widget into the connected Tesserae so it gets
   live data and faithful render. Co-located Studio uses a local symlink; a remote / Home
   Assistant Tesserae gets an HTTP push over MCP.
5. **Mind the restart gate (new widgets only).** A brand-new widget's `client.js` is not
   served until Tesserae reloads the static-asset route for the new plugin id. Register
   swaps in the new registry and hot-loads `server.py`, but a fresh id's asset route needs a
   reload. Symptom: a blank cell or "Failed to fetch dynamically imported module
   .../client.js" on a **new** widget. Confirm with `probe_widget_data`: if it returns your
   real server output, `server.py` is loaded and only the static asset is 404ing, so it is
   the gate, not a JS bug. **Widget updates skip this entirely**; edits to an
   already-registered widget serve immediately.
6. **Faithful render.** `faithful_render(size=xs|sm|md|lg)` returns the true dithered PNG. To
   check live data across every size at once, build a contact-sheet canvas via the
   [dashboards MCP](mcp.md) and `render_preview` it. `sm`/`xs` are where layouts overflow,
   check them.
7. **Mine the data schema.** `mine_data_schema(apply=true)` once the data shape is final, so
   the widget's fields are bindable by the canvas editor's data primitives.

## The linter (the Golden Rules)

`lint_widget` enforces these. Errors block; warnings are strong nudges. They exist because
e-ink screenshots a still frame at an unknown size on a fixed palette.

**Errors:**

- No CSS animations or transitions (`@keyframes`, `transition`, `animation`).
- No network in `client.js` (`fetch`, `XMLHttpRequest`, `WebSocket`, remote `<script>` /
  `<link>`); all fetching lives in `server.py`.
- No hard-coded hex; paint from Spectra tokens. Mark a genuine data-identity colour (team /
  brand / flag) with an `/* identity */` comment to opt out.
- No media queries; use container queries (`cqw` / `cqh` / `cqmin`); cells can be any size.
- No custom fonts and no absolute `font-family`; inherit the page font.
- Idempotent render: set `shadow.innerHTML`, never `append` to the shadow root.
- `client.js` must `export default function(shadow, ctx) {}`.
- A widget declaring `fragments` must branch on `ctx.cell.fragment`.
- `server.py` must not `raise`; return `{"error": "friendly message"}`.
- A `server.py` that makes network calls must declare `requires: ["network:<host>"]`.

**Warnings:**

- Prefer `ph-bold` over `ph-fill` (fill blobs on Spectra 6).
- Avoid ad-hoc `border:` for structure; use spacing, weight, and `--surface-sunken`.
- A `server.py` widget should ship a `data_schema` (mine one from real output).
- Ship a `tests/test_smoke.py` that renders at every declared size.
- Decompose into `fragments` so the widget is canvas-native.

Spectra tokens you paint from: `--surface`, `--surface-sunken`, `--text-primary` /
`-secondary` / `-muted`, `--accent-1..6` (+ `--accent-*-soft`), `--icon`, `--edge`, `--bg`.

## Secrets, options, and egress

- **Per-cell config** (a location, a label): declare it in `cell_options[]`; read it in
  `fetch()` via `options.get(...)`.
- **Secrets** (API keys): declare a `settings[]` field with `"secret": true`; read it via
  `settings.get(...)`. Never put a key in `cell_options`.
- **Network egress**: declare every host in `requires: ["network:api.example.com",
  "settings:plugin"]`. Cache polite fetches in `ctx["data_dir"]`. Error strings render
  verbatim, so make them friendly and actionable.

Before you code a metric, check what the upstream API actually returns for a plain key. If a
stat needs OAuth / analytics / history the API does not expose, adapt honestly (snapshot
into `data_dir` for deltas, or swap an impossible metric for a real one) and flag the
substitution.

## Bundles and admin pages

`scaffold_bundle(name, members, admin=true)` creates a family:

- a `<name>_core` companion (`kind: "data"`) that owns the shared data + a `choices()`
  function (so member `select` options can list live values) and an optional Flask admin
  page under `templates/<core_id>/index.html`,
- one or more member widgets wired to read the core via the plugin registry.

Register the core and each member for the family to work live.

## Publishing to the catalog

When a widget is ready to share, it ships through the community catalog, not bundled into
Tesserae (see [Publish via the catalog](publishing-a-widget.md)):

1. `package_widget` builds the release tarball and reports its `sha256` + folders.
2. `generate_catalog_entry(author, tags, release, source)` builds and validates a
   marketplace entry against the real schema (`tags` are a closed enum; `description`
   <= 280 chars; identity is filled from your manifest).
3. `open_catalog_pr(...)` (dry-run by default) drafts the PR to `tesserae-widgets`: the
   `widgets.json` diff plus the required `screenshots/<id>/lg.png`. Opening the real PR
   needs the widget to live in its own public GitHub repo with a tagged release so
   `tarball_url` resolves.

## Troubleshooting

- **Blank cell / "Failed to fetch dynamically imported module .../client.js" on a new
  widget** → the restart gate (step 5), not a bug. `probe_widget_data` returning your real
  output confirms it; reload/restart Tesserae rather than re-editing the JS. An
  **already-registered** widget with this symptom is a genuine `client.js` error.
- **Faithful render fails / times out** → Tesserae isn't reachable, or the widget isn't
  registered. Check `studio_health`.
- **Live data shows the demo sample** → `server.py` errored or isn't configured;
  `widget_data` / `probe_widget_data` report `source: live | sample | error`; never treat
  sample/error as real.
