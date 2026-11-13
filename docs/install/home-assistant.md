# Home Assistant integration

Run Tesserae inside Home Assistant, connect it through MQTT, or use both options.

| Path | What it gives you |
|---|---|
| HA App | Runs inside HA Supervisor, with the same admin UI in HA's sidebar. No Python setup needed. |
| MQTT auto-discovery | Adds devices and dashboards as HA entities. Works with any Tesserae install. |

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

Use the App's **Configuration** tab for the log level and MQTT connection settings.
The broker settings are host, port, username and password.

Change everything else in Tesserae's Settings. Those changes are saved in `/data`.

### What the App does for you

- Keeps data in `/data` across App restarts and upgrades.
- Sets `TESSERAE_HA_INGRESS=1` so the admin UI trusts HA's Ingress
  session. HA handles login, so you do not sign in twice.
- Wires `SUPERVISOR_TOKEN` through so Tesserae can call Supervisor
  to rebuild the app and read the installed version.
- Provides an MQTT default that points at HA's built-in broker (if
  you're running it as an App too).

## MQTT auto-discovery

Connect Tesserae and Home Assistant to the same MQTT message broker.
HA can then add a Tesserae hub and one HA device per registered display automatically.

### Enable

1. **Settings → Server → MQTT broker**, confirm Tesserae is pointed
   at the same broker HA uses. (If you run HA's Mosquitto App, that's
   `core-mosquitto` from inside the App or `homeassistant.local`
   from a standalone Tesserae host.)
2. **Settings → Server → Network & integrations → Home Assistant MQTT discovery**, flip the toggle on
   and save.

Tesserae publishes the discovery config under
`homeassistant/<component>/tesserae/...` (retained). HA reads them
within seconds; entities appear in **Settings → Devices & Services →
MQTT**.

### What gets published

Under the Tesserae hub device:

