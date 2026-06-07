# Publish a widget through the catalog

Once your widget renders cleanly in `/_test/render` and passes its
smoke test, you have two ways to ship it:

| Path | When to use | Trust model |
|---|---|---|
| **Bundle in the Tesserae repo** | Foundational widgets ([Stable tier](../widgets/tiers.md)), built against a documented API, intended to ship to every installation. | Reviewed in the host PR; lives in `plugins/<id>/` alongside the existing bundled widgets. |
| **Publish through the community catalog** | Your own widget, third-party APIs, niche use cases, anything you want users to opt into rather than installing for everyone. | Audit-only review on each catalog PR; lives in [dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets) and installs into the user's `plugins/` via Settings → Widgets → Browse community widgets. |

This page covers the catalog path. For the bundled path see
[Build a widget with AI → Submitting](writing-a-widget.md#submitting).

## How the catalog works

The catalog is a static `widgets.json` index in a separate repo
([dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets)).
Each entry pins a tagged release tarball + sha256. A user's Browse
page fetches the index, renders one card per entry, and (on Install)
downloads the tarball, verifies the sha256, validates the embedded
`plugin.json` against the host schema, and drops the result into
`plugins/<id>/`.

Trust model is **audit-only** for phase 1: every catalog entry lands
via a PR I review by hand. There's no capability sandbox or process
isolation yet (see GitHub issues
[#2](https://github.com/dmellok/tesserae/issues/2) /
[#3](https://github.com/dmellok/tesserae/issues/3) for the follow-up
work). So:

- Your widget runs as the same Python process as Tesserae itself,
  with full filesystem + network access.
- The PR review is the security boundary. Code that reads
  `settings.json`, writes outside its `data_dir`, or makes
  surprising network calls will be sent back.
- "Just shipped a widget, users will install it" — wait until your
  PR's merged + the catalog raw URL has caught up (\~5 minutes).

## The flow

1. **Build the widget** under any folder layout you like
   (`devref_card/plugin.json` + `client.js` + optional `server.py`).
   The plugin contract is documented at
   [the widget contract page](../widgets.md); validate locally with:

    ```sh
    python -c "import json, jsonschema; \
      jsonschema.validate(json.load(open('plugin.json')), \
                          json.load(open('path/to/tesserae/schema/plugin.schema.json')))"
    ```

2. **Publish your widget to its own GitHub repo and tag a release.**
   GitHub serves a tarball at a stable URL:

    ```text
    https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz
    ```

3. **Capture the tarball sha256.** The catalog pins this to detect
   drift if the tag is republished:

    ```sh
    curl -sL https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz \
      | shasum -a 256
    ```

4. **Take screenshots.** At minimum `lg.png` (largest cell size).
   Render the widget locally via `/_test/render?plugin=<id>&size=lg`,
   crop, and save. Optional sizes (`xs`, `sm`, `md`) help users
   size-shop before installing.

5. **Open a PR to [tesserae-widgets](https://github.com/dmellok/tesserae-widgets).**
   Add your screenshot under `screenshots/<id>/lg.png` and your entry
   to `widgets.json`. See [the catalog
   CONTRIBUTING](https://github.com/dmellok/tesserae-widgets/blob/main/CONTRIBUTING.md)
   for the entry shape; the PR template asks for what to expect from
   a review.

6. **CI verifies.** A GitHub Actions workflow on the catalog repo
   fetches every tarball, recomputes the sha256, checks each
   `plugin.json` against the host schema, and (for bundles) verifies
   the declared `folders` match the tarball contents. Malformed PRs
   fail here before review.

7. **Review + merge.** I read the widget's source end-to-end:
   network egress, settings access, anything outside the
   `data_dir`. The PR template's "Networking + settings" section
   should pre-empt most of the round-trip.

8. **Catalog updates.** Once merged, the raw URL catches up in
   a few minutes, and users see your widget on their next
   Browse-page refresh (or sooner via the page's `?refresh=1`).

## A minimal entry (single widget)

```jsonc
{
  "id": "currency_now",
  "name": "Currency, Now",
  "description": "Single-pair FX rate with sparkline.",
  "icon": "ph-currency-circle-dollar",
  "author": {
    "name": "Your Name",
    "github": "your-handle"
  },
  "tags": ["finance"],
  "kind": "widget",
  "tesserae_compat": "1.x",
  "screenshot_sizes": ["lg"],
  "release": {
    "version": "0.1.0",
    "tarball_url": "https://github.com/your-handle/tesserae-currency-now/archive/refs/tags/v0.1.0.tar.gz",
    "sha256": "5cf3…"
  },
  "source": "https://github.com/your-handle/tesserae-currency-now"
}
```

The full schema with every field's constraints lives at
[`schema/marketplace.schema.json`](https://github.com/dmellok/tesserae-widgets/blob/main/schema/marketplace.schema.json).

## Bundle entries (widget families)

A widget that needs a companion `_core` (admin page, saved instances,
API key store) ships as a **bundle**: one catalog entry installs
multiple plugin folders. The tarball wraps every subplugin in a
single containing folder, and the catalog entry declares the expected
subfolders.

**Tarball layout:**

```text
github-bundle-v1.0/        ← any wrapping folder name (the marketplace
                              strips it on install)
  github_core/              ← admin-only data plugin
    plugin.json
    server.py
    client.js
    templates/
      github_core/index.html
  github_releases/          ← display widget
    plugin.json
    client.js
  github_repo/              ← display widget
    plugin.json
    client.js
```

**Catalog entry:**

```jsonc
{
  "id": "github",
  "name": "GitHub family",
  "description": "Releases / repo / actions / PR queue + shared API token.",
  "kind": "widget",
  "folders": ["github_core", "github_releases", "github_repo"],
  "release": {
    "version": "0.1.0",
    "tarball_url": "https://github.com/your-handle/tesserae-github-bundle/archive/refs/tags/v0.1.0.tar.gz",
    "sha256": "..."
  },
  ...
}
```

The catalog `id` is a logical name for the family (not necessarily a
folder name). The marketplace verifies the tarball's direct children
match the `folders` list exactly, validates each `plugin.json`, and
installs all of them as siblings under `plugins/`. Install +
uninstall act on the whole bundle atomically; per-folder versions
remain independent.

The `folders` field is optional, the install path auto-detects the
layout from the tarball. Declaring it gives the reviewer a single
line to verify and lets the Browse card list every folder under the
description so users see what's about to land. Catalog CI enforces
the match when the field is present.

**Worked example.** The [Dev Reference
Bundle](https://github.com/dmellok/tesserae-devref-bundle) ships
through the catalog as `id: "devref"` with `folders: ["devref_card",
"devref_core"]`. Read it source-by-source as a reference for the
contract surfaces (cell options, `choices_from`, `fetch()`,
`blueprint()`, admin templates).

## Updating an existing entry

Bump the patch version, push a new tag, capture the new sha256, then
PR the catalog with the new `release` block:

```sh
# in your widget repo
git tag -a v0.1.1 -m "v0.1.1, fix the dropdown"
git push origin v0.1.1
curl -sL https://github.com/your-handle/your-widget/archive/refs/tags/v0.1.1.tar.gz \
  | shasum -a 256
```

```jsonc
// widgets.json, in the catalog PR
"release": {
  "version": "0.1.1",         // bumped
  "tarball_url": "https://github.com/your-handle/your-widget/archive/refs/tags/v0.1.1.tar.gz",
  "sha256": "d6745c…"          // new
}
```

The Browse page renders **Update available** on each user's card
when their installed version is older than the catalog's. One click,
restart, done.

## Review checklist (what reviewers look at)

Pre-empt these in your PR's "Networking + settings" section. The
catalog now expects a `requires:` array in `plugin.json` declaring
each capability the widget uses (see [the widget contract's
capability section](../widgets.md#capabilities-requires) for
the vocabulary); the runtime enforces network egress at the socket
layer.

- **`requires:` declared.** Every hostname your widget connects to
  shows up as `network:<host>`. Every settings section the widget
  reads shows up as `settings:plugin` / `settings:plugin/<other>` /
  `settings:app`. Filesystem writes outside `data_dir` show up as
  `filesystem:write:<path>`. Reviewer: grep the source against the
  declared set to confirm there's no drift.
- **Network egress.** Every URL your widget hits (server-side or
  client-side), and which option / setting decides what gets called.
  Unexpected outbound calls are the most common review block.
  `network:*` (unrestricted) is allowed but expensive to review;
  widgets that genuinely need it (gallery image fetchers, etc.)
  should say why in the PR.
- **Settings access.** What `settings.json` keys does your widget
  read? Anything outside `cell_options[*]` and your declared
  `settings`? Reads of other plugins' sections must be declared.
- **Filesystem access.** Reads of your `data_dir` and the plugin
  folder are free. Anything else, especially writes, are a red flag
  and need an explicit `filesystem:write:<path>` declaration.
- **Secrets handling.** If your widget needs an API key, declare it
  in `settings` with `secret: true` so it's stored as
  `<name>_secret` in `settings.json` and isn't echoed back to the
  edit form.
- **Failure modes.** Widgets that crash the cell on upstream errors
  should return `{"error": "..."}` from `fetch()` instead of raising
  so the client.js renders an error card.

## Repo hygiene

- **Add a `.gitignore`** with at least `__pycache__/` and `.DS_Store`.
  GitHub source tarballs include everything in the working tree;
  shipping `.pyc` files bloats the install and makes the diff harder
  to review.
- **Ship a `README.md`** in the widget repo explaining what it does,
  what settings it needs, and (if applicable) the API quotas or
  rate limits users should know about.
- **Ship a `LICENSE`.** MIT is the conventional choice for community
  widgets; it matches Tesserae itself.

## Removing an entry

PR the catalog with the entry removed from `widgets.json` + the
screenshot dir. Installed users keep working until they manually
uninstall, the install record on their disk is the authoritative
source.
