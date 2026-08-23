# tesserae-mcp

> Part of the [Tesserae](https://github.com/dmellok/tesserae) monorepo
> (`packages/tesserae-mcp`), so the bridge stays in lockstep with the `/api/mcp`
> surface it wraps. Published to PyPI as `tesserae-mcp`.

The [MCP](https://modelcontextprotocol.io) bridge for
[Tesserae](https://github.com/dmellok/tesserae). It lets an AI agent (Claude
Desktop, Claude Code, or any MCP client) build **freeform (canvas) dashboards**
for your e-ink panels: it lists your widgets and devices, lays out a canvas,
**renders a preview to check its own work**, and pushes to a panel.

This is a thin stdio client. It talks to a running Tesserae over its `/api/mcp`
HTTP surface, so the rendering, widgets, and devices all come from your own
Tesserae instance.

```
create_canvas_page → set_canvas → render_preview → (look) → set_canvas → …
```

## Prerequisites

A running Tesserae with the MCP API enabled: **Settings → System → MCP → Enable
MCP API**. If this bridge runs on a *different* machine from Tesserae, also
**Regenerate token** there and copy it.

## Install

Run this on the machine where your **agent** runs (your laptop/desktop), which
may be different from where Tesserae runs.

```bash
pipx install tesserae-mcp
```

That gives you the `tesserae-mcp` command. Upgrade it later with `pipx upgrade
tesserae-mcp`; Tesserae flags an out-of-date bridge under **Settings → System →
MCP** once one has connected.

(From source: `pip install "git+https://github.com/dmellok/tesserae#subdirectory=packages/tesserae-mcp"`.
The standalone `dmellok/tesserae-mcp` repo is archived at 0.5.0 — development
moved into the monorepo.)

## Configure your agent

Point your MCP client at `tesserae-mcp`. Example (Claude Desktop / Claude Code
`mcpServers` config):

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

- `TESSERAE_URL` — where your Tesserae is reachable (default
  `http://127.0.0.1:8765`). For a Docker/Home Assistant install, use its LAN
  address, e.g. `http://192.168.1.50:8765` (the port must be reachable; HA
  ingress-only setups won't work).
- `TESSERAE_MCP_TOKEN` — the token from Settings. **Omit it** when the agent and
  Tesserae share a machine (loopback is trusted).

Then just ask: *"Build me an 800×480 dashboard with the time, today's weather for
Melbourne, and my next calendar event, then show me a preview."*

## Tools

| Tool | What it does |
| --- | --- |
| `list_widgets` | Every placeable widget (with fragments) + theme/style/font options |
| `get_widget_options` | A widget's options + format hints (big choice lists omitted by default) |
| `get_widget_choices` | Page through one option's choice rows (HA entity pickers etc.) |
| `probe_widget_data` | A widget's data + `data_source` (live/sample/error) + bindable field paths |
| `list_devices` | Registered panels: dimensions + colour capability (palette, mono flag) |
| `list_pages` | Existing canvas dashboards |
| `create_canvas_page` | Create an empty canvas (size it to your panel) |
| `get_canvas` | Read a canvas document (returns a `rev` for concurrency-safe writes) |
| `set_canvas` | Replace a canvas document (422 with field errors if invalid) |
| `add_element` | Append one element (live-updates an open editor) |
| `update_element` | Change one element in place (no full re-send) |
| `delete_element` | Remove one element |
| `patch_canvas` | Change document-level fields (size, theme, bg) without touching elements |
| `arrange` | Compute aligned grid/row/column boxes so you lay out by intent, not pixels |
| `measure_text` | Measure rendered text width/height so a box fits its content |
| `render_report` | Read back what rendered (values, overflow, live-vs-sample, colours) as JSON |
| `render_preview` | Render the canvas to a PNG the agent can see |
| `push_to_device` | Push the canvas to explicit device(s) |

Writes (`set_canvas`, `add_element`, `update_element`, `delete_element`, `patch_canvas`)
accept an optional `base_rev` (from `get_canvas`); if the page changed since, the
write returns HTTP 409 so you re-read instead of clobbering a concurrent edit.

## Run without installing

From a clone:

```bash
pip install mcp
TESSERAE_URL=http://127.0.0.1:8765 python -m tesserae_mcp
```

## License

AGPL-3.0-or-later, matching Tesserae.
