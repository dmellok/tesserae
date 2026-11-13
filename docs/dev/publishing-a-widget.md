# Publish a widget through the catalog

Publish only after the widget renders cleanly in `/_test/render` and passes its smoke test.
Choose how to ship it:

| Path | When to use | Trust model |
|---|---|---|
| **Bundle in the Tesserae repo** | Foundational widgets ([Stable tier](../widgets/tiers.md)), built against a documented API, intended to ship to every installation. | Reviewed in the host PR; lives in `plugins/<id>/` alongside the existing bundled widgets. |
| **Publish through the community catalog** | Your own widget, third-party APIs, niche use cases, anything you want users to opt into rather than installing for everyone. | Audit-only review on each catalog PR; lives in [dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets) and installs into the user's `data/marketplace/<id>/` via Widgets → Browse catalog. |

For the bundled path see
[Build a widget with AI → Submitting](writing-a-widget.md#submitting).

## How the catalog works

The catalog is `widgets.json` in [dmellok/tesserae-widgets](https://github.com/dmellok/tesserae-widgets).
Each entry pins a tagged release tarball (archive) and its sha256 checksum.
Browse shows one card per entry.

When a user clicks Install, Tesserae:

- Downloads the tarball and verifies its sha256.
- Checks `plugin.json` against the host schema.
- Installs it in `data/marketplace/<id>/`, which survives image upgrades.

For now (phase 1) the trust model is audit-only: every entry needs a PR and manual review. There is no capability sandbox
or process isolation yet. See GitHub issues
[#2](https://github.com/dmellok/tesserae/issues/2) /
[#3](https://github.com/dmellok/tesserae/issues/3) for the follow-up
work. What this means:

- Your widget runs in Tesserae's Python process with full filesystem and network access.
- The PR review is the security boundary. Code that reads
  `settings.json`, writes outside its `data_dir`, or makes
  surprising network calls will be sent back.
- Users can install your widget once your PR has merged and the
  catalog raw URL has caught up (\~5 minutes).

## The flow

1. Build the widget under any folder layout you like
   (`devref_card/plugin.json` + `client.js` + optional `server.py`).
   The plugin contract is documented at
   [the widget contract page](../widgets.md); validate locally with:

    ```sh
    python -c "import json, jsonschema; \
      jsonschema.validate(json.load(open('plugin.json')), \
                          json.load(open('path/to/tesserae/schema/plugin.schema.json')))"
    ```

2. Publish your widget to its own GitHub repo and tag a release.
   GitHub serves a tarball at a stable URL:

    ```text
    https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz
    ```

3. Capture the tarball sha256. The catalog pins this to detect
   drift if the tag is republished:

    ```sh
    curl -sL https://github.com/<you>/<repo>/archive/refs/tags/v0.1.0.tar.gz \
      | shasum -a 256
    ```

4. Take screenshots. At minimum `lg.png` (largest cell size).
   Render the widget locally via `/_test/render?plugin=<id>&size=lg`,
   crop, and save. Optional sizes (`xs`, `sm`, `md`) help users
   choose a size before installing.

5. Open a PR to [tesserae-widgets](https://github.com/dmellok/tesserae-widgets).
   Add your screenshot under `screenshots/<id>/lg.png` and your entry
   to `widgets.json`. See [the catalog
   CONTRIBUTING](https://github.com/dmellok/tesserae-widgets/blob/main/CONTRIBUTING.md)
   for the entry format and review expectations.

6. GitHub Actions downloads every tarball and recomputes its sha256.
   It checks `plugin.json` against the host schema.
   For bundles, it also checks that `folders` matches the tarball contents.

7. Review + merge. I read all the source for network calls, settings access,
   and access outside `data_dir`. Explain these in the PR's "Networking + settings" section.

8. Catalog updates. Once merged, the raw URL catches up in
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

Find all field rules in
[`schema/marketplace.schema.json`](https://github.com/dmellok/tesserae-widgets/blob/main/schema/marketplace.schema.json).
`description` has a 280-character limit; catalog CI rejects longer text.
Put longer descriptions in the widget's README.
Use only the `tags` values listed in the schema.

## Bundle entries (widget families)

A bundle installs several plugin folders from one catalog entry.
Use it when widgets need a companion `_core` for an admin page, saved instances,
or API keys. Put the plugin folders inside one enclosing folder in the tarball.

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

The catalog `id` names the family; it can differ from the folder name.
The marketplace requires direct children to match `folders` exactly and validates each `plugin.json`.
It installs them side by side under `data/marketplace/`.

Install and uninstall apply to the whole bundle atomically (all or nothing).
Each folder keeps its own version.

`folders` is optional; the installer can detect the layout.
Declare it so reviewers and users can see which folders will install on the Browse card.
Catalog CI requires an exact match when the field is present.

The [Dev Reference
Bundle](https://github.com/dmellok/tesserae-widget-devref) ships
through the catalog as `id: "devref"` with `folders: ["devref_card",
"devref_core"]`. Use it as an example of cell options, `choices_from`,
`fetch()`, `blueprint()`, and admin templates.

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
when their installed version is older than the catalog's. Click it to
update, then restart.

## Review checklist (what reviewers look at)

Declare each capability in the `requires:` array in `plugin.json`.
See [the widget contract's capability section](../widgets.md#capabilities-requires) for allowed values.
Tesserae enforces network declarations at the socket layer.

- `requires:` declared. Every hostname your widget connects to
  shows up as `network:<host>`. Every settings section the widget
  reads shows up as `settings:plugin` / `settings:plugin/<other>` /
  `settings:app`. Filesystem writes outside `data_dir` show up as
  `filesystem:write:<path>`. Reviewer: grep the source against the
  declared set.
- List every server-side or client-side URL and which option/setting chooses it.
  Unexpected outbound calls are the most common review block.
  Unrestricted `network:*` access is allowed. Explain it in the PR; it takes more work to review.
  Image galleries are one possible use.
- Settings access. List the `settings.json` keys read outside `cell_options[*]`
  and your declared `settings`. Declare reads of other plugins' sections.
- Filesystem access. Reads of your `data_dir` and the plugin
  folder are free. Anything else, especially writes, are a red flag
  and need an explicit `filesystem:write:<path>` declaration.
- Secrets handling. If your widget needs an API key, declare it
  in `settings` with `secret: true` so it's stored as
  `<name>_secret` in `settings.json` and isn't echoed back to the
  edit form.
- Failure modes. Return `{"error": "..."}` from `fetch()` on upstream errors.
  Don't raise; client.js should show an error card.
- `design.palette` declared (if extended). Strict palette widgets use Spectra tokens by default;
  they dither cleanly on every panel and need no declaration.

  For arbitrary CSS colours (including gradients, layered shapes, and soft shadows), declare
  `"design": {"palette": "extended"}` in `plugin.json`.
  Check the dithered output at the target panel resolution; browser preview alone is not enough.

  Soft scenery dithers well; fine text on gradients does not.
  You take responsibility for these tradeoffs. See
  [the widget contract's `design.palette` section](../widgets.md#designpalette-opting-out-of-strict-colour-tokens).

## Touch actions in widget markup

Use `data-on-tap` for tappable regions on touch displays.
Use `data-on-swipe` with a JSON direction map for swipes.
Values follow the button-action grammar: `refresh`, `page:<id>`, `rotate_next`, etc.

The server matches strokes to each annotated node's rendered box, so touch regions follow your layout.
Set `"on_tap": "refresh"` in `plugin.json` for a whole-cell default.
Users can override it per cell.

Markup may only trigger navigation actions. Actions with side effects (`webhook:`,
and the future Home Assistant action) are ignored in widget markup.
They work only in user-authored cell/element settings or a code element's named actions map.

## Repo hygiene

- Add a `.gitignore` with at least `__pycache__/` and `.DS_Store`.
  GitHub source tarballs include everything in the working tree;
  shipping `.pyc` files bloats the install and makes the diff harder
  to review.
- Ship a `README.md` in the widget repo explaining what it does,
  what settings it needs, and (if applicable) the API quotas or
  rate limits users should know about.
- Ship a `LICENSE`. AGPL-3.0-or-later is the conventional choice
  for community widgets; it matches Tesserae itself. Permissive
  licences (MIT, Apache-2.0) are also accepted as long as they're
  compatible with AGPL-3 for users who combine your widget into a
  Tesserae deployment.

## Removing an entry

Open a catalog PR removing the `widgets.json` entry and screenshot directory.
Existing installs keep working until users uninstall them.
The local install record remains authoritative.
