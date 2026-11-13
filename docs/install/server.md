# Install Tesserae

Install Tesserae on the computer that will send pictures to your screens.
It also runs the dashboard editor in your browser.

It runs on macOS, Linux, Raspberry Pi and Windows.

Choose how screens receive pictures:

- REST is the default for new installs. Screens check Tesserae over HTTP; no message broker is needed.
  See [REST transport](rest-transport.md).
- MQTT uses a message broker, a server that forwards updates to screens. It has lower latency when screens wake.
  Use bundled amqtt, external Mosquitto or Home Assistant's broker.

TRMNL and KOReader devices always check over HTTP, whichever option you choose.

!!! tip "Or use Docker (or Home Assistant)"
    [Docker](docker.md) starts Tesserae with `docker compose up -d`, without setting up Python.
    The [Home Assistant App](home-assistant.md) puts Tesserae in HA's sidebar.
    MQTT discovery can add every device as an HA entity.

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

- Checks for `git` and Python 3.11+
- Clones the repo (default `~/tesserae`, override with `TESSERAE_DIR`)
- Creates a separate Python environment and installs Tesserae
- Asks for a port (default `8765`)
- Installs Chromium via Playwright for webpage rendering (with a system-browser fallback, see below)
- Writes a `run.sh` (or `run.ps1`) shortcut in the install dir

When it finishes, start the server with `./run.sh` (or `.\run.ps1`) from the
install dir and open `http://localhost:8765/`.

## Browser request protection

Tesserae rejects admin changes from other websites with a 403 response.
This includes setup and login, even without cookies. It checks POST, PUT, PATCH and DELETE requests.

Browser checks follow this order:

1. If the Fetch Metadata header is present, it must be `Sec-Fetch-Site: same-origin` or `none`.
2. Otherwise, check `Origin`, falling back to `Referer`.
   The protocol, host and port must match the request or the configured Public URL.
   Different ports are rejected. Direct LAN access still works when a Public URL is set.

If a form behind a reverse proxy fails with 403, check `X-Forwarded-Proto` and `X-Forwarded-Host`.
Fix those headers, or set **Settings → Server → Public URL** to your browser's address.
The error page includes these instructions.

Proxies must preserve the browser's origin headers and forward the correct address details.
Tesserae accepts forwarded headers, including HA ingress, only from trusted proxy addresses.
This covers the client address, host, protocol, port and path prefix.

Trusted ranges by default:

| Network | Ranges |
| --- | --- |
| Loopback | `127.0.0.0/8`, `::1/128` |
| Private IPv4 | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` |
| Link-local | `169.254.0.0/16`, `fe80::/10` |
| IPv6 ULA | `fc00::/7` |

`TESSERAE_FORWARDED_HOPS` sets how many proxy hops Tesserae reads.

For other proxies, set `TESSERAE_TRUSTED_PROXIES` before startup, or in the container's environment.
Use comma-separated ranges for public IPv6, CGNAT or VPN proxies:

```sh
export TESSERAE_TRUSTED_PROXIES='2001:db8::10/128,100.64.1.2/32'
```

These CIDR address ranges add to the defaults. Empty or unset adds nothing.
Invalid ranges and universal `/0` ranges are ignored with a warning.

Trust is checked against the direct connection before reading forwarded headers.
It does not authorise clients. **Trusted networks** and `TESSERAE_TRUSTED_NETWORKS`
control client access separately and never grant proxy trust.

Requests without any of the three browser origin headers are allowed for compatibility.
Their source cannot be checked; session cookies then rely only on SameSite protection.
Without Fetch Metadata, an explicit `Origin: null` or malformed origin is rejected.

Cookie-free device, Companion and webhook requests use separate authentication and are exempt from browser checks.
MCP requests are exempt only with a valid token in `Authorization: Bearer` or `X-Tesserae-Token`.

Token-free MCP calls on the server itself still check browser origin.
A page on another localhost port cannot create or edit dashboards.
Native Claude/Codex bridges without Origin or Fetch Metadata headers keep their loopback access.

Session-only API actions, such as issuing pairing codes, remain protected.
Device-authenticated reads and OTA firmware downloads are unchanged.

Creating a dashboard needs a POST. The old `/pages/new` and empty `/pages/canvas/` GET routes
still open the creation form (the second one preselects Freeform canvas), and GET still opens
existing canvases. Rejected requests are logged once per route per server process, without
request URLs or tokens.

## Manual install

```sh
git clone https://github.com/dmellok/tesserae.git
cd tesserae
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m app.main         # production: waitress, port 8765
.venv/bin/python -m app.main --dev   # Flask dev server: auto-reload + debugger
```

`python -m app.main` uses [waitress](https://docs.pylonsproject.org/projects/waitress/), a Python web server.
It works on a Raspberry Pi; a single-user install needs no nginx.
Use `--dev` for Flask's development server.

!!! note "Windows line endings"
    If `.\install.ps1` fails to parse on PowerShell 5.1, your checkout may have
    LF line endings. `git pull` to get the `.gitattributes` fix, or run the
    manual steps above with `.venv\Scripts\python.exe -m app.main`.

## Run as a service (Linux)

Install Tesserae as a systemd service to start it after reboots and restart it
after a crash. After the main installer finishes:

```sh
cd ~/tesserae
./scripts/install-systemd.sh
```

The script:

- Refuses on macOS / non-systemd distros (use launchd / your own supervisor there)
- Creates the service file using your install directory and chosen port, under your current user
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

Environment variables can override the script's defaults:

- `TESSERAE_DIR`: install directory
- `TESSERAE_PORT`: port
- `TESSERAE_SERVICE_NAME`: rename the service for parallel installs
- `TESSERAE_USER`: run as a different user
- `NONINTERACTIVE=1`: skip prompts

## First run

1. Open `http://127.0.0.1:8765/`. On first startup, `/setup` asks you to set an admin password.
2. Sign in at `/login`. The setup wizard covers welcome, help out, timezone, transport, device and dashboard.
   You can reach the same screens through Settings if you skip it.
