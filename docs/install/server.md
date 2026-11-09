# Install Tesserae

Tesserae is the **server**: it serves the admin UI, renders dashboards, and
publishes frames out to your panels. It runs on macOS, Linux, Raspberry Pi,
and Windows.

Two transports for getting frames out to panels:

- **REST** (default for new installs in v0.52+). Clients poll Tesserae
  over HTTP. No broker required; the simplest setup. See the
  [REST transport](rest-transport.md) page for the full flow.
- **MQTT** (the original). Clients subscribe to a broker. Lower
  per-wake latency but you need a broker (the bundled amqtt or an
  external Mosquitto, or the one Home Assistant ships). TRMNL /
  KOReader devices have always polled over HTTP regardless of this
  choice.

!!! tip "Or use Docker (or Home Assistant)"
    If you'd rather not touch Python, the [Docker install path](docker.md)
    has you running with one `docker compose up -d`. Running Home Assistant?
    See the [Home Assistant integration](home-assistant.md) page, Tesserae
    can install as an HA App (Ingress-tabbed inside HA's sidebar) and
    publish MQTT discovery so every device shows up as an HA entity.

## Quick install

=== "macOS / Linux / Raspberry Pi"

    ```sh
    curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/install.sh | bash
    ```

=== "Windows (PowerShell)"

    ```powershell
    iwr https://raw.githubusercontent.com/dmellok/tesserae/main/install.ps1 -UseBasicParsing | iex
    ```

The installer:

- Sanity-checks `git` + Python 3.11+
- Clones the repo (default `~/tesserae`, override with `TESSERAE_DIR`)
- Creates a venv and installs the project
- Asks for a port (default `8765`)
- Installs Chromium via Playwright for webpage rendering (with a system-browser fallback, see below)
- Writes a `run.sh` (or `run.ps1`) shortcut in the install dir

When it finishes, start the server with `./run.sh` (or `.\run.ps1`) from the
install dir and open `http://localhost:8765/`.

## Browser request protection

Unsafe admin requests (POST, PUT, PATCH and DELETE), including cookie-free setup
and login submissions, must have `Sec-Fetch-Site: same-origin` or `none` when
that header is present.
In its absence, `Origin`, or otherwise `Referer`, must match the request's scheme,
host and port. Cross-port requests are refused with a clear 403. The actual
request origin, resolved through proxy middleware, and the configured Public
URL origin are both accepted, so direct LAN forms keep working with a Public
URL configured. Proxies must preserve the browser's provenance headers and
supply the correct forwarded scheme, host and port.

If saving a form behind a reverse proxy fails with a 403, the proxy is most
likely not forwarding the scheme and host (`X-Forwarded-Proto` and
`X-Forwarded-Host`). Either make it forward them, or set **Settings → Server →
Public URL** to the address you use in the browser. The 403 page says the same.
Forwarded client, host, scheme, port and prefix headers, including HA's ingress
header, are accepted only from trusted proxy peers. The defaults remain loopback
(`127.0.0.0/8`, `::1/128`), private IPv4 (`10.0.0.0/8`, `172.16.0.0/12`,
`192.168.0.0/16`), link-local (`169.254.0.0/16`, `fe80::/10`) and IPv6 ULA
(`fc00::/7`). `TESSERAE_FORWARDED_HOPS` still controls how many proxy hops are read.

Add public IPv6, CGNAT or VPN proxies with the separate comma-separated
`TESSERAE_TRUSTED_PROXIES` environment variable, set before starting the server
(or in the container's environment). For example:

```sh
export TESSERAE_TRUSTED_PROXIES='2001:db8::10/128,100.64.1.2/32'
```

These CIDRs add to the defaults; empty or unset adds nothing. Invalid CIDRs and
universal `/0` ranges are ignored with a warning. Trust is matched against the
direct connection peer before forwarded headers are read. This option trusts
proxy metadata only: it does not authorise clients. The Authentication setting
**Trusted networks** and `TESSERAE_TRUSTED_NETWORKS` remain separate client
authorisation rules and never grant proxy trust.

For compatibility, requests with all three provenance headers missing are
allowed. Their provenance cannot be checked; when a session cookie is present,
only its SameSite protection remains. Without Fetch Metadata, an explicit `Origin: null`
or malformed origin is refused. Explicit API exemptions let cookie-free device,
companion and webhook requests continue through their independent
authentication. MCP requests are exempt only with an explicit valid token
(`Authorization: Bearer` or `X-Tesserae-Token`). Token-free loopback MCP calls
still check browser provenance, so a page on another localhost port cannot
create or edit dashboards. Native Claude/Codex MCP bridges that send no Origin
or Fetch Metadata keep their existing loopback access.
Session-only API actions, such as issuing a device pairing
code, are protected. Device-authenticated reads and OTA downloads are unchanged.
Dashboard creation is POST-only. The legacy `/pages/new` and empty
`/pages/canvas/` GET routes open the existing creation form; the latter
preselects Freeform canvas. An existing canvas can still be opened with GET.
Rejections are logged once per route per server process, without request URLs
or tokens.

## Manual install

If you'd rather do it by hand (or already cloned the repo):

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m app.main         # production: waitress, port 8765
.venv/bin/python -m app.main --dev   # Flask dev server: auto-reload + debugger
```

`python -m app.main` runs under
[waitress](https://docs.pylonsproject.org/projects/waitress/), a pure-Python
production WSGI server, the same command works on a Raspberry Pi appliance, no
nginx required for a single-user install. `--dev` opts into Flask's dev server
when you're hacking on the admin.

!!! windows "Windows line endings"
    If `.\install.ps1` fails to parse on PowerShell 5.1, your checkout may have
    LF line endings. `git pull` to get the `.gitattributes` fix, or run the
    manual steps above with `.venv\Scripts\python.exe -m app.main`.

## Run as a service (Linux)

So Tesserae survives reboots + restarts on crash, install it as a systemd
service. After the main installer finishes:

```sh
cd ~/tesserae
./scripts/install-systemd.sh
```

The script:

- Refuses on macOS / non-systemd distros (use launchd / your own supervisor there)
- Generates a unit file from your install dir, port, and current user
- `sudo` installs it to `/etc/systemd/system/tesserae.service`
- `enable`s it (auto-start on reboot) and `start`s it now
- Prints the `systemctl` / `journalctl` commands you'll use day-to-day

Common follow-ups:

```sh
sudo systemctl status tesserae       # is it running?
sudo systemctl restart tesserae      # bounce after an upgrade
sudo journalctl -u tesserae -f       # tail the logs
```

To uninstall the service (leaves the install dir alone):

```sh
sudo systemctl disable --now tesserae
sudo rm /etc/systemd/system/tesserae.service
sudo systemctl daemon-reload
```

The script supports a few env vars to override defaults: `TESSERAE_DIR`,
`TESSERAE_PORT`, `TESSERAE_SERVICE_NAME` (rename the unit for parallel installs),
`TESSERAE_USER` (run as someone other than yourself), `NONINTERACTIVE=1` to skip
prompts.

## First run

1. Open `http://127.0.0.1:8765/`, on first boot you're sent to `/setup` to pick an admin password.
2. Sign in at `/login`. The onboarding wizard runs through six steps: **welcome → help out → timezone → transport → device → dashboard**. Same screens you'd reach via Settings if you skipped it.
3. **Settings → Server** holds the broker host / credentials, the **Public URL** Tesserae uses when building external links (set it behind a reverse proxy), and optional **mDNS** broadcast of `tesserae.local`. The Chromium fallback for webpage rendering is an environment variable, see [below](#chromium-for-webpage-rendering).
4. **Settings → Server** also holds the timezone and location (**Location & time**) and the **Home Assistant MQTT discovery** toggle (**Network & integrations**). The timezone setting forwards to the rendering Chromium (since 0.44.10), so clock + calendar widgets paint in your local zone whether the container's `TZ` env var matches or not. **Settings → System → Online features** is the single switch for all `api.tesserae.ink` contact: update checks, anonymous install counts and a once-a-day anonymous heartbeat. It is off by default; the onboarding wizard asks once.
5. Renderers and plugins that declare settings show up as their own sections, generated from their manifests.

To preview a single widget without composing a dashboard, open
`http://127.0.0.1:8765/_test/render?plugin=clock_analog&size=md` in a
browser on the server itself. `/_test/render` answers over loopback without
a session (the in-process Playwright renderer and the MCP widget render use
it, like `/compose/`); from another machine it needs the dev (or test)
server **and** a logged-in session.

## Older x86-64 processors

numpy 2.4 and later publish x86-64 wheels built for x86-64-v2 (SSE4.2 and
POPCNT, processors from 2009 on). On an older machine `python -m app.main`
dies with `Illegal instruction` as soon as numpy loads. Pin the last line
whose wheels still run there, inside the same virtualenv:

```bash
pip install 'numpy<2.4'
```

`grep -c sse4_2 /proc/cpuinfo` printing `0` is the tell; in a virtual
machine, setting the CPU type to `host` fixes it too. The Docker image and
the Home Assistant App already pin `numpy<2.4`; see
[Install via Docker](docker.md#limits).

## Chromium for webpage rendering

The **Send → Webpage** tab and the `webpage` widget screenshot pages with
headless Chromium via Playwright. Playwright ships its own binaries for most
platforms; on 32-bit Raspberry Pi OS it doesn't, so the installer falls back to
a system browser. To point at one yourself:

```sh
export TESSERAE_CHROMIUM_PATH=/usr/bin/chromium-browser
```

…or write the path to `data/core/.chromium` (single line). If no browser is
found, everything except webpage rendering still works.

## Webhook push

External systems (Home Assistant automations, cron, GitHub Actions,
shortcuts apps, anything that speaks HTTP) can trigger an on-demand
re-render + push without going through the admin UI.

1. **Settings → System → Webhook.** Click **Regenerate token** to mint
   one (clicking it again invalidates the old one), or paste your own
   under **Custom token**. The token is shown once, then masked; copy it
   then.
2. **Call the endpoint:**

    ```sh
    curl -X POST https://your-tesserae.local/api/v1/push \
      -H "Authorization: Bearer <your-token>" \
      -H "Content-Type: application/json" \
      -d '{"page_id": "ha_home"}'
    ```

3. **Response:** `200` once the frame is sent; `202` with
   `status: "quiet"` if every bound device is in quiet hours; `401` if
   the token is wrong; `404` if the named page doesn't exist; `409` if a
   push is already running; `503` if no token is set.

The endpoint re-renders the named page and publishes the frame to
every device bound to it (pass an optional `device_ids` array to narrow
that). Useful patterns: an HA automation that pings
`/api/v1/push` when a person leaves home so the next refresh shows an
empty-house mode; a cron that triggers a fresh render at sunset so
dusk lighting widgets repaint promptly. The token lives in
`data/core/settings.json` under `app.webhook_token_secret`, encrypted at rest.

## Backup, export, import

Two related features under **Settings → System**, both admin-only:

**Backups** (`Settings → System → Backups`) snapshot your full Tesserae state into a ZIP on disk under `data/core/backups/`. Use it for periodic local safety copies and rollback after a bad change. Endpoints: `/settings/system/backup/{create,restore,delete,download}`.

**Data export / import** (`Settings → System → Backups → Migrate to another install`) is the one-shot migration ZIP. Use it to copy your setup to another self-hosted Tesserae Server, or to move an install to a new host, not for routine snapshots. The ZIP is the same as a backup: everything under `data/` except gallery photos and the render cache, including every page, theme, font pick, device registration and per-plugin settings (with secrets embedded, treat the file like a credential).

- **Export:** clicks straight to a `tesserae-data-<timestamp>.zip` download.
- **Import:** upload a ZIP from another self-hosted install. The server checks that it is a Tesserae export and that no path in it escapes `data/`, takes a `pre-import` backup of the current data (listed under Backups, so a wrong import is one Restore away), then replaces the contents of `data/` with the ZIP's. Gallery photos and the render cache on this server stay put. The server restarts afterwards; under `--dev` the page asks you to restart it yourself.
- **What an import keeps:** by default this server keeps its own admin password and login settings (the password gate and trusted networks), its server name, colour and public URL, its MCP and webhook tokens, its relay link, its paired Companion apps, its install ID and its MQTT client ID. Everything else, devices and the broker settings they connect with included, comes from the ZIP. Connector secrets in the ZIP are re-encrypted for this server so they keep working. If the export came from a server with a different `TESSERAE_SECRET_KEY`, any secret that can't be decrypted is listed after the import and needs re-entering.
- **Moving to a new host:** tick **Also take this export's password, login settings and server identity** to take all of the above from the ZIP too, so the new host signs in with the old password and carries on as the same server.

Endpoints: `/settings/system/data/export` and `/settings/system/data/import`.

## mDNS, `tesserae.local`

`tesserae.local` is the friendly hostname Tesserae can broadcast on
your LAN so panels and clients don't need a hard-coded IP. Toggle it
via **Settings → Server → mDNS** (off by default, the broadcast
needs UDP multicast on port 5353, which some hosting setups disallow).
When enabled, both the admin UI and the panel-side `/compose/` /
`/renders/` routes are reachable at `http://tesserae.local:8765/`.

Clients with their own captive portal (ESP32) use a different scheme:
`tesserae-<device-id>.local` for the portal, then they connect out to
the server URL you give them.

## Running more than one server

If you run several Tesserae servers, say dev, test and prod, give each one
a name and a colour under **Settings → Server → This server**, so you can
tell them apart at a glance:

- The **server name** shows as a small label under the Tesserae wordmark
  and at the start of every browser tab title (`dev · Dashboards`).
- The **server colour** paints a thin stripe across the top of every page,
  including the full-screen editor, tints the tab icon, and becomes the
  accent for buttons and links. Pick one of the five presets (red,
  yellow, green, blue and black, the Spectra 6 inks toned to match the
  admin) or a custom colour.

Both are saved on the server, so every browser and phone sees the same
thing. Leave them blank and the admin looks as it always has. Panels never
show either: they only mark the admin pages.

## Running the tests

```sh
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app/
```

The renderer, transport, push pipeline, auth, and settings flow are all covered
with no broker or Chromium dependency.

## Next steps

- [Install a client](clients.md) for your panel hardware
- [Set up a device](devices.md), register it, calibrate orientation, bind a dashboard
- [Home Assistant integration](home-assistant.md), HA App install + MQTT auto-discovery
- [Browse the widgets](../widgets/gallery.md) you can place on a dashboard
