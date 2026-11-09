# Quickstart: Raspberry Pi + Pimoroni Inky Impression

A Raspberry Pi wired to a Pimoroni Inky Impression: 4", 5.7", 7.3", 13.3", or a pHAT / wHAT. About ten minutes from a fresh Pi OS install to a painted panel.

!!! tip "Fresh SD card?"
    If you're setting up a brand-new Pi specifically for this, the [cloud-init path](pi-inky-cloud-init.md) is zero-touch: flash the SD card with Pi Imager, drop one yaml into the boot partition, edit two values, boot. No SSH required.

!!! tip "One client for every Inky panel"
    [`tesserae-device-pi`](https://github.com/dmellok/tesserae-device-pi) drives every Pimoroni Inky panel. It reads the panel from the HAT, tells the server when it pairs, and the server picks the frame format: a Spectra 6 Impression gets frames already packed for the panel (the fastest path), every other panel gets a PNG the Pi dithers itself. It replaces the earlier `tesserae-device-pi-png` and `tesserae-device-pi-bin` clients.

## 01 — Install the Pi client

On the Pi wired to your Inky, as your normal user:

```sh
git clone https://github.com/dmellok/tesserae-device-pi.git
cd tesserae-device-pi
./scripts/install.sh            # add --bookworm on a Pi 5
```

The script enables SPI / I²C, asks for your Tesserae server URL (REST, no broker needed) and a device id, then installs a `tesserae-device-pi` systemd service that runs on boot. Reboot once when it asks, so SPI and I²C take effect. There is no panel to choose: the client reads it from the HAT.

The client is also on PyPI (`pip install tesserae-device-pi`); the [client README](https://github.com/dmellok/tesserae-device-pi#from-pypi) covers that path.

!!! note "Upgrading from pi-png or pi-bin?"
    Install the new client the same way. It picks up the old client's config, keeps the device's id and pairing, and disables the old service, so nothing needs re-pairing.

## 02 — Pair the panel

In **Settings → Devices**, your Pi appears under **Discovered** within a few seconds. Click **Register** to pair it. No tokens to copy.

If your Pi doesn't appear:

- Check `journalctl -u tesserae-device-pi -f` on the Pi for connection errors.
- Confirm the Pi can reach your Tesserae server's URL (`curl http://<server>:8765/healthz` from the Pi).

## 03 — Keep it up to date

The client updates itself. When a new release is out, **Settings → Firmware** offers it for the Pi; the update is signed, and if the new version fails to start the Pi puts the old one back.

## 04 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. Drop in a weather widget, a calendar, a Spotify card, whatever you want.
3. Bind the page to your Inky in the device picker on the right.
4. Hit **Push**, and the Pi paints the frame.

The composer sizes the layout to your panel automatically: 1600×1200 for the 13.3", 800×480 for the 7.3", 600×448 for the 5.7", 600×400 for the 4" (640×400 on the older 7-colour 4").

## 05 — Put it on a cadence

Open the dashboard's **Schedules** card:

- **Every N minutes**: refresh on a fixed cadence.
- **Daily at a set time**: a single frame per day.
- **Mixed schedule**: different cadence per time-of-day.

Pimoroni Inky panels are line-powered, so cadence is your choice. The constraint is the panel's refresh cycle, which is around 15 to 30 seconds for a full Spectra 6 update.

## Next steps

- [Pair multiple Pis](../install/devices.md#multiple-panels) to drive several panels from one server.
- [Browse the widget catalog](https://tesserae.ink/catalog/) for one-click community widgets.
- [Add a rotation](../install/devices.md#next-steps) to cycle through dashboards across the day.
