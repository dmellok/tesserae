# Install Tesserae via LXC

Use LXC if you already run one of these container hosts:

- Proxmox VE for x86 / amd64 computers.
- Ubuntu MicroCloud for arm64 Raspberry Pi hosts.

Both use LXC / LXD, Linux containers that work more like small virtual machines than [Docker](docker.md).
They are heavier than Docker but have fewer restrictions.

Proxmox has a nicer web UI; MicroCloud uses fewer resources.
Proxmox's arm64 port is unofficial and maintained separately. MicroCloud is the safer choice on a Raspberry Pi.

!!! tip "On Proxmox VE? Use the helper script"
    The [helper script](#proxmox-ve-helper-script) creates the container and installs Tesserae with one command.
    Run it on the Proxmox host to skip the manual steps.

!!! tip "Or just install bare-metal"
    If you don't already use containers, the [manual install path](server.md#manual-install) on bare-metal Linux is simpler.

## Hardware sizing

Tested on a Raspberry Pi CM4 with 8GB RAM:

| Resource | Guidance |
| --- | --- |
| RAM in use | About 1.5GB with Tesserae and a small dashboard running |
| 4GB RAM | Comfortable |
| 2GB RAM | Confirmed working, with about 1GB free after install |
| Root disk | 2GB is enough |

Webpage widgets cause the largest memory spikes. Leave room for these if your dashboards are heavy.
With thin provisioning, the disk uses space only as needed.

## Create the container

=== "Proxmox VE (x86)"

    1. Download the current Debian Trixie LXC template via **Datacenter → Storage → CT Templates**.
       Use the standard template, not the cloud variant.
    2. Create an unprivileged standard container:
       use a 2GB root disk and 2 to 4GB RAM. Pick a hostname, such as `tesserae`.
    3. Start the container and open its console from the Proxmox UI.

=== "Ubuntu MicroCloud (Raspberry Pi)"

    Install MicroCloud on Raspberry Pi OS using the [MicroCloud docs](https://canonical.com/microcloud/docs).
    Then launch the current Trixie image, without cloud-init:

    ```sh
    lxc launch images:debian/trixie tesserae
    ```

    Set memory and any other limits via `lxc config set tesserae limits.memory 4GiB`. Get a shell inside the container:

    ```sh
    lxc shell tesserae
    ```

## Inside the container

Install the required packages and a few utilities in the Debian container:

```sh
apt update
apt install -y git python3 python3.13-venv
apt install -y openssh-server less rsync
```

Use `python3.13-venv` on Trixie or `python3.11-venv` on Bookworm.
Trixie is recommended: its included Python meets Tesserae's Python 3.11+ requirement.

Create a non-root user to own the install:

```sh
adduser --gecos "" tesserae
```

SSH in as that user if you installed `openssh-server`, or run `su tesserae` from the container shell.
Then follow the [manual install steps](server.md#manual-install).

Once Tesserae starts, [install the systemd service](server.md#run-as-a-service-linux) to keep it running across reboots.
It works the same inside the container as on a direct install.

## Proxmox VE helper script

This [community-scripts](https://community-scripts.org/) style installer:

- Creates an unprivileged Debian 13 container
- Installs the newest Tesserae release with Chromium
- Starts Tesserae as a systemd service

Run it on the Proxmox host:

```sh
COMMUNITY_SCRIPTS_URL=https://raw.githubusercontent.com/dmellok/tesserae/main/scripts/proxmox \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/dmellok/tesserae/main/scripts/proxmox/ct/tesserae.sh)"
```

1. Choose a channel within ten seconds:
    - `stable`: newest tagged release; the default if you do not answer
    - `edge`: follows `main`

    Set `var_tesserae_channel=edge` beforehand to skip this question.
2. Choose Default to use the script's sizes, or Advanced to choose your own settings:
   CPU, RAM, disk, network and other options.
3. Open the printed address, `http://<container-ip>:8765/`, to start setup.

Run `update` inside the container to get the newest release or commit on your chosen channel.
Data lives in `/opt/tesserae_data`. Set the port and other environment options in `/opt/tesserae_data/tesserae.env`.

The script sources live at [`scripts/proxmox/`](https://github.com/dmellok/tesserae/tree/main/scripts/proxmox) in the repo.

## Automation with cloud-init

Use cloud-init on MicroCloud or Proxmox to repeat the setup across containers or cloud servers.
One config installs packages, creates the user and sets up Python and systemd.

Pass [`scripts/cloud-init.yaml`](https://github.com/dmellok/tesserae/blob/main/scripts/cloud-init.yaml) to MicroCloud:

```sh
lxc launch images:debian/trixie/cloud tesserae \
  --config=user.user-data="$(cat scripts/cloud-init.yaml)"
```

Use the `/cloud` image, which includes cloud-init.
The slim `images:debian/trixie` image cannot run the user-data block.

On Proxmox, paste the file into **Datacenter → CT → Cloud-Init → User Data**.
First add your public key to `ssh_authorized_keys` so you can log in.

At startup, the config looks up and installs the newest tagged release.
It uses the `main` branch at that release, so **Settings → System** can update it later.
To pin a version, replace the tag lookup in `runcmd` with `tag=vX.Y.Z`.

!!! tip "If `microcloud init` didn't auto-detect your storage"
    MicroCloud may miss NVMe or dedicated storage. If `lxc launch` reports no storage pool,
    find one with `lxc storage list` and pass its name:

    ```sh
    lxc launch images:debian/trixie/cloud tesserae --storage <pool-name> \
      --config=user.user-data="$(cat scripts/cloud-init.yaml)"
    ```

First startup takes 5 to 10 minutes on a Pi, mostly for `pip install` and the Chromium download.
Follow progress inside the container with `tail -f /var/log/cloud-init-output.log`.

The config installs the browser needed by the webpage widget and **Send → Webpage**:

- `playwright install-deps chromium` installs system libraries as root.
- `playwright install chromium` downloads the browser into the `tesserae` user's cache.

This works on x86 and arm64 Trixie. Use Playwright's bundled Chromium on 64-bit Linux.
Only 32-bit Raspberry Pi OS needs the [system-browser fallback](server.md#chromium-for-webpage-rendering).

!!! warning "LAN-only by design"
    Don't expose Tesserae to the internet. Its single-password login is intended for a trusted home network.
    Use a local computer or a private network on your chosen host.
    For remote access, use a VPN such as Tailscale, WireGuard or ZeroTier.

## Config and persistent state

All saved data lives in `data/` next to the install. Tesserae does not use `~/.config/`:

```
/home/tesserae/tesserae/data/
├── core/                  settings.json, pages.json, events.db, .chromium, backups/
├── plugins/<id>/          per-plugin state (cached responses, OAuth tokens)
├── renderers/<id>/        per-renderer state
├── devices/<id>/          per-device state
└── marketplace/           installed widgets / fonts / themes from the catalog
```

Back up `/home/tesserae/tesserae/data/`. It contains all saved state; everything else can be restored by cloning the repo again.

To keep data outside a disposable container, mount a host directory:
`lxc config device add tesserae data disk source=/srv/tesserae path=/home/tesserae/tesserae/data`.
Use `TESSERAE_DATA_ROOT` to choose a different data location.

## Next steps

- [First run](server.md#first-run) covers the onboarding wizard.
- [Install a client](clients.md) for your panel hardware.
- [Set up a device](devices.md) to bind a dashboard.
