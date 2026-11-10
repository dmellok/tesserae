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
| Every few hours (default: every hour) | Wakes every 1, 1½, 2, 3, 4, 6, 8, 12 or 24 hours, counted from midnight |
| At set times each day | Wakes at up to four times, such as `06:00, 18:00` |
| Leave the frame's own schedule | Tesserae doesn't change it (for one you set from Home Assistant, say) and uses the wakes the frame reports |

The frame can't wake more often than hourly. Every wake costs some battery: a few a day cost little, and hourly drains the battery noticeably faster. A changed schedule reaches the frame the next time it is awake, so tap it after saving.

A scheduled wake needs the internet: the frame checks the time online each time it wakes. If the internet is down, its scheduled wakes pause until it is back.

## 04 — Bind a dashboard

Bind a page, schedule or rotation to the frame like any other device. Tesserae dithers each frame to the panel's six inks and sends it as the `.bin` file the frame takes. A page change, a manual **Send** or a Home Assistant trigger shows at the frame's next wake. A frame that is awake anyway (charging, Keep Device Awake, just tapped) gets it within a few seconds.

Each upload takes 20 to 35 seconds, and the frame takes about a minute to draw it. After a scheduled wake Tesserae puts the frame straight back to sleep once its picture is sent (it finishes drawing first), or at once when nothing has changed. Turn off **Sleep straight after a new picture** to let it wait out its two-minute window instead. A frame you tapped awake is never put to sleep.

The device card shows the battery level, Wi-Fi signal and firmware the frame reports on each wake, and when it wakes next.

## Things to know

- **Fraimic's own albums still arrive.** Pictures from the Fraimic web portal or an album are picked up on a scheduled wake too and would replace the dashboard until the next one. Leave the frame without an album while Tesserae drives it.
- **Plugged in.** While the battery is charging the frame stays awake and ignores its schedule, and Tesserae sends new pictures as they come. With a full battery it sleeps and follows the schedule again.
- **No password.** The frame's API is open to anything on your network, as Fraimic designed it.

## Stopping

Delete the device in Tesserae. The frame keeps waking on its schedule, which costs battery: to turn the schedule off, tap the frame and run

```sh
curl -X POST -H "Content-Type: application/json" -d '{"enabled": false}' http://FRAME_IP/api/wake-schedule
```
