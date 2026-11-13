# Privacy

Tesserae has no account system or third-party analytics in the app.
Optional reports go to `api.tesserae.ink`. The sections below explain each request.

The app also fetches two public files from GitHub: the widget catalog and release tags.
Those requests send nothing about your install. Optional cloud relay carries encrypted pictures to remote screens.

Online features are off by default. A fresh install never contacts `api.tesserae.ink` until you opt in:

- In the first-run wizard, which asks once and explains the details.
- In **Settings → System → Online features**.

Leave it off and update indicators and install counts don't appear. Nothing else in the app phones home.
It never contacts the endpoint from GitHub Actions, Codespaces or Gitpod when it can identify those build or development environments.

The server uses your IP to identify a coarse country for aggregate statistics, then discards the IP.
No personal data is stored. It stores neither IP addresses nor User-Agent strings.

Widgets may make their own external requests. Each widget's README documents these separately.

## What the app never sends

Beyond the aggregate `api.tesserae.ink` requests described above, the app does not send:

- IP addresses, hostnames or broker addresses
- File paths, settings values or dashboard layouts
- Secrets, including passwords, tokens and API keys
- Push contents or anything tied to a real-world identity

This list covers the app itself, not the widgets you install.

## Install identifier

On first startup, Tesserae creates a random identifier and saves it in `data/core/install_id.json`.
It uses `uuid.uuid4()`.

The ID isn't tied to your identity or an account.
It doesn't come from your hardware or IP.

Widgets declaring `needs_install_id` use this stable ID across restarts.
Planned shared-world widgets, such as the tamagotchi pet and dashboard traveler, can use it to remember your install.

Widgets can request a separate ID through `needs_scoped_id`, which returns `SHA-256(install_id + plugin_id)`.
Each widget gets a different value, so an external service can't link their requests through that ID.
The `tesserae_status` update indicator uses this form.

Marketplace install counts use the raw install ID. Each install counts once per widget, with no name or personal data.

Regenerate the ID at any time in **Settings → System → Install identifier**.
Services then see a new install, which resets any state they kept for the old ID.
This includes a pet's history or a traveler's home waypoint.

Install counts also treat the new ID as a new install.

## What the app sends to api.tesserae.ink

The **Online features** switch controls everything below. Turning it off stops all these requests.

### Marketplace install count

Installing a marketplace widget sends a POST request to `https://api.tesserae.ink/widgets/install`.
It contains the widget ID, your install's random ID and your running version.

This lets the widget's card show an anonymous install count. Reinstalling the same widget doesn't increase it.
The server counts each install ID once per widget.

The Browse page reads totals from `https://api.tesserae.ink/widgets/installs`.
The server derives a coarse country from the request IP, then discards the IP.

### Template marketplace (share + browse)

Sharing sends nothing until you press Submit in the Share dialog.
The POST request to `https://api.tesserae.ink/templates/submit` contains the cleaned template JSON and a dashboard preview.
It also includes your install's random ID and running version.

The export removes request headers, options marked secret and install-specific values, such as Home Assistant entity IDs.

A credential check blocks submission if anything resembling a key remains.

The preview shows your widgets' actual content at the time you share.
The Share dialog displays the exact image and warns you before submission.
If it contains private details, duplicate the dashboard and replace them with placeholders first.

A person reviews each submission before it appears publicly.
The server gives you a stable public pseudonym based on your install ID, such as `amber-heron-42`.

Authors have no account. Neither real names nor email addresses are used.

A takedown report sends the template's public slug, your short reason, your install's random ID and your version.

Reports go to the same human review queue. Reports from the publishing install are flagged so authors can withdraw their work.

Browsing fetches the public catalog at `https://api.tesserae.ink/templates/index.json`.
Installing fetches the template's JSON and POSTs an anonymous install count event, with the same fields as widget install counts.
The server derives a coarse country from the request IP, then discards the IP.

### Daily heartbeat

Once a day, Tesserae POSTs to `https://api.tesserae.ink/heartbeat`.
This helps the maintainer count active installs and decide what to work on.
It sends these coarse values:

| Field | What is sent |
|---|---|
| Install ID | Your install's random ID |
| Version | Running version |
| Release channel | `stable`: tagged release; `edge`: main-branch image; `main`: source checkout without a tag |
| Operating system | linux, macos or windows |
| CPU | Architecture |
| Python | Minor version |
| Deployment | docker, ha_addon, pip, lxc or source |
| Transport | mqtt, rest, both or none |
| Device count | A range: `0`, `1`, `2-3`, `4-9` or `10+`, never the exact number |
| Install age | `0`, `1-7`, `8-30`, `31-90` or `90+` days since the ID was created |
| Home Assistant | A true/false flag |

For each configured device kind, it sends:

| Field | What is sent |
|---|---|
| Kind | Device kind name |
| Firmware | Newest version reported by devices of that kind |
| Colour range | The panel's declared gamut, such as `bwry_4` |
| Resolution | Such as `800x480` |
| Wake interval | `<5m`, `5-15m`, `15-60m`, `1-6h`, `6h+` or `always_on` |

These describe a hardware model, never an individual device. Counts and kinds cover only devices you've added, not the built-in catalog.

The heartbeat also includes:

