# Security policy

Tesserae is a self-hosted hobby project. The threat model is "trusted
LAN", the admin UI is meant to sit behind your own network or a
Tailscale-style overlay, not on the open internet. Within that scope,
security reports are taken seriously.

## Supported versions

Only the latest minor release is supported. Pre-1.0, that means the
most recent `0.x` tag receives security fixes; older minors do not.
When 1.0 ships, this policy moves to "current + previous minor".

| Version | Supported |
|---|---|
| 0.14.x (current) | ✅ |
| < 0.14 | ❌ |

## Reporting a vulnerability

**Preferred:** use [GitHub's private security advisory flow](https://github.com/dmellok/tesserae/security/advisories/new).
It's private, auditable, and assigns a CVE if warranted.

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
- The four official client repos:
  [tesserae-pi-png-client](https://github.com/dmellok/tesserae-pi-png-client),
  [tesserae-pi-bin-client](https://github.com/dmellok/tesserae-pi-bin-client),
  [tesserae-esp32-bin-client](https://github.com/dmellok/tesserae-esp32-bin-client),
  [tesserae-trmnl-client](https://github.com/dmellok/tesserae-trmnl-client).
- The HA Add-on companion repo:
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

### Always in scope (even if "out" above)

If you find a way to compromise the server _without_ running a plugin
or modifying code, an unauthenticated path on the admin UI, a crafted
HTTP request that escapes the renderer, a stored XSS that survives the
auth check, please report it.
