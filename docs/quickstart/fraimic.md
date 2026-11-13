# Quickstart: Fraimic Canvas

Use Tesserae to send pictures directly to a Fraimic Spectra 6 colour frame over your network. No Fraimic account or app is needed. You don't need its cloud service or a firmware change.

!!! info "10% off with TESSERAE10"
    The code **TESSERAE10** takes 10% off the [Standard Canvas](https://fraimic.com/products/standard-canvas) (13.3") or the [Large Canvas](https://fraimic.com/products/large-canvas) (31.5") at fraimic.com. Fraimic supplied the Large Canvas this guide was written on free of charge, and Tesserae earns a commission on sales made with the code. What is written here is my own experience with the frame.

A sleeping frame can't be reached over the network. It wakes when tapped, while charging, with **Keep Device Awake** on, or at scheduled times.

## 01 — Give the frame a fixed address

Tap the frame to wake it. Find its IP address at `http://fraimic.local/info` or in your router's client list.

Reserve that address in your router, using a DHCP reservation or static lease. Use the IP address in Tesserae. After waking, `fraimic.local` can take a few seconds to respond.

While the frame is awake, open `http://FRAME_IP/api/info`. Check that `firmware_version` is 0.2.31 or later. Older firmware has no wake schedule and only receives pictures while already awake.

## 02 — Add the frame in Tesserae

1. Open **Settings → Devices → Add device → Fraimic**.
2. Pick the **Model**:

    | Frame | Resolution (portrait) |
    |---|---|
    | 31.5" Large Canvas | 1440×2560 |
    | 13.3" Standard Canvas | 1200×1600 |

3. Enter the frame's address. Choose a device id and name, then add it.
4. Choose landscape on the device card if the frame hangs sideways. If the image is upside down, turn on the panel's flip.
5. Tap the frame to wake it. Within a minute, Tesserae sets its wake schedule and sends the current picture.

## 03 — Choose when it wakes

The device card's **Wake schedule** sets when the frame wakes for a new picture:

| Setting | What the frame does |
|---|---|
| On a regular interval (default: every hour) | Wakes every 5, 10, 15, 20 or 30 minutes, or every 1, 1½, 2, 3, 4, 6, 8, 12 or 24 hours. **Counted from** sets where the interval starts (midnight by default) |
| At set times each day | Wakes at the times you list, such as `06:00, 07:30, 18:00`. As many as you like |
| When the picture is due to change | Wakes when a lineup step, schedule or page refresh is due to change the picture, and otherwise every 6 hours (**And at least every**) |
| Leave the frame's own schedule | Tesserae doesn't change it (for one you set from Home Assistant, say) and uses the wakes the frame reports |

Times use the app's timezone. The frame doesn't follow daylight saving, so Tesserae converts times for it.

The frame can manage hourly or slower intervals, or up to four fixed times a day. These schedules work without Tesserae, provided intervals start at midnight and the frame has no quiet hours.

For other schedules, Tesserae sends the next four wake times at each check-in. Keep the server running. If it stops, the frame repeats those times daily until the server answers a wake.

Other settings affect when the frame wakes:

- Quiet hours: set these under Settings → Server or on the device. The frame skips them and wakes after they end.
- Smart sync: a schedule or rotation draws its next image just before the frame wakes. This starts after three on-time wakes.
- A page's own interval overrides **Wake every** while displayed, down to every 15 minutes.
- **Wake less often on a low battery** is on by default. Intervals double below 20% battery and quadruple below 10%.

Every wake uses battery, and every new picture redraws the whole panel:

- A few wakes a day use little battery.
- Hourly wakes drain it noticeably faster.
- Wakes minutes apart suit a frame near its charger.

Schedule changes reach the frame on its next wake. Tap it after saving to apply them now.

Scheduled wakes need internet access to check the time. They pause during an internet outage and resume when it returns.

## 04 — Bind a dashboard

Bind a page, schedule or rotation to the frame.

Tesserae converts the image to its six inks and sends a `.bin` file.

Changes appear on the next wake, including **Send** and Home Assistant triggers. An already-awake frame gets them within a few seconds.

An upload takes 20 to 35 seconds. Drawing takes about a minute.

After a scheduled wake, Tesserae sends the picture and next wake time, then puts the frame to sleep after drawing. With no new picture, it waits about 40 seconds. If the frame is put to sleep sooner than that, it won't set its next alarm.

Turn off **Sleep straight after a new picture** to let the frame finish its two-minute window. Tesserae never puts a tapped-awake frame to sleep.

Turn on **On a tap** to advance the lineup or rotation when you tap the frame.

**Redraw a picture that hasn't changed** redraws the same image after a day or week to clear ghosting.

The device card shows battery level, Wi-Fi signal and firmware from each wake. It also shows the next wake time.

Unanswered wakes, such as a flat battery or lost network connection, are logged and counted as `missed_wakes`.

## Things to know

- Leave the frame without a Fraimic album. Portal and album pictures also arrive on scheduled wakes and replace the dashboard until the next image.
- While charging, the frame stays awake and Tesserae sends pictures as they change. Once fully charged, it sleeps on its schedule again.
- The frame's API has no password. Anything on your network can access it.

## Stopping

Delete the device in Tesserae. Its wake schedule keeps running and using battery. To stop it, tap the frame and run:

```sh
curl -X POST -H "Content-Type: application/json" -d '{"enabled": false}' http://FRAME_IP/api/wake-schedule
```
