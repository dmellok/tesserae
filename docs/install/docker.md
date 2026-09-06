# Install Tesserae via Docker

The official Docker image is the fastest install if you'd rather not
touch Python. It ships Tesserae plus a known-good Playwright Chromium
so the webpage / dashboard renderer works out of the box.

The image is hosted on GitHub Container Registry as
[`ghcr.io/dmellok/tesserae`](https://github.com/dmellok/tesserae/pkgs/container/tesserae).
Tags follow Tesserae versions (e.g. `:0.14.0`, `:0.14`), plus a
`:latest` pointing at the most recent release tag.

!!! tip "Pin the tag in setups you care about"
    `:latest` is convenient for kicking the tyres but moves whenever
    a new release is published. Pin to a specific version (e.g.
    `:0.14.0`) so `docker compose pull && up -d` is the deliberate
    upgrade step.

## Quick start

```sh
mkdir ~/tesserae && cd ~/tesserae
curl -fsSLO https://raw.githubusercontent.com/dmellok/tesserae/main/docker-compose.yml
docker compose up -d
```

That's it. Open `http://<host-ip>:8765` (or
`http://tesserae.local:8765` once mDNS comes up), the first request
walks through password setup and the onboarding wizard.

## Pin the secret key before you store any credentials

Stored secrets — API keys, broker passwords, the Home Assistant token —
are encrypted at rest. Without `TESSERAE_SECRET_KEY` the key is derived
from the persisted session secret, which survives an ordinary restart and
**does not survive a recreated container or a data folder restored
alongside a regenerated session secret**.

When it does not survive, the secrets decrypt to an empty string while
every non-secret setting beside them is intact. The install looks
configured and is not: the Home Assistant URL is still right, and every
widget that needs the token reports the install as unconfigured. Nothing
warns you at the moment the credentials go, only the next time something
tries to use one.

Generate a key once and put it in the compose file before you enter
anything worth keeping:

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    environment:
      TESSERAE_SECRET_KEY: "<the 64 hex chars from above>"
    volumes:
      - ./data:/app/data
```

Tesserae warns at startup while it is running on the derived key, and the
warning carries a ready-to-paste line. Adding the key later is fine — any
secret entered *after* it is pinned is safe; ones entered before are still
tied to the session secret and want re-entering once.

Treat it like any other secret: it decrypts the credentials in your data
folder, so it does not belong in the same backup as them, and it does not
belong in a public repo alongside your compose file.

The default `docker-compose.yml` uses **host networking**, which is
the right choice for a self-hosted Pi / mini-PC / NAS appliance:

- The render-frame URL Tesserae embeds in every MQTT push points at
  the host's real LAN IP, so your panels can actually fetch frames.
- The built-in MQTT broker is reachable on the host's port 1883
  without you publishing it from a `ports:` block.
- mDNS works, so `tesserae.local` resolves on the LAN.

Linux only, though. Docker Desktop on Mac / Windows handles host
networking differently, see [Bridge networking](#bridge-networking)
below if you're testing there.

## What's in the image

- **Tesserae**, installed from the repo at the tag.
- **Playwright Chromium**, preinstalled from the
  [official Playwright Python base image](https://hub.docker.com/_/microsoft-playwright-python)
  so the webpage renderer works without `playwright install`. Chromium
  runs with `--disable-dev-shm-usage`, so Docker's default 64 MB
  `/dev/shm` is enough and the compose file needs no `shm_size`.
- **Waitress** as the WSGI server. Production-tuned by default; no
  nginx required for a single-user install.
- **Non-root user** (`tesserae`, uid 1001) for defence in depth. This
  is not a widget sandbox, widgets still execute in the same Python
  process and can read anything this user can read (see
  [issue #3](https://github.com/dmellok/tesserae/issues/3)).

## Change the web port

Under **host networking**, Tesserae binds its web port directly on
the host, so a clash with something already on 8765 (nginx, another
app) is a real possibility. Move it with the `TESSERAE_BIND_PORT`
environment variable, no `command:` override needed:

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    network_mode: host
    volumes: ["./data:/app/data"]
    environment:
      TESSERAE_BIND_PORT: "8766"
```

Then browse to `http://<host-ip>:8766`.

Under **bridge networking**, leave the container's internal port
alone and remap the host side instead: `ports: ["8766:8765"]`, and
browse to `http://<host-ip>:8766`.

(`TESSERAE_BIND_PORT` needs image `0.378.0` or newer. On older
images, override the CMD instead:
`command: ["tesserae", "--port", "8766"]`.)

The full list of environment variables and flags lives in the
[configuration reference](configuration.md).

## Configuring the MQTT broker

Tesserae publishes frames over MQTT. Two ways to configure it:

