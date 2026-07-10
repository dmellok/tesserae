# tesserae_status

Dashboard status strip. Left identity (leading icon + dashboard name),
right-hand row of ambient info chips. Built to the converged design
handoff (`design_handoff_status_widget/`, round 5): pixel values,
icon choices, and spacing are the source of truth.

## Placement modes

- **Bar** — thin horizontal strip, 48 px tall. Sits at the top or
  bottom of a dashboard. Chips render on the right; overflow is
  clipped rather than wrapped.
- **Block** — resizable rectangular cell (400×160 is the reference
  size); chips wrap onto multiple rows.

## Chip modes

Three modes drive two booleans, ``showIcon`` and ``showText``:

| Mode | Icons | Text | Leading icon |
|---|---|---|---|
| ``icon-text`` (default) | shown | shown | shown (if enabled) |
| ``icon-only`` | shown | hidden | shown (if enabled) |
| ``text-only`` | hidden | shown | force-hidden |

Update signal per mode:

- ``icon-text``: icon + badge dot + value + accent update sub
  (e.g. "→ .18").
- ``icon-only``: icon + badge dot only (the dot is the signal).
- ``text-only``: value + accent update sub, no icon or dot.

Colour is never the only signal, so a 1-bit render still reads the
update.

## Chips

Always-on ambient stats (in order): **time, environment, battery,
wifi, broker**. The environment cluster contains temperature and/or
humidity when the target panel publishes those fields. Update chips
(**app version, panel firmware**) appear only when an update is
pending.

Chip data comes from Tesserae's device heartbeats: battery from
``battery_pct``, Wi-Fi label from ``rssi`` (Excellent / Good / Fair /
Weak buckets), temperature from ``temperature_c``, and relative
humidity from ``humidity_pct``. Temperature remains canonical Celsius
telemetry on the server; the widget's metric / imperial option only
changes its presentation. Broker presence comes from the MQTT
configuration, and firmware aggregate state comes from the in-process
``firmware_check`` cache.

Per-device pushes read all panel telemetry from the render's target
device. Editor previews and virtual-panel renders have no target, so
the environment cluster uses the most recent single sensor-bearing
heartbeat instead of averaging or combining values across panels.

## Auto-contrast

The widget owns its own colour scheme. Set a freeform ``panelBg`` hex
and the widget computes relative luminance to pick foreground:

- Background luminance > 0.42 → ink foreground (``#1B1A16``), red
  accent ``#C24F2C``.
- Background luminance ≤ 0.42 → paper foreground (``#FCFBF7``), red
  accent ``#E0663F``.

No separate light/dark toggle: picking a light or dark colour is the
theme.

## Rendering constraints

- All rules ≥ 2 px (outer border/rule 3 px, badge ring 2 px).
- No gradients, no CSS transitions, no subtle greyscale.
- Only two ink values per render (fg and bg); update accent is the
  only additional colour.
- Space Grotesk 600/700 preferred; falls back to the theme font.

## Network calls

The only outbound call this widget makes is on the *app-version
update indicator*, and only when ``check_for_updates`` is enabled.
When on, the widget fetches
``https://api.tesserae.ink/version/latest`` with three query params:

- ``channel=stable``
- ``current=<running Tesserae version>``
- ``install=<widget-scoped install id>``

The install id is a SHA-256 derivation of the Tesserae install's
random UUID mixed with the widget's plugin id, so aggregate install
counts can dedupe across days without correlating with any other
widget's identity. Regenerating the install identifier in
**Settings → System** resets it. No IP address, User-Agent, or PII is
stored on the api.tesserae.ink side beyond a coarse country code
derived from the request IP. See
[the privacy page](https://tesserae.ink/privacy/) for the full
disclosure.

The *firmware updates* chip reads Tesserae's in-process firmware
cache, so this widget makes no additional network calls for it.
