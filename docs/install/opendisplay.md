# OpenDisplay tags

[OpenDisplay](https://opendisplay.org/) tags receive pictures over Bluetooth LE (BLE).
Tesserae draws a full-colour PNG with the `pi_png` renderer, then sends it through one of two routes:

- [Home Assistant](#through-home-assistant), using its OpenDisplay integration.
- A separate `tesserae-opendisplay` [bridge](#with-the-standalone-bridge), a program on a nearby Bluetooth host.

You can use both routes with the same Tesserae server.

## Through Home Assistant

Choose the **OpenDisplay tag (via Home Assistant)** device kind.
Tesserae saves each picture in HA's media folder and calls `opendisplay.upload_image`.
HA converts the colours for the tag and sends the picture over Bluetooth.

Tesserae needs no Bluetooth hardware or extra process.

### Requirements

1. Home Assistant with the OpenDisplay integration installed and at least one tag paired.
   This provides the `opendisplay.upload_image` action and a tag to target.
2. Tesserae running as the Home Assistant add-on.
   The add-on's `media:rw` permission shares `/media` with HA.
   The upload action needs a media source, not a URL.
3. The Home Assistant Core plugin enabled in Tesserae.

### Set it up

1. Open **Settings → Devices → Add device** and set **Transport** to **OpenDisplay**.
   Fill in the tag form.
   The HA add-on shows this form automatically; other installs show bridge instructions.

2. Choose the tag from **HA device**. The dropdown needs the Home Assistant Core plugin configured.

   Tesserae saves its id and fills in **panel size** if the tag reports a resolution.
   Set **Rotation** to match its mounting.

   If HA is unreachable or the tag is missing, enter its internal hex device id by hand.
   The tag's name and serial number won't work.

3. Assign a dashboard and send it. Each changed picture is written to `media/tesserae/<device-id>.png` and sent to the tag with
`opendisplay.upload_image`.

Add one Tesserae device per tag, up to however many HA can drive.
Each uses one file that is overwritten on updates, so files don't accumulate.
Removing a device cleans up its file automatically.

### Telemetry

The device card reads battery, signal strength, temperature, and firmware version from HA.
Battery-drain history is included.

Tesserae checks every 15 minutes by default.
Change this with `app.opendisplay_telemetry_interval_s`.
It uses the Home Assistant Core plugin and only shows values the tag reports.

### Firmware and SDK compatibility

Newer tag firmware may be incompatible with HA's `py-opendisplay` SDK, the library that reads tag settings.
HA may then refuse to add the tag or send pictures.
For example, an older parser may reject a `DATA_EXTENDED` / `0x2c` configuration packet.

If either fails, check that the integration and SDK support your tag's firmware.
The beta / HACS build is often newer than the version bundled with Home Assistant Core.

## With the standalone bridge

Choose the **OpenDisplay tag** device kind and install the
[`tesserae-opendisplay`](https://pypi.org/project/tesserae-opendisplay/) bridge.
It checks Tesserae for pictures over the network and sends them to nearby tags over BLE.

### Requirements

Run the bridge on a nearby machine with a Bluetooth LE adapter and Python 3.11+.
A Pi or mini PC works. That machine must be able to reach Tesserae over the network.

### Set it up

1. Add an **OpenDisplay tag** device in Tesserae and set its panel size.
   Generate a **pairing code** under **Settings → Devices**.

2. Install the bridge and scan for tags:

    ```bash
    pip install tesserae-opendisplay
    tesserae-opendisplay discover
    ```

    `discover` prints a starting configuration for each nearby tag, including its screen size.

3. Create `config.toml` pointing at Tesserae.
   Add one `[[tags]]` block per tag with its device id, BLE address, panel size, and pairing code.

   Run the bridge:

    ```bash
    tesserae-opendisplay --config config.toml
    ```

On first run, the bridge registers each tag and saves its token.
It sends updates only when pictures change.

One bridge can drive many tags. Use several bridges, one per Bluetooth cluster, to cover more rooms.
Full options are in the [bridge README](https://github.com/dmellok/tesserae-opendisplay).

## Which path?

- Use HA if you already run it. Its integration handles Bluetooth and colour conversion, with nothing extra to install or run.
- Use the bridge without HA, or when you want a dedicated Bluetooth host near your tags.
