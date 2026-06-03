# Privacy & telemetry

**Off by default.** Tesserae ships with no usage telemetry enabled. A
fresh clone or install never phones home.

## What's sent when you opt in

When you opt in (Settings → Server → App), Tesserae posts a small set
of **anonymous events** to the project's analytics backend (running the
open-source [aptabase/aptabase][aptabase]) so the maintainer can see how
many people are running Tesserae, what versions they're on, and roughly
how active a typical install is:

- **`app.started`** — once per process start. Carries the Tesserae
  version, Python version, and platform name.
- **`app.heartbeat`** — every hour while the process is running. Lets
  the maintainer see session duration / daily-active counts instead of
  only process-start counts. Props carry **shape, not content**:
  - fleet shape: `n_devices`, `device_kinds` (kinds only, e.g.
    `pi_bin,esp32_bin`), `n_pages`, `n_user_themes`, `is_docker`,
    `is_homeassistant`
  - activity counters since the previous heartbeat:
    `n_pushes_since_last`, `n_push_failures_since_last`,
    `n_widget_errors_since_last`
- **`update.applied`** — when the in-app updater applies a new
  revision. Carries the from/to short SHAs, the channel (edge/stable),
  and whether deps were reinstalled.
- **`theme.user_created`** — the first time a user persists a custom
  theme. Fires once per install — so the maintainer sees how often the
  theme builder is actually reached. **No theme content** (palette
  values, name, tokens) is sent.

## What's never sent

The only stable identifier is a random UUID generated on first run and
written to `data/core/.instance_id`. Tesserae never sends:

- IP addresses
- hostnames
- file paths
- settings values
- secrets (passwords, tokens, API keys)
- push contents
- dashboard layouts
- broker addresses
- anything tied to a real-world identity

## The endpoint

The endpoint is hard-coded in [`app/telemetry.py`][telemetry-py] —
it's the maintainer's analytics deployment, not user-configurable. That
keeps opted-in counts adding up to a real total instead of being
scattered across whoever set up their own backend.

## How to disable

Untick *Send anonymous usage telemetry* in **Settings → Server → App**,
or set the kill switch environment variable (wins over stored
settings):

```sh
export TESSERAE_TELEMETRY=0
```

[aptabase]: https://github.com/aptabase/aptabase
[telemetry-py]: https://github.com/dmellok/tesserae/blob/main/app/telemetry.py
