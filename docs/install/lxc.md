# Install Tesserae via LXC

Tesserae runs cleanly inside a Linux container, alongside the [Docker](docker.md) and bare-metal install paths. LXC containers are heavier than Docker but less constrained, closer to a small VM than a single-process sandbox, which suits anyone already running Proxmox VE on a homelab box or Ubuntu MicroCloud on a Raspberry Pi.

This page covers both:

- **Proxmox VE** for x86 / amd64 hosts (homelab servers, NUCs, mini-PCs).
- **Ubuntu MicroCloud** for arm64 Raspberry Pi hosts.

Both wrap the same underlying LXC / LXD stack. Proxmox ships a nicer web UI; MicroCloud has less overhead and is the right pick on a Pi. Proxmox has an unofficial arm64 port maintained outside the official project, so on Raspberry Pi hardware MicroCloud is the safer path.

!!! tip "Or just install bare-metal"
    If you don't already use containers, the [manual install path](server.md#manual-install) on bare-metal Linux is simpler. LXC is for people who want their applications isolated in a container without Docker's process-model constraints.

## Hardware sizing

Confirmed on a Raspberry Pi CM4 with 8GB RAM: roughly 1.5GB resident with a running Tesserae instance and a small composed dashboard. **4GB is comfortable**, **2GB also confirmed working** with about 1GB of headroom after install. Webpage-widget rendering is the spikiest component; if you compose heavy dashboards, leave headroom.

For the container's root device, 2GB is sufficient. With thin provisioning, the on-disk footprint matches actual usage anyway.

## Create the container

=== "Proxmox VE (x86)"

    1. **Base template:** download the current Debian Trixie LXC template via **Datacenter → Storage → CT Templates** in the Proxmox web UI. Use the standard template, not the cloud variant.
    2. **Create CT:** standard container, 2GB root disk, 2–4GB RAM, unprivileged. Pick a hostname (`tesserae` is fine).
    3. Start the container and open its console from the Proxmox UI.

=== "Ubuntu MicroCloud (Raspberry Pi)"

    Install MicroCloud from a running Raspberry Pi OS install; the [upstream MicroCloud docs](https://canonical.com/microcloud/docs) cover the setup in detail. Then launch a container from the current Trixie image (not the cloud variant):

    ```sh
    lxc launch images:debian/trixie tesserae
    ```

    Set memory and any other limits via `lxc config set tesserae limits.memory 4GiB`. Get a shell inside the container:

    ```sh
    lxc shell tesserae
    ```

## Inside the container

The container starts as a minimal Debian. Install the packages Tesserae needs, plus the conveniences worth having:

```sh
apt update
apt install -y git python3 python3.13-venv
apt install -y openssh-server less rsync
```

`python3.13-venv` is the Trixie name; on Bookworm it's `python3.11-venv`. The Trixie image is recommended because Tesserae targets Python 3.11+ and a current Python ships with the base image.

Create a non-root user to own the install:

```sh
adduser --gecos "" tesserae
```

SSH in as that user (if you installed `openssh-server`) or `su tesserae` from the container shell, then follow the [manual install steps](server.md#manual-install) on the main install page.

Once the venv is built and Tesserae starts cleanly, the [systemd service path](server.md#run-as-a-service-linux) keeps it running across reboots. The systemd unit runs identically inside the container as on bare metal.

## Automation with cloud-init

MicroCloud and Proxmox both support cloud-init, so the package install, user creation, venv build, and systemd setup can be baked into a single config you reuse across containers (or hyperscaler instances).

A ready-to-use config lives at [`scripts/cloud-init.yaml`](https://github.com/dmellok/tesserae/blob/main/scripts/cloud-init.yaml) in the repo. Pass it to MicroCloud via:

```sh
lxc launch images:debian/trixie/cloud tesserae \
  --config=user.user-data="$(cat scripts/cloud-init.yaml)"
```

Note the `/cloud` suffix on the image name. The slim `images:debian/trixie` image used in the manual section above does not include cloud-init, so the user-data block never runs. The `/cloud` variant ships with cloud-init pre-installed and is the right pick for any automated provisioning path.

On Proxmox, paste the same file into **Datacenter → CT → Cloud-Init → User Data**. Edit the `ssh_authorized_keys` block first to add your public key so you can log in once the container's up.

!!! tip "If `microcloud init` didn't auto-detect your storage"
    Some MicroCloud installs don't pick up NVMe / dedicated storage on their own. If `lxc launch` complains it has no pool to land the container in, list pools with `lxc storage list` and pass the right name through:

    ```sh
    lxc launch images:debian/trixie/cloud tesserae --storage <pool-name> \
      --config=user.user-data="$(cat scripts/cloud-init.yaml)"
    ```

First boot takes 5 to 10 minutes on a Pi (dominated by `pip install` plus the Playwright Chromium download); follow progress with `tail -f /var/log/cloud-init-output.log` inside the container.

The config runs `playwright install-deps chromium` (root-side apt install of runtime libs) and `playwright install chromium` (download the Playwright-pinned browser into the `tesserae` user's cache), so the webpage widget and **Send → Webpage** tab work out of the box on both x86 and arm64 Trixie images. Playwright's bundled Chromium is the supported path for 64-bit Linux; the [system-browser fallback](server.md#chromium-for-webpage-rendering) is only needed for 32-bit Raspberry Pi OS.

!!! warning "LAN-only by design"
    Don't run Tesserae open to the internet. The auth model is a single-password gate intended for a trusted home LAN, not a public-facing service. Use a homelab box, a Pi on your LAN, or a private network on whatever host you pick. If you need remote access, put Tesserae behind a VPN (Tailscale, WireGuard, ZeroTier) rather than exposing it directly.

## Config and persistent state

Tesserae does not use `~/.config/` (no XDG Base Directory convention). All persistent state lives in a single `data/` directory next to the install:

```
/home/tesserae/tesserae/data/
├── core/                  settings.json, pages.json, events.db, .chromium, backups/
├── plugins/<id>/          per-plugin state (cached responses, OAuth tokens)
├── renderers/<id>/        per-renderer state
├── devices/<id>/          per-device state
└── marketplace/           installed widgets / fonts / themes from the catalog
```

Back up `/home/tesserae/tesserae/data/` to preserve your dashboard pages, device registrations, theme picks, plugin settings, and the sqlite event log. The directory is the only stateful thing in the install; everything else is recreated by re-cloning the repo.

If you'd rather mount the data dir as an LXC volume so the container itself is disposable, point it at a host path with `lxc config device add tesserae data disk source=/srv/tesserae path=/home/tesserae/tesserae/data`. Override the data location via `TESSERAE_DATA_ROOT` if you want it somewhere other than the install dir.

## Next steps

- [First run](server.md#first-run) covers the onboarding wizard.
- [Install a client](clients.md) for your panel hardware.
- [Set up a device](devices.md) to bind a dashboard.
