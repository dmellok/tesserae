# Configuration reference

Use Settings for everyday changes. Tesserae has three places to configure it,
from most to least common:

1. Settings saves changes to `settings.json`. Use it whenever the option has a Settings page.
2. Environment variables set options before startup.
   Set them in your compose file's `environment:` block, a systemd unit or the shell.
3. Command-line flags on `tesserae` are mainly for direct installs and development.
   Under Docker, use environment variables to avoid replacing the image's startup command.

## CLI flags

```
tesserae [--dev] [--host HOST] [--port PORT] [--log-level LEVEL] [--reset-password]
```

| Flag | Default | What it does |
| --- | --- | --- |
| `--host` | `TESSERAE_BIND_HOST` if set, else `0.0.0.0` | Bind address. Set `::` to listen on IPv6 (dual-stack where the OS supports it). |
| `--port` | `TESSERAE_BIND_PORT` if set, else `8765` | Bind port for the web UI + API. |
| `--dev` | off | Flask dev server with auto-reload + debugger instead of waitress. |
| `--log-level` | `info` | Root log level (`trace`/`debug`/`info`/`warning`/`error`). Wins over `TESSERAE_LOG_LEVEL`. |
| `--reset-password` | | Clear the stored admin password and exit; the next request drops to `/setup`. |

## Environment variables

### Serving

