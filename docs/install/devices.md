# Set up a device

Once the [server is running](server.md) and (for MQTT clients) pointed at your
broker (**Settings → Server → MQTT broker**), and you've
[flashed or paired a client](clients.md), you turn that client into a registered
**device** so dashboards can target it.

## Register an MQTT client (Pi / ESP32)

1. **Flash a client** for your hardware (see [Install a client](clients.md)). On first run it publishes a heartbeat on `tesserae/<device-id>/status`.
2. **Settings → Devices → Discovered.** A client that announced itself shows up here with its kind and panel size pre-filled, click **Register** to turn it into a device instance.

    !!! note "No heartbeat yet?"
        Use **Add device** to create one by hand. Check the broker host in
        **Settings → Server**, and that the client's `device_id` and broker
        credentials match.

3. **Calibrate orientation.** Hit **Calibrate** to push a numbered test card to the panel, then tell Tesserae which number landed in the top-left corner; it sets the rotation that makes your dashboard read upright. The **Rotation** dropdown (0 / 90 / 180 / 270°) is there for manual tweaks. A client can also declare its rotation when it registers, which additionally tells Tesserae that the dims it reported are its framebuffer rather than the dashboard canvas (see [the client protocol](../dev/client-protocol.md)); the dropdown edits that afterwards either way.
4. **Bind a dashboard.** Open the page editor and set the page's **panel** block to match the device's panel size, then pick a layout preset and assign a widget per cell (see [Compose a dashboard](#compose-a-dashboard) below). Send the page to push it to the device.

## Register a TRMNL / KOReader client (HTTP-pull)

Two paths, depending on the client:

**TRMNL device (auto-provision):** Just point the device at Tesserae's URL via its captive-portal Wi-Fi setup. The TRMNL firmware (running on Seeed-built hardware) calls `/api/setup` with its MAC in the `Id` header on first boot; Tesserae auto-creates a device record, mints an access token, and the device starts polling `/api/display` immediately. The new device appears in **Settings → Devices** within seconds, no admin click required. Auto-provision was wired up in 0.44.1; the device card lets you rename it after the fact.

**KOReader on a Kindle (token-typed):** KOReader's `trmnl-display` plugin doesn't send a MAC, so you provision via a short token instead.

1. **Settings → Devices → Add device → TRMNL.** Tesserae generates a five-character access token and prints it on the card.
2. **Type the token into the KOReader plugin config.** It calls `/api/setup`, exchanges the token for a permanent device-id + access token, and starts polling `/api/display` on the cadence you configure.
3. **Calibrate + bind a dashboard** exactly as above, the device appears in the Devices list the moment it completes setup.

## Per-device settings

Each registered device carries its own panel block: logical width and height,
orientation, colour gamut, and underscan (inset content to clear a physical
mat/bezel) - plus picture-quality controls (dither algorithm, saturation,
contrast) that tune the output for the specific panel. The width and height are
**logical**: the composition orientation dashboards are rendered at, not the
physical size the device reports. A panel that reports 1200×1920 can carry a
1920×1200 logical size, and the renderer turns the finished dashboard back onto
the device's own framebuffer. These live on the device, not on the
renderer, so two panels driven by the same renderer can differ. For TRMNL /
1-bit panels the dither dropdown carries the full set: Floyd-Steinberg,
Atkinson, Jarvis-Judice-Ninke, Stucki, Bayer 8×8, halftone, crosshatch, or none.

### Update delivery

**Update delivery** on the device card decides how a change reaches a panel
that is already showing the same page. *Auto*, the default, sends a small
change (a clock tick, a toggled switch) as a partial-refresh patch on firmware
that supports it, so the panel repaints only the changed area with no full
flash. A change too large to patch, or a device that was asleep when the patch
was staged, gets a whole frame and a full repaint on its next poll instead.

*Always full refresh* turns the patch path off for that device: every change
mints a new frame and repaints the whole panel. It is slower and flashes, but
it is the setting to reach for when small pushed updates look incomplete or
ghosted. Ghosting that also shows up on timed refreshes, or that follows a
touch, has a different cause: report it against the firmware rather than
changing this setting.

The setting is per device, so one panel can be pinned to full refresh while
the rest stay on Auto.

## Compose a dashboard

The page editor models a dashboard as **one page → one layout preset → one
widget per cell**.

1. **Top nav → Dashboards → New dashboard** (or pick an existing one). Set its panel
   block to the size the dashboard targets, usually the size of the device
   you're binding to.
2. **Pick a layout preset.** Ten built-in presets are exposed as cards: one
   cell, two columns, two rows, three rows, 2×2 grid, hero top / bottom /
   left / right, and hero sandwich. Click the card you want. The "Custom
   layout" disclosure below lets you snap rows / columns to a grid and
   drag corners if no preset fits.
3. **Assign a widget per cell.** Each cell shows a "Choose widget" picker
   on the left and the widget's per-cell options on the right (place
   labels, units, the `variant` style dropdown, etc.). Pick from the
   gallery sidebar; the per-cell form rewrites itself from the widget's
   `cell_options`.
4. **Tweak the cell.** A zoom slider per cell scales widget content up
   or down without changing the cell's footprint on the panel -
   useful for a "make this number bigger" pass without resizing.
5. **Bind devices.** Drop devices into the page's **devices** block -
   one or many. A page bound to several differently-sized panels
   renders once per distinct panel size and fans each frame out.
6. **Send.** Hit Send to push immediately, or let the scheduler do it
   on the cadence set in the top nav's **Schedules** entry. For
   cycling between several dashboards on one device, use
   **Rotations** instead (also in the top nav), shipped in 0.45.0.

## Multiple panels

Running more than one display? Repeat with a distinct `device-id` per client.
Each gets its own topics or HTTP token, panel size, and orientation, and a
dashboard can be bound to one, several, or all of them. A page bound to several
panels of different sizes renders once per distinct panel and fans each frame
out only to the displays that share it.

## Next steps

- [Browse the widget gallery](../widgets/gallery.md) and start composing
- [Screens & compatibility](../compatibility.md), panel presets, renderers, and what's tested
- [Physical buttons](buttons.md), bind hardware buttons on the device (Seeed reTerminal front buttons, Inky Impression back buttons, etc.) to rotate through dashboards, jump straight to one, refresh, or fire a webhook
- [Home Assistant integration](home-assistant.md), surface every device as HA entities via MQTT discovery, or run Tesserae as an HA App
