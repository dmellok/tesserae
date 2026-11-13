# Quickstart: Visionect signs

Connect a Visionect sign directly to Tesserae using [pyvisionect](https://github.com/schlarpc/pyvisionect). The sign connects to a saved server address and port, then draws the images it receives.

You don't need a Visionect account or a firmware flash. Tesserae replaces Visionect Software Suite (VSS), which requires a paid licence from version 7.7.

!!! note "Tested on one sign so far"
    Tested: Place & Play 32, HardwareNameID 8, firmware 6.17.4168.

    Joan 6, Joan 13, 13" Place & Play and 9.7" boards use the same protocol. They're listed but haven't been tested on hardware.

!!! warning "No authentication"
    Anything that can reach the listening port can pretend to be a sign. Keep it on your LAN. Never forward it from the internet.

    Tesserae rejects:

    - A second connection using a connected sign's UUID from another address. The device page shows the rejection.
    - Oversized or over-compressed images.
    - Connections that don't identify within 20 seconds.

## 01 — Point the sign at Tesserae

Choose **Server port** under **Settings → Devices → Add device → Visionect**, then save.

- The default is **11113**. Keep it if your sign already uses that port.
- This setting applies to all Visionect signs.
- Use a whole number from **1024 to 65535**.
- Don't use Tesserae's web port (usually 8765) or another listener's port.

Tesserae refuses occupied ports before saving. If it can't open the port later, setup and the device page show an error. Tesserae keeps running and retries every half minute.

Saving causes an immediate **rebind**: existing sign connections close and the new port opens. Reconfigure every sign to use it. Tesserae only listens while a Visionect device exists.

An environment override takes priority over the saved setting and needs a Tesserae restart:

```sh
TESSERAE_VISIONECT_PORT=12000 tesserae
```

The setting is stored as `app.visionect_port` in `data/core/settings.json`. Invalid values show an error and disable the listener until corrected. Tesserae never silently switches to another port.

Point the sign at Tesserae using one of these methods:

- In Configurator, open **Settings > Advanced connectivity**. Set **Server IP / Domain** to Tesserae's LAN address and **Server port** to the port shown in setup. Save.
- Or connect the micro-USB console (FTDI serial, 115200 8N1). Run these in order:
    1. `server_tcp_set <tesserae-ip> <server-port>`
    2. `flash_save`
    3. `reboot`

    For port 12000, use `server_tcp_set 192.168.1.50 12000`.

- Or keep the old VSS address and default port. Stop VSS and run Tesserae at that address. The sign reconnects at its next check-in. To switch back, stop Tesserae's listener and start VSS.

The sign must reach the configured port:

| Setup | What to do |
|---|---|
| Docker host networking (Linux compose default) | No port mapping needed. Set the port in Tesserae or export `TESSERAE_VISIONECT_PORT` in compose. |
| Docker bridge networking | Publish `"12000:12000"` for port 12000, or `"11113:11113"` for the default. After changing the port, recreate the container with the matching mapping. `Dockerfile EXPOSE` doesn't publish a port. |
| Home Assistant App | Unsupported here: the App doesn't publish 11113/tcp. Changing Server port won't help. Use Docker or a plain install elsewhere. |
| Bind address | `TESSERAE_BIND_HOST` selects the listening address for both Visionect and the web server. The default is all interfaces. Give the sign an address it can reach. |

## 02 — Add the sign in Tesserae

Choose one method:

- Open **Settings → Devices → Add device → Visionect**. Choose the model. Enter an id and name. For **Sign UUID**, either:
    - Paste the UUID from the sign's label or VSS device list.
    - Leave it blank to accept the first unknown sign within one hour. The hour starts when you add the device or clear its UUID. Later signs appear as discovered devices with their UUID.
- Or let another sign connect after you've added one Visionect device. It appears in the **discovered devices** banner. Click **Register**. Tesserae chooses the model if the screen size identifies it.

Tesserae uses the screen size reported by the sign, regardless of your chosen model:

| Sign | Resolution |
|---|---|
| Joan 6 | 1024×758 |
| Place & Play 32" | 1440×2560 |

Use **Rotation** to turn the image. On the 32", choose 270° if it arrives upside down.

The Place & Play 32" has no buttons or touch input. It has no Controls section, so there's no button map or touch monitor. Home Assistant gets no Input entity.

The device page shows the listener status. It also shows whether the sign has the latest image, is asleep until a given time, or isn't connected.

If VSS is still running, the port is busy.

## 03 — Bind a dashboard

Bind a page, schedule or lineup to the sign.

Tesserae renders at the sign's own size in 16 greys. The device card's **Picture quality** settings control dithering and contrast.

Connected signs draw the image straight away. Sleeping signs receive only the newest image on their next check-in.

A reconnecting sign keeps its current image if it's already up to date, including after a Tesserae restart.

## 04 — Sleep and battery

Each check-in reports battery, signal, temperature and firmware. View these on the device card, in battery history or in Home Assistant.

The device page lets you change the heart beat interval, power saving mode, touch mode and system screens.

Use the device page to update firmware from Visionect's list. Updates run only when you ask. The sign must be connected with at least 30% battery.

Tesserae only offers builds matching the sign's exact hardware and display.

If a flash fails, recover with a USB cable and Visionect's Configurator.

Set quiet hours for the app or device, then turn on **sleep through**. At the start, Tesserae sends the current image and tells the sign to sleep until quiet hours end.

The network can't wake a sleeping sign early. Images sent during sleep appear when it wakes.
