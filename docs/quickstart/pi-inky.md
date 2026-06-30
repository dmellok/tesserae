# Quickstart: Raspberry Pi + Pimoroni Inky Impression

A Raspberry Pi wired to a Pimoroni Inky Impression: 4", 5.7", 7.3", 13.3", or a pHAT / wHAT. About ten minutes from a fresh Pi OS install to a painted panel.

!!! tip "Fresh SD card?"
    If you're setting up a brand-new Pi specifically for this, the [cloud-init path](pi-inky-cloud-init.md) is zero-touch: flash the SD card with Pi Imager, drop one yaml into the boot partition, edit two values, boot. No SSH required.

!!! tip "Which client?"
    Two clients drive Pimoroni Inky panels: `pi-bin` (fast, panel-native 4-bpp packed buffer) and `pi-png` (PNG path, quantises on the Pi). This guide uses `pi-bin`. Switch to `pi-png` only if your panel uses a colour gamut `pi-bin` doesn't pack (the pHAT and wHAT in particular).

## 01 — Install the Pi client

On the Pi wired to your Inky, as your normal user:

```sh
git clone https://github.com/dmellok/tesserae-device-pi-bin.git
cd tesserae-device-pi-bin
./scripts/install.sh
```

The script enables SPI / I²C, asks for your Tesserae server URL (REST, no broker needed) and panel size, then installs a `tesserae-pi-bin-client` systemd service that runs on boot.

If you want the PNG path instead:

```sh
git clone https://github.com/dmellok/tesserae-device-pi-png.git
cd tesserae-device-pi-png
./scripts/install.sh
```

## 02 — Pair the panel

In **Settings → Devices**, your Pi appears under **Discovered** within a few seconds. Click **Register** to pair it. No tokens to copy.

If your Pi doesn't appear:

- Check `journalctl -u tesserae-pi-bin-client -f` on the Pi for connection errors.
- Confirm the Pi can reach your Tesserae server's URL (`curl http://<server>:8765/api/healthz` from the Pi).

## 03 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. Drop in a weather widget, a calendar, a Spotify card, whatever you want.
3. Bind the page to your Inky in the device picker on the right.
4. Hit **Push**, and the Pi paints the frame.

The composer sizes the layout to your panel automatically: 1600×1200 for the 13.3", 800×480 for the 7.3", 600×448 for the 5.7", 640×400 for the 4".

## 04 — Put it on a cadence

Open the dashboard's **Schedules** card:

- **Every N minutes**: refresh on a fixed cadence.
- **Daily at a set time**: a single frame per day.
- **Mixed schedule**: different cadence per time-of-day.

Pimoroni Inky panels are line-powered, so cadence is your choice. The constraint is the panel's refresh cycle, which is around 15 to 30 seconds for a full Spectra 6 update.

## Next steps

- [Pair multiple Pis](../install/devices.md#multiple-panels) to drive several panels from one server.
- [Browse the widget catalog](../widgets/community.md) for one-click community widgets.
- [Add a rotation](../install/devices.md#next-steps) to cycle through dashboards across the day.
