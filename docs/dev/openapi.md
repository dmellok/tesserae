# OpenAPI spec & SDK generation

`schema/openapi.yaml` describes Tesserae's machine-facing HTTP API
in a single file that off-the-shelf code generators can fan out into
client SDKs for any popular language. If you want to drive a
Tesserae instance from a script, an integration, or an automation
runtime, this is the contract.

The spec is OpenAPI 3.0.3, validated on every push.

[:material-file-code: View the raw spec on GitHub](https://github.com/dmellok/tesserae/blob/main/schema/openapi.yaml){ .md-button }
[:material-open-in-new: Open in Swagger Editor](https://editor.swagger.io/?url=https%3A%2F%2Fraw.githubusercontent.com%2Fdmellok%2Ftesserae%2Fmain%2Fschema%2Fopenapi.yaml){ .md-button .md-button--primary target="_blank" }
[:material-book-open-variant: Open in Redoc](https://redocly.github.io/redoc/?url=https%3A%2F%2Fraw.githubusercontent.com%2Fdmellok%2Ftesserae%2Fmain%2Fschema%2Fopenapi.yaml){ .md-button target="_blank" }

## What's covered

Four surfaces, intentionally narrow so the contract stays small and
versionable.

| Surface | Paths | What it's for |
| --- | --- | --- |
| **Native device API** | `/api/v1/device/{frame,status,log,discover,register}` | Battery + always-on panels that prefer HTTP polling over MQTT. Per-device bearer-token auth, bootstrapped by pairing code or MAC-based discovery. The companion to MQTT, same envelope shape on both transports. |
| **TRMNL-compatible BYOS** | `/api/display`, `/api/setup`, `/api/log`, `/api/log/level` | Stock TRMNL / Terminus firmware. Point a TRMNL device at a Tesserae host and it self-provisions on first boot. |
| **Webhook push** | `/api/v1/push` | External automation (Home Assistant beyond MQTT, n8n, Stream Deck, GitHub Actions, cron + curl). One global token; per-device quiet hours are honoured. |
| **Render artifacts** | `/renders/{filename}`, `/preview/{id}.png`, `/mirror/{id}` | The binary frames clients fetch after being told their URL through one of the above. The `/mirror/<id>` endpoint serves a browser-friendly HTML auto-refresh page so an old iPad or Kindle browser can stand in as a panel. |

Plus `/healthz` for liveness probes.

## What's deliberately not in the spec

The Tesserae web UI (the page editor, settings, composer, plugin
browse, themes browse) is rendered as Jinja templates and isn't a
stable external contract. Internal JSON endpoints used only by the
editor's own JavaScript (live preview, condition tester, battery
history series, event SSE stream) are out of scope too. They change
between releases without notice; if you depend on them you're on
your own.

If you need machine access to something the spec doesn't cover,
open an issue with the use case and we'll consider promoting it.

## Generating a client

The spec is plain OpenAPI 3.0.3, so any generator that supports the
format works. Two popular options:

### openapi-generator (40+ language targets)

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

The two buttons at the top of this page open the live spec in
**Swagger Editor** (try-it-out forms, request builder) and **Redoc**
(read-only reference, three-pane layout) with the file pre-loaded
from `raw.githubusercontent.com`. Both render the spec straight in
the browser; no setup, nothing installed.

## Authentication at a glance

The spec declares six security schemes spanning the four surfaces.
Each operation tags which ones it accepts.

| Scheme | Where it's sent | Used by |
| --- | --- | --- |
| `DeviceToken` | `Authorization: Bearer <token>` | All `/api/v1/device/*` except `/discover` (unauthenticated) and `/register` (which uses the pairing code). |
| `PairingCode` | `X-Pairing-Code: <6-digit-code>` | `/api/v1/device/register` only. Single-use, 15-minute TTL. |
| `TrmnlAccessToken` | `access-token: <token>` | TRMNL BYOS endpoints. Legacy header name; the bearer header is also accepted. |
| `TrmnlAuthBearer` | `Authorization: Bearer <token>` | TRMNL BYOS endpoints. |
| `WebhookBearer` | `Authorization: Bearer <token>` | `/api/v1/push`. Global token, generated under Settings -> Server -> App. |
| `WebhookToken` | `X-Tesserae-Token: <token>` | Same global webhook token, alternate header for tools that can't customise `Authorization`. |

### Bootstrap flow for a native REST client

A device needs an `access_token` before it can fetch frames. **You
never type one in.** The default flow is:

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

For environments where the admin can't be at the UI when the
device boots, sealed appliances, BLE provisioning, kiosk mode,
there's a fallback: pre-mint a 6-digit pairing code under
Settings → Devices → Pair, type it into the firmware setup form,
and the firmware POSTs `/api/v1/device/register` with
`X-Pairing-Code: <code>` instead of `/discover`. Same end state, no
admin click required. This path is the exception, not the default.

## Worked example: trigger a webhook push from cron

```sh
TOKEN="..."  # from Settings -> Server -> App
curl -X POST https://tesserae.local:8765/api/v1/push \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"page_id": "hallway"}'
```

Outcome codes are deliberate. `200 sent` means it was published.
`202 quiet` means all bound devices are in quiet hours and the push
was deliberately skipped (the caller can branch on that without
parsing the body). `404 not_found` means the page id is wrong.
`409 busy` means a previous push for the same page is still in
flight.

## Versioning

The spec's `info.version` tracks the Tesserae release that produced
it. The HTTP surface itself is versioned under `/api/v1/...`, so
within v1 the contract is additive: new fields can appear in
responses, new endpoints can be added, but existing fields keep
their shape. Breaking changes earn a `/api/v2/...` and a
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

If you generate a client and a field is wrong, missing, or has the
wrong type, open an issue on
[GitHub](https://github.com/dmellok/tesserae/issues/new) with the
language target, the generator + version, and a snippet of the
disagreement (expected vs. what came back). The spec is the source
of truth for the contract; if the live server disagrees with it,
that's a server bug.
