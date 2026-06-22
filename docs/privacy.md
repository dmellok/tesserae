# Privacy & telemetry

**Off by default.** Tesserae ships with no usage telemetry enabled. A
fresh clone or install never phones home.

## What's sent when you opt in

When you opt in (Settings → Server → App), Tesserae posts a small set
of **anonymous events** to the project's analytics backend
([PostHog Cloud][posthog], US region) so the maintainer can see how
many people are running Tesserae, what versions they're on, and roughly
how active a typical install is:

- **`app.started`**, once per process start. Carries the Tesserae
  version, Python version, and platform name.
- **`app.heartbeat`**, every hour while the process is running. Lets
  the maintainer see session duration / daily-active counts instead of
  only process-start counts. Props carry **shape, not content**:
  - fleet shape: `n_devices`, `device_kinds` (kinds only, e.g.
    `pi_bin,esp32_bin`), `n_pages`, `n_user_themes`, `is_docker`,
    `is_homeassistant`
  - activity counters since the previous heartbeat:
    `n_pushes_since_last`, `n_push_failures_since_last`,
    `n_widget_errors_since_last`
- **`update.applied`**, when the in-app updater applies a new
  revision. Carries the from/to short SHAs, the channel (edge/stable),
  and whether deps were reinstalled.
- **`theme.user_created`**, the first time a user persists a custom
  theme. Fires once per install, so the maintainer sees how often the
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

## PostHog privacy hardening

The events Tesserae sends to PostHog explicitly disable the
surveillance features that would otherwise apply by default:

- **No IP storage**. Each event carries `$ip = ""` so the request IP
  is never written to the stored event.
- **Country and region only**. PostHog enriches each event with the
  country and region derived from the request IP at ingestion time,
  then drops the IP itself. No city, no latitude/longitude, no
  neighbourhood-level data. This lets the maintainer see roughly
  where Tesserae is running so they can plan hardware support and
  docs translations.
- **No person profile**. `$process_person_profile = false` keeps each
  event from creating or updating a "person" record — the install
  UUID is the only identity surface and it's never enriched.
- **No session recording**. Server-side captures don't support it,
  and the docs site explicitly disables it too.
- **No autocapture**. On the docs site, only page views are
  captured — never automatic clicks, scrolls, or form-field
  inspection.
- **DNT respected**. Browsers that send Do-Not-Track headers skip
  docs-site analytics entirely.

## The endpoint

The endpoint is hard-coded in [`app/telemetry.py`][telemetry-py] —
it's the maintainer's PostHog project, not user-configurable. That
keeps opted-in counts adding up to a real total instead of being
scattered across whoever set up their own backend.

The docs site uses the same project, configured via
[`overrides/main.html`][overrides-main].

Before v0.64.0 the analytics backend was a self-hosted Aptabase
deployment at `aptabase.dmello.io`; the data shape was the same but
the dashboards weren't giving the maintainer the cohort + funnel
views needed to actually answer questions about how Tesserae is
used. PostHog's free tier gives those views at no extra cost.

## How to disable

Untick *Send anonymous usage telemetry* in **Settings → Server → App**,
or set the kill switch environment variable (wins over stored
settings):

```sh
export TESSERAE_TELEMETRY=0
```

For the docs site, browsers with Do-Not-Track headers skip analytics
automatically. To force-disable in any browser, block the
`us.i.posthog.com` domain.

[posthog]: https://posthog.com
[telemetry-py]: https://github.com/dmellok/tesserae/blob/main/app/telemetry.py
[overrides-main]: https://github.com/dmellok/tesserae/blob/main/overrides/main.html
