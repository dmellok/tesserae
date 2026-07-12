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
    pipx install git+https://github.com/dmellok/tesserae-mcp
    ```

    Confirm it's on your `PATH`:

    ```bash
    which tesserae-mcp
    ```

    (You may need to open a new terminal after `pipx ensurepath`.)

=== "venv"

    ```bash
    python3 -m venv ~/.tesserae-mcp
    ~/.tesserae-mcp/bin/python -m pip install git+https://github.com/dmellok/tesserae-mcp
    ```

    The command is then `~/.tesserae-mcp/bin/tesserae-mcp` (use that full path in
    the config below).

=== "from a clone"

    ```bash
    git clone https://github.com/dmellok/tesserae-mcp
    cd tesserae-mcp
    python3 -m pip install mcp
    # run with:  python -m tesserae_mcp
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
agent saves, so you can watch it place and adjust elements. If you start editing
yourself, it won't overwrite your unsaved changes — you'll get a "changed
externally" reload prompt instead.

---

## Tools

| Tool | What it does |
| --- | --- |
| `list_widgets` | Every placeable widget (with fragments) + theme/style/font options |
| `get_widget_options` | A widget's configurable options (e.g. a weather location) |
| `probe_widget_data` | A widget's live data payload, to discover real field names/shapes |
| `list_devices` | Registered panels with their pixel dimensions |
| `list_pages` | Existing canvas dashboards |
| `create_canvas_page` | Create an empty canvas (size it to your panel) |
| `get_canvas` | Read a canvas document |
| `set_canvas` | Replace a canvas document (compact ack; `?return=doc` for the full doc) |
| `add_element` | Append one element (each call saves, so an open editor updates live) |
| `render_preview` | Render the canvas to a PNG the agent can see |
| `push_to_device` | Push the canvas to explicit device(s) |

---

## What an agent can place

A canvas element is one of: a **widget** (or a fragment of one), a **decoration**
(text, rectangle, ellipse, line, icon), a **data primitive** (a widget data field
shown as a scalable number / text / line / bar / sparkline), or a **custom HTML**
block (static HTML + CSS in a sandboxed iframe). Elements may sit partly off the
panel edge. The exact JSON shape for each is in the `set_canvas` tool description.

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
