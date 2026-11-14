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
   The tag form appears on the HA add-on, and on any install with the Home Assistant Core plugin configured.
   Other installs show bridge instructions.

2. Under **Found in Home Assistant**, click **Add** beside your tag.
   Tesserae reads the tag's size and colour palette from Home Assistant and creates the device with them.
   A tag that is already added is not listed.

   To set it up by hand instead, choose the tag from **HA device** and check the fields it fills in.
   **Model** fills in the size and palette for known hardware when Home Assistant cannot be reached.
   If the tag is missing from the list, enter its internal hex device id; the tag's name and serial number won't work.

3. Assign a dashboard and send it. Each changed picture is stored as `media/tesserae/<device-id>.png` and sent to the tag with
`opendisplay.upload_image`.

Add one Tesserae device per tag, up to however many HA can drive.
Each uses one file that is overwritten on updates, so files don't accumulate.
Removing a device removes its file too.
For an uploaded picture, Tesserae asks Home Assistant to delete it; if that fails, the log says so and one small file stays in HA's media folder.

### Did the picture arrive?

A tag never reports back to Tesserae, so its page shows the last delivery at the top of **Display**:

- **Picture delivered**, with the time.
- **The last picture did not reach the tag**, with the reason Home Assistant gave, such as a tag out of Bluetooth range.

A failed picture is tried again after 1 minute, then 5, 15 and 30 minutes.
**Send again** sends the current picture straight away.

If the reason says the upload was refused, the token is not an administrator's.

### Size and rotation

Tesserae reads each tag's pixel size and its own rotation setting from Home Assistant, at add time and every 15 minutes after.
It needs an administrator's token for this; without one it still reads the palette, and assumes the tag is wider than tall.

**Panel → Rotation** on the tag's page is the only rotation setting.
Tesserae turns the picture before it sends it, so 90°, 180° and 270° all work, and **Underscan** applies too.
0° is the tag as it was built, so a tag that is taller than wide shows a portrait dashboard at 0°.
Not sure which way round it is? Send the numbered orientation card and click the number that reads upright.

A tag added before this had a second **Rotation** setting that asked Home Assistant to do the turning.
That tag keeps working as it was, and its page shows the old setting as **Turn in Home Assistant (older setup)**.
Set it to **None** to move the tag onto Panel → Rotation, then send the orientation card to check the result.

### Colour conversion and refresh

Two settings under **Rendering** on the tag's page:

- **Colour conversion** says who reduces the picture to the tag's colours.
  **Home Assistant**, the default, dithers a full-colour picture its own way.
  **Tesserae** reduces it to the tag's palette the way previews are drawn, and Home Assistant sends it unchanged, so the tag shows what the preview showed.
  Check **Panel → Colour palette** matches the tag before choosing Tesserae.
- **Refresh** is **Full** or **Fast**.
  Fast flashes less on panels that support it and can leave faint ghosting.

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

## Colour palette

A tag driven through Home Assistant reports its colours, and Tesserae sets the palette to match.
It follows the tag again if the tag is reconfigured, and otherwise leaves a palette you pick alone.
A tag driven by the bridge does not report it yet, so pick it yourself.

**Panel → Colour palette** on the tag's page has:

- Black and white
- Black, white and red
- Black, white and yellow
- Black, white, red and yellow
- Six colours (Spectra 6), the default
- Seven colours
- 4 greys
- 16 greys

Previews are drawn in the tag's colours.
The picture is sent in full colour unless **Colour conversion** is set to Tesserae.

## Known hardware

The add form's **Model** list fills in the size and palette for these.
Sizes are as the tag reports them with its default rotation.

| Model | Size | Palette |
| --- | --- | --- |
| [OpenDisplay 4.26" Mono Kit](https://www.seeedstudio.com/OpenDisplay-4-26-Mono-Kit-p-7032.html?sensecap_affiliate=YcT0R1h&referring_service=link) (Seeed) | 800 × 480 | Black and white |
| [OpenDisplay 7.3" Color Kit](https://www.seeedstudio.com/OpenDisplay-7-3-Color-Kit-p-7031.html?sensecap_affiliate=YcT0R1h&referring_service=link) (Seeed) | 800 × 480 | Six colours |
| Seeed reTerminal E1001 | 800 × 480 | Black and white |
| Seeed reTerminal E1002 | 800 × 480 | Six colours |
| Seeed reTerminal E1003 | 1872 × 1404 | 16 greys |
| Seeed reTerminal E1004 | 1200 × 1600 | Six colours |
| Solum M3 3.5" (red and yellow) | 184 × 384 | Black, white, red and yellow |
| Solum M3 4.1" (red and yellow) | 400 × 300 | Black, white, red and yellow |
| Waveshare PhotoPainter 7.3" | 800 × 480 | Six colours |

Any other OpenDisplay tag works too: add it from Home Assistant, or type its size.

Both OpenDisplay kits are made by Seeed and have been run with Tesserae through Home Assistant.
Their links are affiliate links: buying through one pays a small commission to the project and gives a discount at checkout.
The code **TESSERAE** gives a discount on them at seeedstudio.com.

## Which path?

- Use HA if you already run it. Its integration handles Bluetooth and colour conversion, with nothing extra to install or run.
  Tesserae does not have to be the add-on.
- Use the bridge without HA, or when you want a dedicated Bluetooth host near your tags.
