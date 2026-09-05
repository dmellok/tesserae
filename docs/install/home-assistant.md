# Home Assistant integration

Tesserae fits Home Assistant two ways, which compose freely, you can
use either, both, or neither. Pick whichever matches how you already
run HA.

| Path | What it gives you |
|---|---|
| **HA App** | Tesserae installs inside HA Supervisor and shows up as a sidebar Ingress tab. Same admin UI you'd run standalone, but you don't manage the Python install. |
| **MQTT auto-discovery** | Tesserae publishes HA discovery messages so every device + dashboard surfaces as HA entities (button, select, image, diagnostics). Works whether Tesserae is installed standalone, in Docker, or as the App. |

---

## HA App (Ingress)

The companion repo
[:material-github: dmellok/homeassistant-tesserae-addon](https://github.com/dmellok/homeassistant-tesserae-addon)
publishes the Tesserae App to Home Assistant Supervisor.

### Install

1. **Settings → Apps → app store → ⋮ → Repositories**, paste
   `https://github.com/dmellok/homeassistant-tesserae-addon`, click **Add**.
2. The **Tesserae** app appears under the new repository. Open it and
   click **Install**. (For the in-development build, use the
   **Tesserae (edge)** entry, it tracks `main` and is rebuilt on every
   release.)
3. Once installed, click **Start**. Open **Web UI** (or the sidebar
   icon), Tesserae's admin UI loads inside HA's Ingress tab.

### Configuration

The App's **Configuration** tab exposes the small set of options
that need to be set at boot (port, broker host, app password). Anything
else is configured from inside Tesserae's own Settings UI, which
persists into the App's mounted `/data` volume, your state survives
App restarts and version upgrades.

### What the App does for you

- Mounts `/data` so pages / themes / settings persist across upgrades.
- Sets `TESSERAE_HA_INGRESS=1` so the admin UI trusts HA's Ingress
  session and skips the standalone login form (HA's own auth gates
  access, you don't sign in twice).
- Wires `SUPERVISOR_TOKEN` through so Tesserae can call Supervisor
  for "rebuild the app" and "read installed version" niceties.
- Provides an MQTT default that points at HA's built-in broker (if
  you're running it as an App too).

---

## MQTT auto-discovery

When Tesserae and Home Assistant share an MQTT broker, Tesserae can
publish HA's discovery messages so HA auto-creates a **Tesserae hub**
device plus **one HA device per registered display**. No extra glue
in HA, the entities just appear.

### Enable

1. **Settings → Server → MQTT broker**, confirm Tesserae is pointed
   at the same broker HA uses. (If you run HA's Mosquitto App, that's
   `core-mosquitto` from inside the App or `homeassistant.local`
   from a standalone Tesserae host.)
2. **Settings → App → Home Assistant discovery**, flip the toggle on
   and save.

Tesserae publishes the discovery config under
`homeassistant/<component>/tesserae/...` (retained). HA reads them
within seconds; entities appear in **Settings → Devices & Services →
MQTT**.

### What gets published

Under the **Tesserae hub** device:

- One **button** per saved dashboard, pressing it re-renders that
  page and fans the frame out to every device the page is bound to.
- A **select** named *Active dashboard*, same as the buttons, but as
  a single dropdown (handy for HA Lovelace UIs).
- An **image** entity named *Last render*, the composition PNG of
  the most recent push (covers the legacy / virtual-panel case where
  no devices are bound).
- A **switch** named *Automation*. ON means the scheduler fires as
  normal; OFF holds every automated push (lineup rotations and
  schedules, timed page refreshes, deck pre-renders, home returns)
  until you turn it back on. Manual sends, physical buttons and HA
  commands still go through. The same toggle lives in
  **Settings → App → Automation**.
- A **switch** named *Quiet hours*, the app-level quiet-hours toggle
  (the window itself is set in **Settings → App → Quiet hours**).
- One **switch** per lineup, mirroring its enabled flag. Lineups with
  a bound display also get a *Push now* **button** (warm every page and
  send the entry page), and lineups with more than one page get *Next*
  and *Previous* **buttons** that step every bound display.
- A **notify** entity named *Notify all displays*: call
  `notify.send_message` on it and the text is rendered as a
  black-on-white card onto every display.
- Diagnostic **sensors** + **binary_sensors**: *Last push*, *Pushes
  today*, *Last error*, *Busy*.

Under each **registered display** (one HA device card per panel, linked
to the hub via `via_device`):

- **image** *Frame*, the composition PNG currently published to that
  display.
- **sensor** *Current dashboard*, the page assigned to it right now.
- **sensor** *Last updated*, when it last received a frame.
- **sensor** *Last seen*, when it last sent a heartbeat.
- **sensor** *Next change*, when the next visible update is projected
  to land (rotation step, schedule firing, page refresh).
- **select** *Dashboard*, push one of the dashboards bound to *this*
  display to *only* this display.
- **select** *Lineup*, which lineup the display follows. Picking one
  binds the display to it and makes it live there; *(none)* unbinds
  it from every lineup.
- **number** *Hold* (minutes). Set it to hold the page currently on the
  glass against the rotation for that long; 0 clears the hold. Counts
  down as the hold runs.
- **switch** *Quiet hours override*, the per-display quiet-hours
  override. Turning it on without stored times copies the app-level
  window.
- **binary_sensor** *In quiet hours*, whether the display is inside
  its effective quiet window right now.
- **binary_sensor** *Online*, derived from the last heartbeat against
  the display's wake interval (two missed intervals means offline).
- **button** *Refresh now*, re-render whatever the display is showing
  and force a repaint.
- **event** *Input*, fires for physical buttons (`button`) and touch
  gestures (`tap`, `swipe`, `slide`) with the button name, gesture,
  action, slider value and the page landed on as attributes. Use it as
  an automation trigger.
- **notify**, `notify.send_message` renders the text onto this display
  only.
- Where the kind carries a sleep interval: **number** *Wake interval*
  (seconds), bounded by the kind's schema. Changing it saves the
  device config and republishes it, same as the device card.
- Once the device has advertised OTA support: an **update** entity
  showing installed vs latest firmware, with *Install* queueing the
  device for its kind's newest release (the Firmware page's per-device
  update button).
- Lazily (when the device's heartbeat carries them): **Battery**,
  **Battery voltage**, **Signal**, **IP address**, **Firmware**,
  **Temperature**, **Humidity**, **Uptime**, plus **Low battery**
  (against the Settings → App threshold) and **Next wake** (the
  server's wake prediction).

### Use cases

- **Lovelace dashboards.** Drop the *Frame* image entity onto a
  Lovelace dashboard and you've got a mirror of every e-ink display
  in HA.
- **Automations.** Trigger a re-render from an HA automation by
  calling the *Active dashboard* select. ("When the front door
  opens, switch the kitchen Inky to the `home_arrival` page.")
- **Lineups on a schedule HA owns.** Turn the *Weekend* lineup switch
  on Friday evening and off Sunday night, or flip *Automation* off
  while guests are staying so nothing rotates overnight.
- **Guest mode / focus mode.** Set *Hold* to 120 on the office display
  when a calendar event starts, so the agenda page stays put through
  the meeting.
- **Physical buttons as HA triggers.** Trigger on the *Input* event
  entity: a `swipe` on the hall panel toggles the porch light, a
  `button` press named `refresh` re-runs a script.
- **Notifications.** `notify.send_message` with "Bins tonight" onto
  the kitchen display at 20:00; press *Refresh now* afterwards (or let
  the next rotation step) to clear it.
- **Fleet health.** Alert on *Online* going off or *Low battery*
  turning on, and let the *Firmware* update entity show up on HA's
  Settings page like any other device update.
- **Diagnostics.** Use the *Busy* binary_sensor to gate other
  automations so HA waits for the current push to finish.

### Per-display fan-out vs. hub

The hub's *Active dashboard* select pushes to **every** device bound
to that page. Each per-display *Dashboard* select pushes to **only
that one display**. Use whichever scopes the automation correctly -
both are independently driven.

---

## Webhook push from HA

A lower-coupling alternative to the MQTT discovery: use the webhook
endpoint from an HA RESTful command, no broker required.

```yaml
# configuration.yaml
rest_command:
  tesserae_push:
    url: "http://tesserae.local:8765/api/v1/push"
    method: POST
    headers:
      Authorization: "Bearer !secret tesserae_webhook_token"
      Content-Type: "application/json"
    payload: '{"page": "{{ page }}"}'
```

```yaml
# automation
- alias: "Push kitchen on arrival"
  trigger:
    - platform: state
      entity_id: person.you
      to: "home"
  action:
    - service: rest_command.tesserae_push
      data:
        page: "home_arrival"
```

The token comes from **Settings → System → Webhook** in Tesserae, see
[Install Tesserae](server.md#webhook-push) for the full request shape.

---

## Troubleshooting

- **Entities don't appear after enabling discovery.** Confirm Tesserae
  and HA are pointed at the same broker (same host:port and same
  credentials). HA's MQTT integration must be installed and connected
 , check **Settings → Devices & Services → MQTT**.
- **The App starts but Ingress shows a blank page.** Open the
  App's **Log** tab, Tesserae logs why it bailed (most often a
  missing broker host or a wrong port). The App's **Configuration**
  tab is the right place to fix it; the in-app Settings pages won't
  let you change Ingress wiring.
- **Buttons fire but nothing pushes.** Check the *Last error* sensor
  on the hub device, push failures (broker offline, no device bound)
  surface there with the upstream error.
