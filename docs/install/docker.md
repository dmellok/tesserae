# Install Tesserae via Docker

The official Docker image is the fastest install if you'd rather not
touch Python. It ships Tesserae plus a known-good Playwright Chromium
so the webpage / dashboard renderer works out of the box.

The image is hosted on GitHub Container Registry as
[`ghcr.io/dmellok/tesserae`](https://github.com/dmellok/tesserae/pkgs/container/tesserae).
Tags follow Tesserae versions: `:0.7.4`, `:0.7`, plus a `:latest`
pointing at the most recent release tag.

!!! tip "Pin the tag in setups you care about"
    `:latest` is convenient for kicking the tyres but moves whenever
    a new release is published. Pin to a specific version (`:0.7.4`)
    so `docker compose pull && up -d` is the deliberate upgrade step.

## Quick start

```sh
mkdir ~/tesserae && cd ~/tesserae
curl -fsSLO https://raw.githubusercontent.com/dmellok/tesserae/main/docker-compose.yml
docker compose up -d
```

That's it. Open `http://<host-ip>:8765` (or
`http://tesserae.local:8765` once mDNS comes up) — the first request
walks through password setup and the onboarding wizard.

The default `docker-compose.yml` uses **host networking**, which is
the right choice for a self-hosted Pi / mini-PC / NAS appliance:

- The render-frame URL Tesserae embeds in every MQTT push points at
  the host's real LAN IP, so your panels can actually fetch frames.
- The built-in MQTT broker is reachable on the host's port 1883
  without you publishing it from a `ports:` block.
- mDNS works, so `tesserae.local` resolves on the LAN.

Linux only, though. Docker Desktop on Mac / Windows handles host
networking differently — see [Bridge networking](#bridge-networking)
below if you're testing there.

## What's in the image

- **Tesserae**, installed from the repo at the tag.
- **Playwright Chromium**, preinstalled from the
  [official Playwright Python base image](https://hub.docker.com/_/microsoft-playwright-python)
  so the webpage renderer works without `playwright install`.
- **Waitress** as the WSGI server. Production-tuned by default; no
  nginx required for a single-user install.
- **Non-root user** (`tesserae`, uid 1001) for defence in depth. This
  is not a widget sandbox — widgets still execute in the same Python
  process and can read anything this user can read (see
  [issue #3](https://github.com/dmellok/tesserae/issues/3)).

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
version to 3.1.1 — v5 clients get rejected.

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

## Bridge networking

If you can't use host networking — typically Docker Desktop on Mac /
Windows, or a setup with port conflicts on the host — switch to
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
   easily fix on bridge — use the host's IP directly, or run a
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
loud warning on startup and the admin UI flags the bad URL — but it
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
Docker — a `git pull` inside a layered filesystem would lose changes
on the next image rebuild, so upgrades go through `docker compose
pull` instead. The Settings page surfaces a `docker compose` hint
instead of the update form.

## Backups

The **Settings → System → Backups** card still works — backups land
in `./data/core/backups/` on your host. Snapshotting `./data` with
your normal backup tool (restic, borg, rsnapshot, or a plain cron'd
tarball) covers everything Tesserae has, including those backups.

## Limits

- **No self-update.** Use `docker compose pull`. The in-app update
  flow is gated off when `TESSERAE_IN_DOCKER=1` (the image sets that
  for you).
- **mDNS needs host networking.** See above.
- **arm/v7 is not built.** Pi 3 and below would need a different
  Playwright story; not currently in scope.
- **The image is ~970 MB to pull**, ~2.5 GB on disk uncompressed.
  Most of that is Chromium and its sandboxes. There's no smaller
  Tesserae image plan — the renderer fundamentally needs a real
  browser.
