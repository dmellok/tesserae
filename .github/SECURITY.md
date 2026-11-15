# Security policy

Tesserae is a self-hosted hobby project. The threat model is "trusted
LAN", the admin UI is meant to sit behind your own network or a
Tailscale-style overlay, not on the open internet. Within that scope,
security reports are taken seriously.

## Supported versions

Only the latest minor release is supported. Pre-1.0, that means the
most recent `0.x` tag receives security fixes; older minors do not.
When 1.0 ships, this policy moves to "current + previous minor".

Releases are listed on the
[releases page](https://github.com/dmellok/tesserae/releases). A fix
ships in a new release; it is not backported.

## Reporting a vulnerability

**Preferred:** use [GitHub's private vulnerability reporting](https://github.com/dmellok/tesserae/security/advisories/new)
(the **Report a vulnerability** button on the repository's Security tab).
It's private, auditable, and assigns a CVE if warranted.

Please don't open a public issue for a vulnerability. A public report
puts every install at risk until a fix has shipped.

**Alternative:** if you can't use GitHub, email **tesserae@dmello.io**
with "Tesserae security" in the subject line. PGP isn't set up;
encrypt only if you can match your own key out-of-band.

Please include:

- Affected version (tag or commit)
- Reproduction steps
- Impact you've observed or believe is possible
- A suggested fix, if you have one

## What to expect

- **Acknowledgement within 7 days.** A single-maintainer hobby project
  can't promise faster.
- A fix timeline depends on severity and bandwidth. Critical issues are
  worked on the same day; lower-severity ones may take a few weeks.
- You'll be credited in the release notes unless you ask not to be.
- Coordinated disclosure preferred, please hold details until a fix is
  out, or until 90 days have elapsed without progress.

## Scope

### In scope

- The Tesserae server in this repository.
- The official client repos:
  [tesserae-device-pi](https://github.com/dmellok/tesserae-device-pi),
  [tesserae-device-esp32-bin](https://github.com/dmellok/tesserae-device-esp32-bin),
  [tesserae-trmnl-client](https://github.com/dmellok/tesserae-trmnl-client).
- The HA App companion repo:
  [homeassistant-tesserae-addon](https://github.com/dmellok/homeassistant-tesserae-addon).

### Out of scope

- **Third-party plugins.** The drop-a-folder model deliberately runs
  whatever Python you drop into `plugins/`, `renderers/`, or `devices/`
  with the server's full privileges. By design, you trust the code you
  install. Vulnerabilities in third-party plugins are their authors'
  concern, not Tesserae's.
- **User-modified deployments / forks.** If you've patched the code or
  exposed the admin UI to the open internet without your own auth layer
  in front, that's your operational risk.
- **Upstream dependencies.** Bugs in Flask, paho-mqtt, the KOReader
  trmnl-display plugin, etc. should be reported upstream. Cross-link us
  if their fix needs a corresponding change here.
- **Unconfigured deployments.** Tesserae's onboarding wizard walks you
  through setting a password and broker creds on first run; deployments
  that skip that aren't supported.

### Known limits of device pairing

Devices pair on a network the owner trusts, and two paths identify a
device by its MAC address, which anything on that network can read:

- **Zero-touch pairing** (`POST /api/v1/device/discover`) returns a
  device's token to a matching MAC only until the device has used the
  token once. After that the owner has to allow a re-pair from the
  device's page.
- **TRMNL (BYOS) devices** authenticate by MAC on `/api/setup` and
  `/api/display`. That is TRMNL's own protocol and Tesserae follows it
  so stock TRMNL firmware works. Something on your network that knows a
  TRMNL device's MAC can fetch that device's picture.

### Always in scope (even if "out" above)

If you find a way to compromise the server _without_ running a plugin
or modifying code, an unauthenticated path on the admin UI, a crafted
HTTP request that escapes the renderer, a stored XSS that survives the
auth check, please report it.
