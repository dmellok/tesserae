# Touch interactions

Touch a dashboard to run an action.
The screen reports your touch; Tesserae runs the action on the server.

- Add actions in the dashboard editor. Any dashboard can have them.
- Build touch support into a client through the REST device API. See [Client side](#client-side-any-device).

## Requirements

1. A touchscreen. The Seeed reTerminal E1003 is the reference device.
   It reports `touch: true` so the editor and agents know it supports touch.
2. Firmware or a client that reports touch through REST. See [Client side](#client-side-any-device).
   Display-only screens show the same dashboard, but touch actions do nothing.
3. Turn on **Touch input** under **Settings → Devices**.
   On battery devices, this keeps the touch controller powered during sleep and uses a few mA.
   It is off by default.

   **Touch linger** keeps the device awake briefly after a touch, so follow-up taps respond faster.

   **Touch wake** is available on reTerminal E1003 and Sticky with firmware 1.44.0 or later:

   | Mode | Behaviour |
   |---|---|
   | Any tap | Keeps scanning; the first tap wakes the screen |
   | Double tap or swipe | Uses about 1 mA instead of several; a single tap does nothing, but a double tap or swipe wakes it |

   Once awake, normal taps work in either mode.

## Audible feedback

Seeed reTerminal E series devices can beep immediately, while the e-ink repaint takes seconds.
In **Settings → Devices**, turn on **Beep on interaction**.
Choose the sound with **Beep tone** and **Beep volume**.

The beep happens on the device before it contacts the server.
It also works for physical buttons, including the button-only E1001, E1002, and E1004.

Beeps are off by default. Consider other people before enabling them in a shared room.
Volume is the buzzer's drive strength as a percentage: loud at 100, quiet around 30.

For a custom tone, choose **Custom** and enter notes such as `1500:40,2000:40,2600:60`.
Notes use `freq:ms` pairs; `0` is a rest.
The server sends the notes, so changing tones needs no firmware update.

Use up to eight notes at 200-8000 Hz, with a maximum total length of one second.

The tone plays before Tesserae runs the tap action.

## Attaching actions in the editor

Every element in the grid and canvas editors has the same **Interaction** picker:

- On tap: choose one action.
- Swipe up / down / left / right: use **+ Add swipe action** for each direction you need.
- Make this a slider: turn the element into a 0-100 control.

A slider uses the end of your drag to set the action's `{value}`.
It follows the selected axis; vertical sliders increase upward.
Drag and release to set a light's brightness.

### The action types

| Action | What it does |
|---|---|
| Refresh | Re-render and re-push the current page |
| Next / Previous in rotation | Step the device's rotation |
| Jump to step | Go to a rotation step by index |
| Go to page | Switch the device to a saved dashboard |
| Webhook | Fire an HTTP request (`{value}` is substituted on a slider) |
| Home Assistant | Call a service (e.g. `light.turn_on`) with a target entity and optional service data |

Home Assistant actions use the **Home Assistant Core** plugin's connection under **Settings → Widgets → Home Assistant Core**.
The screen doesn't need direct access to HA.
Choose a service and entity.

Add optional JSON service data, such as `{"brightness_pct": "{value}"}` for a slider.

Navigation actions (refresh, rotate, step, and page) work on any device.

Webhook and HA actions only run when configured in the editor. Raw widget markup cannot trigger them.

### Touch regions (hotspots)

Each element's box becomes its touch region automatically, so you don't need to enter coordinates.
To make another area tappable, place a **Touch region** over it.
This invisible hotspot also works over part of a code element's output.

### Code elements

In a code element's Interaction card, add named **Actions**.
Reference those names from HTML or a DOM built by JavaScript:

```html
<button data-on-tap="@toggle">Lights</button>
<div data-on-swipe='{"left":"@next"}'>…</div>
```

`@name` refers to the element's Actions map.
The action stays in validated settings; the markup holds only its name.
Elements added by JavaScript after the first paint work too.

## Verifying it works

Open the device card's **Touch monitor**.
It shows the screen at true size, with the last render's touch regions overlaid.
Touches appear as dots for taps and arrows for swipes.

Their colours show the result: fired, no target, or blocked.

Choose another dashboard to preview its regions without sending it to the screen.
**Fade old** dims older marks.

For checks with an agent, `render_report` returns:

- `tap_regions`: the regions drawn.
- `tap_invalid`: regions whose actions would not run, with a reason.

An empty `tap_invalid` means every region's action would dispatch.

## Client side (any device)

The client reports a stroke. Tesserae decides whether it is a tap or directional swipe and finds the target region.
The client doesn't need the region coordinates.

Any client that reads touch coordinates and sends HTTP requests can use this.
Examples include ESP-IDF firmware, CircuitPython, a Raspberry Pi, or a browser shim.

Both reporting methods use the REST device API:

- For deep-sleep clients, add these fields to `GET /api/v1/device/<id>/frame`:
  `touch_x0`, `touch_y0`, `touch_x1`, `touch_y1`, `touch_ms`, `touch_digest`, and `touch_event_id`.

  The action runs before frame lookup, so a page or rotation change returns its picture on the same wake.

- For always-on clients, send `POST /api/v1/device/<id>/tap`
  with `{"x0","y0","x1"?,"y1"?,"duration_ms"?,"digest","event_id"?}`.

Coordinates use the served picture's pixels, before device-side rotation or mirroring.
`digest` is the ETag of the picture on screen.
Tesserae drops strokes for pictures it has since replaced.

Every response is `200`. Re-poll `/frame` after any outcome that didn't run an action, including `stale`, `no_target`, or `deduped`.

For all fields and outcomes, see the [Client protocol spec](../dev/client-protocol.md#touch-wakes).
It also defines swipe and slider behaviour.

!!! note "Swipes need real motion"
    A swipe is only detected when the reported stroke actually moves
    (start and end differ by more than a small radius). A client that
    reports only the touch-down point produces taps only. To support
    swipes, sample the digitizer across the stroke and send the end
    point as `touch_x1`/`touch_y1` (or `x1`/`y1`).
