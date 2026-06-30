# Quickstart: Pi + Pimoroni Inky, automated via cloud-init

The fastest way to stand up a dedicated Raspberry Pi driving a Pimoroni Inky panel. Flash the SD card with Raspberry Pi Imager, drop one cloud-init file into the boot partition, edit two values, insert + power on. After one reboot you have a `tesserae-pi-bin-client` systemd service polling your Tesserae server.

!!! tip "Already have the Pi running?"
    Skip this page; use the manual flow at [Raspberry Pi + Pimoroni Inky](pi-inky.md). cloud-init is for *fresh* SD cards, not for adding the client to a Pi you're already using for something else.

## When to use this

- You're setting up a **new Pi** dedicated to driving an Inky panel.
- You want **no manual SSH / shell** between flashing the SD card and seeing the panel paint.
- Your Tesserae server is already running and reachable at a known URL on the LAN.

If any of those don't fit, the [manual install path](pi-inky.md) is the better choice; it walks you through the same steps interactively.

## 01 — Flash Raspberry Pi OS Lite

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (Mac / Windows / Linux):

1. Pick **Raspberry Pi OS Lite (64-bit)**. Lite is enough; no desktop required.
2. Pick the SD card.
3. Open **Advanced options** (Ctrl-Shift-X on Win/Linux, Cmd-Shift-X on Mac):
   - **Set WiFi**: SSID + password + country.
   - **Set locale**: timezone + keyboard layout.
   - **Enable SSH** with a public key (paste your `~/.ssh/id_ed25519.pub` or similar) so you can log in later for diagnostics.
   - **Do NOT set a username here** — the cloud-init file below creates one.
4. **Save**, then **Write**. Wait for the flash to finish.

## 02 — Drop in the cloud-init file

After the flash finishes, eject and re-insert the SD card so the BOOT partition shows up in your file browser.

1. Download [`scripts/pi-client-cloud-init.yaml`](https://github.com/dmellok/tesserae/blob/main/scripts/pi-client-cloud-init.yaml) from the Tesserae repo.
2. Copy it to the SD card's BOOT partition, renaming it to `user-data` (overwriting whatever Pi Imager wrote there).
3. Leave `network-config` and `meta-data` as Pi Imager wrote them; your WiFi credentials are in `network-config` and stay separate.
4. **Edit two values** in the copy of `user-data`:

    ```toml
    [rest]
    server_url = "http://tesserae.local:8765"  # <-- your Tesserae server URL

    [panel]
    model = "inky_13_3"                        # <-- inky_4 / inky_5_7 / inky_7_3 / inky_13_3
    ```

    If your Tesserae host doesn't broadcast `tesserae.local` over mDNS, use its IP (e.g. `http://192.168.1.10:8765`). Default port is 8765.

5. Save. Unmount the SD card cleanly.

## 03 — Boot the Pi

Insert the SD card into the Pi and power it on. **First boot takes 5 to 10 minutes** because cloud-init:

- Updates apt + installs `git`.
- Creates a `tesserae` user with `gpio` + `spi` group membership.
- Clones [`tesserae-device-pi-bin`](https://github.com/dmellok/tesserae-device-pi-bin) into `/home/tesserae/tesserae-device-pi-bin`.
- Runs the client's `install.sh --non-interactive`, which:
  - Apt-installs the Inky runtime deps (`libopenjp2-7`, `libtiff6`, `build-essential`, etc.).
  - Enables SPI + I²C via `raspi-config`.
  - Adds `dtoverlay=spi0-0cs` to `/boot/firmware/config.txt` (frees the chip-select pin for Inky).
  - Builds a Python venv, `pip install -e .` to pull `inky[rpi]`.
  - Symlinks `tesserae-pi-bin-client` into `/usr/local/bin/`.
  - Installs + enables the `tesserae-pi-bin-client` systemd unit.
- Reboots so SPI / I²C take effect.

After the reboot the systemd unit starts automatically. From this point the Pi is announcing itself to your Tesserae server every poll interval (default 60s).

## 04 — Pair the Pi

In the Tesserae web UI:

1. **Settings → Devices → Discovered**. The new Pi appears within a couple of poll cycles (under two minutes after the post-cloud-init reboot).
2. Click **Register** on the entry. Tesserae mints a per-device access token and stores it; the Pi picks it up on its next `/discover` poll and switches to authenticated polling.
3. The device moves from **Discovered** to the regular **Devices** list.

No tokens to copy. No prompts to type. The MAC-match auto-claim flow handles everything.

## 05 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. Drop in widgets; bind the page to your Inky in the device picker on the right.
3. Hit **Push**. The Pi paints the frame on the next wake.

## 06 — Set the refresh

Open the dashboard's **Schedules** card. For a wall-powered Pi:

- **Every N minutes** is the natural fit, refresh as often as the panel's physical refresh cycle allows (15 to 30 seconds for a full Spectra 6 update).
- **Daily at a set time** for a once-a-day cadence.

For battery-powered Pi setups (rare but possible), **smart sync** keeps the panel painting a freshly-rendered frame at each wake without burning battery on idle renders.

## What to do if something didn't work

| Symptom | Likely cause + fix |
|---|---|
| Pi doesn't appear under Discovered after 10 minutes. | SSH into the Pi (`ssh tesserae@<pi-ip>`), check `journalctl -u tesserae-pi-bin-client -f`. Common: server_url unreachable, or SPI didn't enable. |
| Service log says `No EEPROM detected!` | I²C didn't enable. SSH in and run `sudo raspi-config nonint do_i2c 0 && sudo reboot`. |
| Service log says `Chip Select … currently claimed by spi0 CS0` | The `dtoverlay=spi0-0cs` line is missing from `/boot/firmware/config.txt`. Add it manually and reboot. |
| Service log says `connection refused` to the server URL | Tesserae server isn't reachable from the Pi's network. Verify `curl http://<server>:8765/api/healthz` from the Pi. |
| Pi reboots forever / never finishes cloud-init | Watch progress: `tail -f /var/log/cloud-init-output.log` on the Pi (after SSH-ing in once first boot reaches a login prompt). |

## What the cloud-init does NOT do

- **Doesn't open any ports** on a host firewall.
- **Doesn't configure WiFi**: that's in `network-config`, written by Pi Imager.
- **Doesn't tune `quiet_hours`, smart sync, or other server-side schedule behaviour**: that lives in the Tesserae server's UI on a per-device basis.
- **Doesn't pin the client to a specific version**: the install pulls `main` from `tesserae-device-pi-bin`. To pin, edit the `runcmd` `git clone` line to add `--branch <tag>`.

## Next steps

- [Compose a multi-cell dashboard](../widgets/community.md): browse the community widget catalog for one-click installs.
- [Schedule rotations](../install/devices.md#next-steps) to cycle through several dashboards across the day.
- [Add another Pi or panel](../install/devices.md#multiple-panels): re-run this cloud-init on another SD card with a different `device_id` in the config.
