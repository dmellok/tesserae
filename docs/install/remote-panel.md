# Remote panel (cloud relay)

Run a Tesserae panel somewhere else, a parent's house, a partner's office, a
cabin, and have it show dashboards from your **home** instance, without opening
your home network to the internet.

No port-forwarding, no dynamic DNS, no VPN. A small cloud **relay** sits in the
middle that both ends reach *outbound*: your home instance pushes each rendered
frame to it; the remote panel polls it. Your home network never accepts an
inbound connection.

## Is it private?

Yes. Frames are sealed **end-to-end** with a key that only your home instance
and that one panel share (an X25519 handshake done at pairing). The relay stores
only ciphertext, so it can't read your dashboards, only forward them. Your
household config and data never leave home; only the finished, encrypted image
transits the relay.

## What you need

- Your home Tesserae instance (any network, behind NAT is fine).
- A relay: the hosted `relay.tesserae.ink`, or [your own Cloudflare Worker](../relay/self-host.md).
- A panel whose firmware supports the relay transport (it must decrypt frames;
  see the [contract](../relay/contract.md)).

## Set it up

Your **home instance must be running** during pairing, it is what completes the
handshake.

### 1. Link your install to the relay (once)

Only needed the first time you use the relay. **Settings → Cloud relay**, set
the relay URL (default `https://relay.tesserae.ink`) and choose **Register this
install**. Tesserae generates a keypair, registers its public key, and stores
the returned credentials. Outbound only; nothing is opened on your network.

### 2. Add the remote panel

**Settings → Cloud relay → Add a remote panel.** Pick the device kind, screen
size, and an id/name (e.g. `parents_panel`) as you would for any device.
Tesserae shows a **pairing code** plus the relay URL. The code expires in about
10 minutes.

### 3. Enter the code on the panel

At the remote location, give the panel the relay URL and the pairing code (via
its captive portal / companion app). The panel generates its keypair and pairs
**over the internet**, so it can already be at its destination, no LAN access
needed. Within a poll or two (about 30 seconds) your home instance completes the
handshake and the device appears under **Settings → Devices** and **Cloud relay
→ Remote panels**.

### 4. Give it a dashboard

It is a normal device now. Compose a page bound to it (or add it to a rotation /
schedule) and **Send**, exactly like a local panel. That render is what gets
sealed and uploaded to its relay mailbox. It appears on the panel at its next
wake, and each later render lands on the following poll.

### If it does not pair

- **Code expired** — mint a fresh one (10-minute window).
- **Nothing after ~1 min** — confirm the home instance is running and the Cloud
  relay page shows "linked", and the panel's relay URL exactly matches.
- **Paired but blank** — it has no dashboard yet; do step 4.
- **Start over** — Cloud relay → Remote panels → **Remove** drops the mailbox +
  token, then re-add.

## How fresh is it?

The panel fetches on its normal e-ink sleep cadence (every 15–60 minutes,
typically). A new frame appears at the next scheduled wake. This keeps battery
life the same as a local panel.

## Device status

A remote panel still reports its telemetry (battery, signal, firmware, last
seen) through the relay, so its Devices card stays populated just like a
local panel. This rides the panel's normal poll and is plaintext operational
data, not dashboard content. Firmware that doesn't send it simply leaves those
fields blank.

## If something goes offline

- **Home offline:** the relay keeps serving the last frame; e-ink holds the
  image anyway.
- **Relay offline:** the panel keeps its current frame and retries next wake.
- **Revoke a panel:** removing it (or revoking its access) drops its relay
  mailbox, and the panel stops receiving frames.

## Related

- [Self-host the relay](../relay/self-host.md) — run your own instead of the hosted one.
- [Cloud relay contract](../relay/contract.md) — the wire protocol, for firmware authors.
- [REST transport (no broker)](rest-transport.md) — the local-network equivalent.
