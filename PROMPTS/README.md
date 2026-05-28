# Client-side prompts

Self-contained briefs you can hand to a coding assistant (Claude
Code, Cursor, etc.) to build the firmware / listener on the device
that paints your e-ink panel. Each prompt is independent — pick the
one that matches your hardware.

| File | Hardware | Stack |
|---|---|---|
| [pi_client.md](pi_client.md) | Raspberry Pi + Pimoroni Inky panel | Python, `paho-mqtt`, `inky` |
| [esp32_client.md](esp32_client.md) | ESP32 + Waveshare E6 / similar | Arduino, `PubSubClient`, `GxEPD2` |

Both contain:
- The exact MQTT topics to subscribe / publish on
- The payload format (Pi-PNG, Pi-bin, ESP32-bin)
- A working code skeleton you can build on
- A verification checklist

## Topic-prefix convention

Tesserae devices are identified by a topic prefix — `pi` for the
default Pi device, `esp32` for the default ESP32. Custom devices
(added via Settings → Devices → Add device) can pick their own
prefix (e.g. `pi-kitchen`, `esp32-hallway`). Make sure your firmware
subscribes to the right one — it's the single string you'll change
when you wire up a second display of the same kind.

## Update briefs

If you already have a working client from an earlier version, these
briefs walk a coding assistant through retrofitting it for the named-
device topic convention (no breaking change to the default prefix —
old clients keep working with `pi` / `esp32`):

| File | Hardware |
|---|---|
| [updates/pi_bin_multihead.md](updates/pi_bin_multihead.md) | tesserae-pi-bin-client → device_id in install script |
| [updates/pi_png_multihead.md](updates/pi_png_multihead.md) | tesserae-pi-png-client → device_id in install script |
| [updates/esp32_multihead.md](updates/esp32_multihead.md) | esp32-inky-dash-client → device_id via captive portal + always-on settings page |
