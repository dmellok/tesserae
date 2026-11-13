# Companion Bluetooth status observations

Companion reads a display's mode, battery percentage and firmware over an authenticated Bluetooth connection.
It sends the readings to its paired server.

Settings shows the last confirmed mode. Newer phone readings update the battery
and firmware fields, with their Bluetooth source and collection time.

## Reporting a reading

Before sending a reading, check:

- The server advertises `device_mode_observations` for mode readings.
- It also advertises `device_status_observations` for optional battery and firmware readings.
- The paired Companion token has `device_setup:write`.
- The display is registered.

Send `PUT /api/app/v1/devices/{device_id}/mode-observation` with a JSON body:

```json
{
  "mode": "bluetooth",
  "observed_at": "2026-01-02T03:04:05Z",
  "source": "ios_ble",
  "battery": {"percent": 65, "observed_at": "2026-01-02T03:04:06Z"},
  "firmware": {"version": "0.9.11", "observed_at": "2026-01-02T03:04:05Z"}
}
```

Success returns `204`. Readings must meet these rules:

| Field | Rule |
| --- | --- |
| Mode | `bluetooth` or `wifi` |
| Each timestamp | RFC 3339 with a timezone; no more than five minutes in the future |
| `source` | Client-defined identifier, 1 to 32 ASCII characters; matches `[a-z][a-z0-9_]{0,31}` |
| Battery percentage | Integer from 0 to 100 |
| Firmware | Nonempty version string, at most 128 characters, without control characters |

After the initial lowercase letter, the source can contain lowercase letters, digits or underscores.

iOS uses `ios_ble`. Other clients can use `android_ble` or `web_ble` without a server update.

The server saves and returns the original identifier.

Each reading uses its own observation time. A delayed retry cannot replace a
newer value; leaving out a reading keeps the saved one.

`GET /api/app/v1/devices` includes the saved `mode_observation`
for clients with `devices:read`.

## Meaning of the displayed status

Settings uses a newer Wi-Fi heartbeat reading over an older Bluetooth reading.
The last-confirmed mode appears only after a valid observation.

Reports do not change heartbeat freshness, reachability or configured transport.
Quiet hours and push scheduling stay unchanged.

A successful report means the server saved the reading. It sends no command to the display.