3. In **Settings → Server**, set:
    - Broker host and credentials
    - **Public URL**, used for external links; set this behind a reverse proxy
    - **mDNS**, optional access at `tesserae.local`

    For the Chromium browser setting, see [below](#chromium-for-webpage-rendering).
4. Set your location and integrations:
    - **Settings → Server → Location & time**: timezone and location.
      Clock and calendar widgets use this timezone even if the container's `TZ` differs.
    - **Settings → Server → Network & integrations**: Home Assistant MQTT discovery.
    - **Settings → System → Online features**: all contact with `api.tesserae.ink`.
      It is off by default; the wizard asks once.
      Enabling it allows update checks, anonymous install counts and a daily anonymous heartbeat.
5. Renderer and plugin settings appear in their own sections.

To preview one widget, open
`http://127.0.0.1:8765/_test/render?plugin=clock_analog&size=md` on the server itself.
Like `/compose/`, `/_test/render` needs no login over loopback, a connection from the server to itself.
From another machine, it requires the development or test server and a logged-in session.

## Older x86-64 processors

numpy 2.4 and later publish x86-64 wheels built for x86-64-v2 (SSE4.2 and
POPCNT, processors from 2009 on). On an older machine `python -m app.main`
dies with `Illegal instruction` as soon as numpy loads. Pin the last line
whose wheels still run there, inside the same virtualenv:

```bash
pip install 'numpy<2.4'
```

`grep -c sse4_2 /proc/cpuinfo` printing `0` identifies the problem; in a virtual
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

Or write the path to `data/core/.chromium` (single line). If no browser is
found, everything except webpage rendering still works.

## Webhook push

Any tool that sends HTTP requests can redraw and send a dashboard without opening the admin UI.
Examples include Home Assistant automations, cron, GitHub Actions and shortcut apps.

1. **Settings → System → Webhook.** Click **Regenerate token** to create
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

3. Check the response:

    | Status | Meaning |
    | --- | --- |
    | `200` | Frame sent |
    | `202` with `status: "quiet"` | Every bound device is in quiet hours |
    | `401` | Wrong token |
    | `404` | Page does not exist |
    | `409` | A push is already running |
    | `503` | No token is set |

The endpoint redraws the page and sends it to every bound device.
Pass a `device_ids` array to choose specific devices.

An HA automation can call `/api/v1/push` when someone leaves to show an empty-house dashboard.
A cron job can redraw it at sunset for dusk lighting widgets.

The stored token is encrypted.

## Backup, export, import

Both options are under **Settings → System** and require admin access.

Use `Settings → System → Backups` for regular backups and restoring a setup after a bad change.
It saves a ZIP under `data/core/backups/`.

Use `Settings → System → Backups → Migrate to another install` to copy or move your setup.
Exports use the same ZIP format as backups. Treat the file like a password: it contains secrets.

The ZIP contains everything under `data/` except gallery photos and the render cache.

Click Export to download `tesserae-data-<timestamp>.zip`.

To import, upload a ZIP from another self-hosted install. Tesserae then:

1. Checks the export and rejects paths outside `data/`.
2. Makes a `pre-import` backup. You can restore it from Backups if needed.
3. Replaces `data/` with the ZIP's contents. Existing gallery photos and render cache stay put.
4. Restarts. With `--dev`, the page asks you to restart it yourself.

By default, this server keeps its own:

- Admin password and login settings, including the password gate and trusted networks
- Server name and colour
- Public URL
- MCP and webhook tokens
- Relay link
- Paired Companion apps
- Install ID and MQTT client ID

Everything else comes from the ZIP, including devices and their broker settings.
Connector secrets are re-encrypted for this server.
If the source used a different `TESSERAE_SECRET_KEY`, secrets that cannot be decrypted are listed for you to re-enter.

When moving hosts, tick **Also take this export's password, login settings and server identity**.
This imports the items above too. The new host uses the old password and server identity.

## mDNS, `tesserae.local`

Enable **Settings → Server → mDNS** to use `tesserae.local` instead of a fixed IP address.
It is off by default. It needs UDP multicast on port 5353, which some hosts block.

Open the admin UI at `http://tesserae.local:8765/`.
Device routes `/compose/` and `/renders/` work there too.

Clients with their own captive portal (ESP32) use a different scheme:
`tesserae-<device-id>.local` for the portal, then they connect out to
the server URL you give them.

## Running more than one server

Give each server a name and colour under **Settings → Server → This server** to tell them apart.

- The name appears below the Tesserae logo and starts every browser tab title (`dev · Dashboards`).
- The colour adds a stripe to every page, including the full-screen editor.
  It also tints the tab icon, buttons and links.

Choose a custom colour or a Spectra 6 preset: red, yellow, green, blue or black.

Both are saved on the server, so every browser and phone sees the same
thing. Leave them blank and the admin looks as it always has. Panels never
show either: they only mark the admin pages.

## Running the tests

```sh
.venv/bin/pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy app/
```

The tests need no broker or Chromium.

## Next steps

- [Install a client](clients.md) for your panel hardware
- [Set up a device](devices.md) and connect it to a dashboard
- [Home Assistant integration](home-assistant.md), HA App install + MQTT auto-discovery
- [Browse the widgets](../widgets/gallery.md) you can place on a dashboard
