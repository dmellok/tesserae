# Home Assistant integration

Tesserae fits Home Assistant two ways, which compose freely, you can
use either, both, or neither. Pick whichever matches how you already
run HA.

| Path | What it gives you |
|---|---|
| **HA Add-on** | Tesserae installs inside HA Supervisor and shows up as a sidebar Ingress tab. Same admin UI you'd run standalone, but you don't manage the Python install. |
| **MQTT auto-discovery** | Tesserae publishes HA discovery messages so every device + dashboard surfaces as HA entities (button, select, image, diagnostics). Works whether Tesserae is installed standalone, in Docker, or as the Add-on. |

---

## HA Add-on (Ingress)

The companion repo
[:material-github: dmellok/homeassistant-tesserae-addon](https://github.com/dmellok/homeassistant-tesserae-addon)
publishes the Tesserae Add-on to Home Assistant Supervisor.

### Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, paste
   `https://github.com/dmellok/homeassistant-tesserae-addon`, click **Add**.
2. The **Tesserae** add-on appears under the new repository. Open it and
   click **Install**. (For the in-development build, use the
   **Tesserae (edge)** entry, it tracks `main` and is rebuilt on every
   release.)
3. Once installed, click **Start**. Open **Web UI** (or the sidebar
   icon), Tesserae's admin UI loads inside HA's Ingress tab.

### Configuration

The Add-on's **Configuration** tab exposes the small set of options
that need to be set at boot (port, broker host, app password). Anything
else is configured from inside Tesserae's own Settings UI, which
persists into the Add-on's mounted `/data` volume, your state survives
Add-on restarts and version upgrades.

### What the Add-on does for you

- Mounts `/data` so pages / themes / settings persist across upgrades.
- Sets `TESSERAE_HA_INGRESS=1` so the admin UI trusts HA's Ingress
  session and skips the standalone login form (HA's own auth gates
  access, you don't sign in twice).
- Wires `SUPERVISOR_TOKEN` through so Tesserae can call Supervisor
  for "rebuild the add-on" and "read installed version" niceties.
- Provides an MQTT default that points at HA's built-in broker (if
  you're running it as an Add-on too).

---

## MQTT auto-discovery

When Tesserae and Home Assistant share an MQTT broker, Tesserae can
publish HA's discovery messages so HA auto-creates a **Tesserae hub**
device plus **one HA device per registered display**. No extra glue
in HA, the entities just appear.

### Enable

1. **Settings → Server → MQTT broker**, confirm Tesserae is pointed
   at the same broker HA uses. (If you run HA's Mosquitto Add-on, that's
   `core-mosquitto` from inside the Add-on or `homeassistant.local`
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
- Diagnostic **sensors** + **binary_sensors**: *Last push*, *Pushes
  today*, *Last error*, *Busy*.

Under each **registered display** (one HA device card per panel, linked
to the hub via `via_device`):

- **image** *Frame*, the composition PNG currently published to that
  display.
- **sensor** *Current dashboard*, the page assigned to it right now.
- **sensor** *Last updated*, when it last received a frame.
- **sensor** *Last seen*, when it last sent a heartbeat.
- **select** *Dashboard*, push one of the dashboards bound to *this*
  display to *only* this display.
- Lazily (when the device's heartbeat carries them): **Battery**,
  **Signal**, **IP address**.

### Use cases

- **Lovelace dashboards.** Drop the *Frame* image entity onto a
  Lovelace dashboard and you've got a mirror of every e-ink display
  in HA.
- **Automations.** Trigger a re-render from an HA automation by
  calling the *Active dashboard* select. ("When the front door
  opens, switch the kitchen Inky to the `home_arrival` page.")
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
- **The Add-on starts but Ingress shows a blank page.** Open the
  Add-on's **Log** tab, Tesserae logs why it bailed (most often a
  missing broker host or a wrong port). The Add-on's **Configuration**
  tab is the right place to fix it; the in-app Settings pages won't
  let you change Ingress wiring.
- **Buttons fire but nothing pushes.** Check the *Last error* sensor
  on the hub device, push failures (broker offline, no device bound)
  surface there with the upstream error.
