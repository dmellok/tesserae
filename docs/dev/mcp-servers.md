# MCP servers: install & use

Tesserae has two Model Context Protocol (MCP) servers, so an AI agent (Claude Code, Claude
Desktop, or any MCP client) can do two different jobs. They are independent, install and
enable whichever you need:

| Server | Package / command | Talks to | The agent can |
| --- | --- | --- | --- |
| **tesserae-mcp** | `tesserae-mcp` | a running **Tesserae** (`/api/mcp`) | compose canvas dashboards, render previews, push to devices |
| **tesserae-studio-mcp** | `tesserae-studio-mcp` | a running **Studio** (`:8770`) | scaffold, lint, register, render, and publish **widgets** |

Both are thin stdio bridges: they carry no logic of their own, they call the HTTP surface
of the app behind them. So `tesserae-mcp` needs a running Tesserae, and
`tesserae-studio-mcp` needs a running [Studio](studio.md) (which in turn connects to a
Tesserae for live data + faithful render). Each server sends the agent its own operating
instructions at connect, so the agent follows the right loop without you pasting a prompt.

---

## tesserae-mcp (build dashboards)

Lets an agent build freeform canvas dashboards for your panels: list widgets and devices,
lay out a canvas, render a preview to check its work, and push to a device. Full capability
reference: [Build dashboards with AI (MCP)](mcp.md).

**1. Enable the API in Tesserae.** Settings → System → MCP → **Enable MCP API**. If the
agent runs on a *different* machine from Tesserae, also **Regenerate token** and copy it.

**2. Install the bridge** on the machine where your *agent* runs:

```bash
pip install tesserae-mcp
```

That gives you the `tesserae-mcp` command. (The bridge lives in the Tesserae repo under
`packages/tesserae-mcp`; to install straight from source use
`pip install "git+https://github.com/dmellok/tesserae#subdirectory=packages/tesserae-mcp"`.)

**3. Configure your agent.** Claude Desktop / Claude Code `mcpServers` config:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "tesserae-mcp",
      "env": {
        "TESSERAE_URL": "http://127.0.0.1:8765",
        "TESSERAE_MCP_TOKEN": "<your-token>"
      }
    }
  }
}
```

Or add it to Claude Code in one line:

```bash
claude mcp add tesserae \
  -e TESSERAE_URL=http://127.0.0.1:8765 \
  -e TESSERAE_MCP_TOKEN=<your-token> \
  -- tesserae-mcp
```

- `TESSERAE_URL` — where Tesserae is reachable (default `http://127.0.0.1:8765`). For a
  Docker / Home Assistant install, use its LAN address, e.g. `http://192.168.1.50:8765`
  (the port must be reachable; HA ingress-only setups won't work).
- `TESSERAE_MCP_TOKEN` — the token from Settings. **Omit it** when the agent and Tesserae
  share a machine (loopback is trusted).

**4. Use it.** Ask the agent, e.g. *"Build an 800×480 dashboard with the time and today's
weather for Melbourne, preview it, then push it to the kitchen panel."* Key tools:
`list_widgets`, `get_widget_options`, `probe_widget_data`, `list_devices`,
`create_canvas_page`, `set_canvas` / `add_element` / `update_element`, `arrange`,
`measure_text`, `render_preview`, `render_report`, `push_to_device`. Pushing is always
explicit, the agent must name the device.

---

## tesserae-studio-mcp (build widgets)

Lets an agent author widgets in a Studio workspace: scaffold, edit, lint, register to your
Tesserae, faithfully render, mine a data schema, and prepare a catalog PR. Full workflow:
[Build widgets with Studio](studio.md).

**1. Run Studio.** Install and start it (see [Build widgets with Studio](studio.md)); it
serves on `http://localhost:8770` and should point at your Tesserae via
`STUDIO_TESSERAE_URL` for live data + faithful render.

**2. Install the bridge** on the machine where your *agent* runs:

```bash
pip install tesserae-studio-mcp
```

That gives you the `tesserae-studio-mcp` command. (The bridge lives in the Tesserae Studio repo
under `packages/tesserae-studio-mcp`; to install straight from source use
`pip install "git+https://github.com/dmellok/tesserae-studio#subdirectory=packages/tesserae-studio-mcp"`.
It also still ships with the Studio server package for source installs.)

**3. Configure your agent.** Claude Desktop / Claude Code `mcpServers` config:

```json
{
  "mcpServers": {
    "tesserae-studio": {
      "command": "tesserae-studio-mcp",
      "env": { "STUDIO_URL": "http://localhost:8770" }
    }
  }
}
```

Or, with the package installed, add it to Claude Code in one line:

```bash
claude mcp add tesserae-studio -e STUDIO_URL=http://localhost:8770 -- tesserae-studio-mcp
```

- `STUDIO_URL` — where the Studio server is reachable (default `http://localhost:8770`).

**4. Use it.** Ask the agent, e.g. *"Scaffold a stat widget that shows my GitHub stars,
lint it, register it, and show me the faithful render at every size."* Key tools:
`scaffold_widget`, `scaffold_bundle`, `list_files` / `read_file` / `write_file`,
`lint_widget`, `register_widget`, `faithful_render`, `mine_data_schema`, `widget_data`,
`package_widget`, `generate_catalog_entry`, `open_catalog_pr`.

---

## Client configuration

Both servers are standard stdio MCP servers with nothing Claude-specific in them, so any
MCP-capable client works, Claude Desktop / Code, Cursor, Windsurf, Cline, VS Code (Copilot
agent mode), Codex CLI, Zed, or a custom client on the MCP SDK. Only the config **file
format** differs; the command and environment variables are the same everywhere.

What every client needs:

| | tesserae-mcp | tesserae-studio-mcp |
| --- | --- | --- |
| command | `tesserae-mcp` | `tesserae-studio-mcp` |
| env | `TESSERAE_URL` (+ `TESSERAE_MCP_TOKEN` for a remote target) | `STUDIO_URL` |

If the console scripts aren't on your `PATH`, use `command: "python"` with
`args: ["-m", "tesserae_mcp"]` (or `["-m", "tesserae_studio_mcp"]`).

**Claude Desktop / Code, Cursor, Windsurf, Cline** share the `mcpServers` JSON shape.
The same block works in each; only the file it goes in differs:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "tesserae-mcp",
      "env": { "TESSERAE_URL": "http://127.0.0.1:8765", "TESSERAE_MCP_TOKEN": "<token>" }
    },
    "tesserae-studio": {
      "command": "tesserae-studio-mcp",
      "env": { "STUDIO_URL": "http://localhost:8770" }
    }
  }
}
```

- **Claude Desktop** → `claude_desktop_config.json`; **Claude Code** → `claude mcp add …`
  (see above) or a project `.mcp.json`.
- **Cursor** → `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project).
- **Windsurf** → `~/.codeium/windsurf/mcp_config.json`.
- **Cline** → the extension's `cline_mcp_settings.json`.

