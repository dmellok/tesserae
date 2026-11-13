# Install Tesserae via Docker

Use Docker to install Tesserae without setting up Python.
The official image includes Chromium for drawing dashboards and webpages.

The image is hosted on GitHub Container Registry as
[`ghcr.io/dmellok/tesserae`](https://github.com/dmellok/tesserae/pkgs/container/tesserae).
Tags follow Tesserae versions (e.g. `:0.418.1`, `:0.418`), plus two
moving tags:

| Tag | Follows | Use it for |
| --- | --- | --- |
| `:latest` | the most recent [GitHub Release](https://github.com/dmellok/tesserae/releases) | the default `docker-compose.yml`; what the HA App's stable channel ships |
| `:edge` (alias `:main`) | every commit on `main` | trying a fix before it is released; the HA App's edge channel |

To track `main`, use `:edge`.

!!! tip "Pin the tag in setups you care about"
    `:latest` changes with each release. Pin a version such as
    `:0.418.1` to control upgrades with `docker compose pull && up -d`.

## Quick start

```sh
mkdir ~/tesserae && cd ~/tesserae
curl -fsSLO https://raw.githubusercontent.com/dmellok/tesserae/main/docker-compose.yml
docker compose up -d
```

Open `http://<host-ip>:8765` (or
`http://tesserae.local:8765` once mDNS comes up). The first request
opens password setup and the onboarding wizard.

## Pin the secret key before you store any credentials

Set `TESSERAE_SECRET_KEY` before saving API keys, broker passwords or the Home Assistant token.
It encrypts these stored secrets.

Without it, Tesserae derives an encryption key from the session secret in `settings.json`.
That survives normal restarts and upgrades. It is lost if you:

- Restore data without `settings.json`
- Recreate a container without its data volume

Stored secrets then read back as empty strings. Other settings, such as the Home Assistant URL, stay intact.
Widgets that need the lost token report an unconfigured install.

There is no warning when credentials disappear. You find out when something next tries to use them.
Generate a key once and add it to your compose file:

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

Until you set a key, Tesserae's startup warning includes a configuration line you can paste.
If you add the key later, re-enter earlier secrets once. New secrets use the pinned key.

Keep the key separate from data backups. It can decrypt their credentials.
Never put it in a public repo with your compose file.

The default `docker-compose.yml` uses host networking: the container shares the host's network.
This suits a Pi, mini-PC or NAS:

- The render-frame URL Tesserae embeds in every MQTT push points at
  the host's real LAN IP, so your panels can fetch frames.
- The built-in MQTT broker is reachable on the host's port 1883
  without you publishing it from a `ports:` block.
- mDNS works, so `tesserae.local` resolves on the LAN.

These benefits apply to Linux only. On Docker Desktop for Mac or Windows,
see [Bridge networking](#bridge-networking).

## What's in the image

- Tesserae, installed from the repo at the tag.
- Playwright Chromium, preinstalled from the
  [official Playwright Python base image](https://hub.docker.com/_/microsoft-playwright-python)
  so the webpage renderer works without `playwright install`. Chromium
  runs with `--disable-dev-shm-usage`, so Docker's default 64 MB
  `/dev/shm` is enough and the compose file needs no `shm_size`.
- Waitress, the web server. Ready for normal use; a single-user install needs no nginx.
- A non-root user (`pwuser`, uid 1001). Widgets still run in the same Python
  process and can read anything this user can read. They are not isolated from it (see
  [issue #3](https://github.com/dmellok/tesserae/issues/3)).

## Change the web port

With host networking, change `TESSERAE_BIND_PORT` if another app uses port 8765.
No `command:` override is needed:

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

With bridge networking, map the host port instead: `ports: ["8766:8765"]`.
Open `http://<host-ip>:8766`.

(`TESSERAE_BIND_PORT` needs image `0.378.0` or newer. On older
images, override the CMD instead:
`command: ["tesserae", "--port", "8766"]`.)

The full list of environment variables and flags lives in the
[configuration reference](configuration.md).

## Configuring the MQTT broker

Skip this section if you use REST, the default. MQTT devices need a broker.

### Use the built-in broker

Turn on **Settings → Server → MQTT broker → Built-in broker**.
With default host networking, it is already reachable on port 1883.
With [bridge networking](#bridge-networking), publish port 1883 too:

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

The built-in amqtt broker supports MQTT v3.1.1 only. Version 5 clients are rejected.
Tesserae's Pi and ESP32 clients work as they are.
Set version 3.1.1 in MQTT Explorer, MQTTX, Home Assistant or Node-RED.

### Point at Mosquitto (or HA's broker)

In **Settings → Server → MQTT broker**, enter the Mosquitto host, port, username and password.

No extra compose ports are needed.

To run MQTT v5, add Mosquitto as a second container:

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

Set the broker host to `mosquitto`, its name inside the compose network.

If the broker runs on the Docker host, bridge networking cannot reach it at `localhost`.
That address points inside the container. Use the host's LAN IP, or add this alias:

```yaml
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

Then set the broker host to `host.docker.internal`.
With host networking, `localhost:1883` works directly.

## Bridge networking

Use bridge networking on Docker Desktop or when host ports conflict.
It gives the container its own network address. Two changes are needed:

1. Set `TESSERAE_HOST_IP` to your host's LAN IP.
   Otherwise, frame URLs and the broker URL use the container's unreachable `172.x.x.x` address.
2. mDNS multicast doesn't escape the bridge network, so
   `tesserae.local` won't resolve on the LAN. Use the host's IP directly.
   A separate mDNS reflector can relay these announcements, but needs setup outside this guide.

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
warning on startup and the admin UI flags the bad URL. It cannot
fix the URL automatically because a bridge-networked container
cannot detect the host's IP.

## Upgrading

```sh
docker compose pull       # fetch the new image
docker compose up -d      # recreate with the new image
```

Your `./data` volume survives upgrades.

**Settings → System → Updates** shows a `docker compose` hint instead of an update form.
Use `docker compose
pull` to upgrade. Changes made with `git pull` inside the container would be lost on the next image rebuild.

## Backups + data export

Two related features under **Settings → System**:

- Backups (`Settings → System → Backups`) saves your full state as a ZIP under `./data/core/backups/` on the host.
  Use it for regular backups and rollback.
- Data export / import (`Settings → System → Backups → Migrate to another install`) moves your setup to another self-hosted server. Use Backups for routine copies.

Both work under Docker. Backing up `./data` also saves everything, including the in-app backups.
Use restic, borg, rsnapshot or a scheduled tarball.

## Limits

- No self-update. Use `docker compose pull`. The image sets `TESSERAE_IN_DOCKER=1` to disable in-app updates.
- mDNS needs host networking. See above.
- No arm/v7 build. Pi 3 and below need a different Playwright setup, which is not planned.
- Older x86-64 CPUs work because the image pins `numpy<2.4`.
  Newer numpy needs x86-64-v2 (SSE4.2 and POPCNT), missing on pre-2009 CPUs and basic VM models such as `kvm64`.

  When building for x86-64-v2 or arm64, `--build-arg NUMPY_SPEC=` keeps the newest numpy.
  On a VM, consider setting the CPU type to `host`.
- The download is about 970 MB; unpacked, it uses about 2.5 GB.
  Most is Chromium and its sandboxes. No smaller image is planned because rendering needs a browser.
