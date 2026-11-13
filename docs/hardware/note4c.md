# ZecTrix NOTE4C Devkit

The NOTE4C Devkit is a battery-powered, four-colour e-paper screen.
Community firmware by [@varanu5](https://github.com/varanu5) supports
Tesserae REST and Cloud relay.

> **For the four-colour NOTE4C Devkit only. Do not install this firmware on the grayscale NOTE4 variant.**

## Firmware and setup

- [Firmware and instructions](https://github.com/varanu5/zectrix-note4c-devkit-tesserae-client)
- [Web installer](https://note4ctesserae.pages.dev) (desktop Chrome or Edge)

We strongly recommend backing up the original firmware before installing.

1. Follow the firmware repository's flashing guide.
2. Open the device's Wi-Fi setup portal. Choose REST or Cloud relay and enter its connection details:
    - REST needs a server address the device can reach.
    - Cloud relay needs the relay URL and pairing code from your relay setup.
3. Select **ZecTrix NOTE4C Devkit** as the device type in Tesserae.
4. After pairing, assign a page or lineup.

Hold the Front button for 20 seconds to reopen setup.

## Display and sleep settings

| Property | Value |
|---|---|
| Resolution | 400 × 300, landscape |
| Colours | Black, white, red and yellow (`bwry_4`) |
| Renderer | `esp32_bin`, packed 2 bits per pixel (30,000 bytes) |
| Device kind | `note4c_client` |
| Default sleep interval | 15 minutes (900 seconds) |
| Configurable interval | 30 seconds to 7 days |

Set the sleep interval in the server's device settings. It overrides the default and takes effect at the next server contact.

Press Front briefly to wake the device for a check.

The firmware handles refresh modes and battery protection.
The device reports battery voltage, estimated charge, Wi-Fi signal and firmware version.
