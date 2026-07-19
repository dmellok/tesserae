# OpenDisplay tags

[OpenDisplay](https://opendisplay.org/) e-paper tags are driven over
Bluetooth LE, not over the network, so Tesserae reaches them one of two
ways. Both render the dashboard the same way (a full-colour PNG via the
`pi_png` renderer); they differ only in who owns the Bluetooth.

- **Via Home Assistant** (recommended if you already run HA). Tesserae
  hands each frame to HA's OpenDisplay integration, which owns the radio.
  No extra process and no BLE hardware on the Tesserae host. See
  [Through Home Assistant](#through-home-assistant).
- **Via the standalone bridge.** A small companion process,
  `tesserae-opendisplay`, polls Tesserae over the network and pushes to
  the tags over BLE itself. Use this when you don't run Home Assistant,
  or you want a dedicated BLE host near a cluster of tags. See
  [With the standalone bridge](#with-the-standalone-bridge).

You can mix both against one Tesserae server: some tags through HA,
others through a bridge.

## Through Home Assistant

This path uses the **OpenDisplay tag (via Home Assistant)** device kind.
Tesserae writes each rendered frame into HA's media folder and calls the
`opendisplay.upload_image` action; HA's OpenDisplay integration then
pushes it to the tag over Bluetooth LE and dithers for the panel.

### Requirements

1. **Home Assistant** with the **OpenDisplay integration** installed and
   at least one tag paired, so `opendisplay.upload_image` is available
   and you have a device to target.
2. **Tesserae running as the Home Assistant add-on.** The add-on is
   granted `media:rw`, so Tesserae and HA share the same `/media`
   folder; that shared folder is how the frame reaches the
   `opendisplay.upload_image` action (it takes a media source, not a
   URL).
3. The **Home Assistant Core plugin** enabled in Tesserae (it makes the
   service call).

### Set it up

1. In Tesserae, **Settings → Devices → Add device** and pick **OpenDisplay
   tag (via Home Assistant)**.
2. Pick your tag from the **HA device** dropdown. It lists the OpenDisplay
   devices Home Assistant knows about, so you don't have to copy any ids;
   Tesserae stores the device id behind the scenes, and if the tag's model
   reports a resolution it fills the **panel size** for you. Set a
   **Rotation** if the tag's mounting needs it. (If Home Assistant isn't
   reachable, or the tag isn't listed, you can type the device id by hand,
   its internal hex id, not the tag's own name or serial.) The dropdown
   needs the **Home Assistant Core plugin** configured.
3. Bind a dashboard to the device and send, the same as any other panel.
   On each changed frame Tesserae writes
   `media/tesserae/<device-id>.png` and calls `opendisplay.upload_image`
   for that tag.

Add one device per tag; each targets its own HA device id, so this
scales to as many tags as HA can drive. The frame uses a stable
per-device filename overwritten in place, so the media folder never
grows, and files for removed devices are swept automatically.

## With the standalone bridge

This path uses the **OpenDisplay tag** device kind (REST-polled) plus the
[`tesserae-opendisplay`](https://pypi.org/project/tesserae-opendisplay/)
bridge. Tesserae renders and serves the frame; the bridge polls it over
the network and pushes to the tags over BLE.

### Requirements

- A machine near your tags with a **Bluetooth LE adapter** (a Pi, a mini
  PC) to run the bridge on. Python 3.11+.
- Network reach from that machine to the Tesserae server.

### Set it up

1. In Tesserae, add an **OpenDisplay tag** device (set the panel size),
   then generate a **pairing code** under **Settings → Devices**.
2. On the BLE host, install and scan for tags:

    ```bash
    pip install tesserae-opendisplay
    tesserae-opendisplay discover
    ```

    `discover` prints a config stub for each tag in range, including the
    panel size it reads off the tag.

3. Create `config.toml` pointing at your server, with one `[[tags]]`
   block per tag (device id, BLE address, panel size, and the pairing
   code). Then run it:

    ```bash
    tesserae-opendisplay --config config.toml
    ```

The bridge registers each tag on first run, caches the token, and from
then on pushes only when a frame changes. One bridge drives many tags;
run several bridges (one per BLE cluster) against the same server to
cover more tags or more rooms. Full options are in the
[bridge README](https://github.com/dmellok/tesserae-opendisplay).

## Which path?

- **Run Home Assistant already?** Use the HA path. There's nothing extra
  to install or keep running, and HA's integration handles the radio and
  dithering.
- **No Home Assistant, or want a dedicated BLE host?** Use the bridge.
