# Physical buttons

Use your device's buttons to change dashboards or run an action.
Set each button's action in the device's **Controls** panel.

Buttons work on the Seeed reTerminal E-Series, Waveshare and XIAO ePaper boards (ESP32
firmware), and on any other device whose firmware supports the button protocol.
For a Raspberry Pi with an Inky Impression,
[tesserae-device-pi](https://github.com/dmellok/tesserae-device-pi) sends buttons A to D over REST or the cloud relay.
MQTT does not carry button presses.

## What buttons can do

The default map, applied to any device that doesn't override it, is:

| Button | Action | Effect |
|---|---|---|
| `left` | `rotate_prev` | Move back one step in the device's rotation |
| `right` | `rotate_next` | Advance one step in the device's rotation |
| `refresh` | `refresh` | Re-render + re-push the current step |

Configure other button names or extra buttons on the device.

### Inky Impression: binding each button by its letter

The Pi client sends both a letter and an action name for each press:

| Button | Default action name |
|---|---|
| A | `left` |
| B | `refresh` |
| C | `right` |

Letters run from `a` to `d`. Tesserae checks the letter first, then the action name.
Without a custom map, the defaults above apply.

To set each button separately, use its letter:

```json
{
  "a": "page:Calendar",
  "b": "page:Weather",
  "c": "page:Home",
  "d": "page:Family"
}
```

Button D does nothing until you assign `d` an action.
A deck on screen uses its own button links first.

Holding refresh (B) always keeps these functions, regardless of your map:

- 3 to 19 seconds unlocks the Pi's settings page.
- 20 seconds factory-resets the Pi.

## Configuring buttons per device

Open the device from **Devices** and go to its **Controls** panel.
Enter your button settings as JSON in **Button map**:

```json
{
  "a": "page:Morning briefing",
  "b": "page:b2f1e07a9c55",
  "c": "rotate_next",
  "d": "refresh"
}
```

`page:` accepts a dashboard name or its 12-character id from the dashboard's details page.
Names ignore case. Use an id to keep the link working after a rename.
Tesserae refuses a name shared by two dashboards.

Expand **Dashboard ids** to find each dashboard's name and `page:` value.
Expand **Effective map** to see your settings combined with the defaults.
An empty **Button map** restores the defaults.

Use the device page's **Save** bar.
Tesserae rejects invalid JSON, unknown actions, and invalid action arguments.

## Actions

Each button needs an action name, such as `rotate_next`, or an action with an argument in `<action>:<arg>` format.
Examples are `page:Kitchen` and `webhook:https://…`.

| Action | Argument | What it does |
|---|---|---|
| `rotate_prev` | none | Advance the device's rotation one step backwards. Wraps at the start. |
| `rotate_next` | none | Advance the device's rotation one step forwards. Wraps at the end. |
| `fetch_latest` | none | Download and repaint the latest frame already rendered for this device. Does not re-render, push, move the rotation, or set a manual override. |
| `refresh` | none | Force a fresh render + push of the current step. Useful when a widget's upstream data changed but the composition hash is stable. |
| `step:<index>` | rotation step index (0-based) | Jump straight to that step in the rotation. |
| `page:<page>` | dashboard name or id | Push a specific dashboard to this device. Doesn't touch rotation state, so a later timer wake resumes the scheduled step. |
| `webhook:<url>` | absolute `http(s)://` URL | Fire a POST to that URL with `{device_id, button, button_event_id, action_spec, timestamp, rotation_id, step_index, step_page_id}` in the body. Fire-and-forget, 3s timeout. |
| `webhook_refresh:<url>` | absolute `http(s)://` URL | Same POST as `webhook:`, then re-render and repaint the panel a few seconds later. For buttons whose webhook changes the state the dashboard reads. |

Actions added by plugins appear in the admin help text automatically.

### `webhook` vs `webhook_refresh`

Use `webhook:` to send an HTTP POST request without updating the screen.
Use `webhook_refresh:` when the request changes something the dashboard shows, such as a room booking.
Plain `webhook:` leaves the old picture on screen until the next wake.

Tesserae doesn't wait for the receiving server to confirm the change.
It waits 5 s before repainting to give that server time to finish.
For a slower server, increase `button_webhook_refresh_delay_s` in app settings.

Several presses close together cause one repaint after the last press.

If you control the receiving server, have it call
[`POST /api/v1/push`](server.md#webhook-push) after saving the change.
That updates the screen when the work finishes. Use `webhook_refresh:` for servers you can't change.

Touch panels with firmware v1.21.0 or later can repaint while still awake after a tap.
Small changes update part of the screen; larger changes need a whole new picture.

Set **Touch linger** long enough for the server's work and the repaint.
A four-second booking needs more than the default 5 s wait to fit inside a 10 s window.
If the panel sleeps first, it updates at its next scheduled wake.

## Audible feedback

Seeed reTerminal E series devices can beep as soon as you press a button or tap the screen.
Open **Settings → Devices** and turn on **Beep on interaction**.
Choose the sound with **Beep tone** and **Beep volume**.

The device beeps before contacting the server. Beeps are off by default and need firmware v1.22.0 or later.

## Manual override sticks until the next anchor

A button that rotates, refreshes, or uses `step:` puts the schedule on hold.

The hold lasts until the rotation's next daily anchor, midnight.
It uses the time zone in **Settings → Server → Location & time**, or the host's time zone if unset.

A touch that rotates the page holds it for that step's dwell time, then the lineup continues.
Set `settings.app.button_hold_seconds` to use a fixed number of seconds for both buttons and touch.

`page:` shortcuts don't hold the schedule. It resumes at the next timer wake.
During a hold, the Lineups card shows **held** and the timer skips that display.

Click **Play** on the lineup to release the hold.

## Duplicate presses are dropped

Tesserae ignores a `button_event_id` equal to the last processed id, so a network retry doesn't run the action twice.
Ids increase with each press. A lower id means the counter restarted after a power cycle; the action still runs.

For firmware without a counter, repeat presses of the same button within 3 seconds are ignored.
Change that window with `settings.app.button_debounce_s`.

## Buttons on remote (relay) panels

Remote panels send presses through their relay status reports.
Your home instance picks them up within about 30 seconds.
It uploads the new picture for the panel to fetch before sleeping.

Follow-up presses are checked faster while the panel stays awake.

Expect a delay of seconds. Presses older than a few minutes are dropped, for example after your home instance was offline.
See firmware requirements in the
[Cloud relay contract](../relay/contract.md).

## Seeing button events on the History page

Each press appears on `/history` with source `button`.
The target column shows the device's friendly name. The detail line describes the action:

```
[Button] Kitchen wall panel · button right → rotate_next pushed Afternoon calendar
```

| Status | Meaning |
|---|---|
| `dispatched` | A page was pushed |
| `deduped` | A retry was ignored |
| `unmapped` | The button has no map entry |
| `webhook_dispatched` | A webhook was queued |
| `noop` | The action had nothing to do |
| `error` | An action argument was invalid |

Use the **Button** filter in the History toolbar to see only button events.

## For firmware / client developers

For client code, see the [Client protocol spec](../dev/client-protocol.md).
Send events through either the frame request or the status body:

- On the frame request: `GET /api/v1/device/<id>/frame?button=<name>&button_event_id=<uint>`.
- In the status body: `{"button": "<name>", "button_event_id": <uint>, …}`.
- Optionally, `button_key=<key>` (query) or `"button_key"` (status body)
  identifies the physical button alongside the action name. Tesserae checks the key's map entry first.
  Clients that send only action names still work.
- The server runs the mapped action before selecting the frame.
  The returned picture shows the new state on the same wake.
- On a `refresh` action, clear the cached `ETag` before making the request.
  The server then returns `200` with a full frame instead of `304`.
