# Quickstart: TRMNL OG / TRMNL X

Connect a TRMNL to Tesserae using its stock firmware. You don't need to flash it. BYOS means "bring your own server"; here, that server is Tesserae.

| Model | Screen | Resolution (landscape) | Colours |
|---|---|---|---|
| TRMNL OG | 7.5" | 800×480 | Black and white |
| TRMNL X | 10.3" | 1872×1404 | 16 greys |

!!! tip "Want to use Tesserae as your sender for TRMNL devices?"
    TRMNL supports this setup. See its [Private API documentation](https://docs.trmnl.com/go/private-api/introduction).

## 01 — Point the device at your Tesserae server

Open the device's Wi-Fi setup page, or use the TRMNL app if you've used it before. Set:

- **API base URL**: `http://<your-server>:8765`

## 02 — It pairs itself

On its next check-in, the device calls `/api/setup` with its MAC address and registers automatically. It appears under **Settings → Devices** within seconds. You don't need to enter a token.

Tesserae reads the `Model` header the firmware sends. A TRMNL X (`Model: x`) gets 1872×1404 and a 16-grey PNG; you can switch it to the 1-bit renderer on its device card. Any other model gets the OG size, 800×480.

## 03 — Compose a dashboard

In the editor:

1. Open **Dashboards → New**.
2. Design for your panel:
    - OG: high-contrast text and 1-bit illustrations. Firmware 1.6.0 or later supports 4 greys with the `trmnl_png_gray4` renderer.
    - X: landscape, 16 greys. Its resolution supports serif text and fine lines.
3. Bind the page to the device in the device picker.
4. Click **Push**.

The firmware controls how often the device wakes. It downloads and draws the image, then sleeps.

## 04 — Set the refresh

In the dashboard's **Schedules** card:

- Turn on **Smart sync** on an **Every N minutes** schedule. Tesserae draws each image just before the device's expected wake.

The OG's default wake interval is 60 minutes, for several months of battery life. The X is similar.

## Next steps

- [Bind a second TRMNL device](../install/devices.md#multiple-panels) and target a different dashboard per device.
- [Build a widget](../dev/writing-a-widget.md) tuned for the 1-bit aesthetic.
- [Browse community widgets](https://tesserae.ink/catalog/): many are tuned for the TRMNL OG specifically.