| Entity | Type | What it does |
| --- | --- | --- |
| Each saved dashboard | Button | Redraws the page and sends it; can be disabled under [Trimming what's published](#trimming-whats-published) |
| Active dashboard | Select | The dashboard buttons in one dropdown, useful in Lovelace |
| Last render | Image | PNG from the latest push; also covers virtual panels with no bound devices |
| Automation | Switch | Enables or stops automated sends; also in **Settings → Server → Automation** |
| Quiet hours | Switch | Toggles server quiet hours; set the window in **Settings → Server → Quiet hours** |
| Each lineup | Switch | Matches the lineup's enabled setting |
| Notify all displays | Notify | `notify.send_message` draws a black-on-white text card on every display |

Turning Automation off stops lineup rotations and schedules, timed page refreshes, deck pre-renders and returns home.
Manual sends, physical buttons and HA commands still work.

Lineups also publish buttons:

- Push now: for lineups with a bound display. Pre-renders every page, then sends the entry page.
- Next and Previous: for lineups with multiple pages. Advances every bound display.

You can disable these under [Trimming what's published](#trimming-whats-published).

The hub's diagnostic sensors and binary_sensors are Last push, Pushes today, Last error and Busy.

Rendering commands run in the background, one at a time, in the order HA sends them.
They do not block the MQTT connection or the handling of other commands.
Firmware installs use this queue too.

A command waiting over a minute for another render fails as busy.
Failures appear in Last error.

Each registered display gets an HA device card, linked to the hub by `via_device`:

| Entity | Type | What it shows or controls |
| --- | --- | --- |
| Frame | Image | PNG currently published to the display |
| Current dashboard | Sensor | Assigned page |
| Last updated | Sensor | Last received frame |
| Last seen | Sensor | Last heartbeat |
| Next change | Sensor | Next planned visible update |
| Next update | Sensor | When that update reaches the screen, as shown on Devices and Lineups |
| Dashboard | Select | A dashboard bound to this display |
| Lineup | Select | Picking one binds the display and starts it there; *(none)* unbinds all |
| Hold | Number, minutes | Holds the current page against rotation; 0 clears it; counts down |
| Pause | Number, minutes | Stops automated delivery to this display; 0 ends it; counts down |
| Quiet hours override | Switch | Overrides server quiet hours; enabling it copies server times if none are stored |
| In quiet hours | Binary sensor | Whether the display is in its effective quiet window |
| Online | Binary sensor | Offline after two missed wake intervals, based on its last heartbeat |
| Refresh now | Button | Redraws the current page and forces a repaint |
| Next / Previous | Buttons | Advances one page on this display only |

Next change accounts for rotation steps, scheduled sends and page refreshes.

Next update accounts for the screen's availability:

- Connected screens, such as Visionect, and screens that stay awake receive updates at push time.
- Sleeping battery screens receive them at the next check-in.
- Quiet hours or a pause delay them until that period ends.

Timing attributes are `reason`, `detail`, `mode`, `held` and `next_check_in`.
Reasons include `connected`, `check-in` and `held by quiet hours`.

The Lineup select is hidden when lineup controls are disabled.

Pause stops lineup steps and daily or interval lineups.
It also stops rotations, schedules, page refreshes and returns to a lineup's home page.

Manual sends and physical buttons still work. Other displays are unaffected.
When the pause ends, the display catches up only if it missed something:

- It gets the lineup's current page.
- A later daily or interval send missed that day takes precedence.
- Quiet hours can delay this catch-up until they end.

Hold stays separate: it keeps a page on screen against rotation after a button press.
Pause does not change Hold.

Next and Previous work like the panel's left / right buttons, with these rules:

- They use the lineup currently on screen, matching the Lineup select.
  This includes a daily or interval lineup that fired, even when several lineups are bound.
- The page's link for that button wins. Otherwise, they follow lineup order and wrap around.
  They use a pre-rendered frame when available.
- They set no hold. A timed lineup continues at its next step.
- A one-page lineup does nothing; History records a no-op.
- With no lineup, they advance the display's rotation if one reaches it, and set a hold like a physical button.
  With neither a lineup nor a rotation, they do nothing.
- After a manually sent dashboard, Next goes to the page after the lineup's previous position.
- They work with Automation off and during quiet hours.
- A failed delivery leaves the displayed page and lineup position unchanged. Last error gives the reason.
  Possible causes:
    - Render failure
    - A newer send winning
    - A missing dashboard
- Remapping a panel button does not change these commands. They do not fire the Input event.

Other entities appear when the device supports them:

| Entity | Type | Availability and behavior |
| --- | --- | --- |
| Input | Event | Physical `button` presses and touch gestures: `tap`, `swipe`, `slide` |
| Notification | Notify | `notify.send_message` draws text on this display only |
| Wake interval | Number, seconds | For device kinds with a sleep interval; limited by the kind's schema |
| Firmware | Update | After the device advertises OTA support; shows installed and latest firmware |

Input can trigger automations. Its attributes include the button name, gesture, action, slider value and page reached.

Changing Wake interval saves and republishes the device config, just like the device card.
Install on the update entity queues the newest firmware for that kind, like the Firmware page's per-device button.

Heartbeat readings add Battery, Battery voltage, Signal and IP address sensors.
They also add Firmware, Temperature, Humidity and Uptime.

- Low battery uses **Settings → Server → Low-battery warnings**.
- Next wake uses the check-in cycle the screen has actually followed.

Next wake is unknown while the screen is connected or stays awake, because it is always reachable.
Use Next update to see when a change will arrive.

### Trimming what's published

Two switches under **Settings → Server → Network & integrations**, next
to the discovery toggle, leave whole groups out. Both are on by default:

- **Publish lineup controls to Home Assistant** adds:
    - Each lineup's switch
    - Push now, Next and Previous on the hub
    - Each display's Lineup select
- **Publish a button per dashboard to Home Assistant**: the hub's
  dashboard buttons. The hub's *Active dashboard* select and each
  display's *Dashboard* select stay.

Saving removes the disabled entities from HA immediately.
Turn a switch back on to restore them. No restart needed.

### Use cases

- Add the Frame image to a Lovelace dashboard to mirror a display in HA.
- Set Active dashboard from an automation. For example, show `home_arrival` on the kitchen Inky when the front door opens.
- Turn the Weekend lineup on Friday evening and off Sunday night.
  Turn Automation off while guests stay to stop overnight rotation.
- Set Hold to 120 minutes when a meeting starts to keep the agenda visible.
  Use Pause to stop automated updates while guests use a display.
- Use Input events as triggers. A `swipe` can toggle a light; a `button` named `refresh` can rerun a script.
- Send "Bins tonight" at 20:00 with `notify.send_message`.
  Clear it with Refresh now or the next rotation step.
- Alert when Online turns off or Low battery turns on. Firmware updates also appear in HA Settings.
- Use Busy to make other automations wait until the current push finishes.

### Per-display fan-out vs. hub

The hub's Active dashboard sends to every device bound to that page.
A display's Dashboard select sends to that display alone. They work independently.

## Webhook push from HA

An HA RESTful command can call Tesserae's webhook to send a dashboard without a broker.

```yaml
# configuration.yaml
rest_command:
  tesserae_push:
    url: "http://tesserae.local:8765/api/v1/push"
    method: POST
    headers:
      Authorization: "Bearer !secret tesserae_webhook_token"
      Content-Type: "application/json"
    payload: '{"page_id": "{{ page }}"}'
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

## Troubleshooting

- Entities missing: check that Tesserae and HA use the same broker address and credentials.
  HA's MQTT integration must be installed and connected under **Settings → Devices & Services → MQTT**.
- Blank Ingress page: read the App's Log tab. A missing broker host or wrong port is a common cause.
  Fix it in the App's Configuration tab; Tesserae's Settings cannot change Ingress setup.
- Login page inside an HA dashboard: `SameSite=Lax` cookies do not work across different origins in an embedded page.
  A Webpage card at `http://tesserae.local:8765` can cause this.
  Open Tesserae in its own tab, or use the HA App's Ingress on HA's own address.
- Buttons work but nothing sends: read the hub's Last error sensor. It reports the original error for:
    - An offline broker
    - No bound device
    - Rendering busy for over a minute
    - Too many waiting commands
