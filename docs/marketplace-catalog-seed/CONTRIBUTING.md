# Contributing a widget

This catalog is **audit-only**: every entry is a PR I (the maintainer)
review before merge. That keeps the trust model honest while there's
no widget sandbox yet.

## How a submission works

1. Build your widget against the [Tesserae plugin contract]
   (https://github.com/dmellok/tesserae/blob/main/docs/widget-build-prompt.md).
   You need at minimum a `plugin.json` and a `client.js`. Server-side
   data fetching is optional (`server.py`).
2. Publish your widget to its own GitHub repo and tag a release (e.g.
   `v0.1.0`). GitHub will serve the source tarball at
   `https://github.com/<you>/<repo>/archive/refs/tags/<tag>.tar.gz`.
3. Capture the tarball's sha256:
   ```sh
   curl -sL https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz | sha256sum
   ```
4. Prepare at least one screenshot at the largest cell size (`lg`).
   Optionally include `xs`, `sm`, `md`. PNG only.
5. Open a PR to **this** repo (`tesserae-widgets`):
   - Add a `screenshots/<id>/lg.png` (plus any other sizes you shot).
   - Add a new entry to `widgets.json` with your widget metadata
     (see schema below).
   - Fill out the PR template.
6. Wait for review. I'll read your widget's source end-to-end, focused
   on:
   - **Network egress.** What URLs does it hit? Are they declared
     intent (e.g. weather API) or surprising?
   - **Filesystem access.** Anything outside the widget's data dir?
   - **Settings access.** Does it read secrets it doesn't need?
   - **Reliability.** Does it handle upstream failures gracefully?
7. Once merged, Tesserae users will see your widget in
   Settings → Plugins → Browse on the next catalog refresh.

## Widget metadata (per-entry shape)

```json
{
  "id": "currency_now",
  "name": "Currency, Now",
  "description": "Single-pair FX rate with sparkline.",
  "icon": "ph-currency-circle-dollar",
  "author": {
    "name": "Your Name",
    "github": "your-handle"
  },
  "tags": ["finance", "utility"],
  "kind": "widget",
  "tesserae_compat": "1.x",
  "official": false,
  "screenshot_sizes": ["lg", "md"],
  "release": {
    "version": "0.1.0",
    "tarball_url": "https://github.com/your-handle/tesserae-currency-now/archive/refs/tags/v0.1.0.tar.gz",
    "sha256": "the-sha256-from-step-3"
  },
  "source": "https://github.com/your-handle/tesserae-currency-now"
}
```

See `schema/marketplace.schema.json` for the full contract, including
the closed list of supported tags.

## Updates

Bumping a release follows the same flow: bump `version` in your
`plugin.json`, push a new tag, capture the new sha256, then open a
PR here updating your `widgets.json` entry. The Browse page will show
"Update available" to users running the older version.

## What the `official` flag means

Reserved for entries whose source repo is owned by the catalog
maintainer (me). Don't set it on community submissions; the PR review
will strip it if you do.
