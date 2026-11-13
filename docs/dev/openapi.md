# OpenAPI spec & SDK generation

Use `schema/openapi.yaml` to build scripts, integrations or automation clients.
Code generators can turn this file into client libraries (SDKs) for any popular language.

The spec is OpenAPI 3.0.3, validated on every push.

[:material-file-code: View the raw spec on GitHub](https://github.com/dmellok/tesserae/blob/main/schema/openapi.yaml){ .md-button }
[:material-open-in-new: Open in Swagger Editor](https://editor.swagger.io/?url=https%3A%2F%2Fraw.githubusercontent.com%2Fdmellok%2Ftesserae%2Fmain%2Fschema%2Fopenapi.yaml){ .md-button .md-button--primary target="_blank" }
[:material-book-open-variant: Open in Redoc](https://redocly.github.io/redoc/?url=https%3A%2F%2Fraw.githubusercontent.com%2Fdmellok%2Ftesserae%2Fmain%2Fschema%2Fopenapi.yaml){ .md-button target="_blank" }

## What's covered

The spec covers four parts of the API:

| Surface | Paths | What it's for |
| --- | --- | --- |
| **Native device API** | `/api/v1/device/{frame,status,log,discover,register}` | Battery + always-on panels that prefer HTTP polling over MQTT. Per-device bearer-token auth, bootstrapped by pairing code or MAC-based discovery. The companion to MQTT, same envelope shape on both transports. |
| **TRMNL-compatible BYOS** | `/api/display`, `/api/setup`, `/api/log`, `/api/log/level` | Stock TRMNL / Terminus firmware. Point a TRMNL device at a Tesserae host and it self-provisions on first boot. |
| **Webhook push** | `/api/v1/push`, `/api/v1/push/image` | External automation (Home Assistant beyond MQTT, n8n, Stream Deck, GitHub Actions, cron + curl). One global token; per-device quiet hours are honoured. `/push` renders a saved dashboard; `/push/image` sends a single image straight to named displays, for when there's no dashboard behind the frame. |
| **Render artifacts** | `/renders/{filename}`, `/preview/{id}.png`, `/mirror/{id}` | The binary frames clients fetch after being told their URL through one of the above. The `/mirror/<id>` endpoint serves a browser-friendly HTML auto-refresh page so an old iPad or Kindle browser can stand in as a panel. |

Plus `/healthz` for health checks.

### Pushing one image from a script

Generate a token under **Settings → System → Webhook**, then run:

```sh
curl -sS -X POST http://tesserae.local:8765/api/v1/push/image \
  -H "Authorization: Bearer $TESSERAE_TOKEN" \
  -F image=@photo.jpg \
  -F device_ids=hallway \
  -F fit=fill
```

```json
{"status": "sent", "sent": ["hallway"], "quiet": [], "failed": []}
```

`device_ids` is required and takes a comma-separated list or a repeated
field. Quiet hours are honoured, so a display inside its window comes
back under `quiet` with a 202 rather than being reported as sent. Add
`-F override_quiet_hours=true` when the image is worth waking a panel
for anyway.

EXIF orientation is applied, so phone photos match their camera-roll orientation.

- `-F rotate=auto` turns portrait images a quarter turn to fill landscape panels, and the reverse.
- `-F rotate=90|180|270` rotates clockwise by that amount.
- Omit rotation to keep the image as shot, which suits images containing text.

For a panel mounted the other way around, set **Rotation** under Settings → Devices.
This also rotates dashboards.

With the Home Assistant App, call the instance's port directly.
The ingress URL needs a browser login session.

## What's deliberately not in the spec

The web UI uses Jinja templates and has no stable external contract.
This includes the page editor, settings, composer, plugin browser and theme browser.

Editor-only JSON endpoints are also excluded: live preview, condition tester,
battery history series and event SSE stream. They change between releases
without notice; if you depend on them you're on your own.

If you need machine access to something the spec doesn't cover,
open an issue with the use case and we'll consider promoting it.

## Generating a client

Any generator that supports OpenAPI 3.0.3 works. Examples follow for
openapi-generator and kiota.

### openapi-generator (40+ language targets)

Install with pip. The generator needs Java: use a system JDK on your PATH,
or install a bundled runtime with the `jdk4py` extra:

```sh
# Bundled JDK (works without any system Java setup):
pip install "openapi-generator-cli[jdk4py]"

# Or, if you already have Java installed:
pip install openapi-generator-cli
```

The openapi-generator output covers every spec feature and includes its own runtime helpers.
It suits desktop or server clients. On memory-limited microcontrollers
running CircuitPython or MicroPython, a small handwritten client is likely a better fit.

```sh
# Python
openapi-generator-cli generate \
    -i https://raw.githubusercontent.com/dmellok/tesserae/main/schema/openapi.yaml \
    -g python \
    -o sdk/python \
    --package-name tesserae_client

# TypeScript (fetch)
openapi-generator-cli generate \
    -i https://raw.githubusercontent.com/dmellok/tesserae/main/schema/openapi.yaml \
    -g typescript-fetch \
    -o sdk/typescript

# Go
openapi-generator-cli generate \
    -i https://raw.githubusercontent.com/dmellok/tesserae/main/schema/openapi.yaml \
    -g go \
    -o sdk/go \
    --package-name tesserae

# Rust
openapi-generator-cli generate \
    -i https://raw.githubusercontent.com/dmellok/tesserae/main/schema/openapi.yaml \
    -g rust \
    -o sdk/rust
```

Full target list: `openapi-generator-cli list`.

### kiota (Microsoft, smaller code, fewer languages)

```sh
kiota generate \
    -d https://raw.githubusercontent.com/dmellok/tesserae/main/schema/openapi.yaml \
    -l python \
    -o sdk/python \
    -c TesseraeClient
```

### Browsing the spec interactively

The buttons above load the live spec from `raw.githubusercontent.com` into your browser:

- Swagger Editor has request builders and forms to try calls.
- Redoc is a read-only reference with a three-pane layout.

Neither needs installation or setup.

## Authentication at a glance

The spec declares six security schemes spanning the four surfaces.
Each operation tags which ones it accepts.

| Scheme | Where it's sent | Used by |
| --- | --- | --- |
| `DeviceToken` | `Authorization: Bearer <token>` | All `/api/v1/device/*` except `/discover` (unauthenticated) and `/register` (which uses the pairing code). |
| `PairingCode` | `X-Pairing-Code: <6-digit-code>` | `/api/v1/device/register` only. Single-use, 10-minute TTL. |
| `TrmnlAccessToken` | `access-token: <token>` | TRMNL BYOS endpoints. Legacy header name; the bearer header is also accepted. |
| `TrmnlAuthBearer` | `Authorization: Bearer <token>` | TRMNL BYOS endpoints. |
| `WebhookBearer` | `Authorization: Bearer <token>` | `/api/v1/push`. Global token, generated under Settings -> System -> Webhook. |
| `WebhookToken` | `X-Tesserae-Token: <token>` | Same global webhook token, alternate header for tools that can't customise `Authorization`. |

### Bootstrap flow for a native REST client

A device needs a token (`device_token` in the discover and register
responses) before it can fetch frames. The default flow supplies it
automatically:

1. Firmware boots, has no token, POSTs `/api/v1/device/discover`
   with its MAC.
2. Device appears in the **Discovered** strip on Settings →
   Devices.
3. Admin clicks **Register** once.
4. On the firmware's next discover poll (default 30 s later), the
   server recognises the MAC and returns the token in the response.
5. Firmware persists the token in flash and sends it as
   `Authorization: Bearer <token>` on every subsequent call. The
   user never sees it.

For sealed appliances, BLE provisioning or kiosk mode, the admin may be absent at boot.
Use a pairing code to get a token without an admin click:

1. Create a 6-digit code under Settings → Devices → Add device (REST → **Issue pairing code**).
2. Enter it in the firmware setup form.
3. Firmware POSTs `/api/v1/device/register` with `X-Pairing-Code: <code>` instead of `/discover`.

## Worked example: trigger a webhook push from cron

```sh
TOKEN="..."  # from Settings -> System -> Webhook
curl -X POST https://tesserae.local:8765/api/v1/push \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"page_id": "hallway"}'
```

- `200 sent`: published.
- `202 quiet`: all bound devices are in quiet hours, so the push was skipped. Callers can use the status without parsing the body.
- `404 not_found`: wrong page id.
- `409 busy`: a previous push for this page is still running.

## Versioning

The spec's `info.version` tracks the Tesserae release that produced
it. The HTTP surface itself is versioned under `/api/v1/...`, so
within v1 the contract is additive: new fields can appear in
responses, new endpoints can be added, but existing fields keep
their shape. Breaking changes require a `/api/v2/...` and a
deprecation window on `/api/v1/...`.

The TRMNL BYOS endpoints (`/api/display`, `/api/setup`, `/api/log*`)
follow Terminus's published contract; if Terminus ships a breaking
change to its protocol, Tesserae will match it (we are the
ecosystem-compatible end, not the upstream).

## Cross-references

* [Client protocol spec](client-protocol.md): the human-readable
  companion to this spec. Covers framing, auth handshakes, MQTT
  topics, and example payload exchanges in narrative form.
* [REST transport (no broker)](../install/rest-transport.md):
  walk-through of running a REST device end-to-end, intended for
  someone installing a panel without an MQTT broker on the LAN.
* [Architecture](architecture.md): how the renderer + push pipeline
  produce the artifacts the spec points at.

## Reporting an issue with the spec

If a generated client's field is wrong, missing or the wrong type,
[open a GitHub issue](https://github.com/dmellok/tesserae/issues/new). Include:

- The language target.
- The generator and version.
- A snippet showing expected and actual results.

The spec defines the contract. A server that disagrees with it has a bug.
