# Quickstart: Kindle Paperwhite + KOReader

A jailbroken Kindle Paperwhite running [KOReader](https://github.com/koreader/koreader) with the `trmnl-display` plugin. KOReader polls Tesserae's BYOS endpoint and paints whatever frame is scheduled.

!!! warning "Requires jailbreak"
    This path needs a jailbroken Kindle running KOReader. The jailbreak process is well-documented but Amazon does not endorse or support it. Tested on a Kindle Paperwhite 2 (DP75SDI, 758×1024); other generations work too if KOReader supports them.

## 01 — Install KOReader + trmnl-display plugin

If you don't already have KOReader on your Kindle, follow the [KOReader install guide](https://github.com/koreader/koreader/wiki/Installation-on-Kindle-devices). Once installed:

1. SSH or USB-mount your Kindle.
2. Drop the `trmnl-display` plugin into `koreader/plugins/`. See the [KOReader plugin docs](https://github.com/koreader/koreader/wiki/User-Plugins) for the file path on your model.
3. Restart KOReader from the menu.

In KOReader's plugin settings, set the BYOS endpoint:

- **Server URL**: `http://<your-server>:8765`

## 02 — It pairs itself

KOReader polls Tesserae's `/api/setup` and auto-provisions. The Kindle appears under **Settings → Devices** within seconds. No token to type.

## 03 — Compose a dashboard

The Kindle Paperwhite 2 panel is 758×1024 portrait, 16-level greyscale. Tesserae fits the composed PNG to the panel size and dithers server-side.

1. **Dashboards → New**.
2. Build a tall portrait layout. The Paperwhite shines as a bedside dashboard, a hallway notice board, or a calendar surface.
3. Bind the page to the Kindle in the device picker.
4. Hit **Push**.

KOReader paints the frame using its existing display routines. The 16-level greyscale handles dithered images reasonably well.

## 04 — Set the refresh

The Kindle runs on a battery you can't replace easily, so cadence matters. In the dashboard's **Schedules** card:

- **Daily at a set time** is the natural fit: refresh once overnight while it charges.
- **Smart sync** keeps the panel fresh without burning the battery on idle polls.

A well-tuned Kindle Tesserae setup runs for weeks between charges, depending on cadence.

## Next steps

- [Quiet hours](../install/devices.md#per-device-settings) to skip overnight wakes if you're not on a daily cadence.
- [Browse community widgets](https://tesserae.ink/catalog/). The Kindle's tall portrait aspect suits the calendar and news widgets particularly well.
