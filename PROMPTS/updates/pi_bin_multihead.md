# Update: tesserae-pi-bin-client → multi-head / named-device topics

## Why

The Tesserae server now supports **multiple named devices** and split
the old `pi_client` kind into `pi_bin_client` + `pi_png_client`. Each
kind has its own topic prefix; this client should match `pi_bin_client`
and use prefix `pi_bin` by default:

```
tesserae/<device_id>/frame/bin    ← QoS 1, JSON announcement
tesserae/<device_id>/status       ← this client's heartbeat
```

This client currently **hardcodes** `tesserae/pi/...`, which the new
server doesn't publish on. Two changes are needed: change the default
topic prefix from `pi` to `pi_bin`, and let the user pick a per-device
prefix for multi-Pi setups.

## Goal

* Add a `device_id` config value (default `"pi_bin"`).
* Derive `frame_topic` + `status_topic` from it — no other hardcoded
  topic strings remain.
* Prompt for it during `scripts/install.sh` (default `pi_bin`).
* Existing installs without `device_id` in their `config.toml` get the
  new default `"pi_bin"` from the parser — note this is a **breaking
  topic change** from older versions of this client. Document it in
  the upgrade instructions: if the user wants to keep the legacy
  `tesserae/pi/...` prefix they can set `device_id = "pi"` by hand
  and create a matching instance in Tesserae's Settings → Devices.

## Repo layout (the bits that need changing)

```
src/tesserae_pi_bin_client/
    config.py            ← add device_id to schema + parser + renderer
    bootstrap_config.py  ← T_DEVICE_ID env var → render_config_toml(device_id=...)
    heartbeat.py         ← STATUS_TOPIC constant → derive from device_id
    mqtt_loop.py         ← FRAME_TOPIC constant → derive from device_id
    main.py              ← thread the resolved topics through wiring
scripts/
    install.sh           ← prompt user for device_id, pass via T_DEVICE_ID
```

`grep -rn "tesserae/pi/" src/` will find every hardcoded reference.
All callers must consume the resolved topic from config.

## Implementation

### 1. `config.py` — add `device_id` to the schema

In `render_config_toml(...)`, add a new keyword (default `"pi_bin"`)
emitted in the `[mqtt]` section near `client_id`:

```toml
[mqtt]
host = "..."
...
client_id = "pi-impression-1"
device_id = "pi_bin"               # new — sets the MQTT topic prefix
```

In `parse_toml(...)` (the dataclass-builder in the same file), add a
`device_id: str` field to the config dataclass with the same default.
Validate it against a regex: `^[a-z][a-z0-9_-]{1,31}$` (lowercase,
2–32 chars, starts with a letter — matches what the Tesserae server
accepts for instance ids).

### 2. `heartbeat.py` + `mqtt_loop.py` — derive topics

Replace the module-level constants with topic builders:

```python
# heartbeat.py
def status_topic(device_id: str) -> str:
    return f"tesserae/{device_id}/status"

# mqtt_loop.py
def frame_topic(device_id: str) -> str:
    return f"tesserae/{device_id}/frame/bin"
```

Delete the old `STATUS_TOPIC` / `FRAME_TOPIC` constants — keeping them
exported as legacy aliases just invites accidental misuse. Update any
tests to compute the expected topic from a fixture `device_id`.

### 3. `main.py` — wire the resolved topics

`main()` already loads `Config` from disk. After that, compute the
two topics from `cfg.mqtt.device_id` and hand them to the
`HeartbeatPublisher(...)` and `MqttLoop(...)` constructors. Update
those constructors to accept `frame_topic: str` / `status_topic: str`
instead of importing the constants directly.

### 4. `bootstrap_config.py` — accept `T_DEVICE_ID`

Add to the env-var → kwarg map:

```python
("T_DEVICE_ID", "device_id"),
```

so `T_DEVICE_ID=pi_kitchen scripts/install.sh ...` writes
`device_id = "pi_kitchen"` into the generated `config.toml`.

### 5. `scripts/install.sh` — prompt for it

In `collect_config_via_prompts()`, add a prompt **before** the
MQTT-host question so the device id frames the rest of the config:

```bash
echo "    A device id identifies this Pi to the Tesserae server."
echo "    Use 'pi_bin' if this is your only BIN-protocol Pi display;"
echo "    pick something like 'pi_kitchen' if you're running more"
echo "    than one (each must have its own id)."
prompt_default device_id "Device id" "pi_bin"
# basic client-side validation; the parser also enforces this
if ! [[ "$device_id" =~ ^[a-z][a-z0-9_-]{1,31}$ ]]; then
    echo "    invalid device id; falling back to 'pi_bin'" >&2
    device_id="pi_bin"
fi
```

Then export `T_DEVICE_ID="$device_id"` alongside the other `T_*`
vars before invoking `bootstrap_config`.

Add a `--reconfigure` doc note that re-prompting overwrites the
existing device_id (it already does via `T_OVERWRITE=1`).

## Verification

1. **Fresh install (default)** — `scripts/install.sh --non-interactive`
   writes a config with `device_id = "pi_bin"` and the client subscribes
   to `tesserae/pi_bin/frame/bin` and publishes heartbeats on
   `tesserae/pi_bin/status`. Matches the built-in `pi_bin_client` kind
   on the server.
2. **Fresh install (named)** — running the interactive installer and
   entering `pi_kitchen` writes that device_id, subscribes to
   `tesserae/pi_kitchen/frame/bin`, heartbeats on
   `tesserae/pi_kitchen/status`. Confirm with `mosquitto_sub -v -t
   'tesserae/#'` while the service runs.
3. **Upgrade an existing install** — old `config.toml` parses with the
   new default `device_id = "pi_bin"`. **Breaking change** from the
   prior `tesserae/pi/...` prefix: the user must also create a
   matching instance in Tesserae (or leave the default kind
   `pi_bin_client` selected). README needs a one-line note about
   this.
4. **Tests** — update the topic constants in the test suite to take a
   `device_id` fixture and build the topic at test time. `pytest`
   should still go green.
5. **Manual** — in the Tesserae UI, Settings → Devices → Add device,
   create a new instance with the same id you set on the Pi. Bind a
   page to that device and push. The Pi should paint.

## Out of scope

* No firmware-side capability to *change* device_id at runtime (the
  install script + a service restart is the workflow). If you want a
  CLI subcommand for it later, that's a separate task.
* Discovery: a follow-up will expand the heartbeat to advertise
  `kind: "pi_bin_client"`, `panel_w`, `panel_h`, `fw_version`, etc.
  so the Tesserae UI can surface unconfigured devices automatically.
  That's a separate brief.
