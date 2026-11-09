# Physical buttons

Hardware buttons on a Tesserae device (the front buttons on a Seeed
reTerminal E-Series, the back-panel buttons on a Pimoroni Inky
Impression, etc.) can be bound to server-side **actions**: rotate
through the dashboards on this device, jump straight to a specific
dashboard, download the latest rendered frame, force a fresh render,
or fire a webhook. The mapping is
per-device and edited on the device's **Controls** panel.

Introduced in v0.65.0. Server-side works for any device kind whose
firmware speaks the button contract; ESP32 firmwares (Seeed reTerminal
E-Series, Waveshare, XIAO ePaper family) got wire support in the same
release. On a Raspberry Pi with a Pimoroni Inky Impression, the
[tesserae-device-pi](https://github.com/dmellok/tesserae-device-pi) client reads
the four buttons (A to D) and sends them over REST or the cloud relay; MQTT
does not carry button presses.

## What buttons can do

The default map, applied to any device that doesn't override it, is:

| Button | Action | Effect |
|---|---|---|
| `left` | `rotate_prev` | Move back one step in the device's rotation |
| `right` | `rotate_next` | Advance one step in the device's rotation |
| `refresh` | `refresh` | Re-render + re-push the current step |

That's the "conventional three-button e-ink appliance" shape. If your
hardware has different names (`a` / `b` / `c` / `d` on a Pimoroni Inky,
say) or more than three, you configure them under the device.

### Inky Impression: binding each button by its letter

The Pi client sends every press with two names: the button's action
(`left` for A, `refresh` for B, `right` for C by default) and its letter
(`a` to `d`). The map is checked for the letter first, then the action,
so with no map the buttons behave as the default table above, and a map
keyed by letter binds each of the four buttons on its own:

```json
{
  "a": "page:Calendar",
  "b": "page:Weather",
  "c": "page:Home",
  "d": "page:Family"
}
```

Button D has no action of its own, so it does nothing until the map
binds `d`. A deck showing on the panel still takes its own button links
first. Holding the refresh button (B) keeps its Pi-side meaning whatever
the map says: 3 to 19 seconds unlocks the Pi's settings page, 20 seconds
factory-resets it.

## Configuring buttons per device

Open the device from **Devices** and go to its **Controls** panel.
The **Button map** field there is a small JSON editor, with an
**Effective map** fold-out showing what each button *actually resolves
to right now* once your map is merged over the built-in default, and a
**Dashboard ids** fold-out listing the `page:` value for each of your
dashboards.

To override, drop a JSON object into the textarea:

```json
{
  "a": "page:Morning briefing",
  "b": "page:b2f1e07a9c55",
  "c": "rotate_next",
  "d": "refresh"
}
```

`page:` takes a dashboard's **name** (matched ignoring case) or its
**id**, the 12-character code shown on the dashboard's details page.
The **Dashboard ids** fold-out under the map lists both. A name is
easier to read, but the binding breaks if you rename the dashboard, and
saving is refused when two dashboards share the name. An id keeps
working through a rename.

An empty field clears the override and the device falls back to the
built-in default above. Save is wired into the
device page's normal **Save** bar; validation rejects
malformed JSON, unknown actions, and bad action arguments before
writing.

## Actions

The value on each button is an **action spec**: either a bare action
name (`rotate_next`) or `<action>:<arg>` (`page:Kitchen`,
`webhook:https://…`).

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

Adding a new action is a plugin hook (`app.button_actions.register`);
third-party actions show up in the admin UI's help text automatically.

### `webhook` vs `webhook_refresh`

Use `webhook:` when the POST doesn't change anything on screen (log an
event, ring a bell, start a job you'll see later). Use
`webhook_refresh:` when it does: book a meeting room, toggle a door,
file a ticket that the dashboard displays. Plain `webhook:` leaves the
panel showing pre-action state until its next wake, which on a
deep-sleeping panel can be a long time.

The repaint is **delayed, not immediate**. The POST is fire-and-forget
with no acknowledgement, so re-rendering straight away would usually
read the receiver's *pre-action* state and repaint the same frame with
more conviction. The default wait is 5 s; set
`button_webhook_refresh_delay_s` in app settings if your receiver is
slower. A burst of presses coalesces into one repaint after the last
one.

If you control the receiving server, calling
[`POST /api/v1/push`](server.md#webhook-push) from it after the change commits is
strictly better: it fires when the work is actually done rather than
after a guessed delay. `webhook_refresh:` is for receivers you can't
modify.

Either way the repaint reaches a touch panel while it is still awake
from the tap, provided the panel is inside its touch linger window and
running firmware v1.21.0 or later. Small changes arrive as patched
rectangles; a change too big for that arrives as a whole new frame, and
the panel is told to come and fetch it. Set the display's **Touch
linger** long enough to cover your receiver's latency plus the repaint:
a booking that takes four seconds to commit needs more than the 5 s
default wait to fit inside a 10 s window. Miss the window and the panel
still catches up, just not until its next scheduled wake.

## Audible feedback

The Seeed reTerminal E series has a buzzer on the board, and Tesserae can
sound it the instant a press registers rather than leaving the user waiting
on the repaint: open the device from **Settings → Devices** and turn on
**Beep on interaction**, with **Beep tone** and **Beep volume** to pick the sound. The panel makes
the sound locally, before the server is contacted. Off by default. The same
setting covers taps on the touch models. Needs firmware v1.22.0 or later.

## Manual override sticks until the next anchor

When a button press moves the device's rotation position (a rotate,
a refresh, a `step:`), the new position is treated as a **manual
override** that suppresses the time-based scheduler for the rest of
the day. This is what you almost always want: pressing "next" on a
kitchen panel shouldn't get yanked back by the scheduler a minute
later.

The override expires at the rotation's next daily anchor (midnight
in the timezone from **Settings → Server → Location & time**, else the host's). A **touch**
that rotates the page holds it for one dwell window of the step it
landed on instead, then the lineup carries on; a tap is a lighter
gesture than a button, and touch panels tend to run short-dwell
lineups where a day-long hold reads as a frozen display. If you'd
rather set a different hold window for both,
`settings.app.button_hold_seconds` overrides the expiry with a fixed
number of seconds. `page:` shortcuts don't set an override;
they're one-shot pushes that let the scheduler resume normally on the
next timer wake.

While a hold is active the lineup's timer skips that display and the
log says so (`deck timer advance skipped: … (manual hold)`); the
Lineups card shows **held**. Pressing **Play** on the lineup releases
the hold.

## Duplicate presses are dropped

Firmware retries on network flakiness are common. Every event carries
a monotonically increasing `button_event_id`; the server treats an id
equal to the last processed one as a retry and no-ops (a lower id
reads as a counter restart after a power cycle and still dispatches).
Firmwares without a counter fall back to the
same-button-within-3-seconds window (overridable via
`settings.app.button_debounce_s`).

## Buttons on remote (relay) panels

Buttons work on relay-paired panels too. The press rides the panel's
relayed status report instead of the frame request, so the round trip
is store-and-forward: the home instance picks the press up on its
next relay poll (within ~30 seconds, then faster for follow-up
presses while the panel stays awake), renders, and uploads the new
frame for the panel to fetch before it goes back to sleep. Expect
seconds of latency rather than the instant response of a local
panel; presses older than a few minutes (for example if the home
instance was offline) are dropped rather than replayed. Firmware
requirements are in the
[Cloud relay contract](../relay/contract.md).

## Seeing button events on the History page

Every button press writes a row to `/history` with source `button`,
the friendly device name in the target column, and a synthesised
detail line describing what happened:

```
[Button] Kitchen wall panel · button right → rotate_next pushed Afternoon calendar
```

The row also carries the status: `dispatched` (a page was pushed),
`deduped` (retry), `unmapped` (no map entry for that button),
`webhook_dispatched` (webhook queued), `noop` (action resolved to
nothing to do), `error` (bad action arg). Filter by the **Button**
chip in the History toolbar to see just the button feed.

## For firmware / client developers

If you're building a client and want it to send button events, see
the [Client protocol spec](../dev/client-protocol.md). Short version:

- On the frame request: `GET /api/v1/device/<id>/frame?button=<name>&button_event_id=<uint>`.
- In the status body: `{"button": "<name>", "button_event_id": <uint>, …}`.
- Optionally, `button_key=<key>` (query) or `"button_key"` (status body)
  names the physical button beside the action name. A map entry for the
  key wins over one for the name, which lets one map bind buttons
  individually while clients that send names only keep working.
- The server dispatches the mapped action synchronously before
  selecting the frame, so the returned artefact reflects the new
  state on this same wake.
- On a `refresh` action, drop your cached `ETag` before making the
  request so the server always returns `200` with a full frame
  rather than `304`.
