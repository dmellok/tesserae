# Meural account API

The cloud path is unofficial. Its Cognito and OAuth client ids come from Netgear's app (via ha-meural); Netgear can change them or block requests, and the integration can stop working.

Sign-in and cloud single-image delivery have been verified against a real account and frame. Token renewal has been checked against the live service by the contributor. Native sleep schedule sync is not included; manage native schedules in the Meural app. Playback is handled as described under Cleanup failures.

Cloud deliveries have a per-device minimum interval (`cloud_interval_s`, default 300 seconds, range 60 to 86400). Intermediate renders are coalesced; only the latest image is delivered. The timestamp is saved in the frame journal and survives publisher and server restarts.

All routes require the normal administrator session and CSRF checks. Responses use
`Cache-Control: no-store`. Requests that change settings accept JSON unless noted.
Send `Accept: application/json` for JSON success responses on existing-device actions;
ordinary form submissions redirect to the device page.

## Account state

`GET /settings/meural/account` reads the pending Add device account.
`GET /settings/devices/<id>/meural/account` reads a saved device account.

Both return an `account` object:

```json
{
  "account": {
    "state": "signed_in",
    "account_label": "owner@example.com",
    "frames": [{"id": "101", "name": "Example frame"}],
    "chosen_frame": {"id": "101", "name": "Example frame"},
    "saved": false,
    "paused": false
  }
}
```

`state` is `signed_out`, `signing_in`, `signed_in`, or `session_expired`.
Show "Session expired, sign in again" for the last state. `chosen_frame` is null
until one is selected. `saved` is false for a pending account; it becomes true
when the chosen frame is stored on a device. Tokens and pending-account IDs are
never returned.

## Sign in before adding a device

1. Open `/settings/meural/login` in the same tab. This returns the sign-in page
   with a one-use state nonce and Netgear's public client configuration.
2. POST the existing browser callback to that same path: `state`, `trust_id`,
   `email`, and `cognito_access_token`. Passwords and verification codes go only
   to Netgear. Success returns `status`, `return_url`, and `account`.
3. POST `/settings/meural/frame` with `cloud_device_id`. The response contains
   the updated pending `account` and `status: "ok"`.
4. POST form data to `/settings/devices/add`: `kind=meural`, `id`, `name`, and
   `cloud_device_id`. The add consumes the pending account and redirects to the
   new device. Missing or expired sign-in does not create it: a request that asks for JSON gets a 400 with `error`, and the Add device form is sent back to the Devices page with the reason.

The account is bound to the browser's signed session cookie. Access and refresh
tokens are SecretBox-encrypted in server memory. It expires after 15 minutes,
is single-use, and is discarded by POST `/settings/meural/cancel` or
`/settings/meural/logout`. Opening the sign-in page preserves the pending account and selection. A completed new sign-in replaces it.
Typed device id and name can be preserved in browser session storage; keep
account tokens and pending IDs out of storage, URLs, and page HTML.

LAN-only add uses `delivery_method=local` and `host`; it needs no pending account.

## Frame swipes and thumbnails

Cloud delivery with a LAN host checks `GET /remote/get_gallery_status_json/` every five seconds. A changed `current_item` advances the current dashboard's item, or the lineup when that dashboard has one item. A lineup with nowhere to advance re-shows Tesserae's image. Sleep, quiet hours, pending delivery and ten seconds after a show suppress gestures; each confirmed show allows one action. External artwork is downloaded from the cloud device's signed `currentImage` URL and cached in the shared held-frame record. A matching Tesserae item uses its existing thumbnail. Screenshot item metadata identifies the item to advance from; older screenshots without it start at the first listed item. No host means no gesture detection. A confirmed swipe delivers its next item immediately; scheduled updates keep the configured cloud interval.

## Existing device actions

Use the prefix `/settings/devices/<id>/meural`:

- `GET/POST /login`: same sign-in callback. Reauthentication of the same account
  and selected frame keeps the delivery journal.
- `POST /frame`: `cloud_device_id`; saves it and returns `account` with `saved: true`.
- `POST /logout`: removes tokens, attempts upload cleanup, and returns `account`.
- `POST /manage`: `action` is `manage` or `release` (stop Tesserae delivery).

Selecting a frame already assigned to another Tesserae display returns JSON 400.
Device deletion remains POST `/settings/devices/<id>/delete`. It accepts ordinary
form data and returns `status` and `return_url` when JSON is requested.

## Cleanup failures

Sign-out, frame changes, stopping delivery and deletion attempt to remove Tesserae uploads. Cleanup is best effort; an expired session or unavailable cloud never blocks the action. On first delivery Tesserae saves the frame's `galleryRotation` and `imageDuration`, then sets rotation off and the duration to 0. Release puts the saved values back, best effort: a failure never blocks the action and there is no Remove anyway flow. When the restore fails, `meural_playback_unrestored` is set on the device, the account state carries a `notice` telling the user to turn rotation back on in the Meural app, and a delete shows the same text as a flash message. Device deletion uses an ordinary form redirect and does not open a JSON error page when cleanup fails.

Native sleep schedule sync is not implemented. Quiet hours uses the existing core behavior. A configured LAN address lets Tesserae sleep and wake a frame during its own quiet hours. Deletion makes a best-effort wake only when Tesserae put the frame to sleep.
