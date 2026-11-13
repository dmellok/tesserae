# Quickstart: Kindle + KOReader

Turn a jailbroken Kindle into a dashboard with [KOReader](https://github.com/koreader/koreader) and the [Tesserae plugin](https://github.com/dmellok/tesserae-koreader). It pairs with a claim code and sleeps between image updates.

!!! warning "Requires jailbreak"
    Amazon doesn't endorse or support jailbreaking, though the process is well documented. Any Kindle supported by KOReader can use this plugin; it detects the screen size. Kobo readers need no jailbreak and can follow the same steps.

## 01 — Install KOReader and the Tesserae plugin

If you don't already have KOReader on your Kindle, follow the [KOReader install guide](https://github.com/koreader/koreader/wiki/Installation-on-Kindle-devices). Once installed:

1. Download `tesserae.koplugin` from the [latest release](https://github.com/dmellok/tesserae-koreader/releases) and unzip it.
2. USB-mount the Kindle and copy the `tesserae.koplugin` folder into `koreader/plugins/`.
3. Restart KOReader from its menu.

If KOReader's TRMNL plugin is installed and set to auto-refresh, switch it off. Two plugins repainting the same screen fight over it.

## 02 — Make a claim code

In Tesserae: **Settings → Devices → Add device → REST API → Issue pairing code**. The code is valid for 10 minutes and works once.

Tesserae Cloud users: **Settings › Panels › New claim code**.

## 03 — Pair the reader

On the Kindle: **Tools → Tesserae → Pair with a claim code…**

- **Server**: your Tesserae address, for example `http://192.168.1.20:8765`, or `https://cloud.tesserae.ink`.
- **Claim code**: the digits from step 02.

The reader appears under **Settings → Devices** within a few seconds, with its actual screen size:

| Kindle | Resolution |
|---|---|
| Paperwhite 2 | 758×1024 |
| Paperwhite 3 | 1072×1448 |

## 04 — Compose a dashboard

Tesserae fits the dashboard to the Kindle's portrait screen and its 16 grey levels. Use **Picture quality** on the device card to adjust dithering (dot patterns for shading) and contrast.

1. Open **Dashboards → New**.
2. Build a portrait layout, such as a bedside calendar.
3. Bind the page to the Kindle in the device picker.
4. On the Kindle, open **Tools → Tesserae → Show dashboard**.

Tap the dashboard for Refresh now, Status and Hide dashboard.

## 05 — Set the refresh

Set **Refresh interval** on the device card to choose the time between downloads. The plugin applies changes on its next check-in.

- With a hardware alarm, the reader sleeps fully between updates. Recent KOReader builds support this; Kobo has it out of the box. Check the plugin's **Status** entry for the active mode.
- Without an alarm, the reader stays awake with Wi-Fi off between updates. It uses more battery but still runs for days.
- An unchanged dashboard needs one small request and no redraw. Short intervals use little power when the page rarely changes.

## Next steps

- [Quiet hours](../install/devices.md#quiet-hours) to skip overnight wakes.
- [Browse community widgets](https://tesserae.ink/catalog/). The tall portrait aspect suits the calendar and news widgets particularly well.
- KOReader's own `trmnl-display` plugin also works against Tesserae's TRMNL endpoint, at 1-bit, if you prefer it: see [Install a client](../install/clients.md#trmnl-koreader-http-pull).
