# Build dashboards with AI (MCP)

Tesserae ships an optional [MCP](https://modelcontextprotocol.io) integration so
an AI agent (Claude Desktop, Claude Code, or any MCP client) can build **freeform
(canvas) dashboards** for you: it lists your widgets and devices, lays out a
canvas, **renders a preview to check its own work**, and pushes to a panel.

It's experimental and off by default.

## How it works

The canvas editor is, underneath, a JSON-document editor. The MCP integration
lets an agent write that same document directly and render a preview PNG to see
the result, so it can place, look, and adjust:

```
create_canvas_page → set_canvas → render_preview → (look) → set_canvas → …
```

There are two pieces, and they can run on **different machines**:

- **The API** (`/api/mcp/*`) is built into Tesserae. It ships with the app, so
  there's nothing to install on the Tesserae side (Docker, Home Assistant, or
  source). You just enable it.
- **The bridge** (`tesserae-mcp`) is a small stdio program your agent launches.
  It's a separate package, [dmellok/tesserae-mcp](https://github.com/dmellok/tesserae-mcp),
  installed on **the machine where your agent runs** (your laptop/desktop). It
  talks to your Tesserae over HTTP.

So: nothing to `pip install` inside Tesserae or Home Assistant. You install the
bridge only on your agent's machine.

---

## Step 1 — Enable the API in Tesserae

1. Open Tesserae → **Settings → System → MCP**.
2. Click **Enable MCP API**.
3. **Token:** if your agent runs on the **same machine** as Tesserae, you can
   skip this (loopback is trusted). If it runs on a **different machine**, click
   **Regenerate token** and copy it — you'll need it in Step 3.

While the experiment is off, `/api/mcp` returns 404, so the API is invisible
until you switch it on here.

---

## Step 2 — Install the bridge (on your agent's machine)

