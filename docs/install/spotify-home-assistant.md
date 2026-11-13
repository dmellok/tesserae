# Spotify widget on Home Assistant

Sign in to the [Spotify widget bundle](https://github.com/dmellok/tesserae-widget-spotify)
once to show your playback state. On Home Assistant, this needs a stable HTTPS address.

For a direct install with access from the server itself, use the simpler
[Spotify bundle README](https://github.com/dmellok/tesserae-widget-spotify).

## What you'll do

1. Install **NGINX Proxy Manager** + **DuckDNS** HA Apps.
2. Set up a public HTTPS URL pointing at Tesserae on your LAN.
3. Tell Tesserae what its public URL is via the **Settings → Server →
   Public URL** field.
4. Register a Spotify Developer app with the matching redirect URI.
5. Click Connect.

## Why this is involved

Clicking Connect opens Spotify's sign-in page. After you approve access,
Spotify sends you back to the registered `redirect_uri`, the return address.
It must be:

- A public HTTPS address, or
- `http://127.0.0.1`, the local computer's own address.

Home Assistant's normal access paths do not meet those requirements:

| Path | Protocol | Stable? | Spotify OK? |
|---|---|---|---|
| HA Ingress (sidebar tab) | HTTPS | The token in the URL changes per session | No |
| `http://homeassistant.local:8765` | HTTP | Stable | No (not HTTPS, not loopback) |
| `http://<HA-IP>:8765` | HTTP | Stable | No |

Create a stable HTTPS address pointing at port 8765 (stable channel) or 8766 (edge).

Save that address in Tesserae so it can build the correct return URL.

## Step 1: Install DuckDNS + NGINX Proxy Manager

Install the Home Assistant Community Add-ons versions of both Apps.
Similarly named alternatives will not work with this guide.

### DuckDNS

1. **Settings → Apps → App store → search "DuckDNS"**. Install the
   one by *Home Assistant Community Add-ons*.
2. Sign in at [duckdns.org](https://www.duckdns.org/).
   Choose a subdomain, such as `myhome` for `myhome.duckdns.org`.
   Copy the token shown at the top.
3. In Home Assistant, open the DuckDNS App's **Configuration** tab:
   ```yaml
   domains:
     - myhome.duckdns.org
   token: <your-duckdns-token>
   ```
4. Start the DuckDNS App. It'll keep the DNS record pointing at your
   home's current public IP.

### Nginx Proxy Manager

Choose **Nginx Proxy Manager** by Home Assistant Community Add-ons, with no extra suffix.

Avoid:

- NGINX Home Assistant SSL proxy: only proxies HA itself.
- Nginx Proxy Manager + Static Web Server: a fork by `alexbelgium` that can fail on aarch64 with
  `/usr/bin/env: 'bash': Permission denied`.

Steps:

1. **Settings → Apps → App store → search "Nginx Proxy Manager"**.
   Install the Community Add-ons one.
2. Start it and click **Open Web UI**, or use port `81` if the button is missing.
   Sign in with `admin@example.com` / `changeme`. NPM requires a new password on first login.

## Step 2: Get a stable HTTPS URL

Choose the simplest path your router and ISP allow.

### 2a: The happy path (port 443 forwarded, Let's Encrypt HTTP-01)

Use this if your router can forward TCP ports 80 and 443 to the HA host.

1. **Router → port forwarding**: forward external `443 → <HA-IP>:443`
   (TCP) and `80 → <HA-IP>:80` (TCP).
2. **NPM → Hosts → Proxy Hosts → Add Proxy Host**.

    | Field | Value |
    | --- | --- |
    | Domain Names | `tesserae.myhome.duckdns.org` |
    | Scheme | `http` |
    | Forward Hostname / IP | `homeassistant.local` or HA's LAN IP, such as `192.168.1.50` |
    | Forward Port | `8765` (stable) or `8766` (edge) |
    | Block Common Exploits | On |
    | Websockets Support | On |

3. **SSL tab**:
   - **SSL Certificate**: Request a new SSL Certificate.
   - **Force SSL**: on. **HTTP/2 Support**: on.
   - Email + Let's Encrypt T&C.
   - Save.

NPM gets the certificate in 30 to 90 seconds. The SSL dot turns green.
Your address is `https://tesserae.myhome.duckdns.org`.

### 2b: Can't forward port 80 (use DNS-01 challenge)

If port 80 is blocked but 443 works, use a DNS-01 challenge.
This lets NPM get a Let's Encrypt certificate through DuckDNS.

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

Some ISPs block ports 80 and 443 on residential plans.
Check [yougetsignal.com/tools/open-ports](https://www.yougetsignal.com/tools/open-ports/).
If 443 is closed after forwarding, use external port 8443 to get around the ISP block.

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
- If "filtered" / "timed out": likely CGNAT, where the ISP shares one public address across customers.
  Your router's WAN IP is in `100.64.0.0 / 10` and cannot be reached from the internet.
  Port forwarding cannot fix this. Ask for a public IPv4 plan or a "static IP" / "public IP" add-on.
  It may be free or cost a few dollars per month.

## Step 3: Tell Tesserae its public URL

Without Public URL, Tesserae builds the return address from NPM's internal
`http://<some-internal-host>:8765` connection. Spotify rejects that address.

1. Open Tesserae via the new HTTPS URL (NOT via HA Ingress).
2. **Settings → Server → Network & integrations → Public URL**.
3. Paste the full address with no trailing slash. Include the protocol and hostname,
   plus the port if non-standard:
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
   catalog → Install Spotify Widgets**. Restart when
   prompted.
3. **top nav → Widgets → Spotify Core** (listed under "Admin pages").
4. Paste Client ID + Client Secret. Save.
5. Confirm the **Redirect URI** displayed at the top of the page matches
   exactly what you registered with Spotify. If it doesn't, double-check
   Step 3 (the Public URL setting).
6. Click **Connect**. The browser redirects to Spotify. After you approve,
   it redirects back to Tesserae. You should see "Connected as
   `<your-name>`".

The bundle uses `spotify_core` for connection settings.
The composition picker now has `spotify_now_playing`, `spotify_queue`, `spotify_album_art` and `spotify_top`.
Add them to a dashboard like any other widget.

## Once it works, optional cleanups

You can use HA Ingress for everyday admin. Spotify tokens refresh automatically.
The public address is needed only to connect or reconnect after tokens stop refreshing.

This rarely happens, usually after a Spotify password change or months without use.

Take the public URL down if you don't need it for anything else.
Disable the proxy host from NPM's list view to block public access.
The certificate, DNS setup and Public URL setting stay in place.

Enable the proxy host again if you need to reconnect Spotify.

If you leave the public URL up, pick a strong admin password
(20+ random characters from a password manager). Tesserae stores a
PBKDF2 hash; brute-forcing it locally is slow but possible against a
weak password.

## Troubleshooting

**Spotify says "INVALID_CLIENT: Invalid redirect URI"**

- Compare Tesserae's Redirect URI with Spotify's app dashboard.
  The protocol, hostname, port and path must match. If they differ, fix **Public URL** in Step 3.
- After editing the URI on Spotify's side, hit **Save** on the
  dashboard explicitly; it doesn't auto-save.

**Tesserae's displayed Redirect URI is wrong (http://, missing port,
mangled hostname)**

Fix Public URL using **Step 3**, including the non-standard port if needed.
Leave off the trailing slash.

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

Check that your network can reach `accounts.spotify.com` and `api.spotify.com`.
The widget's `requires:` block allows them in Tesserae, but local filtering can still block access.
Check Pi-hole rules and whether DNS can resolve `spotify.com`.

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

Use the same setup for widgets that need HTTPS sign-in callbacks.
Only the path changes: `/plugins/<widget_id>/callback`.
Current widgets in this category:

- [Spotify](https://github.com/dmellok/tesserae-widget-spotify) (this guide).

These need only a key, with no callback:

- GitHub's PAT-based widgets
- Picture widgets using an Unsplash key
- Apple Music