| Variable | Default | What it does |
| --- | --- | --- |
| `TESSERAE_BIND_PORT` | `8765` | Server port; sets the `--port` default in image `0.378.0`+. Set it in compose `environment:`; an explicit `--port` wins. Internal renders use this port too, including behind a proxy or Docker port map. |
| `TESSERAE_BIND_HOST` | `0.0.0.0` | Server address; sets the `--host` default in image `0.381.0`+. An explicit `--host` wins. Use `::` for IPv6; internal renders then use `[::1]` instead of `127.0.0.1`. |
| `TESSERAE_HTTP_PORT` | `8765` | Port shown to panels and used in generated URLs when it differs from the server port. Use for a proxy or a mapping such as `ports: ["8766:8765"]`. Only a fallback until the first request supplies the external port. |
| `TESSERAE_HOST_IP` | auto-detected | LAN IP used in frame and broker URLs. Required with Docker bridge networking, which detects an unreachable `172.x` container address. IPv6 works in `0.381.0`+; URL brackets are added automatically. |
| `TESSERAE_THREADS` | `24` | Waitress worker threads, minimum 4. Raise if open editors and live event streams hold up rendering. |
| `TESSERAE_FORWARDED_HOPS` | `1` | Trusted `X-Forwarded-*` proxy hops. Use `2` for two proxies or `0` to trust none. |
| `TESSERAE_TRUSTED_PROXIES` | unset | Extra trusted proxy addresses in comma-separated CIDR ranges. Adds to loopback, private and link-local defaults. Allows `X-Forwarded-*` and HA ingress headers; does not grant client access. See [Browser request protection](server.md#browser-request-protection). |
| `TESSERAE_TRUSTED_NETWORKS` | unset | Extra client networks treated as local, in comma-separated CIDR ranges, such as `2001:db8:abcd::/48` for IPv6. Clients can fetch `/renders/` without login and access admin pages if the password is disabled. Adds to the fixed private ranges and Settings → System → Authentication → Trusted networks. |
| `TESSERAE_VISIONECT_PORT` | `11113` | TCP port the Visionect listener binds, shared by all Visionect signs. Wins over the port set in Settings, which then can't be changed there. Must be 1024 to 65535 and not the web port. |

### Storage and secrets

| Variable | Default | What it does |
| --- | --- | --- |
| `TESSERAE_DATA_ROOT` | `<repo>/data` | Data directory: settings, pages, schedules, event log, render cache, backups. The Docker image keeps the default and expects a volume at `/app/data`. |
| `TESSERAE_SECRET_KEY` | derived | 64 hex characters (32 bytes) that encrypt stored secrets. Without it, Tesserae derives a key from its saved session secret. Restarts preserve that key. Restoring without `settings.json` or recreating without the data volume loses it: stored secrets read back empty; other settings stay intact. See [Pin the secret key](docker.md#pin-the-secret-key-before-you-store-any-credentials). |
| `OPENAI_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `FAL_KEY` (or `FAL_API_KEY`) | unset | Fallback API keys for image generation, used only when no key for that provider is saved in Settings. |

### Logging and behaviour

| Variable | Default | What it does |
| --- | --- | --- |
| `TESSERAE_LOG_LEVEL` | `info` | Root log level. `--log-level` wins; the HA App's own Log level option wins there. |
| `TESSERAE_IN_DOCKER` | unset | Set to `1` by the Docker image. Hides the in-app self-update card and shows a `docker compose pull` hint instead. |
| `TESSERAE_CHROMIUM_PATH` | bundled | Path to a Chromium executable for the renderer, when the Playwright-bundled one can't be used. |
| `TESSERAE_EXPERIMENT_<NAME>` | unset | Sets and locks an optional feature in Settings → System → Features. On: `1`/`true`; off: `0`/`false`. Names: `COMPOSER` (canvas editor), `MCP` (agent API), `TEMPLATES` (template marketplace). All default to on. Example: `TESSERAE_EXPERIMENT_MCP=0`. |
| `TESSERAE_API_BASE` | `https://api.tesserae.ink` | Base URL for online features (template marketplace, community catalog). Only useful for development or a self-hosted API. |
| `TESSERAE_FIRMWARE_API` | `https://api.tesserae.ink` | Base URL the firmware-update check queries. Development override. |
| `TZ` | unset | IANA timezone name (e.g. `Australia/Melbourne`) used when the timezone setting is on system auto-detect. Checked before the host's `/etc/localtime`. |

Open **Settings → Logs** to read the newest 2000 lines as they arrive.
You can filter by watch level, search or download the log.

**Record level** changes what the server logs until its next restart.
For a lasting change, use `TESSERAE_LOG_LEVEL` or `--log-level`.

Device tokens and pairing codes are hidden on this page. Console output is unchanged.

### Set by the platform (not for operators)

| Variable | Set by | Purpose |
| --- | --- | --- |
| `TESSERAE_HA_INGRESS` | HA App | Enables ingress-aware URL handling under Home Assistant. |
| `TESSERAE_PARENT_PID` | in-app updater | Windows restart handshake. |
| `TESSERAE_CHANNEL` | Docker image build | Release channel of the build (`stable` or `edge`), used by the update check and the daily heartbeat. |
| `TESSERAE_BUILD_SHA` | Docker image build | Commit the image was built from, so an edge build can tell how far it is behind `main`. |
| `TESSERAE_INSTALL_METHOD` | Proxmox helper script | `community-script` makes Settings → System point at the script's `update` command. |

The `tesserae-screenshots` developer tool reads `TESSERAE_URL` and
`TESSERAE_MCP_TOKEN` to find a running server; those configure the
tool, not the server.

## settings.json

Settings writes to `settings.json` in the data directory:

- Direct install: `data/core/settings.json`
- Docker: `/app/data/core/settings.json`

Avoid editing this file by hand:

- Keys appear when features first use them.
- Stored secrets are encrypted.
- Saving rewrites the whole file.

If you must edit it, stop the server first. Use Settings to check what each value means.

Two supported ways to manage settings outside the UI:

- Backups (Settings → System → Backups) save the whole data
  root including `settings.json`.
- `tesserae --reset-password` clears the auth section when you've
  locked yourself out.
  If you can still open the admin UI, for example through Home Assistant ingress, use Settings instead:

    1. Turn the password off under Settings → System → Authentication.
    2. Press **Forget saved password**.
    3. Enable it again to set a new password.
