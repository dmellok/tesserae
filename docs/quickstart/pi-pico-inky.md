# Quickstart: Pi Pico Plus 2 W + Pimoroni Inky 13.3"

Run an Inky Impression 13.3" from a Pimoroni Pico Plus 2 W (RP2350). It sleeps between updates and lasts months on a small LiPo battery.

!!! note "Different from the Pi path"
    A Pico is a microcontroller running C firmware. The Pi quickstart is for a Linux Raspberry Pi running Python. Both connect to the same Tesserae server.

## 01 — Flash the Pico

Hold BOOTSEL while connecting the Pico Plus 2 W to your computer over USB. It appears as a drive named `RP2350`.

Download the latest UF2 firmware file from [tesserae-device-pico-bin releases](https://github.com/dmellok/tesserae-device-pico-bin/releases). Drag it onto the drive. The Pico restarts and unmounts within a second.

On first boot, join `Tesserae-Setup-XXXX` from your phone or laptop. Its Wi-Fi setup page asks for:

- Tesserae server URL: `http://<your-server>:8765`
- Your home Wi-Fi name and password.

Save and the Pico restarts onto your network.

## 02 — It pairs itself

The Pico contacts `/api/v1/device/discover`. Within a few seconds, it appears under **Settings → Devices → Discovered**. Click **Register**.

## 03 — Compose a dashboard

In the editor:

1. Open **Dashboards → New**.
2. Build a 1600×1200 landscape layout for the 13.3" Spectra 6 panel's six colours.
3. Bind the page to the Pico in the device picker on the right.
4. Click **Push**.

The Pico downloads and draws the image when it wakes, then sleeps again.

## 04 — Set the refresh

Open the dashboard's **Schedules** card and pick:

- Choose **Every N minutes** for regular updates.
- Choose **Daily at a set time** for one update per day.
- Turn on **Smart sync** on an **Every N minutes** schedule. Tesserae draws the image just before each wake, saving battery and idle work.

## Next steps

- [Tune the wake interval](../install/devices.md#dashboard-refresh-settings) for the battery vs freshness tradeoff you want.
- [Quiet hours](../install/devices.md#quiet-hours) to skip renders overnight.
- [Build your own widget](../dev/writing-a-widget.md) for the panel.
