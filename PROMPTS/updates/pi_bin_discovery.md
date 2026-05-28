# Update: tesserae-pi-bin-client → discovery heartbeat fields

## Why

Tesserae now listens on `tesserae/+/status` and surfaces any
unregistered device id in Settings → Devices as a "Discovered" row
with a one-click Register button. To make that one-click flow
pre-fill the kind and panel size, this client needs to embed a few
well-known keys in its heartbeat payload.

Apply this **after** the [pi_bin_multihead.md](pi_bin_multihead.md)
update — that one introduced the `device_id` config knob; this one
extends the heartbeat body the publisher emits on that topic.

## Goal

Heartbeat JSON published to `tesserae/<device_id>/status` includes
these top-level keys (in addition to whatever the client already
sends — Tesserae merges, doesn't replace):

| Key | Type | Source |
|---|---|---|
| `kind` | string — always `"pi_bin_client"` | constant |
| `panel_w` | int (px) | resolved panel width |
| `panel_h` | int (px) | resolved panel height |
| `fw_version` | string | `importlib.metadata.version("tesserae-pi-bin-client")` |
| `ip` | string (optional) | primary outbound interface, blank on failure |

`panel_w` / `panel_h` are the **post-rotation** painted dims (whatever
the Inky panel actually consumes), so Tesserae's one-click register
pre-fills the right values without the user thinking about
orientation.

## Implementation

### 1. `heartbeat.py` — extend the status dataclass

Find the dataclass that currently models the heartbeat payload
(probably `Status` or similar) and add the five fields above. Keep
them optional in the type (default `""` / `0` / `None`) so existing
tests don't have to construct the full set.

Compute the values once at startup (they don't change between
heartbeats):

```python
from importlib.metadata import PackageNotFoundError, version

def _fw_version() -> str:
    try:
        return version("tesserae-pi-bin-client")
    except PackageNotFoundError:
        return "0.0.0+unknown"

def _primary_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # no packets sent — just routes the socket
        ip = s.getsockname()[0]
        s.close()
        return str(ip)
    except OSError:
        return ""
```

Wire `kind="pi_bin_client"` as a constant. Pass `panel_w`, `panel_h`
in from the resolved `Panel` (the painter already knows its target
dims — surface them via a property or pass via the constructor).

### 2. JSON serialisation

The existing `to_json()` (or whatever the heartbeat publisher uses)
keeps emitting the same shape — just with these extra keys included.
**Don't** rename existing keys; Tesserae's settings page picks up
arbitrary keys and renders them as KV rows.

### 3. Tests

Add a single test that:
1. Builds a `Status` with the new fields populated.
2. Asserts `to_json()` includes `kind`, `panel_w`, `panel_h`,
   `fw_version`.
3. Asserts the JSON parses back to the same values.

`ip` is environment-dependent — test the helper in isolation by
patching `socket.socket`, or just don't cover it.

## Verification

1. **Manual** — run the client against a broker, `mosquitto_sub -t
   'tesserae/+/status' -v` should show the new keys in the JSON.
2. **End-to-end** — in Tesserae's Settings → Devices, before
   registering the device, the "Discovered devices" strip should
   appear with the right kind chip (`pi_bin_client`) and the panel
   dims pre-filled. Clicking Register creates the instance without
   prompting for kind / panel.

## Out of scope

* No client config file changes — the heartbeat fields are derived
  at runtime.
* No back-compat shim for old Tesserae servers — extra keys in a JSON
  heartbeat are ignored by older parsers, so this is safe to ship.
