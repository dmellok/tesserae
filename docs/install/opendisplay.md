# OpenDisplay tags

[OpenDisplay](https://opendisplay.org/) tags receive pictures over Bluetooth LE (BLE).
Tesserae draws a full-colour PNG with the `pi_png` renderer, then sends it through one of two routes:

- [Home Assistant](#through-home-assistant), using its OpenDisplay integration.
- A separate `tesserae-opendisplay` [bridge](#with-the-standalone-bridge), a program on a nearby Bluetooth host.

You can use both routes with the same Tesserae server.

## Through Home Assistant

Choose the **OpenDisplay tag (via Home Assistant)** device kind.
Tesserae puts each picture in HA's media folder and calls `opendisplay.upload_image`.
HA converts the colours for the tag and sends the picture over Bluetooth.

Tesserae needs no Bluetooth hardware or extra process.
It works from any install that can reach Home Assistant: the add-on, Docker, a Proxmox guest, or bare metal.

### Requirements

1. Home Assistant with the OpenDisplay integration installed and at least one tag paired.
   This provides the `opendisplay.upload_image` action and a tag to target.
2. The Home Assistant Core plugin enabled in Tesserae, with HA's URL and a long-lived access token.
3. Outside the add-on, that token must belong to an HA administrator.
   HA only accepts media uploads from an administrator.

### How the picture reaches HA

The upload action needs a media source, not a URL, so the picture has to be in HA's media folder.

- **As the Home Assistant add-on**, Tesserae writes it there directly.
  The add-on's `media:rw` permission shares `/media` with HA.
  If that folder is not writable, Tesserae uploads the picture instead.
- **On any other install**, Tesserae uploads it through HA's API, the same way HA's media browser does.
  Nothing needs mounting.
- **With a mount of HA's media folder**, set `app.opendisplay_media_root` to the mount and Tesserae writes there instead of uploading.

### Set it up

1. Open **Settings → Devices → Add device** and set **Transport** to **OpenDisplay**.
   Fill in the tag form.
   The form appears on the HA add-on, and on any install with the Home Assistant Core plugin configured.
   Other installs show bridge instructions.

2. Choose the tag from **HA device**. The dropdown needs the Home Assistant Core plugin configured.

   Tesserae saves its id and fills in **panel size** if the tag reports a resolution.
   Set **Rotation** to match its mounting.
   Not sure which way round it is? On the tag's page, send the numbered orientation card and click the number that reads upright.

   If HA is unreachable or the tag is missing, enter its internal hex device id by hand.
   The tag's name and serial number won't work.

3. Assign a dashboard and send it. Each changed picture is stored as `media/tesserae/<device-id>.png` and sent to the tag with
`opendisplay.upload_image`.

Add one Tesserae device per tag, up to however many HA can drive.
Each uses one file that is overwritten on updates, so files don't accumulate.
Removing a device cleans up its file automatically when Tesserae writes to the folder itself.
An uploaded picture stays in HA, because HA's API cannot delete media; remove it from HA's media browser if you want it gone.

If pictures never arrive and the log says the upload failed with `401`, the token is not an administrator's.

### Rotation

**Panel → Rotation** on the tag's page is the only rotation setting.
Tesserae turns the picture before it sends it, so 90°, 180° and 270° all work, and **Underscan** applies too.

A tag added before this had a second **Rotation** setting that asked Home Assistant to do the turning.
That tag keeps working as it was, and its page shows the old setting as **Turn in Home Assistant (older setup)**.
Set it to **None** to move the tag onto Panel → Rotation, then send the orientation card to check the result.

## Colour palette

An OpenDisplay tag does not tell Tesserae which colours its panel has.
Pick it under **Panel → Colour palette** on the tag's page, for both the Home Assistant and the bridge kind:

- Black and white
- Black, white and red
- Black, white, red and yellow
- Six colours (Spectra 6), the default
- 4 greys
- 16 greys

Previews are then drawn in the tag's colours.
The picture is still sent in full colour, and Home Assistant or the bridge converts it for the panel.
There is no black, white and yellow choice yet; use black, white, red and yellow for such a tag.

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
  Tesserae does not have to be the add-on.
- Use the bridge without HA, or when you want a dedicated Bluetooth host near your tags.