**Codex CLI** (`~/.codex/config.toml`):

```toml
[mcp_servers.tesserae]
command = "tesserae-mcp"
env = { TESSERAE_URL = "http://127.0.0.1:8765", TESSERAE_MCP_TOKEN = "<token>" }

[mcp_servers.tesserae-studio]
command = "tesserae-studio-mcp"
env = { STUDIO_URL = "http://localhost:8770" }
```

**VS Code (Copilot agent mode)** (`.vscode/mcp.json`):

```json
{
  "servers": {
    "tesserae": {
      "type": "stdio",
      "command": "tesserae-mcp",
      "env": { "TESSERAE_URL": "http://127.0.0.1:8765", "TESSERAE_MCP_TOKEN": "<token>" }
    }
  }
}
```

**OpenAI Agents SDK / custom clients** point an stdio MCP transport at the command:

```python
from agents.mcp import MCPServerStdio

tesserae = MCPServerStdio(params={
    "command": "tesserae-mcp",
    "env": {"TESSERAE_URL": "http://127.0.0.1:8765"},
})
```

Other clients (Zed, Continue, …) take the same command + env in their own MCP /
context-server config; see the client's MCP docs for the file location and key names.

Two behaviours depend on how rich the client's MCP support is, not on the servers: whether
it surfaces the server's handshake **instructions** (Claude clients do; if not, paste the
loop from the pages above), and whether it renders **image** tool results (`render_preview`
/ `faithful_render`), a text-only client still gets every other tool, use `render_report`
for structured verification instead.

---

## Using both together

The two servers compose. A typical end-to-end session connects **both** and moves between
them:

1. With **tesserae-studio-mcp**: scaffold → lint → register the widget, then
   `faithful_render` to check it.
2. With **tesserae-mcp**: `probe_widget_data` the new widget for its bindable fields, then
   `create_canvas_page` / `set_canvas` to place it on a dashboard, `render_preview`, and
   `push_to_device`.

If a freshly registered widget renders blank ("Failed to fetch dynamically imported module
.../client.js"), that is the new-widget restart gate, not a canvas or client bug: Tesserae
has hot-loaded the widget's `server.py` but not yet wired the static-asset route for the new
plugin id. `probe_widget_data` returning the real server output confirms it. Reload / restart
Tesserae and re-render; widget *updates* (already registered) never hit this.

## Auth and networking notes

- Both bridges are trusted over **loopback** with no token when the agent shares a machine
  with the app. A **remote / Home Assistant** target needs the bearer token
  (`TESSERAE_MCP_TOKEN`, or `STUDIO_TESSERAE_MCP_TOKEN` for Studio's own reach into a remote
  Tesserae), and the app's port must be reachable (ingress-only HA setups won't work).
- The `tesserae-mcp` surface is gated behind the **MCP experiment** and 404s until you
  enable it in Settings → System → MCP.
