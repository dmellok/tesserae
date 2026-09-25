# Quickstart: Kindle + KOReader

A jailbroken Kindle running [KOReader](https://github.com/koreader/koreader) with the [Tesserae plugin](https://github.com/dmellok/tesserae-koreader). The reader pairs with a claim code, fetches 16-level greyscale frames on the interval you set, and sleeps in between.

!!! warning "Requires jailbreak"
    This path needs a jailbroken Kindle running KOReader. The jailbreak process is well-documented but Amazon does not endorse or support it. The steps below apply to any Kindle KOReader supports; the plugin reports the real screen size when it pairs. Kobo readers run KOReader without a jailbreak and follow the same steps from 01 onward.

## 01 — Install KOReader and the Tesserae plugin

If you don't already have KOReader on your Kindle, follow the [KOReader install guide](https://github.com/koreader/koreader/wiki/Installation-on-Kindle-devices). Once installed:

1. Download `tesserae.koplugin` from the [latest release](https://github.com/dmellok/tesserae-koreader/releases) and unzip it.
2. USB-mount the Kindle and copy the `tesserae.koplugin` folder into `koreader/plugins/`.
3. Restart KOReader from its menu.

If KOReader's TRMNL plugin is installed and set to auto-refresh, switch it off. Two plugins repainting the same screen fight over it.

## 02 — Make a claim code

In Tesserae: **Settings → Devices → Add device → Pair with a code**. The code is valid for a short while and works once.

Tesserae Cloud users: **Settings › Panels › New claim code**.

## 03 — Pair the reader

On the Kindle: **Tools → Tesserae → Pair with a claim code…**

- **Server**: your Tesserae address, for example `http://192.168.1.20:8765`, or `https://cloud.tesserae.ink`.
- **Claim code**: the digits from step 02.

Within a few seconds the reader appears under **Settings → Devices** with its real resolution, for example 758×1024 for a Paperwhite 2 or 1072×1448 for a Paperwhite 3.

## 04 — Compose a dashboard

The Kindle is a tall portrait panel with 16 grey levels. Tesserae fits the composed page to the screen and dithers server-side; on the device card, **Picture quality** sets the dither and contrast.

1. **Dashboards → New**.
2. Build a tall portrait layout. A Kindle shines as a bedside dashboard, a hallway notice board, or a calendar surface.
3. Bind the page to the Kindle in the device picker.
4. On the Kindle: **Tools → Tesserae → Show dashboard**.

Tap the dashboard at any time for Refresh now, Status, and Hide dashboard.

## 05 — Set the refresh

The reader runs on a battery, so cadence matters. On the device card, **Refresh interval** sets how long it sleeps between fetches; the plugin picks the new value up on its next check-in.

- On a KOReader build with the hardware alarm (recent builds, Kobo out of the box) the Kindle suspends between refreshes and wakes for each one. The plugin's **Status** entry says which mode is in force.
- Where the alarm is not available the reader stays awake with Wi-Fi off in between, which costs more but still runs for days.
- A dashboard that has not changed costs one small request and no repaint, so a shorter interval on a slow-changing page is cheap.

## Next steps

- [Quiet hours](../install/devices.md#per-device-settings) to skip overnight wakes.
- [Browse community widgets](https://tesserae.ink/catalog/). The tall portrait aspect suits the calendar and news widgets particularly well.
- KOReader's own `trmnl-display` plugin also works against Tesserae's TRMNL endpoint, at 1-bit, if you prefer it: see [Install a client](../install/clients.md#trmnl-koreader-http-pull).
