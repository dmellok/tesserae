# Git hooks

Versioned hooks for this repo. Activate them once with:

```sh
git config core.hooksPath .githooks
```

## `pre-push`

Two guards.

### Release guard

Refuses to push any `v*` tag. Pushing one triggers
`.github/workflows/release.yml`, which publishes a GitHub Release and
marks it `--latest` (so `api.tesserae.ink/version/latest` serves it as
the newest version). This stops a routine `git push --tags` /
`git push --follow-tags` from cutting a release by accident.

To cut a release on purpose, opt in:

```sh
TESSERAE_ALLOW_RELEASE=1 git push origin vX.Y.Z
```

### Version-bump nudge

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
