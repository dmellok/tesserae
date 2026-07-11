# Build dashboards with AI (MCP)

Tesserae ships an optional [MCP](https://modelcontextprotocol.io) server so an AI
agent (Claude Desktop, Claude Code, or any MCP client) can build **freeform
(canvas) dashboards** for you: it lists your widgets and devices, lays out a
canvas, **renders a preview to check its own work**, and pushes to a panel.

It's experimental and off by default.

## How it works

The canvas editor is, underneath, a JSON-document editor. The MCP server lets an
agent write that same document directly and render a preview PNG to see the
result, so it can place, look, and adjust:

```
create_canvas_page → set_canvas → render_preview → (look) → set_canvas → …
```

Two pieces:

- **The API** (`/api/mcp/*`) — a token-authed surface built into Tesserae, gated
  behind the `mcp` experiment.
- **The bridge** (`tesserae-mcp`) — a small stdio MCP server your agent launches;
  it talks to a running Tesserae. Ships as an optional extra.

## Enable it

1. In Tesserae, go to **Settings → System → MCP** and click **Enable MCP API**.
2. If your agent runs on a **different machine** from Tesserae, click **Regenerate
   token** and copy it. On the **same machine** you can skip the token (loopback
   is trusted).
3. Install the bridge:

    ```bash
    pip install "tesserae[mcp]"
    ```

## Connect an agent

Point your MCP client at the `tesserae-mcp` command. Example (Claude Desktop /
Claude Code `mcpServers` config):

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

- `TESSERAE_URL` — where your Tesserae is reachable (default `http://127.0.0.1:8765`).
- `TESSERAE_MCP_TOKEN` — the token from Settings. Omit it when the agent and
  Tesserae share a machine.

Then just ask: *"Build me an 800×480 dashboard with the time, today's weather for
Melbourne, and my next calendar event, then show me a preview."*

## Tools

| Tool | What it does |
| --- | --- |
| `list_widgets` | Every placeable widget (with fragments) + theme/style/font options |
| `get_widget_options` | A widget's configurable options (e.g. a weather location) |
| `list_devices` | Registered panels with their pixel dimensions |
| `list_pages` | Existing canvas dashboards |
| `create_canvas_page` | Create an empty canvas (size it to your panel) |
| `get_canvas` | Read a canvas document |
| `set_canvas` | Replace a canvas document (422 with field errors if invalid) |
| `render_preview` | Render the canvas to a PNG the agent can see |
| `push_to_device` | Push the canvas to explicit device(s) |

## Guardrails

- The API **404s** entirely while the `mcp` experiment is off.
- Remote callers need the token; loopback is trusted so a co-located agent works
  with zero config.
- Writes only touch **canvas** dashboards, never grid ones.
- Pages an agent creates are flagged **Agent** in the Dashboards list.
- Pushing is **always explicit** — the agent must name the device(s); nothing is
  pushed automatically.

## Notes

- The bridge needs a **running** Tesserae (rendering uses its headless browser).
- Core Tesserae never imports the `mcp` package; only the `tesserae-mcp` bridge
  does, via the `[mcp]` extra.
