# Quickstart: Bloomin8 Canvas

Connect your battery-powered Bloomin8 Canvas colour frame to Tesserae through its Schedule Pull API. You don't need to flash it or use Home Assistant.

!!! warning "Built against Bloomin8's spec, not yet tested on a Canvas"
    This setup follows Bloomin8's [Schedule Pull API document](https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component/blob/main/docs/Schedule_Pull_API.md). It hasn't been tested on hardware. Report results on [issue #344](https://github.com/dmellok/tesserae/issues/344), with the Canvas log if something fails.

## 01 — Add the Canvas in Tesserae

1. Open **Settings → Devices → Add device** and choose **Bloomin8 Canvas**.
2. Set the panel:

    | Canvas | Resolution (portrait) |
    |---|---|
    | 13.3" (default) | 1200×1600 |
    | Smaller Canvas | 480×800 |

    Choose landscape if it hangs sideways.

3. Save. Copy the access token, upstream URL and command shown for the next step. You can reveal the token again on the device card.

## 02 — Point the Canvas at Tesserae

Wake the Canvas with its button or the Bloomin8 app. Run the command from a computer on the same network. Replace these values:

- `CANVAS_IP`: the Canvas address.
- `TESSERAE`: your Tesserae address.
- `TOKEN`: the device's access token.
- The time: your first wake time.

macOS or Linux:

```sh
curl -X PUT http://CANVAS_IP/upstream/pull_settings \
  -H 'Content-Type: application/json' \
  -d '{
    "upstream_on": true,
    "upstream_url": "http://TESSERAE:8765/api/bloomin8",
    "token": "TOKEN",
    "cron_time": "2026-10-13T08:30:00Z"
  }'
```

Windows PowerShell (the `curl` there is a different program, so use this form):

```powershell
$cron = (Get-Date).ToUniversalTime().AddMinutes(2).ToString("yyyy-MM-ddTHH:mm:ssZ")
$body = @{ upstream_on = $true; upstream_url = "http://TESSERAE:8765/api/bloomin8"; token = "TOKEN"; cron_time = $cron } | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "http://CANVAS_IP/upstream/pull_settings" -ContentType "application/json" -Body $body
```

Set `cron_time` to a minute or two ahead, in UTC with a trailing `Z`. Tesserae sets later wake times.

To check the saved settings, `GET` the same address. In PowerShell, use `Invoke-RestMethod http://CANVAS_IP/upstream/pull_settings`.

`TESSERAE` is Tesserae's address and port; the token pop-up fills it in. For the Home Assistant App, use the HA host and the App's port, normally 8765. Don't use HA's port 8123.

The Canvas must be able to reach `upstream_url` to download images:

- `http://` works on your local network.
- `https://` needs a certificate from a public authority, such as Let's Encrypt.
- Redirects don't work. Use Tesserae's direct address.

## 03 — Bind a dashboard

Bind a page, schedule or rotation to the Canvas.

It downloads a full-colour JPEG on each wake and converts it to its panel colours. Tesserae turns landscape images sideways and marks them as landscape, as the Canvas requires.

## 04 — Wakes and battery

The device card's **Wake interval** is the longest the Canvas sleeps. It defaults to 30 minutes.

For a scheduled page change, Tesserae wakes the Canvas just after the change. Manual **Send** and Home Assistant triggers appear at the next wake.

If the last confirmed image hasn't changed, the Canvas sleeps without drawing it again. This saves battery.

Each wake reports battery level to the device card, battery history and Home Assistant.

## Stopping

While the Canvas is awake, send `{"upstream_on": false}` to `pull_settings`.

Deleting it in Tesserae doesn't stop it waking. It will keep waking hourly with "unknown token" errors until you turn pulling off.
