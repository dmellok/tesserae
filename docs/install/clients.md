# Install a client

A client runs on your screen and draws the pictures Tesserae sends.
Choose one for your hardware below.

For supported ESP32-S3 boards, flash
[`tesserae-device-firmware`](https://github.com/dmellok/tesserae-device-firmware)
from [tesserae.ink/flash](https://tesserae.ink/flash). You don't need build tools.
TRMNL hardware and jailbroken Kindles can also download pictures over HTTP without a Tesserae client.

After installing, [set up the device](devices.md).

| Client | Transport | Default id | Best for |
|---|---|---|---|
| [`tesserae-device-firmware`](https://github.com/dmellok/tesserae-device-firmware) | REST | `esp32` | Seeed reTerminal E1001-E1004, XIAO EE02, XIAO EE03, XIAO 7.5", Waveshare 13.3" Spectra 6, PhotoPainter 7.3" (browser flash at [tesserae.ink/flash](https://tesserae.ink/flash)) |
| `tesserae-device-esp32-bw` | MQTT | `esp32_bw` | Waveshare 4.2" B/W (400×300, 1-bpp) and other small B/W panels |
| [`tesserae-device-pi`](https://github.com/dmellok/tesserae-device-pi) | REST / MQTT / cloud relay | `pi_device` | Any Pimoroni Inky panel on a Raspberry Pi: Impression (4", 5.7", 7.3", 13.3"), wHAT, pHAT |
| `tesserae-device-pico-bin` | REST (MQTT optional) | `pico_bin` | Pimoroni Pico-driven Inky Impression (4-bpp Spectra 6) |
| [`tesserae-device-inkyframe`](https://github.com/dmellok/tesserae-device-inkyframe) | REST | `inky_frame_73` | Pimoroni Inky Frame 7.3" (Spectra 6 on a Pico 2 W, or the earlier 7-colour ACeP); setup from a phone, signed over-the-air updates |
| [`tesserae-device-badger`](https://github.com/dmellok/tesserae-device-badger) | REST | `badger_<mac>` | Pimoroni Badger 2350 (264×176, 4 greys) and Badger 2040 W (296×128, mono), as an app in the badge's own launcher |
| [`tesserae-koreader`](https://github.com/dmellok/tesserae-koreader) | REST | `koreader_client` | Jailbroken Kindles, Kobo, and other e-readers running KOReader; 16-level greyscale, or colour on a Kaleido 3 reader (Kobo Libra Colour, Clara Colour), sleeps between refreshes |
| [TRMNL stock firmware](https://github.com/usetrmnl/trmnl-firmware) or [KOReader plugin](https://github.com/koreader/koreader) | HTTP-pull (BYOS) | `trmnl` | TRMNL devices + KOReader-on-Kindle |

See [Screens & compatibility](../compatibility.md) for which renderer feeds each
client and what's been tested on real hardware.

## tesserae-device-firmware

[:material-github: dmellok/tesserae-device-firmware](https://github.com/dmellok/tesserae-device-firmware)
· pairs with the `esp32_bin` renderer · default id `esp32`

Firmware for the battery-powered ESP32-S3 boards in the table above.
It downloads ready-to-draw `.bin` pictures through Tesserae's v1 REST API.
The board doesn't need to convert the colours.

Install it with the browser-based flasher at
[tesserae.ink/flash](https://tesserae.ink/flash): pick your board and click Install.
You don't need an ESP-IDF toolchain.
On first boot, enter your Wi-Fi details, a device id, and your Tesserae server URL on the setup page.

The flasher installs the latest firmware.

!!! success "This is the maintainer's daily driver"
    The Waveshare 13.3" and reTerminal E-Series paths run in production daily.
    See [Screens & compatibility](../compatibility.md#whats-been-tested-on-real-hardware)
    for the full per-client real-hardware status table.

## tesserae-device-esp32-bw

[:material-github: dmellok/tesserae-device-esp32-bw](https://github.com/dmellok/tesserae-device-esp32-bw)
· pairs with the `esp32_bw_bin` renderer · default id `esp32_bw`

Firmware for the Waveshare 4.2" black-and-white panel (400×300, 1 bit per pixel).
It receives pictures on `tesserae/<device_id>/frame/bin`.

Other black-and-white sizes work if the width is a multiple of 8.
The firmware reports the size, so Tesserae fills it in on the Discovered card.

!!! warning "Untested in the wild as of v0.46.x"
    Wire contract verified by unit tests; no real-hardware paint confirmation
    yet. If you flash it, [open an issue](https://github.com/dmellok/tesserae/issues)
    with the result either way and the table on [Screens & compatibility](../compatibility.md)
    gets updated.

## tesserae-device-pi

[:material-github: dmellok/tesserae-device-pi](https://github.com/dmellok/tesserae-device-pi)
· pairs as `pi_client`, with the `pi_bin` or `pi_png` renderer · default id `pi_device`

A Python program for a Raspberry Pi with any Pimoroni Inky panel.
It detects the panel when pairing. Tesserae then chooses the picture format:

| Panel | Format | Colour conversion |
|---|---|---|
| Spectra 6 Impression, 4", 7.3", 13.3" | `pi_bin` | Tesserae prepares the picture; the Pi draws it directly. This is the fastest path. |
| 7-colour Impression, wHAT, pHAT | `pi_png` | The Pi converts the PNG to the panel's colours. |

REST is the default connection and needs no MQTT broker.
MQTT also works. Use the cloud relay when the panel can't reach your server.

The client supports push updates, offline decks and albums, and front buttons.
It reports battery and sensor readings and shows status screens on the panel.
It serves a settings page on your network, and you can install signed updates from **Settings → Firmware**.

Choose an install method:

- The install script in the client's repo.
- PyPI: `pip install tesserae-device-pi`.
- A fresh SD card: [cloud-init quickstart](../quickstart/pi-inky-cloud-init.md).

The [Raspberry Pi quickstart](../quickstart/pi-inky.md) has the full steps.
Upgrading from an older Pi client keeps your configuration, device id and pairing.
A device paired by an old client keeps its `pi_bin_client` or `pi_png_client` kind and gets the
same frames.

## tesserae-koreader (Kindle, Kobo, any KOReader e-reader)

[:material-github: dmellok/tesserae-koreader](https://github.com/dmellok/tesserae-koreader)
· pairs with the `esp32_gray_bin` renderer (`kaleido_png` on a colour reader) · default id `koreader_client`

A KOReader plugin for showing Tesserae dashboards on an e-reader over REST.
Kindles need a jailbreak to run KOReader. Kobo, PocketBook, and reMarkable do not.

1. Copy `tesserae.koplugin/` from the [latest release](https://github.com/dmellok/tesserae-koreader/releases) into `koreader/plugins/` and restart KOReader.
2. In Tesserae, **Settings → Devices → Add device → REST API → Issue pairing code** to make a claim code.
3. On the reader, **Tools → Tesserae → Pair with a claim code…**, enter your server's address and the code.
4. Assign the new panel a dashboard, then **Tools → Tesserae → Show dashboard**.

The plugin detects the screen size when pairing, so the same device kind works for every model.
An unchanged dashboard returns `304` and skips repainting.

| Reader mode | Picture |
|---|---|
| Greyscale | 4 bits per pixel, 16 greys, decoded on the reader |
| E Ink Kaleido 3 with colour rendering on | PNG with 16 levels per channel, 4096 colours; pairs with the `kaleido3` gamut |

Colour mode applies to the Kobo Libra Colour and Clara Colour.
With colour rendering off, they pair as greyscale readers. Colour readers are not yet confirmed on hardware.

The reader sleeps between refreshes where KOReader supports a hardware wake alarm.
This works on Kobo and Kindles with a recent KOReader build.
Other readers stay awake on a timer, with Wi-Fi off between refreshes.

Set **Refresh interval** on the device card.

Tesserae Cloud speaks the same protocol: enter `https://cloud.tesserae.ink`
as the server and a claim code from **Settings › Panels**.

## tesserae-device-inkyframe (Pimoroni Inky Frame 7.3")

[:material-github: dmellok/tesserae-device-inkyframe](https://github.com/dmellok/tesserae-device-inkyframe)
· pairs with the `pico_bin` renderer · kind `pimoroni_inky_frame_73` (Spectra 6) or `pimoroni_inky_frame_73_acep`

Firmware for the Inky Frame 7.3", maintained by Tesserae from
[partridgeworks/tesserae.inkyframe](https://github.com/partridgeworks/tesserae.inkyframe).
Tesserae prepares pictures in the panel's 4-bit colour format, ready to draw.

1. Flash it from Chrome or Edge at
   [tesserae.ink/flash/inky-frame](https://tesserae.ink/flash/inky-frame): pick
   your Frame, connect it over USB and click **Connect & flash**.

   On a new Frame or one running MicroPython, hold BOOTSEL on the Pico and tap Reset first.
   A Frame already running Tesserae restarts into flashing mode automatically.
   On a Pico 2 W, the flasher also writes the A/B partition table needed for wireless updates.

   To build it yourself, follow the PlatformIO instructions in the repo README.
2. Power the frame and join the `Tesserae-Setup-XXXX` network it opens (password
   `tesserae`). Enter your Wi-Fi and server address on the setup page.
3. The frame announces itself: click **Register** on it in **Settings → Devices**.

The five front buttons send their letters (`a`-`e`).
Signed wireless updates are available in **Settings → Firmware** after the A/B partition table is written over USB.
The web flasher does this. Tested on a Pico 2 W Frame with the Spectra 6 panel.

## tesserae-device-badger (Pimoroni Badger 2350 and 2040 W)

[:material-github: dmellok/tesserae-device-badger](https://github.com/dmellok/tesserae-device-badger)
· Badger 2350 pairs with `esp32_gray2_bin` (4 greys), Badger 2040 W with `esp32_bw_bin` (mono)

An app in the badge's normal menu. Your other apps stay available.
It runs in Badgeware on the 2350 and Badger OS on the 2040 W.

1. Copy the app onto the badge: on the 2350, double-tap RESET and copy the
   `tesserae` folder into `apps/`; on the 2040 W, copy it with Thonny or
   `mpremote`. The repo README has the exact files.
2. Copy `config/tesserae_config.py` beside the badge's Wi-Fi settings and set
   `SERVER_URL` to your server's IP address (these boards can't resolve
   `.local` names).
3. Launch **Tesserae** from the menu and click **Register** on the badge in
   **Settings → Devices**.

The A/B/C/up/down buttons each send their own key.
The config file controls the activity light, wake time after a refresh, refresh speed, and what each button sends.

Tested on the 2350 with Badgeware 1.27.0. The 2040 W is not yet tested on hardware.

## TRMNL / KOReader (HTTP-pull)

[:material-github: usetrmnl/trmnl-firmware](https://github.com/usetrmnl/trmnl-firmware)
or [:material-github: koreader/koreader](https://github.com/koreader/koreader) (`trmnl-display` plugin)
· pairs with the `trmnl_png` renderer · default id `trmnl`

Use TRMNL's stock firmware or KOReader's `trmnl-display` plugin.
For e-readers, prefer the `tesserae-koreader` plugin above.

The device checks `GET /api/display` for the next picture URL and check-in interval.
It uses HTTP and needs no MQTT broker, so it can connect over the internet.

Pairing has two paths:

- TRMNL on Seeed-built hardware: the first `/api/setup` sends its MAC address in the `Id` header. Tesserae creates the device and an access token automatically. Its card appears in **Settings → Devices** within seconds.
- KOReader on a Kindle: `trmnl-display` doesn't send a MAC address. Add a **TRMNL client** under **Settings → Devices → Add device** and paste its 5-character token into the plugin config. The next `/api/setup` exchanges it for a permanent device id and access token.

The device then checks `/api/display` using its token.
It reports errors, battery level, and signal strength through `POST /api/log`.
These appear on the device card.

The `trmnl_png` renderer fits the picture to your screen and converts it to 1-bit black-and-white.
Set contrast and the dither pattern on the device card.
Dither choices are Floyd-Steinberg, Atkinson, Jarvis, Stucki, Bayer 8×8, halftone, crosshatch, or none.

The device reports its screen size on every check-in.
KOReader uses `png-width` / `png-height`; TRMNL firmware uses `Width` / `Height`.

Tesserae saves the first reported size as the native buffer, the screen's drawing area.
It uses this to rotate a landscape dashboard 90° for a portrait reader without stretching it.

Later check-ins don't change that size. A second reader using the same token still gets pictures sized for the first.
If you move a token to another reader, update the size in the device card's panel settings.

Set **Rotation** relative to the reported buffer:

| Rotation | Result |
|---|---|
| 0° | Keeps the dashboard's orientation |
| 90° / 270° | Places a landscape dashboard along a portrait screen's long axis |
| Flipped variants | Add 180° for an upside-down mount |

## Browser-based "client" (no firmware, no native client)

Any registered device has a mirror page that refreshes automatically.
Open it on a screen with a browser:

```
http://<your-tesserae-host>:8765/mirror/<device_id>
```

It shows the device's preview image (`/preview/<device_id>.png`) and reloads on a timer.
Find the `/preview/` and `/mirror/` links on the device card in **Settings → Devices**.

The default refresh interval is the device's `sleep_interval_s` setting.
Override it with `?refresh=N` in seconds, limited to `[5, 86400]`.
For a sideways screen, add `?rotate=90` to the URL.
Other allowed angles are `180` or `270`.

No authentication is required on your local network, as with `/preview/` and `/renders/`.

## After flashing / pairing

On first run, the client announces itself according to its connection type:

- REST, the default: `POST /api/v1/device/discover`. See [REST transport](rest-transport.md).
- MQTT: a heartbeat on `tesserae/<device-id>/status`.
- TRMNL: `/api/setup` then `/api/display`.

[Set up the device](devices.md) to register it and choose its dashboard and orientation.

For multiple panels, flash / pair each with a distinct
`device-id`. Each has its own connection and screen settings.
