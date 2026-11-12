# Quickstart: Fraimic Canvas

A Fraimic is a framed colour e-paper canvas (Spectra 6) with a local REST API. Tesserae drives it directly over your network: no Fraimic account, app, cloud or firmware change. It works with the 31.5" Large Canvas and the 13.3" frame, on firmware 0.2.31 or later.

On battery the frame spends most of its time in deep sleep, where nothing can reach it. It wakes on a tap, while charging, with **Keep Device Awake** on, or at the times in its own wake schedule. Tesserae sets that schedule for you, and at each wake it uploads the latest picture and puts the frame back to sleep.

## 01 — Give the frame a fixed address

Find the frame's IP address on its portal at `http://fraimic.local/info` (tap the frame first so it is awake), or in your router's list of clients. Then reserve that address for it in your router (often called a DHCP reservation or static lease). Tesserae reaches the frame by its address, and right after a wake `fraimic.local` can take a few seconds to answer, so the IP address is the reliable choice.

To check the firmware, open `http://FRAME_IP/api/info` while the frame is awake: `firmware_version` should be 0.2.31 or later. Older firmware has no wake schedule, so pictures only arrive while the frame is awake for some other reason.

## 02 — Add the frame in Tesserae

1. **Settings → Devices → Add device → Fraimic**.
2. Enter the frame's address, a device id and a name, and add it.
3. The panel defaults to the 31.5" Large Canvas, 1440×2560 hung upright. For the 13.3", set the panel to 1200×1600 on the device card. Hung on its side, pick landscape; if the picture then comes out upside down, turn on the panel's flip.
4. **Tap the frame** so it is awake. Within a minute Tesserae writes its wake schedule to it and sends the current picture.

## 03 — Choose when it wakes

The device card's **Wake schedule** sets when the frame wakes for a new picture:

| Setting | What the frame does |
|---|---|
| On a regular interval (default: every hour) | Wakes every 5, 10, 15, 20 or 30 minutes, or every 1, 1½, 2, 3, 4, 6, 8, 12 or 24 hours. **Counted from** sets where the interval starts (midnight by default) |
| At set times each day | Wakes at the times you list, such as `06:00, 07:30, 18:00`. As many as you like |
| When the picture is due to change | Wakes when a lineup step, schedule or page refresh is due to change the picture, and otherwise every 6 hours (**And at least every**) |
| Leave the frame's own schedule | Tesserae doesn't change it (for one you set from Home Assistant, say) and uses the wakes the frame reports |

Times are in the app's timezone. The frame keeps its own clock, which doesn't follow daylight saving; Tesserae converts for it.

The frame's own schedule only goes down to hourly, or four times a day. For anything else, Tesserae leads it along: at each wake it tells the frame its next wake (and the three after, in case one is missed). This needs the Tesserae server running. If the server is away the frame keeps waking at the last times it was given, once a day each, and picks up again at the first wake the server answers. An hourly or slower interval from midnight and up to four set times use the frame's own schedule and carry on without the server, as long as quiet hours aren't set for the frame.

Wakes are planned around the rest of Tesserae:

- **Quiet hours.** The frame doesn't wake during its quiet hours (Settings → Server, or the device's own). The last wake before them sets the first wake after.
- **Smart sync.** Tesserae knows when the frame wakes next, so a schedule or rotation with smart sync renders just before the wake instead of on its own clock. The frame is trusted for this after three wakes on time.
- **A page's own interval.** A dashboard that declares a wake interval overrides **Wake every** while it is on the frame, down to every 15 minutes.
- **Low battery.** With **Wake less often on a low battery** on (the default), interval wakes are twice as far apart below 20% and four times below 10%.

Every wake costs some battery, and every new picture is a full redraw of the panel: a few wakes a day cost little, hourly drains the battery noticeably faster, and wakes minutes apart suit a frame that is near its charger. A changed schedule reaches the frame the next time it is awake, so tap it after saving.

A scheduled wake needs the internet: the frame checks the time online each time it wakes. If the internet is down, its scheduled wakes pause until it is back.

## 04 — Bind a dashboard

Bind a page, schedule or rotation to the frame like any other device. Tesserae dithers each frame to the panel's six inks and sends it as the `.bin` file the frame takes. A page change, a manual **Send** or a Home Assistant trigger shows at the frame's next wake. A frame that is awake anyway (charging, Keep Device Awake, just tapped) gets it within a few seconds.

Each upload takes 20 to 35 seconds, and the frame takes about a minute to draw it. After a scheduled wake Tesserae puts the frame back to sleep once it has its picture and its next wake (it finishes drawing first). With nothing new to send, that is about 40 seconds after it woke: put down sooner, the frame doesn't set its next alarm. Turn off **Sleep straight after a new picture** to let it wait out its two-minute window instead. A frame you tapped awake is never put to sleep.

**On a tap** can make a tap a Next button: the frame wakes, and the display moves to the next page of its lineup or rotation. **Redraw a picture that hasn't changed** draws the same picture again after a day or a week on the glass, to clear ghosting.

The device card shows the battery level, Wi-Fi signal and firmware the frame reports on each wake, and when it wakes next. A wake the frame doesn't answer (flat battery, off the network) is logged and counted in its status as `missed_wakes`.

## Things to know

- **Fraimic's own albums still arrive.** Pictures from the Fraimic web portal or an album are picked up on a scheduled wake too and would replace the dashboard until the next one. Leave the frame without an album while Tesserae drives it.
- **Plugged in.** While the battery is charging the frame stays awake and ignores its schedule, and Tesserae sends new pictures as they come. With a full battery it sleeps and follows the schedule again.
- **No password.** The frame's API is open to anything on your network, as Fraimic designed it.

## Stopping

Delete the device in Tesserae. The frame keeps waking on its schedule, which costs battery: to turn the schedule off, tap the frame and run

```sh
curl -X POST -H "Content-Type: application/json" -d '{"enabled": false}' http://FRAME_IP/api/wake-schedule
```