### Use the built-in broker

After first-run, go to **Settings → Server → MQTT** and toggle the
built-in broker on. Then expose port 1883 from the container so your
panels can reach it:

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    ports:
      - "8765:8765"
      - "1883:1883"        # built-in broker
    volumes:
      - ./data:/app/data
```

The built-in broker is amqtt, which speaks MQTT v3.1.1 only. Tesserae's
own Pi / ESP32 clients are fine; if you connect with MQTT Explorer /
MQTTX / Home Assistant / Node-RED you'll need to set their protocol
version to 3.1.1, v5 clients get rejected.

### Point at Mosquitto (or HA's broker)

If you already run Mosquitto, point Tesserae at it via **Settings →
Server → MQTT** (host, port, username, password). The compose file
doesn't need any extra ports for this path.

For a "full MQTT v5 with a Mosquitto sidecar" setup, add the sidecar
yourself:

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    ports: ["8765:8765"]
    volumes: ["./data:/app/data"]
    depends_on: [mosquitto]

  mosquitto:
    image: eclipse-mosquitto:2
    restart: unless-stopped
    ports: ["1883:1883"]
    volumes:
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf
```

Then set the broker host to `mosquitto` in Tesserae's settings (the
service name is the resolvable hostname inside the compose network).

If your broker runs **on the Docker host itself** and Tesserae is on
bridge networking, the container can't reach it via `localhost` (that
is the container's own loopback). Either use the host's LAN IP as the
broker host, or add the standard host alias to the service:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

and set the broker host to `host.docker.internal`. Under host
networking this doesn't come up, `localhost:1883` just works.

## Bridge networking

If you can't use host networking, typically Docker Desktop on Mac /
Windows, or a setup with port conflicts on the host, switch to
bridge networking. Two things break under bridge that host networking
got for free, and **both need fixing** before your panels can talk to
Tesserae:

1. The **render-frame URL** in every MQTT push and the **MQTT broker
   URL** the wizard shows your panels both point at
   `detect_local_ip()`. Under bridge networking that resolves to the
   container's internal `172.x.x.x` address, which LAN clients can't
   reach. The fix: set `TESSERAE_HOST_IP` to your host's real LAN IP.
2. mDNS multicast doesn't escape the bridge network, so
   `tesserae.local` won't resolve on the LAN. That one you can't
   easily fix on bridge, use the host's IP directly, or run a
   separate mDNS reflector (out of scope here).

The compose snippet:

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    container_name: tesserae
    restart: unless-stopped
    ports:
      - "8765:8765"
      - "1883:1883"          # only if using the built-in MQTT broker
    volumes:
      - ./data:/app/data
    environment:
      TESSERAE_IN_DOCKER: "1"
      TESSERAE_HOST_IP: "192.168.1.50"   # your host's actual LAN IP
```

Find your host's LAN IP with `hostname -I` (first address printed) or
`ip addr show eth0`. Without `TESSERAE_HOST_IP` set, Tesserae logs a
loud warning on startup and the admin UI flags the bad URL, but it
won't auto-fix itself, because the host's IP isn't introspectable
from inside a bridge-networked container.

## Upgrading

```sh
docker compose pull       # fetch the new image
docker compose up -d      # recreate with the new image
```

Your `./data` volume carries settings, pages, schedules, history, and
the render cache across the upgrade.

The in-app **Settings → System → Updates** card is hidden under
Docker, a `git pull` inside a layered filesystem would lose changes
on the next image rebuild, so upgrades go through `docker compose
pull` instead. The Settings page surfaces a `docker compose` hint
instead of the update form.

## Backups + data export

Two related features under **Settings → System**:

- **Backups** (`Settings → System → Backups`) snapshots the full Tesserae state into a ZIP under `./data/core/backups/` on your host. Use it for periodic safety copies and rollback.
- **Data export / import** (`Settings → System → Data`) is the one-shot migration ZIP for moving to another install, not for routine snapshots.

Both still work under Docker. Snapshotting `./data` with your normal backup tool (restic, borg, rsnapshot, or a plain cron'd tarball) covers everything Tesserae has, including the in-app backups.

## Limits

- **No self-update.** Use `docker compose pull`. The in-app update
  flow is gated off when `TESSERAE_IN_DOCKER=1` (the image sets that
  for you).
- **mDNS needs host networking.** See above.
- **arm/v7 is not built.** Pi 3 and below would need a different
  Playwright story; not currently in scope.
- **The image is ~970 MB to pull**, ~2.5 GB on disk uncompressed.
  Most of that is Chromium and its sandboxes. There's no smaller
  Tesserae image plan, the renderer fundamentally needs a real
  browser.
