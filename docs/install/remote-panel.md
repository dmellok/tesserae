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

### 1. Link your install to the relay

**Settings → Cloud relay.** Set the relay URL (default `https://relay.tesserae.ink`)
and choose **Register this install**. Tesserae generates a keypair, registers its
public key with the relay, and stores the returned credentials. This only runs
outbound; nothing is opened on your network.

### 2. Add the remote panel

Choose **Add a remote panel**, pick the panel kind and screen size as you would
for any device, and Tesserae shows a **pairing code** plus the relay URL.

### 3. Enter the code on the panel

At the remote location, give the panel the relay URL and the pairing code (via
its captive portal or companion app). The panel and your home instance complete
the handshake through the relay; within a poll or two the panel is paired and
starts showing frames. You can take the panel to its destination first, the
pairing happens over the internet, not your LAN.

## How fresh is it?

The panel fetches on its normal e-ink sleep cadence (every 15–60 minutes,
typically). A new frame appears at the next scheduled wake. This keeps battery
life the same as a local panel.

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
