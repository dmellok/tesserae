# Publish a theme through the catalog

Themes are catalog-installable in the same way as widgets: a tagged
release tarball in your own GitHub repo, an entry in the catalog
index, one-click install from **Settings → Themes** (via Browse
community widgets). The contract is deliberately minimal — one
manifest file plus one CSS file per theme — so publishing a theme
doesn't require any Python, JavaScript, or build tooling.

## When to publish a theme through the catalog

| Path | When to use |
|---|---|
| **User themes builder** (Settings → Themes → New theme) | Personal palette you want on your own dashboards. Lives in `data/themes/user.json`; never leaves the instance. |
| **Bundle in Tesserae** (PR against [`static/style/spectra-tokens.css`](https://github.com/dmellok/tesserae/blob/main/static/style/spectra-tokens.css)) | Generally-useful palette that ships to every install; touches the curated Spectra palette family. |
| **Community catalog** (this page) | Your own theme or pack you want users to opt into — niche aesthetics, brand-specific palettes, holiday packs, monochrome sets, etc. |

The catalog path lets you (a) iterate on your theme in your own repo
on your own cadence, (b) ship multiple themes in one install as a
pack, and (c) get listed in the same Browse UI as widgets.

## The tarball convention

A theme entry's release tarball is **flat**: each theme is two files
at the envelope root, named by id.

**Single theme** (one pair):

```
my-cool-theme-v0.1.0/
  my-cool-theme.json
  my-cool-theme.css
```

**Theme pack** (N pairs at the root):

```
my-pack-v0.1.0/
  my-pack-rose.json
  my-pack-rose.css
  my-pack-mint.json
  my-pack-mint.css
  my-pack-lavender.json
  my-pack-lavender.css
```

The id-prefix trick (`my-pack-rose` rather than just `rose`) avoids
collisions with themes from other packs and groups your pack's
themes together in the alphabetical picker. It's optional but
recommended.

## `theme.json` shape

```json
{
  "id": "my-cool-theme",
  "name": "My Cool Theme",
  "family": "light",
  "tagline": "soft + sunny"
}
```

| Field | Required | Description |
|---|---|---|
| `id` | yes | Must match the file stem (`my-cool-theme.json` → `id: "my-cool-theme"`) and the selector in the CSS. Pattern: `^[a-z][a-z0-9_-]*$`. |
| `name` | yes | Display name shown in the picker and on the themes browse strip. |
| `family` | yes | One of `light`, `dark`, `movement`, `vivid`, `gradient`. Drives picker grouping. Unknown values fall back to `community`. |
| `tagline` | no | Short flavour line shown beneath the name in pickers (e.g. `"warm paper"`). Omit when there's nothing useful to add. |

## `theme.css` shape

One CSS block targeting `[data-theme="<id>"]`. Set whichever Spectra
tokens you want to override — the bundled cascade fills any token
you skip with its own default.

```css
[data-theme="my-cool-theme"] {
  --font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;

  --bg:             #F2EBE3;
  --surface:        #F9F5EE;
  --surface-sunken: #E8DFD0;
  --text-primary:   #2A1F14;
  --text-secondary: #5D4D38;
  --text-muted:     #8A7860;
  --icon:           var(--text-primary);
  --edge:           #C8B89A;

  --accent-1: #B85730; --accent-1-soft: #EAD3C5;
  --accent-2: #A88030; --accent-2-soft: #ECDFC4;
  --accent-3: #5F7045; --accent-3-soft: #DCE2CC;
  --accent-4: #506F8A; --accent-4-soft: #D5DEE7;
  --accent-5: #7F4A6A; --accent-5-soft: #E5D3DD;
  --accent-6: #B27050; --accent-6-soft: #ECD9CD;
  --on-accent: #FFFFFF;
}
```

The full Spectra token reference is in
[Cross-widget design system](../widget-design-system.md). The
short version:

- **`--bg`** / **`--surface`** / **`--surface-sunken`** — page
  background, card surfaces, slightly recessed surfaces.
- **`--text-primary`** / **`--text-secondary`** / **`--text-muted`** —
  three legibility tiers.
- **`--edge`** — borders, dividers, hairline strokes.
- **`--accent-1`** through **`--accent-6`** — six categorical
  accents widgets cycle through (chips, indicators, list-row
  highlights). Each has a `-soft` companion for tinted backgrounds.
- **`--on-accent`** — foreground colour rendered on top of any
  filled accent block.

## What gets validated

When the install path runs (and the catalog CI on every PR), it
verifies:

1. Every `<id>.json` at the envelope root has a matching `<id>.css`.
   Unpaired files reject the install rather than skip silently.
2. Every manifest's `id` field equals the file stem.
3. Every CSS file contains a `[data-theme="<id>"]` selector somewhere.
4. No theme id clashes with a bundled Spectra theme (catalogs can't
   shadow the defaults).
5. The tarball sha256 matches what the catalog entry declares.

## The flow

1. Write your `theme.json` + `theme.css` (one pair per theme).
2. Test locally: drop the pair(s) into
   `data/themes/community/<id>/theme.json` + `theme.css` on your
   running Tesserae instance, hard-refresh the themes page, and
   confirm everything looks right in `/themes` and the page editor's
   theme picker.
3. Publish to a GitHub repo. Recommended naming:
   `tesserae-<theme-or-pack-name>` (e.g. `tesserae-theme-tonal`).
4. Tag a release (e.g. `v0.1.0`). GitHub will serve the source
   tarball at
   `https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz`.
5. Capture the sha256:
   ```sh
   curl -sL https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz | sha256sum
   ```
6. Open a PR to
   [`dmellok/tesserae-widgets`](https://github.com/dmellok/tesserae-widgets)
   adding an entry to `widgets.json`:

   ```json
   {
     "id": "my-cool-theme",
     "name": "My Cool Theme",
     "description": "One-line pitch shown on the Browse card.",
     "author": { "name": "Your Name", "github": "your-handle" },
     "tags": ["utility"],
     "kind": "theme",
     "tesserae_compat": "1.x",
     "screenshot_sizes": ["lg"],
     "release": {
       "version": "0.1.0",
       "tarball_url": "https://github.com/your-handle/tesserae-my-cool-theme/archive/refs/tags/v0.1.0.tar.gz",
       "sha256": "the-sha256-from-step-5"
     },
     "source": "https://github.com/your-handle/tesserae-my-cool-theme"
   }
   ```

   For a pack, set `"folders"` to every theme id the tarball ships:

   ```json
   "kind": "theme",
   "folders": ["my-pack-rose", "my-pack-mint", "my-pack-lavender"],
   ```

7. Add a screenshot at `screenshots/<entry-id>/lg.png` in the catalog
   repo — same shape widgets use (3:2 aspect, ~1200×800).

8. Wait for review. The catalog maintainer reads your theme.css
   end-to-end. Themes have no Python, no JavaScript, and no network
   egress to audit, so the review is much shorter than a widget's:

   - CSS block targets the declared id?
   - Every Spectra token a typical card consumes is set (or inherits
     cleanly)?
   - Screenshot shows what users would actually see?

9. Once merged, the theme shows up in everyone's
   **Settings → Widgets → Browse community widgets** on the next
   catalog refresh.

## Updates

Bumping a release follows the same flow: bump `version` in your
catalog entry, push a new tag, capture the new sha256, then open a
PR updating the entry. The Browse page will show "Update available"
to users running an older version.

## Where themes live on disk

| Source | On-disk location |
|---|---|
| Bundled | `static/style/spectra-tokens.css` (one big file, lockstep with `app/state/theme_registry.py`) |
| User-built | `data/themes/user.json` (single JSON file, one record per theme) |
| Community (this page) | `data/themes/community/<id>/theme.json` + `<id>/theme.css` (one folder per installed theme; the marketplace install path lays them down) |

All three sources merge through the same `build_registry()` call,
land in the same picker, and respect the same "Show in picker"
toggle — community themes aren't second-class.

## What you can't do (yet)

- **Ship JavaScript or Python with a theme.** A theme is colour
  tokens only. Anything dynamic belongs in a widget.
- **Override Spectra structural tokens** (e.g. `--stroke-*`,
  `--radius-*`, `--space-*`). Those live in the structural Style
  layer, not the colour Theme layer.
- **Mark a theme as a per-page favourite** with metadata. You can
  hide it from the picker per-instance via the eye toggle on the
  browse strip; pinning is a future feature.
