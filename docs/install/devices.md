# Set up a device

Once the [server is running](server.md) and pointed at your MQTT broker
(**Settings → Server → MQTT broker**), and you've [flashed a client](clients.md),
you turn that client into a registered **device** so dashboards can target it.

## Register the device

1. **Flash a client** for your hardware (see [Install a client](clients.md)). On first run it publishes a heartbeat on `tesserae/<device-id>/status`.
2. **Settings → Devices → Discovered.** A client that announced itself shows up here with its kind and panel size pre-filled — click **Register** to turn it into a device instance.

    !!! note "No heartbeat yet?"
        Use **Add device** to create one by hand. Check the broker host in
        **Settings → Server**, and that the client's `device_id` and broker
        credentials match.

3. **Calibrate orientation.** Hit **Calibrate** to push a numbered test card to the panel, then tell Tesserae which number landed in the top-left corner; it sets the rotation that makes your dashboard read upright. The **Rotation** dropdown (0 / 90 / 180 / 270°) is there for manual tweaks.
4. **Bind a dashboard.** In the page editor, set **Target device** so a dashboard sizes to that panel and pushes only to its renderers. Leave it on *(any)* to fan out to every renderer at the virtual-panel size.

## Per-device settings

Each registered device carries its own panel block — width, height, orientation,
colour gamut, and underscan (inset content to clear a physical mat/bezel). These
live on the device, not on the renderer, so two panels driven by the same
renderer can differ.

## Multiple panels

Running more than one display? Repeat with a distinct `device-id` per client.
Each gets its own topics, panel size, and orientation, and a dashboard can be
bound to one, several, or all of them. A page bound to several panels of
different sizes renders once per distinct panel and fans each frame out only to
the displays that share it.

## Next steps

- [Browse the widget gallery](../widgets/gallery.md) and start composing
- [Screens & compatibility](../compatibility.md) — panel presets, renderers, and what's tested
