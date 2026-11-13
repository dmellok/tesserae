# Build widgets with Studio

[Tesserae Studio](https://github.com/dmellok/tesserae-studio) gives you an editor
and previews for writing widgets. It runs separately from Tesserae.
Connect it to Tesserae for live data, accurate renders, and widget registration.

There are two ways to use it, and they share one workspace:

- Use the web UI at `http://localhost:8770`: a Monaco editor with live and faithful previews.
- Use an AI agent over MCP (`tesserae-studio-mcp`).
  Set it up with [MCP servers: install & use](mcp-servers.md).

To build without Studio, see
[Build a widget with AI](writing-a-widget.md) and the [Widget contract](../widgets.md).

## What you need first

Choose a mode based on what you connect:

| Mode | You have | You get |
| --- | --- | --- |
| **none** | Studio only | the editor + interactive (shadow-mount) preview |
| **disk** | a local `tesserae` checkout | + Spectra assets and the plugin schema from disk |
| **live** | a running Tesserae reachable over HTTP | + live `fetch()` data and **faithful e-ink render** |

Use live mode to run `server.py`, check the dithered output, and register the widget.
Only the faithful e-ink render shows real dithering. The browser preview is faster.

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

Start with the minimal widget in `examples/hello_stat`:

```
hello_stat/
  plugin.json          # manifest (required, schema-validated)
  client.js            # the render (required): export default function(shadow, ctx)
  server.py            # optional: fetch(options, settings, *, ctx) -> dict
  tests/test_smoke.py  # optional but recommended: renders at every size
```

`client.js` is an ES module. Its default export replaces the shadow root's content on each render:

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

Add `server.py` only for data fetching. Return a dict; never raise:

```python
def fetch(options, settings, *, ctx):
    try:
        ...
    except Exception:
        return {"error": "Something the user can act on."}   # renders verbatim
    return {"value": 42}
```

See the [Widget contract](../widgets.md) for the manifest, `ctx`, Spectra tokens, and fragments.

## The build loop

Agents use [MCP servers](mcp-servers.md) for these tools. The UI has buttons for the same actions.

1. Create files with `scaffold_widget(name, archetype, server=true)`. Choose an archetype:
   `stat | list | chart | status | weather | calendar | image`.
   The generated widget passes lint and supports fragments.

   For shared data and an admin page, use `scaffold_bundle(name, members)`.
   It creates a `<name>_core` companion and member widgets.
2. Read and edit the files. Use Spectra tokens in `client.js` and branch on `ctx.cell.fragment`.
   Keep `server.py` returning dicts.
3. Run `lint_widget` to check the Golden Rules and manifest schema. Fix every error before continuing.
4. Call `register_widget` for live data and faithful rendering.
   Local Studio uses a symlink; remote / Home Assistant installs get an HTTP push over MCP.
5. Restart gate: reload Tesserae for a new widget's `client.js` route.
   Registration replaces the registry and loads `server.py`, but new asset routes need a reload.

   A blank cell or "Failed to fetch dynamically imported module .../client.js" can mean the route is missing.
   Call `probe_widget_data`: real server output confirms `server.py` loaded and the static asset returns 404.
   Existing widgets serve edits immediately; they do not need this reload.
6. Run `faithful_render(size=xs|sm|md|lg)` for a dithered PNG.
   To check live data at all sizes together, build a contact-sheet canvas with
   [dashboards MCP](mcp.md) and `render_preview` it. Check `sm`/`xs` for overflow.
7. Run `mine_data_schema(apply=true)` once the data format is final.
   This lets canvas data primitives bind to the widget's fields.

## The linter (the Golden Rules)

`lint_widget` checks these rules for still images, variable cell sizes, and a fixed palette.
Errors block progress. Warnings are strong recommendations.

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

- Per-cell config (a location, a label): declare it in `cell_options[]`; read it in
  `fetch()` via `options.get(...)`.
- Secrets (API keys): declare a `settings[]` field with `"secret": true`; read it via
  `settings.get(...)`. Never put a key in `cell_options`.
- Network egress: declare every host in `requires: ["network:api.example.com",
  "settings:plugin"]`. Cache polite fetches in `ctx["data_dir"]`. Error strings render
  verbatim, so make them friendly and actionable.

Check what the API returns for a plain key before choosing a metric.
If OAuth, analytics, or history is unavailable, save snapshots in `data_dir`
to calculate changes, or choose a supported metric. Say when you substitute one.

## Bundles and admin pages

`scaffold_bundle(name, members, admin=true)` creates a family:

- A `<name>_core` companion (`kind: "data"`) holds shared data.
  Its `choices()` function supplies live values for member `select` options.
  It can have a Flask admin page at `templates/<core_id>/index.html`.
- One or more member widgets read the core through the plugin registry.

Register the core and each member for the family to work live.

## Publishing to the catalog

Share widgets through the community catalog; don't bundle them into Tesserae.
See [Publish via the catalog](publishing-a-widget.md):

1. `package_widget` builds the release tarball and reports its `sha256` + folders.
2. `generate_catalog_entry(author, tags, release, source)` creates and validates a catalog entry.
   Use allowed `tags` values; `description` must be <= 280 chars. Identity comes from the manifest.
3. `open_catalog_pr(...)` drafts a PR to `tesserae-widgets` in dry-run mode by default.
   It includes the `widgets.json` diff and required `screenshots/<id>/lg.png`.
   To open it, publish the widget in its own public GitHub repo with a tagged release that `tarball_url` can reach.

## Troubleshooting

- Blank cell / "Failed to fetch dynamically imported module .../client.js" on a new
  widget → the restart gate (step 5), not a bug. `probe_widget_data` returning your real
  output confirms it; reload/restart Tesserae rather than re-editing the JS. An
  **already-registered** widget with this symptom is a genuine `client.js` error.
- Faithful render fails / times out → Tesserae isn't reachable, or the widget isn't
  registered. Check `studio_health`.
- Live data shows the demo sample → `server.py` errored or isn't configured;
  `widget_data` / `probe_widget_data` report `source: live | sample | error`; never treat
  sample/error as real.
