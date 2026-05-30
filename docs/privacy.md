# Privacy & telemetry

**Off by default.** Tesserae ships with no usage telemetry enabled. A
fresh clone or install never phones home.

## What's sent when you opt in

When you opt in (Settings → Server → App), Tesserae posts at most **two
anonymous events** to the project's analytics backend (running the
open-source [aptabase/aptabase][aptabase]) so the maintainer can see how
many people are running Tesserae and what versions they're on:

- **`app.started`** — once per process start. Carries the Tesserae
  version, Python version, and platform name.
- **`update.applied`** — when the in-app updater applies a new
  revision. Carries the from/to short SHAs, the channel (edge/stable),
  and whether deps were reinstalled.

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

**You control whether to send; you don't control where it goes.**

## How to disable

Untick *Send anonymous usage telemetry* in **Settings → Server → App**,
or set the kill switch environment variable (wins over stored
settings):

```sh
export TESSERAE_TELEMETRY=0
```

[aptabase]: https://github.com/aptabase/aptabase
[telemetry-py]: https://github.com/dmellok/tesserae/blob/main/app/telemetry.py
