# Quickstart: Pi + Pimoroni Inky, automated via cloud-init

Set up a new Pi without logging in over SSH. After one reboot, [`tesserae-device-pi`](https://github.com/dmellok/tesserae-device-pi) starts automatically.

For the fastest setup, flash the SD card and add the files below. Your server URL goes in `meta-data`.

!!! tip "Already have the Pi running?"
    Use the [manual install](pi-inky.md). cloud-init only works on fresh SD cards.

## When to use this

- You're setting up a new Pi dedicated to driving an Inky panel.
- You want to go from flashing the SD card to the first frame without manual SSH / shell commands.
- Your Tesserae server is already running and reachable at a known URL on the LAN.

For other setups, use the [manual install path](pi-inky.md).

## 01 — Flash Raspberry Pi OS Lite

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on Mac, Windows or Linux.

1. Pick **Raspberry Pi OS Lite (64-bit)**. No desktop needed.
2. Pick the SD card.
3. Open **Advanced options** (Ctrl-Shift-X on Win/Linux, Cmd-Shift-X on Mac):
   - **Set WiFi**: enter your network name, password and country.
   - **Set locale**: choose your timezone and keyboard layout.
   - **Enable SSH**: paste a public key, such as `~/.ssh/id_ed25519.pub`, for later diagnostics.
   - **Do NOT set a username here**. cloud-init creates one.
4. **Save**, then **Write**. Wait for flashing to finish.

## 02 — Drop in the cloud-init file

Eject and reinsert the card. Open its BOOT partition in your file browser.

1. Download [`scripts/pi-client-cloud-init.yaml`](https://github.com/dmellok/tesserae/blob/main/scripts/pi-client-cloud-init.yaml).
2. Copy it to BOOT as `user-data`, replacing Pi Imager's file.
3. Leave `network-config` alone; it contains your Wi-Fi settings.
4. Don't edit `user-data`. Put settings in `meta-data`, so you can reuse the unchanged `user-data` file on other Pis.

## 03 — Edit `meta-data`

Open BOOT's `meta-data`. It already has an `instance-id: ...` line. Add your server URL and, optionally, a device id:

```yaml
instance-id: pi-inky-01
server_url: http://tesserae.local:8765
device_id: pi_lounge
```

- `instance-id`: a unique name for this Pi. Keep Pi Imager's value, or number your Pis (`pi-inky-01`, `pi-inky-02`, …).
- `server_url`: the address you use to open Tesserae. If `tesserae.local` isn't available, use an IP address: `http://192.168.1.10:8765`.
- `device_id`: optional, defaults to `pi_device`. Use a different id for each Pi: lowercase, 2 to 32 characters, starting with a letter.

The client detects the panel from the Inky HAT's EEPROM.

Save `meta-data`. Unmount the SD card cleanly.

## 04 — Boot the Pi

Insert the card and power on. First boot takes 5 to 10 minutes. cloud-init:

- Updates packages and installs Python with the Inky libraries.
- Installs [`tesserae-device-pi`](https://pypi.org/project/tesserae-device-pi/), plus `rpi-lgpio` on Pi 5.
- Enables the panel connections (SPI / I²C) and starts the client at boot.
- Sets up rollback if an update fails to start.
- Reboots.

The Pi announces itself to your Tesserae server every poll interval (default 60s).

## 05 — Pair the Pi

In the Tesserae web UI:

1. Open **Settings → Devices → Discovered**. The Pi appears within two poll cycles, under two minutes after reboot.
2. Click **Register**. Tesserae creates an access token. The Pi gets it on its next `/discover` poll.
3. The Pi switches to authenticated polling and moves to the **Devices** list.

Pairing matches the Pi's MAC address. You don't need to copy a token or answer prompts.

## 06 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. Drop in widgets; bind the page to your Inky in the device picker on the right.
3. Hit **Push**. The Pi paints the frame on the next wake.

## 07 — Set the refresh

Open the dashboard's **Schedules** card. For a wall-powered Pi:

- Choose **Every N minutes** for regular updates. Allow 15 to 30 seconds for a full Spectra 6 refresh.
- Choose **Daily at a set time** for one update per day.

For a battery-powered Pi, use **smart sync**. It draws a fresh image just before the Pi wakes, saving idle work.

## What to do if something didn't work

| Symptom | Likely cause + fix |
|---|---|
| Not in Discovered after 10 minutes | Connect with `ssh tesserae@<pi-ip>` and check `journalctl -u tesserae-device-pi -f`. Check that `server_url` is reachable and SPI is enabled. |
| `No EEPROM detected!` | Enable I²C: `sudo raspi-config nonint do_i2c 0 && sudo reboot`. |
| `Chip Select … currently claimed by spi0 CS0` | Add `dtoverlay=spi0-0cs` to `/boot/firmware/config.txt`, then reboot. |
| `connection refused` | Check server access from the Pi: `curl http://<server>:8765/healthz`. |
| `config.toml` contains `{{ ds.meta_data.server_url }}` | The template didn't render. Check that `user-data` starts with `## template: jinja`. Download a fresh `pi-client-cloud-init.yaml` from the repo. |
| Repeated reboots or unfinished setup | Once SSH works, watch `tail -f /var/log/cloud-init-output.log`. |

## What the cloud-init does NOT do

- It doesn't open firewall ports.
- It doesn't configure Wi-Fi. Pi Imager writes that to `network-config`.
- It doesn't change `quiet_hours`, smart sync or other schedule settings. Set these per device in Tesserae.
- It installs the latest `tesserae-device-pi` release from PyPI. Later updates come through **Settings → Firmware**. To pin a version, change the `runcmd` `pip install` line to `tesserae-device-pi==<version>`.

## Next steps

- [Compose a multi-cell dashboard](https://tesserae.ink/catalog/): browse the community widget catalog for one-click installs.
- [Schedule rotations](../install/devices.md#compose-a-dashboard) to cycle through several dashboards across the day.
- [Add another Pi or panel](../install/devices.md#multiple-panels): re-run this cloud-init on another SD card with a different `instance-id` in meta-data.
