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
   Optionally include `xs`, `sm`, `md`. PNG only. If your widget has
   meaningfully different states worth previewing (playing vs paused,
   day vs night, sun vs rain), you can also ship up to 9 extra `lg`-
   sized shots at `screenshots/<id>/extra-1.png`, `extra-2.png`, ... and
   declare them in `widgets.json` with `"extra_screenshot_count": N`.
   The Browse card renders all N+1 shots as an inline carousel; entries
   without the field stay single-image.
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
   Settings → Widgets → Browse on the next catalog refresh.

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

## Theme entries (`kind: theme`)

Themes are even simpler than widgets: one CSS file + one tiny manifest
per theme, no Python, no JavaScript. The tarball is flat — each theme
is two files at the envelope root, named by id:

```
my-cool-theme-v0.1.0/
  my-cool-theme.json   # { "id": "my-cool-theme",
                       #   "name": "My Cool Theme",
                       #   "family": "light",
                       #   "tagline": "soft + sunny" }
  my-cool-theme.css    # [data-theme="my-cool-theme"] { --bg: #...; ... }
```

**Theme packs** ship multiple themes in one entry. Just include more
pairs at the root:

```
my-pastels-pack-v0.1.0/
  pastel-rose.json
  pastel-rose.css
  pastel-mint.json
  pastel-mint.css
  pastel-lavender.json
  pastel-lavender.css
```

Packs need a `folders` claim on the catalog entry listing every theme id
the pack ships — the same field widget bundles use. The install path
verifies the tarball's id set matches your claim exactly so a pack
silently picking up a stray file gets caught at review time:

```json
{
  "id": "my-pastels-pack",
  "kind": "theme",
  "folders": ["pastel-rose", "pastel-mint", "pastel-lavender"],
  …
}
```

Single-theme entries can omit `folders`; the install path then requires
the one discovered id to match the catalog `id` exactly.

The host install path validates that:

- Every `<id>.json` has a matching `<id>.css` at the root (no unpaired files).
- Every manifest's `id` field equals the file stem.
- Every CSS file contains a `[data-theme="<id>"]` block.
- No theme id clashes with a bundled Spectra theme.

Each pair lands at `data/themes/community/<id>/theme.json` +
`<id>/theme.css` on the host so the read side stays uniform regardless
of pack vs single layout.

`family` is one of `light` / `dark` / `movement` / `vivid` / `gradient`
(matches the bundled Spectra families); anything else falls back to a
generic `community` family. `tagline` is the short line shown in the
picker (e.g. `"warm paper"`); omit if there's nothing useful to add.

Themes don't have settings, data dirs, or network egress to audit, so
the review focuses on:

- The CSS block targets the declared id (mismatched ids break the cascade).
- Every Spectra token the cells consume is set (or inherits cleanly).
- The screenshot shows what users would actually see.

## Updates

Bumping a release follows the same flow: bump `version` in your
`plugin.json`, push a new tag, capture the new sha256, then open a
PR here updating your `widgets.json` entry. The Browse page will show
"Update available" to users running the older version.

## Bundle entries (widget families)

If your widget needs a shared admin / data-fetch sibling (e.g. a
`_core` folder that holds an API key admin page consumed by several
display widgets), you can ship the whole family as one catalog
entry. Two changes to the layout:

**Tarball layout.** Instead of one folder containing `plugin.json`,
put your subplugins as direct children of the wrapping folder:

```
github-bundle-v1.0/        ← single wrapping folder (the GitHub
  github_core/              archive envelope; can be any name)
    plugin.json
    server.py
    client.js
  github_releases/
    plugin.json
    client.js
  github_repo/
    plugin.json
    client.js
```

**Catalog entry.** Add a `folders` field listing every subplugin:

```json
{
  "id": "github",
  "name": "GitHub family",
  "folders": ["github_core", "github_releases", "github_repo"],
  "release": { ... }
}
```

The catalog `id` is a logical name for the family (it's NOT a real
folder). The marketplace verifies the tarball's child folders match
this list exactly, validates each `plugin.json`, and installs all of
them as siblings under `plugins/`. Install + uninstall act on the
whole bundle atomically; per-folder versions are independent (each
subfolder ships its own `version`).

The `folders` field is optional, the install path auto-detects the
bundle layout from the tarball. Declaring it gives the reviewer a
single line to verify and lets the Browse card list every folder
under the description so users know what's about to land. CI on this
repo enforces the match when the field is present.

## What the `official` flag means

Reserved for entries whose source repo is owned by the catalog
maintainer (me). Don't set it on community submissions; the PR review
will strip it if you do.
