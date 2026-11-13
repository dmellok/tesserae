# MCP servers: install & use

Choose an MCP server for your AI agent. MCP (Model Context Protocol) works with
Claude Code, Claude Desktop, and other clients. The two servers are independent;
install whichever you need:

| Server | Package / command | Talks to | The agent can |
| --- | --- | --- | --- |
| **tesserae-mcp** | `tesserae-mcp` | a running **Tesserae** (`/api/mcp`) | compose canvas dashboards, render previews, push to devices |
| **tesserae-studio-mcp** | `tesserae-studio-mcp` | a running **Studio** (`:8770`) | scaffold, lint, register, render, and publish **widgets** |

Both bridges pass requests over standard input/output (stdio) to the app's HTTP API.
`tesserae-mcp` needs Tesserae running. `tesserae-studio-mcp` needs [Studio](studio.md),
which connects to Tesserae for live data and faithful renders.

Each server sends instructions when the agent connects. You do not need to paste a workflow prompt.

---

## tesserae-mcp (build dashboards)

Build canvas dashboards, preview them, and push them to panels.
See [Build dashboards with AI (MCP)](mcp.md) for all tools.

**1. Get a token (remote agents only).** The API is on by default. If the agent runs on a
*different* machine from Tesserae, open Settings → System → MCP, **Regenerate token** and
copy it.

**2. Install the bridge** on the machine where your *agent* runs:

```bash
pipx install tesserae-mcp
```

This installs `tesserae-mcp`. The source lives in `packages/tesserae-mcp`.
To install from source, run
`pip install "git+https://github.com/dmellok/tesserae#subdirectory=packages/tesserae-mcp"`.

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

- `TESSERAE_URL`: where Tesserae is reachable (default `http://127.0.0.1:8765`). For a
  Docker / Home Assistant install, use its LAN address, e.g. `http://192.168.1.50:8765`.
  The port must be reachable; HA ingress-only setups won't work.
- `TESSERAE_MCP_TOKEN`: the token from Settings. **Omit it** when the agent and Tesserae
  share a machine (loopback is trusted).

**4. Use it.** Ask the agent, e.g. *"Build an 800×480 dashboard with the time and today's
weather for Melbourne, preview it, then push it to the kitchen panel."*

| Task | Tools |
|---|---|
| Inspect | `list_widgets`, `get_widget_options`, `probe_widget_data`, `list_devices` |
| Edit | `create_canvas_page`, `set_canvas` / `add_element` / `update_element` |
| Lay out | `arrange`, `measure_text` |
| Check | `render_preview`, `render_report` |
| Send | `push_to_device` |

Pushing is always explicit; the agent must name the device.

---

## tesserae-studio-mcp (build widgets)

Build widgets and prepare catalog PRs in a Studio workspace.
Follow [Build widgets with Studio](studio.md).

**1. Run Studio.** Install and start it (see [Build widgets with Studio](studio.md)); it
serves on `http://localhost:8770` and should point at your Tesserae via
`STUDIO_TESSERAE_URL` for live data + faithful render.

**2. Install the bridge** on the machine where your *agent* runs:

```bash
pip install tesserae-studio-mcp
```

This installs `tesserae-studio-mcp`. The source lives in `packages/tesserae-studio-mcp`.
To install from source, run
`pip install "git+https://github.com/dmellok/tesserae-studio#subdirectory=packages/tesserae-studio-mcp"`.
Source installs of the Studio server also include the bridge.

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

- `STUDIO_URL`: where the Studio server is reachable (default `http://localhost:8770`).

**4. Use it.** Ask the agent, e.g. *"Scaffold a stat widget that shows my GitHub stars,
lint it, register it, and show me the faithful render at every size."*

| Task | Tools |
|---|---|
| Create | `scaffold_widget`, `scaffold_bundle` |
| Edit | `list_files` / `read_file` / `write_file` |
| Check | `lint_widget`, `register_widget`, `faithful_render` |
| Inspect data | `mine_data_schema`, `widget_data` |
| Publish | `package_widget`, `generate_catalog_entry`, `open_catalog_pr` |

---

## Client configuration

Any MCP-capable client works, including custom clients built with the MCP SDK.
Only the config file format changes. Commands and environment variables stay the same.

What every client needs:

| | tesserae-mcp | tesserae-studio-mcp |
| --- | --- | --- |
| command | `tesserae-mcp` | `tesserae-studio-mcp` |
| env | `TESSERAE_URL` (+ `TESSERAE_MCP_TOKEN` for a remote target) | `STUDIO_URL` |

If the console scripts aren't on your `PATH`, use `command: "python"` with
`args: ["-m", "tesserae_mcp"]` (or `["-m", "tesserae_studio_mcp"]`).

Claude Desktop / Code, Cursor, Windsurf, and Cline share the `mcpServers` JSON format.
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

- Claude Desktop → `claude_desktop_config.json`; Claude Code → `claude mcp add …`
  (see above) or a project `.mcp.json`.
- Cursor → `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project).
- Windsurf → `~/.codeium/windsurf/mcp_config.json`.
- Cline → the extension's `cline_mcp_settings.json`.

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

Check your client's MCP support:

- It should show the server's connection instructions. Claude clients do;
  otherwise paste the workflow from the linked pages above.
- It may show images from `render_preview` / `faithful_render`.
  Text-only clients still get all other tools; use `render_report` for structured checks.

---

## Using both together

Connect both servers to build a widget and use it on a dashboard:

1. With tesserae-studio-mcp: scaffold → lint → register the widget, then
   `faithful_render` to check it.
2. With tesserae-mcp: `probe_widget_data` the new widget for its bindable fields, then
   `create_canvas_page` / `set_canvas` to place it on a dashboard, `render_preview`, and
   `push_to_device`.

If a new widget is blank or says "Failed to fetch dynamically imported module .../client.js",
its asset route may need a reload. Tesserae has loaded `server.py` but not the new static route.
Real output from `probe_widget_data` confirms this.

Reload/restart Tesserae and render again. Already-registered widgets never need this reload.

## Auth and networking notes

- Same-machine loopback connections need no token.
  Remote / Home Assistant targets need `TESSERAE_MCP_TOKEN`, or `STUDIO_TESSERAE_MCP_TOKEN`
  for Studio's connection to remote Tesserae. The port must be reachable; ingress-only HA setups won't work.
- The `tesserae-mcp` surface is on by default. If it 404s, it has been switched off in
  Settings → System → MCP (or by `TESSERAE_EXPERIMENT_MCP=0`).
