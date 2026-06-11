# Install Tesserae

Tesserae is the **server**: it serves the admin UI, renders dashboards, and
publishes frames out to your panels. It runs on macOS, Linux, Raspberry Pi,
and Windows. Most clients (Pi / ESP32) consume frames via MQTT, so for those
you'll want an **MQTT broker** (e.g. Mosquitto, or the one built into Home
Assistant) and at least one [client](clients.md) to paint a panel. TRMNL /
KOReader devices poll the server over HTTP instead, no broker required.

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

## First run

1. Open `http://127.0.0.1:8765/`, on first boot you're sent to `/setup` to pick an admin password.
2. Sign in at `/login`. The onboarding wizard runs through five steps: **welcome → broker → device → dashboard → telemetry consent**. Same screens you'd reach via Settings if you skipped it.
3. **Settings → Server** holds the broker host / credentials, **base URL** the panel uses to fetch frames, optional **mDNS** broadcast of `tesserae.local`, and Chromium fallback for webpage rendering.
4. **Settings → App** holds the timezone, location, telemetry toggle, and Home Assistant discovery toggle. The timezone setting forwards to the rendering Chromium (since 0.44.10), so clock + calendar widgets paint in your local zone whether the container's `TZ` env var matches or not.
5. Renderers and plugins that declare settings show up as their own sections, generated from their manifests.

To preview a single widget without composing a dashboard, run `--dev`, sign
in, then open
`http://127.0.0.1:8765/_test/render?plugin=clock_analog&size=md` in your
browser. `/_test/render` needs the dev (or test) server **and** a logged-in
session, it isn't loopback-exempt. The loopback bypass is only for
`/compose/`, `/renders/`, and `/plugins/<id>/<asset>`, which the in-process
Playwright renderer fetches without a session.

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

1. **Settings → System → Webhook.** Click **Generate token** the first
   time, or **Rotate** to invalidate the old one. The token is shown
   masked after creation; copy it once.
2. **Call the endpoint:**

    ```sh
    curl -X POST https://your-tesserae.local/api/v1/push \
      -H "Authorization: Bearer <your-token>" \
      -H "Content-Type: application/json" \
      -d '{"page": "ha_home"}'
    ```

3. **Response:** `200` once the request is queued (the actual render
   happens asynchronously); `401` if the token is wrong; `404` if the
   named page doesn't exist; `429` if you're hitting it too fast.

The endpoint re-renders the named page and publishes the frame to
every device bound to it. Useful patterns: an HA automation that pings
`/api/v1/push` when a person leaves home so the next refresh shows an
empty-house mode; a cron that triggers a fresh render at sunset so
dusk lighting widgets repaint promptly. The token lives at
`data/core/settings.json` under `webhook.token` and is masked on disk.

## Backup, export, import

Two related features under **Settings → System**, both admin-only:

**Backups** (`Settings → System → Backups`) snapshot your full Tesserae state into a ZIP on disk under `data/core/backups/`. Use it for periodic local safety copies and rollback after a bad change. Endpoints: `/settings/system/backup/{create,restore,delete,download}`.

**Data export / import** (`Settings → System → Data`) is the one-shot migration ZIP. Use it when moving to another install, not for routine snapshots. The ZIP includes every page JSON, theme definition, font pick, device registration, and per-plugin settings (with secrets embedded, treat the file like a credential).

- **Export:** clicks straight to a `tesserae-export-<timestamp>.zip` download.
- **Import:** upload a ZIP from another install. The server validates every file against the matching JSON Schema before writing, then replaces state atomically. On Docker / HA App installs the in-place restart happens automatically; on a venv install the page flashes a "stop and restart" hint so nothing is left mid-flight.

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
