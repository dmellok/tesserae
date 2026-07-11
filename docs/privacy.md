# Privacy

**Tesserae contacts one first-party endpoint, `api.tesserae.ink`, and
nothing else.** There is no account system and no third-party analytics
in the app. The single endpoint is used for: checking for app and
device-firmware updates, reporting an anonymous, aggregate count of how
many installs use each marketplace widget, and a once-a-day heartbeat of
low-cardinality facts about the install.

This is controlled by one master switch, **Settings → System → Online
features**. Turn it off and Tesserae never contacts `api.tesserae.ink`;
the update indicators and install counts simply disappear. Nothing else
in the app phones home.

What a request may include: your install's random ID, the widget id, and
your running version. A coarse country is derived from your IP address
for aggregate geography and the IP is then discarded. No account, no
personal data, and no IP addresses or User-Agent strings are stored.

Individual widgets you install may make their own external network calls,
documented in their own READMEs. Those are separate from the app.

## What the app never sends

Beyond the aggregate `api.tesserae.ink` requests described above, the app
does not send:

- IP addresses
- hostnames
- file paths
- settings values
- secrets (passwords, tokens, API keys)
- push contents
- dashboard layouts
- broker addresses
- anything tied to a real-world identity

This applies to the app itself. Individual widgets may make external
network calls documented in their own READMEs.

## Install identifier

On first startup Tesserae generates a **random UUID** and stores it
at `data/core/install_id.json`. The value has no connection to your
identity, hardware, IP address, or Tesserae account: it's a random
string, generated with `uuid.uuid4()`, persisted so widgets that
declare `needs_install_id` (shared-world features like the planned
tamagotchi pet or dashboard traveler) can key against a stable
per-install identity across restarts.

Widgets can also request a **widget-scoped derivation** via
`needs_scoped_id`, which returns `SHA-256(install_id + plugin_id)`.
Different widgets get different derived identifiers so their outbound
calls can't be correlated by an external service; the `tesserae_status`
update chip uses this scoped form.

The marketplace install count uses the **raw** install ID instead (the
app-level identity), so a unique install counts once per widget. It
carries no name or personal data; regenerating the identifier below
makes you a fresh install from the count's point of view.

You can regenerate the identifier at any time in
**Settings → System → Install identifier**. Regeneration resets any
per-install state the widget side has accumulated (a pet's history, a
traveler's home waypoint), because from those services' point of view
you look like a new install.

## What the app sends to api.tesserae.ink

Everything below rides the single **Online features** switch (on by
default). Turning it off stops all of it.

### Marketplace install count

When you install a widget from the marketplace, Tesserae POSTs to
`https://api.tesserae.ink/widgets/install` with the widget id, your
install's random ID, and your running version, so the widget's card can
show an anonymous "how many installs use this" count. Reinstalling the
same widget does not inflate the count (it dedupes on your install ID).
The Browse page reads the aggregate counts from
`https://api.tesserae.ink/widgets/installs`. A coarse country is derived
from the request IP on the server side and the IP is then discarded.

### Daily heartbeat

Once a day, Tesserae POSTs to `https://api.tesserae.ink/heartbeat` so the
maintainer can see how many installs are active and what to prioritise.
The body is only low-cardinality, aggregate values: your install's random
ID, the running version and channel, the OS family (linux/macos/windows),
CPU arch, Python minor version, deployment kind (docker/ha_addon/pip/lxc/
source), transport (mqtt/rest/both/none), a **bucketed** device count
(`0`, `1`, `2-3`, `4-9`, `10+`, never the exact number), the set of
registered device kinds, and a Home Assistant boolean. No names, paths,
layouts, or exact counts. The server stores only the **day** (not a
timestamp), so the cadence can't become a per-install activity trace, and
it dedupes to one heartbeat per install per day. A coarse country is
derived from the request IP and the IP is then discarded.

### App update check (`tesserae_status`)

When the update-indicator chip is enabled on a `tesserae_status`
placement, the widget fetches
`https://api.tesserae.ink/version/latest?channel=stable&current=<v>&install=<scoped-id>`
so it can show an amber "update available" chip when a newer Tesserae
release exists. The `install` parameter is the widget-scoped derivation
of your install identifier. No IP address or User-Agent is stored; only
a coarse country lookup plus the query params. The chip is off per
placement by default, and does nothing at all when Online features is
off.

### Device firmware check

Tesserae looks up the latest known firmware version for each of your
registered device kinds against
`https://api.tesserae.ink/firmware/<kind>/latest`, so the Devices card
shows "v1.1.0 (v1.2.0 available)" when a device is behind. The lookup
happens lazily (first Devices page render, then on demand every 60 min).
The only outbound data is the device kind name; no install identifier,
no device-specific fields. With Online features off, the Devices card
still shows the firmware version each device reports in its heartbeat;
the "update available" pill just never fires.

See the [tesserae-api source](https://github.com/dmellok/tesserae-api)
for the exact server-side implementation.

### Home Assistant App auto-update

When installed as the Home Assistant App, Supervisor manages Tesserae's
update cycle through Home Assistant's own update infrastructure. That
path is a Home Assistant feature, not Tesserae phoning home; the
[Home Assistant privacy policy](https://www.home-assistant.io/privacy/)
covers what Supervisor sends.

## Per-device telemetry (stays on the server)

Tesserae *does* track per-device diagnostics for the displays you
register (battery percentage, RSSI, heartbeat cadence, smart-sync
predictions). That data stays on your server in
`data/core/device_telemetry.json` and `data/core/battery_history.db`,
and is only surfaced inside the admin UI (Settings → Devices, the
battery indicator, smart-sync scheduling). It is never transmitted
off the box.

## Docs site analytics

Aggregate analytics on the documentation site
(`dmellok.github.io/tesserae/`) are kept separately from the app and
provide a coarse traffic overview only. The docs site honours
Do-Not-Track headers and skips analytics entirely when DNT is set.
