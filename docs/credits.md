# Credits

Tesserae is built on top of generously-licensed open source. If anything
here is wrong or someone's missing, please [open an issue](https://github.com/dmellok/tesserae/issues).

## Frontend assets bundled in this repo

| Project | Used for | Version | Licence |
|---|---|---|---|
| [Phosphor Icons](https://phosphoricons.com/) | Admin UI iconography (6 weights, regular, bold, fill, duotone, light, thin) | 2.x | MIT |
| [Chart.js](https://chartjs.org/) | Finance / weather / forecast / stats charts | 4.4.0 | MIT |
| [`widget-bauhaus.css`](https://github.com/dmellok/tesserae/blob/main/static/style/widget-bauhaus.css) + [`widget-bauhaus-wx.css`](https://github.com/dmellok/tesserae/blob/main/static/style/widget-bauhaus-wx.css) | Shared widget design system: refined title bars, `--c-*` semantic tokens, `--wx-*` decorative tokens for the weather/sky family |, | MIT (this repo) |

## Bundled fonts

All SIL Open Font License. Used in widgets, themes, and the admin UI;
live in `plugins/fonts_core/static/`.

| Font | Designer | Licence |
|---|---|---|
| [Anton](https://fonts.google.com/specimen/Anton) | Vernon Adams | OFL |
| [Archivo](https://fonts.google.com/specimen/Archivo) | Omnibus-Type | OFL |
| [Archivo Black](https://fonts.google.com/specimen/Archivo+Black) | Omnibus-Type | OFL |
| [Atkinson Hyperlegible](https://www.brailleinstitute.org/freefont) | Braille Institute | OFL |
| [Bebas Neue](https://fonts.google.com/specimen/Bebas+Neue) | Ryoichi Tsunekawa | OFL |
| [Crimson Pro](https://fonts.google.com/specimen/Crimson+Pro) | Sebastian Kosch | OFL |
| [DM Serif Display](https://fonts.google.com/specimen/DM+Serif+Display) | Colophon Foundry | OFL |
| [IBM Plex Sans / Serif / Mono](https://www.ibm.com/plex/) | IBM | OFL |
| [Inter](https://rsms.me/inter/) | Rasmus Andersson | OFL |
| [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | JetBrains | OFL |
| [Lora](https://fonts.google.com/specimen/Lora) | Cyreal | OFL |
| [Manrope](https://manropefont.com/) | Mikhail Sharanda | OFL |
| [Outfit](https://fonts.google.com/specimen/Outfit) | Smith | OFL |
| [Space Grotesk](https://fonts.google.com/specimen/Space+Grotesk) | Florian Karsten | OFL |
| [Space Mono](https://fonts.google.com/specimen/Space+Mono) | Colophon Foundry | OFL |

## Upstream protocols and reference clients

- **[TRMNL](https://usetrmnl.com/)** - the team behind the TRMNL
  devices and the open BYOS protocol Tesserae's HTTP-pull path
  implements. Tesserae's `trmnl_png` renderer, `/api/display`,
  `/api/setup`, and `/api/log` endpoints exist because TRMNL
  published a documented protocol you can host yourself. The
  rotations feature is Tesserae's take on TRMNL's playlists concept.
- **[Terminus](https://github.com/usetrmnl/byos_laravel)** - TRMNL's
  Laravel reference BYOS server. I aligned Tesserae's HTTP envelopes
  + access-token flow against Terminus to make sure existing TRMNL
  clients (firmware, KOReader plugin) drop in without firmware
  changes.
- **[TRMNL BYOS specification](https://help.trmnl.com/en/articles/9510536-bring-your-own-server)** -
  usetrmnl.com's documented protocol that the `trmnl_png` renderer +
  `/api/display` blueprint implement.
- **[KOReader trmnl-display plugin](https://github.com/koreader/koreader)** -
  Lua plugin running on jailbroken Kindles that paints frames over the
  TRMNL BYOS protocol. The Kindle Paperwhite 2 testing on Tesserae's
  TRMNL HTTP path leans entirely on this work.

## Python dependencies

Pip dependencies aren't enumerated here, their licences travel with the
wheels and `pyproject.toml` is the authoritative source. The main ones,
for context:

- [Flask](https://flask.palletsprojects.com/), web framework (BSD)
- [Pillow](https://python-pillow.org/), image rendering pipeline (HPND)
- [paho-mqtt](https://www.eclipse.org/paho/), MQTT client (EPL / EDL)
- [amqtt](https://github.com/Yakifo/amqtt), embedded broker (MIT)
- [Playwright](https://playwright.dev/), headless Chromium for the
  webpage / screenshot widget (Apache 2.0)
- [waitress](https://docs.pylonsproject.org/projects/waitress/) -
  production WSGI server (ZPL)
- [pydantic](https://docs.pydantic.dev/), schema models (MIT)
- [zeroconf](https://github.com/python-zeroconf/python-zeroconf), mDNS
  advertiser (LGPL 2.1)
