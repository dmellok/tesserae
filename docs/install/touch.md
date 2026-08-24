# Touch interactions

A touch-capable panel can react to a tap, a swipe, or a press-drag on
the dashboard: change the page, rotate, fire a webhook, or call a Home
Assistant service (dim a light, toggle a switch). Like physical buttons,
the logic runs **server-side** from **actions** you attach to the
dashboard; the device only reports where the finger went. Introduced for
issue #49.

Two sides to it:

- **Authoring** — attach actions to a dashboard in the editor (below).
  Works for any dashboard; the actions are ignored on display-only
  panels and act on touch-capable ones.
- **Implementing** — a client reports the stroke over the REST device
  API. Any client that can read a touch coordinate and make an HTTP
  request can do this: an ESP-IDF firmware, a **CircuitPython** board, a
  Pi, a browser shim. See [Client side](#client-side-any-device).

## Requirements

1. A **touch-capable panel** (a digitizer). The Seeed reTerminal E1003
   is the reference. A panel reports `touch: true` through the device
   APIs so the editor and MCP agents know it's tappable.
2. **Firmware / a client that reports touch** over the REST API (see
   [Client side](#client-side-any-device)). A display-only panel renders
   the same dashboard fine; the touch actions just never fire.
3. Touch **enabled** on the device: **Settings → Devices** → the device
   card → **General** → turn on **Touch input**. On battery boards this
   keeps the digitizer powered through deep sleep (a few mA), so it's off
   by default. **Touch linger** keeps the device awake briefly after a
   touch so follow-up taps skip the deep-sleep wake latency.

## Audible feedback

E-ink repaints in seconds, not milliseconds, so a tap gives the person in
front of the panel nothing to go on until the whole pipeline (dispatch,
render, download, flash) has finished. On the Seeed reTerminal E series
there's a buzzer on the board to close that gap: **Settings → Devices** →
the device card → **General** → **Beep on interaction**, with a tone and a
volume next to it.

The panel sounds it itself, the moment the touch controller reports the
stroke, before the server is contacted at all. That's the whole point: a
beep the server triggered would arrive with the repaint, which is the wait
it exists to cover. It fires for physical button presses too, so the
button-only models in the series (E1001, E1002, E1004) get it as well.

Off by default, and worth thinking about before switching it on in a shared
room. Volume is the drive strength as a percentage: the piezo is loud at
100 and discreet around 30.

The tones are the server's, not the firmware's. Tesserae sends the notes
themselves (`freq:ms` pairs, `0` for a rest), so picking **Custom** and
writing `1500:40,2000:40,2600:60` in the box below gets you exactly that,
and retuning any of the built-in tones never needs a firmware update. Up to
eight notes, 200-8000 Hz, one second in total: it plays before the tap is
dispatched, so it has to be brief.

## Attaching actions in the editor

Every element in the **grid** and **canvas** editors has an
**Interaction** section (both editors use the same picker, so they
behave identically). It offers:

- **On tap** — one action, fired on a tap.
- **Swipe up / down / left / right** — one action per direction; add
  the directions you want with **+ Add swipe action**.
- **Make this a slider** — the whole element becomes a 0-100 control:
  the stroke's end point maps to a value along an axis (vertical fills
  upward), substituted into the action's `{value}` placeholder. One
  press-drag-lift sets a light's brightness.

### The action types

| Action | What it does |
|---|---|
| Refresh | Re-render and re-push the current page |
| Next / Previous in rotation | Step the device's rotation |
| Jump to step | Go to a rotation step by index |
| Go to page | Switch the device to a saved dashboard |
| Webhook | Fire an HTTP request (`{value}` is substituted on a slider) |
| Home Assistant | Call a service (e.g. `light.turn_on`) with a target entity and optional service data |

**Home Assistant** actions run server-side through the Home Assistant
Core plugin's connection (Settings → Plugins → Home Assistant Core), so
you don't expose HA to the panel. Pick a service and entity; add extra
service data as JSON (e.g. `{"brightness_pct": "{value}"}` on a slider).
Navigation actions (refresh / rotate / step / page) work on any device;
webhook and HA actions dispatch only when authored here (trusted
config), never from raw widget markup.

### Touch regions (hotspots)

A dashboard element's on-screen box becomes its touch region
automatically, so regions always track the design; there are no
coordinates to place by hand. The **Touch region** element (a
"hotspot") paints nothing: drop it over anything, including part of a
code element's output, to make that area tappable.

### Code elements

A code element can annotate its own markup. Give it named **Actions**
(the Interaction card), then reference them from the HTML or JS-built
DOM:

```html
<button data-on-tap="@toggle">Lights</button>
<div data-on-swipe='{"left":"@next"}'>…</div>
```

The `@name` resolves against the element's Actions map, so the
structured action stays in validated config and the markup carries only
a reference. Nodes your JavaScript builds **after** first paint are
picked up too.

## Verifying it works

Open the touch-capable device's card → **Touch monitor**. It draws the
panel at true size and plots touches as they land (taps as dots, swipes
as arrows), colour-coded by outcome — **fired**, **no target**,
**blocked** — with the last render's regions overlaid so you can see
whether a tap hit its target. A dashboard picker previews any
dashboard's regions without pushing it. **Fade old** dims older marks so
recent activity stands out.

For a machine-readable check while building with an agent,
`render_report` returns `tap_regions` (what rendered) and `tap_invalid`
(regions whose action would not dispatch, with a reason). `tap_invalid`
being empty is the real "this dashboard is wired" signal.

## Client side (any device)

Touch is **transport-agnostic**: the panel reports a raw stroke and the
server does gesture classification (tap vs directional swipe) and
hit-testing against the frame's region map. The firmware never needs
region geometry. This means a **CircuitPython** board, an ESP-IDF
firmware, a Pi, or any HTTP client can add touch the same way.

Two ways to report a stroke, both over the REST device API:

- **On the frame poll (deep-sleep clients):** add `touch_x0`,
  `touch_y0`, `touch_x1`, `touch_y1`, `touch_ms`, `touch_digest`, and
  `touch_event_id` to `GET /api/v1/device/<id>/frame`. The action
  dispatches before the frame lookup, so a page/rotate action's repaint
  comes back on the same wake.
- **Out of band (always-on clients):** `POST /api/v1/device/<id>/tap`
  with `{"x0","y0","x1"?,"y1"?,"duration_ms"?,"digest","event_id"?}`.

Coordinates are in the served frame's pixel space (before any
device-side rotation/mirror). `digest` is the ETag of the frame on
screen, so a stroke against a frame the server has since replaced is
dropped rather than misfired. Every response is a `200`; on any
non-dispatched outcome (`stale`, `no_target`, `deduped`, …) the client's
correct move is the same: re-poll `/frame`. Full field tables, the
outcome list, and the swipe/slider semantics are in the
[Client protocol spec](../dev/client-protocol.md#touch-wakes).

!!! note "Swipes need real motion"
    A swipe is only detected when the reported stroke actually moves
    (start and end differ by more than a small radius). A client that
    reports only the touch-down point produces taps only. To support
    swipes, sample the digitizer across the stroke and send the end
    point as `touch_x1`/`touch_y1` (or `x1`/`y1`).
