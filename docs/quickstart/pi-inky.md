# Quickstart: Raspberry Pi + Pimoroni Inky Impression

Connect your Pi to an Inky panel. Setup takes about ten minutes from a fresh Pi OS install.

!!! tip "Fresh SD card?"
    For a new Pi, use the [cloud-init path](pi-inky-cloud-init.md). Flash the SD card, copy the yaml file, edit two values, then boot. You don't need SSH.

!!! tip "One client for every Inky panel"
    [`tesserae-device-pi`](https://github.com/dmellok/tesserae-device-pi) reports your panel when it pairs. Tesserae chooses the image format:

    - Spectra 6 Impression: ready-to-display frames, the fastest option.
    - Other Inky panels: PNG images. The Pi reduces the colours to match the panel (dithering).

## 01 — Install the Pi client

On the Pi wired to your Inky, as your normal user:

```sh
git clone https://github.com/dmellok/tesserae-device-pi.git
cd tesserae-device-pi
./scripts/install.sh            # add --bookworm on a Pi 5
```

The script enables SPI / I²C, the connections your panel uses. Enter your Tesserae server URL and a device id when asked. It uses REST, so you don't need a message broker.

The script installs a `tesserae-device-pi` service that starts on boot. Reboot when asked to enable SPI / I²C. The client detects your panel from its HAT; you don't need to choose one.

The client is also on PyPI (`pip install tesserae-device-pi`); the [client README](https://github.com/dmellok/tesserae-device-pi#from-pypi) covers that path.

!!! note "Upgrading from pi-png or pi-bin?"
    Run the same installer. It keeps your settings and pairing, including the device id. It disables the old service; no need to pair again.

## 02 — Pair the panel

In **Settings → Devices**, your Pi appears under **Discovered** within a few seconds. Click **Register** to pair it. You don't need to copy a token.

If your Pi doesn't appear:

- Check `journalctl -u tesserae-device-pi -f` on the Pi for connection errors.
- Confirm the Pi can reach your Tesserae server's URL (`curl http://<server>:8765/healthz` from the Pi).

## 03 — Keep it up to date

Updates appear under **Settings → Firmware**. The Pi installs signed updates and restores the old version if a new one fails to start.

## 04 — Compose a dashboard

In the editor:

1. Open **Dashboards → New**.
2. Add the widgets you want.
3. Bind the page to your Inky in the device picker on the right.
4. Click **Push** to display it.

The editor sizes the layout for your panel:

| Inky Impression | Resolution |
|---|---|
| 13.3" | 1600×1200 |
| 7.3" | 800×480 |
| 5.7" | 600×448 |
| 4" | 600×400 |
| Older 7-colour 4" | 640×400 |

The client also supports pHAT and wHAT panels.

## 05 — Put it on a cadence

Open the dashboard's **Schedules** card:

- Choose **Every N minutes** for regular updates.
- Choose **Daily at a set time** for one image per day.
- Use **Active window** on an **Every N minutes** schedule to set the hours it runs. Leave it blank to run around the clock.

Inky panels use mains power. Choose any refresh interval that allows the panel to finish drawing. A full Spectra 6 refresh takes around 15 to 30 seconds.

## Next steps

- [Pair multiple Pis](../install/devices.md#multiple-panels) to drive several panels from one server.
- [Browse the widget catalog](https://tesserae.ink/catalog/) for one-click community widgets.
- [Add a rotation](../install/devices.md#compose-a-dashboard) to cycle through dashboards across the day.
