# Meural account API

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
   new device. Missing or expired sign-in returns JSON 400 without creating it.

The account is bound to the browser's signed session cookie. Access and refresh
tokens are SecretBox-encrypted in server memory. It expires after 15 minutes,
is single-use, and is discarded by POST `/settings/meural/cancel` or
`/settings/meural/logout`. Starting another pending sign-in also discards it.
Typed device id and name can be preserved in browser session storage; keep
account tokens and pending IDs out of storage, URLs, and page HTML.

LAN-only add uses `delivery_method=local` and `host`; it needs no pending account.

## Existing device actions

Use the prefix `/settings/devices/<id>/meural`:

- `GET/POST /login`: same sign-in callback. Reauthentication of the same account
  and selected frame keeps its playback snapshot and avoids restoring/re-pausing.
- `POST /frame`: `cloud_device_id`; saves it and returns `account` with `saved: true`.
- `POST /logout`: attempts restoration, removes tokens, returns `account`.
- `POST /manage`: `action` is `manage` or `release`.

Selecting a frame already assigned to another Tesserae display returns JSON 400.
Device deletion remains POST `/settings/devices/<id>/delete`. It accepts ordinary
form data and returns `status` and `return_url` when JSON is requested.

## Restore failures and errors

Rejections return `{"error": "message"}` with a non-success HTTP status.
A failed restoration returns HTTP 409:

```json
{
  "error": "Could not restore Meural playback. Meural may stay paused. Re-enable rotation in the Meural app. Choose Remove anyway to continue.",
  "restore_failed": true
}
```

Offer an explicit **Remove anyway** button. Retry the original request with
`remove_anyway: true` in JSON, or `remove_anyway=1` in form data. Account-switch
callbacks reuse their staged result, so the retry does not exchange the Cognito
token again. Cancel leaves the saved credentials and playback snapshot available.
The browser should show a plain fallback message if a proxy returns HTML.

## Native sleep

`GET /settings/devices/<id>/meural/sleep` returns `status` and `sleep`.
POST `/sleep/refresh` reads the frame again. `sleep` includes `source` (`frame`
or `tesserae`), `available`, `can_edit`, `enabled`, `timezone`, `start`, `end`,
`days`, normalized `events`, `synced_at`, `checked_at`, and a display `message`.
Unavailable state keeps the last successful sync time and labels the fallback.
The cache lasts five minutes. Authentication backoff still applies to Refresh.

POST `/sleep` with `start` and `end` as `HH:MM`, `enabled` as a boolean, and
`days` as full weekday names. It replaces sleep/wake events, preserves artwork
events, synchronizes the frame, and re-reads the result. A form can send
`return_to_device=1` to redirect back instead of returning JSON.

The wire contract comes from Netgear's public [scheduler source](https://my.meural.netgear.com/_next/static/chunks/pages/scheduler-245f99a04134b86b27ff.js)
and [device API source](https://my.meural.netgear.com/_next/static/chunks/13562f62644a107ccab29118de91ecd911576338.77f2c2aa5e539eb8b520.js):
`GET/PUT devices/<id>/scheduler`, `events[].command`, `events[].time` (`HHMM`),
`events[].days`, and device `timezone`/`schedulerEnabled`. The
[HA Meural reference](https://github.com/GuySie/ha-meural/blob/master/custom_components/meural/pymeural.py)
exposes LAN sleep status but no schedule. None of these paths has been checked
against a physical frame in this change.
