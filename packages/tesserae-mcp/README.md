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

**Discover**

| Tool | What it does |
| --- | --- |
| `list_widgets` | Every placeable widget (with fragments), summarised; `section="appearance"` for themes, styles and fonts; `full=True` for the whole catalog |
| `get_widget_options` | A widget's options + format hints (big choice lists omitted by default) |
| `get_widget_choices` | Page through one option's choice rows (HA entity pickers etc.) |
| `probe_widget_data` | A widget's data + `data_source` (live/sample/error) + bindable field paths; long lists truncated unless `full=True` |
| `list_services` | Non-placeable service plugins (Open-Meteo, REST/JSON, Home Assistant) usable as code/data element sources |
| `list_icons` | Search the vendored Phosphor icon set for a valid slug |
| `list_fonts` / `add_font` / `delete_font` | Cache a Google Fonts family or a single woff2/ttf/otf face on the server, list the cache, remove an entry |
| `list_devices` | Registered panels: dimensions, colour capability, touch flag, firmware capabilities (overlay, deck cache, protocol) |
| `describe_actions` | The authoritative touch-action vocabulary |

**Build**

| Tool | What it does |
| --- | --- |
| `list_pages` | Existing canvas dashboards |
| `create_canvas_page` | Create an empty canvas (size it to your panel) |
| `delete_canvas_page` | Remove a canvas dashboard |
| `get_canvas` | Read a canvas document (returns a `rev` for concurrency-safe writes) |
| `set_canvas` | Replace a canvas document (422 with field errors if invalid) |
| `patch_canvas` | Change document-level fields (size, theme, bg) without touching elements |
| `set_canvas_background` | Generate a full-bleed background image from a prompt (fal.ai) |
| `add_element` | Append one element (live-updates an open editor) |
| `add_elements_bulk` | Append many elements in one all-or-nothing save |
| `update_element` | Change one element in place (no full re-send) |
| `append_code` | Append to a code element's `html` / `css` / `js`, streaming it in chunk by chunk |
| `delete_element` | Remove one element |
| `arrange` | Compute aligned grid/row/column boxes so you lay out by intent, not pixels |
| `measure_text` | Measure rendered text width/height so a box fits its content |

**Check and ship**

| Tool | What it does |
| --- | --- |
| `render_preview` | Render the canvas to a PNG the agent can see (`fresh=True` bypasses caches) |
| `render_report` | Read back what rendered (values, overflow, colours, tap regions, invalid actions and icons) as JSON; `debug=True` adds console errors, failed requests, dropped CSS and font load status |
| `bind_devices` | Persist the canvas's target device set for Send and scheduling |
| `push_to_device` | Render once and push the canvas to explicit device(s) |

**Schedule and navigate**

| Tool | What it does |
| --- | --- |
| `list_rotations` / `create_rotation` / `delete_rotation` | Ordered page cycles that advance on a wall-clock interval |
| `list_schedules` / `create_schedule` / `delete_schedule` | Time-driven pushes of a page to its devices |
| `list_decks` / `create_deck` / `delete_deck` | Pages kept pre-rendered per device so buttons and taps switch instantly |
| `suggest_decks` | Derive a deck from the `page:<id>` links already on elements |

Writes (`set_canvas`, `add_element`, `add_elements_bulk`, `update_element`,
`delete_element`, `patch_canvas`) accept an optional `base_rev` (from
`get_canvas`); if the page changed since, the write returns HTTP 409 so you
re-read instead of clobbering a concurrent edit.

Release history is in [CHANGELOG.md](CHANGELOG.md).

## Run without installing

From a clone:

```bash
pip install mcp
TESSERAE_URL=http://127.0.0.1:8765 python -m tesserae_mcp
```

## License

AGPL-3.0-or-later, matching Tesserae.
