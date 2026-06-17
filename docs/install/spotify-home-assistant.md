# Spotify widget on Home Assistant

The [Spotify widget bundle](https://github.com/dmellok/tesserae-widget-spotify)
(`spotify_core`, `spotify_now_playing`, `spotify_queue`,
`spotify_album_art`, `spotify_top`) needs a one-time OAuth handshake
before it can show your playback state. This guide walks through that
handshake when you're running Tesserae as a Home Assistant App, which
has one specific wrinkle: **Spotify will only redirect OAuth callbacks
to an HTTPS URL** (or `http://127.0.0.1`), and the App's normal access
paths don't give you a stable HTTPS URL out of the box.

If you're running Tesserae as a bare-metal install on your LAN with
loopback access, the flow is simpler; see the
[Spotify bundle README](https://github.com/dmellok/tesserae-widget-spotify).

## What you'll do

1. Install **NGINX Proxy Manager** + **DuckDNS** HA Apps.
2. Set up a public HTTPS URL pointing at Tesserae on your LAN.
3. Tell Tesserae what its public URL is via the **Settings → App →
   Public URL** field.
4. Register a Spotify Developer app with the matching redirect URI.
5. Click Connect.

That's it. There are several ways this can go off the rails in the
middle steps; the rest of this guide covers each in order.

## Why this is involved

Spotify's OAuth contract is: when the user clicks **Connect** in the
widget admin page, the browser bounces to Spotify, the user authorises,
Spotify bounces them back to a `redirect_uri` we registered up front.
Spotify only allows redirect URIs that are:

- a public **HTTPS** URL, or
- the literal **`http://127.0.0.1`** (loopback IP).

In a Home Assistant install, you usually reach Tesserae via one of three
paths:

| Path | Protocol | Stable? | Spotify OK? |
|---|---|---|---|
| HA Ingress (sidebar tab) | HTTPS | The token in the URL changes per session | No |
| `http://homeassistant.local:8765` | HTTP | Stable | No (not HTTPS, not loopback) |
| `http://<HA-IP>:8765` | HTTP | Stable | No |

So we need to give Tesserae a **stable HTTPS URL** that points at port
8765 (stable channel) or 8766 (edge), and tell Tesserae what that URL
is so its plugin OAuth code uses it when building redirect URIs.

## Step 1: Install DuckDNS + NGINX Proxy Manager

**Both are Home Assistant Community Add-ons.** A couple of similarly
named alternatives exist in the store; make sure you pick the right ones
or this guide won't work.

### DuckDNS

1. **Settings → Apps → App store → search "DuckDNS"**. Install the
   one by *Home Assistant Community Add-ons*.
2. Visit [duckdns.org](https://www.duckdns.org/), sign in, pick a
   subdomain (e.g. `myhome`, giving you `myhome.duckdns.org`), copy
   the token shown at the top.
3. In Home Assistant, open the DuckDNS App's **Configuration** tab:
   ```yaml
   domains:
     - myhome.duckdns.org
   token: <your-duckdns-token>
   ```
4. Start the DuckDNS App. It'll keep the DNS record pointing at your
   home's current public IP.

### Nginx Proxy Manager

Watch out for naming: the store may show **multiple** Nginx-named Apps.
You want the **Home Assistant Community Add-ons** one, titled simply
**Nginx Proxy Manager** (no extra suffix).

Avoid:

- **NGINX Home Assistant SSL proxy** (different App, only proxies HA
  itself, not arbitrary services).
- **Nginx Proxy Manager + Static Web Server** (a community fork by
  `alexbelgium`; known to fail at startup on aarch64 with
  `/usr/bin/env: 'bash': Permission denied`).

Steps:

1. **Settings → Apps → App store → search "Nginx Proxy Manager"**.
   Install the Community Add-ons one.
2. Start it. Click **Open Web UI** (NPM also serves on port `81` if the
   button doesn't appear). Default login: `admin@example.com` /
   `changeme`. NPM will force you to set a real password on first login.

## Step 2: Get a stable HTTPS URL

There are three sub-paths depending on what your router and ISP allow.
Pick the simplest that works for you.

### 2a: The happy path (port 443 forwarded, Let's Encrypt HTTP-01)

If you can forward inbound TCP port 80 AND 443 from your router to your
HA host, this is the cleanest setup.

1. **Router → port forwarding**: forward external `443 → <HA-IP>:443`
   (TCP) and `80 → <HA-IP>:80` (TCP).
2. **NPM → Hosts → Proxy Hosts → Add Proxy Host**.
   - **Domain Names**: `tesserae.myhome.duckdns.org`
   - **Scheme**: `http`
   - **Forward Hostname / IP**: `homeassistant.local` (or your HA's LAN
     IP, e.g. `192.168.1.50`).
   - **Forward Port**: `8765` (stable) or `8766` (edge).
   - **Block Common Exploits**: on.
   - **Websockets Support**: on.
3. **SSL tab**:
   - **SSL Certificate**: Request a new SSL Certificate.
   - **Force SSL**: on. **HTTP/2 Support**: on.
   - Email + Let's Encrypt T&C.
   - Save.

NPM provisions the cert (30 – 90 seconds), turns the row's SSL dot
green. Your URL is now `https://tesserae.myhome.duckdns.org`.

### 2b: Can't forward port 80 (use DNS-01 challenge)

If your router or ISP blocks inbound 80 but you can forward 443, NPM
can still get a Let's Encrypt cert via DNS-01 using your DuckDNS token.

Same NPM setup as 2a, but in the SSL tab:

- Tick **Use a DNS Challenge** → **DNS Provider**: DuckDNS.
- **Credentials File Content**: paste exactly (including the
  `dns_duckdns_token=` prefix):
  ```
  dns_duckdns_token=<your-duckdns-token>
  ```
  One line, no quotes.
- Continue with the rest of the SSL tab as in 2a.

Forward router port `443 → <HA-IP>:443` only.

### 2c: ISP blocks inbound port 443 (use non-standard port)

Some ISPs block well-known inbound ports (80, 443) on residential
plans. Test this with [yougetsignal.com/tools/open-ports](https://www.yougetsignal.com/tools/open-ports/):
if 443 comes back **closed** even after the port forward is set up,
this is you.

Solution: use external port 8443.

1. NPM **Configuration** tab → set **HTTPS/SSL Entrance port** to
   `8443` (left field; the right side stays `443/tcp`). Save. Restart
   NPM.
2. Router port forward: external `8443 → <HA-IP>:8443` (TCP).
3. Set up the NPM Proxy Host as in 2b (DNS-01 challenge, since port 80
   inbound is almost certainly also blocked).

Your URL becomes `https://tesserae.myhome.duckdns.org:8443` (the
`:8443` matters).

### Check the URL works from outside

From your phone on mobile data (WiFi OFF), open your URL. You should
land on Tesserae's login page over HTTPS with a green padlock.

If it doesn't load:

- Check the port is open with
  [yougetsignal.com/tools/open-ports](https://www.yougetsignal.com/tools/open-ports/).
- If "closed": ISP is blocking that port. Try 2c with a different port.
- If "filtered" / "timed out": **likely CGNAT.** Your router's WAN IP
  is in the `100.64.0.0 / 10` range and isn't actually reachable from
  the internet. Port forwarding can't fix that; you'd need to switch
  ISP plan to one with a real public IPv4 (often called "static IP" or
  "public IP" add-on, sometimes free, sometimes a few dollars/month).

## Step 3: Tell Tesserae its public URL

This is the most important step and the one most likely to be skipped.
Tesserae's plugin OAuth code (Spotify Core's Connect button, etc.)
builds the redirect URI from what it thinks its own URL is. Without the
override, it sees the internal `http://<some-internal-host>:8765`
connection NPM forwarded, which Spotify rejects.

1. Open Tesserae via the new HTTPS URL (NOT via HA Ingress).
2. **Settings → App → Public URL** (top of the page).
3. Paste your full URL, scheme + host + port if non-standard, no
   trailing slash:
   - `https://tesserae.myhome.duckdns.org` (paths 2a / 2b)
   - `https://tesserae.myhome.duckdns.org:8443` (path 2c)
4. Save.

The setting takes effect immediately; no restart needed.

## Step 4: Register the Spotify Developer app

1. Go to
   [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard),
   log in (no developer fee).
2. **Create app**:
   - **App name**: anything (`Tesserae` is fine).
   - **App description**: anything.
   - **Website**: anything (`https://github.com/dmellok/tesserae` is
     fine).
   - **Redirect URI**: your public URL from Step 3, plus
     `/plugins/spotify_core/callback`. The match must be exact,
     including the port if you used 2c. Examples:
     - `https://tesserae.myhome.duckdns.org/plugins/spotify_core/callback`
     - `https://tesserae.myhome.duckdns.org:8443/plugins/spotify_core/callback`
   - **APIs/SDKs**: tick **Web API**.
3. Save.
4. On the app's overview page: **Settings → Basic Information**.
   Copy the **Client ID**, click **View client secret**, copy the
   **Client Secret**.

## Step 5: Connect Spotify

1. Open Tesserae via the public HTTPS URL (still NOT via HA Ingress).
2. Install the bundle if you haven't: **top nav → Widgets → Browse
   community widgets → Install Spotify Widgets**. Restart when
   prompted.
3. **top nav → Widgets → Spotify Core** (listed under "Admin pages").
4. Paste Client ID + Client Secret. Save.
5. Confirm the **Redirect URI** displayed at the top of the page matches
   exactly what you registered with Spotify. If it doesn't, double-check
   Step 3 (the Public URL setting).
6. Click **Connect**. Browser bounces to Spotify, you approve, it
   bounces you back to Tesserae. You should see "Connected as
   `<your-name>`".

The widgets (`spotify_now_playing`, `spotify_queue`,
`spotify_album_art`, `spotify_top`) now show your live state in the
composition picker. Drop them on a page like any other widget.

## Once it works, optional cleanups

**You can keep using Tesserae via HA Ingress for everyday admin.** The
OAuth tokens are stored once and refresh automatically; the public URL
is only needed for the initial Connect click and the rare case where
Spotify's refresh-token flow drops (usually after a Spotify password
change or many months of disuse).

**Take the public URL down if you don't need it for anything else.**
NPM lets you disable a proxy host without deleting it (toggle on the
list view), which makes Tesserae no longer reachable from the public
internet while leaving the configured cert + DNS + Public URL setting
in place. If you ever need to re-Connect, re-enable the proxy host.

**If you leave the public URL up**, pick a strong admin password
(20+ random characters from a password manager). Tesserae stores the
bcrypt hash; brute-forcing it locally is slow but possible against a
weak password.

## Troubleshooting

**Spotify says "INVALID_CLIENT: Invalid redirect URI"**

The redirect URI registered with Spotify must match the URL Tesserae
displays on the Spotify Core admin page _exactly_, including scheme,
host, port, and path.

- Confirm Tesserae's displayed Redirect URI matches what you pasted
  into Spotify's app dashboard. If they differ, the **Public URL**
  setting (Step 3) is wrong or unset.
- After editing the URI on Spotify's side, hit **Save** on the
  dashboard explicitly; it doesn't auto-save.

**Tesserae's displayed Redirect URI is wrong (http://, missing port,
mangled hostname)**

You've skipped or misconfigured the Public URL setting. Go back to
**Step 3** and paste the full public URL with scheme + host + port if
non-standard, no trailing slash.

**Port 443 (or 8443) shows "closed" on
yougetsignal.com/tools/open-ports**

Two common causes:

1. NPM isn't actually listening on the host port you forwarded to.
   Check NPM's **Configuration → Network** section. The right field
   shows the container port (always 443/tcp for HTTPS); the left field
   is the host port. Make sure that matches the port your router is
   forwarding to.
2. Your ISP blocks the port. Try a different external port (path 2c).

**"Couldn't reach Spotify" or hanging on Connect**

Outbound calls hit `accounts.spotify.com` and `api.spotify.com`. The
widget declares these in its `requires:` capability block so Tesserae
allows them, but a network-level block (Pi-hole rule, local DNS that
doesn't resolve `spotify.com`, etc.) will trip this.

**Spotify connected once but stops working after a few hours**

Tokens expire; the widget refreshes them automatically using the
refresh token Spotify gave us. If you see the connection drop without
recovering, open Spotify Core's admin page and hit **Reconnect**.

**`/usr/bin/env: 'bash': Permission denied` when starting NPM**

You probably installed the wrong NPM add-on. See the warning in Step 1:
the `alexbelgium/nginx_webserver_proxy` build (titled "Nginx Proxy
Manager + Static Web Server" in the store) ships a broken aarch64
image. Uninstall it, install the Home Assistant Community Add-ons one
(titled simply "Nginx Proxy Manager").

## Other widgets with the same constraint

The same setup works for any Tesserae widget that needs an OAuth flow
with an HTTPS callback. The redirect URI path changes per widget
(`/plugins/<widget_id>/callback`) but the rest of the flow is
identical. Existing widgets in this category:

- [Spotify](https://github.com/dmellok/tesserae-widget-spotify) (this guide).

GitHub's PAT-based widgets, the picture widgets' Unsplash key, and
Apple Music are all simpler (paste a key, no callback needed).
