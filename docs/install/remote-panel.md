# Remote panel (cloud relay)

Send dashboards from your home Tesserae server to a screen somewhere else.
Your home network needs no incoming connections.

A cloud relay passes pictures between them. Your home server uploads each picture; the remote screen checks for it.
You don't need port-forwarding, dynamic DNS, or a VPN.

## Is it private?

Yes. Only your home server and the paired screen can decrypt the pictures.
They share a key created during pairing with X25519.

The relay stores and forwards encrypted pictures that it cannot read.
Your configuration and data stay at home.

## What you need

- Your home Tesserae server. It can be behind NAT, your router's normal network sharing.
- The hosted `relay.tesserae.ink`, or [your own relay](../relay/self-host.md) in a container or Cloudflare Worker.
- A screen with relay-capable firmware that can decrypt pictures. See the [contract](../relay/contract.md).

## Set it up

Your home server must be running during pairing to complete the connection.

### 1. Link your install to the relay (once)

Open **Settings → Cloud relay** and set the relay URL.
The default is `https://relay.tesserae.ink`.

Choose **Register this install**. Tesserae creates its keys and saves the relay credentials.

### 2. Add the remote panel

Open **Settings → Cloud relay → Add a remote panel**.
Choose the device kind, screen size, and an id and name, such as `parents_panel`.

Tesserae shows a pairing code and the relay URL.
Under "Code valid for", choose how long the code lasts: 10 minutes by default, up to 24 hours.

Use a longer window if someone needs to travel before entering it.

### 3. Enter the code on the panel

At the remote location, enter the relay URL and pairing code in the screen's setup page or companion app.
The screen creates its keys and pairs over the internet. It doesn't need access to your home network.

Your home server completes pairing within a check or two, about 30 seconds.
The device then appears in **Settings → Devices** and **Cloud relay → Remote panels**.

### 4. Give it a dashboard

Assign a dashboard or lineup to the device and click **Send**.
Tesserae encrypts the picture and uploads it to the relay.
The screen shows it at its next wake; later pictures also arrive at the next check-in.

Open its card in **Settings → Devices** to adjust sleep interval, quiet hours, orientation, and calibration.

The Cloud relay page links to this card.
Server-side settings apply on the next render.
Device-side settings, such as sleep interval and button wake, arrive encrypted at the next wake if the firmware supports them.

### If it does not pair

- Expired code: create a new one. Codes last 10 minutes by default; "Code valid for" allows up to 24 hours.
- Nothing after about 1 minute: check that your home server is running. The Cloud relay page must show "linked", and the screen's relay URL must match exactly.
- Paired but blank: assign a dashboard in step 4.
- Start again: choose **Remove** under **Cloud relay → Remote panels**, then add the panel again. This deletes its relay storage and token.

## How fresh is it?

The screen checks at its normal sleep interval, typically every 15 to 60 minutes.
New pictures appear at the next scheduled wake. Battery life stays the same as for a local screen.

## Device status

The device card can show battery, signal, firmware version, and last seen through the relay.

These arrive with the normal check-in. They are unencrypted device-status data; dashboard pictures stay encrypted.
Fields remain blank if the firmware doesn't report them.

## If something goes offline

- Home server offline: the relay serves the last picture. E-ink keeps it on screen.
- Relay offline: the screen keeps its picture and retries at its next wake.
- Access revoked: removing a panel or revoking access deletes its relay storage and access token. Its next check gets `401`, so the firmware clears its pairing and reopens setup. Deleting it from **Settings → Devices** does the same.

## Related

- [Self-host the relay](../relay/self-host.md).
- [Cloud relay contract](../relay/contract.md) for firmware authors.
- [REST transport (no broker)](rest-transport.md) for local connections.
