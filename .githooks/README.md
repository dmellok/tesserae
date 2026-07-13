# Git hooks

Versioned hooks for this repo. Activate them once with:

```sh
git config core.hooksPath .githooks
```

## `pre-push`

One guard: the release gate.

Refuses to push any `v*` tag. Pushing one triggers
`.github/workflows/release.yml`, which publishes a GitHub Release and
marks it `--latest` (so `api.tesserae.ink/version/latest` serves it as
the newest version). This stops a routine `git push --tags` /
`git push --follow-tags` from cutting a release by accident.

To cut a release on purpose, opt in:

```sh
TESSERAE_ALLOW_RELEASE=1 git push origin vX.Y.Z
```

Bypass for a one-off push:

```sh
git push --no-verify
```