The bridge is a command-line tool, so **[pipx](https://pipx.pypa.io)** is the
cleanest way to install it: pipx puts it in its own isolated environment and adds
it to your `PATH`, which is exactly what an MCP client needs.

=== "pipx (recommended)"

    ```bash
    # macOS
    brew install pipx
    pipx ensurepath

    # or, any platform
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    ```

    Then install the bridge:

    ```bash
    pipx install tesserae-mcp
    ```

    Confirm it's on your `PATH`:

    ```bash
    which tesserae-mcp
    ```

    (You may need to open a new terminal after `pipx ensurepath`.)

=== "venv"

    ```bash
    python3 -m venv ~/.tesserae-mcp
    ~/.tesserae-mcp/bin/python -m pip install tesserae-mcp
    ```

    The command is then `~/.tesserae-mcp/bin/tesserae-mcp` (use that full path in
    the config below).

=== "from source"

    ```bash
    # the bridge lives in the Tesserae repo under packages/tesserae-mcp
    pip install "git+https://github.com/dmellok/tesserae#subdirectory=packages/tesserae-mcp"
    # or from a clone:  pip install -e tesserae/packages/tesserae-mcp
    ```

!!! warning "`error: externally-managed-environment`"
    Homebrew and other modern Python installs block `pip install` into the
    system Python ([PEP 668](https://peps.python.org/pep-0668/)). That's exactly
    why pipx (or a venv) is recommended here — both sidestep it. Avoid
    `--break-system-packages`.

---

## Step 3 — Configure your agent

Point your MCP client at the `tesserae-mcp` command, with two environment
variables:

- **`TESSERAE_URL`** — where your Tesserae is reachable. Default
  `http://127.0.0.1:8765`. For a Docker/Home Assistant install, use its LAN
  address, e.g. `http://192.168.1.50:8765`.
- **`TESSERAE_MCP_TOKEN`** — the token from Step 1. **Omit it** when the agent
  and Tesserae are on the same machine (loopback is trusted).

=== "Claude Code"

    One command:

    ```bash
    claude mcp add tesserae --scope user \
      --env TESSERAE_URL=http://127.0.0.1:8765 \
      -- tesserae-mcp
    ```

    Add the token for a remote Tesserae:

    ```bash
    claude mcp add tesserae --scope user \
      --env TESSERAE_URL=http://192.168.1.50:8765 \
      --env TESSERAE_MCP_TOKEN=your-token-here \
      -- tesserae-mcp
    ```

    `--scope user` makes it available in every project; drop it to scope to the
    current project. Check with `claude mcp list`, or `/mcp` inside a session.

=== "Claude Desktop"

    Edit `claude_desktop_config.json`:

    - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
    - Windows: `%APPDATA%\Claude\claude_desktop_config.json`

    ```json
    {
      "mcpServers": {
        "tesserae": {
          "command": "tesserae-mcp",
          "env": {
            "TESSERAE_URL": "http://127.0.0.1:8765",
            "TESSERAE_MCP_TOKEN": "your-token-here"
          }
        }
      }
    }
    ```

    Drop `TESSERAE_MCP_TOKEN` if the agent and Tesserae share a machine. Restart
    Claude Desktop after editing.

    !!! note "Desktop can't find `tesserae-mcp`?"
        GUI apps don't inherit your shell `PATH`, so the bare command may not
        resolve. Run `which tesserae-mcp` and use the **absolute path** it prints
        (e.g. `/Users/you/.local/bin/tesserae-mcp`) as `"command"`.

---

## Step 4 — Use it

Make sure Tesserae is running, then ask your agent something like:

> Using Tesserae, build an 800×480 canvas dashboard with the time, today's
> weather for Melbourne, and my next calendar event, then show me a preview.

It'll call `list_widgets` / `get_widget_options` to see what's available,
`create_canvas_page`, `set_canvas` to lay it out, and `render_preview` to show
you the result. Iterate from there ("make the clock bigger", "move weather to the
top"), and when you're happy, `push_to_device`.

### Watch it work live

Open the dashboard in Tesserae's canvas editor (Dashboards → the agent-made page,
tagged **Agent**) while the agent works. The editor updates in real time as the
agent saves, so you can watch it place and adjust elements. Each element arrives
on its own: a new one reveals top-down, an edited one pulses, and a code element
being streamed in holds a running underline until the chunks stop.

An **Agent** panel in the right sidebar narrates the build — the step in flight
at the top ("Streaming code in · css · +260 B of 833 B"), everything already done
as ticks below it, with reads and repeated steps folded into single counted
lines. A pill next to the dashboard name shows the same thing when the sidebar is
collapsed.

You don't have to be in the editor to notice. On any Tesserae page, a toast names
the dashboard the agent has started building and offers to open it, following
after a short countdown. Turn following off with the **follow** checkbox on the
toast; the choice sticks. It won't navigate away from unsaved edits, a form
you're typing in, or an open dialog.

If you start editing while the agent is working, it won't overwrite your unsaved
changes — you'll get a "changed externally" reload prompt instead.

---

## Tools

| Tool | What it does |
| --- | --- |
| `list_widgets` | Every placeable widget (with fragments) + theme/style/font options |
| `get_widget_options` | A widget's options + format hints (big choice lists omitted by default) |
| `get_widget_choices` | Page through one option's choice rows (HA entity pickers etc.) |
| `probe_widget_data` | A widget's data + `data_source` (live/sample/error) + bindable field paths |
| `list_devices` | Registered panels: dimensions + colour capability (palette, `mono` flag) |
| `list_pages` | Existing canvas dashboards |
| `create_canvas_page` | Create an empty canvas (size it to your panel) |
| `get_canvas` | Read a canvas document (returns a `rev` for concurrency-safe writes) |
| `set_canvas` | Replace a canvas document (compact ack; `?return=doc` for the full doc) |
| `add_element` | Append one element (each call saves, so an open editor updates live) |
| `update_element` | Change one element in place, without re-sending the whole document |
| `delete_element` | Remove one element by id |
| `patch_canvas` | Change document-level fields (size, theme, bg) without touching elements |
| `arrange` | Compute aligned grid/row/column boxes so you lay out by intent, not pixels |
| `measure_text` | Measure rendered text width/height so a box fits its content |
| `render_report` | Read back what rendered (values, overflow, live-vs-sample, colours) as JSON |
| `render_preview` | Render the canvas to a PNG the agent can see |
| `push_to_device` | Push the canvas to explicit device(s) |

The write tools (`set_canvas`, `add_element`, `update_element`, `delete_element`,
`patch_canvas`) accept an optional `base_rev` (the `rev` from `get_canvas`); if the
page changed since, the write is refused with a conflict so a concurrent UI edit
isn't clobbered. Prefer `update_element` / `patch_canvas` over resending the whole
document for a one-field change. Verify a render with `render_report` (structured)
alongside `render_preview` (image).

---

## Widget push (authoring)

A separate set of endpoints under `/api/mcp` lets an authoring client (Tesserae
Studio) install a widget you are building onto a running Tesserae over the network,
with no shared filesystem, so authoring works against a remote or Home Assistant App
/ Docker instance. These are developer pushes, not the public catalog flow (no catalog
entry, no `sha256` gate).

| Endpoint | What it does |
| --- | --- |
| `POST /api/mcp/widgets/install` | Install/upsert a widget from a tarball (`application/gzip` body, or `multipart` with a `tarball` part). Query: `id` (override), `reload` (`auto`/`in_process`/`restart`/`none`). |
| `DELETE /api/mcp/widgets/<id>` | Uninstall a pushed widget (only touches the `authored/` dir). |
| `POST /api/mcp/reload` | Rebuild the plugin registry without installing. Body/query `mode`. |
| `GET /api/mcp/widgets?origin=authored` | List pushed widgets with an `active` flag. |
| `GET /api/mcp/widgets/<id>/render.png` | Faithful e-ink PNG of a single widget (`size`, `opts`, `theme`, `style`). |

Pushed widgets live under `<data_root>/authored/<id>`, isolated from catalog installs
and on the persistent volume so they survive restarts / App upgrades. Install is
guarded: tarballs are size- and count-capped, extracted with tar-slip protection, and
only `kind: "widget"` manifests that validate against the schema and do not collide
with a bundled id are accepted; `server.py` is never imported at install time. Reload
is in-process (fast) unless the widget adds an admin `blueprint()`, which needs a
restart; when a restart is scheduled the response has `restarting: true` and the client
polls `/healthz` then `/api/mcp/catalog` until the id appears.

---

## What an agent can place

A canvas element is one of: a **widget** (or a fragment of one), a **decoration**
(text, rectangle, ellipse, line, icon), a **data primitive** (a widget data field
shown as a scalable number / text / line / bar / sparkline), or a **custom HTML**
block (static HTML + CSS in a sandboxed iframe). Elements may sit partly off the
panel edge. The exact JSON shape for each is in the `set_canvas` tool description.

Any element can also carry **live bindings** (`bind`): decorations are otherwise
static geometry, but a binding reads a widget field each render and maps it through
a transform to patch the element's props, so a shape reflects data in lockstep with
the data primitives on the same canvas (no polling). Transforms: `position` (a
moving marker), `length` (a gauge that grows), `pick` (hop between discrete states),
`color` (threshold colouring), `gradient` (a value interpolated smoothly along
colour stops, quantised to the panel palette on e-ink), and `icon` (condition to a
glyph). Full shape in the `set_canvas` tool description.

## Give the dashboard its own settings page

A dashboard an agent builds usually has a handful of values that will need
changing later: the API endpoint behind a code tile, a postcode, an entity id, a
city. Left as raw element options, changing one means opening the composer and
finding the right source drawer.

Instead, an agent can declare them. A canvas carries an `inputs` list, one entry
per value worth asking about, each pointing at where the answer lands:

```json
{
  "name": "bin_api",
  "label": "Bin collection API URL",
  "type": "string",
  "secret": true,
  "mask": false,
  "targets": [{"el": "e1", "slot": "source_options", "index": 0, "key": "url"}]
}
```

Tesserae then serves a **Settings** page for that dashboard at
`/pages/canvas/c/<id>/configure`, linked from the Dashboards list, with one
field per declared input pre-filled from what the dashboard is currently
rendering. Set `inputs` with `set_canvas`, or with `patch_canvas` alone once the
elements are already placed. Full field and slot reference is in the
`set_canvas` tool description.

Two things follow from using the same declaration the template format uses:

- **Sharing asks the same questions.** Declared inputs are what the Share flow
  offers as install questions, so an installer is asked what the author was
  asked, with the author's labels rather than derived ones.
- **A dashboard with no declarations can derive them.** On the Settings page,
  *Suggest settings* runs the Share flow's own analysis: whatever it would
  redact and ask an installer about becomes a setting. Existing declarations are
  kept, so it only ever adds.

`secret: true` keeps a value out of the render context and redacts it on share.
`mask` is the separate question of whether the settings field is a password box,
and defaults to `secret`; set it `false` for a value that is sensitive but still
has to be readable to maintain, a URL being the usual case.

## Guardrails

- The API **404s** entirely while the `mcp` experiment is off.
- Remote callers need the token; loopback is trusted so a co-located agent works
  with zero config.
- Writes only touch **canvas** dashboards, never grid ones.
- Pages an agent creates are flagged **Agent** in the Dashboards list.
- Pushing is **always explicit** — the agent must name the device(s); nothing is
  pushed automatically.

---

## Troubleshooting

**"Cannot reach Tesserae at …"** — Tesserae isn't running at `TESSERAE_URL`, or
the port isn't reachable from the agent's machine. Confirm the URL in a browser.
For a Home Assistant install, the add-on must expose a **direct port** on your
LAN; an ingress-only setup can't be reached by an external agent.

**HTTP 401 / unauthorized** — the agent is calling from a non-loopback address
without a valid token. Generate one in Settings → System → MCP and set
`TESSERAE_MCP_TOKEN`. (Same machine as Tesserae? You shouldn't hit this — check
`TESSERAE_URL` really is `127.0.0.1`.)

**HTTP 404 on every call** — the `mcp` experiment is off. Enable it in Settings →
System → MCP.

**`tesserae-mcp: command not found`** — after `pipx install`, run
`pipx ensurepath` and open a new terminal. For Claude Desktop, use the absolute
path from `which tesserae-mcp` (GUI apps don't inherit your shell `PATH`).

**`externally-managed-environment`** — see the warning in Step 2; use pipx or a
venv rather than installing into the system Python.

---

## Notes

- The bridge needs a **running** Tesserae (rendering uses its headless browser).
- Core Tesserae never imports the `mcp` package; it lives entirely in the
  separate `tesserae-mcp` bridge.
- The API surface and canvas-document schema are the contract between the two.
  See the [`tesserae-mcp` repo](https://github.com/dmellok/tesserae-mcp) for the
  bridge itself.
