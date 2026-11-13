# Build dashboards with AI (MCP)

Use [MCP](https://modelcontextprotocol.io) to let an AI agent build canvas dashboards.
The agent can find widgets and devices, arrange a dashboard, preview it, and push it to a panel.
Claude Desktop, Claude Code, and other MCP clients work.

The API is on by default. Install the bridge on your agent's machine.
If Tesserae runs elsewhere, copy an access token too.

## How it works

Agents edit the same JSON document as the canvas editor and check a PNG preview:

```
create_canvas_page → set_canvas → render_preview → (look) → set_canvas → …
```

Two pieces can run on different machines:

- The API (`/api/mcp/*`) comes with Tesserae in Docker, Home Assistant, and source installs.
- The bridge (`tesserae-mcp`) runs on the agent's machine and calls Tesserae over HTTP.
  The agent launches it over standard input/output (stdio).
  Install the separate [`tesserae-mcp`](https://github.com/dmellok/tesserae/tree/main/packages/tesserae-mcp) package from `packages/tesserae-mcp`.

You don't need to `pip install` anything inside Tesserae or Home Assistant.

---

## Step 1 — Get a token (remote agents only)

Skip this step for a direct loopback connection on Tesserae's machine; it is trusted.
For remote or reverse-proxy connections, open **Settings → System → MCP**.
Click **Regenerate token** and copy it for Step 3.

To turn off agent access, click **Disable MCP API** on the same card (or
set `TESSERAE_EXPERIMENT_MCP=0`), and `/api/mcp` returns 404.

---

## Step 2 — Install the bridge (on your agent's machine)

Use [pipx](https://pipx.pypa.io) to install the bridge in its own environment
and add it to your `PATH`:

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
    system Python ([PEP 668](https://peps.python.org/pep-0668/)). Use pipx or a
    venv to avoid this restriction. Avoid
    `--break-system-packages`.

---

## Step 3 — Configure your agent

Point your MCP client at the `tesserae-mcp` command, with two environment
variables:

- `TESSERAE_URL`: where your Tesserae is reachable. Default
  `http://127.0.0.1:8765`. For a Docker/Home Assistant install, use its LAN
  address, e.g. `http://192.168.1.50:8765`.
- `TESSERAE_MCP_TOKEN`: the token from Step 1. **Omit it** when the agent
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
        GUI apps don't inherit your shell `PATH`.
        Run `which tesserae-mcp` and use the absolute path it prints
        (e.g. `/Users/you/.local/bin/tesserae-mcp`) as `"command"`.

---

## Step 4 — Use it

Make sure Tesserae is running, then ask your agent something like:

> Using Tesserae, build an 800×480 canvas dashboard with the time, today's
> weather for Melbourne, and my next calendar event, then show me a preview.

The agent uses these tools:

- `list_widgets` / `get_widget_options` to find widgets.
- `create_canvas_page` and `set_canvas` to lay out the dashboard.
- `render_preview` to show it to you.

Ask for changes, such as "make the clock bigger" or "move weather to the top".
When ready, use `push_to_device`.

### Watch it work live

Open the page tagged **Agent** under Dashboards, in the canvas editor, to watch edits appear as the agent saves.
The right sidebar's **Agent** panel shows the current step and completed work.
When collapsed, a pill beside the dashboard name shows progress.

The sidebar estimates context use (`~6.3k ctx`), the information sent to and from the agent.
This is a lower bound based on call bytes, not real token usage from your agent's client.
Reading the widget catalog typically costs more context than all writes combined.

On any Tesserae page, a toast names the dashboard the agent has started building
and offers to open it. After a short countdown, it follows automatically.
Turn following off with the **follow** checkbox on the toast; the choice sticks.

It won't navigate away from unsaved edits, a form
you're typing in, or an open dialog.

If you start editing while the agent is working, it won't overwrite your unsaved
changes. You'll get a "changed externally" reload prompt instead.

---

## Tools

| Tool | What it does |
| --- | --- |
| `list_widgets` | Every placeable widget (with fragments) + theme/style/font options |
| `list_services` | Non-placeable service data sources (HA, weather, REST) a code element can read |
| `list_icons` | Search the vendored Phosphor icon set by substring for a real slug |
| `add_font` / `list_fonts` / `delete_font` | Cache a webfont on the server (Google Fonts or a URL), list, or remove it |
| `get_widget_options` | A widget's options + format hints (big choice lists omitted by default) |
| `get_widget_choices` | Page through one option's choice rows (HA entity pickers etc.) |
| `probe_widget_data` | A widget's data + `data_source` (live/sample/error) + bindable field paths |
| `list_devices` | Registered panels: dimensions + colour capability (palette, `mono` flag) |
| `list_pages` | Existing canvas dashboards |
| `create_canvas_page` | Create an empty canvas (size it to your panel) |
| `delete_canvas_page` | Delete a canvas dashboard (refused while a lineup uses it) |
| `get_canvas` | Read a canvas document (returns a `rev` for concurrency-safe writes) |
| `set_canvas` | Replace a canvas document (compact ack; `?return=doc` for the full doc) |
| `set_canvas_background` | Generate an AI background image (fal.ai) and set it as the canvas background |
| `add_element` | Append one element (each call saves, so an open editor updates live) |
| `add_elements_bulk` | Append many elements in one save (max 500), all-or-nothing |
| `append_code` | Stream html / css / js into a code element a chunk at a time |
| `update_element` | Change one element in place, without re-sending the whole document |
| `delete_element` | Remove one element by id |
| `patch_canvas` | Change document-level fields (size, theme, bg) without touching elements |
| `arrange` | Compute aligned grid/row/column boxes so you lay out by intent, not pixels |
| `measure_text` | Measure rendered text width/height so a box fits its content |
| `describe_actions` | The touch-action vocabulary (`on_tap` / `on_swipe` / `actions`) for elements |
| `render_report` | Read back what rendered (values, overflow, live-vs-sample, colours) as JSON |
| `render_preview` | Render the canvas to a PNG the agent can see |
| `push_to_device` | Push the canvas to explicit device(s) |
| `bind_devices` | Persistently bind a canvas to a set of devices (do it early) |
| `list_rotations` / `create_rotation` / `delete_rotation` | Manage rotations (timed page cycles) |
| `list_schedules` / `create_schedule` / `delete_schedule` | Manage schedules (time-driven pushes) |
| `list_decks` / `suggest_decks` / `create_deck` / `delete_deck` | Manage decks (pre-rendered linked pages); `suggest_decks` derives them from tap/swipe links |

Write tools (`set_canvas`, `add_element`, `add_elements_bulk`, `update_element`,
`delete_element`, `patch_canvas`) accept optional `base_rev`: the `rev` from `get_canvas`.
If the page changed, the write returns a conflict and protects the UI edit.

Use `update_element` / `patch_canvas` for single-field changes.
Check renders with both `render_report` (structured data) and `render_preview` (image).

---

## Widget push (authoring)

Studio can install widgets over `/api/mcp` without a shared filesystem.
This works with remote, Home Assistant App, and Docker installs.
Developer pushes skip the public catalog entry and `sha256` check.

| Endpoint | What it does |
| --- | --- |
| `POST /api/mcp/widgets/install` | Install/upsert a widget from a tarball (`application/gzip` body, or `multipart` with a `tarball` part). Query: `id` (override), `reload` (`auto`/`in_process`/`restart`/`none`). |
| `DELETE /api/mcp/widgets/<id>` | Uninstall a pushed widget (only touches the `authored/` dir). |
| `POST /api/mcp/reload` | Rebuild the plugin registry without installing. Body/query `mode`. |
| `GET /api/mcp/widgets?origin=authored` | List pushed widgets with an `active` flag. |
| `GET /api/mcp/widgets/<id>/render.png` | Faithful e-ink PNG of a single widget (`size`, `opts`, `theme`, `style`). |

Pushed widgets live in `<data_root>/authored/<id>`, separate from catalog installs.
They survive restarts and App upgrades.

Archives have size and entry-count limits. Extraction blocks tar-slip path traversal.
Only valid `kind: "widget"` manifests with no bundled-id clash are accepted;
`server.py` is never imported during installation.

Reload happens in-process unless the widget adds an admin `blueprint()`, which needs a restart.
A scheduled restart returns `restarting: true`.
The client polls `/healthz`, then `/api/mcp/catalog`, until the id appears.

---

## What an agent can place

| Element | Content |
|---|---|
| Widget | A whole widget or fragment |
| Decoration | Text, rectangle, ellipse, line, or icon |
| Data primitive | A widget field shown as a scalable number, text, line, bar, or sparkline |
| Custom HTML | Static HTML + CSS in a sandboxed iframe |

Elements may extend past the panel edge. See `set_canvas` for each element's JSON format.

Any element can use live bindings (`bind`). These read a widget field on each
render and transform the element's properties.

Decorations are static without bindings. With bindings, they follow data
alongside data primitives, without polling.

| Transform | Effect |
|---|---|
| `position` | Move a marker |
| `length` | Grow a gauge |
| `pick` | Switch between discrete states |
| `color` | Change colour at thresholds |
| `gradient` | Interpolate between colour stops; quantise to the panel palette on e-ink |
| `icon` | Choose an icon from a condition |

See `set_canvas` for the full format.

## Give the dashboard its own settings page

Declare dashboard settings so users can change values without opening the composer.
Examples include an API endpoint, postcode, entity id, or city.
Add each value to the canvas `inputs` list with a target for the answer:

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

Tesserae adds a Settings page at `/pages/canvas/c/<id>/configure`, linked from Dashboards.
Each input gets a field pre-filled with the current rendered value.

Set `inputs` with `set_canvas`, or use `patch_canvas` after placing elements.
See `set_canvas` for fields and slots.

Inputs use the template format's declarations:

- Share uses the same install questions and author-supplied labels.
- On the dashboard's Settings page, *Suggest settings* finds values Share would redact and ask about, then adds them as settings.
  It keeps existing declarations and only adds new ones, including on dashboards with none.

`secret: true` keeps a value out of the render context and redacts it on share.

`mask` controls whether the field is a password box. It defaults to `secret`.
Set it `false` for sensitive values that need to stay readable, such as URLs.

## Guardrails

- The API **404s** entirely while MCP is switched off (Settings → System → MCP,
  or `TESSERAE_EXPERIMENT_MCP=0`).
- Remote callers need the token; a direct loopback connection is trusted so a
  co-located agent works with zero config. A request that arrives through a
  reverse proxy (any `X-Forwarded-For`, `Forwarded` or `X-Real-IP` header)
  always needs the token.
- Writes only touch **canvas** dashboards, never grid ones.
- Pages an agent creates are flagged **Agent** in the Dashboards list.
- Pushing is **always explicit**: the agent must name the device(s); nothing is
  pushed automatically.

---

## Troubleshooting

**"Cannot reach Tesserae at …"**: Tesserae isn't running at `TESSERAE_URL`, or
the port isn't reachable from the agent's machine. Confirm the URL in a browser.
For a Home Assistant install, the add-on must expose a **direct port** on your
LAN; an ingress-only setup can't be reached by an external agent.

**HTTP 401 / unauthorized**: the agent is calling from a non-loopback address
without a valid token. Generate one in Settings → System → MCP and set
`TESSERAE_MCP_TOKEN`. (On the same machine as Tesserae, check that
`TESSERAE_URL` really is `127.0.0.1` and not a reverse-proxied address; a
proxied request always needs the token.)

**HTTP 404 on every call**: MCP has been switched off. Enable it in Settings →
System → MCP (and check `TESSERAE_EXPERIMENT_MCP` isn't set to `0`).

**`tesserae-mcp: command not found`**: after `pipx install`, run
`pipx ensurepath` and open a new terminal. For Claude Desktop, use the absolute
path from `which tesserae-mcp` (GUI apps don't inherit your shell `PATH`).

**`externally-managed-environment`**: see the warning in Step 2; use pipx or a
venv rather than installing into the system Python.

---

## Notes

- The bridge needs a **running** Tesserae (rendering uses its headless browser).
- Core Tesserae never imports the `mcp` package; it lives entirely in the
  separate `tesserae-mcp` bridge.
- The API surface and canvas-document schema are the contract between the two.
  See [`packages/tesserae-mcp`](https://github.com/dmellok/tesserae/tree/main/packages/tesserae-mcp)
  for the bridge itself.
