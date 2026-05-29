# Git hooks

Versioned hooks for this repo. Activate them once with:

```sh
git config core.hooksPath .githooks
```

## `pre-push`

Nudges to bump `pyproject.toml` + create a matching `vX.Y.Z` tag when
shippable code has changed since the last tag. Triggers when:

- The current `pyproject.toml` version equals the version on the last
  `v*` tag, **and**
- Any path in `app/`, `plugins/`, `renderers/`, `devices/`, `static/`,
  `templates/`, `schema/`, `pyproject.toml`, `install.sh`, or
  `install.ps1` has changed since that tag.

Docs-only, tests-only, screenshot, and `.github/` changes do **not**
trigger the nudge.

Bypass it for a one-off push (docs hotfix, WIP):

```sh
git push --no-verify
```