| Field | What is sent |
|---|---|
| Paired companion apps | Count range, using the device-count ranges above; no client name, install ID or app version |
| Lineups | Count range |
| Cloud relay | Whether it is linked |
| Touch | Whether it is used |
| MCP agent | Whether one is connected |
| Quiet hours | Whether they are set |
| Admin design | `classic` or `paper` (the default) |
| Design choice | `ui_chosen`: whether someone chose the design or it was the default |
| Firmware rollout | Count ranges for devices offered a release, already on it, or whose last report failed |

Feature flags say only whether a feature is used, never how.
No names or paths are sent; neither are layouts or exact counts.

The server stores only the day, so reports can't form a timed activity trace.
It keeps one heartbeat per install per day. It derives a coarse country from the request IP, then discards the IP.

The first heartbeat is sent as soon as you opt in through the wizard, Settings or the header prompt.
After that, it is sent daily.

### App update check (header badge + `tesserae_status`)

Tesserae checks for a newer release by fetching
`https://api.tesserae.ink/version/latest?channel=stable&current=<v>&install=<scoped-id>`.
The header shows an "update available" badge when a newer release exists.

It checks once in the background and caches the result for several hours. It never blocks a page.

The `tesserae_status` widget can show the same indicator on your screen.
It is off by default for each placement. Both checks stop when Online features is off.

The `install` parameter is a separate, one-way ID derived from your install identifier.
It isn't the raw ID and can't be linked to the daily heartbeat.
The server stores only the query parameters and a coarse country lookup, with no IP address or User-Agent.

### Widget update count (sidebar badge)

The Widgets sidebar counts installed catalog widgets with newer releases.
It reads `widgets.json` from GitHub, the same public file Browse catalog uses.
Tesserae fetches it in the background at most once an hour and compares versions locally.

GitHub receives no install details. Online features off stops the check.
**Settings → Widgets → Catalog updates** hides only the count.

### Server release check (Docker and non-git installs)

Without a git checkout, the Updates card in **Settings → System** checks GitHub's public API.
This includes Docker images and release archives. It fetches the newest release tag from `api.github.com/repos/dmellok/tesserae/tags`.

This runs in the background at most once an hour while that page is open.
GitHub receives no install details. With Online features off, the card only links to the releases page.

For git checkouts, **Check for updates** runs `git fetch` only when you press it.

### Device firmware check

Tesserae checks `https://api.tesserae.ink/firmware/<kind>/latest` for each registered device kind.
The Devices card can then show "v1.1.0 (v1.2.0 available)".
It checks when the Devices page first loads, then on demand every 60 min.

The request sends only the device kind name. It includes no install ID or device-specific fields.

With Online features off, the card still shows the version reported in each device's heartbeat.
It doesn't show an "update available" badge.

See the [tesserae-api source](https://github.com/dmellok/tesserae-api) for the server implementation.

### Home Assistant App auto-update

Home Assistant's Supervisor manages updates to the Tesserae App through Home Assistant's own infrastructure.
The [Home Assistant privacy policy](https://www.home-assistant.io/privacy/) covers what Supervisor sends.

## Cloud relay (remote panels)

The [remote-panel feature](install/remote-panel.md) lets screens elsewhere show your dashboards through a cloud relay.
Use the hosted `relay.tesserae.ink` or [your own](relay/self-host.md).
This is separate from the switches above and off unless you set it up.

Your server uploads each picture for the paired remote screen to fetch.
Only your server and that screen share the encryption key.
The relay stores encrypted data and cannot read your dashboards.

It sees the encrypted picture and routing details: install and device, picture size and timing.

Nothing is sent for a device until you pair it. Hosting your own relay keeps those details under your control too.

## Per-device telemetry (stays on the server)

For registered screens, Tesserae tracks battery percentage, signal strength (RSSI), heartbeat timing and smart-sync predictions.

They stay on your server in `data/core/device_telemetry.json` and `data/core/battery_history.db`.
They never leave the server. They appear only in the admin UI: Settings → Devices, the battery indicator and smart-sync scheduling.

## Local stats (stays on the server)

The **Stats** page counts:

- Pushes per day and what requested them
- Pictures drawn per screen
- How often each screen checks in
- How long pictures take to draw
- Other logged events, counted by type

The event log has a size limit. These totals are collected as events happen so they can cover months.

Counters stay in `data/core/stats.db` on your server. Only the Stats page and your export button read them.
There is no client or upload path to send them anywhere.

| Stored field | What it contains |
|---|---|
| Date | Day only, never a finer time |
| Metric | Metric name |
| Dimension | An existing ID, such as a device ID or event type |
| Value | Integer count |

No URLs or dashboard titles are stored, nor any push contents.
Names are looked up for display and never written to the file.

Use the Stats page to export all counters as JSON, pause collection or delete every counter.

## Docs site analytics

The docs site (`docs.tesserae.ink`) uses PostHog, separately from the app. It sends two events:

| Event | When | Data |
|---|---|---|
| Page view | Each page you open | Page URL, referrer and basic browser info attached by PostHog |
| `store_outbound_click` | A click on a device vendor's store link | Destination URL and link text |

There is no session recording or other click tracking. Nothing is written to cookies or local storage.
The site honours Do-Not-Track headers and skips all analytics when DNT is set.
