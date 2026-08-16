# Quickstart: TRMNL OG / TRMNL X

The flagship TRMNL devices: TRMNL OG (800×480 mono, 7.5") and TRMNL X (1872×1404 mono, 10.3"). Both ship with stock firmware that speaks the TRMNL BYOS protocol, so no flashing is required.

!!! tip "Want to use Tesserae as your sender for TRMNL devices?"
    This is what TRMNL's own [Private API documentation](https://docs.trmnl.com/go/private-api/introduction) explicitly endorses: *"With a device's API key you can request content without a TRMNL device or TRMNL firmware."* Tesserae acts as your BYOS server.

## 01 — Point the device at your Tesserae server

On the device's Wi-Fi captive portal (or via the TRMNL app, if you've used one):

- **API base URL**: `http://<your-server>:8765`

That's the whole flash-equivalent step. The device's stock firmware does the rest.

## 02 — It pairs itself

On its next poll, the device calls Tesserae's `/api/setup` with its MAC and auto-provisions. It appears under **Settings → Devices** within seconds. No token to type.

Tesserae detects the device model from the `Model` HTTP header the firmware sends:

- `Model: TRMNL` → 800×480 OG.
- `Model: x` → 1872×1404 X.
- Unknown models default to 800×480 OG dimensions.

## 03 — Compose a dashboard

In the editor:

1. **Dashboards → New**.
2. The TRMNL OG is 800×480 landscape mono; the X is 1872×1404 landscape mono with 16-level greyscale. Reach for high-contrast type and 1-bit illustration on the OG; the X has enough resolution for serif text and finer rules.
3. Bind the page to the device in the device picker.
4. Hit **Push**.

The device polls on its own cadence (configured in the device's firmware), pulls the rendered frame, paints, and sleeps.

## 04 — Set the refresh

The TRMNL firmware controls its own wake schedule from device-side settings. Tesserae's job is to keep a fresh frame ready when the device asks for one. In the dashboard's **Schedules** card:

- **Smart sync** is the right pick for battery TRMNL devices. Tesserae renders just before the device's next expected wake, so the panel always gets a freshly-rendered frame.

The standard TRMNL OG manages multi-month battery life on its default 60-minute cadence; the X is similar.

## Next steps

- [Bind a second TRMNL device](../install/devices.md#multiple-panels) and target a different dashboard per device.
- [Build a widget](../dev/writing-a-widget.md) tuned for the 1-bit aesthetic.
- [Browse community widgets](https://tesserae.ink/catalog/): many are tuned for the TRMNL OG specifically.
