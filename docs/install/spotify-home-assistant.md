# Spotify widget on Home Assistant

The [Spotify widget bundle](https://github.com/dmellok/tesserae-spotify)
(`spotify_core` + `spotify_now_playing` + `spotify_queue` +
`spotify_album_art`) needs a one-time OAuth handshake before it can show
your now-playing track. This guide walks through that handshake when
you're running Tesserae as a Home Assistant App, which has one
particular wrinkle: **Spotify will only redirect OAuth callbacks to an
HTTPS URL** (or `http://localhost`), and the App's normal access
paths don't give you a stable HTTPS URL out of the box.

If you're running Tesserae as a bare-metal install on your LAN, the
flow is simpler, see the
[Spotify bundle README](https://github.com/dmellok/tesserae-spotify)
instead.

## Why this is involved

Spotify's OAuth contract is: when a user clicks **Connect** in the
widget admin page, the browser bounces to Spotify, the user authorises,
Spotify bounces them back to a `redirect_uri` that we register up
front. Spotify checks the redirect URI's host has either:

- a public **HTTPS** URL, or
- the literal hostname **`localhost`** (or `127.0.0.1`).

In an HA install, you usually reach Tesserae via one of three paths:

| Path | Protocol | Stable? | Spotify OK? |
|---|---|---|---|
| HA Ingress (sidebar tab) | HTTPS | The token in the URL changes per session | No |
| `http://homeassistant.local:8765` | HTTP | Stable | No (not HTTPS, not localhost) |
| `http://<HA-IP>:8765` | HTTP | Stable | No |

So we need to give Tesserae a **stable HTTPS URL** that points at port
8765 (stable channel) or 8766 (edge). The rest of this guide is two
options for doing that, then the same final Spotify-app registration
and Tesserae-side configuration for each.

## Option A: NGINX Proxy Manager + DuckDNS

The most common HA pattern; if you already have these two apps
installed (for remote access generally), you're already done with the
hard part. Skip to **Add the Tesserae host** below.

If not, install both first:

- **DuckDNS** app (free dynamic DNS + Let's Encrypt certs):
  Settings → Apps → app store → Search DuckDNS → Install. Get a
  token from [duckdns.org](https://www.duckdns.org/) and pick a
  subdomain (e.g. `myhome.duckdns.org`).
- **Nginx Proxy Manager** app: Settings → Apps → app store →
  Search Nginx Proxy Manager → Install. Default config is fine; open
  the web UI from its app page.

### Add the Tesserae host

In NGINX Proxy Manager → Hosts → Proxy Hosts → **Add Proxy Host**:

- **Domain Names**: `tesserae.myhome.duckdns.org` (use your subdomain)
- **Forward Hostname / IP**: `homeassistant.local` (or the HA host IP)
- **Forward Port**: `8765` (stable) or `8766` (edge)
- **Block Common Exploits**: on
- **Websockets Support**: on

Under the **SSL** tab:

- **SSL Certificate**: Request a new SSL Certificate (Let's Encrypt)
- **Force SSL**: on
- **HTTP/2 Support**: on
- Accept the LE T&Cs, hit Save.

Tesserae is now reachable at
`https://tesserae.myhome.duckdns.org`. That's the URL you'll use as
the Spotify redirect. Continue to **Register the Spotify app** below.

## Option B: Tailscale Funnel

Simplest if you already use Tailscale; gives you an HTTPS URL on a
`*.ts.net` subdomain with no extra config. Needs Tailscale running on
HA.

1. Install the **Tailscale** HA app.
2. Sign in to your Tailnet on the app's page.
3. On the HA host's shell (Terminal & SSH app), expose Tesserae:

   ```bash
   tailscale funnel --bg --https=443 http://localhost:8765
   ```

   (Use `:8766` for the edge channel.) The command prints the public
   HTTPS URL: something like
   `https://homeassistant.<your-tailnet>.ts.net`.

Tailscale Funnel needs HTTPS Certificates enabled and Funnel allowed
on your Tailnet (admin console → Settings → Funnel). It's free for
personal use.

Continue to **Register the Spotify app**.

## Register the Spotify app

Same for all three options above.

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
   and log in with your Spotify account (no developer fee).
2. Click **Create app**.
3. Fill in:
   - **App name**: anything (`Tesserae` works).
   - **App description**: anything.
   - **Website**: anything (`https://github.com/dmellok/tesserae`).
   - **Redirect URI**: this is the important one. It's your stable
     HTTPS URL from above, plus
     `/plugins/spotify_core/callback`. Examples:
     - NGINX Proxy Manager: `https://tesserae.myhome.duckdns.org/plugins/spotify_core/callback`
     - Tailscale Funnel: `https://homeassistant.<your-tailnet>.ts.net/plugins/spotify_core/callback`
4. Tick the **APIs/SDKs** you plan to use: **Web API** is enough for
   Tesserae.
5. Accept the developer terms and save.
6. On the app's overview page, click **Settings** → **Basic
   Information**. Copy the **Client ID** and click **View client
   secret** to copy the **Client Secret**. You'll need both in the
   next step.

## Configure Tesserae

1. Open Tesserae through the stable HTTPS URL you set up above
   (NOT through the HA sidebar ingress tab). You'll log in to the
   admin UI as you would normally.
2. Install the Spotify widget bundle if you haven't:
   **top nav → Widgets → Browse community widgets → Install Spotify
   Widgets**. Restart when prompted. (The Widgets entry is in the
   top nav, not under Settings.)
3. After the restart, go to **top nav → Widgets → Spotify Core**
   (the admin page the bundle drops in, listed under "Admin pages"
   in the dropdown).
4. Paste your **Client ID** and **Client Secret** into the form, hit
   Save.
5. Click **Connect**. Your browser bounces to Spotify, you authorise
   Tesserae to read your now-playing state, Spotify bounces you back,
   you see a "Connected as `<your-name>`" confirmation.
6. Now `spotify_now_playing`, `spotify_queue`, and `spotify_album_art`
   show your live state in the composition picker. Drop them onto a
   page like any other widget.

## Troubleshooting

**Spotify says "INVALID_CLIENT: Invalid redirect URI"**

The redirect URI registered on the Spotify app dashboard must match
the URL Tesserae is computing _exactly_, including the protocol and
trailing path. Check:

- Did you tick "Force SSL" on the proxy host? Tesserae needs to be
  reached via `https://...`, not the raw HTTP port.
- Does the Spotify app's redirect URI end with
  `/plugins/spotify_core/callback`?
- If you set up the tunnel after Spotify, hit **Save** on the Spotify
  app dashboard after editing the redirect URI; it doesn't auto-save.

**"Couldn't reach Spotify" or hanging on Connect**

The widget's outbound calls hit `accounts.spotify.com` and
`api.spotify.com`. The widget declares these in its `requires:`
capability block so the runtime allows them, but a network-level
block (HA's local DNS pointing somewhere that doesn't resolve
`spotify.com`, a Pi-hole rule, etc.) will trip this too.

**Spotify connected once but stops working after a few hours**

Tokens expire; the widget refreshes them automatically using the
refresh token Spotify gave us. If you see the connection drop without
recovering, check the Spotify Core admin page; there should be a
**Reconnect** button.

**I want to access Tesserae via the HA sidebar Ingress tab but the
Spotify OAuth flow needs the public URL**

You can. Once the OAuth dance is complete, the tokens are stored in
Tesserae's settings and the widgets work however you access the
server (Ingress, public URL, LAN IP). The public HTTPS URL is only
needed for the one-time **Connect** click.

If you stop being able to reach the public URL later (you take down
the tunnel, etc.), the widgets keep working until the refresh-token
flow has to renew, at which point you'll need the URL back to
re-authorise.

## Security: take the tunnel down after the OAuth dance

The tunnel you set up in Option A / B exposes Tesserae's full
admin UI to the public internet. Tesserae's only auth gate is the
single admin password you set during onboarding (no 2FA today), and
the admin UI carries the Spotify Client Secret, any GitHub PAT,
MQTT credentials, etc. If someone scans your endpoint and
brute-forces the password, they have full Tesserae control and your
widget credentials.

The simplest mitigation is to **take the tunnel down once the
Connect dance is complete**:

- **NGINX Proxy Manager**: disable the proxy host (toggle on the
  list view), or delete it entirely. Spin it back up if you ever
  need to re-authorise.
- **Tailscale Funnel**: `tailscale funnel off` on the HA host shell.

The widgets keep working on every other access path (HA sidebar
Ingress, `http://homeassistant.local:8765`, LAN IP). Tesserae
caches the Spotify access + refresh tokens in its settings; the
public URL is only needed for the one-time **Connect** click and
for the rare case Spotify's refresh-token flow drops (very
infrequent, typically only after a Spotify account password change
or a long disconnect).

If you'd rather leave the tunnel permanently up, say, you also
want to access Tesserae remotely from your phone without HA Cloud,
pick a strong admin password: 20+ random characters from a password
manager. Tesserae stores the bcrypt hash, brute-forcing it locally
is slow but possible against a weak password.

## Other widgets with the same constraint

The same setup works for any future Tesserae widget that needs an
OAuth flow with an HTTPS callback. The redirect URI path changes per
widget (e.g., `/plugins/<widget_id>/callback`) but the rest is the
same. Existing widgets in this category:

- [Spotify](https://github.com/dmellok/tesserae-spotify) (this guide).

GitHub's PAT-based widgets, the picture widgets' Unsplash key, and
Apple Music are all simpler (paste a key, no callback needed).
