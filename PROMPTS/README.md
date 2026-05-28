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
