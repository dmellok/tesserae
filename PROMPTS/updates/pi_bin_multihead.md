# Update: tesserae-pi-bin-client → multi-head / named-device topics

## Why

The Tesserae server now supports **multiple named devices** instead of
a single hardcoded `pi` / `esp32` pair. Each physical display is given
its own id and gets its own MQTT topic namespace:

```
tesserae/<device_id>/frame/bin    ← retained, JSON announcement
tesserae/<device_id>/status       ← this client's heartbeat
```

The default Pi kind still uses `device_id="pi"` for back-compat — but
this client currently **hardcodes** that prefix, so a second Pi
display (or any user-renamed instance) won't be reached.

This change makes the topic prefix configurable from a single
`device_id` setting, prompted for during install and stored in
`config.toml`.

## Goal

* Add a `device_id` config value (default `"pi"`).
* Derive `frame_topic` + `status_topic` from it — no other hardcoded
  topic strings remain.
* Prompt for it during `scripts/install.sh` (with the existing default
  `pi` so an Enter-press keeps current behaviour).
* Existing installs without `device_id` in their `config.toml` keep
  working — the parser defaults to `"pi"`.

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

`grep -rn "tesserae/pi/" src/` will find every hardcoded reference —
there should be exactly two after this change: nowhere. All callers
must consume the resolved topic from config.

## Implementation

### 1. `config.py` — add `device_id` to the schema

In `render_config_toml(...)`, add a new keyword (default `"pi"`)
emitted in the `[mqtt]` section near `client_id`:

```toml
[mqtt]
host = "..."
...
client_id = "pi-impression-1"
device_id = "pi"               # new — sets the MQTT topic prefix
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

Keep the old `STATUS_TOPIC` / `FRAME_TOPIC` constants exported but
mark them as `STATUS_TOPIC_LEGACY = "tesserae/pi/status"` so tests that
explicitly opt into the default-prefix case stay readable. Don't import
the legacy constants from `main.py` — that path resolves topics from
config now.

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
echo "    Use 'pi' if this is your only Pi display; pick something"
echo "    like 'pi_kitchen' if you're running more than one."
prompt_default device_id "Device id" "pi"
# basic client-side validation; the parser also enforces this
if ! [[ "$device_id" =~ ^[a-z][a-z0-9_-]{1,31}$ ]]; then
    echo "    invalid device id; falling back to 'pi'" >&2
    device_id="pi"
fi
```

Then export `T_DEVICE_ID="$device_id"` alongside the other `T_*`
vars before invoking `bootstrap_config`.

Add `--reconfigure` doc note that re-prompting overwrites the existing
device_id (it already does via `T_OVERWRITE=1`).

## Verification

1. **Fresh install (default)** — `scripts/install.sh --non-interactive`
   writes a config with `device_id = "pi"` and the client subscribes to
   `tesserae/pi/frame/bin` and publishes heartbeats on
   `tesserae/pi/status`. Behaviour identical to before this change.
2. **Fresh install (named)** — running the interactive installer and
   entering `pi_kitchen` writes that device_id, subscribes to
   `tesserae/pi_kitchen/frame/bin`, heartbeats on
   `tesserae/pi_kitchen/status`. Confirm with `mosquitto_sub -v -t
   'tesserae/#'` while the service runs.
3. **Existing install upgrade** — leave an old `config.toml` (no
   `device_id` line) in place, reinstall, restart the service.
   Parser fills the default `"pi"` and the client keeps working.
4. **Tests** — update the topic constants in the test suite to
   exercise both the legacy `pi` prefix and a custom one (e.g.
   `pi_kitchen`). `pytest` should still go green.
5. **Manual** — in the Tesserae UI, Settings → Devices → Add device,
   create a new instance with the same id you set on the Pi. Bind a
   page to that device and push. The Pi should paint.

## Out of scope

* No firmware-side capability to *change* device_id at runtime (the
  install script + a service restart is the workflow). If you want a
  CLI subcommand for it later, that's a separate task.
* No need to break or migrate existing `tesserae/pi/...` consumers —
  the default keeps that prefix.
