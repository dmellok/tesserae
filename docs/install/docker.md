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

Save this as `docker-compose.yml` somewhere you want to keep your
data, then run `docker compose up -d`:

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    container_name: tesserae
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
```

Open `http://localhost:8000` — the first request walks you through
password setup and the onboarding wizard.

The same compose file ships in the repo at the project root if you'd
rather `git clone` and pick it up there.

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
      - "8000:8000"
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
    ports: ["8000:8000"]
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

## The built-in broker URL: `TESSERAE_HOST_IP`

If you're using the **built-in MQTT broker** (Settings → Server →
MQTT), Tesserae shows your panels what `mqtt://...` address to point
at. Under Docker's default bridge networking, the container has an
internal IP (`172.18.0.x` or similar) that LAN clients can't reach —
the broker URL the onboarding wizard would otherwise show is useless
to your panels.

Two ways to fix it:

### Set `TESSERAE_HOST_IP` (simplest)

Find your Docker host's LAN IP (typically what `ip addr show eth0` or
`hostname -I` prints), then set it in `docker-compose.yml`:

```yaml
services:
  tesserae:
    environment:
      TESSERAE_IN_DOCKER: "1"
      TESSERAE_HOST_IP: "192.168.1.50"   # your host's actual LAN IP
```

`docker compose up -d` after editing. The onboarding wizard's broker
URL and the Settings → MQTT broker card now show the right address.

### Or use host networking

`network_mode: host` shares the host's network namespace with the
container, so `detect_local_ip()` returns the host's real LAN IP
automatically — no env var needed. Also fixes the mDNS issue below.
See the section below.

## mDNS / tesserae.local

The mDNS advertiser needs **host networking** to announce
`tesserae.local` on your LAN — Docker's default bridge network is
isolated, so multicast doesn't escape. If you want
`http://tesserae.local:8000` to resolve from another machine on the
LAN:

```yaml
services:
  tesserae:
    image: ghcr.io/dmellok/tesserae:latest
    network_mode: host
    volumes:
      - ./data:/app/data
```

Drop the `ports:` block when using host networking — the container
listens directly on the host's port 8000. Linux only; host mode is a
no-op on Docker Desktop for Mac/Windows.

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
