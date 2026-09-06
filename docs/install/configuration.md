# Configuration reference

Tesserae is configured in three layers, from most to least common:

1. **The Settings UI** (persisted to `settings.json`). Everything
   day-to-day lives here: MQTT broker, devices, schedules, auth,
   experiments, online features. If a knob has a Settings page, that
   page is the supported way to turn it.
2. **Environment variables.** Deployment-level concerns that have to
   exist before the app boots or that belong to the host rather than
   the install: bind port, data directory, advertised IP, log level.
   Set them in your compose file's `environment:` block, your systemd
   unit, or the shell.
3. **CLI flags** on the `tesserae` command. Mostly for bare-metal
   installs and development; under Docker prefer the env vars so you
   don't have to override the image CMD.

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
| `TESSERAE_BIND_PORT` | `8765` | The port the server actually listens on. Feeds the `--port` default (image `0.378.0`+), so a compose `environment:` entry is enough to move the web port; an explicit `--port` flag wins if both are given. Also used internally so loopback renders target the real bind port behind a reverse proxy or Docker port map. |
| `TESSERAE_BIND_HOST` | `0.0.0.0` | The address the server binds. Feeds the `--host` default (image `0.381.0`+); an explicit `--host` flag wins. Set `::` for IPv6. Also used internally so loopback renders target `[::1]` instead of `127.0.0.1` under an IPv6 bind. |
| `TESSERAE_HTTP_PORT` | `8765` | The *advertised* port, what panels and generated URLs should use when it differs from the bind port (reverse proxy, `ports: ["8766:8765"]` remap). Only the fallback before the first request captures the real external port. |
| `TESSERAE_HOST_IP` | auto-detected | The LAN IP baked into frame URLs and the broker URL shown to panels. Required under Docker bridge networking, where auto-detection finds the container's unreachable `172.x` address. May be an IPv6 address (`0.381.0`+); URLs bracket it automatically. |
| `TESSERAE_THREADS` | `24` | Waitress worker threads (minimum 4). Raise if many open editors + SSE streams starve renders. |
| `TESSERAE_FORWARDED_HOPS` | `1` | Trusted `X-Forwarded-*` proxy hops (Flask ProxyFix). Set `2` behind a double proxy, `0` to trust none. |
| `TESSERAE_TRUSTED_NETWORKS` | unset | Comma-separated CIDRs the auth gate treats as local on top of the fixed private ranges, e.g. `2001:db8:abcd::/48` for a LAN on an ISP-delegated IPv6 prefix. Clients in these networks fetch `/renders/` without a session and, with the password disabled, reach the admin UI. Merged with the list under Settings → System → Authentication → Trusted networks. |

### Storage and secrets

| Variable | Default | What it does |
| --- | --- | --- |
| `TESSERAE_DATA_ROOT` | `<repo>/data` | Data directory: settings, pages, schedules, event log, render cache, backups. The Docker image keeps the default and expects a volume at `/app/data`. |
| `TESSERAE_SECRET_KEY` | derived | 64 hex chars (32 bytes) used to encrypt stored secrets (API keys, broker passwords) at rest. Unset, the key is derived from the persisted session secret: that survives a restart but **not** a recreated container or a restored data folder with a regenerated session secret, and stored secrets then read back empty while the rest of the settings stay intact. Pin it — see [Pin the secret key](docker.md#pin-the-secret-key-before-you-store-any-credentials). |

### Logging and behaviour

| Variable | Default | What it does |
| --- | --- | --- |
| `TESSERAE_LOG_LEVEL` | `info` | Root log level. `--log-level` wins; the HA App's own Log level option wins there. |
| `TESSERAE_IN_DOCKER` | unset | Set to `1` by the Docker image. Hides the in-app self-update card and shows a `docker compose pull` hint instead. |
| `TESSERAE_CHROMIUM_PATH` | bundled | Path to a Chromium executable for the renderer, when the Playwright-bundled one can't be used. |
| `TESSERAE_EXPERIMENT_<NAME>` | unset | Force an experiment flag on (`1`/`true`) or off (`0`/`false`) at deployment level, e.g. `TESSERAE_EXPERIMENT_COMPOSER=1`. Overrides and locks the Settings toggle. |
| `TESSERAE_API_BASE` | `https://api.tesserae.ink` | Base URL for online features (template marketplace, community catalog). Only useful for development or a self-hosted API. |
| `TESSERAE_FIRMWARE_API` | `https://api.tesserae.ink` | Base URL the firmware-update check queries. Development override. |

### Set by the platform (not for operators)

| Variable | Set by | Purpose |
| --- | --- | --- |
| `TESSERAE_HA_INGRESS` | HA App | Enables ingress-aware URL handling under Home Assistant. |
| `TESSERAE_PARENT_PID` | in-app updater | Windows restart handshake. |

The `tesserae-screenshots` developer tool reads `TESSERAE_URL` and
`TESSERAE_MCP_TOKEN` to find a running server; those configure the
tool, not the server.

## settings.json

Everything else lives in `settings.json` under the data root
(`data/core/settings.json`; `/app/data/core/settings.json` in
Docker). It is written by the Settings UI and is not designed for
hand-editing: keys appear as features first touch them, secrets are
encrypted at rest, and the file is rewritten wholesale on save. If
you do edit it, stop the server first, and treat the Settings UI as
the reference for what a value means.

Two supported non-UI touchpoints:

- **Backups** (Settings → System → Backups) snapshot the whole data
  root including `settings.json`.
- `tesserae --reset-password` clears the auth section when you've
  locked yourself out.
