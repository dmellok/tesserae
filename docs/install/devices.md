# Set up a device

After [installing a client](clients.md), register it so Tesserae can send dashboards to it.
The [server must be running](server.md).
For MQTT clients, set the broker under **Settings → Server → MQTT broker**.

## Register an MQTT client (Pi / ESP32)

New devices use REST by default. They announce themselves over HTTP and appear in the same banner.
See [REST transport](rest-transport.md#adding-a-new-rest-device).

1. Flash a client for your hardware (see [Install a client](clients.md)). It sends a heartbeat on `tesserae/<device-id>/status` at first startup.
2. Open **Settings → Devices**. Find the client in the "heard but not registered" banner and click **Register**. Its kind and screen size are already filled in.

    !!! note "No heartbeat yet?"
        Use **Add device** to create one by hand. Check the broker host in
        **Settings → Server**, and that the client's `device_id` and broker
        credentials match.

3. Click the compass button, **Send orientation card**, beside **Rotation**. Tell Tesserae which number appears in the screen's top-left corner. It sets the rotation for you.

   To set it yourself, pick 0, 90, 180 or 270° in **Rotation**. A client can also report its
   rotation when it registers (see [the client protocol](../dev/client-protocol.md)); the dropdown
   still works afterwards.

4. Open the page editor and match the **panel** block to the device's screen size.
   Choose a layout and a widget for each cell. See [Compose a dashboard](#compose-a-dashboard) for the steps.

   Send the page to the device.

## Register a TRMNL / KOReader client (HTTP-pull)

For a TRMNL device on Seeed-built hardware, enter Tesserae's URL in its Wi-Fi setup page.
The device registers automatically and appears in **Settings → Devices** within seconds.
You can rename it on its card.

KOReader's `trmnl-display` plugin needs a short token instead:

1. Open **Settings → Devices → Add device** and choose **TRMNL client**. Tesserae shows a five-character access token on the card.
2. Type the token into the KOReader plugin config. It calls `/api/setup` to exchange it for a permanent device id and token. It then checks `/api/display` at your configured interval.
3. Set the orientation and dashboard as above. The device appears in Devices as soon as setup finishes.

## Per-device settings

Each device has its own dashboard width and height, orientation, saturation, and contrast.
Other screen settings are:

| Setting | Meaning |
|---|---|
| Colour gamut | The colours the screen can show |
| Underscan | An inset to keep content clear of the frame or bezel |
| Dither algorithm | The dot pattern used to reproduce colours |

Width and height describe the dashboard's orientation.
A 1200×1920 screen can use a 1920×1200 dashboard; Tesserae rotates the picture to fit.
Two screens using the same renderer can have different settings.

For TRMNL and 1-bit screens, choose Floyd-Steinberg, Atkinson, Jarvis-Judice-Ninke, Stucki, Bayer 8×8, halftone, crosshatch, or none.

### Update delivery

**Update delivery** controls updates to the page already on screen.
*Auto* is the default. On supported firmware, small changes repaint only the changed area without a full flash.

Large changes need a full repaint at the next check-in.
So do devices that were asleep when the small update was prepared.

Choose *Always full refresh* if small pushed updates leave missing content or traces of the old picture.
Every change then repaints the whole screen. This is slower and causes a full flash.

Report ghosting on timed refreshes or after touch to the firmware project.
Those have a different cause; this setting won't fix them.

### Quiet hours

**Quiet hours** stop automated updates from schedules, lineups, and webhooks during a daily window.

Set the window in **Settings → Server → Quiet hours**. Each device can override it on its card.
Manual sends still work, including Push-now and physical buttons.

The window has three parts:

- **Start** and **End**. An End earlier than Start crosses midnight, so 22:00 → 07:00
  is overnight.
- **Window applies on**: the weekdays the Start/End window is in force. Each
  day is judged by its own clock: on a ticked day an overnight window covers
  that morning up to End and that evening from Start.
- **Quiet all day**: weekdays that are quiet from midnight to midnight
  whatever the window says.

The window follows the server's time zone and daylight-saving changes.
If the End falls in a skipped or repeated hour, quiet hours end at the first real moment after it.
The End minute itself is still quiet.

For an office closed at night and on weekends, set Start to 20:00 and End to 08:00.
Select Monday to Friday under *Window applies on* and Saturday and Sunday under *Quiet all day*.

This keeps the office quiet from Friday 20:00 through Monday 08:00.

**Sleep through quiet hours** keeps battery-powered screens asleep until the window ends.
By default, they keep waking at their normal interval even when there are no updates.
The trade-off: with this on, a manual push made during the window isn't seen until the window ends.

Always-on screens are unaffected. Any client that follows `next_poll_s` supports this without extra device setup.

**Touch off during quiet hours** saves battery by putting the touch controller to sleep too.
The switch appears only on touchscreen devices and needs firmware v1.40.0 or later.
Older firmware leaves the touch controller running.

- Touch turns off when the screen next wakes during quiet hours.
- Taps do nothing, but physical buttons still wake the screen.
- The first wake after quiet hours turns touch back on.
- Your saved touch setting stays unchanged.

This works with or without **Sleep through quiet hours**. Always-on screens are unaffected.

Leave it off if you use the screen at night, such as for a bedside light switch.
For lower battery use while keeping touch wake available, choose double tap or swipe under **Touch wake**.

### Device logs

A device with log-upload support has a **Logs** section on its page.
Choose **1, 5 or 20 wakes** to collect logs from its next check-ins. **Stop** ends collection early.

Each log includes the end of the previous wake and the current wake up to the upload.
That includes the previous repaint, which happens with Wi-Fi off.
You can view or download logs without a USB cable.

The upload list shows each log's time, size, and line count.

Tesserae keeps the newest 20, up to 1 MB per device. Deleting the device deletes its logs.

Failed repaints and abnormal resets trigger one log upload on the same wake.
Abnormal resets include:

- Brownouts, when power drops too low.
- Panics, when the firmware crashes.
- Watchdog resets, when the device stops responding.

The failure appears in Settings › Events and as the last detected failure under Logs.
To stop automatic uploads, turn off **Collect a device's log after a failed paint** in **Settings → Server**.
The Events entry still appears.

Logs contain timings, the Wi-Fi network name, signal strength, and IP address.

The firmware removes URL query strings and tokens before saving log lines. It never logs passwords.

## Compose a dashboard

1. Open **Top nav → Dashboards → New dashboard**, or choose an existing dashboard.
   Set its panel block to the target screen size, usually the device you're connecting.
2. Choose one of the ten layout cards: one cell, two columns, two rows, three rows, 2×2 grid,
   hero top / bottom / left / right, or hero sandwich.

   For a different layout, expand "Custom layout" below.
   Snap rows and columns to the grid, then drag corners to adjust them.

3. Choose a widget for each cell from the gallery sidebar.
   Each cell has a "Choose widget" picker on the left and that widget's options on the right.
   Options include place labels, units, and the `variant` style dropdown.

4. Use each cell's zoom slider to make its content larger or smaller without resizing the cell.
5. Add one or more devices to the page's **devices** block.
   Tesserae draws a picture for each screen size and sends it to the matching devices.
6. Click Send for an immediate update. For scheduled updates, use **Lineups** in the top navigation.
   To cycle through dashboards, create a rotation lineup there.

### Dashboard refresh settings

In **Dashboards**, expand a dashboard and open **Refresh settings** below its status tiles.

| Setting | Controls |
|---|---|
| Content updates | How often Tesserae updates the dashboard content, in minutes |
| Device wake-up interval | How often the screen checks for new content, in seconds |

A dashboard in a Lineup uses the Lineup's background refresh interval.
Choose a preset to save immediately.
For **custom**, enter the interval and press **Set**.

Choose **device's own interval** to let the device decide when to wake.

## Multiple panels

For multiple displays, repeat with a distinct `device-id` per client.
Each has its own connection and screen settings.
A dashboard can target any number of devices. Tesserae draws it once per screen size and sends it to matching devices.

## Next steps

- [Browse the widget gallery](../widgets/gallery.md).
- [Check screen compatibility](../compatibility.md).
- [Set up physical buttons](buttons.md).
- [Connect Home Assistant](home-assistant.md) or run Tesserae as an HA App.
