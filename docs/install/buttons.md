# Physical buttons

Hardware buttons on a Tesserae device (the front buttons on a Seeed
reTerminal E-Series, the back-panel buttons on a Pimoroni Inky
Impression, etc.) can be bound to server-side **actions**: rotate
through the dashboards on this device, jump straight to a specific
dashboard, force a fresh render, fire a webhook. The mapping is
per-device and edited from **Settings → Devices**.

Introduced in v0.65.0. Server-side works for any device kind whose
firmware speaks the button contract; ESP32 firmwares (Seeed reTerminal
E-Series, Waveshare, XIAO ePaper family) got wire support in the same
release. Pi + Inky Impression back-button support is on the roadmap.

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

## Configuring buttons per device

**Settings → Devices** → open the card for your device → **General**
tab. Near the bottom of the tab you'll find a **Buttons** section
with a small JSON editor and a live "effective map" fold-out showing
what each button *actually resolves to right now* after the default,
global, and per-device layers are merged.

To override, drop a JSON object into the textarea:

```json
{
  "a": "page:morning_briefing",
  "b": "page:weather_forecast",
  "c": "page:home_status",
  "d": "page:family_calendar"
}
```

An empty textarea clears the override and the device falls back to
the app-global map (**Settings → Server → Buttons**, if configured)
and finally to the built-in default above. Save is wired into the
device card's normal **Save changes** button; validation rejects
malformed JSON, unknown actions, and bad action arguments before
writing.

## Actions

The value on each button is an **action spec**: either a bare action
name (`rotate_next`) or `<action>:<arg>` (`page:morning_briefing`,
`webhook:https://…`).

| Action | Argument | What it does |
|---|---|---|
| `rotate_prev` | none | Advance the device's rotation one step backwards. Wraps at the start. |
| `rotate_next` | none | Advance the device's rotation one step forwards. Wraps at the end. |
| `refresh` | none | Force a fresh render + push of the current step. Useful when a widget's upstream data changed but the composition hash is stable. |
| `step:<index>` | rotation step index (0-based) | Jump straight to that step in the rotation. |
| `page:<page_id>` | dashboard page id | Push a specific dashboard to this device. Doesn't touch rotation state, so a later timer wake resumes the scheduled step. |
| `webhook:<url>` | absolute `http(s)://` URL | Fire a POST to that URL with `{device_id, button, button_event_id, action_spec, timestamp, rotation_id, step_index, step_page_id}` in the body. Fire-and-forget, 3s timeout. |

Adding a new action is a plugin hook (`app.button_actions.register`);
third-party actions show up in the admin UI's help text automatically.

## Manual override sticks until the next anchor

When a button press moves the device's rotation position (a rotate,
a refresh, a `step:`), the new position is treated as a **manual
override** that suppresses the time-based scheduler for the rest of
the day. This is what you almost always want: pressing "next" on a
kitchen panel shouldn't get yanked back by the scheduler a minute
later.

The override expires at the rotation's next daily anchor (midnight
local by default). If you'd rather set a different hold window,
`settings.app.button_hold_seconds` overrides the anchor-based expiry
with a fixed number of seconds. `page:<page_id>` shortcuts don't set
an override; they're one-shot pushes that let the scheduler resume
normally on the next timer wake.

## Duplicate presses are dropped

Firmware retries on network flakiness are common. Every event carries
a monotonically increasing `button_event_id`; the server treats any
incoming id `<= last processed` as a retry and no-ops. Firmwares
without a counter fall back to the same-button-within-3-seconds
window (overridable via `settings.app.button_debounce_s`).

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

- On the frame request: `GET /api/v1/device/<id>/frame?button=<name>`.
- In the status body: `{"button": "<name>", "button_event_id": <uint>, …}`.
- The server dispatches the mapped action synchronously before
  selecting the frame, so the returned artefact reflects the new
  state on this same wake.
- On a `refresh` action, drop your cached `ETag` before making the
  request so the server always returns `200` with a full frame
  rather than `304`.
